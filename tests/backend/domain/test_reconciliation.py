"""
tests/backend/domain/test_reconciliation.py — Unit tests for domain/reconciliation.py.

Covers:
  - Perfect agreement (all three figures match) → agrees=True, discrepancy=0.
  - One-cent discrepancy → agrees=False (zero tolerance enforced).
  - Each of the three legs disagrees independently.
  - assert_agrees() raises ValueError with helpful breakdown on failure.
  - All arithmetic uses integer cents only (no float operations).
"""
import pytest

from domain.reconciliation import ReconciliationResult, three_way_reconcile


# ── Happy path ────────────────────────────────────────────────────────────────

def test_perfect_agreement():
    result = three_way_reconcile(
        cleared_bank_cents=1_000_000,
        deposits_in_transit_cents=50_000,
        unpresented_withdrawals_cents=30_000,
        ledger_balance_cents=1_020_000,  # 1M + 50k DIT - 30k unpresented
        subledger_sum_cents=1_020_000,
    )
    assert result.agrees is True
    assert result.max_discrepancy_cents == 0
    assert result.adjusted_bank_cents == 1_020_000


def test_assert_agrees_passes_when_balanced():
    result = three_way_reconcile(
        cleared_bank_cents=500_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=500_000,
        subledger_sum_cents=500_000,
    )
    result.assert_agrees()  # must not raise


def test_zero_balance_perfect_agreement():
    result = three_way_reconcile(0, 0, 0, 0, 0)
    assert result.agrees is True
    assert result.max_discrepancy_cents == 0


# ── One-cent tolerance: zero is the only passing value ───────────────────────

def test_one_cent_bank_discrepancy_fails():
    result = three_way_reconcile(
        cleared_bank_cents=1_000_001,  # one cent too high
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=1_000_000,
    )
    assert result.agrees is False
    assert result.max_discrepancy_cents == 1


def test_one_cent_ledger_vs_subledger_fails():
    result = three_way_reconcile(
        cleared_bank_cents=1_000_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=999_999,  # one cent off
    )
    assert result.agrees is False
    assert result.max_discrepancy_cents == 1


# ── Each leg disagreeing independently ───────────────────────────────────────

def test_bank_leg_disagrees():
    result = three_way_reconcile(
        cleared_bank_cents=900_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=1_000_000,
    )
    assert result.agrees is False
    assert result.adjusted_bank_cents == 900_000


def test_ledger_leg_disagrees():
    result = three_way_reconcile(
        cleared_bank_cents=1_000_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_100_000,  # wrong
        subledger_sum_cents=1_000_000,
    )
    assert result.agrees is False


def test_subledger_leg_disagrees():
    result = three_way_reconcile(
        cleared_bank_cents=1_000_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=800_000,  # wrong
    )
    assert result.agrees is False


# ── DIT and unpresented adjustments ──────────────────────────────────────────

def test_deposits_in_transit_increase_adjusted_bank():
    result = three_way_reconcile(
        cleared_bank_cents=800_000,
        deposits_in_transit_cents=200_000,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=1_000_000,
    )
    assert result.agrees is True
    assert result.adjusted_bank_cents == 1_000_000


def test_unpresented_withdrawals_reduce_adjusted_bank():
    result = three_way_reconcile(
        cleared_bank_cents=1_100_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=100_000,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=1_000_000,
    )
    assert result.agrees is True
    assert result.adjusted_bank_cents == 1_000_000


def test_combined_dit_and_unpresented():
    result = three_way_reconcile(
        cleared_bank_cents=950_000,
        deposits_in_transit_cents=100_000,
        unpresented_withdrawals_cents=50_000,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=1_000_000,
    )
    assert result.agrees is True


# ── assert_agrees error message ───────────────────────────────────────────────

def test_assert_agrees_raises_with_breakdown():
    result = three_way_reconcile(
        cleared_bank_cents=900_000,
        deposits_in_transit_cents=0,
        unpresented_withdrawals_cents=0,
        ledger_balance_cents=1_000_000,
        subledger_sum_cents=1_000_000,
    )
    with pytest.raises(ValueError) as exc:
        result.assert_agrees()
    msg = str(exc.value)
    assert "900" in msg  # adjusted_bank appears
    assert "1,000,000" in msg  # ledger appears
    assert "reconcil" in msg.lower()


def test_assert_agrees_mentions_reconciliation_items():
    """The error hint must direct the operator to reconciliation_items collection."""
    result = three_way_reconcile(100, 0, 0, 200, 200)
    with pytest.raises(ValueError) as exc:
        result.assert_agrees()
    assert "reconciliation" in str(exc.value).lower()


# ── Result is immutable (frozen dataclass) ────────────────────────────────────

def test_result_is_immutable():
    result = three_way_reconcile(1000, 0, 0, 1000, 1000)
    with pytest.raises(Exception):
        result.agrees = False  # type: ignore
