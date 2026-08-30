"""
Migration: Create building_id indexes for collections that were active in code
but absent from the database.py registry (D-001 / D-002 from database_duplication_audit.md).

Without these indexes, queries filtered by building_id perform full collection scans.

Collections and indexes:
  conflict_of_interest    : building_id, declared_at
  tenancies               : building_id, property_id
  occupancy_status        : building_id
  occupancy_snapshots     : building_id, snapshot_month
  levy_reminder_settings  : building_id (unique)
  essential_services_log  : building_id, next_due
  strata_sync_jobs        : building_id, created_at
  property_health_snapshots: building_id, property_id, computed_at
  building_financial_health_p2: building_id, computed_at
  document_conversion_logs: building_id, created_at
  ppm_items               : building_id, scheduled_date
  key_fob_register        : building_id
  financial_stress_scores : building_id, computed_at

Run with:
  cd /path/to/backend
  venv/bin/python3 -m scripts.migrations.002_add_unregistered_collection_indexes
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
import motor.motor_asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def create_indexes(db) -> None:
    """Generated function header.

    Function: create_indexes
    Path: backend/scripts/migrations/002_add_unregistered_collection_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    INDEXES = [
        # conflict_of_interest — EC member COI declarations (building-scoped)
        ("conflict_of_interest", [("building_id", 1), ("declared_at", -1)], {}),
        ("conflict_of_interest", [("building_id", 1), ("status", 1)], {}),
        # tenancies — investment property tenancy records (building-scoped)
        ("tenancies", [("building_id", 1), ("property_id", 1)], {}),
        ("tenancies", [("building_id", 1), ("status", 1)], {}),
        # occupancy_status — per-lot occupancy classification
        ("occupancy_status", [("building_id", 1), ("unit_number", 1)], {}),
        # occupancy_snapshots — monthly trend snapshots
        ("occupancy_snapshots", [("building_id", 1), ("snapshot_month", -1)], {}),
        # levy_reminder_settings — one config doc per building
        ("levy_reminder_settings", [("building_id", 1)], {"unique": True}),
        # essential_services_log — fire/lift/etc. service records
        ("essential_services_log", [("building_id", 1), ("next_due", 1)], {}),
        ("essential_services_log", [("building_id", 1), ("service_type", 1)], {}),
        # strata_sync_jobs — strata integration job history
        ("strata_sync_jobs", [("building_id", 1), ("created_at", -1)], {}),
        ("strata_sync_jobs", [("building_id", 1), ("status", 1)], {}),
        # property_health_snapshots — OwnerHub property health scoring
        ("property_health_snapshots", [("building_id", 1), ("property_id", 1), ("computed_at", -1)], {}),
        # building_financial_health_p2 — Phase 2 financial stress scores
        ("building_financial_health_p2", [("building_id", 1), ("computed_at", -1)], {}),
        # document_conversion_logs — PDF/DOCX conversion audit trail
        ("document_conversion_logs", [("building_id", 1), ("created_at", -1)], {}),
        # ppm_items — planned preventive maintenance items
        ("ppm_items", [("building_id", 1), ("scheduled_date", 1)], {}),
        ("ppm_items", [("building_id", 1), ("status", 1)], {}),
        # key_fob_register — access control key assignment records
        ("key_fob_register", [("building_id", 1)], {}),
        # financial_stress_scores — building financial stress indicators
        ("financial_stress_scores", [("building_id", 1), ("computed_at", -1)], {}),
        # scraper_run_logs — global (no building_id), index on ran_at for recency queries
        ("scraper_run_logs", [("ran_at", -1)], {}),
        ("scraper_run_logs", [("scraper_type", 1), ("ran_at", -1)], {}),
    ]

    created = 0
    skipped = 0
    for collection, keys, options in INDEXES:
        try:
            index_name = await db[collection].create_index(keys, **options)
            logger.info("✅  %-45s → %s", collection, index_name)
            created += 1
        except Exception as exc:
            exc_str = str(exc).lower()
            if "already exists" in exc_str or "indexkeyspecsconflict" in str(type(exc).__name__).lower():
                logger.debug("   %-45s → already exists, skipping", collection)
                skipped += 1
            else:
                logger.warning("⚠️  %-45s → %s", collection, exc)
                skipped += 1

    logger.info("\n✅  Migration complete: %d indexes created, %d skipped.", created, skipped)


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/002_add_unregistered_collection_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    load_dotenv()
    mongo_url = os.getenv("MONGO_URL", os.getenv("MONGODB_URL", "mongodb://localhost:27017"))
    db_name = os.getenv("DB_NAME", "strata_production")

    logger.info("Connecting to MongoDB: %s / %s", mongo_url, db_name)
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    await create_indexes(db)
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
