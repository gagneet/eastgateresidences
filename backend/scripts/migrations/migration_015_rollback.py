"""
migration_015_rollback.py — Drop AP pipeline collections created by migration_015.

WARNING: Destructive. Only run if rolling back a failed migration_015 on a
         non-production database. Production data requires a backup-and-restore
         approach instead.
"""
from __future__ import annotations

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

_COLLECTIONS = ["invoice_documents", "recurring_bill_templates", "abn_validation_cache"]


async def run_rollback(db) -> None:
    """Generated function header.

    Function: run_rollback
    Path: backend/scripts/migrations/migration_015_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for name in _COLLECTIONS:
        await db.drop_collection(name)
        logger.info("Dropped collection: %s", name)
    logger.info("migration_015 rollback complete")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_015_rollback.py

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
