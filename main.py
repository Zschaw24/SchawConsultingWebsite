import math
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional
from pymongo import MongoClient
from backend.config import MONGO_URI, DB_NAME, COLLECTION_NAME  # relative import from backend

def sanitize(obj):
    """Recursively replace NaN floats with None."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize(item) for item in obj]
    elif isinstance(obj, float) and math.isnan(obj):
        return None
    else:
        return obj

app = FastAPI(title="Inventory API")

# Connect to MongoDB
client = MongoClient(
    MONGO_URI,
    tls=True,
    tlsAllowInvalidCertificates=True  # skip cert validation if necessary
)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# Serve frontend static files
frontend_static_path = os.path.join("frontend", "static")
if os.path.exists(frontend_static_path):
    app.mount("/static", StaticFiles(directory=frontend_static_path), name="static")
else:
    print(f"Warning: static directory {frontend_static_path} does not exist!")

@app.get("/")
def read_root():
    """Serve index.html from frontend folder."""
    index_file = os.path.join("frontend", "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    else:
        raise HTTPException(status_code=404, detail="index.html not found in frontend folder")

@app.get("/listings")
def get_listings(
    sku: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    fulfillment_channel: Optional[str] = None
):
    """Get active listings from MongoDB with optional filtering."""
    query = {"status": "active"}

    if sku:
        query["seller-sku"] = sku

    price_query = {}
    if min_price is not None:
        price_query["$gte"] = min_price
    if max_price is not None:
        price_query["$lte"] = max_price
    if price_query:
        query["price"] = price_query

    if fulfillment_channel:
        query["fulfillment-channel"] = fulfillment_channel

    results = list(collection.find(query, {"_id": 0}))

    if sku and not results:
        raise HTTPException(status_code=404, detail=f"SKU {sku} not found")

    return {"count": len(results), "listings": sanitize(results)}

# Windows-friendly entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
