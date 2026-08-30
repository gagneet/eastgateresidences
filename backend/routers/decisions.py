"""
Decision Register Router

Formal owners corporation / executive committee resolutions.

Endpoints:
  POST  /decisions          — create decision record
  GET   /decisions          — list (building-scoped, paginated, filterable)
  GET   /decisions/search   — full-text search across decisions
  GET   /decisions/{id}     — get single decision
  PUT   /decisions/{id}     — update decision

Decision numbers are auto-generated: DEC-YYYY-NNNN using a per-year counter
stored in the `decisions_counter` collection.

Legislation:
  - ACT UTMA 2011 Sch.2 (EC meetings) / Sch.3 (general meetings)
  - ACT UTMA 2011 s.21  — ordinary resolution (simple majority)
  - ACT UTMA 2011 s.22  — special resolution (no more than 25% against)
  - ACT UTMA 2011 s.23  — unopposed resolution
  - ACT UTMA 2011 s.24  — unanimous resolution
  - NSW SSMA 2015 Pt.4  — resolutions at meetings

NOTE: /search route MUST appear before /{id} to avoid route shadowing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import asyncio
from asyncio import gather
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Any, Dict

from models.user import UserRole
from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log
from utils.route_guards import OPERATIONAL_MANAGEMENT, assert_roles

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/decisions", tags=["Decision Register"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/decisions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/decisions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_ec(user: dict) -> dict:
    """Who may RECORD a committee decision.

    admin_staff is deliberately absent. Until 2026-08-28 this set carried "admin",
    which is not a UserRole and never matched; it is not resolved to admin_staff here
    because minuting a decision of the executive committee is a governance act, not
    records administration. The read side is _require_view, which does admit them.
    """
    return assert_roles(
        user,
        OPERATIONAL_MANAGEMENT,
        detail="EC or manager access required.",
    )


def _require_view(user: dict) -> dict:
    """Most roles can view decisions (transparency).

    admin_staff restored: this guard already admits owner and tenant, so excluding the
    managing agent's own back office from a register the residents can read was an
    artefact of the dead "admin" string, not a boundary anyone chose.
    """
    return assert_roles(
        user,
        OPERATIONAL_MANAGEMENT | {UserRole.ADMIN_STAFF, UserRole.OWNER, UserRole.TENANT},
        detail="Access denied.",
    )


# ─── Decision Number Generator ────────────────────────────────────────────────

async def _generate_decision_number(db, year: int) -> str:
    """Auto-generate DEC-YYYY-NNNN using an atomic counter per year."""
    result = await db.decisions_counter.find_one_and_update(
        {"year": year},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,  # pymongo ReturnDocument.AFTER equivalent
    )
    seq = result.get("seq", 1)
    return f"DEC-{year}-{seq:04d}"


# ─── Models ───────────────────────────────────────────────────────────────────

MEETING_TYPES = ["agm", "egm", "ec_meeting", "special"]
RESOLUTION_TYPES = ["ordinary", "special", "unopposed", "unanimous"]
DECISION_RESULTS = ["passed", "failed", "lapsed", "deferred"]


class DecisionCreate(BaseModel):
    """Formal owners corporation or EC resolution record."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    meeting_type: str = Field(..., description=f"One of: {MEETING_TYPES}")
    meeting_date: str = Field(..., description="ISO-8601 date of the meeting")
    motion_title: str = Field(..., min_length=5, max_length=500)
    motion_text: str = Field(..., min_length=10, description="Full text of the motion as read at the meeting")
    resolution_type: str = Field(..., description=f"One of: {RESOLUTION_TYPES}")
    vote_for: Optional[int] = Field(None, ge=0)
    vote_against: Optional[int] = Field(None, ge=0)
    vote_abstain: Optional[int] = Field(None, ge=0)
    result: str = Field(..., description=f"One of: {DECISION_RESULTS}")
    proposer: Optional[str] = None
    seconder: Optional[str] = None
    tags: List[str] = Field(default_factory=list, description="e.g. ['finance', 'maintenance', 'bylaw']")
    related_levy_id: Optional[str] = None
    related_maintenance_id: Optional[str] = None
    document_urls: List[str] = Field(default_factory=list, description="Links to supporting documents or minutes")
    notes: Optional[str] = None
    is_test_data: bool = False


class DecisionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meeting_type: Optional[str] = None
    meeting_date: Optional[str] = None
    motion_title: Optional[str] = None
    motion_text: Optional[str] = None
    resolution_type: Optional[str] = None
    vote_for: Optional[int] = None
    vote_against: Optional[int] = None
    vote_abstain: Optional[int] = None
    result: Optional[str] = None
    proposer: Optional[str] = None
    seconder: Optional[str] = None
    tags: Optional[List[str]] = None
    related_levy_id: Optional[str] = None
    related_maintenance_id: Optional[str] = None
    document_urls: Optional[List[str]] = None
    notes: Optional[str] = None


def _validate_enum_fields(
        meeting_type: Optional[str],
        resolution_type: Optional[str],
        result: Optional[str],
) -> None:
    """Generated function header.

    Function: _validate_enum_fields
    Path: backend/routers/decisions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if meeting_type and meeting_type not in MEETING_TYPES:
        raise HTTPException(400, f"Invalid meeting_type. Valid: {MEETING_TYPES}")
    if resolution_type and resolution_type not in RESOLUTION_TYPES:
        raise HTTPException(400, f"Invalid resolution_type. Valid: {RESOLUTION_TYPES}")
    if result and result not in DECISION_RESULTS:
        raise HTTPException(400, f"Invalid result. Valid: {DECISION_RESULTS}")


# ─── Routes (search BEFORE /{id}) ─────────────────────────────────────────────

@router.get("/search")
async def search_decisions(
        q: str = Query(..., min_length=2, description="Search term"),
        building_id: str = Depends(get_current_building),
        meeting_type: Optional[str] = Query(None),
        result: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None, description="ISO-8601 date — start of date range"),
        date_to: Optional[str] = Query(None, description="ISO-8601 date — end of date range"),
        tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        include_test_data: bool = Query(False),
        current_user: dict = Depends(get_current_user),
):
    """Full-text search across decision register entries.

    Searches: motion_title, motion_text, tags, decision_number.
    Supports filtering by meeting_type, result, date range, and tags.
    """
    _require_view(current_user)
    db = _get_db()

    # Build query
    query: Dict[str, Any] = {"building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}

    # Text search across key fields using regex
    search_regex = re.compile(re.escape(q), re.IGNORECASE)
    query["$or"] = [
        {"motion_title": {"$regex": search_regex}},
        {"motion_text": {"$regex": search_regex}},
        {"decision_number": {"$regex": search_regex}},
        {"tags": {"$in": [search_regex]}},
        {"notes": {"$regex": search_regex}},
    ]

    if meeting_type:
        query["meeting_type"] = meeting_type
    if result:
        query["result"] = result

    date_filter: Dict[str, str] = {}
    if date_from:
        date_filter["$gte"] = date_from
    if date_to:
        date_filter["$lte"] = date_to
    if date_filter:
        query["meeting_date"] = date_filter

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            query["tags"] = {"$in": tag_list}

    skip = (page - 1) * page_size
    # Performance Optimization⚡: Parallelize total count and paginated doc
    # fetch. This reduces sequential database round-trips from 2 to 1.
    # Additionally, uses `to_list(page_size)` for fast batch retrieval
    # instead of sequential cursor iteration.
    total_task = db.decisions.count_documents(query)
    docs_task = (
        db.decisions.find(query)
        .sort("meeting_date", -1)
        .skip(skip)
        .to_list(page_size)
    )

    total, docs = await gather(total_task, docs_task)

    decisions = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        decisions.append(doc)

    return {
        "data": decisions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "query": q,
    }


@router.post("", status_code=201)
async def create_decision(
        payload: DecisionCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Record a formal owners corporation or EC decision (resolution).

    Auto-generates decision_number in format DEC-YYYY-NNNN.
    """
    user = _require_ec(current_user)
    db = _get_db()

    _validate_enum_fields(payload.meeting_type, payload.resolution_type, payload.result)

    # Extract year from meeting_date for numbering
    try:
        year = int(payload.meeting_date[:4])
    except (ValueError, TypeError):
        year = datetime.now(timezone.utc).year

    decision_number = await _generate_decision_number(db, year)

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["decision_number"] = decision_number
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.decisions.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="decision_created",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="decision",
        resource_id=doc["id"],
        details={
            "decision_number": decision_number,
            "meeting_type": payload.meeting_type,
            "meeting_date": payload.meeting_date,
            "motion_title": payload.motion_title,
            "result": payload.result,
        },
        building_id=building_id,
    ))

    return doc


@router.get("")
async def list_decisions(
        building_id: str = Depends(get_current_building),
        meeting_type: Optional[str] = Query(None),
        resolution_type: Optional[str] = Query(None),
        result: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None, description="ISO-8601 date"),
        date_to: Optional[str] = Query(None, description="ISO-8601 date"),
        tags: Optional[str] = Query(None, description="Comma-separated tags"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        include_test_data: bool = Query(False),
        current_user: dict = Depends(get_current_user),
):
    """List decisions (building-scoped, paginated, filterable by type/result/date/tags)."""
    _require_view(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}
    if meeting_type:
        query["meeting_type"] = meeting_type
    if resolution_type:
        query["resolution_type"] = resolution_type
    if result:
        query["result"] = result

    date_filter: Dict[str, str] = {}
    if date_from:
        date_filter["$gte"] = date_from
    if date_to:
        date_filter["$lte"] = date_to
    if date_filter:
        query["meeting_date"] = date_filter

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            query["tags"] = {"$in": tag_list}

    skip = (page - 1) * page_size
    # Performance Optimization⚡: Parallelize total count and paginated doc
    # fetch. This reduces sequential database round-trips from 2 to 1.
    # Additionally, uses `to_list(page_size)` for fast batch retrieval
    # instead of sequential cursor iteration.
    total_task = db.decisions.count_documents(query)
    docs_task = (
        db.decisions.find(query)
        .sort("meeting_date", -1)
        .skip(skip)
        .to_list(page_size)
    )

    total, docs = await gather(total_task, docs_task)

    decisions = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        decisions.append(doc)

    return {
        "data": decisions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/{decision_id}")
async def get_decision(
        decision_id: str,
        include_test_data: bool = Query(False),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single decision record."""
    _require_view(current_user)
    db = _get_db()

    try:
        oid = ObjectId(decision_id)
    except Exception:
        raise HTTPException(400, "Invalid decision ID format.")

    query: dict[str, Any] = {"_id": oid, "building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}

    doc = await db.decisions.find_one(query)
    if not doc:
        raise HTTPException(404, "Decision not found.")

    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/{decision_id}")
async def update_decision(
        decision_id: str,
        building_id: str = Depends(get_current_building),
        payload: DecisionUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update a decision record (corrections, add document URLs, etc.)."""
    user = _require_ec(current_user)
    db = _get_db()

    try:
        oid = ObjectId(decision_id)
    except Exception:
        raise HTTPException(400, "Invalid decision ID format.")

    _validate_enum_fields(payload.meeting_type, payload.resolution_type, payload.result)

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = user["id"]

    result = await db.decisions.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Decision not found.")

    asyncio.create_task(create_audit_log(
        action="decision_updated",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="decision",
        resource_id=decision_id,
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    ))

    return {"message": "Decision updated.", "decision_id": decision_id}


@router.delete("/{decision_id}", status_code=204)
async def delete_decision(
        decision_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Delete a test decision record. Production decision records remain append-only."""
    user = _require_ec(current_user)
    db = _get_db()

    try:
        oid = ObjectId(decision_id)
    except Exception:
        raise HTTPException(400, "Invalid decision ID format.")

    doc = await db.decisions.find_one({"_id": oid, "building_id": building_id}, {"_id": 0, "is_test_data": 1})
    if not doc:
        raise HTTPException(404, "Decision not found.")
    if not doc.get("is_test_data"):
        raise HTTPException(400, "Only is_test_data decision records can be deleted.")

    result = await db.decisions.delete_one({"_id": oid, "building_id": building_id, "is_test_data": True})
    if result.deleted_count == 0:
        raise HTTPException(404, "Decision not found.")

    asyncio.create_task(create_audit_log(
        action="decision_deleted",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="decision",
        resource_id=decision_id,
        details={"is_test_data": True},
        building_id=building_id,
    ))
    return None
