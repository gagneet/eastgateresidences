#!/usr/bin/env python3
# @featuretrace:owner-transfers — Backfill review requests for owner-name drift already present in imported snapshots.
# Layer: migration
# Data flow: strata_owners imported snapshots -> ownership_transfer_detection_service -> owner_transfer_requests (building-scoped).
# Related: backend/services/ownership_transfer_detection_service.py
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
    CO_OWNER_ADDITION_REASON,
    detect_and_create_portal_owner_transfer,
)


def _owner_names(owner: dict) -> str:
    """Return the raw imported owner field.

    strata_owners.owner is the imported primary owner from the external snapshot.
    Do not merge units.owner_name_b here; the detection service treats imported
    owner names as the exact owner set.
    """
    if owner.get("owner"):
        return owner["owner"]
    names = [owner.get("owner_name"), owner.get("owner_name_b")]
    return " & ".join([name for name in names if name])


async def run(
    building_id: str,
    apply: bool,
    unit_number: str | None = None,
    previous_owner_names: str | None = None,
) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/data_repair/create_owner_transfer_requests_from_imported_owner_drift.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}
    if unit_number:
        query["unit_number"] = unit_number

    owners = await db._db["strata_owners"].find(query, {"_id": 0}).sort("unit_number", 1).to_list(1000)
    now = datetime.now(timezone.utc).isoformat()
    results = []
    non_candidates = []
    # A pure owner-set addition is NOT a transfer (joint ownership is lawful), so the
    # detector never creates a request for one. Report them separately instead of
    # dropping them silently — they still need action, just a co-owner link rather
    # than a transfer (see link_missing_co_owners / link_missing_co_owners_from_import).
    co_owner_additions = []
    for owner in owners:
        un = owner.get("unit_number")
        imported_names = _owner_names(owner)
        if not un or not imported_names:
            continue
        result = await detect_and_create_portal_owner_transfer(
            db,
            building_id,
            un,
            imported_names,
            detected_at=now,
            source="imported_owner_snapshot_backfill",
            dry_run=not apply,
            previous_owner_names=previous_owner_names,
            # Baseline from the serving store (Postgres once promoted), not Mongo alone.
            use_cutover_baseline=True,
        )
        if result.get("created") or result.get("would_create") or result.get("reason") == "pending_request_exists":
            results.append(result)
        elif result.get("reason") == CO_OWNER_ADDITION_REASON:
            co_owner_additions.append(result)
        elif unit_number:
            # Single-unit dry-runs are usually operator diagnostics. Preserve
            # the detector's reason and compared owner sets so a zero-candidate
            # result explains whether the baseline already matches the import
            # or whether an explicit --previous-owner-names override is needed.
            non_candidates.append(result)

    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "previous_owner_names_override": previous_owner_names,
        "apply": apply,
        "scanned": len(owners),
        "candidates": len(results),
        "co_owner_additions": co_owner_additions,
        "non_candidates": non_candidates,
        "results": results,
    }


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/data_repair/create_owner_transfer_requests_from_imported_owner_drift.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description="Create owner-transfer review requests for imported owner-name drift already in strata_owners."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. TH078")
    parser.add_argument(
        "--previous-owner-names",
        help=(
            "Optional confirmed pre-import owner names, e.g. 'Olivia Rollings & Mark Raets'. "
            "Use only when the units/user_units baseline has already been overwritten."
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Write pending owner_transfer_requests. Default is dry-run.")
    args = parser.parse_args()

    result = asyncio.run(run(args.building_id, args.apply, args.unit_number, args.previous_owner_names))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
