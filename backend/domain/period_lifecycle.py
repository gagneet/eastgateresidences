"""
backend/domain/period_lifecycle.py — Financial period lifecycle state machine.

# @featuretrace:financial_integration_v2 — period state machine; guards journal posting.
# Layer: domain
# Data flow: trust_accounting router → can_post_to_period() → financial_periods (building-scoped).
# Related: backend/domain/journals.py
#           backend/routers/trust_accounting.py

State diagram:
  OPEN → RECONCILING → CLOSED → AUDITED → LOCKED
              ↑
          (revert to OPEN allowed from RECONCILING only)

Posting is only permitted in OPEN periods. Corrections to CLOSED or later periods
must be posted as offsetting reversals in the current OPEN period.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Set

from fastapi import HTTPException, status
from pydantic import BaseModel, Field


class PeriodState(str, Enum):
    OPEN = "open"
    RECONCILING = "reconciling"
    CLOSED = "closed"
    AUDITED = "audited"
    LOCKED = "locked"


# Transitions: key = current state, value = set of permitted next states.
_ALLOWED_TRANSITIONS: dict[PeriodState, Set[PeriodState]] = {
    PeriodState.OPEN: {PeriodState.RECONCILING},
    PeriodState.RECONCILING: {PeriodState.CLOSED, PeriodState.OPEN},
    PeriodState.CLOSED: {PeriodState.AUDITED},
    PeriodState.AUDITED: {PeriodState.LOCKED},
    PeriodState.LOCKED: set(),
}

# Only OPEN periods accept new postings.
_POSTING_ALLOWED: Set[PeriodState] = {PeriodState.OPEN}


class FinancialPeriod(BaseModel):
    """Document shape stored in the financial_periods collection.

    Not yet used by routers — reserved for future period-management endpoints
    (open/close/reconcile/lock). The router currently reads raw dicts from
    financial_periods; migrate to this model when those endpoints are built.
    """

    building_id: str = Field(..., min_length=1)
    period_id: str = Field(..., description="Human-readable label, e.g. '2026-Q1' or '2026-04'")
    fund_type: str = Field(..., description="admin_fund | capital_works_fund | special_levy")
    state: PeriodState = PeriodState.OPEN
    opened_at: str = Field(..., description="ISO 8601 datetime")
    opened_by: str = Field(..., description="user_id")
    reconciling_at: Optional[str] = None
    reconciling_by: Optional[str] = None
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None
    audited_at: Optional[str] = None
    audited_by: Optional[str] = None
    locked_at: Optional[str] = None
    locked_by: Optional[str] = None


def can_post_to_period(state: PeriodState) -> None:
    """Raise HTTP 409 if the period is not OPEN.

    Called by the journal posting route before any DB write.
    Passing a non-OPEN state means the caller tried to write into a read-only
    period; the correction path is a reversal in the current OPEN period.
    """
    if state not in _POSTING_ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot post to a period in '{state.value}' state. "
                "Post an offsetting reversal in the current OPEN period, "
                "then repost correctly — the original entry is preserved for audit."
            ),
        )


def validate_transition(from_state: PeriodState, to_state: PeriodState) -> None:
    """Raise ValueError if the requested state transition is not in the allowed set.

    Callers (e.g. the period-close route) should catch ValueError and surface
    it as an HTTP 409 or 400 as appropriate.
    """
    allowed = _ALLOWED_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise ValueError(
            f"Invalid period state transition: {from_state.value!r} → {to_state.value!r}. "
            f"Allowed next states from '{from_state.value}': "
            f"{sorted(s.value for s in allowed) or 'none (terminal state)'}."
        )
