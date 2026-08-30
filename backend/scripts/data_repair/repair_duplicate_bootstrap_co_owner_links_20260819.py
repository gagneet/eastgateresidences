#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that's the building it was written to fix. Already applied
# and fully resolved for 13195 (0 affected units remaining, re-running is a safe no-op) —
# kept as historical record and in case the same _ensure_bootstrap_owner_user email-dedup
# bug signature is ever found in another building's data.
# @featuretrace:owner-transfers — One-off repair for a same-day bootstrap bug: co-owners sharing one
# household email were collapsed into a single user_units link instead of two.
# Layer: migration
# Related: backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
#          backend/services/ownership_transfer_detection_service.py
"""
Repair units where bootstrap_initial_owner_links_20260819.py created TWO
user_units owner links pointing at the SAME user_id, because the pre-fix
version of `_ensure_bootstrap_owner_user` deduped on email alone. Several
East Gate units have genuine co-owners (owner_name / owner_name_b) sharing
one household contact email (owner_email == owner_email_b) — that is real
data, not an import error; the bug was in treating "same email" as "same
person".

For each affected unit this script:
  1. Verifies exactly one active primary owner link + one active non-primary
     link, both referencing the same user_id (the specific bug signature —
     refuses to touch anything that doesn't match exactly, to stay safe).
  2. Creates a distinct provisional user for the second name (owner_name_b),
     using the now-fixed dedup (email+name, not email alone).
  3. Repoints the non-primary user_units link at the new distinct user_id.
  4. Creates a membership row for the new user (mirrors create_initial_ownership_link).

Usage:
    python3 backend/scripts/data_repair/repair_duplicate_bootstrap_co_owner_links_20260819.py --building-id 13195
    python3 .../repair_duplicate_bootstrap_co_owner_links_20260819.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from services.ownership_transfer_detection_service import (  # noqa: E402
    _ensure_bootstrap_owner_user,
)


async def run(building_id: str, apply: bool) -> dict:
    raw_units = db._db["units"]
    raw_user_units = db._db["user_units"]
    raw_memberships = db._db["memberships"]

    units = await raw_units.find(
        {"building_id": building_id, "owner_name_b": {"$nin": [None, ""]}},
        {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1, "owner_email": 1, "owner_email_b": 1},
    ).to_list(1000)

    now = datetime.now(timezone.utc).isoformat()
    repaired, skipped = [], []

    for unit in units:
        un = unit["unit_number"]
        links = await raw_user_units.find(
            {"building_id": building_id, "unit_number": un, "role_at_unit": "owner", "is_active": True},
            {"_id": 0},
        ).to_list(10)

        if len(links) != 2:
            continue
        primary = next((l for l in links if l.get("is_primary")), None)
        secondary = next((l for l in links if not l.get("is_primary")), None)
        if not primary or not secondary:
            continue
        if primary["user_id"] != secondary["user_id"]:
            continue  # not the bug signature — already distinct, nothing to repair

        result = {
            "unit_number": un,
            "shared_user_id": primary["user_id"],
            "owner_name_b": unit.get("owner_name_b"),
        }

        if not apply:
            result["would_repair"] = True
            repaired.append(result)
            continue

        new_user = await _ensure_bootstrap_owner_user(
            db, building_id, un, unit["owner_name_b"], now, email=unit.get("owner_email_b"),
        )
        new_user_id = new_user["id"]

        await raw_user_units.update_one(
            {"id": secondary["id"], "building_id": building_id},
            {"$set": {"user_id": new_user_id, "updated_at": now}},
        )

        existing_membership = await raw_memberships.find_one(
            {"user_id": new_user_id, "building_id": building_id}, {"_id": 0}
        )
        if existing_membership:
            await raw_memberships.update_one(
                {"user_id": new_user_id, "building_id": building_id},
                {"$set": {"is_active": True, "updated_at": now}, "$addToSet": {"roles": "owner", "units": un}},
            )
        else:
            await raw_memberships.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "user_id": new_user_id,
                    "building_id": building_id,
                    "roles": ["owner"],
                    "is_active": True,
                    "is_primary": False,
                    "units": [un],
                    "created_at": now,
                    "updated_at": now,
                }
            )

        result["new_user_id"] = new_user_id
        result["repaired"] = True
        repaired.append(result)

    return {"building_id": building_id, "apply": apply, "scanned": len(units), "affected": len(repaired), "results": repaired}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair co-owner links wrongly merged by a same-email dedup bug.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id, args.apply))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
