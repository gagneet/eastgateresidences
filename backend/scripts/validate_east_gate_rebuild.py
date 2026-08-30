#!/usr/bin/env python3
"""Phase C — independent annual reconciliation validator for East Gate (13195).

Read-only. Checks each year/fund's annual_levies doc against its own declared figures:

    expected_closing_cents = opening_balance_cents + actual_income_cents - actual_spent_cents
    variance_cents = declared_closing_balance_cents - expected_closing_cents

This validator MUST NEVER query demo_bank_transactions or any other transaction-level
collection -- annual controls are validated against annual_levies/levy_categories alone,
entirely independent of synthetic or real transaction data (this is the core correction from
this session's plan: authoritative history and transaction simulation are never checked against
each other).

Statuses:
    reconciled                              -- variance is zero, all fields present
    partial                                 -- year is explicitly incomplete (e.g. 2026)
    reconciled_control_income_detail_pending -- income is a derived plug (2023); variance is
                                                zero by construction, but income lacks full
                                                category-level backing -- never auto-promotable
    missing_control                         -- a required field is absent and the year isn't
                                                marked partial
    basis_mismatch                          -- opening/closing bases differ without a recorded
                                                reconciliation_basis explaining the bridge
    category_detail_mismatch                -- category-level sum disagrees with the fund total

is_promotable() is the hard gate: any future automated process that might consider promoting a
record to a "fully reconciled" / postable state MUST call this first. A derived-income record
(reconciliation_status=reconciled_control_income_detail_pending, or
derived_amount_posting_allowed=False) always returns False here, regardless of variance.

Usage:
    cd backend
    venv/bin/python3 scripts/validate_east_gate_rebuild.py
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from pathlib import Path

import motor.motor_asyncio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / "backend" / ".env")

BUILDING_ID = "13195"
DOCS_DIR = ROOT / "docs" / "finances"
YEARS = list(range(2021, 2027))
FUND_KEYS = ("admin_fund", "sinking_fund")

NEVER_PROMOTABLE_STATUSES = {"reconciled_control_income_detail_pending"}


def is_promotable(fund_doc: dict) -> bool:
    """Hard gate -- call before ANY code considers treating a fund/year as fully reconciled
    or postable. Never returns True for a derived-income record, regardless of variance."""
    if fund_doc.get("reconciliation_status") in NEVER_PROMOTABLE_STATUSES:
        return False
    if fund_doc.get("derived_amount_posting_allowed") is False:
        return False
    return True


def _check_fund(year: int, fund_type: str, fund_doc: dict) -> dict:
    opening = fund_doc.get("opening_balance_cents")
    income = fund_doc.get("actual_income_cents")
    spent = fund_doc.get("actual_spent_cents")
    closing = fund_doc.get("closing_balance_cents")
    stored_status = fund_doc.get("reconciliation_status")

    result = {
        "year": year, "fund_type": fund_type,
        "opening_balance_cents": opening, "actual_income_cents": income,
        "actual_spent_cents": spent, "closing_balance_cents": closing,
        "reconciliation_status_stored": stored_status,
        "promotable": is_promotable(fund_doc),
    }

    if stored_status == "partial":
        result["status"] = "partial"
        result["variance_cents"] = None
        return result

    if any(v is None for v in (opening, income, spent, closing)):
        result["status"] = "missing_control"
        result["variance_cents"] = None
        return result

    expected_closing = opening + income - spent
    variance = closing - expected_closing
    result["expected_closing_cents"] = expected_closing
    result["variance_cents"] = variance

    if stored_status in NEVER_PROMOTABLE_STATUSES:
        result["status"] = stored_status
    elif variance != 0:
        result["status"] = "basis_mismatch" if fund_doc.get("reconciliation_basis") else "variance_unexplained"
    else:
        result["status"] = "reconciled"

    return result


def _category_totals_from_csv(year: int) -> dict[str, int]:
    """Independent cross-check: category CSV expense sums, per fund -- compared against
    annual_levies.actual_spent_cents. Still not touching Demo Bank -- this is category-level
    Mongo-source-of-truth evidence, the CSV that fed the import."""
    path = DOCS_DIR / f"eastgate_{year}_category_detail.csv"
    if not path.exists():
        return {}
    totals: dict[str, float] = {}
    fund_map = {"administrative": "admin", "sinking": "sinking"}
    with path.open() as f:
        for row in csv.DictReader(f):
            if row["section"] != "expense":
                continue
            fund_type = fund_map[row["fund"].strip().lower()]
            totals[fund_type] = totals.get(fund_type, 0.0) + float(row["actual_amount"])
    return {k: round(v * 100) for k, v in totals.items()}


async def main() -> int:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "strata_production")
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    results = []
    for year in YEARS:
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": str(year)})
        if doc is None:
            results.append({"year": year, "status": "missing_control", "note": "no annual_levies doc"})
            continue
        category_totals = _category_totals_from_csv(year)
        for fund_key, fund_type in (("admin_fund", "admin"), ("sinking_fund", "sinking")):
            fund_doc = doc.get(fund_key) or {}
            r = _check_fund(year, fund_type, fund_doc)
            csv_total = category_totals.get(fund_type)
            if csv_total is not None and r.get("actual_spent_cents") is not None:
                r["category_csv_total_cents"] = csv_total
                if csv_total != r["actual_spent_cents"] and r["status"] not in ("partial", "missing_control"):
                    r["status"] = "category_detail_mismatch"
            results.append(r)

    print(json.dumps(results, indent=2, sort_keys=True, default=str))

    non_reconciled = [
        r for r in results
        if r["status"] not in ("reconciled", "partial", "reconciled_control_income_detail_pending")
    ]
    print(f"\n{len(results)} fund/year checks run. {len(non_reconciled)} not cleanly reconciled/partial.", file=sys.stderr)
    for r in non_reconciled:
        print(f"  ATTENTION: {r['year']} {r['fund_type']}: {r['status']}", file=sys.stderr)

    return 1 if non_reconciled else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
