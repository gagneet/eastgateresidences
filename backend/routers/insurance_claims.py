"""
Insurance Claims Router

Endpoints:
  POST   /insurance-claims                          — create claim
  GET    /insurance-claims                          — list claims (building-scoped)
  GET    /insurance-claims/{claim_id}               — get single claim
  PUT    /insurance-claims/{claim_id}               — update claim details
  PUT    /insurance-claims/{claim_id}/status        — update claim status
  POST   /insurance-claims/{claim_id}/timeline      — add timeline milestone
  GET    /insurance-claims/{claim_id}/timeline      — get timeline
"""

import uuid
from datetime import datetime, timezone

import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional

from models.insurance_claim import (
    InsuranceClaimCreate,
    InsuranceClaimResponse,
    InsuranceClaimStatus,
    InsuranceClaimTimelineEntry,
    InsuranceClaimUpdate,
)
from utils.auth import get_approved_user, get_current_building, effective_role
from utils.route_guards import require_manager_surface
from utils.helpers import create_audit_log
from utils.permissions import get_user_permissions

# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/insurance-claims", tags=["Insurance Claims"],
    dependencies=[Depends(require_manager_surface("claims"))],
)
logger = logging.getLogger(__name__)

_VALID_STATUSES = [
    InsuranceClaimStatus.REPORTED,
    InsuranceClaimStatus.UNDER_REVIEW,
    InsuranceClaimStatus.LODGED,
    InsuranceClaimStatus.IN_PROGRESS,
    InsuranceClaimStatus.FINALIZED,
    InsuranceClaimStatus.REJECTED,
]


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/insurance_claims.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/insurance_claims.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _require_finance(user: dict):
    """Generated function header.

    Function: _require_finance
    Path: backend/routers/insurance_claims.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not get_user_permissions(user).can_manage_finances:
        raise HTTPException(403, "Finance access required.")


def _require_view(user: dict):
    """Generated function header.

    Function: _require_view
    Path: backend/routers/insurance_claims.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not get_user_permissions(user).can_view_finances:
        raise HTTPException(403, "Finance view access required.")


@router.post("", response_model=InsuranceClaimResponse, status_code=201)
async def create_insurance_claim(
        data: InsuranceClaimCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Create a new insurance claim. Scoped to building."""
    _require_finance(current_user)
    db = _get_db()

    claim_id = str(uuid.uuid4())
    now = _now()
    claim_doc = {
        "id": claim_id,
        **data.model_dump(),
        "building_id": building_id,  # auth overrides any payload value
        "status": InsuranceClaimStatus.REPORTED,
        "timeline": [],
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    }

    await db.insurance_claims.insert_one(claim_doc)
    await create_audit_log(
        "created", "insurance_claim", claim_id, current_user["id"],
        current_user.get("full_name", "Unknown"),
        {"claim_number": data.claim_number, "insurer": data.insurer},
        building_id,
    )
    return InsuranceClaimResponse(**claim_doc)


@router.get("", response_model=List[InsuranceClaimResponse])
async def get_insurance_claims(
        status: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """List all insurance claims for the building."""
    _require_view(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if status:
        query["status"] = status

    claims = await db.insurance_claims.find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return [InsuranceClaimResponse(**c) for c in claims]


@router.get("/{claim_id}", response_model=InsuranceClaimResponse)
async def get_insurance_claim(
        claim_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Get a specific insurance claim."""
    _require_view(current_user)
    db = _get_db()

    claim = await db.insurance_claims.find_one({"id": claim_id, "building_id": building_id}, {"_id": 0})
    if not claim:
        raise HTTPException(404, "Claim not found.")
    return InsuranceClaimResponse(**claim)


@router.put("/{claim_id}", response_model=InsuranceClaimResponse)
async def update_insurance_claim(
        claim_id: str,
        payload: InsuranceClaimUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Update claim details (settlement amount, assessor, reserve, notes)."""
    _require_finance(current_user)
    db = _get_db()

    upd = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not upd:
        raise HTTPException(400, "No fields to update.")
    upd["updated_at"] = _now()

    res = await db.insurance_claims.update_one(
        {"id": claim_id, "building_id": building_id}, {"$set": upd}
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Claim not found.")

    await create_audit_log(
        "updated", "insurance_claim", claim_id, current_user["id"],
        current_user.get("full_name", "Unknown"), {"fields": list(upd.keys())}, building_id,
    )
    claim = await db.insurance_claims.find_one({"id": claim_id, "building_id": building_id}, {"_id": 0})
    return InsuranceClaimResponse(**claim)


@router.put("/{claim_id}/status")
async def update_claim_status(
        claim_id: str,
        status: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Update the status of an insurance claim."""
    _require_finance(current_user)
    db = _get_db()

    if status not in _VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(_VALID_STATUSES)}")

    res = await db.insurance_claims.update_one(
        {"id": claim_id, "building_id": building_id},
        {"$set": {"status": status, "updated_at": _now()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Claim not found.")

    await create_audit_log(
        "status_updated", "insurance_claim", claim_id, current_user["id"],
        current_user.get("full_name", "Unknown"), {"new_status": status}, building_id,
    )
    return {"message": "Claim status updated.", "status": status}


@router.post("/{claim_id}/timeline", status_code=201)
async def add_timeline_entry(
        claim_id: str,
        entry: InsuranceClaimTimelineEntry,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Add a milestone to the claim timeline (e.g. assessor appointed, settlement agreed)."""
    _require_finance(current_user)
    db = _get_db()

    claim = await db.insurance_claims.find_one({"id": claim_id, "building_id": building_id}, {"_id": 0})
    if not claim:
        raise HTTPException(404, "Claim not found.")

    milestone_doc = {**entry.model_dump(), "recorded_by": current_user["id"]}
    res = await db.insurance_claims.update_one(
        {"id": claim_id, "building_id": building_id},
        {"$push": {"timeline": milestone_doc}, "$set": {"updated_at": _now()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Claim not found.")

    return {"message": "Timeline entry added.", "milestone": entry.milestone}


@router.get("/{claim_id}/timeline")
async def get_claim_timeline(
        claim_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Get the full timeline for a claim."""
    _require_view(current_user)
    db = _get_db()

    claim = await db.insurance_claims.find_one({"id": claim_id, "building_id": building_id}, {"_id": 0})
    if not claim:
        raise HTTPException(404, "Claim not found.")

    return {"claim_id": claim_id, "timeline": claim.get("timeline", [])}
