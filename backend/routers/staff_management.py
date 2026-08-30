"""
Staff User Management Router

Super Admin only endpoints for managing staff accounts (strata_manager,
real_estate_agent, admin_staff, service_provider) and assigning them elevated roles per building.

Endpoints:
  GET    /admin/staff-users                                   — list all staff users
  GET    /admin/staff-users/{user_id}                         — staff user detail + role assignments
  POST   /admin/staff-users/{user_id}/building-roles          — assign a role to a staff user for a building
  DELETE /admin/staff-users/{user_id}/building-roles/{asgn_id} — revoke a role assignment
  GET    /admin/staff-users/buildings                         — list available buildings (for dropdowns)

Data isolation: A staff user may be assigned roles in multiple buildings but
cannot see data for buildings they are not assigned to. All queries are
building-scoped and cross-building data leaks are prevented at the API layer.
"""

from datetime import datetime, timezone
from uuid import uuid4

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional

from database import db
from models.user import UserRole
from utils.auth import get_current_user, effective_role
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────────

# Roles staff members can be elevated to (per building)
ELEVATABLE_ROLES = {"owner", "ec_member", "secretary", "treasurer", "strata_admin"}

# Roles treated as "staff" for this management interface
STAFF_ROLES = {"strata_manager", "real_estate_agent", "admin_staff", "service_provider"}

# EC sub-roles that map to the legacy ec_position field on the user document.
# Strata Admin is an organisation-administration role, not an EC chairperson
# shortcut. A chairperson appointment should be represented by an ec_member
# assignment plus ec_position="CHAIRMAN".
EC_POSITION_MAP = {
    "treasurer": "TREASURER",
    "secretary": "SECRETARY",
    "ec_member": "MEMBER",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _new_id() -> str:
    """Generated function header.

    Function: _new_id
    Path: backend/routers/staff_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid4())


def _utcnow() -> datetime:
    """Generated function header.

    Function: _utcnow
    Path: backend/routers/staff_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc)


def _require_super_admin(current_user: dict) -> None:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/staff_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super Admin access required.")


async def _get_staff_user_or_404(user_id: str) -> dict:
    """Generated function header.

    Function: _get_staff_user_or_404
    Path: backend/routers/staff_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = await db.users.find_one({"id": user_id, "role": {"$in": list(STAFF_ROLES)}})
    if not user:
        raise HTTPException(status_code=404, detail="Staff user not found.")
    return user


async def _enrich_staff_user(user: dict) -> dict:
    """Add building memberships + active RBAC role assignments to a staff user dict."""
    uid = user.get("id", "")

    # Stage 1: Parallel fetch memberships and active role assignments
    memberships_task = db.memberships.find({"user_id": uid}).to_list(100)
    role_assignments_task = db.user_roles.find(
        {"user_id": uid, "is_active": True}
    ).to_list(200)

    memberships, role_assignments = await asyncio.gather(
        memberships_task, role_assignments_task
    )

    building_ids = [m.get("building_id") for m in memberships if m.get("building_id")]
    role_ids = list(
        {r.get("role_id", "") for r in role_assignments if r.get("role_id")}
    )

    # Stage 2: Parallel fetch building details and role definitions
    buildings = []
    role_defs = {}

    batch_tasks = []
    batch_task_map = {}

    if building_ids:
        batch_task_map["buildings"] = len(batch_tasks)
        batch_tasks.append(
            db.buildings.find(
                {"id": {"$in": building_ids}},
                {"_id": 0, "id": 1, "name": 1, "address": 1},
            ).to_list(100)
        )

    if role_ids:
        batch_task_map["roles"] = len(batch_tasks)
        batch_tasks.append(db.roles.find({"id": {"$in": role_ids}}).to_list(100))

    if batch_tasks:
        batch_results = await asyncio.gather(*batch_tasks)
        if "buildings" in batch_task_map:
            buildings = batch_results[batch_task_map["buildings"]]
        if "roles" in batch_task_map:
            for rd in batch_results[batch_task_map["roles"]]:
                role_defs[rd["id"]] = rd.get("name", rd["id"])

    enriched_assignments = []
    for ra in role_assignments:
        rid = ra.get("role_id", "")
        enriched_assignments.append(
            {
                "id": ra.get("id"),
                "role_id": rid,
                "role_name": role_defs.get(rid, rid),
                "building_id": ra.get("building_id"),
                "unit_id": ra.get("unit_id"),
                "start_date": ra.get("start_date"),
                "end_date": ra.get("end_date"),
                "is_active": ra.get("is_active", True),
                "notes": ra.get("notes"),
                "created_at": ra.get("created_at"),
            }
        )

    return {
        "id": uid,
        "full_name": user.get("full_name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", ""),
        "phone": user.get("phone", ""),
        "organisation": user.get("organisation", ""),
        "professional_licence": user.get("professional_licence", ""),
        "is_active": user.get("is_active", False),
        "is_approved": user.get("is_approved", False),
        "status": user.get("status", ""),
        "ec_position": user.get("ec_position"),
        "created_at": user.get("created_at", ""),
        "last_login_at": user.get("last_login_at"),
        "buildings": buildings,
        "building_ids": building_ids,
        "role_assignments": enriched_assignments,
    }


# ── Request / Response models ─────────────────────────────────────────────────


class BuildingRoleAssignRequest(BaseModel):
    role_name: str  # e.g. "owner", "ec_member", "strata_admin"
    building_id: str  # which building
    unit_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    notes: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/admin/staff-users", summary="List all staff users")
async def list_staff_users(
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        current_user: dict = Depends(get_current_user),
):
    """
    Returns all strata_manager / real_estate_agent / admin_staff / service_provider users across
    all buildings (super admin view, not limited to a single building).

    Each user includes their building memberships and active RBAC role
    assignments. Optimized with batch fetching to eliminate O(N) database
    round-trips.
    """
    _require_super_admin(current_user)

    query: dict = {"role": {"$in": list(STAFF_ROLES)}}
    if role and role in STAFF_ROLES:
        query["role"] = role
    if is_active is not None:
        query["is_active"] = is_active

    # Exclude archived
    query["status"] = {"$nin": ["archived"]}

    users = await db.users.find(query, {"_id": 0, "hashed_password": 0}).to_list(500)
    if not users:
        return []

    user_ids = [u["id"] for u in users]

    # Batch fetch all memberships and active role assignments for all users
    # in parallel
    memberships_task = db.memberships.find({"user_id": {"$in": user_ids}}).to_list(1000)
    role_assignments_task = db.user_roles.find(
        {"user_id": {"$in": user_ids}, "is_active": True}
    ).to_list(2000)

    all_memberships, all_role_assignments = await asyncio.gather(
        memberships_task, role_assignments_task
    )

    # Group related data by user_id
    memberships_by_user = {}
    for m in all_memberships:
        user_id = m.get("user_id")
        if user_id:
            memberships_by_user.setdefault(user_id, []).append(m)

    assignments_by_user = {}
    for ra in all_role_assignments:
        user_id = ra.get("user_id")
        if user_id:
            assignments_by_user.setdefault(user_id, []).append(ra)

    # Collect building and role IDs for batch fetching definitions
    building_ids = list(
        {m.get("building_id") for m in all_memberships if m.get("building_id")}
    )
    role_ids = list(
        {ra.get("role_id", "") for ra in all_role_assignments if ra.get("role_id")}
    )

    # Parallel fetch building details and role definitions
    batch_tasks = []
    batch_task_map = {}

    if building_ids:
        batch_task_map["buildings"] = len(batch_tasks)
        batch_tasks.append(
            db.buildings.find(
                {"id": {"$in": building_ids}},
                {"_id": 0, "id": 1, "name": 1, "address": 1},
            ).to_list(1000)
        )

    if role_ids:
        batch_task_map["roles"] = len(batch_tasks)
        batch_tasks.append(db.roles.find({"id": {"$in": role_ids}}).to_list(500))

    batch_results = await asyncio.gather(*batch_tasks) if batch_tasks else []

    all_buildings = (
        batch_results[batch_task_map["buildings"]]
        if "buildings" in batch_task_map
        else []
    )
    all_role_defs = (
        batch_results[batch_task_map["roles"]] if "roles" in batch_task_map else []
    )

    # Index definitions for O(1) lookup
    buildings_by_id = {b["id"]: b for b in all_buildings}
    role_defs_by_id = {rd["id"]: rd.get("name", rd["id"]) for rd in all_role_defs}

    results = []
    for u in users:
        uid = u["id"]
        user_memberships = memberships_by_user.get(uid, [])
        user_building_ids = [
            m.get("building_id") for m in user_memberships if m.get("building_id")
        ]
        user_buildings = [
            buildings_by_id[bid] for bid in user_building_ids if bid in buildings_by_id
        ]

        user_assignments = assignments_by_user.get(uid, [])
        enriched_assignments = []
        for ra in user_assignments:
            rid = ra.get("role_id", "")
            enriched_assignments.append(
                {
                    "id": ra.get("id"),
                    "role_id": rid,
                    "role_name": role_defs_by_id.get(rid, rid),
                    "building_id": ra.get("building_id"),
                    "unit_id": ra.get("unit_id"),
                    "start_date": ra.get("start_date"),
                    "end_date": ra.get("end_date"),
                    "is_active": ra.get("is_active", True),
                    "notes": ra.get("notes"),
                    "created_at": ra.get("created_at"),
                }
            )

        results.append(
            {
                "id": uid,
                "full_name": u.get("full_name", ""),
                "email": u.get("email", ""),
                "role": u.get("role", ""),
                "phone": u.get("phone", ""),
                "organisation": u.get("organisation", ""),
                "professional_licence": u.get("professional_licence", ""),
                "is_active": u.get("is_active", False),
                "is_approved": u.get("is_approved", False),
                "status": u.get("status", ""),
                "ec_position": u.get("ec_position"),
                "created_at": u.get("created_at", ""),
                "last_login_at": u.get("last_login_at"),
                "buildings": user_buildings,
                "building_ids": user_building_ids,
                "role_assignments": enriched_assignments,
            }
        )

    return results


@router.get("/admin/staff-users/buildings", summary="List buildings for dropdowns")
async def list_buildings_for_staff(
        current_user: dict = Depends(get_current_user),
):
    """Return all active buildings — used in role-assignment dropdowns."""
    _require_super_admin(current_user)
    buildings = await db.buildings.find(
        {"is_active": True}, {"_id": 0, "id": 1, "name": 1, "address": 1}
    ).to_list(200)
    return buildings


@router.get("/admin/staff-users/{user_id}", summary="Get staff user detail")
async def get_staff_user(
        user_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Full profile for a single staff user including:
    - Profile fields
    - Building memberships
    - All RBAC role assignments (active only)
    """
    _require_super_admin(current_user)
    user = await _get_staff_user_or_404(user_id)
    return await _enrich_staff_user(user)


@router.post(
    "/admin/staff-users/{user_id}/building-roles",
    status_code=status.HTTP_201_CREATED,
    summary="Assign an elevated role to a staff user for a building",
)
async def assign_building_role(
        user_id: str,
        body: BuildingRoleAssignRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Assign a governance / owner role to a staff user scoped to a specific building.

    Allowed elevation roles: owner, ec_member, secretary, treasurer, strata_admin.

    The assignment is written to the ``user_roles`` (RBAC) collection.
    For EC governance roles (ec_member, secretary, treasurer) the
    legacy ``ec_position`` field on the user document is also updated so that
    existing checks continue to work.

    Building-isolation guarantee: the building_id is validated to be a real,
    active building before any write is performed.
    """
    _require_super_admin(current_user)

    role_name = body.role_name.lower()
    if role_name not in ELEVATABLE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role '{role_name}' cannot be assigned here. "
                   f"Allowed: {sorted(ELEVATABLE_ROLES)}",
        )

    # Validate building exists
    building = await db.buildings.find_one({"id": body.building_id, "is_active": True})
    if not building:
        raise HTTPException(status_code=404, detail="Building not found or inactive.")

    # Validate target is a staff user
    target = await _get_staff_user_or_404(user_id)

    # Look up the role definition in the roles collection
    role_def = await db.roles.find_one({"name": role_name.upper()})
    if not role_def:
        # Fallback: use role_name itself as role_id (seeds may use different casing)
        role_def = await db.roles.find_one(
            {"name": {"$regex": f"^{role_name}$", "$options": "i"}}
        )
    role_id = role_def["id"] if role_def else role_name

    # Check for a duplicate active assignment (same user + role + building)
    existing = await db.user_roles.find_one(
        {
            "user_id": user_id,
            "role_id": role_id,
            "building_id": body.building_id,
            "is_active": True,
        }
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"User already has role '{role_name}' in this building.",
        )

    # Create the assignment
    assignment_id = _new_id()
    now = _utcnow()
    doc = {
        "id": assignment_id,
        "user_id": user_id,
        "role_id": role_id,
        # role_name stored explicitly so the M-1 role-sync query can project it
        # without a join back to the roles collection.
        "role_name": role_name,
        "building_id": body.building_id,
        "unit_id": body.unit_id,
        "start_date": (
            body.start_date.isoformat() if body.start_date else now.isoformat()
        ),
        "end_date": body.end_date.isoformat() if body.end_date else None,
        "is_active": True,
        "assigned_by": str(current_user.get("id", "")),
        "notes": body.notes,
        "created_at": now.isoformat(),
    }
    await db.user_roles.insert_one(doc)

    # Also ensure the user is a member of this building
    existing_membership = await db.memberships.find_one(
        {
            "user_id": user_id,
            "building_id": body.building_id,
        }
    )
    if not existing_membership:
        await db.memberships.insert_one(
            {
                "id": _new_id(),
                "user_id": user_id,
                "building_id": body.building_id,
                "role": role_name,
                "is_active": True,
                "created_at": now.isoformat(),
            }
        )

    # Update legacy ec_position for EC governance roles (non-breaking backward compat)
    ec_pos = EC_POSITION_MAP.get(role_name)
    if ec_pos:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"ec_position": ec_pos}},
        )

    # M-1: sync users.role to the highest-privilege active role for this user+building.
    # users.role drives JWT claims and permission guards; it must reflect the user's
    # current highest role, not just the legacy field set at registration.
    # Phase G: core.user_role_assignments is already written correctly; add a
    #   core.users.role update in identity_repo.update_user_role() (Phase G task).
    _ROLE_PRIORITY = [
        "super_admin", "strata_admin", "ec_member", "strata_manager",
        "admin_staff", "owner", "tenant", "service_provider", "guest",
    ]
    # secretary and treasurer are EC governance sub-roles — normalise to ec_member
    # so the priority comparison works correctly.
    _EC_SUB_ROLES = {"secretary", "treasurer"}
    _active_roles = await db.user_roles.find(
        {"user_id": user_id, "building_id": body.building_id, "is_active": True},
        {"_id": 0, "role_name": 1},
    ).to_list(20)
    _active_names = {
        "ec_member" if (r.get("role_name") or "").lower() in _EC_SUB_ROLES
        else (r.get("role_name") or "").lower()
        for r in _active_roles
        if r.get("role_name")   # skip legacy/rbac.py docs that only stored role_id
    }
    # Guard against downgrade caused by rbac.py-created assignments that store
    # only role_id (no role_name) — those are skipped above, so without this guard
    # a user with a high role from rbac.py could have users.role reduced to the
    # lower role being assigned here. Include the current users.role as a baseline
    # so we never set a role of lower priority than what the user already holds.
    _current_user_doc = await db.users.find_one({"id": user_id}, {"_id": 0, "role": 1})
    if _current_user_doc and _current_user_doc.get("role"):
        _active_names.add((_current_user_doc["role"] or "").lower())
    _highest = next((r for r in _ROLE_PRIORITY if r in _active_names), None)
    if _highest:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"role": _highest, "updated_at": now.isoformat()}},
        )

    # Audit log (fire-and-forget)
    asyncio.create_task(
        create_audit_log(
            action="staff.role_assigned",
            resource_type="user_role",
            resource_id=assignment_id,
            user_id=str(current_user.get("id", "")),
            user_name=str(current_user.get("full_name", current_user.get("email", ""))),
            details={
                "target_user_id": user_id,
                "target_email": target.get("email"),
                "role": role_name,
                "building_id": body.building_id,
                "assignment_id": assignment_id,
            },
        )
    )

    return {
        "id": assignment_id,
        "message": f"Role '{role_name}' assigned to user in building '{body.building_id}'.",
        "assignment": doc,
    }


@router.delete(
    "/admin/staff-users/{user_id}/building-roles/{assignment_id}",
    summary="Revoke a role assignment from a staff user",
)
async def revoke_building_role(
        user_id: str,
        assignment_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Soft-revoke a role assignment (is_active = False).
    The record is preserved for audit purposes.

    If the revoked role was an EC governance role and no other active EC role
    remains for the user in any building, the legacy ``ec_position`` field is
    cleared.
    """
    _require_super_admin(current_user)

    target = await _get_staff_user_or_404(user_id)

    assignment = await db.user_roles.find_one(
        {
            "id": assignment_id,
            "user_id": user_id,
        }
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Role assignment not found.")

    if not assignment.get("is_active", True):
        raise HTTPException(
            status_code=409, detail="Role assignment is already revoked."
        )

    await db.user_roles.update_one(
        {"id": assignment_id},
        {
            "$set": {
                "is_active": False,
                "revoked_at": _utcnow().isoformat(),
                "revoked_by": str(current_user.get("id", "")),
            }
        },
    )

    # Clear legacy ec_position if no active EC roles remain for this user
    revoked_role_id = assignment.get("role_id", "")
    revoked_role_name = revoked_role_id.lower()
    if revoked_role_name in EC_POSITION_MAP:
        remaining_ec = await db.user_roles.find_one(
            {
                "user_id": user_id,
                "role_id": {"$in": list(EC_POSITION_MAP.keys())},
                "is_active": True,
            }
        )
        if not remaining_ec:
            await db.users.update_one(
                {"id": user_id},
                {"$unset": {"ec_position": ""}},
            )

    asyncio.create_task(
        create_audit_log(
            action="staff.role_revoked",
            resource_type="user_role",
            resource_id=assignment_id,
            user_id=str(current_user.get("id", "")),
            user_name=str(current_user.get("full_name", current_user.get("email", ""))),
            details={
                "target_user_id": user_id,
                "target_email": target.get("email"),
                "assignment_id": assignment_id,
                "role_id": assignment.get("role_id"),
                "building_id": assignment.get("building_id"),
            },
        )
    )

    return {"message": "Role assignment revoked.", "assignment_id": assignment_id}
