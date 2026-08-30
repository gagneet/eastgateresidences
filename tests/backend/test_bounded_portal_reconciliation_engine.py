"""Tests for services/reconstruction_generators/bounded_portal_reconciliation_engine.py
(GAP-ONBOARD-004 A2 / Workstream C). Pure-logic tests, no I/O.
"""
from __future__ import annotations

from services.reconstruction_generators.bounded_portal_reconciliation_engine import (
    classify_bounded_adjustment,
)

_BOUND = 100_000  # $1,000.00 one-period levy


class TestReconciledAndMissingData:
    def test_within_tolerance_is_reconciled(self):
        d = classify_bounded_adjustment(
            unit_number="UA001", pg_true_balance_cents=5000, portal_balance_cents=5050,
            period_bound_cents=_BOUND,
        )
        assert d.decision == "reconciled"
        assert d.gap_cents == 50

    def test_missing_pg_balance_is_no_portal_data_not_zero(self):
        d = classify_bounded_adjustment(
            unit_number="UA002", pg_true_balance_cents=None, portal_balance_cents=5000,
            period_bound_cents=_BOUND,
        )
        assert d.decision == "no_portal_data"
        assert d.gap_cents is None

    def test_missing_portal_balance_is_no_portal_data_not_zero(self):
        d = classify_bounded_adjustment(
            unit_number="UA003", pg_true_balance_cents=5000, portal_balance_cents=None,
            period_bound_cents=_BOUND,
        )
        assert d.decision == "no_portal_data"


class TestDebitDirection:
    def test_portal_shows_more_owed_posts_debit_adjustment(self):
        """PG true_balance=0 (shows square), portal says $500 owed -> PG's paid_cents is
        overstated -> debit adjustment (decrement paid_cents), matching
        gap_fin_046_portal_evidenced_adjustment_20260806.py's existing direction."""
        d = classify_bounded_adjustment(
            unit_number="UA004", pg_true_balance_cents=0, portal_balance_cents=50_000,
            period_bound_cents=_BOUND,
        )
        assert d.decision == "post_debit_adjustment"
        assert d.gap_cents == 50_000


class TestCreditDirection:
    def test_portal_shows_credit_pg_shows_square_posts_credit_receipt(self):
        """The TH087/TH075/UA038 pattern verified live in this session's A1 evidence pack:
        portal shows a credit PG's levy_items can't express -> post a new receipt."""
        d = classify_bounded_adjustment(
            unit_number="TH087", pg_true_balance_cents=0, portal_balance_cents=-25_498,
            period_bound_cents=_BOUND,
        )
        assert d.decision == "post_credit_receipt"
        assert d.gap_cents == -25_498


class TestBound:
    def test_residual_within_bound_is_postable(self):
        d = classify_bounded_adjustment(
            unit_number="UA005", pg_true_balance_cents=0, portal_balance_cents=_BOUND,  # == 1.0x bound
            period_bound_cents=_BOUND,
        )
        assert d.decision == "post_debit_adjustment"

    def test_residual_exceeding_bound_halts_and_flags_not_auto_posted(self):
        """canonical-spec finding #2(a): a residual > ~one period's levy is a probable
        systematic bug -- halt, never auto-post."""
        d = classify_bounded_adjustment(
            unit_number="UA006", pg_true_balance_cents=0, portal_balance_cents=_BOUND + 1,
            period_bound_cents=_BOUND,
        )
        assert d.decision == "halt_flag"
        assert d.is_postable is False
        assert "systematic bug" in d.reason

    def test_custom_bound_periods_multiplier(self):
        d = classify_bounded_adjustment(
            unit_number="UA007", pg_true_balance_cents=0, portal_balance_cents=int(_BOUND * 1.5),
            period_bound_cents=_BOUND, bound_periods=2.0,
        )
        assert d.decision == "post_debit_adjustment"  # within 2x bound

    def test_missing_bound_halts_rather_than_assumes_safe(self):
        d = classify_bounded_adjustment(
            unit_number="UA008", pg_true_balance_cents=0, portal_balance_cents=50_000,
            period_bound_cents=None,
        )
        assert d.decision == "halt_flag"
        assert "no usable one-period-levy bound" in d.reason.lower()

    def test_zero_bound_halts_rather_than_assumes_safe(self):
        d = classify_bounded_adjustment(
            unit_number="UA009", pg_true_balance_cents=0, portal_balance_cents=50_000,
            period_bound_cents=0,
        )
        assert d.decision == "halt_flag"


class TestNeverPostsForMissingOrUnknown:
    def test_no_portal_data_is_never_postable(self):
        d = classify_bounded_adjustment(
            unit_number="UA010", pg_true_balance_cents=None, portal_balance_cents=None,
            period_bound_cents=_BOUND,
        )
        assert d.is_postable is False

    def test_halt_flag_is_never_postable(self):
        d = classify_bounded_adjustment(
            unit_number="UA011", pg_true_balance_cents=0, portal_balance_cents=_BOUND * 5,
            period_bound_cents=_BOUND,
        )
        assert d.is_postable is False

    def test_reconciled_is_never_postable(self):
        d = classify_bounded_adjustment(
            unit_number="UA012", pg_true_balance_cents=100, portal_balance_cents=100,
            period_bound_cents=_BOUND,
        )
        assert d.is_postable is False
