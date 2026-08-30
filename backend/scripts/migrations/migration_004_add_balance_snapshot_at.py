"""
Migration 004: Add balance_snapshot_at to trust_accounts_v2 documents (D-005).

The trust_accounts_v2 collection stores current_balance_cents as a maintained
running balance (updated by cron_trust_interest and trust_phase1 endpoints).
This migration adds balance_snapshot_at so callers can detect staleness.

For existing documents without the field, snapshot_at is set to the
document's updated_at value (or created_at as a fallback).

Run with:
  cd /path/to/backend
  venv/bin/python3 -m scripts.migrations.migration_004_add_balance_snapshot_at

Flags:
  --dry-run   Print what would change without writing to the database.
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


async def backfill(db, *, dry_run: bool = False) -> dict:
    """Add balance_snapshot_at to documents that are missing it."""
    docs = await db.trust_accounts_v2.find(
        {"balance_snapshot_at": {"$exists": False}},
        {"_id": 1, "updated_at": 1, "created_at": 1},
    ).to_list(None)

    total = len(docs)
    if total == 0:
        logger.info("All trust_accounts_v2 documents already have balance_snapshot_at — nothing to do.")
        return {"total": 0, "updated": 0}

    logger.info("Found %d trust_accounts_v2 documents missing balance_snapshot_at.", total)

    updated = 0
    for doc in docs:
        snapshot_at = doc.get("updated_at") or doc.get("created_at")
        if not snapshot_at:
            from datetime import datetime, timezone
            snapshot_at = datetime.now(timezone.utc).isoformat()

        if dry_run:
            logger.info("  [dry-run] _id=%s  balance_snapshot_at → %s", doc["_id"], snapshot_at)
            continue

        result = await db.trust_accounts_v2.update_one(
            {"_id": doc["_id"]},
            {"$set": {"balance_snapshot_at": snapshot_at}},
        )
        if result.modified_count:
            updated += 1

    prefix = "[dry-run] " if dry_run else ""
    logger.info(
        "\n%sMigration 004 complete:\n"
        "  documents processed: %d\n"
        "  balance_snapshot_at set: %d",
        prefix,
        total,
        updated if not dry_run else total,
    )
    return {"total": total, "updated": updated}


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_004_add_balance_snapshot_at.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import argparse
    parser = argparse.ArgumentParser(description="Add balance_snapshot_at to trust_accounts_v2")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    load_dotenv()
    mongo_url = os.getenv("MONGO_URL", os.getenv("MONGODB_URL", "mongodb://localhost:27017"))
    db_name = os.getenv("DB_NAME", "strata_production")

    logger.info("Connecting to MongoDB: %s / %s", mongo_url, db_name)
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    try:
        await backfill(db, dry_run=args.dry_run)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
