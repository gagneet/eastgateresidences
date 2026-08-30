"""
Tests for the unified Next Estimated Payment + Next Due Date algorithm.

The algorithm lives in server.py (both /owners-units and /owners-units/{unit})
and implements the formula:

    funding_base = effective_total_paid + max(0, -opening_arrears)
    carry_forward_arrears = max(0, opening_arrears)
    outstanding = (periods_funded + 1) × period_levy - funding_base

    Before / within grace (14 days):
        next_payment = outstanding + carry_forward_arrears
        next_due     = due_dates[periods_funded]

    After grace (period is overdue):
        next_payment = period_levy + outstanding + carry_forward_arrears + interest + penalty
        next_due     = due_dates[periods_funded + 1]

opening_arrears semantics:
    < 0  →  prior-year credit  (helps fund current-year periods)
    > 0  →  prior-year arrears (added on top of the next amount due)
    = 0  →  clean slate

Covered scenarios
-----------------
Class 1  — Prior-year credit, partial in-year payment (before grace)
Class 2  — Prior-year credit, partial in-year payment (after grace)
Class 3  — Prior-year arrears, partial in-year payment (before grace)
Class 4  — Prior-year arrears, partial in-year payment (after grace)
Class 5  — No carry-forward, partial payment (before grace)
Class 6  — No carry-forward, advance payment covering >1 period
Class 7  — Fully paid for the year
Class 8  — Multi-period advance, mid-year
Class 9  — Zero net credit (no payments, no carry-forward)
Class 10 — Edge: payment exactly equals one period levy
Class 11 — Edge: period_levy = 0 (guard against ZeroDivisionError)
Class 12 — Prior-year arrears larger than one period levy
"""

from datetime import datetime, timedelta

import pytest

from utils.finance_helpers import compute_period_installment_amounts


# ---------------------------------------------------------------------------
# Pure-logic helper — mirrors the algorithm in server.py exactly
# ---------------------------------------------------------------------------

def compute_next_payment(
        effective_total_paid: float,
        opening_arrears: float,
        period_levy: float,
        due_dates: list[str],  # ["YYYY-MM-DD", ...]
        period_amounts: list[float] | None = None,
        today: datetime | None = None,
        grace_days: int = 14,
        interest_rate_per_month: float = 0.0,
        penalty_amount: float = 0.0,
) -> tuple[float, str | None]:
    """
    Returns (next_payment_adjusted, next_due_date_str).
    Mirrors server.py single-unit and list-endpoint logic verbatim.
    """
    if today is None:
        today = datetime.now()
    grace = timedelta(days=grace_days)

    if period_levy <= 0:
        return 0.0, due_dates[0] if due_dates else None

    prior_year_credit = max(0.0, -opening_arrears)
    carry_forward_arrears = max(0.0, opening_arrears)
    funding_base = effective_total_paid + prior_year_credit
    scheduled_period_amounts = period_amounts or [period_levy] * max(len(due_dates), 1)
    schedule_cents = [max(0, round(amount * 100)) for amount in scheduled_period_amounts]
    funding_base_cents = max(0, round(funding_base * 100))

    periods_funded = 0
    cumulative_due_cents = 0
    for scheduled_cents in schedule_cents:
        cumulative_due_cents += scheduled_cents
        if funding_base_cents >= cumulative_due_cents:
            periods_funded += 1
        else:
            break

    if periods_funded >= len(schedule_cents):
        return round(carry_forward_arrears, 2), None

    outstanding = round(
        max(0, sum(schedule_cents[: periods_funded + 1]) - funding_base_cents) / 100.0,
        2,
    )
    curr_due_str = due_dates[periods_funded]
    curr_due_dt = datetime.strptime(curr_due_str, "%Y-%m-%d")
    past_grace = (curr_due_dt + grace) < today

    if past_grace:
        next_idx = periods_funded + 1
        interest_amount = 0.0
        penalty_applied = 0.0
        if outstanding > 0 and interest_rate_per_month > 0:
            days_overdue = max(0, (today - (curr_due_dt + grace)).days)
            interest_amount = round(
                outstanding * interest_rate_per_month * (days_overdue / 30.0), 2
            )
        if outstanding > 0 and penalty_amount > 0:
            penalty_applied = round(penalty_amount, 2)
        if next_idx < len(due_dates):
            next_period_amount = scheduled_period_amounts[next_idx]
            return round(next_period_amount + outstanding + carry_forward_arrears + interest_amount + penalty_applied,
                         2), due_dates[next_idx]
        else:
            return round(outstanding + carry_forward_arrears + interest_amount + penalty_applied, 2), None
    else:
        return round(outstanding + carry_forward_arrears, 2), curr_due_str


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

PERIOD_LEVY = 1400.00
DUE_DATES = ["2026-03-31", "2026-06-01", "2026-09-30", "2026-12-31"]

# A "now" solidly before Q1 grace (grace expires 2026-04-14)
BEFORE_GRACE = datetime(2026, 3, 15)
# After Q1 grace (2026-04-14) but before Q2 due
AFTER_Q1_GRACE = datetime(2026, 4, 20)
# After Q2 grace (2026-06-15) but before Q3 due
AFTER_Q2_GRACE = datetime(2026, 6, 20)


# ---------------------------------------------------------------------------
# Class 1 — Prior-year credit + partial payment, BEFORE grace
# ---------------------------------------------------------------------------

class TestPriorYearCreditBeforeGrace:
    """
    Prior-year credit $200 + in-year payment $1,000 → net_credit $1,200.
    Outstanding = $200. Due date stays Q1 (Mar 31).
    """

    def test_next_payment_amount(self):
        payment, _ = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=-200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 200.00) < 0.01

    def test_next_due_date_is_q1(self):
        _, due = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=-200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert due == "2026-03-31"

    def test_large_credit_reduces_payment_further(self):
        """Credit of $1,000 + paid $200 → net $1,200 → outstanding $200."""
        payment, _ = compute_next_payment(
            effective_total_paid=200.00,
            opening_arrears=-1000.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 200.00) < 0.01


# ---------------------------------------------------------------------------
# Class 2 — Prior-year credit + partial payment, AFTER Q1 grace
# ---------------------------------------------------------------------------

class TestPriorYearCreditAfterGrace:
    """
    Same $1,200 net_credit, but Q1 is now past grace.
    next_payment = Q2 levy ($1,400) + Q1 arrears ($200) = $1,600
    next_due     = Q2 (Jun 1)
    """

    def test_next_payment_includes_arrears(self):
        payment, _ = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=-200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
        )
        assert abs(payment - 1600.00) < 0.01

    def test_next_due_advances_to_q2(self):
        _, due = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=-200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
        )
        assert due == "2026-06-01"


# ---------------------------------------------------------------------------
# Class 3 — Prior-year arrears + partial payment, BEFORE grace
# ---------------------------------------------------------------------------

class TestPriorYearArrearsBeforeGrace:
    """
    Arrears $200 from prior year. Paid $600 in-year.
    net_credit = 600 - 200 = 400 → outstanding = 1400 - 400 = $1,000.
    Next due: Q1 (Mar 31).
    """

    def test_next_payment_amount(self):
        payment, _ = compute_next_payment(
            effective_total_paid=600.00,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 1000.00) < 0.01

    def test_next_due_date_is_q1(self):
        _, due = compute_next_payment(
            effective_total_paid=600.00,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert due == "2026-03-31"

    def test_zero_payment_full_arrears_carry(self):
        """No in-year payment, $200 arrears → outstanding = 1400 + 200 = $1,600."""
        payment, _ = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 1600.00) < 0.01

    def test_exact_current_period_payment_advances_due_date_but_keeps_arrears(self):
        """Paying Q1 exactly should advance to Q2 while still carrying prior-year arrears."""
        payment, due = compute_next_payment(
            effective_total_paid=PERIOD_LEVY,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert due == "2026-06-01"
        assert abs(payment - 1600.00) < 0.01

    def test_three_period_advance_payment_advances_to_q4_and_keeps_arrears(self):
        """Advance-paid current-year instalments should not be reduced by prior-year arrears."""
        payment, due = compute_next_payment(
            effective_total_paid=3 * PERIOD_LEVY,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert due == "2026-12-31"
        assert abs(payment - 1600.00) < 0.01


# ---------------------------------------------------------------------------
# Class 4 — Prior-year arrears + partial payment, AFTER Q1 grace
# ---------------------------------------------------------------------------

class TestPriorYearArrearsAfterGrace:
    """
    Arrears $200, paid $600 → outstanding Q1 = $1,000.
    After grace: next = Q2 levy + $1,000 = $2,400. Next due: Jun 1.
    """

    def test_next_payment_after_grace(self):
        payment, _ = compute_next_payment(
            effective_total_paid=600.00,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
        )
        assert abs(payment - 2400.00) < 0.01

    def test_next_due_advances_to_q2(self):
        _, due = compute_next_payment(
            effective_total_paid=600.00,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
        )
        assert due == "2026-06-01"


class TestAfterGraceInterestAndPenalty:
    """
    When a period is past grace, the next estimated payment must include:
      - the overdue shortfall
      - the next period levy
      - prorated interest to today
      - fixed penalty when configured
    """

    def test_after_grace_adds_interest_and_penalty(self):
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
            interest_rate_per_month=0.02,
            penalty_amount=50.00,
        )
        # Q1 outstanding = 1400.00; six overdue days past the 14-day grace deadline:
        # interest = 1400 * 0.02 * (6 / 30) = 5.60
        assert payment == pytest.approx(2855.60, abs=0.01)
        assert due == "2026-06-01"

    def test_longer_grace_defers_interest_and_penalty(self):
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
            grace_days=30,
            interest_rate_per_month=0.02,
            penalty_amount=50.00,
        )
        assert payment == pytest.approx(1400.00, abs=0.01)
        assert due == "2026-03-31"


# ---------------------------------------------------------------------------
# Class 5 — No carry-forward, partial payment, BEFORE grace
# ---------------------------------------------------------------------------

class TestNoCarryForwardPartialPaymentBeforeGrace:
    """
    Clean slate. Paid $1,000 toward Q1 ($1,400). Outstanding = $400.
    """

    def test_next_payment_amount(self):
        payment, _ = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 400.00) < 0.01

    def test_next_due_date_is_q1(self):
        _, due = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert due == "2026-03-31"

    def test_zero_payment_shows_full_period_levy(self):
        payment, _ = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - PERIOD_LEVY) < 0.01


# ---------------------------------------------------------------------------
# Class 6 — Advance payment covering more than one period
# ---------------------------------------------------------------------------

class TestAdvancePaymentMultiplePeriods:
    """
    Paid $1,800 (= 1 full period + $400 toward Q2). net_credit = $1,800.
    periods_funded = 1, outstanding Q2 = $1,000.
    Before Q2 grace: next = $1,000 / Jun 1.
    """

    def test_periods_funded_advances_to_q2(self):
        payment, due = compute_next_payment(
            effective_total_paid=1800.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,  # well before Q2 grace
        )
        assert abs(payment - 1000.00) < 0.01
        assert due == "2026-06-01"

    def test_paid_exactly_two_full_periods_advances_to_q3(self):
        payment, due = compute_next_payment(
            effective_total_paid=2800.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - PERIOD_LEVY) < 0.01
        assert due == "2026-09-30"

    def test_after_q2_grace_arrears_roll_into_q3(self):
        """
        Paid $1,800 → Q2 outstanding $1,000.
        After Q2 grace: next = Q3 levy + $1,000 = $2,400 / Sep 30.
        """
        payment, due = compute_next_payment(
            effective_total_paid=1800.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q2_GRACE,
        )
        assert abs(payment - 2400.00) < 0.01
        assert due == "2026-09-30"


# ---------------------------------------------------------------------------
# Class 7 — Fully paid for the year
# ---------------------------------------------------------------------------

class TestFullyPaidForYear:
    """
    Paid all 4 periods (4 × $1,400 = $5,600).
    next_payment = $0.00, next_due = None.
    """

    def test_exact_full_year_payment(self):
        payment, due = compute_next_payment(
            effective_total_paid=5600.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert payment == 0.00
        assert due is None


class TestResidualCentInstallments:
    """Quarter schedules must preserve the annual total exactly, even when cents do not divide evenly."""

    UA063_ANNUAL_LEVY = 6121.22
    UA063_INSTALLMENTS = compute_period_installment_amounts(UA063_ANNUAL_LEVY, 4)
    UA063_DUE_DATES = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
    UA063_ADVANCE_PAID = 4590.90

    def test_last_installment_absorbs_residual_cents(self):
        assert self.UA063_INSTALLMENTS == [1530.30, 1530.30, 1530.30, 1530.32]
        assert round(sum(self.UA063_INSTALLMENTS), 2) == pytest.approx(self.UA063_ANNUAL_LEVY, abs=0.01)

    def test_three_exact_instalments_leave_q4_residual_amount(self):
        payment, due = compute_next_payment(
            effective_total_paid=self.UA063_ADVANCE_PAID,
            opening_arrears=0.00,
            period_levy=self.UA063_INSTALLMENTS[0],
            due_dates=self.UA063_DUE_DATES,
            period_amounts=self.UA063_INSTALLMENTS,
            today=datetime(2026, 10, 1),
        )
        assert payment == pytest.approx(1530.32, abs=0.01)
        assert due == "2026-12-01"

    def test_overpaid_shows_zero_next_payment(self):
        """Paying more than annual levy → still $0 next payment."""
        payment, due = compute_next_payment(
            effective_total_paid=6000.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert payment == 0.00
        assert due is None

    def test_prior_credit_plus_payments_covers_year(self):
        """Credit $400 + paid $5,200 = $5,600 → fully paid."""
        payment, due = compute_next_payment(
            effective_total_paid=5200.00,
            opening_arrears=-400.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert payment == 0.00
        assert due is None


# ---------------------------------------------------------------------------
# Class 8 — Mid-year multi-period scenario
# ---------------------------------------------------------------------------

class TestMidYearScenarios:
    """Scenarios where Q1 and Q2 are already paid; focus is on Q3."""

    def test_two_full_periods_plus_partial_q3(self):
        """Paid $3,200 = 2 × $1,400 + $400 toward Q3. Outstanding Q3 = $1,000."""
        payment, due = compute_next_payment(
            effective_total_paid=3200.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=datetime(2026, 8, 1),  # before Q3 due
        )
        assert abs(payment - 1000.00) < 0.01
        assert due == "2026-09-30"

    def test_credit_pushes_obligation_past_q2(self):
        """Credit $1,400 (= exactly 1 period) + $0 paid → Q2 fully funded from credit.
        Outstanding starts at Q2; periods_funded=1."""
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=-1400.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - PERIOD_LEVY) < 0.01
        assert due == "2026-06-01"


# ---------------------------------------------------------------------------
# Class 9 — Zero net credit (no payments, no carry-forward)
# ---------------------------------------------------------------------------

class TestZeroNetCredit:
    """No payments, no carry-forward → show full period levy."""

    def test_full_levy_shown_before_grace(self):
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - PERIOD_LEVY) < 0.01
        assert due == "2026-03-31"

    def test_after_grace_full_levy_plus_arrears(self):
        """After Q1 grace: next = Q2 levy + Q1 levy = $2,800."""
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
        )
        assert abs(payment - 2800.00) < 0.01
        assert due == "2026-06-01"


# ---------------------------------------------------------------------------
# Class 10 — Boundary: payment exactly equals one period levy
# ---------------------------------------------------------------------------

class TestExactPeriodPayment:
    """Paying exactly one period levy leaves outstanding = $0 for that period."""

    def test_exact_period_payment_outstanding_zero_advances_due(self):
        """Exactly $1,400 paid → Q1 fully funded. Outstanding = $0 for Q2 is wrong;
        periods_funded=1, outstanding Q2 = $1,400. next_due = Q2."""
        payment, due = compute_next_payment(
            effective_total_paid=1400.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        # Q1 fully funded (floor(1400/1400)=1), Q2 outstanding = 2×1400 - 1400 = 1400
        assert abs(payment - PERIOD_LEVY) < 0.01
        assert due == "2026-06-01"

    def test_net_credit_exactly_zero_outstanding_is_period_levy(self):
        """net_credit=0 means no funding. outstanding = period_levy."""
        payment, _ = compute_next_payment(
            effective_total_paid=200.00,
            opening_arrears=200.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - PERIOD_LEVY) < 0.01


# ---------------------------------------------------------------------------
# Class 11 — Edge: period_levy = 0 (guard against division by zero)
# ---------------------------------------------------------------------------

class TestZeroPeriodLevy:
    """period_levy = 0 must return (0.0, first_due_or_None) without raising."""

    def test_zero_period_levy_returns_zero(self):
        payment, _ = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=0.00,
            period_levy=0.00,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert payment == 0.00

    def test_zero_period_levy_no_crash_with_credit(self):
        payment, _ = compute_next_payment(
            effective_total_paid=500.00,
            opening_arrears=-200.00,
            period_levy=0.00,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert payment == 0.00


# ---------------------------------------------------------------------------
# Class 12 — Prior-year arrears larger than one period levy
# ---------------------------------------------------------------------------

class TestLargeArrearsCarryForward:
    """
    Arrears $1,800 (> 1 period) + paid $0 → net_credit = -$1,800.
    Before grace: outstanding = $1,400 + $1,800 = $3,200.
    Q1 is the current period (periods_funded = 0).
    """

    def test_large_arrears_outstanding_before_grace(self):
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=1800.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 3200.00) < 0.01
        assert due == "2026-03-31"

    def test_large_arrears_after_grace_rolls_into_q2(self):
        """After Q1 grace: Q2 levy + Q1 outstanding ($3,200) = $4,600."""
        payment, due = compute_next_payment(
            effective_total_paid=0.00,
            opening_arrears=1800.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=AFTER_Q1_GRACE,
        )
        assert abs(payment - 4600.00) < 0.01
        assert due == "2026-06-01"

    def test_partial_payment_offsets_large_arrears(self):
        """
        Arrears $1,800, paid $1,000 → net_credit = -$800.
        outstanding = $1,400 + $800 = $2,200.
        """
        payment, due = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=1800.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=BEFORE_GRACE,
        )
        assert abs(payment - 2200.00) < 0.01
        assert due == "2026-03-31"


# ---------------------------------------------------------------------------
# Class 13 — Grace boundary (exactly on the boundary)
# ---------------------------------------------------------------------------

class TestGraceBoundary:
    """Test behaviour at the exact grace-period boundary."""

    def test_exactly_at_grace_deadline_is_not_past(self):
        """Due Mar 31 + 14 days = Apr 14. On Apr 14 at midnight: NOT past grace."""
        exactly_at_grace = datetime(2026, 4, 14, 0, 0, 0)
        payment, due = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=exactly_at_grace,
        )
        # Still before grace (not strictly less-than): shows outstanding Q1
        assert abs(payment - 400.00) < 0.01
        assert due == "2026-03-31"

    def test_one_second_past_grace_deadline_is_overdue(self):
        """One second past Apr 14 → overdue."""
        past_grace = datetime(2026, 4, 14, 0, 0, 1)
        payment, due = compute_next_payment(
            effective_total_paid=1000.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=past_grace,
        )
        assert abs(payment - 1800.00) < 0.01  # 1400 + 400
        assert due == "2026-06-01"


# ---------------------------------------------------------------------------
# Class 14 — Last period overdue (no next period to roll into)
# ---------------------------------------------------------------------------

class TestLastPeriodOverdue:
    """When Q4 is the overdue period, there is no Q5. Just show accumulated arrears."""

    def test_last_period_overdue_returns_arrears_no_next_due(self):
        """
        Paid $4,200 (= 3 × $1,400): Q1-Q3 fully funded, Q4 partially with $0.
        Outstanding Q4 = $1,400. After Q4 grace: last period, no next period.
        """
        after_q4_grace = datetime(2027, 1, 15)
        payment, due = compute_next_payment(
            effective_total_paid=4200.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=after_q4_grace,
        )
        assert abs(payment - PERIOD_LEVY) < 0.01
        assert due is None

    def test_last_period_partial_overdue(self):
        """Paid $4,600 toward Q4 ($400 of $1,400). After Q4 grace: $1,000 arrears, no next due."""
        after_q4_grace = datetime(2027, 1, 15)
        payment, due = compute_next_payment(
            effective_total_paid=4600.00,
            opening_arrears=0.00,
            period_levy=PERIOD_LEVY,
            due_dates=DUE_DATES,
            today=after_q4_grace,
        )
        assert abs(payment - 1000.00) < 0.01
        assert due is None
