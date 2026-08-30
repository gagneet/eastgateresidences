"""
migration_012_create_ledger_hardening_collections.py — Phase 2 Ledger Hardening.

Creates three new collections required by the trust ledger invariants:
  - financial_periods   : period lifecycle state machine (one doc per building × period × fund)
  - trust_ledger_seals  : daily Merkle seals (append-only, hash-chained)
  - reconciliation_items: in-flight reconciling items (deposits-in-transit, unpresented)

Idempotent: safe to re-run; existing collections and indexes are skipped.
Rollback:   migration_012_rollback.py drops all three collections.
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
from pymongo.errors import CollectionInvalid

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration(db) -> None:
    """Generated function header.

    Function: run_migration
    Path: backend/scripts/migrations/migration_012_create_ledger_hardening_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await db.list_collection_names()

    # ── financial_periods ─────────────────────────────────────────────────────
    if "financial_periods" not in existing:
        await db.create_collection("financial_periods")
        logger.info("Created collection: financial_periods")
    else:
        logger.info("Collection already exists: financial_periods (skipping)")

    await db.financial_periods.create_indexes([
        IndexModel(
            [("building_id", ASCENDING), ("period_id", ASCENDING), ("fund_type", ASCENDING)],
            unique=True,
            name="uq_building_period_fund",
        ),
        IndexModel([("building_id", ASCENDING), ("state", ASCENDING)], name="idx_building_state"),
    ])
    logger.info("Indexes ensured: financial_periods")

    # ── trust_ledger_seals ────────────────────────────────────────────────────
    if "trust_ledger_seals" not in existing:
        await db.create_collection("trust_ledger_seals")
        logger.info("Created collection: trust_ledger_seals")
    else:
        logger.info("Collection already exists: trust_ledger_seals (skipping)")

    await db.trust_ledger_seals.create_indexes([
        IndexModel(
            [("tenant_id", ASCENDING), ("period_id", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="uq_tenant_period_date",
        ),
        IndexModel([("tenant_id", ASCENDING), ("date", ASCENDING)], name="idx_tenant_date"),
    ])
    logger.info("Indexes ensured: trust_ledger_seals")

    # ── reconciliation_items ──────────────────────────────────────────────────
    if "reconciliation_items" not in existing:
        await db.create_collection("reconciliation_items")
        logger.info("Created collection: reconciliation_items")
    else:
        logger.info("Collection already exists: reconciliation_items (skipping)")

    await db.reconciliation_items.create_indexes([
        IndexModel(
            [("building_id", ASCENDING), ("period_id", ASCENDING)],
            name="idx_building_period",
        ),
        IndexModel(
            [("building_id", ASCENDING), ("item_type", ASCENDING), ("status", ASCENDING)],
            name="idx_building_type_status",
        ),
    ])
    logger.info("Indexes ensured: reconciliation_items")

    logger.info("migration_012 complete — %s", datetime.now(timezone.utc).isoformat())


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_012_create_ledger_hardening_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "strata_management")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    logger.info("Migration 012 connecting to %s / %s", mongo_url, db_name)
    try:
        await run_migration(db)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
