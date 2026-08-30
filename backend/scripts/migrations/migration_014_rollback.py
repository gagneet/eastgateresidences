"""migration_014_rollback.py — Drop match_review_queue and payer_entities."""
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_rollback(db) -> None:
    """Generated function header.

    Function: run_rollback
    Path: backend/scripts/migrations/migration_014_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for coll in ("match_review_queue", "payer_entities"):
        existing = await db.list_collection_names()
        if coll in existing:
            await db.drop_collection(coll)
            logger.info("Dropped collection: %s", coll)
        else:
            logger.info("Collection not found (already dropped?): %s", coll)


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_014_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "strata_management")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        await run_rollback(db)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
