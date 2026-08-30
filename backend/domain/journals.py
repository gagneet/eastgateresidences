"""
backend/domain/journals.py — Journal domain model with write-time invariant validation.

# @featuretrace:financial_integration_v2 — domain Journal model enforcing double-entry balance.
# Layer: domain
# Data flow: trust_accounting router → Journal.model_validate() → trust_ledger_entries (building-scoped).
# Related: backend/routers/trust_accounting.py
#           backend/domain/period_lifecycle.py (posting guard)
#           backend/domain/__init__.py (Cents type)

Invariants enforced at construction time:
  1. At least 2 lines — enforced by model_validator (explicit error message).
  2. Every line amount_cents > 0 — enforced at field level by JournalLine.amount_cents Field(gt=0).
     Pydantic fires field validators before model_validator, so the model_validator never sees
     a zero or negative amount; the check there would be dead code.
  3. sum(debit lines) == sum(credit lines) — exact integer equality; no float tolerance.
     Enforced by model_validator.

These checks run BEFORE any MongoDB write. A malformed Journal cannot be persisted.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class JournalSide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class JournalLine(BaseModel):
    account_id: str = Field(..., min_length=1)
    side: JournalSide
    # Positive integers only — the side field encodes direction, not the sign.
    amount_cents: int = Field(..., gt=0, le=10 ** 15)


class Journal(BaseModel):
    """Balanced double-entry journal — the atomic unit of trust ledger mutation.

    Construction raises ValueError on any invariant violation, so callers never
    need to re-validate after the object exists.
    """

    building_id: str = Field(..., min_length=1)
    # None when posting outside a managed period (legacy / unguarded path).
    period_id: Optional[str] = None
    posting_date: str = Field(..., description="ISO 8601 date, e.g. 2026-04-23")
    reference: str = Field(..., min_length=1)
    narration: str = Field(..., min_length=1)
    lines: List[JournalLine] = Field(..., min_length=2)

    @model_validator(mode="after")
    def _enforce_invariants(self) -> "Journal":
        # Invariant 1: at least 2 lines (Field min_length=2 also covers this, but provides a
        # clearer error message than Pydantic's generic "List should have at least 2 items").
        """Generated function header.

        Function: Journal._enforce_invariants
        Path: backend/domain/journals.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if len(self.lines) < 2:
            raise ValueError("Journal must have at least 2 lines.")

        # Invariant 2 (amount_cents > 0) is enforced at the JournalLine field level via
        # Field(..., gt=0). Pydantic fires field validators before model_validator(mode="after"),
        # so any zero or negative value raises ValidationError before this point; no check needed.

        # Invariant 3: exact integer balance
        debit_total = sum(
            ln.amount_cents for ln in self.lines if ln.side == JournalSide.DEBIT
        )
        credit_total = sum(
            ln.amount_cents for ln in self.lines if ln.side == JournalSide.CREDIT
        )
        if debit_total != credit_total:
            raise ValueError(
                f"Journal is not balanced: debits={debit_total} cents, "
                f"credits={credit_total} cents (difference={debit_total - credit_total} cents)."
            )

        return self

    @property
    def control_total_cents(self) -> int:
        """Sum of debit-side amounts — the canonical control figure for reconciliation."""
        return sum(ln.amount_cents for ln in self.lines if ln.side == JournalSide.DEBIT)
