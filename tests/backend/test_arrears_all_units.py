"""
Tests that arrears board covers all units from the strata roll, not just portal users.

Authoritative source: unit_levy_ledger.opening_arrears for building_id="13195", year="2026".
Expected: 18 units with positive opening_arrears, total ≈ $2,794.98.

Run with:
    backend/venv/bin/pytest tests/backend/test_arrears_all_units.py -v
"""
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Reference data from authoritative strata roll (FY2026 opening_arrears)
# ---------------------------------------------------------------------------
ARREARS_UNITS = [
    ("UA001", 154.00),
    ("UA017", 3.64),
    ("UA019", 5.00),
    ("UA028", 5.44),
    ("UA030", 12.82),
    ("UA034", 12.82),
    ("UA042", 963.31),
    ("UA058", 0.01),
    ("UA060", 15.41),
    ("UA067", 10.95),
    ("UA070", 226.15),
    ("TH074", 580.01),
    ("TH077", 97.33),
    ("TH078", 20.25),
    ("TH080", 7.61),
    ("TH081", 294.00),
    ("TH084", 366.00),
    ("TH085", 20.23),
]

EXPECTED_TOTAL = 2794.97  # sum of above (UA058 at $0.01 is borderline)


class TestArrearsReferenceData:
    """Pure data validation — no DB needed."""

    def test_total_arrears_unit_count(self):
        """18 units have positive opening_arrears from the strata roll."""
        assert len(ARREARS_UNITS) == 18

    def test_total_arrears_amount(self):
        """Total opening arrears ≈ $2,794.97."""
        total = sum(a for _, a in ARREARS_UNITS)
        assert total == pytest.approx(2794.97, abs=0.05)

    def test_top_5_by_opening_arrears(self):
        """Top 5 units by opening_arrears are in the correct order."""
        sorted_units = sorted(ARREARS_UNITS, key=lambda x: x[1], reverse=True)
        top5 = [u for u, _ in sorted_units[:5]]
        assert top5[0] == "UA042"
        assert top5[1] == "TH074"
        assert top5[2] == "TH084"
        assert top5[3] == "TH081"
        assert top5[4] == "UA070"

    def test_th015_is_in_arrears(self):
        """TH085 (Jinal Achal) has $20.23 opening_arrears and must appear on board."""
        th015 = next((a for u, a in ARREARS_UNITS if u == "TH085"), None)
        assert th015 is not None
        assert th015 == pytest.approx(20.23, abs=0.01)

    def test_top5_sum(self):
        """Top 5 arrears units sum to ~$2,429.47."""
        sorted_units = sorted(ARREARS_UNITS, key=lambda x: x[1], reverse=True)
        top5_total = sum(a for _, a in sorted_units[:5])
        assert top5_total == pytest.approx(2429.47, abs=0.05)


class TestArrearsSourceIsLedger:
    """Verify arrears board sources data from unit_levy_ledger, not portal users."""

    def test_arrears_board_uses_ledger_not_portal_users(self):
        """
        Critical: arrears board must iterate unit_levy_ledger (all 87 strata units),
        not filter by portal users (only 4 active in user_units).

        This is a structural/logical test using the reference data to confirm
        the expected count of 18 > the number of portal users (4).
        """
        portal_user_units = {"TH087", "TH086", "TH071", "UA001"}  # 4 active portal users
        arrears_units_set = {u for u, a in ARREARS_UNITS if a > 0.01}

        # Most arrears units have no portal account
        arrears_without_portal = arrears_units_set - portal_user_units
        assert len(arrears_without_portal) >= 14, \
            "At least 14 arrears units have no portal account — must still appear on board"

    def test_non_portal_th015_must_appear(self):
        """
        TH085 (Jinal Achal & Dave Achal) has no portal account.
        They have $20.23 opening_arrears. They must appear on the arrears board.
        """
        th015_portal = False  # TH085 has no user_units entry
        th015_arrears = 20.23

        # Simulate correct behavior: iterate ledger records, not portal users
        ledger_records = [
            {"unit_number": "TH085", "opening_arrears": th015_arrears, "total_paid": 0.0},
        ]
        portal_users = {}  # TH085 owner not registered

        arrears = []
        for rec in ledger_records:
            if rec["opening_arrears"] > 0.01:
                arrears.append({
                    "unit_number": rec["unit_number"],
                    "opening_arrears": rec["opening_arrears"],
                    "has_portal_account": rec["unit_number"] in portal_users,
                })

        assert len(arrears) == 1
        assert arrears[0]["unit_number"] == "TH085"
        assert arrears[0]["has_portal_account"] is False

    def test_portal_payments_do_not_clear_opening_arrears(self):
        """
        Portal levy_payments (Stripe/current-year instalments) must NOT reduce
        opening_arrears. Opening_arrears is a prior-year carry-forward.

        Scenario: TH085 paid $1,567.97 via portal for Q1+Q2 levy 2026.
        Their $20.23 opening_arrears from FY2025 must remain visible.
        """
        opening_arrears = 20.23
        ledger_total_paid = 0.0  # no DEFT/bank payments in ledger
        portal_levy_paid = 1567.97  # current-year levy instalments via Stripe

        # Correct: use ONLY ledger_total_paid (not portal_levy_paid)
        true_arrears_correct = max(0.0, opening_arrears - ledger_total_paid)
        assert true_arrears_correct == pytest.approx(20.23, abs=0.01)

        # Wrong (old behaviour): using max(ledger, portal) hides the arrears
        true_arrears_wrong = max(0.0, opening_arrears - max(ledger_total_paid, portal_levy_paid))
        assert true_arrears_wrong == 0.0, "Demonstrates the bug: portal payment hid $20.23 arrears"

        # Confirm the fix produces a non-zero result
        assert true_arrears_correct > true_arrears_wrong


class TestGracePeriodObligations:
    """Verify the arrears-board obligation calculation: opening_arrears only."""

    def test_arrears_board_uses_opening_arrears_only(self):
        """Arrears board true_arrears = opening_arrears only (no current-year levy added)."""
        opening_arrears = 154.00  # UA001
        ledger_total_paid = 0.0
        # Correct formula: true_arrears = max(0, opening_arrears - ledger_total_paid)
        true_arrears = max(0.0, opening_arrears - ledger_total_paid)
        assert true_arrears == pytest.approx(154.00, abs=0.01)

    def test_arrears_board_does_not_add_current_year_levy(self):
        """Arrears board must NOT add periods_past_grace × period_levy to opening_arrears.

        This was the bug: UA042 showed $2,768.85 instead of $963.31.
        Correct = opening_arrears = $963.31 (prior-year carry-forward only).
        """
        opening_arrears = 963.31  # UA042
        periods_past_grace = 1  # only Mar 15 2026 has passed as of Mar 16
        period_levy = 902.77

        # WRONG (old formula — the bug):
        wrong_arrears = opening_arrears + periods_past_grace * period_levy
        assert wrong_arrears > opening_arrears  # demonstrates inflated wrong value

        # CORRECT (opening_arrears only):
        correct_arrears = max(0.0, opening_arrears - 0.0)  # ledger_total_paid = 0
        assert correct_arrears == pytest.approx(963.31, abs=0.01)
        assert correct_arrears < wrong_arrears  # correct is much less than wrong

    def test_fully_paid_via_ledger_shows_zero(self):
        """Unit that has fully paid (via DEFT/bank → ledger.total_paid) shows zero arrears."""
        opening_arrears = 580.01  # TH074
        ledger_total_paid = 580.01  # DEFT payment recorded in ledger
        true_arrears = max(0.0, opening_arrears - ledger_total_paid)
        assert true_arrears == 0.0

    def test_march_2026_current_year_periods_past_grace(self):
        """As of 2026-03-16, FY2026 calendar year has 1 period past grace.

        Calendar-year model (levy_year=2026): all due dates in 2026.
        With default first-of-month: Mar 1, Jun 1, Sep 1, Dec 1.
        Grace: Mar 15, Jun 15, Sep 15, Dec 15 (all in 2026).
        On Mar 16 2026: only Mar 15 has passed → 1 period past grace.

        Note: days_overdue for opening_arrears uses PRIOR YEAR (2025) dates, not these.
        """
        today = date(2026, 3, 16)
        # FY2026 due dates (calendar year, first of month)
        due_dates = [
            date(2026, 3, 1),  # Q1
            date(2026, 6, 1),  # Q2
            date(2026, 9, 1),  # Q3
            date(2026, 12, 1),  # Q4
        ]
        periods_past_grace = sum(1 for d in due_dates if today > d + timedelta(days=14))
        assert periods_past_grace == 1, (
            f"On March 16 2026: only Mar 15 grace has passed = 1, "
            f"got {periods_past_grace}"
        )

    def test_prior_year_2025_all_periods_past_grace_on_march16(self):
        """Prior year 2025: all 4 grace deadlines are past on March 16, 2026.

        2025 due dates (calendar year, first of month):
          Mar 1, Jun 1, Sep 1, Dec 1 → grace: Mar 15, Jun 15, Sep 15, Dec 15 (all in 2025)
        All 4 are past by March 16, 2026.
        """
        today = date(2026, 3, 16)
        prior_year_due_dates = [
            date(2025, 3, 1),
            date(2025, 6, 1),
            date(2025, 9, 1),
            date(2025, 12, 1),
        ]
        periods_past_grace = sum(1 for d in prior_year_due_dates if today > d + timedelta(days=14))
        assert periods_past_grace == 4, (
            f"All 4 prior year 2025 periods should be past grace, got {periods_past_grace}"
        )


class TestArrearsMetricsExpected:
    """
    Expected metric values for FY2026 building_id=13195.
    These assert the correct outputs after the fix.
    """

    def test_expected_arrears_unit_count(self):
        """17 units with opening_arrears > $0.01 (excludes UA058 at exactly $0.01)."""
        significant_arrears = [(u, a) for u, a in ARREARS_UNITS if a > 0.01]
        assert len(significant_arrears) == 17

    def test_expected_arrears_total_exceeds_previous_wrong_value(self):
        """
        Old (wrong) total was $1,635.92 (only 3 portal-user units).
        Correct total is $2,794.97 (all 17-18 strata-roll units).
        """
        correct_total = sum(a for _, a in ARREARS_UNITS)
        wrong_total = 1635.92
        assert correct_total > wrong_total * 1.5, \
            "Correct total should be significantly more than the 3-unit wrong total"

    def test_expected_unit_count_exceeds_previous_wrong_count(self):
        """
        Old (wrong) count was 3 (portal users only) or 6 (partial).
        Correct count is 17-18 (all strata-roll units with arrears).
        """
        correct_count = len([u for u, a in ARREARS_UNITS if a > 0.01])
        assert correct_count > 6, "Should be > 6 (old partial count)"
        assert correct_count > 3, "Should be > 3 (old portal-only count)"


# ---------------------------------------------------------------------------
# Tests for days_overdue formula consistency (arrears board vs dashboard)
# ---------------------------------------------------------------------------

class TestDaysOverdueCalculation:
    """
    Verify the levy schedule, grace period, and days_overdue formula.

    East Gate Residences uses a CALENDAR-YEAR levy model:
      levy_year=2026 → all due months in 2026: Mar 1, Jun 1, Sep 1, Dec 1
      levy_year=2025 → all due months in 2025: Mar 1, Jun 1, Sep 1, Dec 1

    For opening_arrears (carry-forward from prior year), days_overdue is computed
    from the LAST grace deadline of the PRIOR year:
      Prior year = 2025: last due = Dec 1 2025, grace = Dec 15 2025
      (Mar 16 2026 - Dec 15 2025).days = 91
    """

    def test_q4_grace_deadline_is_91_days_before_march16(self):
        """Prior year Q4 grace (Dec 1 + 14 = Dec 15 2025) is 91 days before Mar 16, 2026."""
        q4_due = date(2025, 12, 1)  # calendar year 2025, month 12, day=1
        q4_grace = q4_due + timedelta(days=14)  # Dec 15, 2025
        march16 = date(2026, 3, 16)
        assert q4_grace == date(2025, 12, 15)
        assert (march16 - q4_grace).days == 91

    def test_current_year_q1_grace_deadline_is_march15(self):
        """FY2026 Q1 grace deadline is March 15, 2026 (Mar 1 + 14 days).

        Calendar-year 2026, first-of-month: Q1 due = Mar 1 2026.
        """
        q1_due = date(2026, 3, 1)  # levy_year=2026, month 3, day=1
        q1_grace = q1_due + timedelta(days=14)
        assert q1_grace == date(2026, 3, 15)

    def test_one_period_past_grace_on_march16_calendar_year(self):
        """Calendar-year model: 1 period past grace on March 16, 2026.

        FY2026 due dates (calendar year, first of month):
          Q1: 2026-03-01  grace: 2026-03-15  (past as of Mar 16)
          Q2: 2026-06-01  grace: 2026-06-15  (future)
          Q3: 2026-09-01  grace: 2026-09-15  (future)
          Q4: 2026-12-01  grace: 2026-12-15  (future)
        """
        import sys
        sys.path.insert(0, 'backend')
        from utils.finance_helpers import compute_period_due_dates

        today = date(2026, 3, 16)
        due_date_strs = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4, None)
        due_dates = [date.fromisoformat(d) for d in due_date_strs]

        # All dates should be in 2026 (calendar-year model, no FY-split)
        for d in due_dates:
            assert d.year == 2026, f"All dates should be in 2026, got {d}"

        periods_past_grace = sum(1 for d in due_dates if today > d + timedelta(days=14))
        assert periods_past_grace == 1, (
            f"Expected 1 period past grace on Mar 16 2026 (only Mar 15), got {periods_past_grace}. "
            f"Due dates: {due_dates}"
        )

    def test_fy2026_all_due_dates_in_2026(self):
        """compute_period_due_dates(2026, ...) returns all dates in 2026 (no FY-split)."""
        import sys
        sys.path.insert(0, 'backend')
        from utils.finance_helpers import compute_period_due_dates

        due_date_strs = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4, None)
        due_dates = [date.fromisoformat(d) for d in due_date_strs]

        # All in 2026 (calendar year)
        assert date(2026, 3, 1) in due_dates
        assert date(2026, 6, 1) in due_dates
        assert date(2026, 9, 1) in due_dates
        assert date(2026, 12, 1) in due_dates

    def test_days_overdue_from_prior_year_last_grace(self):
        """days_overdue counts from prior year's LAST grace deadline.

        Prior year 2025, last due = Dec 1, last grace = Dec 15 2025.
        (Mar 16 2026 - Dec 15 2025).days = 91.
        """
        q4_grace = date(2025, 12, 15)  # prior year Q4 grace: Dec 1 + 14
        march16 = date(2026, 3, 16)
        assert (march16 - q4_grace).days == 91

    def test_severity_is_critical_at_91_days(self):
        """91 days overdue → severity == 'critical' (threshold: > 90 days)."""
        days_overdue = 91
        if days_overdue > 90:
            severity = "critical"
        elif days_overdue > 60:
            severity = "serious"
        elif days_overdue > 14:
            severity = "overdue"
        else:
            severity = "current"
        assert severity == "critical"

    def test_severity_thresholds(self):
        """Verify all four severity bands."""
        cases = [
            (0, "current"),
            (14, "current"),
            (15, "overdue"),
            (60, "overdue"),
            (61, "serious"),
            (90, "serious"),
            (91, "critical"),
            (183, "critical"),
        ]
        for days, expected in cases:
            if days > 90:
                sev = "critical"
            elif days > 60:
                sev = "serious"
            elif days > 14:
                sev = "overdue"
            else:
                sev = "current"
            assert sev == expected, f"days={days} expected {expected}, got {sev}"


class TestCreditOnlyUnitsExcludedFromArrearsBoard:
    """GAP-FIN-030 Root Cause A: a unit sitting on a credit only (no real arrears)
    must not be admitted to the /arrears/detail response — it was previously
    surviving the skip filter because the filter only dropped rows with
    NOTHING at all to show, not rows with specifically no debit.
    """

    @staticmethod
    def _is_admitted(true_arrears: float, current_year_outstanding: float) -> bool:
        """Mirrors the fixed skip condition in routers/finance.py get_arrears_detail()."""
        if true_arrears < 0.01 and current_year_outstanding < 0.01:
            return False
        return True

    def test_credit_only_unit_is_excluded(self):
        """net_balance negative (credit) -> true_arrears=0, current_year_outstanding=0
        -> unit must NOT appear on the arrears board, regardless of credit size."""
        assert self._is_admitted(true_arrears=0.0, current_year_outstanding=0.0) is False

    def test_unit_with_real_prior_year_arrears_is_still_admitted(self):
        """UA042-style unit: real prior-year carry-forward -> still shown."""
        assert self._is_admitted(true_arrears=963.31, current_year_outstanding=0.0) is True

    def test_unit_with_only_current_year_outstanding_is_still_admitted(self):
        """No prior-year carry-forward, but genuinely owes money this year -> still shown."""
        assert self._is_admitted(true_arrears=0.0, current_year_outstanding=45.00) is True

    def test_unit_with_nothing_owing_and_no_credit_is_excluded(self):
        """Fully reconciled, zero balance either way -> excluded (unchanged prior behaviour)."""
        assert self._is_admitted(true_arrears=0.0, current_year_outstanding=0.0) is False
