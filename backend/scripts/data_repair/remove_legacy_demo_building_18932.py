"""Hard-remove the Harbourview Residences (18932) legacy seed building.

Why a hard delete is appropriate here, despite the 7-year retention rule
-----------------------------------------------------------------------
CLAUDE.md forbids hard-deleting BUILDINGS because ACT/NSW require strata records to be
retained for seven years. That rule protects real strata records. Harbourview holds none:

    users            0        memberships      0
    levy_payments    0        documents        0
    demo_bank_transactions 0

It is a synthetic seed row (is_demo=True) created in the same second as Sierra
(2026-05-26T10:25:48Z) by a seeding run, with fabricated owner names and no user account
ever attached. There is no owner, no money and no legal record to retain, so the
retention rule has nothing to bite on. Verified 2026-08-20 before running.

Sierra (16244) is deliberately NOT touched — it is being kept for now.

    cd backend && python3 scripts/data_repair/remove_legacy_demo_building_18932.py --dry-run
    cd backend && python3 scripts/data_repair/remove_legacy_demo_building_18932.py --apply
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

TARGET = "18932"
TARGET_NAME = "Harbourview Residences"

# Buildings that must survive untouched. Guard rather than comment: a typo in TARGET
# should abort, not delete East Gate.
PROTECTED = {"13195", "16244", "DEMO-0001", "UP-DEMO-001", "UPDEMO5"}


def _match() -> dict:
    # plan_id is the legacy alias for building_id and some rows carry only it
    # (backend/database.py resolves them interchangeably), so both must be matched or
    # orphaned ledger rows survive the delete.
    return {"$or": [{"building_id": TARGET}, {"plan_id": TARGET}]}


async def main(apply: bool) -> int:
    if TARGET in PROTECTED:
        print(f"ABORT: {TARGET} is in the protected set.")
        return 2

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        building = await db.buildings.find_one({"building_id": TARGET}, {"_id": 0, "name": 1})
        if not building:
            print(f"{TARGET} is not present — nothing to do.")
            return 0
        if building.get("name") != TARGET_NAME:
            # The id and the name must agree, or this is not the building we audited.
            print(f"ABORT: {TARGET} is named {building.get('name')!r}, expected {TARGET_NAME!r}.")
            return 2

        # Refuse if a real person or real money ever became attached after the audit.
        for coll, label in (("users", "user"), ("memberships", "membership"),
                            ("levy_payments", "levy payment")):
            n = await db[coll].count_documents(_match())
            if n:
                print(f"ABORT: {n} {label} record(s) exist for {TARGET}. "
                      "This is no longer an empty seed building — do not hard delete.")
                return 2

        plan = []
        total = 0
        for cname in sorted(await db.list_collection_names()):
            try:
                n = await db[cname].count_documents(_match())
            except Exception:
                continue
            if n:
                plan.append((cname, n))
                total += n

        print(f"{TARGET} ({TARGET_NAME}) — {total} document(s) across {len(plan)} collection(s):")
        for cname, n in sorted(plan, key=lambda x: -x[1]):
            print(f"  {cname:38s} {n}")

        if not apply:
            print("\n--dry-run: nothing deleted. Re-run with --apply to remove.")
            return 0

        removed = 0
        for cname, _ in plan:
            res = await db[cname].delete_many(_match())
            removed += res.deleted_count
        print(f"\nDeleted {removed} document(s).")

        leftover = sum([await db[c].count_documents(_match()) for c, _ in plan])
        if leftover:
            print(f"WARNING: {leftover} document(s) still match. Re-run --dry-run.")
            return 1
        print("Verified: no documents remain for this building.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
