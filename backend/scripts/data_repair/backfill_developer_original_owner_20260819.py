#!/usr/bin/env python3
# ⚠️ EAST GATE RESIDENCES (Unit Plan 13195) ONLY — NOT building-agnostic. ⚠️
# The developer entity name/ACN/ABN/trust/POA details and the settlement dates below
# (DEVELOPER_ENTITY_KEY, DEFAULT_SETTLEMENT_DATE, UNIT_SETTLEMENT_OVERRIDES, and the
# full user_doc literal in _ensure_developer_user) are East Gate-specific real-world
# facts hardcoded into this script, not generic platform logic. Do NOT point this at
# another building's --building-id without first replacing every one of those values —
# doing so would attribute Cappello Developments No 6 Pty Ltd as the original owner of
# a different scheme, which would be factually wrong. If another building needs the
# same kind of historical-developer backfill, copy this file and substitute real data
# for that building — do not parameterize this one and reuse it as-is.
# @featuretrace:owner-transfers — Insert the Developer as the historical original registered
# proprietor for every East Gate lot, ending at settlement (per-unit), ahead of the current owner.
# Layer: migration
# Related: backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
#          tasks/archive/GAP-IDENTITY-OWNER-BOOTSTRAP-001-canonical-owner-bootstrap.md
"""
Every East Gate lot had a registered proprietor before it was sold to its
first owner: the developer. GAP-IDENTITY-OWNER-BOOTSTRAP-001's initial
bootstrap recorded `old_owners=[]` (no prior owner at all) for the units it
touched — factually incomplete. This script adds the missing predecessor
and corrects the current owner's `start_date` (previously the date the
bootstrap script happened to run, not the real settlement date) to the real
settlement date, per explicit user-supplied facts (2026-08-19):

  Developer: Cappello Developments No 6 Pty Ltd, ACN 609 763 153, as trustee
  of the Cappello Developments No 6 Unit Trust, ABN 94 736 035 588, under
  power of attorney registered number 0146073 dated 20 November 2018.
  Settlement date: 1 December 2020 for every unit EXCEPT TH087, settled
  16 December 2020.

For each of the 87 units:
  1. Ensure a single, shared Developer `users` record exists (created once,
     reused across all units — one legal entity, not one account per unit).
  2. Insert a Developer `user_units` row for that unit if one doesn't already
     exist: `role_at_unit="owner"`, `is_primary=True`, `is_active=False`,
     `start_date=None` (the strata plan registration date was not supplied —
     left unknown rather than guessed), `actual_end_date=<settlement date>`.
     `is_active=False` means this never appears in any "current owner" query
     — it is purely a historical record.
  3. If the unit has a current active owner link (whether from this task's
     bootstrap or pre-existing), correct its `start_date` to the real
     settlement date. Applies uniformly — user confirmed ownership has not
     changed since the original sale for any unit in this building, so
     "when this row was created in our system" was never a valid stand-in
     for "when the owner actually settled."

Idempotent: re-running skips units that already have a Developer row.

Usage:
    python3 backend/scripts/data_repair/backfill_developer_original_owner_20260819.py --building-id 13195
    python3 backend/scripts/data_repair/backfill_developer_original_owner_20260819.py --building-id 13195 --apply
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
from utils.auth import hash_password  # noqa: E402

DEVELOPER_ENTITY_KEY = "cappello_developments_no_6"
DEFAULT_SETTLEMENT_DATE = "2020-12-01"
UNIT_SETTLEMENT_OVERRIDES = {"TH087": "2020-12-16"}


async def _ensure_developer_user(building_id: str, now: str) -> dict:
    existing = await db._db["users"].find_one(
        {"building_id": building_id, "developer_entity_key": DEVELOPER_ENTITY_KEY}, {"_id": 0}
    )
    if existing:
        return existing

    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "email": f"developer+{DEVELOPER_ENTITY_KEY}@strataos.local",
        "full_name": "Cappello Developments No 6 Pty Ltd",
        "first_name": "",
        "last_name": "",
        "role": "owner",
        "building_id": building_id,
        "is_approved": False,
        "is_active": False,
        "status": "historical_original_owner",
        "requires_account_setup": False,
        "password_hash": hash_password(str(uuid.uuid4())),
        "is_internal_contact_email": True,
        "developer_entity_key": DEVELOPER_ENTITY_KEY,
        "entity_type": "company_as_trustee",
        "acn": "609 763 153",
        "trust_name": "Cappello Developments No 6 Unit Trust",
        "abn": "94 736 035 588",
        "power_of_attorney_number": "0146073",
        "power_of_attorney_date": "2018-11-20",
        "bootstrap_source": "developer_original_proprietor_backfill",
        "created_at": now,
        "updated_at": now,
    }
    await db._db["users"].insert_one(user_doc)
    return user_doc


async def run(building_id: str, apply: bool) -> dict:
    units = await db._db["units"].find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1}
    ).sort("unit_number", 1).to_list(1000)

    now = datetime.now(timezone.utc).isoformat()
    if apply:
        developer = await _ensure_developer_user(building_id, now)
        developer_id = developer["id"]
    else:
        # Dry-run must still resolve the real developer_id (read-only lookup, no write) —
        # otherwise the existence check below can never match and every dry-run reports
        # "would create 87" even after a real --apply has already run. Falls back to a
        # sentinel only on a truly fresh, never-applied building (no developer exists yet
        # to look up), where "would create for all N units" is in fact the correct preview.
        existing_developer = await db._db["users"].find_one(
            {"building_id": building_id, "developer_entity_key": DEVELOPER_ENTITY_KEY}, {"_id": 0, "id": 1}
        )
        developer_id = existing_developer["id"] if existing_developer else None

    developer_rows_created, developer_rows_skipped, start_dates_fixed = [], [], []
    audit_settlement_dates_fixed = []

    for unit in units:
        un = unit["unit_number"]
        settlement_date = UNIT_SETTLEMENT_OVERRIDES.get(un, DEFAULT_SETTLEMENT_DATE)

        existing_dev_row = None
        if developer_id:
            existing_dev_row = await db._db["user_units"].find_one(
                {"building_id": building_id, "unit_number": un, "user_id": developer_id, "role_at_unit": "owner"},
                {"_id": 0, "id": 1},
            )

        if existing_dev_row:
            developer_rows_skipped.append({"unit_number": un, "reason": "developer_row_exists"})
        else:
            if apply:
                await db._db["user_units"].insert_one(
                    {
                        "id": str(uuid.uuid4()),
                        "building_id": building_id,
                        "user_id": developer_id,
                        "unit_number": un,
                        "role_at_unit": "owner",
                        "is_primary": True,
                        "is_active": False,
                        "start_date": None,
                        "end_date": None,
                        "actual_end_date": settlement_date,
                        "ownership_period_type": "developer_original_proprietor",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            developer_rows_created.append({"unit_number": un, "settlement_date": settlement_date})

        current_owner_rows = await db._db["user_units"].find(
            {
                "building_id": building_id,
                "unit_number": un,
                "role_at_unit": "owner",
                "is_active": True,
            },
            {"_id": 0, "id": 1, "user_id": 1},
        ).to_list(10)
        for row in current_owner_rows:
            if apply:
                await db._db["user_units"].update_one(
                    {"id": row["id"], "building_id": building_id},
                    {"$set": {"start_date": settlement_date, "updated_at": now}},
                )
            start_dates_fixed.append({"unit_number": un, "user_id": row["user_id"], "settlement_date": settlement_date})

        # bootstrap_initial_owner_links_20260819.py stamped settlement_date on its audit
        # trail as the date IT ran (the real date wasn't known yet at that point) — correct
        # it now to the real settlement date for consistency with the start_date fix above.
        # Read-only find_one first so dry-run's count is accurate rather than always "N".
        bootstrap_audit = await db._db["owner_transfer_requests"].find_one(
            {"building_id": building_id, "unit_number": un, "source": "initial_ownership_bootstrap"},
            {"_id": 0, "id": 1, "settlement_date": 1},
        )
        if bootstrap_audit and bootstrap_audit.get("settlement_date") != settlement_date:
            if apply:
                await db._db["owner_transfer_requests"].update_one(
                    {"id": bootstrap_audit["id"], "building_id": building_id},
                    {"$set": {"settlement_date": settlement_date, "updated_at": now}},
                )
            audit_settlement_dates_fixed.append({"unit_number": un, "settlement_date": settlement_date})

    return {
        "building_id": building_id,
        "apply": apply,
        "scanned_units": len(units),
        "developer_id": developer_id,
        "developer_rows_created": len(developer_rows_created),
        "developer_rows_skipped": len(developer_rows_skipped),
        "start_dates_fixed": len(start_dates_fixed),
        "audit_settlement_dates_fixed": len(audit_settlement_dates_fixed),
        "created_detail": developer_rows_created,
        "skipped_detail": developer_rows_skipped,
        "start_dates_fixed_detail": start_dates_fixed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill the Developer as historical original owner + correct current-owner start_date."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true", help="Write records. Default is dry-run.")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id, args.apply))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
