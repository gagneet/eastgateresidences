"""migration_012_rollback.py — Drop the three Phase 2 collections."""
import asyncio
import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "strata_management")

_COLLECTIONS = ["financial_periods", "trust_ledger_seals", "reconciliation_items"]


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_012_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    try:
        for name in _COLLECTIONS:
            await db.drop_collection(name)
            logger.info("Dropped collection: %s", name)
        logger.info("migration_012 rollback complete")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
