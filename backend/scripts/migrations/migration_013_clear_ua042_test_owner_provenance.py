"""
Migration 013 — Clear stale test-owner provenance fields from UA042 (building 13195).

Root cause: _cascade_owner_change was triggered on unit UA042 during a test
run that hit the live DB. It wrote:
  arrears_metadata.previous_owner  = "Test Owner"
  arrears_metadata.inherited_arrears = 1968.37
  arrears_metadata.transferred_at  = "2026-04-10T..."

These fields appear on the Arrears Recovery board as
"$1,968.37 carried from Test Owner (2026-04-10)", which is test noise
on a real production unit.

This migration unsets those three sub-fields only on UA042 / building 13195,
leaving all other arrears_metadata sub-fields (dca_status, contact_log, etc.)
untouched.

Safe to re-run: $unset on already-absent fields is a no-op.
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

BUILDING_ID = "13195"
UNIT_NUMBER = "UA042"


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_013_clear_ua042_test_owner_provenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    result = await db.units.update_one(
        {"building_id": BUILDING_ID, "unit_number": UNIT_NUMBER},
        {
            "$unset": {
                "arrears_metadata.previous_owner": "",
                "arrears_metadata.previous_owner_b": "",
                "arrears_metadata.inherited_arrears": "",
                "arrears_metadata.transferred_at": "",
            }
        },
    )

    if result.matched_count == 0:
        print(f"  WARNING: unit {UNIT_NUMBER} not found in building {BUILDING_ID}")
    else:
        print(
            f"  Cleared stale provenance fields from {UNIT_NUMBER} "
            f"(modified={result.modified_count})"
        )

    client.close()


if __name__ == "__main__":
    asyncio.run(run())
