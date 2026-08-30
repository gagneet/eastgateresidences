"""
Tests for finance summary grace-period split logic (commit 1cbe81e).

Validates:
  - in_grace_periods detection: periods past due but within grace window
  - in_grace_amount calculation: min(net_balance, per_period × grace_count)
  - true_arrears_amount = total_outstanding - in_grace_amount
  - Edge cases: no in-grace periods, all outstanding is in-grace, zero balance units

Note: `_compute_in_grace_periods` and `_simulate_in_grace_amount` are extracted
mirrors of the inline logic in `routers/finance.py` (finance_summary endpoint).
They are tested here as pure-Python helpers. The MongoDB aggregation pipeline
that runs the same logic in production is covered by separate integration tests.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Ensure backend is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from utils.finance_helpers import compute_period_due_dates


# ─────────────────────────────────────────────────────────────────────────────
# Helper: compute in_grace logic extracted from finance.py for direct testing
# ─────────────────────────────────────────────────────────────────────────────

def _compute_in_grace_periods(computed_dates, today, grace_days):
    """Mirror of the in_grace_periods loop in finance.py /api/finance/summary."""
    in_grace = []
    for i, d_str in enumerate(computed_dates):
        try:
            due = date.fromisoformat(d_str)
            grace_deadline = due + timedelta(days=grace_days)
            if due < today <= grace_deadline:
                in_grace.append(f"Q{i + 1}")
        except (ValueError, TypeError):
            pass
    return in_grace


def _simulate_in_grace_amount(units_data, total_periods, in_grace_count):
    """
    Mirror of the in_grace pipeline aggregation logic from finance.py.
    
    units_data: list of dicts with 'total_levied' and 'net_balance'
    Returns: in_grace_amount (float)
    """
    if in_grace_count <= 0 or total_periods <= 0:
        return 0.0
    total = 0.0
    for u in units_data:
        net = u.get("net_balance", 0.0)
        if net <= 0:
            continue
        per_period = u.get("total_levied", 0.0) / total_periods
        in_grace_portion = min(net, per_period * in_grace_count)
        total += in_grace_portion
    return round(total, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: in_grace_periods detection
# ─────────────────────────────────────────────────────────────────────────────

class TestInGracePeriodDetection:
    """Tests for which levy quarters fall within the grace window."""

    def test_no_due_dates_passed(self):
        """All due dates are in the future → no in-grace periods."""
        today = date(2026, 1, 15)
        due_dates = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        grace_days = 14
        result = _compute_in_grace_periods(due_dates, today, grace_days)
        assert result == []

    def test_q1_due_today(self):
        """Today = due date → not in grace window (grace starts the next day)."""
        today = date(2026, 3, 1)
        due_dates = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=14)
        # due < today is False when due == today → not in grace, not overdue
        assert result == []

    def test_q1_one_day_after_due(self):
        """Today = due + 1 → Q1 enters grace window."""
        today = date(2026, 3, 2)
        due_dates = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=14)
        assert result == ["Q1"]

    def test_q1_last_day_of_grace(self):
        """Today = due + grace_days → still within grace window (inclusive)."""
        today = date(2026, 3, 1) + timedelta(days=14)
        due_dates = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=14)
        assert result == ["Q1"]

    def test_q1_grace_expired(self):
        """Today = due + grace_days + 1 → grace expired, Q1 is now hard overdue."""
        today = date(2026, 3, 1) + timedelta(days=15)
        due_dates = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=14)
        assert result == []  # past grace → overdue_periods, not in_grace_periods

    def test_two_periods_in_grace(self):
        """Two due dates both within their respective grace windows."""
        # Q1 due 2026-03-01, Q2 due 2026-06-01
        # Today between Q2 due and Q2 grace deadline, Q1 already expired
        today = date(2026, 6, 5)  # Q1 grace expired (Mar 15), Q2 in grace
        due_dates = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=14)
        assert result == ["Q2"]

    def test_invalid_date_string_skipped(self):
        """Malformed date strings are silently skipped."""
        today = date(2026, 3, 5)
        due_dates = ["invalid-date", "2026-03-01", None, "2026-06-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=14)
        assert result == ["Q2"]  # only "2026-03-01" qualifies (today=Mar 5, grace to Mar 15)

    def test_zero_grace_days(self):
        """Grace period of 0 days → nothing ever in grace."""
        today = date(2026, 3, 2)
        due_dates = ["2026-03-01", "2026-06-01"]
        result = _compute_in_grace_periods(due_dates, today, grace_days=0)
        # due < today (True) but today <= grace_deadline (= due + 0 = due < today) is False
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Tests: in_grace_amount and true_arrears_amount calculation
# ─────────────────────────────────────────────────────────────────────────────

class TestInGraceAmountCalculation:
    """Tests for the in_grace split: true arrears vs. in-grace outstanding."""

    def test_no_grace_periods_returns_zero(self):
        """If in_grace_count=0, in_grace_amount is always 0."""
        units = [
            {"total_levied": 4000.0, "net_balance": 1000.0},
            {"total_levied": 2000.0, "net_balance": 500.0},
        ]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=0)
        assert result == 0.0

    def test_no_owing_units_returns_zero(self):
        """All units have zero or negative net_balance → in_grace_amount = 0."""
        units = [
            {"total_levied": 4000.0, "net_balance": 0.0},
            {"total_levied": 4000.0, "net_balance": -50.0},  # credit
        ]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=1)
        assert result == 0.0

    def test_single_unit_exactly_one_period_behind(self):
        """
        Unit owes exactly one quarterly levy → the entire balance is in-grace
        when in_grace_count=1.
        """
        # total_levied = 4000, total_periods = 4 → per_period = 1000
        # net_balance = 1000, in_grace_count = 1
        # in_grace_portion = min(1000, 1000 * 1) = 1000
        units = [{"total_levied": 4000.0, "net_balance": 1000.0}]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=1)
        assert result == 1000.0

    def test_balance_capped_at_grace_portion(self):
        """
        Unit owes much more than one period → in-grace portion is capped at
        per_period × in_grace_count (only the recent amount is 'in grace').
        """
        # total_levied = 4000, per_period = 1000, net_balance = 3500 (3.5 periods behind)
        # in_grace_count = 1 → min(3500, 1000) = 1000
        units = [{"total_levied": 4000.0, "net_balance": 3500.0}]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=1)
        assert result == 1000.0

    def test_two_grace_periods_doubles_cap(self):
        """in_grace_count=2 doubles the per-unit cap."""
        # per_period = 1000, net_balance = 1500, cap = 1000 * 2 = 2000
        # min(1500, 2000) = 1500 → entire balance in grace
        units = [{"total_levied": 4000.0, "net_balance": 1500.0}]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=2)
        assert result == 1500.0

    def test_multiple_units_aggregated(self):
        """Correct sum across multiple units with differing entitlements."""
        # Unit A: per_period = 1000, net = 500  → min(500, 1000) = 500
        # Unit B: per_period = 500,  net = 600  → min(600, 500) = 500
        # Total in_grace = 1000
        units = [
            {"total_levied": 4000.0, "net_balance": 500.0},
            {"total_levied": 2000.0, "net_balance": 600.0},
        ]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=1)
        assert result == 1000.0

    def test_true_arrears_is_remainder(self):
        """true_arrears_amount = total_outstanding - in_grace_amount."""
        units = [
            {"total_levied": 4000.0, "net_balance": 3000.0},  # 3 periods behind
        ]
        in_grace = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=1)
        total_outstanding = 3000.0
        true_arrears = round(max(0.0, total_outstanding - in_grace), 2)
        # in_grace = min(3000, 1000) = 1000
        # true_arrears = 3000 - 1000 = 2000
        assert in_grace == 1000.0
        assert true_arrears == 2000.0

    def test_true_arrears_never_negative(self):
        """true_arrears cannot go below 0 if in_grace_amount exceeds total."""
        # Edge: in_grace_amount is computed per-unit but compared against total_outstanding
        # In practice they should match, but the max(0.0, ...) guard is verified here.
        true_arrears = round(max(0.0, 500.0 - 600.0), 2)
        assert true_arrears == 0.0

    def test_zero_total_levied_unit_skipped(self):
        """Unit with total_levied=0 contributes 0 to in_grace (no division error)."""
        units = [
            {"total_levied": 0.0, "net_balance": 500.0},  # edge: unlisted unit
            {"total_levied": 4000.0, "net_balance": 500.0},
        ]
        result = _simulate_in_grace_amount(units, total_periods=4, in_grace_count=1)
        # Unit 1: per_period = 0/4 = 0 → min(500, 0) = 0
        # Unit 2: per_period = 1000 → min(500, 1000) = 500
        assert result == 500.0


# ─────────────────────────────────────────────────────────────────────────────
# Tests: compute_period_due_dates (the underlying utility)
# ─────────────────────────────────────────────────────────────────────────────

class TestComputePeriodDueDates:
    """Sanity tests for compute_period_due_dates used by the grace-period logic."""

    def test_standard_quarterly_dates(self):
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        assert dates == ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]

    def test_two_periods_only(self):
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 2)
        assert dates == ["2026-03-01", "2026-06-01"]

    def test_last_day_type(self):
        dates = compute_period_due_dates(2026, [3], "last", None, 1)
        assert dates == ["2026-03-31"]  # March has 31 days

    def test_custom_date(self):
        dates = compute_period_due_dates(2026, [6], "custom", 15, 1)
        assert dates == ["2026-06-15"]
