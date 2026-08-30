"""
Migration 003: Backfill user_id on legacy user_units documents (D-004).

The user_units collection has two document shapes:
  Legacy : {owner_name, owner_email, unit_number, ...}  — no user_id FK
  Current: {user_id, unit_number, ...}                  — no inline owner fields

This script adds user_id to every legacy document by matching owner_email
against the users collection.  It is safe to run multiple times (idempotent).

Steps:
  1. Find all user_units docs where user_id is absent and owner_email is present.
  2. Bulk-fetch matching users by email.
  3. For each match, set user_id on the user_units doc (no other fields changed).
  4. Report unmatched docs (email present but no corresponding user found).

Run with:
  cd /path/to/backend
  venv/bin/python3 -m scripts.migrations.migration_003_backfill_user_units_user_id

Flags:
  --dry-run   Print what would change without writing to the database.
  --building  Restrict to a single building_id (default: all buildings).
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


async def backfill(db, *, dry_run: bool = False, building_filter: str | None = None) -> dict:
    """
    Returns a summary dict with keys: total_legacy, matched, unmatched, updated.
    """
    query: dict = {"user_id": {"$exists": False}, "owner_email": {"$exists": True, "$nin": [None, ""]}}
    if building_filter:
        query["building_id"] = building_filter

    legacy_docs = await db.user_units.find(query, {"_id": 1, "id": 1, "owner_email": 1, "building_id": 1,
                                                   "unit_number": 1}).to_list(None)
    total_legacy = len(legacy_docs)

    if total_legacy == 0:
        logger.info("No legacy user_units documents found — nothing to do.")
        return {"total_legacy": 0, "matched": 0, "unmatched": 0, "updated": 0}

    logger.info("Found %d legacy user_units documents (missing user_id).", total_legacy)

    # Collect unique emails for a bulk users lookup
    emails = {doc["owner_email"].lower().strip() for doc in legacy_docs if doc.get("owner_email")}
    logger.info("Unique owner emails to resolve: %d", len(emails))

    user_docs = await db.users.find(
        {"email": {"$in": list(emails)}},
        {"_id": 0, "id": 1, "email": 1},
    ).to_list(None)
    email_to_user_id: dict[str, str] = {u["email"].lower().strip(): u["id"] for u in user_docs}
    logger.info("Users found by email: %d / %d", len(email_to_user_id), len(emails))

    matched = 0
    unmatched = 0
    updated = 0

    for doc in legacy_docs:
        raw_email = doc.get("owner_email", "")
        email_key = raw_email.lower().strip()
        user_id = email_to_user_id.get(email_key)

        if not user_id:
            unmatched += 1
            logger.debug(
                "  ⚠️  No user found for email=%r  building=%s  unit=%s",
                raw_email,
                doc.get("building_id"),
                doc.get("unit_number"),
            )
            continue

        matched += 1
        if dry_run:
            logger.info(
                "  [dry-run] Would set user_id=%s on user_units._id=%s  (unit=%s  building=%s)",
                user_id,
                doc["_id"],
                doc.get("unit_number"),
                doc.get("building_id"),
            )
            continue

        result = await db.user_units.update_one(
            {"_id": doc["_id"]},
            {"$set": {"user_id": user_id}},
        )
        if result.modified_count:
            updated += 1
            logger.debug(
                "  ✅  Set user_id=%s on user_units._id=%s  (unit=%s  building=%s)",
                user_id,
                doc["_id"],
                doc.get("unit_number"),
                doc.get("building_id"),
            )

    prefix = "[dry-run] " if dry_run else ""
    logger.info(
        "\n%sMigration 003 complete:\n"
        "  total legacy docs : %d\n"
        "  matched (email→user): %d\n"
        "  unmatched (no user) : %d\n"
        "  updated in DB       : %d",
        prefix,
        total_legacy,
        matched,
        unmatched,
        updated,
    )

    if unmatched:
        logger.warning(
            "%d user_units docs could not be matched — owner_email has no corresponding "
            "users document. These may be pre-registration records or imported data. "
            "Check the DEBUG log for details.",
            unmatched,
        )

    return {"total_legacy": total_legacy, "matched": matched, "unmatched": unmatched, "updated": updated}


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_003_backfill_user_units_user_id.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import argparse
    parser = argparse.ArgumentParser(description="Backfill user_id on legacy user_units documents")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    parser.add_argument("--building", default=None, help="Restrict to a single building_id")
    args = parser.parse_args()

    load_dotenv()
    mongo_url = os.getenv("MONGO_URL", os.getenv("MONGODB_URL", "mongodb://localhost:27017"))
    db_name = os.getenv("DB_NAME", "strata_production")

    logger.info("Connecting to MongoDB: %s / %s", mongo_url, db_name)
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    try:
        await backfill(db, dry_run=args.dry_run, building_filter=args.building)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
