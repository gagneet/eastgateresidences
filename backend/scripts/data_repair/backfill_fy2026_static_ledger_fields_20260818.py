"""
Backfill static per-lot fields on the current financial year's unit_levy_ledger
documents, lost to the GAP-FIN-068 dr_mongo_snapshot.py wholesale-replace bug (2026-08-18)
=============================================================================================
`backend/scripts/dr_mongo_snapshot.py`, run every 15 minutes by the live
`strataos-dr-mongo-snapshot.timer`, used `replace_one()` to wholesale-overwrite each
unit's `unit_levy_ledger` document with only the 4 balance fields it computes
(`total_levied`, `total_paid`, `net_balance`, `opening_balance`) plus its own snapshot
provenance -- silently deleting every OTHER field the document previously had, including
`uoe` and `lot_number` (both required by `finance.levy_kpi`'s per-lot loop; a lot with
`uoe <= 0` is skipped entirely) and `property_type`. Confirmed live: all 87 East Gate
FY2026 documents were reduced from ~21 fields to 9. Fixed at the source in
`dr_mongo_snapshot.py` (now uses a scoped `$set`, GAP-FIN-068) -- this script repairs the
already-damaged live documents that fix does not retroactively restore.

## What this backs up, and from where

`uoe` (unit entitlement), `lot_number`, and `property_type` are STRUCTURAL/master-data
fields for a lot -- they do not vary by financial year. Verified live before writing this
script (not assumed): for every one of East Gate's 87 units, these 3 fields are IDENTICAL
across all 5 real historical years (2021-2025) with zero mismatches and zero missing
values. That makes copying them from the most recent intact prior year (2025 by default)
a safe, well-evidenced backfill -- not an invented value.

## What this deliberately does NOT backfill

`admin_paid`, `sinking_paid`, `admin_opening`, `sinking_opening`, `admin_levied`,
`sinking_levied`, `admin_closing`, `sinking_closing`, `reconciled_at`,
`reconciliation_note`, `applied_payment_ids` are genuinely YEAR-SPECIFIC financial
figures -- copying them from 2025 would be actively wrong, not a repair. Per CLAUDE.md's
Financial Formula Verification rule, these need their own dedicated, evidence-based
reconstruction (from Postgres `finance.levy_items`/`finance.receipts` fund-joined sums,
or from the original onboarding pipeline's source documents) -- not invented here. This
gap is tracked in `tasks/GAP-FIN-068-...md` as still open after this script runs.

## Safety

- Idempotent and additive-only: for each field, only writes it if the TARGET document is
  currently missing it (`None`/absent) -- never overwrites an existing value, including a
  value this script itself wrote on a prior run. Safe to re-run.
- `$set` of exactly the 3 named fields per document -- never touches net_balance,
  total_levied, total_paid, opening_balance, or any dollar/financial field.
- Building-agnostic: `--building`/`--year`/`--source-year` are CLI arguments, never
  hardcoded (CLAUDE.md rule) -- `13195` below is only a usage example.
- Dry-run by default; `--apply` required to write.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/backfill_fy2026_static_ledger_fields_20260818.py \
        --building 13195 --year 2026                      # dry-run, reports what would change
    venv/bin/python3 scripts/data_repair/backfill_fy2026_static_ledger_fields_20260818.py \
        --building 13195 --year 2026 --apply               # write
    venv/bin/python3 scripts/data_repair/backfill_fy2026_static_ledger_fields_20260818.py \
        --building 13195 --year 2026 --source-year 2024 --apply   # explicit source year
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger("backfill_fy2026_static_ledger_fields")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_BACKFILL_FIELDS = ("uoe", "lot_number", "property_type")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--building", required=True, dest="building_id")
    p.add_argument("--year", required=True, help="Target financial year to repair (e.g. 2026)")
    p.add_argument(
        "--source-year", default=None,
        help="Financial year to copy uoe/lot_number/property_type FROM. "
             "Default: target year - 1.",
    )
    p.add_argument("--apply", action="store_true", default=False,
                    help="Actually write the missing fields. Default is a dry-run.")
    return p.parse_args()


async def _run(building_id: str, year: str, source_year: str, apply: bool) -> dict:
    from database import db

    source_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": source_year},
        {"_id": 0, "unit_number": 1, "uoe": 1, "lot_number": 1, "property_type": 1},
    ).to_list(500)
    source_by_unit = {d["unit_number"]: d for d in source_docs}

    target_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year},
        {"_id": 0, "unit_number": 1, "uoe": 1, "lot_number": 1, "property_type": 1},
    ).to_list(500)

    report = {
        "building_id": building_id, "year": year, "source_year": source_year,
        "apply": apply, "target_unit_count": len(target_docs),
        "source_unit_count": len(source_docs), "units_updated": [],
        # "missing_source_doc": no source document exists for this unit_number at all --
        # a real gap, worth flagging. "no_usable_value": a source document exists but the
        # remaining gap field(s) are empty there too (e.g. property_type="" in 2025) --
        # NOT an error, there is simply nothing real to copy; distinct from the above so a
        # routine idempotent re-run doesn't look like a problem in the log output.
        "units_missing_source_doc": [], "units_no_usable_value": [],
        "units_already_complete": [], "fields_written_count": 0,
    }

    for target in target_docs:
        unit_number = target["unit_number"]
        source = source_by_unit.get(unit_number)
        missing_fields = [f for f in _BACKFILL_FIELDS if not target.get(f)]

        if not missing_fields:
            report["units_already_complete"].append(unit_number)
            continue
        if source is None:
            report["units_missing_source_doc"].append(unit_number)
            continue

        set_doc = {}
        for field in missing_fields:
            source_value = source.get(field)
            if source_value:  # only backfill if the SOURCE actually has a real value
                set_doc[field] = source_value

        if not set_doc:
            report["units_no_usable_value"].append(unit_number)
            continue

        report["units_updated"].append({"unit_number": unit_number, "fields": set_doc})
        report["fields_written_count"] += len(set_doc)

        if apply:
            await db.unit_levy_ledger.update_one(
                {"building_id": building_id, "unit_number": unit_number, "year": year},
                {"$set": set_doc},
            )

    return report


async def _main() -> None:
    args = parse_args()
    source_year = args.source_year or str(int(args.year) - 1)

    report = await _run(args.building_id, args.year, source_year, args.apply)

    logger.info(
        "building=%s year=%s source_year=%s apply=%s -- target_units=%d source_units=%d "
        "already_complete=%d updated=%d no_usable_value=%d missing_source_doc=%d fields_written=%d",
        report["building_id"], report["year"], report["source_year"], report["apply"],
        report["target_unit_count"], report["source_unit_count"],
        len(report["units_already_complete"]), len(report["units_updated"]),
        len(report["units_no_usable_value"]), len(report["units_missing_source_doc"]),
        report["fields_written_count"],
    )
    if report["units_missing_source_doc"]:
        logger.warning(
            "%d unit(s) have NO source document at all for %s -- a real gap, not just an "
            "empty field: %s",
            len(report["units_missing_source_doc"]), report["source_year"],
            sorted(report["units_missing_source_doc"]),
        )
    if report["units_no_usable_value"]:
        logger.info(
            "%d unit(s) have a source document but no non-empty value for their remaining "
            "gap field(s) (e.g. property_type is empty in the source too) -- nothing real "
            "to copy, not an error: %s",
            len(report["units_no_usable_value"]), sorted(report["units_no_usable_value"]),
        )
    if not args.apply and report["units_updated"]:
        logger.info(
            "DRY RUN -- re-run with --apply to write. Sample of what would change: %s",
            report["units_updated"][:3],
        )


if __name__ == "__main__":
    asyncio.run(_main())
