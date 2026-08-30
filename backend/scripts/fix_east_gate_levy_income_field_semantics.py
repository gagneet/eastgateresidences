#!/usr/bin/env python3
"""Fix a regression found during the post-rebuild audit (2026-07-31): the East Gate
2021-2026 canonical import (scripts/finances/build_eastgate_rebuild_canonical_csv.py +
backend/scripts/import_east_gate_rebuild_canonical.py) fed `proposed_amount` for every
year/fund, which `gap_tolerant_financial_import.py` maps to the LEGACY dollar-float field
`annual_levies.{fund}_fund.levy_income` -- overwriting it with the PROPOSED annual budget.

That field is documented (services/finance_calculation_registry.py's source_precedence,
services/bi_etl_service.py's own comment: "annual_levies.levy_income has been imported
inconsistently across sources and can contain proposed annual income") as ambiguous
codebase-wide -- but 5 consumers (bi_service.py, investor_intelligence.py,
building_stress_service.py, forecast_service.py, maintenance_intelligence_service.py) read it
as ACTUAL income, versus 1 (finance_calculation_registry.py) that treats it as a
de-prioritised proposed-budget fallback. Because East Gate's unit_levy_ledger is empty, the
"prefer ledger data" fallback path those consumers use never fires -- levy_income is the value
actually read for this building today.

This script restores levy_income to the verified actual (or, for 2023, derived) income figure
for 2021-2025, and CLEARS it (sets to None) for 2026, where no reliable actual/YTD figure
exists -- rather than leave either the wrong proposed-budget number or an unverified figure
from a since-superseded document (RECONCILED_LEDGER_NOTES.md's pre-2026-07-31 "$77,470.20 YTD
actual" claim does not match the source CSV, which leaves 2026 actual_income blank).

Idempotent -- safe to re-run any time after re-running the canonical import, since it always
sets the same verified values regardless of what the canonical import most recently wrote.

Usage:
    cd backend
    venv/bin/python3 scripts/fix_east_gate_levy_income_field_semantics.py            # dry-run
    venv/bin/python3 scripts/fix_east_gate_levy_income_field_semantics.py --apply
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

BUILDING_ID = "13195"

# Verified actual income (dollars) -- same authoritative source as actual_income_cents,
# already established and independently verified earlier this session.
VERIFIED_ACTUAL_INCOME = {
    2021: {"admin": 138488.27, "sinking": 0.0},
    2022: {"admin": 248970.54, "sinking": 55048.51},
    2023: {"admin": 246199.29, "sinking": 107971.06},  # derived_from_verified_roll_forward
    2024: {"admin": 225082.41, "sinking": 68738.48},
    2025: {"admin": 349652.23, "sinking": 117324.04},
}


async def run(apply: bool) -> dict:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "strata_production")
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    before = {}
    for year in list(VERIFIED_ACTUAL_INCOME) + [2026]:
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": str(year)})
        if doc:
            before[year] = {
                "admin": doc.get("admin_fund", {}).get("levy_income"),
                "sinking": doc.get("sinking_fund", {}).get("levy_income"),
            }

    if not apply:
        return {"dry_run": True, "current_levy_income_values": before, "would_set": {
            **{y: v for y, v in VERIFIED_ACTUAL_INCOME.items()},
            2026: {"admin": None, "sinking": None},
        }}

    now = datetime.now(timezone.utc)
    results = {}
    for year, funds in VERIFIED_ACTUAL_INCOME.items():
        basis = "derived_from_verified_roll_forward" if year == 2023 else "actual_income_verified_this_session"
        set_doc = {"updated_at": now}
        for fund_type, value in funds.items():
            set_doc[f"{fund_type}_fund.levy_income"] = value
            set_doc[f"{fund_type}_fund.levy_income_basis"] = basis
        r = await db.annual_levies.update_one({"building_id": BUILDING_ID, "year": str(year)}, {"$set": set_doc})
        results[year] = r.modified_count

    r2026 = await db.annual_levies.update_one(
        {"building_id": BUILDING_ID, "year": "2026"},
        {"$set": {
            "admin_fund.levy_income": None, "sinking_fund.levy_income": None,
            "admin_fund.levy_income_basis": "unknown_not_separable_from_expense_activity",
            "sinking_fund.levy_income_basis": "unknown_not_separable_from_expense_activity",
            "updated_at": now,
        }},
    )
    results[2026] = r2026.modified_count

    return {"dry_run": False, "before": before, "modified_counts": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the fix (default: dry-run, prints plan only).")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.apply)), default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
