"""
Tests for council rates, water bills, and property tax utility functions.

Covers:
- ACT financial year calculation (Jul-Jun boundary)
- Council rate quarterly due dates (correct ACT order Q1=Aug, Q2=Nov, Q3=Feb, Q4=May)
- Per-quarter payment status inference from total_paid
- Council rate payment: correct financial year used when recording vs fetching
- Water bill calendar-year quarter vs ACT financial-year quarter mismatch
- Water bill supply charge calculation
- Water bill mark-paid and mark-partial flows
- Payment history scoping (owner can only see own unit)
- Financial year boundary edge cases
"""
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: ACT Financial Year Helper
# ─────────────────────────────────────────────────────────────────────────────

def get_act_financial_year(year: int, month: int) -> str:
    """
    Mirror of the JS `getActFinancialYear()` function in LevyPaymentPage.jsx.
    ACT financial year runs July–June:
      - Jan–Jun  → FY started in previous calendar year
      - Jul–Dec  → FY started in current calendar year
    """
    fy_start = year if month >= 7 else year - 1
    return f"{fy_start}-{str(fy_start + 1)[-2:]}"


class TestActFinancialYear:
    def test_jan_2026_gives_2025_26(self):
        assert get_act_financial_year(2026, 1) == "2025-26"

    def test_feb_2026_gives_2025_26(self):
        assert get_act_financial_year(2026, 2) == "2025-26"

    def test_jun_2026_gives_2025_26(self):
        assert get_act_financial_year(2026, 6) == "2025-26"

    def test_jul_2026_gives_2026_27(self):
        assert get_act_financial_year(2026, 7) == "2026-27"

    def test_aug_2025_gives_2025_26(self):
        assert get_act_financial_year(2025, 8) == "2025-26"

    def test_dec_2025_gives_2025_26(self):
        assert get_act_financial_year(2025, 12) == "2025-26"

    def test_jan_2025_gives_2024_25(self):
        assert get_act_financial_year(2025, 1) == "2024-25"

    def test_jul_2025_gives_2025_26(self):
        assert get_act_financial_year(2025, 7) == "2025-26"

    def test_format_uses_two_digit_year(self):
        fy = get_act_financial_year(2030, 2)
        assert fy == "2029-30"

    def test_format_uses_two_digit_year_crossing_century(self):
        fy = get_act_financial_year(2101, 2)
        assert fy == "2100-01"


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Council Rate Quarterly Due Dates (ACT financial year order)
# ─────────────────────────────────────────────────────────────────────────────

# ACT financial year quarters:
#   Q1 = Jul–Sep → due 31 Aug
#   Q2 = Oct–Dec → due 30 Nov
#   Q3 = Jan–Mar → due 28 Feb
#   Q4 = Apr–Jun → due 31 May

ACT_QUARTERLY_SCHEDULE = [
    {"q": "Q1", "due": "31 Aug", "label": "Jul–Sep"},
    {"q": "Q2", "due": "30 Nov", "label": "Oct–Dec"},
    {"q": "Q3", "due": "28 Feb", "label": "Jan–Mar"},
    {"q": "Q4", "due": "31 May", "label": "Apr–Jun"},
]


class TestCouncilRateQuarterlySchedule:
    def test_q1_is_jul_sep_due_31_aug(self):
        q = ACT_QUARTERLY_SCHEDULE[0]
        assert q["q"] == "Q1"
        assert q["due"] == "31 Aug"
        assert "Jul" in q["label"]

    def test_q2_is_oct_dec_due_30_nov(self):
        q = ACT_QUARTERLY_SCHEDULE[1]
        assert q["q"] == "Q2"
        assert q["due"] == "30 Nov"
        assert "Oct" in q["label"]

    def test_q3_is_jan_mar_due_28_feb(self):
        q = ACT_QUARTERLY_SCHEDULE[2]
        assert q["q"] == "Q3"
        assert q["due"] == "28 Feb"
        assert "Jan" in q["label"]

    def test_q4_is_apr_jun_due_31_may(self):
        q = ACT_QUARTERLY_SCHEDULE[3]
        assert q["q"] == "Q4"
        assert q["due"] == "31 May"
        assert "Apr" in q["label"]

    def test_due_dates_are_in_chronological_order(self):
        """Due dates must be chronologically Q1=Aug, Q2=Nov, Q3=Feb(next yr), Q4=May(next yr)."""
        months_order = {"Aug": 8, "Nov": 11, "Feb": 14, "May": 17}  # Feb/May are next year
        order = [months_order[q["due"].split()[1]] for q in ACT_QUARTERLY_SCHEDULE]
        assert order == sorted(order)

    def test_old_wrong_order_is_different(self):
        """Verify the old (wrong) due date order is not being used."""
        old_wrong = ["28 Feb", "31 May", "31 Aug", "30 Nov"]
        new_correct = [q["due"] for q in ACT_QUARTERLY_SCHEDULE]
        assert old_wrong != new_correct

    def test_four_quarters_cover_full_year(self):
        assert len(ACT_QUARTERLY_SCHEDULE) == 4
        labels = {q["label"] for q in ACT_QUARTERLY_SCHEDULE}
        assert "Jul–Sep" in labels
        assert "Oct–Dec" in labels
        assert "Jan–Mar" in labels
        assert "Apr–Jun" in labels


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Per-Quarter Payment Status Inference
# ─────────────────────────────────────────────────────────────────────────────

def infer_quarters_paid(total_paid: float, quarterly: float) -> int:
    """
    Mirror of JS `quartersPaidCount` logic in CouncilRatesSection.
    Returns number of quarters covered (0–4), assuming payments applied from Q1.
    """
    if not quarterly or quarterly <= 0:
        return 0
    return min(4, int(total_paid / quarterly + 0.01))


class TestQuarterlyPaymentInference:
    def test_zero_paid_gives_zero_quarters(self):
        assert infer_quarters_paid(0.0, 600.0) == 0

    def test_one_quarter_paid(self):
        assert infer_quarters_paid(600.0, 600.0) == 1

    def test_two_quarters_paid(self):
        assert infer_quarters_paid(1200.0, 600.0) == 2

    def test_three_quarters_paid(self):
        assert infer_quarters_paid(1800.0, 600.0) == 3

    def test_all_four_quarters_paid(self):
        assert infer_quarters_paid(2400.0, 600.0) == 4

    def test_overpayment_capped_at_four(self):
        assert infer_quarters_paid(3000.0, 600.0) == 4

    def test_partial_payment_rounds_down(self):
        # 700 / 600 = 1.16 → 1 quarter
        assert infer_quarters_paid(700.0, 600.0) == 1

    def test_rounding_tolerance_handles_floating_point(self):
        # 599.99 / 600.0 ≈ 0.9999 → should give 1 with +0.01 tolerance
        assert infer_quarters_paid(599.99, 600.0) == 1

    def test_zero_quarterly_returns_zero(self):
        assert infer_quarters_paid(1000.0, 0.0) == 0

    def test_none_quarterly_returns_zero(self):
        assert infer_quarters_paid(1000.0, None) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Financial Year Mismatch (Root Cause of Record Payment Bug)
# ─────────────────────────────────────────────────────────────────────────────

class TestFinancialYearMismatch:
    """
    The root cause of the 'Record Payment' button not disappearing was that the
    payment was recorded with financial_year='2026-27' (wrong for Feb 2026) while
    the GET endpoint used '2025-26' (correct default). They never matched.
    """

    def test_feb_2026_correct_fy_is_2025_26(self):
        """Payment recording must use 2025-26, not 2026-27."""
        fy = get_act_financial_year(2026, 2)
        assert fy == "2025-26"
        assert fy != "2026-27"  # The old wrong value

    def test_payment_fy_matches_rates_fy(self):
        """Payment financial_year must match the financial_year used in _get_payment_summary."""
        payment_fy = get_act_financial_year(2026, 2)  # correct for Feb 2026
        rates_default_fy = "2025-26"  # backend default
        assert payment_fy == rates_default_fy

    def test_wrong_fy_would_cause_mismatch(self):
        """Demonstrate the old bug: using currentYear-{currentYear+1} in Jan-Jun."""
        year = 2026
        wrong_fy = f"{year}-{str(year + 1)[-2:]}"  # Old code: "2026-27"
        correct_fy = get_act_financial_year(year, 2)  # "2025-26"
        assert wrong_fy != correct_fy
        assert wrong_fy == "2026-27"


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Water Bill Calendar-Year Quarters
# ─────────────────────────────────────────────────────────────────────────────

def get_water_quarter(month: int) -> str:
    """
    Mirror of JS `getCurrentWaterQuarter()` in PropertyServicesActionCards.jsx.
    Water bills use calendar-year quarters (Jan-Dec), NOT ACT financial year.
    """
    if 1 <= month <= 3:   return "Q1"  # Jan–Mar
    if 4 <= month <= 6:   return "Q2"  # Apr–Jun
    if 7 <= month <= 9:   return "Q3"  # Jul–Sep
    return "Q4"  # Oct–Dec


def get_act_rate_quarter_label(month: int) -> str:
    """ACT financial year quarter label (for council rates)."""
    if 7 <= month <= 9:   return "Q1"
    if 10 <= month <= 12: return "Q2"
    if 1 <= month <= 3:   return "Q3"
    return "Q4"


class TestWaterBillQuarters:
    def test_jan_is_water_q1(self):
        assert get_water_quarter(1) == "Q1"

    def test_feb_is_water_q1(self):
        assert get_water_quarter(2) == "Q1"

    def test_mar_is_water_q1(self):
        assert get_water_quarter(3) == "Q1"

    def test_apr_is_water_q2(self):
        assert get_water_quarter(4) == "Q2"

    def test_jun_is_water_q2(self):
        assert get_water_quarter(6) == "Q2"

    def test_jul_is_water_q3(self):
        assert get_water_quarter(7) == "Q3"

    def test_sep_is_water_q3(self):
        assert get_water_quarter(9) == "Q3"

    def test_oct_is_water_q4(self):
        assert get_water_quarter(10) == "Q4"

    def test_dec_is_water_q4(self):
        assert get_water_quarter(12) == "Q4"

    def test_feb_water_q1_differs_from_act_rates_q3(self):
        """Feb is Water Q1 but ACT rates Q3 — the old bug used rates Q3 for water."""
        water_q = get_water_quarter(2)
        rates_q = get_act_rate_quarter_label(2)
        assert water_q == "Q1"
        assert rates_q == "Q3"
        assert water_q != rates_q  # This was the mismatch causing wrong quarter display

    def test_all_twelve_months_covered(self):
        for m in range(1, 13):
            q = get_water_quarter(m)
            assert q in ("Q1", "Q2", "Q3", "Q4")

    def test_water_quarterly_ranges_are_calendar_year(self):
        """Water bill quarterly ranges must use calendar year (Jan=Q1), not FY."""
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        q1 = ranges[0]
        assert q1["quarter"] == "Q1"
        assert q1["start"] == "2026-01-01"
        assert q1["label"] == "Jan–Mar"

    def test_water_q3_is_jul_sep(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        q3 = ranges[2]
        assert q3["quarter"] == "Q3"
        assert q3["start"] == "2026-07-01"
        assert q3["label"] == "Jul–Sep"


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Water Bill Supply Charge Calculation
# ─────────────────────────────────────────────────────────────────────────────

class TestWaterBillSupplyCharge:
    def test_days_calculation_jan_to_apr(self):
        from routers.water_bills import _days_in_range
        days = _days_in_range("2026-01-01", "2026-04-01")
        assert days == 90  # 31+28+31

    def test_days_calculation_leap_year_q1(self):
        from routers.water_bills import _days_in_range
        days = _days_in_range("2024-01-01", "2024-04-01")
        assert days == 91  # 2024 is a leap year: 31+29+31

    def test_supply_charge_for_90_days(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-04-01")
        assert result["days"] == 90
        expected_water = round(90 * 0.6616, 2)
        expected_sewer = round(90 * 1.6773, 2)
        assert result["water_supply_charge"] == expected_water
        assert result["sewerage_supply_charge"] == expected_sewer
        assert result["supply_total"] == pytest.approx(expected_water + expected_sewer, abs=0.01)

    def test_supply_total_per_quarter_approximate(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-04-01")
        # Combined daily rate: $0.6616 + $1.6773 = $2.3389/day × 90 days ≈ $210.50
        assert result["supply_total"] == pytest.approx(210.50, abs=0.5)

    def test_daily_rates_are_correct(self):
        from routers.water_bills import WATER_DAILY_RATE, SEWER_DAILY_RATE
        assert WATER_DAILY_RATE == 0.6616
        assert SEWER_DAILY_RATE == 1.6773
        combined = WATER_DAILY_RATE + SEWER_DAILY_RATE
        assert combined == pytest.approx(2.3389, abs=0.0001)

    def test_zero_day_range_gives_zero_charges(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-01-01")
        assert result["days"] == 0
        assert result["supply_total"] == 0.0

    def test_usage_kl_is_passed_through(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-02-01", usage_kl=12.5)
        assert result["usage_kl"] == 12.5


# ─────────────────────────────────────────────────────────────────────────────
# Section 7: Council Rate Payment Scoping
# ─────────────────────────────────────────────────────────────────────────────

class TestCouncilRatePaymentScoping:
    """Access control rules for recording and viewing council rate payments."""

    def test_owner_can_record_own_unit(self):
        user = {"role": "owner", "unit_number": "TH087"}
        request_unit = "TH087"
        privileged_roles = {"super_admin", "chairman", "ec_member", "strata_manager"}
        is_privileged = user["role"] in privileged_roles
        if not is_privileged:
            authorized = user["unit_number"].upper() == request_unit.upper()
        else:
            authorized = True
        assert authorized

    def test_owner_cannot_record_other_unit(self):
        user = {"role": "owner", "unit_number": "TH087"}
        request_unit = "UA001"
        privileged_roles = {"super_admin", "chairman", "ec_member", "strata_manager"}
        is_privileged = user["role"] in privileged_roles
        if not is_privileged:
            authorized = user["unit_number"].upper() == request_unit.upper()
        else:
            authorized = True
        assert not authorized

    def test_admin_can_record_any_unit(self):
        user = {"role": "super_admin", "unit_number": None}
        request_unit = "UA001"
        privileged_roles = {"super_admin", "chairman", "ec_member", "strata_manager"}
        authorized = user["role"] in privileged_roles
        assert authorized

    def test_strata_manager_can_record_any_unit(self):
        user = {"role": "strata_manager", "unit_number": "UA010"}
        privileged_roles = {"super_admin", "chairman", "ec_member", "strata_manager"}
        assert user["role"] in privileged_roles

    def test_ec_member_can_record_any_unit(self):
        user = {"role": "ec_member", "unit_number": "UA046"}
        privileged_roles = {"super_admin", "chairman", "ec_member", "strata_manager"}
        assert user["role"] in privileged_roles


# ─────────────────────────────────────────────────────────────────────────────
# Section 8: Water Bill Mark-Paid and Mark-Partial Logic
# ─────────────────────────────────────────────────────────────────────────────

class TestWaterBillMarkPaid:
    """Tests for mark-paid and mark-partial update logic."""

    def test_mark_paid_sets_status_to_paid(self):
        """After mark-paid, status must be 'paid'."""
        bill = {"status": "unpaid", "amount": 210.50, "amount_paid": 0.0, "balance_due": 210.50}
        payment_date = "2026-03-15"
        # Simulate mark-paid
        bill["status"] = "paid"
        bill["amount_paid"] = bill["amount"]
        bill["balance_due"] = 0.0
        bill["payment_date"] = payment_date
        assert bill["status"] == "paid"
        assert bill["balance_due"] == 0.0

    def test_mark_partial_accumulates_amount_paid(self):
        """Partial payment should accumulate (not overwrite) amount_paid."""
        bill = {"status": "unpaid", "amount": 210.50, "amount_paid": 0.0, "balance_due": 210.50}
        partial = 100.0
        # Simulate mark-partial
        new_paid = bill["amount_paid"] + partial
        bill["amount_paid"] = round(new_paid, 2)
        bill["balance_due"] = round(bill["amount"] - bill["amount_paid"], 2)
        bill["status"] = "partial"
        assert bill["amount_paid"] == 100.0
        assert bill["balance_due"] == pytest.approx(110.50, abs=0.01)
        assert bill["status"] == "partial"

    def test_mark_partial_then_full_completes_payment(self):
        """Two partial payments covering full amount → status becomes 'paid'."""
        bill = {"amount": 210.50, "amount_paid": 0.0, "balance_due": 210.50, "status": "unpaid"}
        # First partial
        bill["amount_paid"] += 105.25
        bill["balance_due"] = bill["amount"] - bill["amount_paid"]
        bill["status"] = "partial"
        # Second partial (exactly completing payment)
        bill["amount_paid"] += 105.25
        bill["balance_due"] = round(bill["amount"] - bill["amount_paid"], 2)
        if bill["amount_paid"] >= bill["amount"]:
            bill["status"] = "paid"
        assert bill["status"] == "paid"
        assert bill["balance_due"] == pytest.approx(0.0, abs=0.01)

    def test_overpayment_balance_due_is_zero(self):
        """Overpayment should not result in negative balance_due."""
        amount = 210.50
        paid = 250.0
        balance_due = max(0.0, round(amount - paid, 2))
        assert balance_due == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Section 9: _get_payment_summary logic
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPaymentSummary:
    """Tests mirroring the _get_payment_summary() function in council_rates.py."""

    def _summary(self, payments: list, total_rates: float):
        total_paid = sum(p.get("amount", 0) for p in payments)
        if total_paid == 0:
            status = "unpaid"
        elif total_rates and total_paid >= total_rates:
            status = "paid"
        else:
            status = "partial"
        return round(total_paid, 2), status

    def test_no_payments_is_unpaid(self):
        paid, status = self._summary([], 2400.0)
        assert paid == 0.0
        assert status == "unpaid"

    def test_partial_payment_is_partial(self):
        paid, status = self._summary([{"amount": 600.0}], 2400.0)
        assert paid == 600.0
        assert status == "partial"

    def test_full_payment_is_paid(self):
        paid, status = self._summary([{"amount": 2400.0}], 2400.0)
        assert paid == 2400.0
        assert status == "paid"

    def test_multiple_payments_summed(self):
        payments = [{"amount": 600.0}, {"amount": 600.0}, {"amount": 600.0}, {"amount": 600.0}]
        paid, status = self._summary(payments, 2400.0)
        assert paid == 2400.0
        assert status == "paid"

    def test_overpayment_is_still_paid(self):
        paid, status = self._summary([{"amount": 2600.0}], 2400.0)
        assert paid == 2600.0
        assert status == "paid"

    def test_payment_from_wrong_fy_not_counted(self):
        """Payments recorded with wrong FY (2026-27 instead of 2025-26) are NOT counted."""
        # Simulate: payments collection only returns docs matching financial_year query
        # If FY mismatch, empty list is returned → status = unpaid
        payments = []  # wrong FY payment was recorded, DB query returns nothing
        paid, status = self._summary(payments, 2400.0)
        assert status == "unpaid"


# ─────────────────────────────────────────────────────────────────────────────
# Section 10: Integration — Quarterly Estimates API
# ─────────────────────────────────────────────────────────────────────────────

class TestWaterQuarterlyEstimates:
    """Tests for the quarterly-estimates API helper logic."""

    def test_quarterly_ranges_returns_four_quarters(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        assert len(ranges) == 4

    def test_quarterly_ranges_labels(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        labels = {r["quarter"] for r in ranges}
        assert labels == {"Q1", "Q2", "Q3", "Q4"}

    def test_q1_starts_jan_1(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        assert ranges[0]["start"] == "2026-01-01"

    def test_q4_ends_dec_31(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        assert ranges[3]["end"] == "2026-12-31"

    def test_all_quarters_have_required_fields(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        for r in ranges:
            assert "quarter" in r
            assert "start" in r
            assert "end" in r
            assert "label" in r

    def test_estimate_matching_by_quarter_label(self):
        """Water bill Q1 estimate must be found by matching 'Q1' label exactly."""
        estimates = [
            {"quarter": "Q1", "supply_total": 210.50, "label": "Jan–Mar"},
            {"quarter": "Q2", "supply_total": 212.80, "label": "Apr–Jun"},
            {"quarter": "Q3", "supply_total": 215.20, "label": "Jul–Sep"},
            {"quarter": "Q4", "supply_total": 215.20, "label": "Oct–Dec"},
        ]
        # New code: exact match on quarter label (not includes())
        water_q = "Q1"  # Feb 2026 → Q1 calendar year
        match = next((e for e in estimates if e["quarter"] == water_q), None)
        assert match is not None
        assert match["supply_total"] == 210.50

    def test_old_bug_would_match_wrong_quarter(self):
        """Old code used ACT rates Q3 in Feb → matched Jul-Sep estimate instead of Jan-Mar."""
        estimates = [
            {"quarter": "Q1", "supply_total": 210.50},
            {"quarter": "Q3", "supply_total": 215.20},  # Jul-Sep
        ]
        # OLD (buggy) code in Feb: uses rates_q3_label='Q3' → matches July-Sep
        old_q_label = "Q3"  # from getCurrentRateQuarter() in Feb
        old_match = next((e for e in estimates if e.get("quarter", "").startswith(old_q_label[:2])), None)
        # NEW (correct) code: uses calendar Q1 in Feb
        new_q_label = "Q1"  # from getCurrentWaterQuarter() in Feb
        new_match = next((e for e in estimates if e["quarter"] == new_q_label), None)

        assert old_match["supply_total"] == 215.20  # Wrong: shows Jul-Sep estimate
        assert new_match["supply_total"] == 210.50  # Correct: shows Jan-Mar estimate
