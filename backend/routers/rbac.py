"""
RBAC Router — Role-Based Access Control API

New endpoints for the RBAC+ABAC authorization system.
These are ADDITIVE — they do not replace or modify existing endpoints.

Endpoints:
  GET  /auth/rbac/permissions          — List all permission slugs
  GET  /auth/rbac/roles                — List all role definitions
  POST /auth/rbac/check                — Check if current user has permission

  GET  /auth/rbac/users/{user_id}/roles         — Get user's role assignments
  POST /auth/rbac/users/{user_id}/roles         — Assign role to user
  DELETE /auth/rbac/users/{user_id}/roles/{assignment_id} — Revoke role assignment

  GET  /auth/rbac/users/{user_id}/relationships — Get user's relationship tuples
  POST /auth/rbac/relationships                 — Create relationship tuple
  DELETE /auth/rbac/relationships/{tuple_id}    — Remove relationship tuple

  POST /auth/rbac/migrate/user/{user_id}        — Migrate single user to RBAC
  POST /auth/rbac/migrate/all                   — Migrate all users (admin only)
"""

from datetime import datetime, timezone
from uuid import uuid4

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional

from database import db
from models.rbac_models import (
    PERMISSION_SLUGS,
    PermissionCheckRequest,
    PermissionCheckResponse,
    RelationshipCreateRequest,
    UserRoleAssignRequest,
)
from models.user import UserRole
from utils.auth import get_current_user, effective_role
from utils.helpers import create_audit_log
from utils.permissions import get_user_permissions

# ── Optional service imports — degrade gracefully while services are being developed ──

try:
    from services.permission_service import user_can, sync_user_relationships

    _PERMISSION_SERVICE_AVAILABLE = True
except ImportError:
    _PERMISSION_SERVICE_AVAILABLE = False
    user_can = None
    sync_user_relationships = None

try:
    from services.authorization_engine import (
        add_relationship,
        check_permission,
        expire_committee_relations,
        get_user_relationships,
        remove_relationship,
    )

    _AUTH_ENGINE_AVAILABLE = True
except ImportError:
    _AUTH_ENGINE_AVAILABLE = False
    add_relationship = None
    check_permission = None
    expire_committee_relations = None
    get_user_relationships = None
    remove_relationship = None

logger = logging.getLogger(__name__)

# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth/rbac", tags=["RBAC"])


# ── Helpers ──────────────────────────────────────────────────────────────────


def _new_id() -> str:
    """Generated function header.

    Function: _new_id
    Path: backend/routers/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid4())


def _utcnow() -> datetime:
    """Generated function header.

    Function: _utcnow
    Path: backend/routers/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc)


def _require_service(flag: bool, name: str) -> None:
    """Generated function header.

    Function: _require_service
    Path: backend/routers/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not flag:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} is not available — service module not yet loaded.",
        )


def _require_can_manage_users(current_user: dict) -> None:
    """Generated function header.

    Function: _require_can_manage_users
    Path: backend/routers/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    perms = get_user_permissions(current_user)
    if not perms.can_manage_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to manage users.",
        )


def _require_super_admin(current_user: dict) -> None:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin access required.",
        )


def _require_own_or_can_manage(user_id: str, current_user: dict) -> None:
    """Generated function header.

    Function: _require_own_or_can_manage
    Path: backend/routers/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if str(current_user.get("_id", "")) == user_id:
        return
    perms = get_user_permissions(current_user)
    if not perms.can_manage_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own data, or you need user management permission.",
        )


# ── GET /auth/rbac/permissions ───────────────────────────────────────────────


@router.get("/permissions", summary="List all permission slugs")
async def list_permissions(current_user: dict = Depends(get_current_user)):
    """Return all known permission slugs, descriptions, and categories."""
    try:
        permissions = await db.permissions.find({}, {"_id": 0}).to_list(length=None)
        if not permissions:
            # Fall back to the static PERMISSION_SLUGS constant if the collection is empty
            permissions = [
                {"slug": slug, "description": desc, "category": category}
                for slug, (category, desc) in PERMISSION_SLUGS.items()
            ]
        return {"permissions": permissions}
    except Exception as exc:
        logger.error("list_permissions: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch permissions.")


# ── GET /auth/rbac/roles ─────────────────────────────────────────────────────


@router.get("/roles", summary="List all role definitions")
async def list_roles(current_user: dict = Depends(get_current_user)):
    """Return all role definitions from the roles collection."""
    try:
        roles = await db.roles.find({}, {"_id": 0}).to_list(length=None)
        return {"roles": roles}
    except Exception as exc:
        logger.error("list_roles: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch roles.")


# ── POST /auth/rbac/check ────────────────────────────────────────────────────


@router.post("/check", response_model=PermissionCheckResponse, summary="Check permission for a user")
async def check_user_permission(
        body: PermissionCheckRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Check whether a user has a given permission slug.

    The caller must be the same user being checked, or a Super Admin.
    """
    current_user_id = str(current_user.get("_id", ""))
    if current_user_id != body.user_id and effective_role(current_user) != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only check permissions for yourself.",
        )

    _require_service(_PERMISSION_SERVICE_AVAILABLE, "PermissionService")

    try:
        result: PermissionCheckResponse = await user_can(
            user_id=body.user_id,
            permission_slug=body.action,
            building_id=body.building_id,
            resource=body.object_id,
        )
        return result
    except Exception as exc:
        logger.error("check_user_permission: %s", exc)
        raise HTTPException(status_code=500, detail=f"Permission check failed: {exc}")


# ── GET /auth/rbac/users/{user_id}/roles ─────────────────────────────────────


@router.get("/users/{user_id}/roles", summary="Get role assignments for a user")
async def get_user_roles(
        user_id: str,
        building_id: Optional[str] = Query(None, description="Filter by building"),
        include_inactive: bool = Query(False, description="Include revoked assignments"),
        current_user: dict = Depends(get_current_user),
):
    """Return active (and optionally inactive) role assignments for the given user."""
    _require_own_or_can_manage(user_id, current_user)

    try:
        query: dict = {"user_id": user_id}
        if not include_inactive:
            query["is_active"] = True
        if building_id:
            query["building_id"] = building_id

        assignments = await db.user_roles.find(query, {"_id": 0}).to_list(length=None)
        return {"user_id": user_id, "assignments": assignments}
    except Exception as exc:
        logger.error("get_user_roles user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch role assignments.")


# ── POST /auth/rbac/users/{user_id}/roles ────────────────────────────────────


@router.post("/users/{user_id}/roles", status_code=status.HTTP_201_CREATED, summary="Assign a role to a user")
async def assign_role(
        user_id: str,
        body: UserRoleAssignRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Create a new UserRoleAssignment for the specified user.

    Requires ``can_manage_users`` permission.
    After assigning, relationship tuples are synced for the user.
    """
    _require_can_manage_users(current_user)

    target_user = await db.users.find_one({"_id": user_id})
    if not target_user:
        # Try string _id lookup common in this codebase
        target_user = await db.users.find_one({"_id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    assignment_id = _new_id()
    assignment_doc = {
        "id": assignment_id,
        "user_id": user_id,
        "role_id": body.role_id,
        "building_id": body.building_id,
        "unit_id": body.unit_id,
        "start_date": body.start_date.isoformat() if body.start_date else None,
        "end_date": body.end_date.isoformat() if body.end_date else None,
        "is_active": True,
        "assigned_by": str(current_user.get("_id", "")),
        "notes": body.notes,
        "created_at": _utcnow().isoformat(),
    }

    try:
        await db.user_roles.insert_one(assignment_doc)
    except Exception as exc:
        logger.error("assign_role insert user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to create role assignment.")

    # Sync relationship tuples (fire-and-forget)
    if _PERMISSION_SERVICE_AVAILABLE:
        asyncio.create_task(sync_user_relationships(user_id))

    # Audit log (fire-and-forget)
    asyncio.create_task(
        create_audit_log(
            action="rbac.role_assigned",
            user_id=str(current_user.get("_id", "")),
            details={
                "target_user_id": user_id,
                "role_id": body.role_id,
                "assignment_id": assignment_id,
                "building_id": body.building_id,
            },
        )
    )

    return {"id": assignment_id, "message": "Role assigned successfully."}


# ── DELETE /auth/rbac/users/{user_id}/roles/{assignment_id} ──────────────────


@router.delete(
    "/users/{user_id}/roles/{assignment_id}",
    summary="Revoke a role assignment",
)
async def revoke_role(
        user_id: str,
        assignment_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Soft-revoke a role assignment by setting ``is_active=False``.

    If the role was a committee role, related relationship tuples are also expired.
    Requires ``can_manage_users`` permission.
    """
    _require_can_manage_users(current_user)

    assignment = await db.user_roles.find_one({"id": assignment_id, "user_id": user_id})
    if not assignment:
        raise HTTPException(status_code=404, detail="Role assignment not found.")

    try:
        await db.user_roles.update_one(
            {"id": assignment_id},
            {"$set": {"is_active": False, "revoked_at": _utcnow().isoformat()}},
        )
    except Exception as exc:
        logger.error("revoke_role update id=%s: %s", assignment_id, exc)
        raise HTTPException(status_code=500, detail="Failed to revoke role assignment.")

    # Expire committee relations if applicable
    building_id = assignment.get("building_id")
    if _AUTH_ENGINE_AVAILABLE and building_id:
        asyncio.create_task(expire_committee_relations(user_id, building_id))

    asyncio.create_task(
        create_audit_log(
            action="rbac.role_revoked",
            user_id=str(current_user.get("_id", "")),
            details={
                "target_user_id": user_id,
                "assignment_id": assignment_id,
                "role_id": assignment.get("role_id"),
            },
        )
    )

    return {"message": "Role assignment revoked."}


# ── GET /auth/rbac/users/{user_id}/relationships ─────────────────────────────


@router.get("/users/{user_id}/relationships", summary="Get relationship tuples for a user")
async def get_relationships_for_user(
        user_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Return active Zanzibar-style relationship tuples where subject is ``user:{user_id}``."""
    _require_own_or_can_manage(user_id, current_user)
    _require_service(_AUTH_ENGINE_AVAILABLE, "AuthorizationEngine")

    try:
        relationships = await get_user_relationships(user_id)
        return {"user_id": user_id, "relationships": relationships}
    except Exception as exc:
        logger.error("get_relationships_for_user user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch relationships.")


# ── POST /auth/rbac/relationships ────────────────────────────────────────────


@router.post("/relationships", status_code=status.HTTP_201_CREATED, summary="Create a relationship tuple")
async def create_relationship(
        body: RelationshipCreateRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Create a new Zanzibar-style relationship triple.

    Restricted to Super Admins — automated workflows should use
    ``sync_user_relationships`` instead.
    """
    _require_super_admin(current_user)
    _require_service(_AUTH_ENGINE_AVAILABLE, "AuthorizationEngine")

    try:
        tuple_id = await add_relationship(
            subject=body.subject,
            relation=body.relation,
            obj=body.object,
            building_id=body.building_id,
            expires_at=body.expires_at,
        )
    except Exception as exc:
        logger.error("create_relationship %s-%s-%s: %s", body.subject, body.relation, body.object, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create relationship: {exc}")

    asyncio.create_task(
        create_audit_log(
            action="rbac.relationship_created",
            user_id=str(current_user.get("_id", "")),
            details={
                "subject": body.subject,
                "relation": body.relation,
                "object": body.object,
                "building_id": body.building_id,
                "tuple_id": tuple_id,
            },
        )
    )

    return {"id": tuple_id, "message": "Relationship created successfully."}


# ── DELETE /auth/rbac/relationships/{tuple_id} ───────────────────────────────


@router.delete("/relationships/{tuple_id}", summary="Remove a relationship tuple")
async def delete_relationship(
        tuple_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Soft-delete a relationship tuple (sets ``is_active=False``). Super Admin only."""
    _require_super_admin(current_user)

    existing = await db.relationship_tuples.find_one({"id": tuple_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Relationship tuple not found.")

    try:
        await db.relationship_tuples.update_one(
            {"id": tuple_id},
            {"$set": {"is_active": False, "removed_at": _utcnow().isoformat()}},
        )
    except Exception as exc:
        logger.error("delete_relationship id=%s: %s", tuple_id, exc)
        raise HTTPException(status_code=500, detail="Failed to remove relationship tuple.")

    asyncio.create_task(
        create_audit_log(
            action="rbac.relationship_removed",
            user_id=str(current_user.get("_id", "")),
            details={"tuple_id": tuple_id},
        )
    )

    return {"message": "Relationship removed."}


# ── POST /auth/rbac/migrate/user/{user_id} ───────────────────────────────────


@router.post("/migrate/user/{user_id}", summary="Migrate a single user to RBAC")
async def migrate_user(
        user_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Migrate a single user to the RBAC system.

    - Creates a ``user_roles`` assignment from the user's existing ``role`` field.
    - Syncs relationship tuples via ``sync_user_relationships``.

    Super Admin only.
    """
    _require_super_admin(current_user)
    _require_service(_PERMISSION_SERVICE_AVAILABLE, "PermissionService")

    user = await db.users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    try:
        await _migrate_single_user(user, assigned_by=str(current_user.get("_id", "")))
    except Exception as exc:
        logger.error("migrate_user user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail=f"Migration failed: {exc}")

    return {"message": "User migrated.", "user_id": user_id}


# ── POST /auth/rbac/migrate/all ──────────────────────────────────────────────


@router.post("/migrate/all", summary="Migrate all users to RBAC (admin only)")
async def migrate_all_users(current_user: dict = Depends(get_current_user)):
    """
    Migrate every user in the system to the RBAC system.

    For each user: creates a ``user_roles`` assignment (if not already present)
    and syncs relationship tuples.  Super Admin only.

    This operation processes all users sequentially to avoid hammering the
    database; for very large installations consider running during off-peak hours.
    """
    _require_super_admin(current_user)
    _require_service(_PERMISSION_SERVICE_AVAILABLE, "PermissionService")

    admin_id = str(current_user.get("_id", ""))
    migrated = 0
    errors = 0

    try:
        users = await db.users.find({}, {"_id": 1, "role": 1}).to_list(length=None)
    except Exception as exc:
        logger.error("migrate_all_users: failed to fetch users: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch users for migration.")

    for user in users:
        try:
            await _migrate_single_user(user, assigned_by=admin_id)
            migrated += 1
        except Exception as exc:
            uid = str(user.get("_id", "?"))
            logger.error("migrate_all_users: user=%s error: %s", uid, exc)
            errors += 1

    logger.info("migrate_all_users: migrated=%d errors=%d", migrated, errors)
    asyncio.create_task(
        create_audit_log(
            action="rbac.bulk_migration",
            user_id=admin_id,
            details={"migrated": migrated, "errors": errors},
        )
    )

    return {"message": "Migration complete.", "migrated": migrated, "errors": errors}


# ── Internal migration helper ─────────────────────────────────────────────────


async def _migrate_single_user(user: dict, assigned_by: str) -> None:
    """
    Create a user_roles assignment from the user's existing ``role`` field
    (only if one does not already exist) and sync relationship tuples.
    """
    user_id = str(user.get("_id", ""))
    role_name = user.get("role", UserRole.GUEST)

    # Only create an assignment if none exist yet for this user
    existing = await db.user_roles.find_one({"user_id": user_id, "is_active": True})
    if not existing:
        # Look up (or create) a matching RoleDefinition
        role_def = await db.roles.find_one({"name": role_name})
        role_id = role_def["id"] if role_def else role_name  # fall back to role name as id

        assignment_doc = {
            "id": _new_id(),
            "user_id": user_id,
            "role_id": role_id,
            "building_id": user.get("building_id"),
            "unit_id": user.get("unit"),
            "start_date": None,
            "end_date": None,
            "is_active": True,
            "assigned_by": assigned_by,
            "notes": "Migrated from legacy role field",
            "created_at": _utcnow().isoformat(),
        }
        await db.user_roles.insert_one(assignment_doc)

    await sync_user_relationships(user_id)
