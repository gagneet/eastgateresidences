"""
backend/domain/invoice_lifecycle.py — AP invoice lifecycle state machine.

# @featuretrace:ap_automation — invoice state transitions with jurisdictional gates.
# Layer: domain
# Data flow: supplier upload → draft → submitted → validated → approved →
#            scheduled → paid → reconciled (happy path).
# Related: backend/routers/ap_supplier_upload.py
#           backend/routers/ap_approval.py
#           backend/integrations/abn_validator.py
#           backend/domain/jurisdictional_rules.py
# Scope: (building-scoped)

States: draft, submitted, validated, approved, scheduled, paid, reconciled,
        rejected, on_hold, disputed, failed, pending_resolution.

Jurisdictional approval gates are delegated to JurisdictionalRuleEngine — never
add inline `if jurisdiction ==` branches here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from bson import ObjectId
from db_postgres.repos import config_repo
from fastapi import HTTPException, status

from domain.jurisdictional_rules import rule_engine

logger = logging.getLogger(__name__)


class InvoiceState(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PAID = "paid"
    RECONCILED = "reconciled"
    REJECTED = "rejected"
    ON_HOLD = "on_hold"
    DISPUTED = "disputed"
    FAILED = "failed"
    PENDING_RESOLUTION = "pending_resolution"


# Valid forward transitions (exception paths can go from any state).
_ALLOWED_TRANSITIONS: dict[InvoiceState, set[InvoiceState]] = {
    InvoiceState.DRAFT: {InvoiceState.SUBMITTED, InvoiceState.REJECTED},
    InvoiceState.SUBMITTED: {InvoiceState.VALIDATED, InvoiceState.REJECTED, InvoiceState.ON_HOLD},
    InvoiceState.VALIDATED: {InvoiceState.APPROVED, InvoiceState.REJECTED, InvoiceState.ON_HOLD,
                             InvoiceState.PENDING_RESOLUTION},
    InvoiceState.APPROVED: {InvoiceState.SCHEDULED, InvoiceState.ON_HOLD, InvoiceState.DISPUTED},
    InvoiceState.SCHEDULED: {InvoiceState.PAID, InvoiceState.FAILED},
    InvoiceState.PAID: {InvoiceState.RECONCILED, InvoiceState.DISPUTED},
    InvoiceState.RECONCILED: set(),  # terminal
    InvoiceState.REJECTED: set(),  # terminal
    InvoiceState.ON_HOLD: {InvoiceState.SUBMITTED, InvoiceState.VALIDATED, InvoiceState.APPROVED},
    InvoiceState.DISPUTED: {InvoiceState.ON_HOLD, InvoiceState.RECONCILED},
    InvoiceState.FAILED: {InvoiceState.SCHEDULED, InvoiceState.REJECTED},
    InvoiceState.PENDING_RESOLUTION: {InvoiceState.APPROVED, InvoiceState.REJECTED},
}

# Mandatory fields required for draft → submitted
_MANDATORY_FIELDS = {"vendor_abn", "vendor_name", "invoice_number", "issue_date",
                     "due_date", "total_cents", "description"}

# Roles that may approve invoices
_APPROVE_ROLES = {"super_admin", "ec_member", "strata_manager"}


# Jurisdictional constants removed — rule_engine is the single source of truth.


class InvoiceTransitionError(Exception):
    """Raised when a transition is rejected by the state machine."""


async def transition_invoice(
        invoice_id: str,
        to_state: InvoiceState,
        building_id: str,
        authorised_by: str,
        *,
        db,
        notes: Optional[str] = None,
        lot_count: int = 1,
        budget_line_current_cents: int = 0,
        budget_line_cap_cents: int = 0,
) -> dict:
    """
    Transition an invoice to a new state.

    Enforces:
      - Valid transition per _ALLOWED_TRANSITIONS
      - draft → submitted: mandatory fields present
      - validated → approved: jurisdictional rules (NSW 10% cap, QLD committee cap)
      - approved → scheduled: outbound_payment_initiation_enabled toggle

    Returns the updated invoice document.
    Raises InvoiceTransitionError (mapped to HTTP 409/422 by routers).
    """
    oid = _parse_oid(invoice_id)
    doc = await db.invoice_documents.find_one({"_id": oid})
    if not doc:
        raise InvoiceTransitionError(f"Invoice {invoice_id} not found")

    current = InvoiceState(doc.get("state", InvoiceState.DRAFT))
    _assert_transition_allowed(current, to_state)

    # State-specific guards
    if current == InvoiceState.DRAFT and to_state == InvoiceState.SUBMITTED:
        _check_mandatory_fields(doc)

    if current == InvoiceState.SUBMITTED and to_state == InvoiceState.VALIDATED:
        if not doc.get("abn_valid"):
            # ABN invalid does not hard-block; flag for reviewer attention
            logger.warning("Invoice %s has invalid ABN — proceeding to validated with flag", invoice_id)

    if current == InvoiceState.VALIDATED and to_state == InvoiceState.APPROVED:
        await _check_jurisdictional_approval_gate(
            doc, building_id, lot_count,
            budget_line_current_cents, budget_line_cap_cents,
            db=db,
        )

    if current == InvoiceState.APPROVED and to_state == InvoiceState.SCHEDULED:
        enabled = await _feature_enabled(db, building_id, "outbound_payment_initiation_enabled")
        if not enabled:
            raise InvoiceTransitionError(
                "outbound_payment_initiation_enabled is off for this building — "
                "payment scheduling is disabled."
            )

    now = datetime.now(timezone.utc).isoformat()
    transition_record = {
        "from_state": current.value,
        "to_state": to_state.value,
        "transitioned_at": now,
        "transitioned_by": authorised_by,
        "notes": notes,
    }

    await db.invoice_documents.update_one(
        {"_id": oid},
        {
            "$set": {"state": to_state.value, "updated_at": now},
            "$push": {"transitions": transition_record},
        },
    )

    logger.info(
        "Invoice %s: %s → %s by %s (building=%s)",
        invoice_id, current.value, to_state.value, authorised_by, building_id,
    )

    updated = await db.invoice_documents.find_one({"_id": oid})
    return updated


# ── Guards ────────────────────────────────────────────────────────────────────

def _assert_transition_allowed(current: InvoiceState, to: InvoiceState) -> None:
    """Generated function header.

    Function: _assert_transition_allowed
    Path: backend/domain/invoice_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if to not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise InvoiceTransitionError(
            f"Transition {current.value} → {to.value} is not permitted."
        )


def _check_mandatory_fields(doc: dict) -> None:
    """Generated function header.

    Function: _check_mandatory_fields
    Path: backend/domain/invoice_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    missing = [f for f in _MANDATORY_FIELDS if not doc.get(f)]
    if missing:
        raise InvoiceTransitionError(
            f"Cannot submit invoice — missing mandatory fields: {', '.join(sorted(missing))}"
        )


async def _check_jurisdictional_approval_gate(
        doc: dict,
        building_id: str,
        lot_count: int,
        budget_line_current_cents: int,
        budget_line_cap_cents: int,
        *,
        db,
) -> None:
    """Jurisdictional approval gate — delegated entirely to JurisdictionalRuleEngine."""
    building = await db.buildings.find_one(
        {"$or": [{"id": building_id}, {"building_id": building_id}]}
    )
    jurisdiction = ((building or {}).get("jurisdiction") or "ACT").upper()
    total = doc.get("total_cents", 0)

    overbudget_pct = rule_engine.overbudget_threshold_pct(jurisdiction)
    if overbudget_pct is not None and budget_line_cap_cents > 0:
        # Integer comparison avoids float literals/annotations (no-floats lint rule).
        # overage_pct_display is only used in the error message string; the comparison is pure int.
        overage_numerator = (budget_line_current_cents + total - budget_line_cap_cents) * 100
        overage_pct_display = overage_numerator / budget_line_cap_cents
        if overage_numerator > overbudget_pct * budget_line_cap_cents:
            legislation = (
                rule_engine.get_effective_rules(jurisdiction)
                .get("overbudget_threshold_pct", {})
                .get("legislation", "")
            )
            raise InvoiceTransitionError(
                f"{legislation}: this approval would push the budget line "
                f"{overage_pct_display:.1f}% over cap (>{overbudget_pct}%). "
                "A general meeting resolution is required."
            )

    cap_per_lot = rule_engine.committee_spending_cap_per_lot_cents(jurisdiction)
    if cap_per_lot is not None:
        committee_cap = cap_per_lot * lot_count
        if total > committee_cap:
            legislation = (
                rule_engine.get_effective_rules(jurisdiction)
                .get("committee_spending_cap_formula", {})
                .get("legislation", "")
            )
            raise InvoiceTransitionError(
                f"{legislation}: invoice total ${total / 100:.2f} exceeds the "
                f"committee spending cap of ${committee_cap / 100:.2f} "
                f"(${cap_per_lot / 100:.0f} × {lot_count} lots). "
                "A committee resolution is required."
            )


async def _feature_enabled(db, building_id: str, key: str) -> bool:
    """Check whether a feature toggle is on for this building."""
    try:
        return bool(await config_repo.resolve_feature_toggle(building_id, key, default=False))
    except Exception:
        return False


def _parse_oid(invoice_id: str) -> ObjectId:
    """Generated function header.

    Function: _parse_oid
    Path: backend/domain/invoice_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return ObjectId(invoice_id)
    except Exception:
        raise InvoiceTransitionError(f"Invalid invoice_id: {invoice_id!r}")
