import os
from dotenv import load_dotenv

load_dotenv()

# Amazon SP-API credentials
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "schaw_consult_inventory_neme")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "listings")

# Seller and marketplace info
SELLER_ID_US = os.getenv("SELLER_ID_US")
MARKETPLACE_ID_US = os.getenv("MARKETPLACE_ID_US")

# AWS SP-API user
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
REGION = os.getenv("REGION")
