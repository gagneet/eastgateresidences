"""
GAP-GOV-007: Committee Conflict-of-Interest Register.

Endpoints:
  POST   /conflict-of-interest             — declare a conflict of interest
  GET    /conflict-of-interest             — list declarations (building-scoped, paginated)
  GET    /conflict-of-interest/{id}        — get single declaration
  PUT    /conflict-of-interest/{id}        — update resolution / add notes
  DELETE /conflict-of-interest/{id}        — retract a declaration (manager only)

Conflict declarations must be recorded before the relevant agenda item is discussed.
They are immutable by the declarant — only managers/chairman can update the resolution field.

Ties to:
  - GAP-JUR-NSW-013 (building manager disclosure)
  - decisions.py (decision records may reference a conflict_of_interest id)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log
from utils.permissions import require_feature

logger = logging.getLogger(__name__)
# GAP-FT-004: Gate the conflict-of-interest router behind the conflict_of_interest toggle.
router = APIRouter(
    prefix="/conflict-of-interest",
    tags=["Governance"],
    dependencies=[Depends(require_feature("conflict_of_interest"))],
)


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/conflict_of_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/conflict_of_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_ec(user: dict) -> dict:
    """EC members and managers can view and create declarations."""
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "EC or manager access required.")
    return user


def _require_manager(user: dict) -> dict:
    """Only managers/chairman can update resolution or retract declarations."""
    allowed = {"super_admin", "strata_manager", "strata_admin", "admin_staff"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "Strata manager access required.")
    return user


# ── Models ────────────────────────────────────────────────────────────────────

CONFLICT_TYPES = ["financial", "personal", "professional", "other"]
RESOLUTION_TYPES = ["abstained", "recused", "disclosed_and_voted", "chair_ruled_no_conflict", "pending"]


class ConflictCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    conflict_type: str = Field(..., description=f"One of: {CONFLICT_TYPES}")
    description: str = Field(..., min_length=10, max_length=2000,
                             description="Nature of the conflict as declared by the member")
    related_matter: Optional[str] = Field(
        None, description="Agenda item, motion title, or decision number this relates to"
    )
    resolution: str = Field(
        default="pending",
        description=f"How the conflict was handled. One of: {RESOLUTION_TYPES}",
    )
    meeting_date: Optional[str] = Field(None, description="ISO-8601 date of the meeting")
    notes: Optional[str] = None


class ConflictUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: Optional[str] = None
    notes: Optional[str] = None
    related_matter: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def declare_conflict(
        payload: ConflictCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Record a conflict-of-interest declaration for the authenticated EC member."""
    _require_ec(current_user)
    db = _get_db()

    if payload.conflict_type not in CONFLICT_TYPES:
        raise HTTPException(400, f"Invalid conflict_type. Valid: {CONFLICT_TYPES}")
    if payload.resolution not in RESOLUTION_TYPES:
        raise HTTPException(400, f"Invalid resolution. Valid: {RESOLUTION_TYPES}")

    doc = {
        "building_id": building_id,
        "member_id": current_user.get("id", "unknown"),
        "member_name": current_user.get("full_name", ""),
        "conflict_type": payload.conflict_type,
        "description": payload.description,
        "related_matter": payload.related_matter,
        "resolution": payload.resolution,
        "meeting_date": payload.meeting_date,
        "notes": payload.notes,
        "declared_at": _now_iso(),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "retracted": False,
    }

    result = await db.conflict_of_interest.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="conflict_declared",
        resource_type="conflict_of_interest",
        resource_id=doc["id"],
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={"conflict_type": payload.conflict_type, "related_matter": payload.related_matter},
        building_id=building_id,
    ))

    return doc


@router.get("")
async def list_conflicts(
        member_id: Optional[str] = Query(None, description="Filter to a specific member"),
        conflict_type: Optional[str] = Query(None),
        include_retracted: bool = Query(False),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List conflict-of-interest declarations for this building."""
    _require_ec(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if not include_retracted:
        query["retracted"] = {"$ne": True}
    if member_id:
        query["member_id"] = member_id
    if conflict_type:
        query["conflict_type"] = conflict_type

    total = await db.conflict_of_interest.count_documents(query)
    skip = (page - 1) * page_size
    cursor = db.conflict_of_interest.find(query).sort("declared_at", -1).skip(skip).limit(page_size)

    docs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        docs.append(doc)

    return {"data": docs, "total": total, "page": page, "page_size": page_size}


@router.get("/{declaration_id}")
async def get_conflict(
        declaration_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single conflict-of-interest declaration."""
    _require_ec(current_user)
    db = _get_db()

    try:
        oid = ObjectId(declaration_id)
    except Exception:
        raise HTTPException(400, "Invalid declaration ID.")

    doc = await db.conflict_of_interest.find_one({"_id": oid, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Declaration not found.")

    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/{declaration_id}")
async def update_conflict(
        declaration_id: str,
        payload: ConflictUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update the resolution or notes on a declaration (manager/chairman only)."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(declaration_id)
    except Exception:
        raise HTTPException(400, "Invalid declaration ID.")

    if payload.resolution and payload.resolution not in RESOLUTION_TYPES:
        raise HTTPException(400, f"Invalid resolution. Valid: {RESOLUTION_TYPES}")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = current_user.get("id", "unknown")

    result = await db.conflict_of_interest.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Declaration not found.")

    asyncio.create_task(create_audit_log(
        action="conflict_updated",
        resource_type="conflict_of_interest",
        resource_id=declaration_id,
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    ))

    return {"message": "Declaration updated.", "declaration_id": declaration_id}


@router.delete("/{declaration_id}")
async def retract_conflict(
        declaration_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Retract a declaration (soft-delete — sets retracted=True). Manager only."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(declaration_id)
    except Exception:
        raise HTTPException(400, "Invalid declaration ID.")

    result = await db.conflict_of_interest.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": {"retracted": True, "retracted_at": _now_iso(), "retracted_by": current_user.get("id", "unknown")}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Declaration not found.")

    asyncio.create_task(create_audit_log(
        action="conflict_retracted",
        resource_type="conflict_of_interest",
        resource_id=declaration_id,
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={},
        building_id=building_id,
    ))

    return {"message": "Declaration retracted.", "declaration_id": declaration_id}
