"""
migration_007_fix_owner_names_th084_th085.py

Corrects owner data in building 13195 (East Gate Residences) that was
corrupted by the site scraping API:

  TH084 — users.full_name:  "G Vellore Ranganathan"  →  "Shouvik Aditya"
           users.email:      NOT touched here — the scraped ghost account
                             (g.ranganathan@...) is deleted by migration_008,
                             which also activates the owner's real registered
                             account (adityashouvik@gmail.com).

  TH085 — units.owner_name:   scraped/truncated value  →  "Jinal Achal Dave"
           units.owner_name_b: "Dave Achal Dave"        →  "Achal Dave"
           users.full_name is "Jinal Achal Dave" — already correct, not touched.

Collections updated:
  units      (building-scoped)  — owner_name, owner_name_b
  users      (global)           — full_name, email  (TH084 only)

Idempotent: each field is checked against the desired correct value before
writing. Safe to re-run.

Run:
    cd backend && python3 scripts/migrations/migration_007_fix_owner_names_th084_th085.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME")

BUILDING_ID = "13195"


async def _set_if_wrong(collection, query, field, correct_value, now, label):
    """Update a single field only if it does not already equal correct_value."""
    doc = await collection.find_one(query, {field: 1})
    if doc is None:
        print(f"  ERROR: document not found for {label}")
        return False
    current = doc.get(field, "")
    if current == correct_value:
        print(f"  {label}.{field}: already correct ({correct_value!r}) — skipped")
        return True
    result = await collection.update_one(query, {"$set": {field: correct_value, "updated_at": now}})
    print(f"  {label}.{field}: {current!r} → {correct_value!r} ({result.modified_count} updated)")
    return True


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_007_fix_owner_names_th084_th085.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()

    # ── TH084 ────────────────────────────────────────────────────────────────
    print("\n--- TH084 ---")

    # Find the canonical user via user_units
    mapping = await db.user_units.find_one(
        {"building_id": BUILDING_ID, "unit_number": "TH084",
         "role_at_unit": "owner", "is_active": True},
        {"user_id": 1},
    )
    if mapping and mapping.get("user_id"):
        user_q = {"id": mapping["user_id"]}
        await _set_if_wrong(db.users, user_q, "full_name", "Shouvik Aditya", now, "TH084 user")
        # email not changed here — see migration_008 which deletes the scraped
        # ghost account and activates the owner's real registered account
    else:
        print("  WARNING: no active owner mapping in user_units — users table not updated")

    # units.owner_name for TH084 was already correct in the DB before this migration ran.
    unit_q84 = {"building_id": BUILDING_ID, "unit_number": "TH084"}
    await _set_if_wrong(db.units, unit_q84, "owner_name", "Shouvik Aditya", now, "TH084 units")

    # ── TH085 ────────────────────────────────────────────────────────────────
    print("\n--- TH085 ---")

    unit_q85 = {"building_id": BUILDING_ID, "unit_number": "TH085"}
    await _set_if_wrong(db.units, unit_q85, "owner_name", "Jinal Achal Dave", now, "TH085 units")
    await _set_if_wrong(db.units, unit_q85, "owner_name_b", "Achal Dave", now, "TH085 units")

    # users.full_name for TH085 user is "Jinal Achal Dave" — correct, not touched.
    mapping85 = await db.user_units.find_one(
        {"building_id": BUILDING_ID, "unit_number": "TH085",
         "role_at_unit": "owner", "is_active": True},
        {"user_id": 1},
    )
    if mapping85 and mapping85.get("user_id"):
        user85 = await db.users.find_one({"id": mapping85["user_id"]}, {"full_name": 1, "email": 1})
        print(f"  TH085 user (unchanged): full_name={user85.get('full_name')!r}  email={user85.get('email')!r}")

    print("\nMigration complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
