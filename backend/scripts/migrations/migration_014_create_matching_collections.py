"""
migration_014_create_matching_collections.py — Phase 3 Matching Engine.

Creates two new collections:
  - match_review_queue : per-building pending/decided match items
  - payer_entities     : global registry of known agency BSB/accounts

Idempotent: safe to re-run. Rollback: migration_014_rollback.py.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, IndexModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration(db) -> None:
    """Generated function header.

    Function: run_migration
    Path: backend/scripts/migrations/migration_014_create_matching_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await db.list_collection_names()

    # ── match_review_queue (tenant-scoped) ───────────────────────────────────
    if "match_review_queue" not in existing:
        await db.create_collection("match_review_queue")
        logger.info("Created collection: match_review_queue")
    else:
        logger.info("Collection already exists: match_review_queue (skipping)")

    await db.match_review_queue.create_indexes([
        IndexModel(
            [("tenant_id", ASCENDING), ("inbox_event_id", ASCENDING)],
            unique=True,
            name="uq_tenant_inbox_event",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("status", ASCENDING)],
            name="idx_tenant_status",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("sla_due_at", ASCENDING)],
            name="idx_tenant_sla",
        ),
    ])
    logger.info("Indexes ensured: match_review_queue")

    # ── payer_entities (global — not building-scoped) ────────────────────────
    if "payer_entities" not in existing:
        await db.create_collection("payer_entities")
        logger.info("Created collection: payer_entities")
    else:
        logger.info("Collection already exists: payer_entities (skipping)")

    await db.payer_entities.create_indexes([
        IndexModel(
            [("bsb", ASCENDING), ("account_number", ASCENDING)],
            unique=True,
            name="uq_bsb_account",
        ),
    ])
    logger.info("Indexes ensured: payer_entities")

    logger.info("migration_014 complete — %s", datetime.now(timezone.utc).isoformat())


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_014_create_matching_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "strata_management")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    logger.info("Migration 014 connecting to %s / %s", mongo_url, db_name)
    try:
        await run_migration(db)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
