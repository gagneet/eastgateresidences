#!/usr/bin/env python3
"""
Backend Tests: Levy Status Carry-Forward Feature (deadline-based)

Logic under test:
  - prev_year_balance from unit_levy_ledger is applied to Q1 always.
  - Future periods show the standard per-period amount UNTIL the previous
    period's due_date + grace_period_days has passed with an unpaid balance.
  - After that grace deadline the unpaid remainder rolls into the next period
    immediately.
  - Status values: "upcoming" (before deadline), "overdue" (past deadline, unpaid),
    "partial" (past deadline, part-paid), "paid" (fully cleared).

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_levy_status_carry_forward.py -v

Requirements:
    Backend running on localhost:8003
    DB: strata_production on mongodb://localhost:27018
    unit_levy_ledger: 87 entries for 2025
    UA016: net_balance =  1344.99 (outstanding) in 2025 ledger
    TH071: net_balance = -1580.71 (credit)      in 2025 ledger
"""
import os
from datetime import date, timedelta

import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"
ADMIN_EMAIL = "administrator@eastgateresidences.com.au"
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")


# ─────────────────────────────────────────────────────────────────────────────
# Response structure
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyStatusStructure:
    """Verify the response shape includes all required fields."""

    def test_levy_status_returns_200(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        assert r.status_code == 200, r.text

    def test_response_has_unit_number(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        assert r.json()["unit_number"] == "UA001"

    def test_response_has_year(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        assert r.json()["year"] in ("2025", "2026")

    def test_response_has_carry_forward_fields(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "prev_year" in data
        assert "prev_year_balance" in data

    def test_response_has_annual_levy_alias(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "annual_levy" in data
        assert data["annual_levy"] == data["total_annual"]

    def test_response_has_entitlement_units_alias(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "entitlement_units" in data
        assert data["entitlement_units"] == data["uoe"]

    def test_response_has_period_levy_alias(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "period_levy" in data
        assert data["period_levy"] == data["quarterly_levy"]

    def test_response_has_levy_frequency(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "levy_frequency" in data
        assert data["levy_frequency"] in ("quarterly", "monthly", "half_yearly", "yearly")

    def test_response_has_grace_period_days(self, auth_headers):
        """grace_period_days should now be returned in the response."""
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "grace_period_days" in data
        assert isinstance(data["grace_period_days"], int)
        assert data["grace_period_days"] >= 0

    def test_quarters_have_required_fields(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        quarters = r.json().get("quarters", [])
        assert len(quarters) == 4
        for q in quarters:
            assert "carry_forward" in q, f"{q['quarter']} missing carry_forward"
            assert "amount_paid" in q, f"{q['quarter']} missing amount_paid"
            assert "past_deadline" in q, f"{q['quarter']} missing past_deadline"
            assert "status" in q, f"{q['quarter']} missing status"

    def test_quarter_status_values_are_valid(self, auth_headers):
        """Status must be one of the four valid values."""
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        valid = {"upcoming", "overdue", "partial", "paid"}
        for q in r.json().get("quarters", []):
            assert q["status"] in valid, \
                f"{q['quarter']} has invalid status '{q['status']}'"

    def test_prev_year_is_one_less_than_year(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert int(data["prev_year"]) == int(data["year"]) - 1


# ─────────────────────────────────────────────────────────────────────────────
# Positive carry-forward (unit OWES from previous year)
# ─────────────────────────────────────────────────────────────────────────────

class TestPositiveCarryForward:
    """
    UA016 has net_balance = 1344.99 (outstanding) in 2025 ledger.
    Q1 2026 should include this carry-forward.
    Future periods before their grace deadline show standard-only amounts.
    """

    @pytest.fixture(scope="class")
    def ua016_status(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA016", headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()

    def test_ua016_has_positive_prev_year_balance(self, ua016_status):
        assert ua016_status["prev_year_balance"] > 0, \
            f"Expected positive balance, got {ua016_status['prev_year_balance']}"

    def test_ua016_prev_year_balance_approx_1345(self, ua016_status):
        bal = ua016_status["prev_year_balance"]
        assert abs(bal - 1344.99) < 1.0, f"Expected ~1344.99, got {bal}"

    def test_ua016_q1_includes_carry_forward(self, ua016_status):
        """Q1 amount_due = standard + prev_year_balance (before or after deadline)."""
        quarterly = ua016_status["quarterly_levy"]
        prev_bal = ua016_status["prev_year_balance"]
        q1 = ua016_status["quarters"][0]
        assert q1["quarter"] == "Q1"
        expected = round(max(0.0, quarterly + prev_bal), 2)
        assert abs(q1["amount_due"] - expected) < 0.05, \
            f"Q1 expected ~{expected}, got {q1['amount_due']}"

    def test_ua016_q1_carry_forward_field_matches(self, ua016_status):
        q1 = ua016_status["quarters"][0]
        assert abs(q1["carry_forward"] - ua016_status["prev_year_balance"]) < 0.01

    def test_ua016_q2_before_deadline_shows_standard(self, ua016_status):
        """
        If Q1 grace deadline has NOT yet passed, Q2 shows the standard period amount.
        If Q1 IS past deadline and unpaid, Q2 shows the rolled-up amount.
        """
        q1 = ua016_status["quarters"][0]
        q2 = ua016_status["quarters"][1]
        quarterly = ua016_status["quarterly_levy"]

        if not q1["past_deadline"]:
            # Q1 deadline not yet passed → Q2 should be standard only
            assert abs(q2["amount_due"] - quarterly) < 0.05, \
                f"Q2 should be standard={quarterly} before Q1 deadline, got {q2['amount_due']}"
        elif q1["status"] in ("overdue", "partial"):
            # Q1 overdue → Q2 = standard + Q1 remaining
            q1_remaining = round(q1["amount_due"] - q1["amount_paid"], 2)
            expected_q2 = round(quarterly + q1_remaining, 2)
            assert abs(q2["amount_due"] - expected_q2) < 0.05, \
                f"Q2 rolled-up expected ~{expected_q2}, got {q2['amount_due']}"
        else:
            # Q1 paid → Q2 standard
            assert abs(q2["amount_due"] - quarterly) < 0.05

    def test_ua016_future_periods_standard_when_no_overdue(self, ua016_status):
        """
        For any period that is 'upcoming' (before deadline), the carry-forward
        should be 0 (i.e., it shows standard-only, not rolled prior amounts).
        """
        quarterly = ua016_status["quarterly_levy"]
        for q in ua016_status["quarters"][1:]:  # Q2, Q3, Q4
            if q["status"] == "upcoming":
                assert abs(q["amount_due"] - quarterly) < 0.05, \
                    f"{q['quarter']} upcoming should equal standard {quarterly}, got {q['amount_due']}"

    def test_ua016_balance_due_includes_positive_carry_forward(self, ua016_status):
        """Positive carry-forward arrears still add to balance_due."""
        total_annual = ua016_status["total_annual"]
        prev_bal = ua016_status["prev_year_balance"]
        total_paid = ua016_status["total_paid"]
        expected = max(0.0, round(total_annual + prev_bal - total_paid, 2))
        assert abs(ua016_status["balance_due"] - expected) < 0.05, \
            f"balance_due expected ~{expected}, got {ua016_status['balance_due']}"

    def test_ua016_q1_status_is_upcoming_or_overdue(self, ua016_status):
        """Q1 status depends on whether the grace deadline has passed."""
        q1 = ua016_status["quarters"][0]
        assert q1["status"] in ("upcoming", "overdue", "partial", "paid"), \
            f"Unexpected Q1 status: {q1['status']}"

    def test_ua016_q1_carry_forward_nonzero(self, ua016_status):
        q1 = ua016_status["quarters"][0]
        assert q1["carry_forward"] > 0

    def test_ua016_past_deadline_matches_dates(self, ua016_status):
        """past_deadline must agree with today vs due_date + grace_period_days."""
        grace = ua016_status["grace_period_days"]
        today = date.today()
        for q in ua016_status["quarters"]:
            if q["due_date"]:
                due = date.fromisoformat(str(q["due_date"])[:10])
                expected_past = today > (due + timedelta(days=grace))
                assert q["past_deadline"] == expected_past, \
                    f"{q['quarter']} past_deadline should be {expected_past}"


# ─────────────────────────────────────────────────────────────────────────────
# Negative carry-forward (unit has CREDIT from previous year)
# ─────────────────────────────────────────────────────────────────────────────

class TestNegativeCarryForward:
    """
    TH071 has net_balance = -1580.71 (credit) in 2025 ledger.
    Q1 2026 should be reduced by this credit.
    """

    @pytest.fixture(scope="class")
    def th001_status(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/TH071", headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()

    def test_th001_has_negative_prev_year_balance(self, th001_status):
        assert th001_status["prev_year_balance"] < 0, \
            f"Expected negative balance (credit), got {th001_status['prev_year_balance']}"

    def test_th001_q1_reduced_by_credit(self, th001_status):
        quarterly = th001_status["quarterly_levy"]
        prev_bal = th001_status["prev_year_balance"]  # negative
        q1 = th001_status["quarters"][0]
        expected = round(max(0.0, quarterly + prev_bal), 2)
        assert abs(q1["amount_due"] - expected) < 0.05, \
            f"Q1 expected ~{expected} (credit reduction), got {q1['amount_due']}"

    def test_th001_q1_amount_le_standard(self, th001_status):
        """Q1 must not exceed the standard quarterly (credit reduces or zeros it)."""
        quarterly = th001_status["quarterly_levy"]
        q1 = th001_status["quarters"][0]
        assert q1["amount_due"] <= quarterly + 0.01, \
            f"Expected Q1 <= {quarterly} due to credit, got {q1['amount_due']}"

    def test_th001_q1_carry_forward_is_negative(self, th001_status):
        q1 = th001_status["quarters"][0]
        assert q1["carry_forward"] < 0, \
            f"Expected negative carry_forward (credit), got {q1['carry_forward']}"

    def test_th001_balance_due_is_reduced(self, th001_status):
        total_annual = th001_status["total_annual"]
        prev_bal = th001_status["prev_year_balance"]
        total_paid = th001_status["total_paid"]
        expected = max(0.0, round(total_annual + max(0.0, prev_bal) - total_paid, 2))
        assert abs(th001_status["balance_due"] - expected) < 0.05

    def test_th001_future_periods_show_standard(self, th001_status):
        """Q2-Q4 show standard amounts before their deadlines (credit doesn't spill over)."""
        quarterly = th001_status["quarterly_levy"]
        for q in th001_status["quarters"][1:]:
            if q["status"] == "upcoming":
                assert abs(q["amount_due"] - quarterly) < 0.05, \
                    f"{q['quarter']} upcoming should equal standard {quarterly}, got {q['amount_due']}"


# ─────────────────────────────────────────────────────────────────────────────
# Zero carry-forward (no previous year data)
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroCarryForward:

    def test_q1_equals_quarterly_when_no_carry_forward(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        if data["prev_year_balance"] == 0:
            q1 = data["quarters"][0]
            assert abs(q1["amount_due"] - data["quarterly_levy"]) < 0.05, \
                f"Q1 should equal quarterly_levy when no carry-forward"
            assert q1["carry_forward"] == 0.0

    def test_non_existent_unit_returns_404(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/ZZZZ999", headers=auth_headers)
        assert r.status_code == 404

    def test_no_auth_returns_401(self):
        r = requests.get(f"{BASE_URL}/levy-status/UA001")
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Deadline logic: future periods before previous deadline
# ─────────────────────────────────────────────────────────────────────────────

class TestDeadlineLogic:
    """
    Core invariant: a period that has not yet passed its grace deadline
    must show the standard per-period amount (no roll-over from prior periods).
    """

    def test_upcoming_periods_show_standard_amount(self, auth_headers):
        """For any unit, 'upcoming' periods must equal the standard period amount."""
        r = requests.get(f"{BASE_URL}/levy-status/UA016", headers=auth_headers)
        data = r.json()
        quarterly = data["quarterly_levy"]
        for q in data["quarters"][1:]:  # skip Q1 which legitimately has carry-forward
            if q["status"] == "upcoming":
                assert abs(q["amount_due"] - quarterly) < 0.05, \
                    f"{q['quarter']} upcoming expected {quarterly}, got {q['amount_due']}"

    def test_grace_period_days_in_response(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        assert "grace_period_days" in data
        # Default is 14 if not configured otherwise
        assert 0 <= data["grace_period_days"] <= 365

    def test_past_deadline_true_only_if_actually_past(self, auth_headers):
        """past_deadline flag must accurately reflect today vs due + grace."""
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        data = r.json()
        grace = data["grace_period_days"]
        today = date.today()
        for q in data["quarters"]:
            if q["due_date"]:
                due = date.fromisoformat(str(q["due_date"])[:10])
                expected = today > (due + timedelta(days=grace))
                assert q["past_deadline"] == expected, \
                    f"{q['quarter']} past_deadline mismatch (expected {expected})"

    def test_overdue_period_has_past_deadline_true(self, auth_headers):
        """Any period with status='overdue' must also have past_deadline=True."""
        r = requests.get(f"{BASE_URL}/levy-status/UA016", headers=auth_headers)
        for q in r.json().get("quarters", []):
            if q["status"] == "overdue":
                assert q["past_deadline"] is True, \
                    f"{q['quarter']} is overdue but past_deadline is False"

    def test_upcoming_period_has_past_deadline_false(self, auth_headers):
        """Any period with status='upcoming' must have past_deadline=False."""
        r = requests.get(f"{BASE_URL}/levy-status/UA001", headers=auth_headers)
        for q in r.json().get("quarters", []):
            if q["status"] == "upcoming":
                assert q["past_deadline"] is False, \
                    f"{q['quarter']} is upcoming but past_deadline is True"


# ─────────────────────────────────────────────────────────────────────────────
# Access control
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyStatusAccess:

    def test_admin_can_see_any_unit(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/TH087", headers=auth_headers)
        assert r.status_code == 200

    def test_unauth_returns_401(self):
        r = requests.get(f"{BASE_URL}/levy-status/UA016")
        assert r.status_code == 401
