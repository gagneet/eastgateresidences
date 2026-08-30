# @featuretrace:management-hierarchy — Management hierarchy CRUD and ensure endpoints.
# Layer: router
# Data flow: frontend/BuildingSettings + onboarding →
#            POST /management-hierarchy/schemes/{id}/ensure →
#            management_hierarchy_service → core.management_entities (building-scoped)
# Related: backend/services/management_hierarchy_service.py
#          backend/services/bi_toggle_service.py
#          backend/alembic/versions/0056_management_entity_model.py
#          backend/alembic/versions/0057_scheme_manager_appointments.py
# Toggle: management_hierarchy_enabled
# Table: core.management_entities, core.scheme_management_assignments,
#        core.scheme_manager_appointments
# Tests: tests/backend/test_management_hierarchy.py
"""Management Hierarchy router.

Security: UUIDs are identifiers, not authorisation. Every endpoint calls
require_management_hierarchy_access() which enforces explicit scheme/entity/agency
membership. UUID knowledge alone is insufficient to access any endpoint.

Endpoints
---------
POST   /management-hierarchy/schemes/{scheme_id}/ensure
    Idempotent — creates placeholder hierarchy if none exists.
    Requires: strata_admin or super_admin + scheme write access.

GET    /management-hierarchy/schemes/{scheme_id}
    Return active primary management assignment + entity for a scheme.
    Requires: scheme read access (own-scheme EC, assigned manager, or SA/admin).

GET    /management-hierarchy/entities/{management_entity_id}
    Return management entity details (sensitive fields filtered by access scope).
    Requires: entity is linked to caller's scheme, or caller has entity/agency membership.

GET    /management-hierarchy/entities/{management_entity_id}/buildings
    Return scheme_number list for buildings assigned to this entity.
    Requires: entity read access (same as above).

GET    /management-hierarchy/managers/{user_id}/buildings
    Return scheme_number list for buildings where user has an active appointment.
    Requires: self-access, or strata_admin/super_admin.

POST   /management-hierarchy/schemes/{scheme_id}/appointments
    Create a manager appointment (EC-approved or pending approval).
    Requires: strata_admin or super_admin + scheme write access.

GET    /management-hierarchy/schemes/{scheme_id}/appointments
    List active appointments for a scheme.
    Requires: scheme read access.

Field filtering
---------------
Full fields (abn, email, phone, legal_name) returned only to:
  super_admin, agency admin, management entity admin, assigned strata manager
  (access scope = platform | agency | management_entity)

Limited fields returned to:
  EC members and other scheme-scoped callers (scope = scheme | building)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from utils.auth import get_approved_user
from models.user import UserRole
from services.management_hierarchy_service import VALID_APPOINTMENT_TYPES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/management-hierarchy", tags=["Management Hierarchy"])

# ── Role sets ─────────────────────────────────────────────────────────────────

_ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN}

_VALID_MODES = {"agency_managed", "self_managed", "independent_manager", "unknown"}
# Imported, not restated: services.management_hierarchy_service owns this set, and
# a second copy here is how the router and the service drift apart. This one was
# already a value behind on 2026-08-28.
_VALID_APPOINTMENT_TYPES = VALID_APPOINTMENT_TYPES

# Scopes that warrant full field disclosure (includes sensitive PII like ABN/email/phone)
_FULL_ACCESS_SCOPES = {"platform", "agency", "management_entity"}


# ── Request / Response models ─────────────────────────────────────────────────

class EnsureHierarchyRequest(BaseModel):
    mode: str = Field(..., description="agency_managed|self_managed|independent_manager|unknown")
    entity_name_hint: Optional[str] = Field(None, max_length=200)
    source_agency_id: Optional[str] = Field(
        None, description="Required when mode=agency_managed"
    )
    building_id: Optional[str] = Field(None, description="Human-readable building identifier")
    is_test_data: bool = False

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        """Generated function header.

        Function: EnsureHierarchyRequest._check_mode
        Path: backend/routers/management_hierarchy.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v not in _VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}")
        return v


class CreateAppointmentRequest(BaseModel):
    user_id: str
    management_entity_id: str
    appointment_type: str
    role_title: str = Field(..., max_length=100)
    start_date: Optional[date] = None
    approved_by_ec: bool = False
    approval_reference: Optional[str] = Field(None, max_length=200)

    @field_validator("appointment_type")
    @classmethod
    def _check_atype(cls, v: str) -> str:
        """Generated function header.

        Function: CreateAppointmentRequest._check_atype
        Path: backend/routers/management_hierarchy.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v not in _VALID_APPOINTMENT_TYPES:
            raise ValueError(
                f"appointment_type must be one of {sorted(_VALID_APPOINTMENT_TYPES)}"
            )
        return v


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/schemes/{scheme_id}/ensure")
async def ensure_hierarchy(
    scheme_id: str,
    body: EnsureHierarchyRequest,
    current_user: dict = Depends(get_approved_user),
):
    """Idempotently ensure a scheme has an active primary management assignment.

    Returns the existing assignment without changes if one already exists.
    Creates the appropriate management_entities + scheme_management_assignments
    rows when none exist.

    Requires strata_admin or super_admin + explicit scheme write access.
    """
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
        ensure_scheme_management_hierarchy,
        filter_entity_fields,
    )

    scheme_id = parse_uuid_or_400(scheme_id, "scheme_id")
    if body.source_agency_id:
        body.source_agency_id = parse_uuid_or_400(body.source_agency_id, "source_agency_id")

    # Enforce: must be strata_admin/super_admin (write gate)
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="strata_admin or super_admin required")

    access_ctx = await require_management_hierarchy_access(
        current_user=current_user,
        scheme_id=scheme_id,
        action="write",
    )

    try:
        result = await ensure_scheme_management_hierarchy(
            scheme_id=scheme_id,
            building_id=body.building_id,
            mode=body.mode,  # type: ignore[arg-type]
            actor_user_id=str(current_user.get("_id") or current_user.get("user_id", "")),
            entity_name_hint=body.entity_name_hint,
            source_agency_id=body.source_agency_id,
            is_test_data=body.is_test_data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    full_access = access_ctx["scope"] in _FULL_ACCESS_SCOPES
    return {
        "scheme_id": scheme_id,
        "created": result.created,
        "entity": filter_entity_fields(result.entity.as_dict(), full_access=full_access),
        "assignment": result.assignment.as_dict(),
        "warnings": result.warnings,
    }


@router.delete("/test-cleanup")
async def cleanup_test_hierarchy_rows(
    prefix: str = Query("", max_length=100),
    current_user: dict = Depends(get_approved_user),
):
    """Delete management hierarchy rows created by test/perf runs only."""
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="strata_admin or super_admin required")

    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context

        async with async_session_context() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', false)")
            )
            appointments = await session.execute(text("""
                DELETE FROM core.scheme_manager_appointments
                WHERE is_test_data = TRUE
                   OR (:prefix <> '' AND approval_reference LIKE :prefix_like)
                RETURNING appointment_id
            """), {"prefix": prefix, "prefix_like": f"{prefix}%"})
            assignments = await session.execute(text("""
                DELETE FROM core.scheme_management_assignments
                WHERE is_test_data = TRUE
                RETURNING assignment_id
            """))
            entities = await session.execute(text("""
                DELETE FROM core.management_entities
                WHERE is_test_data = TRUE
                RETURNING management_entity_id
            """))
            await session.commit()
    except Exception as exc:
        logger.error("cleanup_test_hierarchy_rows failed: %s", exc)
        raise HTTPException(status_code=500, detail="Database error")

    return {
        "deleted": {
            "scheme_manager_appointments": len(appointments.fetchall()),
            "scheme_management_assignments": len(assignments.fetchall()),
            "management_entities": len(entities.fetchall()),
        }
    }


@router.get("/schemes/{scheme_id}")
async def get_scheme_hierarchy(
    scheme_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Return the active primary management assignment + entity for a scheme.

    Sensitive entity fields (ABN, email, phone, legal_name) are filtered
    for EC-member callers (scope = scheme).
    """
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
        get_scheme_hierarchy,
        filter_entity_fields,
    )

    scheme_id = parse_uuid_or_400(scheme_id, "scheme_id")
    access_ctx = await require_management_hierarchy_access(
        current_user=current_user,
        scheme_id=scheme_id,
        action="read",
    )

    data = await get_scheme_hierarchy(scheme_id)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"No active management assignment found for scheme {scheme_id}",
        )

    full_access = access_ctx["scope"] in _FULL_ACCESS_SCOPES
    data["entity"] = filter_entity_fields(data["entity"], full_access=full_access)
    return {"scheme_id": scheme_id, **data}


@router.get("/entities/{management_entity_id}")
async def get_management_entity(
    management_entity_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Return management entity details.

    Sensitive fields (ABN, email, phone, legal_name) are filtered for
    EC-member callers whose access scope is scheme/building only.
    Full fields are returned to super_admin, agency admin, entity admin,
    and assigned strata managers.
    """
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
        filter_entity_fields,
    )

    management_entity_id = parse_uuid_or_400(management_entity_id, "management_entity_id")
    access_ctx = await require_management_hierarchy_access(
        current_user=current_user,
        management_entity_id=management_entity_id,
        action="read",
    )

    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context

        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT
                        management_entity_id::text, tenant_id::text, entity_type,
                        name, legal_name, abn, email, phone, status,
                        is_system_generated, is_self_managed, is_independent,
                        source, source_agency_id::text,
                        created_at, updated_at, archived_at
                    FROM core.management_entities
                    WHERE management_entity_id = CAST(:eid AS UUID)
                """),
                {"eid": management_entity_id},
            )
            r = row.fetchone()
    except Exception as exc:
        logger.error("get_management_entity(%s) failed: %s", management_entity_id, exc)
        raise HTTPException(status_code=500, detail="Database error")

    if not r:
        raise HTTPException(
            status_code=404,
            detail=f"Management entity {management_entity_id} not found",
        )

    entity = {
        "management_entity_id": r[0],
        "tenant_id": r[1],
        "entity_type": r[2],
        "name": r[3],
        "legal_name": r[4],
        "abn": r[5],
        "email": r[6],
        "phone": r[7],
        "status": r[8],
        "is_system_generated": r[9],
        "is_self_managed": r[10],
        "is_independent": r[11],
        "source": r[12],
        "source_agency_id": r[13],
        "created_at": r[14].isoformat() if r[14] else None,
        "updated_at": r[15].isoformat() if r[15] else None,
        "archived_at": r[16].isoformat() if r[16] else None,
    }

    full_access = access_ctx["scope"] in _FULL_ACCESS_SCOPES
    return filter_entity_fields(entity, full_access=full_access)


@router.get("/entities/{management_entity_id}/buildings")
async def get_entity_buildings(
    management_entity_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Return scheme_number list for buildings assigned to this management entity."""
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
        get_entity_buildings,
    )

    management_entity_id = parse_uuid_or_400(management_entity_id, "management_entity_id")
    await require_management_hierarchy_access(
        current_user=current_user,
        management_entity_id=management_entity_id,
        action="read",
    )

    building_ids = await get_entity_buildings(management_entity_id)
    return {
        "management_entity_id": management_entity_id,
        "building_ids": building_ids,
        "count": len(building_ids),
    }


@router.get("/managers/{user_id}/buildings")
async def get_manager_buildings(
    user_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Return buildings where this user has an active manager appointment.

    Self-access is always permitted. strata_admin and super_admin may query
    any manager. Other roles are denied.
    """
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
    )

    user_id = parse_uuid_or_400(user_id, "user_id")
    caller_id = str(current_user.get("_id") or current_user.get("user_id", ""))

    await require_management_hierarchy_access(
        current_user=current_user,
        target_user_id=user_id,
        action="read",
    )

    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context

        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT DISTINCT s.scheme_number
                    FROM core.scheme_manager_appointments sma
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE sma.user_id = CAST(:uid AS UUID)
                      AND sma.status = 'active'
                      AND (s.is_archived IS NOT TRUE)
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    ORDER BY s.scheme_number
                """),
                {"uid": user_id},
            )
            building_ids = [r[0] for r in rows.fetchall()]
    except Exception as exc:
        logger.error("get_manager_buildings(%s) failed: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Database error")

    return {
        "user_id": user_id,
        "building_ids": building_ids,
        "count": len(building_ids),
    }


@router.post("/schemes/{scheme_id}/appointments")
async def create_appointment(
    scheme_id: str,
    body: CreateAppointmentRequest,
    current_user: dict = Depends(get_approved_user),
):
    """Create a manager appointment for a scheme.

    EC-internal and volunteer appointments require approved_by_ec=True
    before they grant manager-level access.

    Requires strata_admin or super_admin + explicit scheme write access.
    """
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
        create_manager_appointment,
    )

    scheme_id = parse_uuid_or_400(scheme_id, "scheme_id")
    body.user_id = parse_uuid_or_400(body.user_id, "user_id")
    body.management_entity_id = parse_uuid_or_400(
        body.management_entity_id, "management_entity_id"
    )

    # Enforce admin gate first (fast path before DB queries)
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="strata_admin or super_admin required")

    await require_management_hierarchy_access(
        current_user=current_user,
        scheme_id=scheme_id,
        action="write",
    )

    appointed_by = str(current_user.get("_id") or current_user.get("user_id", ""))

    try:
        record = await create_manager_appointment(
            scheme_id=scheme_id,
            user_id=body.user_id,
            management_entity_id=body.management_entity_id,
            appointment_type=body.appointment_type,
            role_title=body.role_title,
            appointed_by=appointed_by,
            start_date=body.start_date,
            approved_by_ec=body.approved_by_ec,
            approval_reference=body.approval_reference,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # An appointment changes what this user may reach, and the scope resolver caches
    # for 60s. Without this the person who just made the change watches the old
    # access persist for a minute and reasonably concludes it did not work.
    # Clear ALL cached scopes, not just this user's. The cache is keyed by whatever
    # identity the manager's own session carried, which for a legacy Mongo-token
    # session is not the core.users id this appointment names (footgun #24), so a
    # targeted drop can miss the entry it is meant to remove. The cache holds one
    # small tuple per signed-in manager and appointments change rarely.
    from services.manager_function_service import invalidate_manager_scope_cache
    invalidate_manager_scope_cache()

    return record.as_dict()


@router.get("/schemes/{scheme_id}/appointments")
async def list_scheme_appointments(
    scheme_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """List active manager appointments for a scheme.

    Requires explicit read access to the scheme (own-scheme EC, assigned
    manager, or agency/super admin).
    """
    from services.management_hierarchy_service import (
        parse_uuid_or_400,
        require_management_hierarchy_access,
    )

    scheme_id = parse_uuid_or_400(scheme_id, "scheme_id")
    await require_management_hierarchy_access(
        current_user=current_user,
        scheme_id=scheme_id,
        action="read",
    )

    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context

        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT
                        appointment_id::text, scheme_id::text, user_id::text,
                        management_entity_id::text, appointment_type, role_title,
                        status, approved_by_ec, start_date, end_date,
                        approval_reference, appointed_by::text
                    FROM core.scheme_manager_appointments
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND status = 'active'
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    ORDER BY created_at DESC
                """),
                {"sid": scheme_id},
            )
            appointments = [
                {
                    "appointment_id": r[0],
                    "scheme_id": r[1],
                    "user_id": r[2],
                    "management_entity_id": r[3],
                    "appointment_type": r[4],
                    "role_title": r[5],
                    "status": r[6],
                    "approved_by_ec": r[7],
                    "start_date": r[8].isoformat() if r[8] else None,
                    "end_date": r[9].isoformat() if r[9] else None,
                    "approval_reference": r[10],
                    "appointed_by": r[11],
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.error("list_scheme_appointments(%s) failed: %s", scheme_id, exc)
        raise HTTPException(status_code=500, detail="Database error")

    return {"scheme_id": scheme_id, "appointments": appointments, "count": len(appointments)}


class FunctionScopingRequest(BaseModel):
    enabled: bool = Field(
        ...,
        description=(
            "TRUE narrows every strata_manager holding a FUNCTIONAL appointment for "
            "this entity's schemes to that function's surface. FALSE (default) "
            "restores unscoped behaviour."
        ),
    )


@router.put("/entities/{entity_id}/function-scoping")
async def set_function_scoping(
    entity_id: str,
    body: FunctionScopingRequest,
    current_user: dict = Depends(get_approved_user),
):
    """Turn manager function scoping on or off for one management entity.

    This is the opt-in switch described in
    docs/architecture/strata_management_staff_access_model.md. It is per AGENCY
    rather than per building on purpose: an agency adopts a division of labour for
    the whole of its book, and a per-building version would let one scheme narrow
    while its siblings did not — an inconsistency no agency would ask for and one
    more way for a team to meet a confusing 403.

    Enabling it can only ever REMOVE access, and only from a strata_manager who
    holds a functional appointment. Nobody gains anything.
    """
    from services.management_hierarchy_service import parse_uuid_or_400
    from services.manager_function_service import invalidate_manager_scope_cache

    entity_id = parse_uuid_or_400(entity_id, "entity_id")

    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="strata_admin or super_admin required")

    from sqlalchemy import text
    from db_postgres.session import async_session_context

    async with async_session_context() as session:
        result = await session.execute(
            text("""
                UPDATE core.management_entities
                   SET function_scoping_enabled = :enabled
                 WHERE management_entity_id = CAST(:eid AS UUID)
             RETURNING management_entity_id::text, name, function_scoping_enabled
            """),
            {"eid": entity_id, "enabled": bool(body.enabled)},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Management entity not found")
        await session.commit()

    # Every manager under this entity may have just changed scope, and the cache is
    # keyed by user, so there is no narrower key to drop than all of them.
    invalidate_manager_scope_cache()

    logger.info(
        "function scoping %s for management entity %s by %s",
        "ENABLED" if body.enabled else "disabled", entity_id,
        current_user.get("email"),
    )
    return {
        "management_entity_id": row[0],
        "name": row[1],
        "function_scoping_enabled": bool(row[2]),
    }
