"""Joint-owner review router.

# @featuretrace:joint_owner — Per-lot joint-name review and apply endpoints.
# Layer: router
# Data flow: tools/parse_joint_owners.py (CLI dry-run/seed) →
#            POST /joint-owner-review/seed →
#            core.joint_owner_review →
#            PATCH /joint-owner-review/{review_id} (manager decision) →
#            POST /joint-owner-review/apply →
#            core.ownership_periods (append + terminate) + core.outbox (ADR-025 event) (building-scoped).
# Related: backend/services/joint_owner_service.py; backend/db_postgres/repos/ownership_repo.py
# Scope: (building-scoped)

Endpoints
---------
GET    /joint-owner-review              — list reviews for current building
POST   /joint-owner-review/seed        — parse candidates and insert pending rows
PATCH  /joint-owner-review/{review_id} — confirm / edit / reject a single review
POST   /joint-owner-review/apply       — apply all confirmed reviews (dry_run flag)
GET    /joint-owner-review/parse-preview — parse a name string without persisting
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from models.user import UserRole
from utils.auth import get_current_building, get_current_user, effective_role
from services import joint_owner_service

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ParsedOwnerIn(BaseModel):
    name: str = Field(..., min_length=1)
    ownership_share: str = Field(..., description="Decimal fraction, e.g. '0.5'")


class UpdateReviewRequest(BaseModel):
    status: str = Field(..., description="confirmed | edited | rejected")
    manager_owners: Optional[list[ParsedOwnerIn]] = Field(
        None,
        description="Required when status='edited'. Overrides parser output.",
    )

    def validate_status(self) -> None:
        """Generated function header.

        Function: UpdateReviewRequest.validate_status
        Path: backend/routers/joint_owner_review.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.status not in {"confirmed", "edited", "rejected"}:
            raise ValueError(f"status must be confirmed, edited, or rejected — got {self.status!r}")
        if self.status == "edited" and not self.manager_owners:
            raise ValueError("manager_owners required when status is 'edited'")


class ApplyRequest(BaseModel):
    dry_run: bool = Field(False, description="If true, log what would happen without writing.")


# ---------------------------------------------------------------------------
# Role guard helper
# ---------------------------------------------------------------------------

_ALLOWED_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.STRATA_ADMIN,
}


def _require_manager_role(current_user: dict) -> None:
    """Generated function header.

    Function: _require_manager_role
    Path: backend/routers/joint_owner_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = effective_role(current_user)
    if role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role for joint-owner review")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/joint-owner-review")
async def list_joint_owner_reviews(
    status: Optional[str] = Query(None, description="Filter by status: pending|confirmed|edited|rejected|applied"),
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List joint-owner review records for the current building."""
    _require_manager_role(current_user)
    rows = await joint_owner_service.list_reviews(building_id, status=status)
    return {"reviews": rows, "total": len(rows)}


@router.post("/joint-owner-review/seed")
async def seed_joint_owner_reviews(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Parse all joint-owner candidates in core.parties and insert pending review rows.

    Idempotent — safe to call multiple times. Existing rows are not overwritten.
    """
    _require_manager_role(current_user)
    inserted = await joint_owner_service.seed_review_records(building_id)
    return {
        "seeded": len(inserted),
        "rows": inserted,
        "message": (
            f"Seeded {len(inserted)} pending review row(s). "
            "Run GET /joint-owner-review to review."
        ),
    }


@router.patch("/joint-owner-review/{review_id}")
async def update_joint_owner_review(
    review_id: str,
    body: UpdateReviewRequest,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Confirm, edit, or reject a single review row.

    - **confirmed**: accept the parser output as-is.
    - **edited**: provide `manager_owners` to override the parsed result.
    - **rejected**: mark the lot as not requiring a split (no apply will occur).
    """
    _require_manager_role(current_user)
    try:
        body.validate_status()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    manager_owners_data: list[dict] | None = None
    if body.manager_owners:
        manager_owners_data = [
            {"name": o.name, "ownership_share": o.ownership_share}
            for o in body.manager_owners
        ]

    try:
        updated = await joint_owner_service.update_review(
            building_id=building_id,
            review_id=review_id,
            status=body.status,
            manager_owners=manager_owners_data,
            decided_by_user_id=str(current_user.get("id") or current_user.get("_id") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"updated": updated}


@router.post("/joint-owner-review/apply")
async def apply_joint_owner_reviews(
    body: ApplyRequest,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Apply all confirmed/edited reviews.

    Writes bitemporal ownership_period rows and emits ADR-025 restatement
    events.  Set `dry_run=true` to preview without persisting.

    Discipline: only confirmed or edited reviews are processed; rejected and
    pending reviews are skipped.
    """
    _require_manager_role(current_user)
    actor_id = str(current_user.get("id") or current_user.get("_id") or "")

    results = await joint_owner_service.apply_confirmed_reviews(
        building_id=building_id,
        actor_user_id=actor_id,
        dry_run=body.dry_run,
    )

    applied = [r for r in results if not r.get("error") and not r.get("dry_run")]
    errors = [r for r in results if r.get("error")]

    return {
        "dry_run": body.dry_run,
        "processed": len(results),
        "applied": len(applied),
        "errors": len(errors),
        "results": results,
    }


@router.get("/joint-owner-review/parse-preview")
async def parse_name_preview(
    name: str = Query(..., description="Combined name string to parse"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Parse a single name string and return the result without persisting anything.

    Useful for testing the parser before seeding.
    """
    _require_manager_role(current_user)
    result = joint_owner_service.parse_joint_name(name)
    return result.to_dict()
