# fetch_inventory.py
import time
import io
import gzip
import requests
import pandas as pd  # type: ignore
from datetime import datetime, timezone
from pymongo import MongoClient, UpdateOne
import logging

# FIXED IMPORTS (absolute imports)
from backend.auth import get_access_token
from backend.config import MONGO_URI, DB_NAME, COLLECTION_NAME


# --- MongoDB Setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# --- Amazon SP-API Setup ---
HOST = "sellingpartnerapi-na.amazon.com"


def create_inventory_report(token):
    """Create a GET_MERCHANT_LISTINGS_ALL_DATA report"""
    url = f"https://{HOST}/reports/2021-06-30/reports"
    headers = {
        "x-amz-access-token": token,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    body = {
        "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA",
        "marketplaceIds": ["ATVPDKIKX0DER"]
    }
    res = requests.post(url, headers=headers, json=body)
    res.raise_for_status()
    report_id = res.json()["reportId"]
    print("Report created with ID:", report_id)
    return report_id


def poll_report_status(token, report_id):
    """Poll until the report is DONE and return documentId"""
    url = f"https://{HOST}/reports/2021-06-30/reports/{report_id}"
    headers = {"x-amz-access-token": token, "Accept": "application/json"}

    while True:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()
        status = data["processingStatus"]
        print("Report status:", status)
        if status == "DONE":
            return data["reportDocumentId"]
        elif status in ["CANCELLED", "FATAL"]:
            raise Exception(f"Report failed with status: {status}")
        time.sleep(30)


def download_report(token, document_id):
    """Download and decode SP-API report properly"""
    url = f"https://{HOST}/reports/2021-06-30/documents/{document_id}"
    headers = {"x-amz-access-token": token, "Accept": "application/json"}
    meta = requests.get(url, headers=headers).json()
    download_url = meta["url"]

    res = requests.get(download_url)
    res.raise_for_status()
    content = res.content

    if meta.get("compressionAlgorithm") == "GZIP":
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
            file_content = f.read()
    else:
        file_content = content

    return file_content


def parse_report_to_dataframe(file_content):
    """Parse SP-API report into DataFrame with live MFN filtering"""
    text_stream = io.StringIO(file_content.decode("utf-8", errors="ignore"))
    df = pd.read_csv(
        text_stream,
        delimiter="\t",
        engine="python",
        on_bad_lines="skip"
    )

    # --- Basic filters ---
    if "item-status" in df.columns:
        df = df[df["item-status"].str.lower() == "active"]
    if "fulfillment-channel" in df.columns:
        df = df[df["fulfillment-channel"] == "DEFAULT"]  # MFN
    if "quantity" in df.columns:
        df = df[df["quantity"] > 0]

    # --- Extra filters to catch suppressed/blocked listings ---
    if "asin1" in df.columns:
        df = df[df["asin1"].notna()]  # must have valid ASIN
    if "price" in df.columns:
        df = df[df["price"] > 0]      # price > 0
    if "listing-is-suppressed" in df.columns:
        df = df[df["listing-is-suppressed"] != "true"]  # remove suppressed

    # Skip rows without valid seller-sku
    if "seller-sku" in df.columns:
        df = df[df["seller-sku"].notna() & (df["seller-sku"] != "")]

    # Drop duplicates by seller-sku
    if "seller-sku" in df.columns:
        df = df.drop_duplicates(subset=["seller-sku"], keep="first")

    df = df.dropna(how="all")
    print(f"Parsed {len(df)} truly live MFN rows")
    return df


def upsert_inventory(df):
    """Insert new listings, update active listings, mark missing as sold"""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    api_skus = set(df["seller-sku"])
    db_skus = set([doc["seller-sku"] for doc in collection.find({}, {"seller-sku": 1})])

    # 1. Bulk upsert active listings
    bulk_ops_active = []
    for _, row in df.iterrows():
        bulk_ops_active.append(
            UpdateOne(
                {"seller-sku": row["seller-sku"]},
                {
                    "$set": {
                        "item-name": row["item-name"],
                        "asin": row["asin1"],
                        "price": row["price"],
                        "quantity": row["quantity"],
                        "status": "active",
                        "open-date": row.get("open-date", ""),
                        # keep enriched fields untouched
                    }
                },
                upsert=True
            )
        )

    # 2. Bulk mark missing listings as sold
    skus_to_mark_sold = db_skus - api_skus
    bulk_ops_sold = []
    for sku in skus_to_mark_sold:
        bulk_ops_sold.append(
            UpdateOne(
                {"seller-sku": sku},
                {
                    "$set": {
                        "status": "sold",
                        "sold-date": datetime.now(timezone.utc)
                    }
                }
            )
        )

    # Execute bulk operations in batches with error handling
    try:
        batch_size = 1000
        if bulk_ops_active:
            total_upserted = 0
            for i in range(0, len(bulk_ops_active), batch_size):
                batch = bulk_ops_active[i:i + batch_size]
                result = collection.bulk_write(batch)
                total_upserted += result.upserted_count + result.modified_count
            logger.info(f"Active listings upserted: {total_upserted}")
        if bulk_ops_sold:
            total_updated = 0
            for i in range(0, len(bulk_ops_sold), batch_size):
                batch = bulk_ops_sold[i:i + batch_size]
                result = collection.bulk_write(batch)
                total_updated += result.modified_count
            logger.info(f"Sold listings updated: {total_updated}")
        print(f"Upsert complete. {len(api_skus)} active listings processed, {len(skus_to_mark_sold)} marked as sold.")
    except Exception as e:
        logger.error(f"Bulk upsert failed: {e}")
        raise


def main():
    print(f"Using MONGO_URI: {MONGO_URI}")
    print(f"Using DB_NAME: {DB_NAME}")
    print(f"Using COLLECTION_NAME: {COLLECTION_NAME}")
    token = get_access_token()

    report_id = create_inventory_report(token)
    document_id = poll_report_status(token, report_id)
    file_content = download_report(token, document_id)
    df = parse_report_to_dataframe(file_content)
    upsert_inventory(df)

    print("✅ Truly live MFN inventory sync complete!")


if __name__ == "__main__":
    main()
