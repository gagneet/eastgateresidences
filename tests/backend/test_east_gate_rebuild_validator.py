"""Tests for scripts/validate_east_gate_rebuild.py's Phase C reconciliation gate.

Covers the one hard rule this validator exists to enforce: a derived-income record
(reconciliation_status=reconciled_control_income_detail_pending, or
derived_amount_posting_allowed=False) must never be treated as promotable/fully-reconciled
by any automated process, regardless of its variance being zero.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts"))

from validate_east_gate_rebuild import _check_fund, is_promotable  # noqa: E402


def test_is_promotable_true_for_ordinary_reconciled_fund():
    fund_doc = {
        "opening_balance_cents": 0, "actual_income_cents": 100, "actual_spent_cents": 50,
        "closing_balance_cents": 50,
    }
    assert is_promotable(fund_doc) is True


def test_is_promotable_false_for_derived_income_status():
    fund_doc = {"reconciliation_status": "reconciled_control_income_detail_pending"}
    assert is_promotable(fund_doc) is False


def test_is_promotable_false_when_derived_amount_posting_allowed_is_false():
    # Even without the status set, the explicit False flag alone must block promotion.
    fund_doc = {"reconciliation_status": "reconciled", "derived_amount_posting_allowed": False}
    assert is_promotable(fund_doc) is False


def test_derived_income_fund_reports_zero_variance_but_stays_non_promotable():
    # Mirrors East Gate's real 2023 record: opening+income-expense=closing holds exactly,
    # yet the record must still never be auto-promoted.
    fund_doc = {
        "opening_balance_cents": 5_544_364, "actual_income_cents": 24_619_929,
        "actual_spent_cents": 25_237_970, "closing_balance_cents": 4_926_323,
        "reconciliation_status": "reconciled_control_income_detail_pending",
        "derived_amount_posting_allowed": False,
    }
    result = _check_fund(2023, "admin", fund_doc)
    assert result["variance_cents"] == 0
    assert result["status"] == "reconciled_control_income_detail_pending"
    assert result["promotable"] is False


def test_partial_year_never_computes_a_variance():
    fund_doc = {
        "opening_balance_cents": 100, "actual_income_cents": None,
        "actual_spent_cents": 50, "closing_balance_cents": None,
        "reconciliation_status": "partial",
    }
    result = _check_fund(2026, "admin", fund_doc)
    assert result["status"] == "partial"
    assert result["variance_cents"] is None


def test_missing_control_when_a_required_field_is_absent_and_not_partial():
    fund_doc = {"opening_balance_cents": 0, "actual_income_cents": None, "actual_spent_cents": 0, "closing_balance_cents": 0}
    result = _check_fund(2021, "admin", fund_doc)
    assert result["status"] == "missing_control"


def test_nonzero_variance_without_reconciliation_basis_is_flagged_unexplained():
    fund_doc = {
        "opening_balance_cents": 0, "actual_income_cents": 100, "actual_spent_cents": 50,
        "closing_balance_cents": 999,  # doesn't match opening+income-spent=50
    }
    result = _check_fund(2022, "admin", fund_doc)
    assert result["variance_cents"] == 949
    assert result["status"] == "variance_unexplained"
