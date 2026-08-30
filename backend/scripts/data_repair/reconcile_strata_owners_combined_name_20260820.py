#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is where the inconsistency was found.
# @featuretrace:owner-transfers — Reconcile a strata_owners row whose combined `owner`
# string contradicts its own split owner_name / owner_name_b fields.
# Layer: migration
# Data flow: strata_owners (owner vs owner_name/owner_name_b) + units (corroboration)
#            -> strata_owners.owner (building-scoped).
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/create_owner_transfer_requests_from_imported_owner_drift.py
"""
Correct a `strata_owners.owner` value that disagrees with its own split fields.

Why this matters
----------------
`strata_owners` holds the imported owner snapshot twice: `owner` is the combined
string ("A & B"), `owner_name` / `owner_name_b` the split form. The drift
detector reads the COMBINED field in preference (see the repair script's
`_owner_names()`), so a stale or junk value there is what the whole building's
owner-change detection is measured against — even when every other field, and
both datastores, say something else.

East Gate UA042 (found 2026-08-20): `owner` read the literal string
"Test Owner", while the same row's `owner_name`, `units.owner_name`, and
Postgres `core.ownership_periods` all said "Ms Sarah Marrapodi". That single
field made the detector propose transferring the unit to the person who already
owned it. "Test Owner" existed nowhere else in either datastore.

Safety
------
The split fields are only trusted after `units.owner_name` / `owner_name_b`
independently corroborate them. Where the two disagree, the row is reported and
skipped — this script never picks a winner between two conflicting sources, and
never invents a name. The previous value is preserved on the row as
`owner_corrected_from` so the change is auditable rather than silent.

Usage:
    # Dry run (default) — prints what would change, no writes.
    python3 backend/scripts/data_repair/reconcile_strata_owners_combined_name_20260820.py \
        --building-id 13195

    # Apply.
    python3 backend/scripts/data_repair/reconcile_strata_owners_combined_name_20260820.py \
        --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from services.ownership_transfer_detection_service import split_owner_names  # noqa: E402

COMBINED_NAME_JOINER = " & "


def _name_key(name: str | None) -> str:
    """Normalise an owner name for set comparison (mirrors the detection service)."""
    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    return re.sub(r"[^a-z0-9 ]+", "", text)


def _key_set(names: list[str]) -> set[str]:
    return {_name_key(name) for name in names if _name_key(name)}


def _split_fields(record: dict) -> list[str]:
    """The row's own split owner names, in primary-then-secondary order."""
    return [
        name.strip()
        for name in [record.get("owner_name"), record.get("owner_name_b")]
        if name and name.strip()
    ]


async def run(building_id: str, apply: bool, unit_number: str | None = None) -> dict:
    """Reconcile combined owner strings that contradict their own split fields."""
    query = {"building_id": building_id}
    if unit_number:
        query["unit_number"] = unit_number

    records = await db._db["strata_owners"].find(
        query, {"_id": 0, "unit_number": 1, "owner": 1, "owner_name": 1, "owner_name_b": 1}
    ).sort("unit_number", 1).to_list(1000)

    now = datetime.now(timezone.utc).isoformat()
    corrected, skipped = [], []

    for record in records:
        un = record.get("unit_number")
        combined = (record.get("owner") or "").strip()
        split = _split_fields(record)
        if not un or not combined or not split:
            continue
        if _key_set(split_owner_names(combined)) == _key_set(split):
            continue

        entry = {
            "unit_number": un,
            "current_combined_owner": combined,
            "split_owner_names": split,
        }

        unit = await db._db["units"].find_one(
            {"building_id": building_id, "unit_number": un},
            {"_id": 0, "owner_name": 1, "owner_name_b": 1},
        )
        unit_names = _split_fields(unit or {})
        if not unit_names:
            entry["reason"] = "no_corroborating_unit_owner_name"
            skipped.append(entry)
            continue
        if _key_set(unit_names) != _key_set(split):
            # Two sources disagree about who the owner is. Picking one here would
            # be a guess dressed up as a repair.
            entry["reason"] = "unit_owner_names_disagree_with_split_fields"
            entry["unit_owner_names"] = unit_names
            skipped.append(entry)
            continue

        entry["corroborated_by"] = "units.owner_name/owner_name_b"
        entry["new_combined_owner"] = COMBINED_NAME_JOINER.join(split)
        if apply:
            await db._db["strata_owners"].update_one(
                {"building_id": building_id, "unit_number": un},
                {
                    "$set": {
                        "owner": entry["new_combined_owner"],
                        # Keep the superseded value on the row: this is a correction
                        # with a trail, not a silent overwrite.
                        "owner_corrected_from": combined,
                        "owner_corrected_at": now,
                        "owner_corrected_by": "system:strata_owners_combined_name_repair",
                        "updated_at": now,
                    }
                },
            )
            entry["corrected"] = True
        else:
            entry["would_correct"] = True
        corrected.append(entry)

    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "apply": apply,
        "scanned": len(records),
        "corrected": corrected,
        "skipped_needs_manual_review": skipped,
    }


def main() -> int:
    """CLI entry point. Dry-run by default; --apply writes."""
    parser = argparse.ArgumentParser(
        description=(
            "Correct strata_owners.owner values that contradict the same row's "
            "owner_name / owner_name_b, where units.* corroborates the split fields."
        )
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. UA042")
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = parser.parse_args()

    result = asyncio.run(run(args.building_id, args.apply, args.unit_number))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
