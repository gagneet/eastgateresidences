#!/usr/bin/env python3
# @featuretrace:financial-onboarding — one-off converter: reshapes East Gate's
#   consolidated-financials source CSV into the generic column schema
#   services/onboarding/gap_tolerant_financial_import.py expects.
# Layer: script
# Data flow: docs/finances/up13195_consolidated_financials-2021-2026.csv (record_class="ANNUAL
#            FUND SUMMARY" | "CATEGORY BUDGET VS ACTUAL") -> this script -> a CSV matching
#            gap_tolerant_financial_import.py's schema (year, fund_type, proposed_amount,
#            closing_balance, opening_balance, actual_income, actual_spent,
#            control_balance_as_of_date, expense_ledger_cutoff_date, category_name,
#            category_proposed_amount, category_actual_amount) -> uploaded via
#            POST /financials/onboarding/building/{building_id}/import-financial-data.
# Related: backend/services/onboarding/gap_tolerant_financial_import.py
#          backend/routers/financial_onboarding.py
#          docs/finances/up13195_consolidated_financials-2021-2026.csv
"""Convert East Gate's consolidated-financials source CSV into the generic
gap-tolerant import format.

`docs/finances/up13195_consolidated_financials-2021-2026.csv` is a rich, East-Gate-specific
source document (7 record classes: annual fund summaries, category budget-vs-actual, arrears,
special levies, reconciliation issues, per-owner snapshots) with its own column names (`fund`,
`category`, `actual_spent`, `actual_closing_balance`, ...). It does NOT match
`gap_tolerant_financial_import.py`'s generic schema — the column names differ and the file
carries far more than the importer consumes. Uploading it directly via the Import & Generate
wizard or the API fails with "Missing required column(s): fund_type".

This script is deliberately East-Gate-specific (reads exactly this one source file, by design —
it does not belong in the generic importer, which stays building-agnostic) and reshapes only the
two record classes the generic importer's schema actually has fields for:

- `ANNUAL FUND SUMMARY` rows -> one fund-level row per (year, fund):
    - `proposed_amount` from `proposed_levy_ex_gst` — NOT `proposed_total_income`. A real bug
      found running this pipeline live: `proposed_total_income` is already a total figure, but
      `budget_levy_generator.py`'s `_proposed_ex_gst_total()` treats whatever this importer
      supplies as an EX-GST figure and applies GST on top — feeding it `proposed_total_income`
      double-counted GST (~$182,666 inflation across 2021-2026). `proposed_levy_ex_gst` is
      populated for every year in the source file and is the correct ex-GST basis the generator
      actually expects.
    - `closing_balance` from `actual_closing_balance`, `opening_balance` from
      `actual_opening_balance`, `actual_income`/`actual_spent` from the like-named source
      columns — all real, actual-cash figures, distinct from the ex-GST proposed budget figure
      above. These feed the direct annual-control reconciliation
      (`services/reconstruction_generators/annual_fund_reconciliation.py`), which never touches
      the synthetic generated transactions.
    - `control_balance_as_of_date` from this row's own `as_of_date` — the date the declared
      opening/closing balance figures are current to.
- `CATEGORY BUDGET VS ACTUAL` rows -> one category-level row per (year, fund, category):
    - `category_proposed_amount` from `proposed_expense_budget`.
    - `category_actual_amount` from `actual_spent`.
    - Also tracked (not per-row, aggregated below): the latest `as_of_date` across a year/fund's
      "Actual Category" rows becomes that year/fund's `expense_ledger_cutoff_date` — deliberately
      NOT the same field a generic building would supply directly. For East Gate specifically,
      `as_of_date` on category rows is the AGM/meeting date the figures were tabled at, not
      necessarily a true transaction-ledger cutoff — using it here is a converter-level, best-
      effort approximation for this one source document's shape. A building whose own source data
      states a real ledger cutoff explicitly should populate `expense_ledger_cutoff_date` from
      that field directly, not derive it from a meeting date.

The other 5 record classes (arrears, special levies, owner-level snapshots, reconciliation
issues) have no equivalent field in the generic importer's schema and are intentionally NOT
converted — they are a different, richer kind of data than "proposed budget + actual category
spend," and forcing them into this schema would lose information rather than preserve it. See
`GAP-FIN-025` for the tracked, larger source-fact-staging gap this reflects.

Usage:
    python3 scripts/east_gate_convert_consolidated_financials_to_gap_import_csv.py \\
        --source ../docs/finances/up13195_consolidated_financials-2021-2026.csv \\
        --output /tmp/13195_gap_import.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

FUND_MAP = {"administrative": "admin", "admin": "admin", "sinking": "sinking"}

OUTPUT_COLUMNS = [
    "year", "fund_type", "proposed_amount", "closing_balance",
    "opening_balance", "actual_income", "actual_spent",
    "control_balance_as_of_date", "expense_ledger_cutoff_date",
    "category_name", "category_proposed_amount", "category_actual_amount",
]


def _fund_type(raw: str) -> str | None:
    return FUND_MAP.get((raw or "").strip().lower())


def _parse_source_date(raw: str) -> datetime | None:
    """Source file uses DD/MM/YYYY (Australian date format)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y")
    except ValueError:
        return None


def convert(source_path: Path) -> list[dict[str, str]]:
    fund_rows: dict[tuple[str, str], dict[str, str]] = {}
    category_rows_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    category_cutoff_by_key: dict[tuple[str, str], datetime] = {}
    skipped_unmapped_fund = 0

    with source_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        record_class = (row.get("record_class") or "").strip()
        year = (row.get("year") or "").strip()
        fund_type = _fund_type(row.get("fund"))
        if not year or fund_type is None:
            if record_class in ("ANNUAL FUND SUMMARY", "CATEGORY BUDGET VS ACTUAL"):
                skipped_unmapped_fund += 1
            continue

        if record_class == "ANNUAL FUND SUMMARY":
            as_of = _parse_source_date(row.get("as_of_date"))
            fund_rows[(year, fund_type)] = {
                "year": year,
                "fund_type": fund_type,
                "proposed_amount": (row.get("proposed_levy_ex_gst") or "").strip(),
                "closing_balance": (row.get("actual_closing_balance") or "").strip(),
                "opening_balance": (row.get("actual_opening_balance") or "").strip(),
                "actual_income": (row.get("actual_income") or "").strip(),
                "actual_spent": (row.get("actual_spent") or "").strip(),
                "control_balance_as_of_date": as_of.date().isoformat() if as_of else "",
                "expense_ledger_cutoff_date": "",  # filled in below, from category rows
                "category_name": "",
                "category_proposed_amount": "",
                "category_actual_amount": "",
            }
        elif record_class == "CATEGORY BUDGET VS ACTUAL":
            category = (row.get("category") or "").strip()
            if not category:
                continue
            category_key = (year, fund_type, category)
            category_row = category_rows_by_key.setdefault(category_key, {
                "year": year,
                "fund_type": fund_type,
                "proposed_amount": "",
                "closing_balance": "",
                "opening_balance": "",
                "actual_income": "",
                "actual_spent": "",
                "control_balance_as_of_date": "",
                "expense_ledger_cutoff_date": "",
                "category_name": category,
                "category_proposed_amount": "",
                "category_actual_amount": "",
            })
            proposed_expense_budget = (row.get("proposed_expense_budget") or "").strip()
            actual_spent = (row.get("actual_spent") or "").strip()
            if proposed_expense_budget:
                category_row["category_proposed_amount"] = proposed_expense_budget
            if actual_spent:
                category_row["category_actual_amount"] = actual_spent
            as_of = _parse_source_date(row.get("as_of_date"))
            if as_of is not None and actual_spent:
                cutoff_key = (year, fund_type)
                if cutoff_key not in category_cutoff_by_key or as_of > category_cutoff_by_key[cutoff_key]:
                    category_cutoff_by_key[cutoff_key] = as_of

    for key, cutoff in category_cutoff_by_key.items():
        if key in fund_rows:
            fund_rows[key]["expense_ledger_cutoff_date"] = cutoff.date().isoformat()

    if skipped_unmapped_fund:
        print(
            f"WARNING: skipped {skipped_unmapped_fund} ANNUAL FUND SUMMARY/CATEGORY BUDGET VS "
            "ACTUAL row(s) with an unrecognised fund value (expected Administrative/Sinking).",
            file=sys.stderr,
        )

    return list(fund_rows.values()) + list(category_rows_by_key.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Source file not found: {args.source}", file=sys.stderr)
        return 1

    rows = convert(args.source)
    if not rows:
        print("No convertible rows found (no ANNUAL FUND SUMMARY / CATEGORY BUDGET VS ACTUAL rows).", file=sys.stderr)
        return 1

    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    fund_count = sum(1 for r in rows if r["category_name"] == "")
    category_count = len(rows) - fund_count
    print(f"Wrote {len(rows)} rows to {args.output} ({fund_count} fund-level, {category_count} category-level).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
