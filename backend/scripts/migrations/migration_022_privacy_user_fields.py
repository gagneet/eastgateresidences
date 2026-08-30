"""
Migration 022 — Add privacy consent fields to users collection.

Adds two new fields to every user document that does not already have them:
  - privacy_consent_given  (bool, default False)
  - privacy_consent_date   (str|None, default None)

These fields support the APP 3/5 consent-tracking requirements introduced
alongside GAP-SEC-004 (GET /privacy/my-data).

Idempotent — safe to re-run.  Uses $exists guards so existing values are
never overwritten.  The users collection is a global collection (no
building_id scoping required).

Run:
    cd backend && python3 scripts/migrations/migration_022_privacy_user_fields.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def run_migration() -> dict:
    """Backfill privacy_consent_given and privacy_consent_date for all users."""
    from database import db

    # --- privacy_consent_given -------------------------------------------
    result_given = await db.users.update_many(
        {"privacy_consent_given": {"$exists": False}},
        {"$set": {"privacy_consent_given": False}},
    )
    logger.info(
        "privacy_consent_given backfill: matched=%d, modified=%d",
        result_given.matched_count,
        result_given.modified_count,
    )

    # --- privacy_consent_date --------------------------------------------
    result_date = await db.users.update_many(
        {"privacy_consent_date": {"$exists": False}},
        {"$set": {"privacy_consent_date": None}},
    )
    logger.info(
        "privacy_consent_date backfill: matched=%d, modified=%d",
        result_date.matched_count,
        result_date.modified_count,
    )

    total_modified = result_given.modified_count + result_date.modified_count
    logger.info("Migration 022 complete — %d field additions applied.", total_modified)

    return {
        "migration": "022_privacy_user_fields",
        "privacy_consent_given": {
            "matched": result_given.matched_count,
            "modified": result_given.modified_count,
        },
        "privacy_consent_date": {
            "matched": result_date.matched_count,
            "modified": result_date.modified_count,
        },
        "total_modified": total_modified,
    }


if __name__ == "__main__":
    import json

    result = asyncio.run(run_migration())
    print(json.dumps(result, indent=2))
