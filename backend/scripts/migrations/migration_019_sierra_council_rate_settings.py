"""
Migration 019 — Create council_rate_settings for Sierra (16244), FY 2025-26.

Sets block AUV to $4,708,728.00 (Section 227, Block 3, Gungahlin ACT).
Total block entitlement: 10,000.

NOTE: Sierra units currently have no per-unit entitlement values in the units
collection. The block AUV stored here will be used correctly once entitlement
values are added to each unit — the ACT Revenue API call requires a unit's
entitlement to compute its individual rates/land-tax share.
"""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "strata")
SETTINGS_COLLECTION = "council_rate_settings"

BUILDING_ID = "16244"
FINANCIAL_YEAR = "2025-26"
BLOCK_AUV = 4_708_728.0
TOTAL_BLOCK_ENTITLEMENT = 10_000


async def run() -> None:
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_019_sierra_council_rate_settings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    coll = db[SETTINGS_COLLECTION]

    doc = {
        "building_id": BUILDING_ID,
        "financial_year": FINANCIAL_YEAR,
        "block_auv": BLOCK_AUV,
        "total_block_entitlement": TOTAL_BLOCK_ENTITLEMENT,
        "is_configured": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Section 227, Block 3, Gungahlin ACT — official AUV for FY 2025-26.",
    }

    result = await coll.update_one(
        {"building_id": BUILDING_ID, "financial_year": FINANCIAL_YEAR},
        {"$set": doc},
        upsert=True,
    )

    if result.upserted_id:
        print(f"Inserted new Sierra council_rate_settings for FY {FINANCIAL_YEAR}.")
    else:
        print(f"Updated existing Sierra council_rate_settings for FY {FINANCIAL_YEAR}.")

    stored = await coll.find_one(
        {"building_id": BUILDING_ID, "financial_year": FINANCIAL_YEAR},
        {"_id": 0},
    )
    print(f"Stored record: {stored}")
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
