"""Regression test for the quarterly-budget "Outstanding" netting bug (issue 4 of the
arrears/finance batch): a building's Outstanding must be the SUM of per-unit amounts still owed,
never `total_levied - Σ signed(net_balance)`, which lets one owner's overpayment (credit) cancel
another owner's shortfall. Covers utils/finance_helpers.py::sum_ledger_collected_outstanding.
"""
from __future__ import annotations

from utils.finance_helpers import sum_ledger_collected_outstanding


def test_credit_never_nets_against_another_units_arrears():
    """A(owes 500) + B(credit 300, overpaid) + C(paid). Outstanding must be 500 (A's real
    obligation), NOT 200 (500 netted down by B's 300 credit — the old signed-sum bug)."""
    entries = [
        {"total_levied": 1000, "net_balance": 500},    # owes 500
        {"total_levied": 1000, "net_balance": -300},   # credit 300 (paid ahead)
        {"total_levied": 1000, "net_balance": 0},       # fully paid
    ]
    rollup = sum_ledger_collected_outstanding(entries)

    assert rollup["total_levied"] == 3000
    # Only A's positive balance counts — B's credit does not reduce it.
    assert rollup["total_outstanding"] == 500
    # Collected = levied - outstanding; a credit unit's excess never inflates collected.
    assert rollup["total_collected"] == 2500

    # The old, buggy formula would have produced these netted values — assert we are NOT them.
    signed_sum = 500 - 300 + 0
    assert rollup["total_outstanding"] != max(0.0, 3000 - (3000 - signed_sum))  # != 200
    assert rollup["total_collected"] != 3000 - signed_sum                        # != 2800


def test_all_paid_or_credit_is_zero_outstanding():
    entries = [
        {"total_levied": 800, "net_balance": -50},
        {"total_levied": 800, "net_balance": 0},
    ]
    rollup = sum_ledger_collected_outstanding(entries)
    assert rollup["total_outstanding"] == 0.0
    assert rollup["total_collected"] == 1600


def test_handles_missing_and_none_fields():
    entries = [
        {"total_levied": None, "net_balance": None},
        {"total_levied": 500, "net_balance": 120.5},
        {},  # entirely empty
    ]
    rollup = sum_ledger_collected_outstanding(entries)
    assert rollup["total_levied"] == 500
    assert rollup["total_outstanding"] == 120.5
    assert rollup["total_collected"] == 379.5


def test_empty_ledger_is_all_zero():
    rollup = sum_ledger_collected_outstanding([])
    assert rollup == {"total_levied": 0, "total_collected": 0, "total_outstanding": 0}
