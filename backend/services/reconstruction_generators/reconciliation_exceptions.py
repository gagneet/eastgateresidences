# @featuretrace:financial-onboarding — lightweight, non-workflow reconciliation exception
#   records: a documented explanation (and, only when explicitly scoped, a genuine accounting
#   adjustment) for a gap annual_fund_reconciliation.py surfaces. (building-scoped)
# Layer: service
# Data flow: admin-entered (POST .../building/{building_id}/reconciliation-exceptions) ->
#            reconciliation_annual_exceptions (Mongo) -> read back by
#            annual_fund_reconciliation.py's compute_annual_fund_reconciliation() (only
#            effect_scope="annual_control_adjustment" nets into expected_closing_cents).
# Related: backend/services/reconstruction_generators/annual_fund_reconciliation.py
#          backend/routers/financial_onboarding.py
# Toggle: historical_financial_reconstruction
# Collection: reconciliation_annual_exceptions
"""Lightweight annual reconciliation exceptions.

Deliberately NOT a propose/approve governance workflow — created directly by an authorized admin
(`_RECONSTRUCTION_UPLOAD_ROLES`), recorded in the audit log by the caller, immediately effective.
A full governance workflow (dual-control review/approval of exceptions) is explicitly deferred
until multiple buildings repeatedly need one — see
`docs/finances/east-gate-reconciliation-plan01.md` and this session's plan file.

`effect_scope` is the mechanism that prevents a purely explanatory note from silently changing an
accounting result nobody explicitly asked it to change: only `effect_scope="annual_control_adjustment"`
entries net into `annual_fund_reconciliation.py`'s `expected_closing_cents` calculation. The
reason_code -> effect_scope mapping below is a validated DEFAULT suggestion shown to the admin
creating an entry, not an enforced constraint — the creator picks `effect_scope` explicitly per
entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

REASON_CODES = {
    "OPENING_BALANCE_BRIDGE", "ACTUAL_RECEIPTS_UNAVAILABLE", "CATEGORY_DETAIL_INCOMPLETE",
    "CASH_ACCRUAL_DIFFERENCE", "CUTOFF_MISMATCH", "OWNER_ARREARS_OR_CREDITS",
    "OTHER_INCOME_UNALLOCATED",
}

EFFECT_SCOPES = {"informational", "category_detail", "opening_rollforward", "annual_control_adjustment"}

# Suggested default only — surfaced to the UI as a starting point, never enforced server-side.
SUGGESTED_EFFECT_SCOPE_BY_REASON = {
    "OPENING_BALANCE_BRIDGE": "opening_rollforward",
    "ACTUAL_RECEIPTS_UNAVAILABLE": "informational",
    "CATEGORY_DETAIL_INCOMPLETE": "category_detail",
    "CASH_ACCRUAL_DIFFERENCE": "informational",
    "CUTOFF_MISMATCH": "informational",
    "OWNER_ARREARS_OR_CREDITS": "informational",
    "OTHER_INCOME_UNALLOCATED": "informational",
}


class ReconciliationExceptionError(ValueError):
    """Raised only for structurally invalid input (bad reason_code/effect_scope/direction)."""


def _validate(reason_code: str, effect_scope: str, direction: str) -> None:
    if reason_code not in REASON_CODES:
        raise ReconciliationExceptionError(f"Unrecognised reason_code {reason_code!r} — expected one of {sorted(REASON_CODES)}")
    if effect_scope not in EFFECT_SCOPES:
        raise ReconciliationExceptionError(f"Unrecognised effect_scope {effect_scope!r} — expected one of {sorted(EFFECT_SCOPES)}")
    if direction not in ("debit", "credit"):
        raise ReconciliationExceptionError(f"direction must be 'debit' or 'credit', got {direction!r}")


async def create_exception(
    mongo_db, *, building_id: str, year: int, fund_type: str, amount_cents: int,
    direction: Literal["debit", "credit"], reason_code: str, effect_scope: str,
    note: Optional[str], created_by: str, is_test_data: bool = False,
) -> dict[str, Any]:
    _validate(reason_code, effect_scope, direction)
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid4()), "building_id": building_id, "year": year, "fund_type": fund_type,
        "amount_cents": amount_cents, "direction": direction, "reason_code": reason_code,
        "effect_scope": effect_scope, "note": note or "", "created_by": created_by,
        "created_at": now, "is_test_data": is_test_data,
    }
    await mongo_db.reconciliation_annual_exceptions.insert_one(doc)
    return doc


async def list_exceptions(mongo_db, *, building_id: str, financial_year_start: int, financial_year_end: int) -> list[dict[str, Any]]:
    return await mongo_db.reconciliation_annual_exceptions.find(
        {
            "building_id": building_id,
            "year": {"$gte": financial_year_start, "$lte": financial_year_end},
            "is_test_data": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(None)


def net_annual_control_adjustments(exceptions: list[dict[str, Any]], *, year: int, fund_type: str) -> int:
    """Signed cents to add to expected_closing_cents for this (year, fund_type) — only entries
    scoped effect_scope="annual_control_adjustment" contribute; every other scope is display-only
    and must not appear in this sum."""
    total = 0
    for exc in exceptions:
        if exc.get("year") != year or exc.get("fund_type") != fund_type:
            continue
        if exc.get("effect_scope") != "annual_control_adjustment":
            continue
        amount = int(exc.get("amount_cents") or 0)
        total += amount if exc.get("direction") == "credit" else -amount
    return total
