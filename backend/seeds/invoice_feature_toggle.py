"""
Seed: invoice_ocr feature toggle.

Run once to register the Invoice OCR feature in the feature_toggles collection.
Safe to re-run — upserts on feature_key + building_id.
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncMongoClient(mongo_url)
db = client[os.environ["DB_NAME"]]

INVOICE_OCR_TOGGLE = {
    "feature_key": "invoice_ocr",
    "feature_name": "Invoice & Quote OCR",
    "description": "Upload invoices and quotes; Claude Vision auto-extracts vendor, amounts, GST and posts to the financial ledger.",
    "is_enabled": True,
    "category": "finance",
    "icon": "Receipt",
    "routes": ["/financials/invoices"],
    "depends_on": ["finance"],
    "allowed_roles": ["super_admin", "chairman", "strata_admin", "strata_manager", "ec_member"],
    "building_id": None,  # global default
}


async def seed():
    """Generated function header.

    Function: seed
    Path: backend/seeds/invoice_feature_toggle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = datetime.now(timezone.utc).isoformat()
    toggle = {**INVOICE_OCR_TOGGLE, "updated_at": now}
    result = await db.feature_toggles.update_one(
        {"feature_key": toggle["feature_key"], "building_id": None},
        {"$set": toggle, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    if result.upserted_id:
        print("✓ invoice_ocr feature toggle created")
    else:
        print("✓ invoice_ocr feature toggle updated")


if __name__ == "__main__":
    asyncio.run(seed())
