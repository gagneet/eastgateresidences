#!/usr/bin/env python3
# NOTE — building-agnostic tool, but has only ever been run for East Gate Residences
# (Unit Plan 13195). --building-id defaults to "13195" as a convenience for this
# repo's one production building, not a hardcoded assumption — the underlying
# create_initial_ownership_link() takes building_id as a required parameter and does
# not special-case East Gate. Safe to point at another --building-id; nothing here
# needs changing to do so.
# @featuretrace:owner-transfers — Bootstrap canonical user_units owner links for units that have a legacy
# owner_name/owner_email import but no prior transfer/drift-detected account.
# Layer: migration
# Data flow: units (legacy owner_name/owner_email) -> ownership_transfer_detection_service.create_initial_ownership_link
#            -> users / user_units / memberships / strata_owners / owner_transfer_requests (building-scoped).
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/create_owner_transfer_requests_from_imported_owner_drift.py
#          tasks/archive/GAP-IDENTITY-OWNER-BOOTSTRAP-001-canonical-owner-bootstrap.md
"""
Bootstrap canonical ownership for units with no active user_units owner link.

Why this exists
----------------
owner_service.py treats an active `user_units` row (role_at_unit="owner") as
the ONLY canonical source of owner-financial attribution. `units.owner_name`/
`owner_email` are an explicit legacy fallback (source="units_legacy",
owner_id=None) — not canonical. A live audit of building 13195 (2026-08-19)
found 77 of 87 units with a legacy owner_name but no active user_units link.

Neither existing mechanism can close this gap:
  - The drift detector (`detect_and_create_portal_owner_transfer`) treats
    `units.owner_name` as an already-valid current-owner baseline, so a
    re-scrape with the same name is seen as "no drift" and never creates a
    transfer request.
  - The manual staff transfer endpoint (`POST /owner-transfers`) requires an
    EXISTING user_units owner row to transfer from — it 404s otherwise.

This script calls `create_initial_ownership_link()`, whose only precondition
is "no active user_units owner link exists yet" (see that function's
docstring). It creates a provisional owner account (real email stored if the
legacy import had one, but the account stays inactive — no invite email is
sent by this script) and an audit-trailed, auto-approved
`owner_transfer_requests` row for every unit it touches.

Usage:
    # Dry run (default) — prints what would be created, no writes.
    python3 backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py \
        --building-id 13195

    # Single unit, applied.
    python3 backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py \
        --building-id 13195 --unit-number TH071 --apply

    # Full building, applied.
    python3 backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py \
        --building-id 13195 --apply
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
    create_initial_ownership_link,
    link_missing_co_owners,
)


async def run(building_id: str, apply: bool, unit_number: str | None = None) -> dict:
    query = {"building_id": building_id}
    if unit_number:
        query["unit_number"] = unit_number

    units = await db._db["units"].find(
        query,
        {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_email": 1, "owner_name_b": 1, "owner_email_b": 1},
    ).sort("unit_number", 1).to_list(1000)

    now = datetime.now(timezone.utc).isoformat()
    created, skipped, no_name, co_owner_linked = [], [], [], []
    co_owner_link_blocked = []

    for unit in units:
        un = unit.get("unit_number")
        names = [n for n in [unit.get("owner_name"), unit.get("owner_name_b")] if n]
        emails = [unit.get("owner_email"), unit.get("owner_email_b")]
        if not un or not names:
            no_name.append(un)
            continue

        result = await create_initial_ownership_link(
            db, building_id, un, names, emails, detected_at=now, dry_run=not apply,
        )
        result["unit_number"] = result.get("unit_number") or un
        if result.get("created") or result.get("would_create"):
            created.append(result)
            continue

        skipped.append(result)
        if result.get("reason") != "owner_already_canonical":
            continue
        # PARTIALLY-linked unit: create_initial_ownership_link refuses any unit that
        # already has an active owner link, so a unit whose primary owner was linked
        # earlier keeps a genuine second owner unlinked forever. That gap is what made
        # the drift detector mistake an imported co-owner for an incoming transferee.
        # Complete the link here instead — additive only, primary flag untouched.
        link_result = await link_missing_co_owners(
            db, building_id, un, names, emails, detected_at=now, dry_run=not apply,
        )
        link_result.setdefault("unit_number", un)
        if link_result.get("linked") or link_result.get("would_link"):
            co_owner_linked.append(link_result)
        elif link_result.get("reason") != "co_owners_already_linked":
            # e.g. an orphaned user_units link whose user row is gone — the co-owner
            # set can't be determined safely. Surface it; never guess.
            co_owner_link_blocked.append(link_result)

    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "apply": apply,
        "scanned": len(units),
        "bootstrapped": len(created),
        "co_owner_linked": len(co_owner_linked),
        "co_owner_linked_detail": co_owner_linked,
        "co_owner_link_blocked": co_owner_link_blocked,
        "skipped": len(skipped),
        "units_with_no_owner_name": no_name,
        "results": created,
        "skipped_detail": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap canonical user_units owner links for units with no active owner link."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. TH071")
    parser.add_argument("--apply", action="store_true", help="Write records. Default is dry-run.")
    args = parser.parse_args()

    result = asyncio.run(run(args.building_id, args.apply, args.unit_number))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
