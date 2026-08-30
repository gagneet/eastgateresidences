#!/usr/bin/env python3
# NOTE — building-agnostic tool, but has only ever been run for East Gate Residences
# (Unit Plan 13195). --building-id defaults to "13195" as a convenience for this
# repo's one production building; nothing here needs changing to point it at another.
# @featuretrace:owner-transfers — Create pending invite records for owners bootstrapped by
# bootstrap_initial_owner_links_20260819.py. Does NOT send email — see send_owner_bootstrap_invites.py.
# Layer: migration
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
#          backend/scripts/send_owner_bootstrap_invites.py
#          tasks/archive/GAP-IDENTITY-OWNER-BOOTSTRAP-001-canonical-owner-bootstrap.md
"""
Create pending owner_invites records for every real-email account created by
the initial ownership bootstrap (GAP-IDENTITY-OWNER-BOOTSTRAP-001).

This is deliberately split from sending: creating the invite is a data step
(who is due an invite, decided now, from the emails we already have) while
sending is an explicit, separately-triggered action run only once the
platform/building is ready — see send_owner_bootstrap_invites.py, which
generates the actual short-lived token at send time so it can't go stale.

Skips internal-only accounts (email ending @strataos.local — these are the 7
units with no email on file; they need real contact info collected first,
there's nothing to invite yet).

Usage:
    python3 backend/scripts/data_repair/create_owner_bootstrap_invites_20260819.py --building-id 13195
    python3 backend/scripts/data_repair/create_owner_bootstrap_invites_20260819.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from services.ownership_transfer_detection_service import (  # noqa: E402
    INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE,
    create_owner_bootstrap_invite,
)


async def run(building_id: str, apply: bool) -> dict:
    raw_users = db._db["users"]
    candidates = await raw_users.find(
        {
            "building_id": building_id,
            "bootstrap_source": INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE,
            "is_internal_contact_email": False,
        },
        {"_id": 0, "id": 1, "unit_number": 1, "full_name": 1, "email": 1},
    ).to_list(1000)

    now = datetime.now(timezone.utc).isoformat()
    created, skipped = [], []

    for user in candidates:
        if not apply:
            created.append(
                {
                    "would_create": True,
                    "user_id": user["id"],
                    "unit_number": user.get("unit_number"),
                    "full_name": user.get("full_name"),
                    "email": user.get("email"),
                }
            )
            continue

        result = await create_owner_bootstrap_invite(
            db, building_id, user["id"], user.get("unit_number"), user.get("full_name"), user.get("email"), now,
        )
        result["unit_number"] = user.get("unit_number")
        result["email"] = user.get("email")
        if result.get("created"):
            created.append(result)
        else:
            skipped.append(result)

    return {
        "building_id": building_id,
        "apply": apply,
        "candidates": len(candidates),
        "created": len(created),
        "skipped": len(skipped),
        "results": created,
        "skipped_detail": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create pending owner invite records (does not send email).")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true", help="Write owner_invites rows. Default is dry-run.")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id, args.apply))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
