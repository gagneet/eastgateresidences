"""
Organisations Router — Multi-Building Management

Endpoints:
  POST  /organisations                     — create organisation
  GET   /organisations                     — list (super_admin sees all; others see own)
  GET   /organisations/{org_id}            — get org details
  PUT   /organisations/{org_id}            — update org
  POST  /organisations/{org_id}/buildings  — assign building to org
  GET   /organisations/{org_id}/buildings  — list org's buildings
  DELETE /organisations/{org_id}/buildings/{building_id} — unassign
  POST  /organisations/{org_id}/members    — add member
  GET   /organisations/{org_id}/members    — list members
  DELETE /organisations/{org_id}/members/{user_id} — remove member
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from models.organisation import (
    OrgBuildingAssignment,
    OrgMemberCreate,
    OrganisationCreate,
    OrganisationResponse,
)
from utils.auth import get_current_user, effective_role
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/organisations", tags=["Organisations"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_super_admin(user: dict) -> dict:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) != "super_admin":
        raise HTTPException(403, "Super admin access required.")
    return user


def _parse_object_id(raw_id: str, label: str = "ID") -> ObjectId:
    """Convert a string to ObjectId; raise HTTP 400 if the format is invalid."""
    try:
        return ObjectId(raw_id)
    except Exception:
        raise HTTPException(400, f"Invalid {label}: '{raw_id}' is not a valid identifier.")


def _require_org_admin(user: dict) -> dict:
    """Generated function header.

    Function: _require_org_admin
    Path: backend/routers/organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Organisation admin access required.")
    return user


# ─── Organisation CRUD ────────────────────────────────────────────────────────

@router.post("/", response_model=OrganisationResponse, status_code=201)
async def create_organisation(
        payload: OrganisationCreate,
        current_user: dict = Depends(get_current_user),
):
    """Create a new organisation."""
    user = _require_super_admin(current_user)
    db = _get_db()

    doc = payload.model_dump()
    doc["status"] = "active"
    doc["building_count"] = 0
    doc["member_count"] = 0
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = str(user["_id"])

    result = await db.organisations.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="organisation_created",
        user_id=str(user["_id"]),
        user_name=user.get("full_name", ""),
        resource_type="organisation",
        resource_id=doc["id"],
        details={"name": payload.name, "org_type": payload.org_type},
    ))

    return OrganisationResponse(**doc)


@router.get("/")
async def list_organisations(
        status: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List organisations. Super admins see all; others see orgs they belong to."""
    db = _get_db()
    user_id = str(current_user["_id"])

    query: dict = {}
    if effective_role(current_user) != "super_admin":
        # Find orgs this user belongs to
        member_docs = db.organisation_members.find({"user_id": user_id})
        org_ids = [doc["organisation_id"] async for doc in member_docs]
        query["_id"] = {"$in": [_parse_object_id(oid, "organisation ID") for oid in org_ids]}

    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    total = await db.organisations.count_documents(query)
    cursor = db.organisations.find(query).sort("name", 1).skip(skip).limit(page_size)

    orgs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        orgs.append(doc)

    return {"data": orgs, "total": total, "page": page, "page_size": page_size}


@router.get("/{org_id}")
async def get_organisation(
        org_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Get organisation details."""
    db = _get_db()
    user_id = str(current_user["_id"])

    # Enforce access: super_admin sees all; others must be a member of this org
    if effective_role(current_user) != "super_admin":
        member = await db.organisation_members.find_one({
            "organisation_id": org_id,
            "user_id": user_id,
        })
        if not member:
            raise HTTPException(403, "Access denied: not a member of this organisation.")

    doc = await db.organisations.find_one({"_id": _parse_object_id(org_id, "organisation ID")})
    if not doc:
        raise HTTPException(404, "Organisation not found.")
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/{org_id}")
async def update_organisation(
        org_id: str,
        payload: dict,
        current_user: dict = Depends(get_current_user),
):
    """Update organisation fields."""
    user = _require_org_admin(current_user)
    db = _get_db()

    immutable = {"_id", "id", "created_at", "created_by", "building_count", "member_count"}
    update = {k: v for k, v in payload.items() if k not in immutable}
    update["updated_at"] = _now_iso()

    result = await db.organisations.update_one(
        {"_id": _parse_object_id(org_id, "organisation ID")},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Organisation not found.")
    return {"message": "Organisation updated.", "org_id": org_id}


# ─── Building Assignments ─────────────────────────────────────────────────────

@router.post("/{org_id}/buildings")
async def assign_building(
        org_id: str,
        payload: OrgBuildingAssignment,
        current_user: dict = Depends(get_current_user),
):
    """Assign a building to an organisation."""
    user = _require_org_admin(current_user)
    db = _get_db()

    # Check org exists
    org = await db.organisations.find_one({"_id": _parse_object_id(org_id, "organisation ID")})
    if not org:
        raise HTTPException(404, "Organisation not found.")

    existing = await db.organisation_buildings.find_one({
        "organisation_id": org_id,
        "building_id": payload.building_id,
    })
    if existing:
        raise HTTPException(409, "Building already assigned to this organisation.")

    doc = {
        "organisation_id": org_id,
        "building_id": payload.building_id,
        "role": payload.role,
        "assigned_by": str(user["_id"]),
        "assigned_at": _now_iso(),
    }
    await db.organisation_buildings.insert_one(doc)
    await db.organisations.update_one(
        {"_id": _parse_object_id(org_id, "organisation ID")},
        {"$inc": {"building_count": 1}},
    )

    return {"message": "Building assigned.", "org_id": org_id, "building_id": payload.building_id}


@router.get("/{org_id}/buildings")
async def list_org_buildings(
        org_id: str,
        current_user: dict = Depends(get_current_user),
):
    """List all buildings belonging to an organisation."""
    db = _get_db()
    cursor = db.organisation_buildings.find({"organisation_id": org_id})
    buildings = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        buildings.append(doc)
    return {"data": buildings, "count": len(buildings)}


@router.delete("/{org_id}/buildings/{building_id}")
async def unassign_building(
        org_id: str,
        building_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Remove a building from an organisation."""
    user = _require_org_admin(current_user)
    db = _get_db()

    result = await db.organisation_buildings.delete_one({
        "organisation_id": org_id,
        "building_id": building_id,
    })
    if result.deleted_count == 0:
        raise HTTPException(404, "Building assignment not found.")

    await db.organisations.update_one(
        {"_id": _parse_object_id(org_id, "organisation ID")},
        {"$inc": {"building_count": -1}},
    )
    return {"message": "Building unassigned."}


# ─── Members ──────────────────────────────────────────────────────────────────

@router.post("/{org_id}/members")
async def add_org_member(
        org_id: str,
        payload: OrgMemberCreate,
        current_user: dict = Depends(get_current_user),
):
    """Grant a user access to the organisation."""
    user = _require_org_admin(current_user)
    db = _get_db()

    existing = await db.organisation_members.find_one({
        "organisation_id": org_id,
        "user_id": payload.user_id,
    })
    if existing:
        raise HTTPException(409, "User already a member of this organisation.")

    doc = {
        "organisation_id": org_id,
        "user_id": payload.user_id,
        "role": payload.role,
        "building_ids": payload.building_ids,
        "added_by": str(user["_id"]),
        "joined_at": _now_iso(),
    }
    await db.organisation_members.insert_one(doc)
    await db.organisations.update_one(
        {"_id": _parse_object_id(org_id, "organisation ID")},
        {"$inc": {"member_count": 1}},
    )

    return {"message": "Member added.", "org_id": org_id, "user_id": payload.user_id}


@router.get("/{org_id}/members")
async def list_org_members(
        org_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
):
    """List all members of an organisation."""
    db = _get_db()
    skip = (page - 1) * page_size
    total = await db.organisation_members.count_documents({"organisation_id": org_id})
    cursor = (
        db.organisation_members.find({"organisation_id": org_id})
        .skip(skip)
        .limit(page_size)
    )
    members = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        members.append(doc)
    return {"data": members, "total": total, "page": page}


@router.delete("/{org_id}/members/{user_id}")
async def remove_org_member(
        org_id: str,
        user_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Remove a user from the organisation."""
    _require_org_admin(current_user)
    db = _get_db()

    result = await db.organisation_members.delete_one({
        "organisation_id": org_id,
        "user_id": user_id,
    })
    if result.deleted_count == 0:
        raise HTTPException(404, "Member not found.")

    await db.organisations.update_one(
        {"_id": _parse_object_id(org_id, "organisation ID")},
        {"$inc": {"member_count": -1}},
    )
    return {"message": "Member removed."}


# ── Building accessibility endpoint (B2-04) ────────────────────────────────────


from fastapi import APIRouter as _APIRouter  # noqa: F811 — avoid shadow

_buildings_router = _APIRouter()


@_buildings_router.get("/buildings/accessible")
async def get_accessible_buildings(current_user: dict = Depends(get_current_user)):
    """
    Return buildings the authenticated user can access.

    - strata_manager / super_admin: all buildings in their organisation
    - owner / tenant / ec_member / chairman: only their own building
    """
    db = _get_db()
    user_org_id = current_user.get("organisation_id", "org-silverfox-001")
    role = effective_role(current_user)

    multi_building_roles = {"super_admin", "strata_manager"}

    if role in multi_building_roles:
        # Return all buildings assigned to the organisation
        cursor = db.organisation_buildings.find(
            {"organisation_id": user_org_id, "is_active": True},
            {"_id": 0, "building_id": 1, "building_name": 1, "address": 1},
        )
        docs = await cursor.to_list(length=100)
        if docs:
            return [
                {
                    "building_id": d["building_id"],
                    "name": d.get("building_name", d["building_id"]),
                    "address": d.get("address", ""),
                }
                for d in docs
            ]

    # For other roles: return the user's own building from the buildings collection
    user_building_id = current_user.get("building_id")
    if user_building_id:
        building = await db.buildings.find_one(
            {"$or": [{"id": user_building_id}, {"building_id": user_building_id}]},
            {"_id": 0, "id": 1, "building_id": 1, "name": 1, "address": 1},
        )
        if building:
            bid = building.get("id") or building.get("building_id", user_building_id)
            return [{"building_id": bid, "name": building.get("name", bid), "address": building.get("address", "")}]

    return []
