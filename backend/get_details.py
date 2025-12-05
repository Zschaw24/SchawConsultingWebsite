# get_details.py
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymongo import MongoClient
from backend.config import MONGO_URI, DB_NAME, COLLECTION_NAME
from backend.auth import get_access_token
import requests
import sys
import traceback

# --- Config ---
CACHE_DAYS = 100
MAX_RETRIES = 3
MAX_WORKERS = 5        # tune this based on your network / throttling
REQUEST_DELAY = 0.15   # delay per-thread between requests, seconds
MARKETPLACE = "ATVPDKIKX0DER"
HOST = "sellingpartnerapi-na.amazon.com"

# --- MongoDB Setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# ---------------------------------------------------------------------
# SINGLE-ASIN CATALOG REQUEST
# ---------------------------------------------------------------------
def fetch_catalog_item(token: str, asin: str):
    url = f"https://{HOST}/catalog/2020-12-01/items/{asin}"
    headers = {
        "x-amz-access-token": token,
        "Accept": "application/json"
    }
    params = {
        "marketplaceIds": MARKETPLACE,
        "includedData": "images,summaries,attributes"
    }

    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code == 429:
                # Throttled
                print(f"[THROTTLED] ASIN {asin} — retrying... (attempt {retries + 1})")
                retries += 1
                time.sleep(2 ** retries)
                continue

            if response.status_code in (400, 404):
                # Bad/asin not found
                print(f"[INVALID ASIN] {asin} — {response.status_code}")
                return None

            response.raise_for_status()
            data = response.json()
            # Many SP-API responses put useful content under "payload"
            return data.get("payload", data)

        except requests.exceptions.HTTPError as e:
            print(f"[HTTP ERROR] ASIN {asin}: {e} — attempt {retries + 1}")
            retries += 1
            time.sleep(2 ** retries)

        except requests.exceptions.RequestException as e:
            print(f"[REQUEST EXCEPTION] ASIN {asin}: {e} — attempt {retries + 1}")
            retries += 1
            time.sleep(2 ** retries)

    print(f"[FAILED AFTER RETRIES] ASIN {asin}")
    return None

# ---------------------------------------------------------------------
# DATA NORMALIZATION
# ---------------------------------------------------------------------
def clean_listing_data(payload: dict, original: dict):
    if not payload:
        return None

    attributes = payload.get("Attributes", {}) or {}

    # TITLE
    title = attributes.get("ItemName")
    if isinstance(title, list):
        title = title[0] if title else None
    if not title:
        title = original.get("item-name", "")

    # CATEGORY / FORMAT
    category = attributes.get("ProductType")
    if isinstance(category, list):
        category = category[0] if category else ""
    category = category or ""

    # MAIN IMAGE
    image_url = ""
    images = payload.get("Images") or payload.get("images") or []
    if isinstance(images, list):
        for img_group in images:
            # img_group may be dict with marketplaceId + images list
            if isinstance(img_group, dict) and img_group.get("marketplaceId") == MARKETPLACE:
                for img in img_group.get("images", []):
                    if img.get("variant") == "MAIN" or img.get("variant") == "main":
                        image_url = img.get("link") or img.get("url") or ""
                        break
                if image_url:
                    break
        # fallback: first found image
        if not image_url and images:
            first = images[0]
            if isinstance(first, dict):
                imgs = first.get("images", [])
                if isinstance(imgs, list) and imgs:
                    image_url = imgs[0].get("link") or imgs[0].get("url") or ""

    # AUTHORS
    author = ""
    contributors = attributes.get("Contributor") or []
    if isinstance(contributors, list):
        author_names = []
        for c in contributors:
            if isinstance(c, dict):
                # contributor objects often have "value" or "name"
                v = c.get("value") or c.get("name") or ""
                if v:
                    author_names.append(v)
        author = ", ".join(author_names)

    # IDENTIFIERS
    identifiers = {
        "asin": payload.get("asin") or original.get("asin1") or "",
        "isbn_10": attributes.get("ISBN") or attributes.get("ISBN_10") or "",
        "isbn_13": attributes.get("ISBN13") or attributes.get("ISBN_13") or ""
    }

    # PUBLICATION DATE
    publication_date = attributes.get("ReleaseDate") or attributes.get("PublicationDate") or ""

    # LANGUAGE
    language = attributes.get("Language") or ""

    # PAGE COUNT
    page_count = attributes.get("NumberOfPages") or attributes.get("Pages") or None

    # SUMMARY / DESCRIPTION
    summary = ""
    summaries = payload.get("summaries") or payload.get("Summaries") or []
    if isinstance(summaries, list):
        for s in summaries:
            if isinstance(s, dict):
                text = s.get("text") or s.get("summary") or s.get("content")
                if text:
                    summary = text
                    break

    now = datetime.now(timezone.utc)
    enriched_doc = {
        "title_clean": title.strip() if isinstance(title, str) else title,
        "author_or_brand": author,
        "category_clean": category,
        "image_url": image_url,
        "publication_date": publication_date,
        "language": language,
        "page_count": page_count,
        "identifiers": identifiers,
        "summary": summary,
        "enriched": True,
        "enriched_at": now
    }
    return enriched_doc

# ---------------------------------------------------------------------
# THREAD-SAFE PROCESSING FUNCTION
# ---------------------------------------------------------------------
def process_listing(listing: dict, token: str):
    asin = None
    # try common fields
    if listing.get("asin"):
        asin = listing.get("asin")
    elif listing.get("asin1"):
        asin = listing.get("asin1")
    elif listing.get("identifiers", {}).get("asin"):
        asin = listing["identifiers"]["asin"]

    if not asin:
        # no asin found, mark as enriched False with error and skip
        collection.update_one(
            {"_id": listing["_id"]},
            {"$set": {
                "enriched": False,
                "enriched_error": "Missing ASIN",
                "enriched_at": datetime.now(timezone.utc)
            }}
        )
        return f"[SKIP] Missing ASIN → SKU: {listing.get('seller-sku')}"

    payload = fetch_catalog_item(token, asin)
    if payload is None:
        collection.update_one(
            {"_id": listing["_id"]},
            {"$set": {
                "enriched": False,
                "enriched_error": "Invalid or not found",
                "enriched_at": datetime.now(timezone.utc)
            }}
        )
        return f"[FAILED] {asin}"

    enriched = clean_listing_data(payload, listing)
    if enriched:
        # Merge with existing but overwrite fields from enrichment
        try:
            collection.update_one(
                {"_id": listing["_id"]},
                {"$set": enriched}
            )
        except Exception as e:
            # if a write fails, write a debug error field so we can replay later
            collection.update_one(
                {"_id": listing["_id"]},
                {"$set": {
                    "enriched": False,
                    "enriched_error": f"update failed: {str(e)}",
                    "enriched_at": datetime.now(timezone.utc)
                }}
            )
            return f"[ERROR WRITE] {asin}: {e}"
    time.sleep(REQUEST_DELAY)
    return f"[OK] {asin}"

# ---------------------------------------------------------------------
# MAIN ENRICHMENT LOGIC
# ---------------------------------------------------------------------
def main():
    try:
        print(f"Using MONGO_URI: {MONGO_URI}")
        print(f"Using DB_NAME: {DB_NAME}")
        print(f"Using COLLECTION_NAME: {COLLECTION_NAME}")
        print("🔄 Starting catalog enrichment...")
        token = get_access_token()

        threshold = datetime.now(timezone.utc) - timedelta(days=CACHE_DAYS)

        # Query: not enriched OR enrichment older than threshold OR missing key fields
        query = {
            "$or": [
                {"enriched": {"$exists": False}},
                {"enriched_at": {"$lt": threshold}},
                {"image_url": {"$exists": False}},
                {"image_url": ""}
            ]
        }

        listings = list(collection.find(query))
        total = len(listings)
        print(f"📦 Found {total} listings needing enrichment.\n")

        if not listings:
            print("No listings need enrichment. Exiting.")
            return

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_listing, l, token): l for l in listings}
            for i, future in enumerate(as_completed(futures), start=1):
                try:
                    result = future.result()
                    print(f"{i}/{total} {result}")
                except Exception as e:
                    listing = futures[future]
                    # safe printing of exception
                    print(f"[ERROR] Processing listing {listing.get('_id')} → {e}")
                    traceback.print_exc()

        print("\n✅ Enrichment complete!")

    except Exception:
        print("Fatal error in get_details:", file=sys.stderr)
        traceback.print_exc()

if __name__ == "__main__":
    main()
