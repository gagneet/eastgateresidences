"""
Tests for compute_period_due_dates() — Calendar-year model.

East Gate Residences uses a calendar-year levy schedule:
  levy_year=2025 covers January–December 2025.
  levy_year=2026 covers January–December 2026.

There is NO financial-year split. All due months fall within levy_year.
Default due day is 1 (first of month), confirmed by the building committee:
  Q4 2025 = December 1, 2025  (NOT December 31).

Run with:
    backend/venv/bin/pytest tests/backend/test_compute_period_due_dates.py -v
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from utils.finance_helpers import compute_period_due_dates


class TestCalendarYearModel:
    """levy_year is a plain calendar year; all due months fall within it."""

    def test_fy2025_first_day_all_in_2025(self):
        """FY2025, due_months=[3,6,9,12], day_type='first' → all dates in 2025."""
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        assert date(2025, 3, 1) in parsed
        assert date(2025, 6, 1) in parsed
        assert date(2025, 9, 1) in parsed
        assert date(2025, 12, 1) in parsed

    def test_fy2026_first_day_all_in_2026(self):
        """FY2026, due_months=[3,6,9,12], day_type='first' → all dates in 2026."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        for d in parsed:
            assert d.year == 2026, f"All dates should be in 2026, got {d}"

    def test_q4_2025_is_december_1_2025(self):
        """User confirmed: Q4 2025 due = December 1, 2025 (day=1, calendar year)."""
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        q4 = max(parsed)
        assert q4 == date(2025, 12, 1), f"Q4 2025 should be Dec 1, got {q4}"

    def test_no_fy_split_september_in_levy_year(self):
        """September (month>=7) must be in levy_year, NOT levy_year-1."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        sep = next(d for d in parsed if d.month == 9)
        assert sep.year == 2026, f"September must be in 2026, got {sep.year}"

    def test_no_fy_split_december_in_levy_year(self):
        """December (month>=7) must be in levy_year, NOT levy_year-1."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        dec = next(d for d in parsed if d.month == 12)
        assert dec.year == 2026, f"December must be in 2026, got {dec.year}"

    def test_grace_91_days_before_march16(self):
        """Q4 2025: Dec 1 + 14 = Dec 15 → 91 days before Mar 16, 2026."""
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        q4_grace = max(parsed) + timedelta(days=14)
        assert q4_grace == date(2025, 12, 15), f"Q4 grace should be Dec 15, got {q4_grace}"
        assert (date(2026, 3, 16) - q4_grace).days == 91

    def test_output_is_sorted(self):
        """Dates are always returned in sorted order regardless of input order."""
        dates_scrambled = compute_period_due_dates(2026, [12, 3, 9, 6], "first", None, 4)
        dates_ordered = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        assert dates_scrambled == dates_ordered == sorted(dates_scrambled)

    def test_four_dates_returned(self):
        """Four months produce exactly four dates."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        assert len(dates) == 4

    def test_num_periods_limits_output(self):
        """num_periods limits how many due dates are returned."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 2)
        assert len(dates) == 2


class TestDayTypeVariants:
    """Verify all day_type options place dates in levy_year (no FY-split)."""

    def test_first_day_of_month(self):
        """day_type='first' uses day=1 in levy_year for all months."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        assert "2026-03-01" in dates
        assert "2026-06-01" in dates
        assert "2026-09-01" in dates
        assert "2026-12-01" in dates

    def test_last_day_of_month(self):
        """day_type='last' uses last calendar day of each month in levy_year."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "last", None, 4)
        assert "2026-03-31" in dates
        assert "2026-06-30" in dates
        assert "2026-09-30" in dates
        assert "2026-12-31" in dates

    def test_middle_day_of_month(self):
        """day_type='middle' uses day=15 in levy_year for all months."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "middle", None, 4)
        assert "2026-03-15" in dates
        assert "2026-06-15" in dates
        assert "2026-09-15" in dates
        assert "2026-12-15" in dates

    def test_custom_day_from_dict(self):
        """day_type='custom' uses per-month day values, all in levy_year."""
        custom = {"3": 31, "6": 1, "9": 1, "12": 1}
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "custom", None, 4, custom)
        assert "2026-03-31" in dates
        assert "2026-06-01" in dates
        assert "2026-09-01" in dates
        assert "2026-12-01" in dates

    def test_custom_day_fallback_to_levy_due_day(self):
        """day_type='custom' without matching key uses levy_due_day."""
        dates = compute_period_due_dates(2026, [9], "custom", 10, 1, {})
        assert dates == ["2026-09-10"]

    def test_custom_day_clamped_to_last_day(self):
        """Custom day larger than month's last day is clamped."""
        dates = compute_period_due_dates(2026, [2], "custom", None, 1, {"2": 31})
        assert dates == ["2026-02-28"]

    def test_unknown_day_type_defaults_to_first(self):
        """Unrecognised day_type defaults to 1st of the month."""
        dates = compute_period_due_dates(2026, [9], "bogus_type", None, 1)
        assert dates == ["2026-09-01"]

    def test_default_day_1_when_no_levy_due_day(self):
        """When levy_due_day is None and type is unrecognised, day defaults to 1."""
        dates = compute_period_due_dates(2026, [12], "bogus_type", None, 1)
        assert dates == ["2026-12-01"]


class TestLeapYearHandling:
    """February edge cases for leap and non-leap years."""

    def test_feb_in_non_leap_year(self):
        """February in a non-leap year (2026) uses day 28 for 'last'."""
        dates = compute_period_due_dates(2026, [2], "last", None, 1)
        assert dates == ["2026-02-28"]

    def test_feb_in_leap_year(self):
        """February 2028 is a leap year → day 29 for 'last'."""
        dates = compute_period_due_dates(2028, [2], "last", None, 1)
        assert dates == ["2028-02-29"]

    def test_feb_custom_day_31_clamped_to_28(self):
        """Custom day 31 in February 2026 (non-leap) clamped to 28."""
        dates = compute_period_due_dates(2026, [2], "custom", None, 1, {"2": 31})
        assert dates == ["2026-02-28"]


class TestGracePeriodCalculation:
    """Derived periods_past_grace and days_overdue from calendar-year due dates."""

    def test_fy2026_march16_one_period_past_grace(self):
        """As of 2026-03-16 with day=1: only Q1 (Mar 15 grace) has passed.

        FY2026 due dates with 'first' and months=[3,6,9,12]:
          Mar 1, Jun 1, Sep 1, Dec 1  (all in 2026)
          Grace: Mar 15, Jun 15, Sep 15, Dec 15
        On Mar 16: only Mar 15 grace has passed → 1 period past grace.
        """
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        today = date(2026, 3, 16)
        past = sum(1 for d in parsed if today > d + timedelta(days=14))
        assert past == 1, (
            f"Expected 1 period past grace on 2026-03-16 with day=1, got {past}. "
            f"Due dates: {parsed}"
        )

    def test_prior_year_all_four_past_grace_on_march16(self):
        """Prior year 2025 (all months='first'): all 4 grace deadlines past on Mar 16 2026."""
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        today = date(2026, 3, 16)
        past = sum(1 for d in parsed if today > d + timedelta(days=14))
        assert past == 4, f"All 4 prior-year periods should be past grace, got {past}"

    def test_days_overdue_91_from_q4_2025_grace(self):
        """days_overdue from prior year Q4 grace (Dec 15 2025) = 91 on Mar 16 2026."""
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        parsed = sorted([date.fromisoformat(d) for d in dates])
        today = date(2026, 3, 16)
        grace_deadlines = sorted([d + timedelta(days=14) for d in parsed])
        past = [g for g in grace_deadlines if today > g]
        # Prior year last past grace = Dec 15, 2025
        assert past[-1] == date(2025, 12, 15), f"Last prior grace should be Dec 15, got {past[-1]}"
        assert (today - past[-1]).days == 91

    def test_no_periods_past_grace_at_start_of_year(self):
        """January 2 2026 (just after FY2026 starts): no grace deadlines past yet."""
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        today = date(2026, 1, 2)
        past = sum(1 for d in parsed if today > d + timedelta(days=14))
        assert past == 0, f"No periods past grace at start of year, got {past}"

    def test_all_four_periods_past_grace_after_year_end(self):
        """January 1 2027 (all FY2026 grace deadlines passed): 4 periods past grace.

        Q4 grace = Dec 1 + 14 = Dec 15 2026. Jan 1 2027 > Dec 15 2026.
        """
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        today = date(2027, 1, 1)
        past = sum(1 for d in parsed if today > d + timedelta(days=14))
        assert past == 4, f"All 4 periods past grace on Jan 1 2027, got {past}"

    def test_fy2026_with_last_day_type_two_periods_past_grace(self):
        """With 'last' day_type: Mar 31 and Jun 30 still future on Mar 16; Sep 30 and Dec 31 also future.

        All FY2026 dates are in 2026: Mar 31, Jun 30, Sep 30, Dec 31.
        Grace: Apr 14, Jul 14, Oct 14, Jan 14 2027.
        On Mar 16: none are past → 0 periods past grace.
        """
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "last", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        today = date(2026, 3, 16)
        past = sum(1 for d in parsed if today > d + timedelta(days=14))
        assert past == 0, (
            f"With 'last' day type, all FY2026 dates are future on Mar 16. Got {past}. "
            f"Due dates: {parsed}"
        )


class TestReturnTypeAndFormat:
    """Verify return type is list of ISO date strings in sorted order."""

    def test_returns_list_of_strings(self):
        """Return type is list[str]."""
        result = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)

    def test_strings_are_valid_iso_dates(self):
        """All returned strings are valid ISO date format YYYY-MM-DD."""
        result = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        for s in result:
            d = date.fromisoformat(s)
            assert d.year == 2026

    def test_empty_months_returns_empty(self):
        """Empty month list returns empty list."""
        result = compute_period_due_dates(2026, [], "first", None, 0)
        assert result == []

    def test_num_periods_zero_returns_empty(self):
        """num_periods=0 returns empty list."""
        result = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 0)
        assert result == []


class TestDifferentBuildingConfigurations:
    """Various levy schedules a Super Admin might configure."""

    def test_biannual_june_december(self):
        """Biannual: June and December — both in levy_year."""
        dates = compute_period_due_dates(2026, [6, 12], "first", None, 2)
        assert "2026-06-01" in dates
        assert "2026-12-01" in dates

    def test_annual_december(self):
        """Annual single payment in December — in levy_year."""
        dates = compute_period_due_dates(2026, [12], "first", None, 1)
        assert dates == ["2026-12-01"]

    def test_annual_march(self):
        """Annual single payment in March — in levy_year."""
        dates = compute_period_due_dates(2026, [3], "first", None, 1)
        assert dates == ["2026-03-01"]

    def test_jan_apr_jul_oct_schedule(self):
        """Quarterly: Jan, Apr, Jul, Oct — all in levy_year."""
        dates = compute_period_due_dates(2026, [1, 4, 7, 10], "last", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        for d in parsed:
            assert d.year == 2026, f"All dates should be in 2026, got {d}"
        assert date(2026, 7, 31) in parsed
        assert date(2026, 10, 31) in parsed
        assert date(2026, 1, 31) in parsed
        assert date(2026, 4, 30) in parsed

    def test_monthly_all_12_months_in_one_year(self):
        """Monthly schedule: all 12 months are in levy_year."""
        dates = compute_period_due_dates(2026, list(range(1, 13)), "first", None, 12)
        parsed = [date.fromisoformat(d) for d in dates]
        assert len(parsed) == 12
        # All should be in 2026
        for d in parsed:
            assert d.year == 2026, f"All months should be in 2026, got {d}"
        # January should be first, December last
        assert parsed[0] == date(2026, 1, 1)
        assert parsed[-1] == date(2026, 12, 1)

    def test_fy2025_calendar_year(self):
        """FY2025 calendar year: all dates in 2025."""
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        assert "2025-03-01" in dates
        assert "2025-06-01" in dates
        assert "2025-09-01" in dates
        assert "2025-12-01" in dates

    def test_fy2027_calendar_year(self):
        """FY2027 calendar year: all dates in 2027."""
        dates = compute_period_due_dates(2027, [3, 6, 9, 12], "first", None, 4)
        assert "2027-03-01" in dates
        assert "2027-06-01" in dates
        assert "2027-09-01" in dates
        assert "2027-12-01" in dates


class TestEdgeCasesAndRegression:
    """Edge cases and regression tests."""

    def test_east_gate_q4_2025_confirmed_date(self):
        """East Gate confirmed: Q4 2025 due = December 1, 2025.
        This is the authoritative test — all calendar-year model changes must preserve this.
        """
        # Default settings: levy_due_months=[3,6,9,12], levy_due_day_type="first"
        dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        parsed = [date.fromisoformat(d) for d in dates]
        q4 = max(parsed)
        assert q4 == date(2025, 12, 1), (
            f"CONFIRMED: Q4 2025 must be December 1, 2025. Got {q4}."
        )

    def test_days_overdue_91_is_correct_on_march16_2026(self):
        """days_overdue should be 91 on March 16 2026 for prior-year opening_arrears.

        Algorithm: prior_year=2025 dates → last grace = Dec 15 2025
                   (Mar 16 2026 - Dec 15 2025).days = 91
        This was the value BEFORE the incorrectly-applied FY-split fix.
        """
        prior_year_dates = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        prior_parsed = sorted([date.fromisoformat(d) for d in prior_year_dates])
        prior_grace = sorted([d + timedelta(days=14) for d in prior_parsed])
        today = date(2026, 3, 16)
        past = [g for g in prior_grace if today > g]
        days_overdue = (today - past[-1]).days if past else 0
        assert days_overdue == 91, f"days_overdue must be 91 on Mar 16 2026, got {days_overdue}"

    def test_wrong_fy_split_would_give_153_days(self):
        """Documents the old FY-split error: Sep 30 2025 + 14 = Oct 14 2025.
        (Mar 16 2026 - Oct 14 2025) = 153 days — that was WRONG.
        The correct answer is 91 (from Dec 15 2025, the LAST prior-year grace).
        """
        # Old FY-split would put Sep 2026 and Dec 2026 into 2025
        fy_split_dates = [
            date(2025, 9, 30),  # Sep last day (was wrong due date)
            date(2025, 12, 31),  # Dec last day (was wrong due date)
            date(2026, 3, 31),
            date(2026, 6, 30),
        ]
        today = date(2026, 3, 16)
        grace = sorted([d + timedelta(days=14) for d in fy_split_dates])
        past = [g for g in grace if today > g]
        wrong_days = (today - past[0]).days if past else 0
        assert wrong_days == 153, f"Old FY-split Q1 grace gave {wrong_days} (wrong value)"

        # Correct calendar-year model gives 91
        correct_prior = compute_period_due_dates(2025, [3, 6, 9, 12], "first", None, 4)
        correct_parsed = sorted([date.fromisoformat(d) for d in correct_prior])
        correct_grace = sorted([d + timedelta(days=14) for d in correct_parsed])
        correct_past = [g for g in correct_grace if today > g]
        correct_days = (today - correct_past[-1]).days if correct_past else 0
        assert correct_days == 91, f"Calendar-year model must give 91, got {correct_days}"
        assert correct_days != wrong_days
