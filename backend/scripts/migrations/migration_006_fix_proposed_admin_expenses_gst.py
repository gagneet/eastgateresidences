"""
migration_006_fix_proposed_admin_expenses_gst.py

The annual_levies record for building 13195, year 2026 has
proposed_admin_expenses = 340870.2 (inc-GST billing total).
The correct value is 309882.0 (ex-GST AGM-resolved budget).

Root cause: A financial import run placed the per-UOE compatibility rate
(admin_levy_per_uoe_annual × 10000 = 340870.20 inc-GST) into the
proposed_admin_expenses field instead of the canonical ex-GST fund total.

Snapshot_annual_levies.py line 119 has always stored the correct 309882.0
value with a clear comment ("Canonical fund totals are ex-GST").

Run:
    cd backend && python3 scripts/migrations/migration_006_fix_proposed_admin_expenses_gst.py
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME")

CORRECT_ADMIN = 309882.0
CORRECT_SINKING = 90459.0
WRONG_ADMIN = 340870.2

FILTER = {"building_id": "13195", "year": "2026"}


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_006_fix_proposed_admin_expenses_gst.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    doc = await db.annual_levies.find_one(FILTER, {"proposed_admin_expenses": 1, "proposed_sinking_expenses": 1})
    if doc is None:
        print("ERROR: annual_levies record for building_id=13195, year=2026 not found.")
        client.close()
        return

    current_admin = doc.get("proposed_admin_expenses")
    current_sinking = doc.get("proposed_sinking_expenses")
    print(f"Current:  proposed_admin_expenses={current_admin}, proposed_sinking_expenses={current_sinking}")

    if abs((current_admin or 0) - CORRECT_ADMIN) < 0.01:
        print("proposed_admin_expenses is already correct (309882.0 ex-GST). No update needed.")
        client.close()
        return

    if abs((current_admin or 0) - WRONG_ADMIN) > 1.0:
        print(f"WARNING: unexpected proposed_admin_expenses value {current_admin} — not 340870.2. Aborting.")
        client.close()
        return

    result = await db.annual_levies.update_one(
        FILTER,
        {"$set": {
            "proposed_admin_expenses": CORRECT_ADMIN,
            "proposed_sinking_expenses": CORRECT_SINKING,
        }}
    )
    print(f"Updated {result.modified_count} document(s).")
    print(f"Set:      proposed_admin_expenses={CORRECT_ADMIN} (ex-GST), proposed_sinking_expenses={CORRECT_SINKING}")

    client.close()


if __name__ == "__main__":
    asyncio.run(run())
