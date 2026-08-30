"""
tests/backend/domain/test_period_lifecycle.py — Unit tests for domain/period_lifecycle.py.

Covers:
  - All valid state transitions pass validate_transition().
  - All invalid transitions raise ValueError.
  - LOCKED is terminal — no transitions out.
  - can_post_to_period() allows OPEN, raises HTTP 409 for all other states.
  - FinancialPeriod model construction and building_id isolation.
"""
import pytest
from fastapi import HTTPException

from domain.period_lifecycle import (
    FinancialPeriod,
    PeriodState,
    can_post_to_period,
    validate_transition,
)

BUILDING_A = "16244"
BUILDING_B = "13195"

_NOW = "2026-04-23T02:00:00+00:00"


# ── FinancialPeriod model ─────────────────────────────────────────────────────

def test_financial_period_defaults_to_open():
    p = FinancialPeriod(
        building_id=BUILDING_A,
        period_id="2026-Q1",
        fund_type="admin_fund",
        opened_at=_NOW,
        opened_by="mgr-1",
    )
    assert p.state == PeriodState.OPEN


def test_financial_period_building_isolation():
    p_a = FinancialPeriod(
        building_id=BUILDING_A, period_id="2026-Q1", fund_type="admin_fund",
        opened_at=_NOW, opened_by="mgr-1",
    )
    p_b = FinancialPeriod(
        building_id=BUILDING_B, period_id="2026-Q1", fund_type="admin_fund",
        opened_at=_NOW, opened_by="mgr-2",
    )
    assert p_a.building_id != p_b.building_id


# ── Valid transitions ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("from_state, to_state", [
    (PeriodState.OPEN, PeriodState.RECONCILING),
    (PeriodState.RECONCILING, PeriodState.CLOSED),
    (PeriodState.RECONCILING, PeriodState.OPEN),  # revert allowed
    (PeriodState.CLOSED, PeriodState.AUDITED),
    (PeriodState.AUDITED, PeriodState.LOCKED),
])
def test_valid_transition_does_not_raise(from_state, to_state):
    validate_transition(from_state, to_state)  # must not raise


# ── Invalid transitions ───────────────────────────────────────────────────────

@pytest.mark.parametrize("from_state, to_state", [
    (PeriodState.OPEN, PeriodState.CLOSED),  # skip RECONCILING
    (PeriodState.OPEN, PeriodState.LOCKED),  # skip multiple
    (PeriodState.CLOSED, PeriodState.OPEN),  # no reverting from CLOSED
    (PeriodState.LOCKED, PeriodState.OPEN),  # terminal — no exit
    (PeriodState.LOCKED, PeriodState.AUDITED),  # terminal — no exit
    (PeriodState.AUDITED, PeriodState.OPEN),  # backwards jump
    (PeriodState.RECONCILING, PeriodState.LOCKED),  # skip CLOSED + AUDITED
])
def test_invalid_transition_raises_value_error(from_state, to_state):
    with pytest.raises(ValueError) as exc:
        validate_transition(from_state, to_state)
    assert from_state.value in str(exc.value)
    assert to_state.value in str(exc.value)


def test_locked_is_terminal():
    """LOCKED must have no valid outgoing transitions."""
    for target in PeriodState:
        if target == PeriodState.LOCKED:
            continue
        with pytest.raises(ValueError):
            validate_transition(PeriodState.LOCKED, target)


# ── Posting guard ─────────────────────────────────────────────────────────────

def test_can_post_to_open_period():
    """OPEN is the only state that should accept new journal postings."""
    can_post_to_period(PeriodState.OPEN)  # must not raise


@pytest.mark.parametrize("state", [
    PeriodState.RECONCILING,
    PeriodState.CLOSED,
    PeriodState.AUDITED,
    PeriodState.LOCKED,
])
def test_posting_blocked_for_non_open_period(state):
    """All non-OPEN states must raise HTTP 409."""
    with pytest.raises(HTTPException) as exc:
        can_post_to_period(state)
    assert exc.value.status_code == 409
    assert state.value in exc.value.detail


def test_posting_to_closed_period_carries_reversal_hint():
    """The 409 detail must mention 'reversal' so the caller knows the correction path."""
    with pytest.raises(HTTPException) as exc:
        can_post_to_period(PeriodState.CLOSED)
    assert "reversal" in exc.value.detail.lower()


def test_posting_to_locked_period_raises_409():
    """A LOCKED period is tamper-proof — posting must be unconditionally refused."""
    with pytest.raises(HTTPException) as exc:
        can_post_to_period(PeriodState.LOCKED)
    assert exc.value.status_code == 409
