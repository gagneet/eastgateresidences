"""
Tests for backend/domain/finance/formulas/collection.py and money.py.

Pure formula tests — no DB mocking needed (the whole point of the domain
layer). Edge cases per docs/architecture/financial-calculations-deep-dive.md
"Verification strategy > Formula tests": no levies, all paid, partial paid,
overpayment/credit, opening debt, negative adjustments, dollars/cents
conversion.
"""
from decimal import Decimal

import pytest

from domain.finance.formulas.collection import (
    cash_collection_percentage,
    current_year_collection_rate,
    due_date_collection_rate,
    historical_year_collection_rate,
    levy_collection_fraction,
    levy_collection_health_score,
    quarter_collection_fraction,
    quarterly_collection_rate,
)
from domain.finance.money import cents_to_percentage, clamp_non_negative, raw_percentage


class TestCentsToPercentage:
    def test_zero_denominator_returns_zero(self):
        assert cents_to_percentage(5000, 0) == Decimal(0)

    def test_negative_denominator_returns_zero(self):
        assert cents_to_percentage(5000, -100) == Decimal(0)

    def test_half_collected(self):
        assert cents_to_percentage(5000, 10000) == Decimal("50.00")

    def test_fully_collected(self):
        assert cents_to_percentage(10000, 10000) == Decimal("100.00")

    def test_over_100_percent_is_clamped(self):
        """Numerator exceeding denominator (e.g. a credit-adjusted collection) clamps to 100."""
        assert cents_to_percentage(15000, 10000) == Decimal("100.00")

    def test_negative_numerator_is_clamped_to_zero(self):
        assert cents_to_percentage(-500, 10000) == Decimal(0)

    def test_digits_param_defaults_to_2dp_unchanged(self):
        """Default digits=2 must reproduce every existing caller's behaviour exactly."""
        assert cents_to_percentage(5000, 15000) == Decimal("33.33")

    def test_digits_param_1dp(self):
        """GAP-FIN-016 Item C: finance.py's fund_health uses 1dp, not the 2dp default."""
        assert cents_to_percentage(5000, 15000, digits=1) == Decimal("33.3")

    def test_digits_param_still_clamps(self):
        assert cents_to_percentage(20000, 10000, digits=1) == Decimal("100.0")


class TestClampNonNegative:
    def test_positive_value_unchanged(self):
        assert clamp_non_negative(500) == 500

    def test_negative_value_clamped_to_zero(self):
        assert clamp_non_negative(-500) == 0

    def test_zero_unchanged(self):
        assert clamp_non_negative(0) == 0


class TestCurrentYearCollectionRate:
    def test_no_levies_no_opening_arrears_returns_zero(self):
        """Building with no levy raised yet — must not divide by zero."""
        result = current_year_collection_rate(
            opening_arrears_cents=0, levied_cents=0, outstanding_cents=0
        )
        assert result.collection_rate_pct == Decimal(0)
        assert result.total_obligations_cents == 0
        assert result.net_collected_cents == 0

    def test_all_paid_returns_100_percent(self):
        result = current_year_collection_rate(
            opening_arrears_cents=0, levied_cents=100000, outstanding_cents=0
        )
        assert result.collection_rate_pct == Decimal("100.00")

    def test_partial_paid(self):
        # $1000 levied, $400 outstanding -> $600 collected -> 60%
        result = current_year_collection_rate(
            opening_arrears_cents=0, levied_cents=100000, outstanding_cents=40000
        )
        assert result.collection_rate_pct == Decimal("60.00")
        assert result.total_obligations_cents == 100000
        assert result.net_collected_cents == 60000

    def test_opening_debt_included_in_denominator(self):
        # $200 opening arrears + $1000 levied = $1200 obligations; $300 outstanding -> $900/$1200 = 75%
        result = current_year_collection_rate(
            opening_arrears_cents=20000, levied_cents=100000, outstanding_cents=30000
        )
        assert result.total_obligations_cents == 120000
        assert result.collection_rate_pct == Decimal("75.00")

    def test_credit_outstanding_does_not_exceed_100_percent(self):
        """A unit in credit can make outstanding negative in the raw ledger sum —
        must not push collection rate above 100%."""
        result = current_year_collection_rate(
            opening_arrears_cents=0, levied_cents=100000, outstanding_cents=-5000
        )
        assert result.collection_rate_pct == Decimal("100.00")

    def test_reproduces_live_east_gate_value(self):
        """Verified against a live East Gate (13195) snapshot on 2026-07-12 —
        see tests/backend/fixtures/east_gate_2026_07_12_snapshot.json
        building_kpis.collection_rate == 86.36."""
        result = current_year_collection_rate(
            opening_arrears_cents=192803,  # $1928.03
            levied_cents=11009374,  # $110093.74
            outstanding_cents=1528425,  # $15284.25
        )
        assert result.collection_rate_pct == Decimal("86.36")

    def test_digits_param_reproduces_live_fund_health_1dp(self):
        """GAP-FIN-016 Item C (2026-07-21): finance.py::get_building_fund_overview's
        fund_health now delegates here with digits=1. Live-verified East Gate
        (13195) 2026 values: opening_arrears=$1928.04, levied=$110093.74,
        outstanding=$15284.25 -> fund_health=86.4 (matches the 86.36 2dp value
        above, rounded to 1dp — both are the SAME formula, different precision)."""
        result = current_year_collection_rate(
            opening_arrears_cents=192804, levied_cents=11009374, outstanding_cents=1528425,
            digits=1,
        )
        assert result.collection_rate_pct == Decimal("86.4")


class TestDueDateCollectionRate:
    """GAP-FIN-035 (2026-08-03): the canonical "Collection Rate" metric — a
    DIFFERENT metric from current_year_collection_rate()/fund_health above,
    which uses the FULL annual levy as its denominator. This one's denominator
    is only the amount due to date; callers must pass per-unit-clamped inputs
    (see finance_helpers.get_collection_rate_metrics)."""

    def test_zero_due_returns_zero(self):
        result = due_date_collection_rate(due_to_date_cents=0, collected_to_date_cents=0)
        assert result.collection_rate_pct == Decimal(0)

    def test_fully_collected_to_date(self):
        result = due_date_collection_rate(due_to_date_cents=100000, collected_to_date_cents=100000)
        assert result.collection_rate_pct == Decimal("100.0")

    def test_partial_collected_to_date(self):
        result = due_date_collection_rate(due_to_date_cents=100000, collected_to_date_cents=60000)
        assert result.collection_rate_pct == Decimal("60.0")

    def test_advance_payment_never_pushes_above_100_percent(self):
        """A caller that (incorrectly) passed an unclamped collected_to_date
        exceeding due_to_date must still not display over 100% — the domain
        formula clamps as defense in depth, even though the real fix is at
        the aggregation layer (per-unit clamping before this is ever called)."""
        result = due_date_collection_rate(due_to_date_cents=100000, collected_to_date_cents=150000)
        assert result.collection_rate_pct == Decimal("100.0")

    def test_one_unit_advance_payment_must_not_inflate_building_rate(self):
        """Direct regression for the reported bug: unit A owes $1000 (due
        today) and has paid $0; unit B has pre-paid $1000 for a period not
        yet due. A correct per-unit-clamped aggregation must show unit B's
        due_to_date/collected_to_date as $0/$0 (nothing of B's is due yet)
        and unit A's as $1000/$0 — building total 0% collected, not 50%."""
        # Unit A: due_to_date=$1000, collected_to_date=$0 (nothing paid).
        # Unit B: due_to_date=$0 (nothing due yet), collected_to_date=$0
        # (its $1000 advance is tracked as collected_in_advance, never here).
        total_due = 100000 + 0
        total_collected = 0 + 0
        result = due_date_collection_rate(due_to_date_cents=total_due, collected_to_date_cents=total_collected)
        assert result.collection_rate_pct == Decimal("0.0")


class TestRawPercentage:
    def test_zero_denominator_returns_configured_default(self):
        assert raw_percentage(5000, 0, digits=1, zero_denominator_value=Decimal(0)) == Decimal(0)
        assert raw_percentage(5000, 0, digits=1, zero_denominator_value=None) is None

    def test_negative_denominator_returns_configured_default(self):
        assert raw_percentage(5000, -100, zero_denominator_value=Decimal(0)) == Decimal(0)

    def test_not_clamped_above_100(self):
        """Unlike cents_to_percentage(), overpayment must show >100%, not clamp."""
        assert raw_percentage(15000, 10000, digits=1) == Decimal("150.0")

    def test_digits_precision_respected(self):
        assert raw_percentage(1, 3, digits=1) == Decimal("33.3")
        assert raw_percentage(1, 3, digits=2) == Decimal("33.33")


class TestQuarterlyCollectionRate:
    """GAP-FIN-016 Phase 2b: consolidates trust_phase1.py / bi_service.py /
    external_api.py's 3 verified-identical percentage-shape sites."""

    def test_reproduces_live_trust_phase1_value(self):
        """Verified 2026-07-21 against live East Gate (13195) trust_levy_schedules_v2
        data: raised=$110,093.75, received=$72,318.46 -> 65.7%."""
        rate = quarterly_collection_rate(
            paid_cents=7231846, levied_cents=11009375, digits=1,
            zero_denominator_value=Decimal("0.0"),
        )
        assert rate == Decimal("65.7")

    def test_zero_levied_returns_site_specific_default(self):
        # trust_phase1.py / external_api.py return 0.0 on no levy raised yet.
        assert quarterly_collection_rate(
            paid_cents=0, levied_cents=0, digits=1, zero_denominator_value=Decimal("0.0"),
        ) == Decimal("0.0")
        # bi_service.py returns None instead — a real "no data yet" distinction its
        # callers check for. Do not collapse this into the 0.0 sites' default.
        assert quarterly_collection_rate(
            paid_cents=0, levied_cents=0, digits=1, zero_denominator_value=None,
        ) is None

    def test_not_clamped_above_100_percent(self):
        """None of the 3 original sites clamped an overpaid/prepaid building's rate —
        this must not silently start clamping."""
        rate = quarterly_collection_rate(paid_cents=15000, levied_cents=10000, digits=1)
        assert rate == Decimal("150.0")

    def test_external_api_two_decimal_precision(self):
        rate = quarterly_collection_rate(
            paid_cents=2292204, levied_cents=40034100, digits=2,
            zero_denominator_value=Decimal("0.00"),
        )
        assert rate == Decimal("5.73")


class TestCashCollectionPercentage:
    def test_zero_total_revenue_returns_zero(self):
        assert cash_collection_percentage(
            cash_collected_cents=1000,
            total_revenue_cents=0,
        ) == Decimal(0)

    def test_half_collected(self):
        assert cash_collection_percentage(
            cash_collected_cents=5000,
            total_revenue_cents=10000,
        ) == Decimal("50.00")

    def test_over_revenue_is_not_clamped(self):
        assert cash_collection_percentage(
            cash_collected_cents=12500,
            total_revenue_cents=10000,
        ) == Decimal("125.00")


class TestQuarterCollectionFraction:
    """GAP-FIN-016 B2: get_levy_kpi()'s 0-1 quarter denominator rates."""

    def test_returns_zero_to_one_shape_not_display_percent(self):
        rate = quarter_collection_fraction(
            numerator_cents=875000,
            quarter_billed_cents=1000000,
        )
        assert rate == Decimal("0.8750")

    def test_zero_denominator_matches_get_levy_kpi_contract(self):
        assert quarter_collection_fraction(
            numerator_cents=1000,
            quarter_billed_cents=0,
        ) == Decimal(0)

    def test_overpaid_quarter_is_not_clamped(self):
        assert quarter_collection_fraction(
            numerator_cents=1250000,
            quarter_billed_cents=1000000,
        ) == Decimal("1.2500")

    def test_half_tie_uses_domain_decimal_rounding(self):
        # Old inline route code used Python float round(). The extracted domain
        # helper deliberately uses the same Decimal half-up rule as the rest of
        # domain.finance percentage helpers for exact half-tie ratios.
        assert quarter_collection_fraction(
            numerator_cents=1,
            quarter_billed_cents=32,
        ) == Decimal("0.0313")


class TestLevyCollectionHealthScore:
    def test_fraction_returns_zero_to_one_shape_for_bi_score_inputs(self):
        assert levy_collection_fraction(paid_cents=2500, levied_cents=10000) == Decimal("0.25")

    def test_fraction_preserves_overpayment_for_stored_rate(self):
        assert levy_collection_fraction(paid_cents=12000, levied_cents=10000) == Decimal("1.2")

    def test_fraction_zero_denominator_returns_zero(self):
        assert levy_collection_fraction(paid_cents=1000, levied_cents=0) == Decimal(0)

    def test_health_score_clamps_to_100_percent(self):
        assert levy_collection_health_score(paid_cents=12000, levied_cents=10000) == Decimal("100.00")

    def test_health_score_matches_bi_component_scale(self):
        assert levy_collection_health_score(paid_cents=8750, levied_cents=10000) == Decimal("87.50")


class TestHistoricalYearCollectionRate:
    def test_zero_levied_returns_zero(self):
        assert historical_year_collection_rate(levied_cents=0, closing_arrears_cents=0) == Decimal(0)

    def test_all_collected_returns_100_percent(self):
        rate = historical_year_collection_rate(levied_cents=100000, closing_arrears_cents=0)
        assert rate == Decimal("100.00")

    def test_partial_collection(self):
        rate = historical_year_collection_rate(levied_cents=100000, closing_arrears_cents=25000)
        assert rate == Decimal("75.00")

    def test_closing_arrears_exceeding_levied_clamps_to_zero(self):
        """Carry-forward arrears pre-dating the ledger can exceed the year's levy —
        must not go negative."""
        rate = historical_year_collection_rate(levied_cents=100000, closing_arrears_cents=150000)
        assert rate == Decimal(0)
