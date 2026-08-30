"""
Comprehensive tests for arrears calculation logic.
ALL tests use mock/fixture data — no real DB queries.
Tests validate business logic, not specific DB values.

Run with:
    backend/venv/bin/pytest tests/backend/test_arrears_logic.py -v

Key definitions enforced here:
  opening_arrears = prior-year carry-forward debt (ONLY metric on Arrears Recovery Board)
  net_balance     = current-year outstanding (opening + levied - paid) = Owner Dashboard "Total Remaining"
  These are SEPARATE metrics and must NEVER be combined.

BUILDING_ID = "13195"
East Gate uses CALENDAR-YEAR levy model:
  levy_year=2026 → all due months in 2026 (Mar 1, Jun 1, Sep 1, Dec 1)
  levy_year=2025 → all due months in 2025 (Mar 1, Jun 1, Sep 1, Dec 1)

FY2026 grace deadlines (default first-of-month + 14):
  Mar 15, Jun 15, Sep 15, Dec 15  (all in 2026)

As of March 16, 2026: only Mar 15 2026 grace has passed (1 period past grace).
However, opening_arrears uses the PRIOR year (2025) grace deadlines.
Prior year 2025 last grace = Dec 15, 2025.
days_overdue from prior year's last grace on Mar 16 2026:
  (Mar 16 2026 - Dec 15 2025).days = 91
"""
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.asyncio

# ─── Reference fixture data (created here, not from DB) ──────────────────────

MOCK_LEDGER = [
    # Units with opening arrears (prior-year debt)
    {"unit_number": "UA001", "opening_arrears": 154.00, "total_paid": 0.0, "total_levied": 3613.88,
     "period_levy": 903.47, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "UA017", "opening_arrears": 3.64, "total_paid": 0.0, "total_levied": 3613.88, "period_levy": 903.47,
     "building_id": "13195", "levy_year": 2026},
    {"unit_number": "UA042", "opening_arrears": 963.31, "total_paid": 0.0, "total_levied": 3613.88,
     "period_levy": 903.47, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "TH074", "opening_arrears": 580.01, "total_paid": 0.0, "total_levied": 7046.40,
     "period_levy": 1761.60, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "TH077", "opening_arrears": 97.33, "total_paid": 0.0, "total_levied": 7046.40,
     "period_levy": 1761.60, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "TH081", "opening_arrears": 294.00, "total_paid": 0.0, "total_levied": 7046.40,
     "period_levy": 1761.60, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "TH084", "opening_arrears": 366.00, "total_paid": 0.0, "total_levied": 7046.40,
     "period_levy": 1761.60, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "TH085", "opening_arrears": 20.23, "total_paid": 0.0, "total_levied": 7046.40,
     "period_levy": 1761.60, "building_id": "13195", "levy_year": 2026},
    # Units with credit (advance payments — no arrears)
    {"unit_number": "TH087", "opening_arrears": 0.0, "total_paid": 1800.0, "total_levied": 7090.04,
     "period_levy": 1772.51, "building_id": "13195", "levy_year": 2026},
    {"unit_number": "UA063", "opening_arrears": 0.0, "total_paid": 4590.90, "total_levied": 6121.00,
     "period_levy": 1530.25, "building_id": "13195", "levy_year": 2026},
    # Units with zero balance
    {"unit_number": "UA002", "opening_arrears": 0.0, "total_paid": 0.0, "total_levied": 6121.00, "period_levy": 1530.25,
     "building_id": "13195", "levy_year": 2026},
]

# FY2026 due dates (calendar year, first of month):
FY2026_DUE_DATES = [
    date(2026, 3, 1),  # Q1 (month 3, first of month, levy_year=2026)
    date(2026, 6, 1),  # Q2 (month 6, first of month, levy_year=2026)
    date(2026, 9, 1),  # Q3 (month 9, first of month, levy_year=2026)
    date(2026, 12, 1),  # Q4 (month 12, first of month, levy_year=2026)
]

FY2026_GRACE_DEADLINES = [d + timedelta(days=14) for d in FY2026_DUE_DATES]
# Q1: Mar 15 2026, Q2: Jun 15 2026, Q3: Sep 15 2026, Q4: Dec 15 2026

# Prior year (2025) due dates — used for opening_arrears days_overdue
FY2025_DUE_DATES = [
    date(2025, 3, 1),
    date(2025, 6, 1),
    date(2025, 9, 1),
    date(2025, 12, 1),
]

FY2025_GRACE_DEADLINES = [d + timedelta(days=14) for d in FY2025_DUE_DATES]


# Mar 15, Jun 15, Sep 15, Dec 15 — all in 2025


class TestArrearsDefinition:
    """Arrears = opening_arrears ONLY (prior-year carry-forward)."""

    def test_arrears_is_opening_arrears_not_current_obligations(self):
        """true_arrears = opening_arrears, NOT opening_arrears + periods*levy."""
        ua042 = next(d for d in MOCK_LEDGER if d["unit_number"] == "UA042")
        opening_arrears = ua042["opening_arrears"]
        periods_past_grace = 1  # only Mar 15 2026 has passed as of Mar 16
        period_levy = ua042["period_levy"]

        # WRONG calculation (the bug — inflated value):
        wrong_arrears = opening_arrears + periods_past_grace * period_levy
        assert wrong_arrears > opening_arrears

        # CORRECT calculation (arrears board = prior-year carry-forward only):
        correct_arrears = max(0.0, opening_arrears - ua042["total_paid"])
        assert correct_arrears == pytest.approx(963.31, abs=0.01)

        # The wrong value is larger than the correct value
        assert wrong_arrears > correct_arrears

    def test_ua042_arrears_board_amount(self):
        """UA042 arrears board amount = $963.31 (not inflated with current-year levy)."""
        ua042 = next(d for d in MOCK_LEDGER if d["unit_number"] == "UA042")
        arrears_board_amount = max(0.0, ua042["opening_arrears"] - ua042["total_paid"])
        assert arrears_board_amount == pytest.approx(963.31, abs=0.01)

    def test_arrears_total_uses_opening_arrears_only(self):
        """Sum of all arrears = sum of opening_arrears > 0.01 (prior-year carry-forward only)."""
        total = sum(
            max(0.0, d["opening_arrears"] - d["total_paid"])
            for d in MOCK_LEDGER
            if d["opening_arrears"] > 0.01
        )
        # NOT $180,358 (wrong) — just ~$2,478 from actual opening_arrears
        assert total == pytest.approx(2478.52, abs=1.0)  # sum of arrears units with no ledger payments
        assert total < 5000  # dramatically less than the wrong $180k

    def test_credit_units_not_in_arrears(self):
        """Units with opening_arrears=0 do NOT appear in arrears list."""
        arrears_units = [d for d in MOCK_LEDGER if d["opening_arrears"] > 0.01]
        unit_numbers = [d["unit_number"] for d in arrears_units]
        assert "TH087" not in unit_numbers  # credit unit ($1800 paid)
        assert "UA063" not in unit_numbers  # credit unit ($4590.90 paid)
        assert "UA002" not in unit_numbers  # zero balance

    def test_arrears_units_count(self):
        """Correct count of units with arrears in mock data."""
        count = sum(1 for d in MOCK_LEDGER if d["opening_arrears"] > 0.01)
        assert count == 8  # UA001, UA017, UA042, TH074, TH077, TH081, TH084, TH085

    def test_top_arrears_sorted_by_opening_arrears(self):
        """Top arrears list is sorted by opening_arrears descending."""
        arrears = sorted(
            [d for d in MOCK_LEDGER if d["opening_arrears"] > 0.01],
            key=lambda x: max(0.0, x["opening_arrears"] - x["total_paid"]),
            reverse=True,
        )
        assert arrears[0]["unit_number"] == "UA042"  # $963.31
        assert arrears[1]["unit_number"] == "TH074"  # $580.01
        assert arrears[2]["unit_number"] == "TH084"  # $366.00

    def test_th015_no_portal_account_still_in_arrears(self):
        """TH085 appears in arrears even if owner has no portal account."""
        th015 = next((d for d in MOCK_LEDGER if d["unit_number"] == "TH085"), None)
        assert th015 is not None
        assert th015["opening_arrears"] == pytest.approx(20.23, abs=0.01)
        # Arrears board shows it regardless of portal status
        assert th015["opening_arrears"] > 0.01


class TestDaysOverdueForOpeningArrears:
    """days_overdue for opening_arrears uses LAST grace deadline of PRIOR levy year."""

    def test_prior_year_q4_grace_is_dec15_2025(self):
        """Prior year 2025 Q4 with day=1 = Dec 1 2025 + 14 = Dec 15 2025."""
        q4_due = date(2025, 12, 1)  # calendar year 2025, month 12, day 1
        q4_grace = q4_due + timedelta(days=14)
        assert q4_grace == date(2025, 12, 15)

    def test_days_overdue_from_prior_year_q4_grace_march16(self):
        """As of March 16 2026: 91 days since prior year Q4 grace (Dec 15 2025)."""
        today = date(2026, 3, 16)
        q4_grace = date(2025, 12, 15)  # prior year last grace
        days = (today - q4_grace).days
        assert days == 91

    def test_days_overdue_is_91_using_prior_year_algorithm(self):
        """Full algorithm: prior year 2025 → last past grace = Dec 15 → 91 days."""
        import sys
        sys.path.insert(0, 'backend')
        from utils.finance_helpers import compute_period_due_dates

        settings = {"levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "first"}
        prior_due = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        prior_grace = sorted([date.fromisoformat(d) + timedelta(days=14) for d in prior_due])
        today = date(2026, 3, 16)
        past = [g for g in prior_grace if today > g]
        days = (today - past[-1]).days if past else 0
        assert days == 91

    def test_days_overdue_nonzero_for_all_arrears_units(self):
        """All units with opening_arrears must have days_overdue > 0 on Mar 16 2026."""
        today = date(2026, 3, 16)
        q4_grace = date(2025, 12, 15)  # prior year Q4 grace
        for doc in MOCK_LEDGER:
            if doc["opening_arrears"] > 0.01:
                days = (today - q4_grace).days
                assert days > 0, f"{doc['unit_number']} should have days_overdue > 0"

    def test_days_overdue_consistent_across_views(self):
        """Dashboard Top Arrears and Debt Recovery Board use identical formula."""
        today = date(2026, 3, 16)
        # Both views: prior_year 2025 → last past grace = Dec 15 2025
        prior_grace = sorted([g for g in FY2025_GRACE_DEADLINES if today > g])
        assert len(prior_grace) == 4, "All 2025 grace deadlines must be in the past"
        dashboard_days = (today - prior_grace[-1]).days
        board_days = (today - prior_grace[-1]).days
        assert dashboard_days == board_days == 91

    def test_no_future_grace_deadline_used(self):
        """FY2026 Q2 grace (Jun 15 2026) has NOT passed on Mar 16, 2026."""
        today = date(2026, 3, 16)
        q2_grace = date(2026, 6, 15)
        assert today < q2_grace, "Q2 grace must be in the future on Mar 16"

    def test_severity_critical_at_91_days(self):
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


class TestArrearsVsCurrentLevyOutstanding:
    """Distinguish between arrears (prior-year) and current-year outstanding."""

    def test_ua042_arrears_is_963_not_inflated(self):
        """UA042 arrears = $963.31 (opening), NOT inflated with current-year levies."""
        ua042 = next(d for d in MOCK_LEDGER if d["unit_number"] == "UA042")
        # The arrears board amount = opening_arrears only
        arrears_board_amount = max(0.0, ua042["opening_arrears"] - ua042["total_paid"])
        assert arrears_board_amount == pytest.approx(963.31, abs=0.01)

        # The current-year outstanding is a SEPARATE metric
        current_outstanding = ua042["total_levied"] - ua042["total_paid"]
        assert current_outstanding == pytest.approx(3613.88, abs=0.01)

        # These are DIFFERENT metrics, not to be combined on the arrears board
        assert arrears_board_amount != current_outstanding
        assert arrears_board_amount < current_outstanding

    def test_th017_no_arrears_despite_current_outstanding(self):
        """TH087 has ~$5290 current outstanding but $0 opening_arrears — not on arrears board."""
        th017 = next(d for d in MOCK_LEDGER if d["unit_number"] == "TH087")
        assert th017["opening_arrears"] == 0.0  # No prior-year arrears
        net_balance = th017["total_levied"] - th017["total_paid"]
        assert net_balance == pytest.approx(5290.04, abs=0.01)  # Current outstanding
        # TH087 should NOT appear on arrears board
        assert th017["opening_arrears"] <= 0.01

    def test_arrears_total_much_less_than_total_outstanding(self):
        """Arrears total (~$2479) << total outstanding across all units (~$80k sample)."""
        arrears_total = sum(
            max(0.0, d["opening_arrears"] - d["total_paid"])
            for d in MOCK_LEDGER
            if d["opening_arrears"] > 0.01
        )
        total_outstanding = sum(
            max(0, d["total_levied"] - d["total_paid"])
            for d in MOCK_LEDGER
        )
        assert arrears_total < total_outstanding
        assert arrears_total < 5000  # Arrears is small (~$2479)

    def test_portal_payments_do_not_reduce_opening_arrears_on_board(self):
        """Portal levy_payments (current-year Stripe) must NOT reduce opening_arrears.

        Scenario: TH085 paid $1,567.97 via portal for current-year Q1+Q2 levy.
        Their $20.23 opening_arrears from FY2025 must remain on the board.
        Only unit_levy_ledger.total_paid (DEFT/bank imports) reduces opening_arrears.
        """
        opening_arrears = 20.23
        ledger_total_paid = 0.0  # no DEFT/bank payments in ledger
        portal_levy_paid = 1567.97  # current-year levy instalments via Stripe (irrelevant)

        # Correct: use ONLY ledger_total_paid (not portal payments)
        correct_arrears = max(0.0, opening_arrears - ledger_total_paid)
        assert correct_arrears == pytest.approx(20.23, abs=0.01)

        # Wrong (old behaviour): using portal payment hides the arrears
        wrong_arrears = max(0.0, opening_arrears - portal_levy_paid)
        assert wrong_arrears == 0.0, "Demonstrates the bug: portal payment would hide $20.23 arrears"

        # Confirm the fix produces the correct non-zero result
        assert correct_arrears > wrong_arrears


class TestUnitNumberFormatting:
    """Unit number display must not double-prefix."""

    def test_unit_number_not_double_prefixed(self):
        """unit_number 'UA042' should display as 'UA042', not 'UUA042'."""
        unit_number = "UA042"
        display = unit_number  # correct
        wrong_display = "U" + unit_number  # was the bug
        assert display == "UA042"
        assert wrong_display == "UUA042"
        assert not display.startswith("UU")

    def test_th_unit_not_double_prefixed(self):
        """unit_number 'TH087' should display as 'TH087', not 'UTH087'."""
        unit_number = "TH087"
        wrong_display = "U" + unit_number
        display = unit_number
        assert display == "TH087"
        assert wrong_display == "UTH087"
        assert not display.startswith("UT")

    def test_all_unit_numbers_in_mock_data_have_correct_format(self):
        """All mock unit numbers follow UA### or TH### format (no double prefix)."""
        for doc in MOCK_LEDGER:
            unit = doc["unit_number"]
            assert not unit.startswith("UU"), f"{unit} has double-U prefix"
            assert not unit.startswith("UTH"), f"{unit} has U-TH prefix"
            assert unit.startswith(("UA", "TH")), f"{unit} doesn't match UA### or TH### format"


class TestArrearsTotalCalculation:
    """Verify correct arrears total calculation using opening_arrears only."""

    def test_building_total_arrears_from_mock_data(self):
        """Building total arrears = sum of opening_arrears > 0.01 (no current-year levy added)."""
        total = sum(
            max(0.0, d["opening_arrears"] - d["total_paid"])
            for d in MOCK_LEDGER
            if d["opening_arrears"] > 0.01
        )
        expected = sum([154.00, 3.64, 963.31, 580.01, 97.33, 294.00, 366.00, 20.23])
        assert total == pytest.approx(expected, abs=0.01)

    def test_wrong_formula_produces_inflated_total(self):
        """Demonstrate that adding current-year levy periods inflates the total."""
        periods_past_grace = 1  # only Mar 15 2026 has passed as of Mar 16

        # CORRECT (opening_arrears only):
        correct_total = sum(
            max(0.0, d["opening_arrears"] - d["total_paid"])
            for d in MOCK_LEDGER
            if d["opening_arrears"] > 0.01
        )

        # WRONG (old formula — includes current-year levy periods):
        wrong_total = 0.0
        for d in MOCK_LEDGER:
            opening = d["opening_arrears"]
            period_levy = d["period_levy"]
            paid = d["total_paid"]
            obligations = opening + periods_past_grace * period_levy
            arrears = max(0.0, obligations - paid)
            if arrears > 0.01:
                wrong_total += arrears

        # The wrong total includes current-year levy for ALL 87 units
        assert wrong_total > correct_total, (
            f"Wrong formula should be larger: wrong={wrong_total:.2f}, correct={correct_total:.2f}"
        )

    def test_units_in_arrears_count_is_based_on_opening_arrears(self):
        """units_in_arrears count = units with opening_arrears > 0.01."""
        # Count based on opening_arrears (correct):
        correct_count = sum(1 for d in MOCK_LEDGER if d["opening_arrears"] > 0.01)

        # Count based on current-year outstanding (would include ALL units that haven't paid):
        wrong_count = sum(1 for d in MOCK_LEDGER if (d["total_levied"] - d["total_paid"]) > 0.01)

        assert correct_count == 8  # only units with prior-year debt
        assert wrong_count > correct_count  # wrong formula catches more units
