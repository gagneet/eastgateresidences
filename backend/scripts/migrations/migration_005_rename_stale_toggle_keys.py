"""
Migration 005: Rename stale feature toggle keys in the DB (F-012).

Two toggle keys were renamed in the seed (commit 0e6a83f7) to match what the
frontend's hasFeatureAccess() actually checks.  The seed upsert creates the new
documents but leaves the old keys orphaned.  This script deletes them.

Changes:
  savings_ledger       → savings          (frontend checks 'savings')
  building_health_score → building_health  (frontend checks 'building_health')

Idempotent — safe to run multiple times (delete_many on a missing key is a no-op).

Run with:
  cd /path/to/backend
  venv/bin/python3 -m scripts.migrations.migration_005_rename_stale_toggle_keys

Flags:
  --dry-run   Print what would be deleted without writing to the database.
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

STALE_KEYS = ["savings_ledger", "building_health_score"]


async def cleanup(db, *, dry_run: bool = False) -> dict:
    """Delete feature_toggles documents with stale keys."""
    results = {}
    for key in STALE_KEYS:
        docs = await db.feature_toggles.find(
            {"feature_key": key}, {"_id": 1, "building_id": 1}
        ).to_list(None)

        count = len(docs)
        if count == 0:
            logger.info("  %r — not found (already clean)", key)
            results[key] = 0
            continue

        if dry_run:
            logger.info("  [dry-run] %r — would delete %d document(s)", key, count)
            for d in docs:
                logger.info("    _id=%s  building_id=%s", d["_id"], d.get("building_id"))
            results[key] = count
            continue

        result = await db.feature_toggles.delete_many({"feature_key": key})
        logger.info("  %r — deleted %d document(s)", key, result.deleted_count)
        results[key] = result.deleted_count

    prefix = "[dry-run] " if dry_run else ""
    logger.info(
        "\n%sMigration 005 complete: %s",
        prefix,
        ", ".join(f"{k}={v}" for k, v in results.items()),
    )
    return results


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_005_rename_stale_toggle_keys.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import argparse
    parser = argparse.ArgumentParser(description="Delete stale feature toggle keys")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    load_dotenv()
    mongo_url = os.getenv("MONGO_URL", os.getenv("MONGODB_URL", "mongodb://localhost:27017"))
    db_name = os.getenv("DB_NAME", "strata_production")

    logger.info("Connecting to MongoDB: %s / %s", mongo_url, db_name)
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    try:
        await cleanup(db, dry_run=args.dry_run)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
