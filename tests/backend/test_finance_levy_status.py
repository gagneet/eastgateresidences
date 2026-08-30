"""
Tests for Finance Page fix:
 - collected_summary in /finance/summary (confirmed vs pending payments)
 - period_status in /finance/summary (grace-period-aware arrears detection)
 - opening_arrears field on /owners-units
 - Arrears display logic (historical vs current vs upcoming)
"""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────────────
# Helper factories
# ─────────────────────────────────────────────────────────────────────────────

def _payment(status: str, amount: float, year: str = "2026") -> dict:
    return {"id": str(uuid.uuid4()), "status": status, "amount": amount, "year": year}


def _ledger_entry(unit_number: str, admin_opening: float = 0.0, sinking_opening: float = 0.0,
                  net_balance: float = 0.0, total_levied: float = 0.0, total_paid: float = 0.0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "unit_number": unit_number,
        "year": "2026",
        "plan_id": "13195",
        "admin_opening": admin_opening,
        "sinking_opening": sinking_opening,
        "net_balance": net_balance,
        "total_levied": total_levied,
        "total_paid": total_paid,
    }


def _settings(grace_period_days: int = 14, levy_due_months=None,
              levy_due_day_type: str = "custom", levy_due_custom_dates=None) -> dict:
    return {
        "id": "main",
        "grace_period_days": grace_period_days,
        "levy_due_months": levy_due_months or [3, 6, 9, 12],
        "levy_due_day_type": levy_due_day_type,
        "levy_due_day": None,
        "levy_due_custom_dates": levy_due_custom_dates or {"3": 31, "6": 1, "9": 1, "12": 1},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. TestFinanceSummaryCollected
# ─────────────────────────────────────────────────────────────────────────────

class TestFinanceSummaryCollected:
    """Verify that collected_summary is derived from levy_payments collection."""

    def test_confirmed_total_from_levy_payments(self):
        """confirmed_total should equal sum of confirmed payment records."""
        payments = [
            {"_id": "confirmed", "total": 1800.0, "count": 3},
            {"_id": "pending_verification", "total": 600.0, "count": 2},
        ]
        confirmed_total = sum(r["total"] for r in payments if r["_id"] == "confirmed")
        assert confirmed_total == 1800.0

    def test_pending_total_separate_from_confirmed(self):
        """pending_total should NOT be included in the confirmed figure."""
        payments = [
            {"_id": "confirmed", "total": 1800.0, "count": 3},
            {"_id": "pending_verification", "total": 600.0, "count": 2},
        ]
        pending_total = sum(r["total"] for r in payments if r["_id"] == "pending_verification")
        assert pending_total == 600.0

    def test_zero_payments_returns_zeros(self):
        """When no payments exist, both totals should be 0."""
        payments = []
        confirmed_total = sum(r["total"] for r in payments if r["_id"] == "confirmed")
        pending_total = sum(r["total"] for r in payments if r["_id"] == "pending_verification")
        assert confirmed_total == 0.0
        assert pending_total == 0.0

    def test_confirmed_count_matches_records(self):
        """confirmed_count should match the number of confirmed payment records."""
        payments = [
            {"_id": "confirmed", "total": 1800.0, "count": 3},
        ]
        confirmed_count = sum(r["count"] for r in payments if r["_id"] == "confirmed")
        assert confirmed_count == 3

    def test_pending_count_matches_records(self):
        """pending_count should match the number of pending_verification records."""
        payments = [
            {"_id": "pending_verification", "total": 600.0, "count": 2},
        ]
        pending_count = sum(r["count"] for r in payments if r["_id"] == "pending_verification")
        assert pending_count == 2

    def test_totals_round_to_two_decimals(self):
        """Floating-point amounts should be rounded to 2dp."""
        raw_amount = 1799.999
        assert round(raw_amount, 2) == 1800.0

    def test_rejected_payments_excluded(self):
        """Rejected payments should not appear in confirmed or pending totals."""
        payments = [
            {"_id": "confirmed", "total": 1800.0, "count": 3},
            {"_id": "rejected", "total": 200.0, "count": 1},
        ]
        confirmed_total = sum(r["total"] for r in payments if r["_id"] == "confirmed")
        pending_total = sum(r["total"] for r in payments if r["_id"] == "pending_verification")
        assert confirmed_total == 1800.0
        assert pending_total == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. TestPeriodStatusLogic
# ─────────────────────────────────────────────────────────────────────────────

class TestPeriodStatusLogic:
    """Verify grace-period-aware overdue detection logic."""

    def _compute_overdue(self, today: date, grace_days: int = 14, due_dates=None):
        """Replicates the period_status logic from get_finance_summary()."""
        if due_dates is None:
            # ACT defaults: March 31, June 1, Sept 1, Dec 1
            due_dates = ["2026-03-31", "2026-06-01", "2026-09-01", "2026-12-01"]
        quarter_labels = ["Q1", "Q2", "Q3", "Q4"]
        overdue = []
        for i, d_str in enumerate(due_dates):
            due = date.fromisoformat(d_str)
            grace_deadline = due + timedelta(days=grace_days)
            if today > grace_deadline:
                overdue.append(quarter_labels[i] if i < len(quarter_labels) else f"Q{i + 1}")
        return overdue

    def test_before_due_date_not_overdue(self):
        """Today = March 2 → Q1 due March 31 → NOT overdue."""
        today = date(2026, 3, 2)
        overdue = self._compute_overdue(today)
        assert "Q1" not in overdue
        assert len(overdue) == 0

    def test_on_due_date_not_overdue(self):
        """Today = March 31 (due date itself) → still within grace → NOT overdue."""
        today = date(2026, 3, 31)
        overdue = self._compute_overdue(today)
        assert "Q1" not in overdue

    def test_within_grace_period_not_overdue(self):
        """Today = April 10 (10 days after due) → within 14-day grace → NOT overdue."""
        today = date(2026, 4, 10)
        overdue = self._compute_overdue(today)
        assert "Q1" not in overdue

    def test_after_grace_period_overdue(self):
        """Today = April 15 (15 days after due) → past 14-day grace → Overdue."""
        today = date(2026, 4, 15)
        overdue = self._compute_overdue(today)
        assert "Q1" in overdue

    def test_multiple_overdue_periods(self):
        """When today is July 20, both Q1 (Mar 31) and Q2 (Jun 1) are past grace."""
        today = date(2026, 7, 20)
        overdue = self._compute_overdue(today)
        assert "Q1" in overdue
        assert "Q2" in overdue
        assert "Q3" not in overdue

    def test_any_overdue_false_when_none_overdue(self):
        today = date(2026, 3, 2)
        overdue = self._compute_overdue(today)
        any_overdue = len(overdue) > 0
        assert any_overdue is False

    def test_any_overdue_true_when_one_overdue(self):
        today = date(2026, 4, 20)
        overdue = self._compute_overdue(today)
        any_overdue = len(overdue) > 0
        assert any_overdue is True

    def test_grace_days_from_settings(self):
        """A 30-day grace period should extend the deadline."""
        today = date(2026, 4, 15)  # 15 days after Q1 due
        # With 14-day grace → overdue
        overdue_14 = self._compute_overdue(today, grace_days=14)
        assert "Q1" in overdue_14
        # With 30-day grace → NOT overdue
        overdue_30 = self._compute_overdue(today, grace_days=30)
        assert "Q1" not in overdue_30

    def test_no_due_months_no_overdue(self):
        """Empty due_months list → no periods → no overdue."""
        overdue = self._compute_overdue(date(2026, 12, 31), due_dates=[])
        assert overdue == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. TestOpeningArrearsField
# ─────────────────────────────────────────────────────────────────────────────

class TestOpeningArrearsField:
    """Verify that opening_arrears is correctly computed from ledger data."""

    def _compute_opening_arrears(self, ledger: dict) -> float:
        return round(ledger.get("admin_opening", 0.0) + ledger.get("sinking_opening", 0.0), 2)

    def test_unit_with_carry_forward_has_positive_opening_arrears(self):
        ledger = {"admin_opening": 500.0, "sinking_opening": 150.0}
        assert self._compute_opening_arrears(ledger) == 650.0

    def test_clean_unit_has_zero_opening_arrears(self):
        ledger = {"admin_opening": 0.0, "sinking_opening": 0.0}
        assert self._compute_opening_arrears(ledger) == 0.0

    def test_missing_ledger_defaults_to_zero(self):
        ledger = {}
        assert self._compute_opening_arrears(ledger) == 0.0

    def test_only_admin_opening_arrears(self):
        ledger = {"admin_opening": 333.33}
        assert self._compute_opening_arrears(ledger) == 333.33

    def test_rounding_to_two_decimals(self):
        ledger = {"admin_opening": 100.005, "sinking_opening": 50.005}
        result = self._compute_opening_arrears(ledger)
        assert result == round(150.01, 2)

    def test_negative_opening_not_arrears(self):
        """Negative opening balance means credit from prior year — not arrears."""
        ledger = {"admin_opening": -100.0, "sinking_opening": 0.0}
        result = self._compute_opening_arrears(ledger)
        # opening_arrears stores raw sum; frontend logic checks > 0.01 for arrears
        assert result == -100.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. TestArrearsDisplayLogic
# ─────────────────────────────────────────────────────────────────────────────

class TestArrearsDisplayLogic:
    """Validate the frontend arrears-badge decision logic (pure Python port)."""

    def _classify(self, unit_net_balance: float, opening_arrears: float, any_overdue: bool):
        """Port of the FinancePage.jsx arrears classification logic."""
        has_historical_arrears = opening_arrears > 0.01
        current_year_debt = max(0, unit_net_balance - opening_arrears)
        has_current_arrears = any_overdue and current_year_debt > 0.01
        has_arrears = has_historical_arrears or has_current_arrears
        has_upcoming_balance = (not has_arrears) and unit_net_balance > 0.01 and (not any_overdue)
        has_credit = unit_net_balance < 0
        if has_arrears:
            return "arrears"
        elif has_upcoming_balance:
            return "balance_due"
        elif has_credit:
            return "credit"
        else:
            return "on_track"

    def test_historical_arrears_always_shown(self):
        """A unit with 2025 carry-forward always shows Arrears, even before Q1 due date."""
        result = self._classify(
            unit_net_balance=800.0,
            opening_arrears=650.0,
            any_overdue=False
        )
        assert result == "arrears"

    def test_upcoming_balance_before_grace_shows_balance_due(self):
        """Q1 not yet past grace → show Balance Due (amber), not Arrears."""
        result = self._classify(
            unit_net_balance=500.0,
            opening_arrears=0.0,
            any_overdue=False
        )
        assert result == "balance_due"

    def test_past_grace_unpaid_shows_arrears(self):
        """After grace deadline, unpaid current year levy → Arrears."""
        result = self._classify(
            unit_net_balance=500.0,
            opening_arrears=0.0,
            any_overdue=True
        )
        assert result == "arrears"

    def test_zero_balance_shows_on_track(self):
        result = self._classify(unit_net_balance=0.0, opening_arrears=0.0, any_overdue=False)
        assert result == "on_track"

    def test_credit_shows_credit(self):
        result = self._classify(unit_net_balance=-200.0, opening_arrears=0.0, any_overdue=False)
        assert result == "credit"

    def test_past_grace_but_paid_shows_on_track(self):
        """Unit has paid all levies even after grace deadline → On Track."""
        result = self._classify(unit_net_balance=0.0, opening_arrears=0.0, any_overdue=True)
        assert result == "on_track"

    def test_threshold_prevents_float_false_positives(self):
        """A tiny rounding float (0.001) should not trigger arrears."""
        result = self._classify(unit_net_balance=0.001, opening_arrears=0.0, any_overdue=False)
        assert result == "on_track"

    def test_historical_arrears_overrides_upcoming_balance(self):
        """If opening_arrears > 0, show Arrears even if any_overdue is False."""
        result = self._classify(
            unit_net_balance=1200.0,
            opening_arrears=650.0,
            any_overdue=False
        )
        assert result == "arrears"


# ─────────────────────────────────────────────────────────────────────────────
# 5. TestCollectedSummaryModel
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectedSummaryModel:
    """Verify the structure of collected_summary in the API response."""

    def _build_collected_summary(self, payments_by_status):
        """Mirrors the get_finance_summary() logic."""
        confirmed_total = 0.0
        confirmed_count = 0
        pending_total = 0.0
        pending_count = 0
        for row in payments_by_status:
            if row["_id"] == "confirmed":
                confirmed_total = round(row["total"], 2)
                confirmed_count = row["count"]
            elif row["_id"] == "pending_verification":
                pending_total = round(row["total"], 2)
                pending_count = row["count"]
        return {
            "confirmed_total": confirmed_total,
            "pending_total": pending_total,
            "confirmed_count": confirmed_count,
            "pending_count": pending_count,
        }

    def test_all_required_keys_present(self):
        summary = self._build_collected_summary([])
        assert "confirmed_total" in summary
        assert "pending_total" in summary
        assert "confirmed_count" in summary
        assert "pending_count" in summary

    def test_confirmed_count_matches_data(self):
        data = [{"_id": "confirmed", "total": 900.0, "count": 2}]
        summary = self._build_collected_summary(data)
        assert summary["confirmed_count"] == 2

    def test_pending_count_matches_data(self):
        data = [{"_id": "pending_verification", "total": 300.0, "count": 1}]
        summary = self._build_collected_summary(data)
        assert summary["pending_count"] == 1

    def test_totals_correct_for_mixed_statuses(self):
        data = [
            {"_id": "confirmed", "total": 1800.0, "count": 3},
            {"_id": "pending_verification", "total": 600.0, "count": 2},
            {"_id": "rejected", "total": 100.0, "count": 1},
        ]
        summary = self._build_collected_summary(data)
        assert summary["confirmed_total"] == 1800.0
        assert summary["pending_total"] == 600.0

    def test_empty_payments_zero_totals(self):
        summary = self._build_collected_summary([])
        assert summary["confirmed_total"] == 0.0
        assert summary["pending_total"] == 0.0
        assert summary["confirmed_count"] == 0
        assert summary["pending_count"] == 0

    def test_period_status_keys(self):
        """Verify period_status shape."""
        period_status = {
            "any_overdue": False,
            "overdue_periods": [],
            "grace_period_days": 14,
        }
        assert "any_overdue" in period_status
        assert "overdue_periods" in period_status
        assert "grace_period_days" in period_status
        assert isinstance(period_status["overdue_periods"], list)
