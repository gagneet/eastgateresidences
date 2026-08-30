"""Unit tests for migration_027_randomize_east_gate_demo_bank_levies.py::_proposed_ex_gst_total().

Regression coverage for a real bug caught 2026-07-31 by a user cross-checking generated levy
charge totals against docs/finances/up13195_consolidated_financials-2021-2026.csv before approving
a real posting batch: {fund}.levy_income (ACTUAL collected income) was being read as if it were
the PROPOSED ex-GST budget figure whenever {fund}.proposed_amount_cents was also present, because
levy_income was checked first. This overstated every East Gate year's admin/sinking levy charge
total where levy_income happened to be populated (confirmed: 2021-2025 admin, 2022-2023 sinking at
minimum) by the gap between actual collections and the proposed budget, then compounded by an
unwarranted second GST multiplication on top of that already-wrong base.
"""
from __future__ import annotations

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (
    _proposed_ex_gst_total,
)


class TestProposedAmountCentsTakesPriorityOverLevyIncome:
    def test_proposed_amount_cents_wins_when_both_present(self):
        """The exact real-world shape this bug lived in: both fields populated, with
        levy_income (actual collected cash) numerically close to but NOT equal to
        proposed_amount_cents (the real proposed/budgeted ex-GST figure)."""
        levy_doc = {
            "admin_fund": {
                "levy_income": 138488.27,  # actual collected income — must NOT be used
                "proposed_amount_cents": 13846000,  # $138,460.00 — the correct ex-GST figure
            }
        }
        value, source = _proposed_ex_gst_total(levy_doc, "admin")
        assert value == 138460.0
        assert source == "admin_fund.proposed_amount_cents"

    def test_levy_income_still_used_as_fallback_when_proposed_amount_cents_absent(self):
        """Preserves the pre-existing fallback behaviour for a year/building that genuinely
        has no proposed_amount_cents at all — levy_income remains a valid last-resort."""
        levy_doc = {"admin_fund": {"levy_income": 50000.0}}
        value, source = _proposed_ex_gst_total(levy_doc, "admin")
        assert value == 50000.0
        assert source == "admin_fund.levy_income"

    def test_top_level_proposed_admin_expenses_still_wins_over_both(self):
        """The original, oldest schema shape must remain the first-checked source —
        this fix only reordered the two branches BELOW it."""
        levy_doc = {
            "proposed_admin_expenses": 99999.0,
            "admin_fund": {"levy_income": 50000.0, "proposed_amount_cents": 4000000},
        }
        value, source = _proposed_ex_gst_total(levy_doc, "admin")
        assert value == 99999.0
        assert source == "proposed_admin_expenses"

    def test_sinking_fund_same_priority_order(self):
        levy_doc = {
            "sinking_fund": {"levy_income": 55048.51, "proposed_amount_cents": 9500000},
        }
        value, source = _proposed_ex_gst_total(levy_doc, "sinking")
        assert value == 95000.0
        assert source == "sinking_fund.proposed_amount_cents"

    def test_rate_times_uoe_fallback_unchanged_when_nothing_else_present(self):
        levy_doc = {"admin_levy_per_uoe_annual": 100.0, "total_uoe": 87.0}
        value, source = _proposed_ex_gst_total(levy_doc, "admin")
        assert value == 8700.0
        assert source == "admin_levy_per_uoe_annual*total_uoe"

    def test_real_east_gate_2021_2026_figures_match_the_source_csv_exactly(self):
        """Cross-checked against docs/finances/up13195_consolidated_financials-2021-2026.csv's
        own proposed_levy_ex_gst column for every East Gate year/fund — must match to the cent."""
        cases = [
            ({"admin_fund": {"levy_income": 138488.27, "proposed_amount_cents": 13846000}}, "admin", 138460.0),
            ({"admin_fund": {"levy_income": 248970.54, "proposed_amount_cents": 25190000}}, "admin", 251900.0),
            ({"sinking_fund": {"proposed_amount_cents": 9500000}}, "sinking", 95000.0),
            ({"admin_fund": {"levy_income": 246199.29, "proposed_amount_cents": 22131600}}, "admin", 221316.0),
            ({"sinking_fund": {"levy_income": 107971.06, "proposed_amount_cents": 6095800}}, "sinking", 60958.0),
            ({"admin_fund": {"levy_income": 225082.41, "proposed_amount_cents": 22349600}}, "admin", 223496.0),
            ({"sinking_fund": {"levy_income": 68738.48, "proposed_amount_cents": 7079500}}, "sinking", 70795.0),
            ({"admin_fund": {"levy_income": 349652.23, "proposed_amount_cents": 26207600}}, "admin", 262076.0),
            ({"sinking_fund": {"levy_income": 117324.04, "proposed_amount_cents": 9926200}}, "sinking", 99262.0),
            ({"admin_fund": {"proposed_amount_cents": 30988200}}, "admin", 309882.0),
            ({"sinking_fund": {"proposed_amount_cents": 9045900}}, "sinking", 90459.0),
        ]
        for levy_doc, fund, expected in cases:
            value, _source = _proposed_ex_gst_total(levy_doc, fund)
            assert value == expected, f"{levy_doc} / {fund}: expected {expected}, got {value}"
