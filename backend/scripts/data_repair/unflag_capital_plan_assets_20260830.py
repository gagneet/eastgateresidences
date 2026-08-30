#!/usr/bin/env python3
# @featuretrace:levy-fairness — an asset a production capital plan depends on is not test data.
# Layer: script
# Data flow: capital_replacement_schedule (production rows) -> building_assets.is_test_data
#            cleared where a production plan row references the asset (building-scoped).
# Related: backend/services/levy_fairness_service.py
#          backend/scripts/data_repair/repair_cross_year_allocations.py
# Tests: tests/backend/test_unflag_capital_plan_assets.py
"""Clear `is_test_data` on assets that a production capital works plan references.

DRY-RUN BY DEFAULT. `--apply` mutates production records.

THE DEFECT
----------
`building_assets` rows carry `is_test_data`, and the fairness engine's asset read filters
them out — correctly, since a test asset must never influence a real apportionment. The
capital works plan (`capital_replacement_schedule`) references assets by `asset_id` and is
NOT test data.

When a production plan row points at a filtered asset, the reference dangles. The engine
cannot resolve which lots the work serves, so the item falls back to an entitlement split
across every lot in the scheme — including lots the work does not touch. It is silent: no
error, no warning, and the resulting number looks exactly like a decision somebody made.

East Gate has two, and the first is the largest single line in its ten-year plan:

    asset-lift-motor-a   Lift Motor A          $247,611.94   2030
    asset-garage-motor-a Garage Door Motor A     $4,635.00   2027

Both were spreading across all 87 lots, including the 17 townhouses with no lift access.
The operator has confirmed both are genuine ten-year sinking fund items (2026-08-30).

THE RULE, NOT THE INSTANCE
--------------------------
This script does not name East Gate or those two asset ids. The criterion is general and
is the whole argument for automating it: **an asset that a production capital plan row
depends on cannot be test data.** One of the two facts is wrong, and the plan row is the
one with money attached to it and a year it falls due.

The inverse is deliberately NOT handled here. If a plan row is itself test data, the fix
is to flag or delete the plan row, which is a different decision with a different blast
radius — this script never touches `capital_replacement_schedule`.

WHAT IT DOES
------------
For every building (or one, with --building-id):
  1. Read production plan rows (`is_test_data` not True) and collect their `asset_id`s.
  2. Find `building_assets` rows with those ids that are flagged `is_test_data: True`.
  3. Unset the flag. Nothing else on the asset is touched, and no plan row is modified.

Idempotent: a second run finds nothing to do.

    python3 backend/scripts/data_repair/unflag_capital_plan_assets_20260830.py
    python3 backend/scripts/data_repair/unflag_capital_plan_assets_20260830.py --apply
    python3 backend/scripts/data_repair/unflag_capital_plan_assets_20260830.py --building-id 13195 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


async def find_candidates(db, building_id: str | None) -> list[dict]:
    """Assets flagged as test data that a production capital plan row depends on."""
    plan_filter: dict = {"is_test_data": {"$ne": True}}
    if building_id:
        plan_filter["building_id"] = building_id

    plan_rows = await db.capital_replacement_schedule.find(
        plan_filter, {"_id": 0, "asset_id": 1, "asset_name": 1,
                      "estimated_cost": 1, "replacement_year": 1, "building_id": 1},
    ).to_list(5000)

    # Keyed by (building_id, asset_id): the same asset id may legitimately exist in more
    # than one building, and unflagging one building's asset because another building's
    # plan references the same id would be a cross-tenant write.
    wanted: dict[tuple[str, str], list[dict]] = {}
    for row in plan_rows:
        aid = row.get("asset_id")
        bid = row.get("building_id")
        if not aid or not bid:
            continue
        wanted.setdefault((bid, aid), []).append(row)

    candidates = []
    for (bid, aid), rows in sorted(wanted.items()):
        asset = await db.building_assets.find_one(
            {"building_id": bid, "id": aid}, {"_id": 0, "id": 1, "name": 1, "is_test_data": 1},
        )
        if not asset or asset.get("is_test_data") is not True:
            continue
        candidates.append({
            "building_id": bid,
            "asset_id": aid,
            "asset_name": asset.get("name") or aid,
            "plan_rows": rows,
            "total_cost": sum(float(r.get("estimated_cost", 0) or 0) for r in rows),
        })
    return candidates


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", help="restrict to one building; default is every building")
    ap.add_argument("--apply", action="store_true", help="perform the update (default is dry-run)")
    args = ap.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    candidates = await find_candidates(db, args.building_id)
    if not candidates:
        print("Nothing to do: no production capital plan row references a test-flagged asset.")
        return 0

    print(f"{'APPLY' if args.apply else 'DRY-RUN'} — "
          f"{len(candidates)} asset(s) referenced by a production capital plan row\n")
    total = 0.0
    for c in candidates:
        total += c["total_cost"]
        print(f"  {c['building_id']}  {c['asset_id']:<28} {c['asset_name'][:34]:<34} "
              f"${c['total_cost']:>12,.2f}")
        for r in c["plan_rows"]:
            print(f"      plan row {r.get('replacement_year')}  "
                  f"{(r.get('asset_name') or '')[:40]:<40} ${float(r.get('estimated_cost', 0) or 0):>12,.2f}")
    print(f"\n  total capital value currently mis-attributed: ${total:,.2f}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to clear the flag.")
        return 0

    updated = 0
    for c in candidates:
        res = await db.building_assets.update_one(
            {"building_id": c["building_id"], "id": c["asset_id"]},
            {"$unset": {"is_test_data": ""}},
        )
        # "No exception" is not "changed a row" — assert the post-condition.
        if res.modified_count != 1:
            print(f"  WARNING: {c['building_id']}/{c['asset_id']} matched "
                  f"{res.matched_count}, modified {res.modified_count}")
            continue
        updated += 1
    print(f"\nCleared is_test_data on {updated} of {len(candidates)} asset(s).")
    return 0 if updated == len(candidates) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
