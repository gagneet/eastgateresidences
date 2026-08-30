"""Regression test for scripts/east_gate_convert_consolidated_financials_to_gap_import_csv.py.

Runs against the REAL source file (docs/finances/up13195_consolidated_financials-2021-2026.csv),
not a synthetic fixture — the thing that matters here is that the converter's column mapping
still matches that specific file's actual shape, not an idealised one. If the source file's
columns are ever renamed, this test should fail loudly rather than the conversion silently
producing zero rows (both `year` and `fund` blank/unrecognised are skipped, not raised).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.east_gate_convert_consolidated_financials_to_gap_import_csv import OUTPUT_COLUMNS, convert
from services.onboarding.gap_tolerant_financial_import import parse_rows

SOURCE = Path(__file__).resolve().parents[2] / "docs" / "finances" / "up13195_consolidated_financials-2021-2026.csv"


@pytest.mark.skipif(not SOURCE.exists(), reason="source financials CSV not present in this checkout")
class TestConvertConsolidatedFinancialsCsv:
    def test_produces_fund_and_category_rows(self):
        rows = convert(SOURCE)
        fund_rows = [r for r in rows if not r["category_name"]]
        category_rows = [r for r in rows if r["category_name"]]
        assert len(fund_rows) == 12  # 6 years x 2 funds, ANNUAL FUND SUMMARY record_class
        # CATEGORY BUDGET VS ACTUAL has 270 source rows. The converter merges them by
        # (year, fund, category), preserving proposed-only budget rows while avoiding the
        # old blank-actual overwrite problem for categories that also have real spend.
        assert len(category_rows) == 262

    def test_no_duplicate_category_keys(self):
        """Regression guard for the overwrite bug above — every (year, fund_type,
        category_name) key must appear at most once in the converter's output, or a
        later row will silently blank out an earlier real value on upload."""
        rows = convert(SOURCE)
        keys = [(r["year"], r["fund_type"], r["category_name"]) for r in rows if r["category_name"]]
        assert len(keys) == len(set(keys))

    def test_merges_budget_and_actual_category_rows(self):
        rows = convert(SOURCE)
        row = next(
            (r for r in rows if r["year"] == "2022" and r["fund_type"] == "admin" and r["category_name"] == "Insurance premium"),
            None,
        )
        assert row is not None
        assert row["category_proposed_amount"] == "41000"
        assert row["category_actual_amount"] == "43662.26"  # the real actual, not blanked to ""

    def test_preserves_proposed_only_category_rows(self):
        rows = convert(SOURCE)
        row = next(
            (r for r in rows if r["year"] == "2021" and r["fund_type"] == "admin" and r["category_name"] == "Caretaker"),
            None,
        )
        assert row is not None
        assert row["category_proposed_amount"] == "27000"
        assert row["category_actual_amount"] == ""

    def test_fund_type_normalised_to_admin_sinking(self):
        rows = convert(SOURCE)
        fund_types = {r["fund_type"] for r in rows}
        assert fund_types == {"admin", "sinking"}

    def test_2026_admin_proposed_amount_matches_known_figure(self):
        """CLAUDE.md's own documented FY2026 figure: admin levy $309,882 — sourced from
        proposed_levy_ex_gst (NOT proposed_total_income; NOT the GST-inclusive $340,870.02 also
        present in the source). For 2026 specifically, proposed_levy_ex_gst and
        proposed_total_income happen to share the same value, so this assertion alone doesn't
        prove which column was actually read — see test_proposed_amount_uses_ex_gst_not_total_income
        for a year where they differ."""
        rows = convert(SOURCE)
        row = next(r for r in rows if r["year"] == "2026" and r["fund_type"] == "admin" and not r["category_name"])
        assert row["proposed_amount"] == "309882"

    def test_proposed_amount_uses_ex_gst_not_total_income(self):
        """Regression guard for the GST double-counting bug found running this pipeline live:
        proposed_total_income was previously fed into budget_levy_generator.py's ex-GST input,
        which then applied GST on top, inflating generated credits by ~$182,666 across the
        range. 2022 admin has proposed_levy_ex_gst=251900 but proposed_total_income=254904.27 —
        distinct values, so this year proves which column is actually used."""
        rows = convert(SOURCE)
        row = next(r for r in rows if r["year"] == "2022" and r["fund_type"] == "admin" and not r["category_name"])
        assert row["proposed_amount"] == "251900"
        assert row["proposed_amount"] != "254904.27"

    def test_annual_control_figures_match_known_reconciled_table(self):
        """Cross-checks the converter's new opening_balance/actual_income/actual_spent/
        closing_balance output against the independently-verified 2022 Admin figures (external
        Finance review's own reconciliation table): opening $23,374.75, income $248,970.54,
        spent $216,901.65, closing $55,443.64 — these four numbers balance exactly
        (23374.75 + 248970.54 - 216901.65 = 55443.64), confirming the columns are correctly
        mapped, not just present."""
        rows = convert(SOURCE)
        row = next(r for r in rows if r["year"] == "2022" and r["fund_type"] == "admin" and not r["category_name"])
        assert row["opening_balance"] == "23374.75"
        assert row["actual_income"] == "248970.54"
        assert row["actual_spent"] == "216901.65"
        assert row["closing_balance"] == "55443.64"

    def test_control_balance_as_of_date_captured(self):
        rows = convert(SOURCE)
        row = next(r for r in rows if r["year"] == "2022" and r["fund_type"] == "admin" and not r["category_name"])
        assert row["control_balance_as_of_date"] == "2022-12-31"

    def test_expense_ledger_cutoff_date_is_latest_category_as_of_date_not_year_end(self):
        """expense_ledger_cutoff_date is deliberately NOT the same as control_balance_as_of_date
        — it's derived from category-row as_of_date (an AGM/meeting date for this source file,
        a converter-level approximation, not necessarily a true ledger cutoff) and can fall in
        the FOLLOWING calendar year, which is exactly the 2021 case (AGM held 17 Feb 2022)."""
        rows = convert(SOURCE)
        row = next(r for r in rows if r["year"] == "2021" and r["fund_type"] == "admin" and not r["category_name"])
        assert row["expense_ledger_cutoff_date"] == "2022-02-17"
        assert row["expense_ledger_cutoff_date"] != row["control_balance_as_of_date"]

    def test_2026_partial_period_cutoff_matches_known_figure(self):
        """The 2026 category detail is only complete through 8 April 2026, while the closing
        balance is dated 26 July 2026 — the exact partial-period mismatch the Finance review
        flagged. Confirms the converter surfaces both dates so downstream reconciliation can
        detect this generically (Part A4), not specific to this one year in code."""
        rows = convert(SOURCE)
        row = next(r for r in rows if r["year"] == "2026" and r["fund_type"] == "admin" and not r["category_name"])
        assert row["expense_ledger_cutoff_date"] == "2026-04-08"
        assert row["control_balance_as_of_date"] == "2026-07-26"

    def test_output_parses_cleanly_through_the_real_importer(self):
        """The converter's output must actually satisfy gap_tolerant_financial_import.py's
        own required-column check and row parsing — not just look right by eye."""
        rows = convert(SOURCE)
        header = ",".join(OUTPUT_COLUMNS) + "\n"
        body = "\n".join(
            ",".join(f'"{r[c]}"' for c in OUTPUT_COLUMNS)
            for r in rows
        )
        content = (header + body).encode("utf-8")
        parsed = parse_rows(content, "converted.csv")
        assert len(parsed) == len(rows)
