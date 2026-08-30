#!/usr/bin/env python3
"""Import docs/finances/eastgate_rebuild_2021_2026_canonical.csv into Mongo
annual_levies/levy_categories for building_id 13195, via the existing gap-tolerant importer
(services.onboarding.gap_tolerant_financial_import.import_gap_tolerant_financial_data).

Also detects and reports (never silently deletes) any EXISTING levy_categories documents for
13195/2021-2026 whose (year, fund_type, name) key is absent from the new canonical set -- the
importer only upserts, it never removes a category no longer present in a corrected re-upload,
so a stale doc from a prior (now-superseded) import could otherwise linger unnoticed.

IMPORTANT: after --apply, always run
`backend/scripts/fix_east_gate_levy_income_field_semantics.py --apply` next -- this importer's
`proposed_amount` -> `levy_income` mapping otherwise overwrites the legacy `levy_income` field
with the proposed annual budget, which several consumers read as ACTUAL income (found in the
2026-07-31 post-rebuild audit; see that script's docstring for the full explanation).

Usage:
    python3 scripts/import_east_gate_rebuild_canonical.py            # dry-run (default)
    python3 scripts/import_east_gate_rebuild_canonical.py --apply    # actually import
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import motor.motor_asyncio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / "backend" / ".env")

from services.onboarding.gap_tolerant_financial_import import (  # noqa: E402
    import_gap_tolerant_financial_data,
    parse_rows,
)

BUILDING_ID = "13195"
CANONICAL_CSV = ROOT / "docs" / "finances" / "eastgate_rebuild_2021_2026_canonical.csv"
YEARS = list(range(2021, 2027))


async def _find_orphaned_categories(db, new_keys: set[tuple[str, str, str]]) -> list[dict]:
    orphaned = []
    async for doc in db.levy_categories.find({
        "building_id": BUILDING_ID, "year": {"$in": [str(y) for y in YEARS]},
        "is_archived": {"$ne": True},
    }):
        key = (str(doc.get("year")), str(doc.get("fund_type")), str(doc.get("name")))
        if key not in new_keys:
            orphaned.append({
                "id": doc.get("id"), "year": doc.get("year"), "fund_type": doc.get("fund_type"),
                "name": doc.get("name"), "actual_amount": doc.get("actual_amount"),
            })
    return orphaned


async def _archive_orphaned_categories(db, orphaned: list[dict]) -> int:
    """Never hard-delete -- mark superseded pre-rebuild category docs archived, matching this
    repo's general no-hard-delete posture. Explicitly scoped to building_id 13195 + the exact
    ids already identified as orphaned (not a broad filter re-applied at write time)."""
    if not orphaned:
        return 0
    now = datetime.now(timezone.utc)
    result = await db.levy_categories.update_many(
        {"building_id": BUILDING_ID, "id": {"$in": [o["id"] for o in orphaned]}},
        {"$set": {
            "is_archived": True,
            "archived_at": now,
            "archived_reason": (
                "Superseded by the 2021-2026 rebuild (docs/finances/reconcilation-2021-2022-finances.md "
                "and eastgate_20XX_category_detail.csv corrections) -- category name/value predates "
                "verification against primary source documents this session."
            ),
        }},
    )
    return result.modified_count


async def run(apply: bool) -> dict:
    content = CANONICAL_CSV.read_bytes()
    rows = parse_rows(content, CANONICAL_CSV.name)
    new_keys = {
        (str(r.get("year")), str(r.get("fund_type")).strip().lower(), str(r.get("category_name")).strip())
        for r in rows if str(r.get("category_name") or "").strip()
    }

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "strata_production")
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    orphaned = await _find_orphaned_categories(db, new_keys)

    if not apply:
        return {
            "dry_run": True,
            "rows_in_canonical_csv": len(rows),
            "distinct_category_keys": len(new_keys),
            "orphaned_categories_would_remain_stale": orphaned,
        }

    archived_count = await _archive_orphaned_categories(db, orphaned)

    result = await import_gap_tolerant_financial_data(
        db, building_id=BUILDING_ID, content=content, filename=CANONICAL_CSV.name,
        uploaded_by="east_gate_2021_2026_rebuild_script", is_test_data=False,
    )
    return {
        "dry_run": False, "import_result": result,
        "orphaned_categories_archived": archived_count,
        "orphaned_category_details": orphaned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import East Gate 2021-2026 canonical rebuild CSV.")
    parser.add_argument("--apply", action="store_true", help="Execute the import. Default is dry-run.")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.apply)), default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
