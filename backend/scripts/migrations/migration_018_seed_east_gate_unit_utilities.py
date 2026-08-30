"""
Migration 018 — Seed unit_utilities collection from East Gate static meter data.

Reads the embedded static maps in routers/utilities.py (_ELECTRICITY_LOC, _GAS_METERS, _NBN,
_unit_to_lot, _build_utilities) and inserts one document per unit into `unit_utilities` for
building_id="13195" (East Gate Residences, Units Plan 13195).

This migration is the prerequisite for removing the `if building_id == "13195":` branch from
the utilities router (audit Step 4b, finding R1). After this migration runs, the router can
use `unit_utilities` for ALL buildings and the static-map branch can be deleted.

Upsert semantics: existing documents are updated (not replaced) to remain idempotent.

Usage:
    cd backend
    python3 scripts/migrations/migration_018_seed_east_gate_unit_utilities.py           # dry-run
    python3 scripts/migrations/migration_018_seed_east_gate_unit_utilities.py --live     # write to DB
"""

import asyncio
import os
import sys

DRY_RUN = "--live" not in sys.argv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from motor.motor_asyncio import AsyncIOMotorClient
from routers.utilities import _unit_to_lot, _build_utilities

BUILDING_ID = "13195"

# All East Gate unit numbers: UA001–UA070 (apartments) + TH071–TH087 (townhouses)
EAST_GATE_UNITS = (
        [f"UA{i:03d}" for i in range(1, 71)] +
        [f"TH{i:03d}" for i in range(71, 88)]
)


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_018_seed_east_gate_unit_utilities.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "strata_management")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    collection = db["unit_utilities"]

    upserted = 0
    modified = 0
    skipped = 0

    print(f"{'DRY RUN — ' if DRY_RUN else ''}Seeding unit_utilities for building_id={BUILDING_ID!r}")
    print(f"Units: {len(EAST_GATE_UNITS)} ({EAST_GATE_UNITS[0]} → {EAST_GATE_UNITS[-1]})")
    print()

    for unit_number in EAST_GATE_UNITS:
        lot = _unit_to_lot(unit_number)
        if lot is None:
            print(f"  WARN: {unit_number} — _unit_to_lot returned None; skipping")
            skipped += 1
            continue

        utilities = _build_utilities(unit_number, lot)
        # Pydantic v1 .dict() / v2 .model_dump() — try both
        try:
            doc = utilities.model_dump()
        except AttributeError:
            doc = utilities.dict()

        doc["building_id"] = BUILDING_ID
        doc["unit_number"] = unit_number  # already present but ensure exact case

        # Check what's already stored
        existing = await collection.find_one(
            {"building_id": BUILDING_ID, "unit_number": unit_number},
            {"_id": 0, "electricity": 1}
        )
        status = "UPDATE" if existing else "INSERT"

        if DRY_RUN:
            loc_id = doc.get("electricity", {}).get("loc_id", "N/A")
            nmi = doc.get("nbn", {}).get("nmi", "N/A")
            has_gas = doc.get("gas", {}).get("has_gas", False)
            print(
                f"  [{status}] {unit_number} lot={lot:2d}  elec={loc_id}  nbn={nmi}  gas={'yes' if has_gas else 'no'}")
        else:
            result = await collection.update_one(
                {"building_id": BUILDING_ID, "unit_number": unit_number},
                {"$set": doc},
                upsert=True,
            )
            if result.upserted_id:
                upserted += 1
            elif result.modified_count:
                modified += 1

    if DRY_RUN:
        print(f"\nDRY RUN complete. {len(EAST_GATE_UNITS) - skipped} documents would be written.")
        print("Re-run with --live to apply.")
    else:
        print(f"\nDone. inserted={upserted}  updated={modified}  skipped={skipped}")
        total = await collection.count_documents({"building_id": BUILDING_ID})
        print(f"unit_utilities documents for {BUILDING_ID}: {total}")

    client.close()


if __name__ == "__main__":
    asyncio.run(run())
