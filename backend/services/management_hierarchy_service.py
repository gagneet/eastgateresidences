# @featuretrace:management-hierarchy — Unified management entity resolution, access control, and auto-placeholder creation.
# Layer: service
# Data flow: /api/management-hierarchy/* → management_hierarchy_service
#            → core.management_entities + core.scheme_management_assignments (building-scoped)
# Related: backend/routers/management_hierarchy.py
#          backend/services/bi_toggle_service.py
#          backend/alembic/versions/0056_management_entity_model.py
#          backend/alembic/versions/0057_scheme_manager_appointments.py
# Toggle: management_hierarchy_enabled
# Table: core.management_entities, core.scheme_management_assignments, core.scheme_manager_appointments,
#        core.schemes, core.outbox
# Tests: tests/backend/test_management_hierarchy.py
"""Unified management hierarchy service.

Supports four management models:
  1. agency_managed       — Owners Corporation appointed a strata management agency.
  2. self_managed         — OC manages itself; placeholder entity auto-created.
  3. independent_manager  — Sole-trader strata manager; no agency parent.
  4. unknown              — Transitional/legacy; pending-status placeholder.

Entry point: ensure_scheme_management_hierarchy()
  - Idempotent: returns existing assignment if one exists.
  - Creates correct management_entities + scheme_management_assignments rows.
  - Emits audit event.
  - Never creates duplicate active primary assignments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException

logger = logging.getLogger(__name__)

ManagementMode = Literal[
    "agency_managed",
    "self_managed",
    "independent_manager",
    "unknown",
]

_VALID_ENTITY_TYPES = frozenset({
    "agency",
    "self_managed_oc",
    "independent_manager",
    "owners_corporation",
    "platform_demo",
    "unknown",
})

_VALID_ASSIGNMENT_TYPES = frozenset({
    "agency_managed",
    "self_managed",
    "independent_manager",
    "ec_internal_manager",
    "caretaker",
    "contractor",
    "unknown",
})

#: THE list of appointment types. Public and owned here — routers/management_hierarchy.py
#: imports it rather than restating it, and alembic 0103 renders the matching
#: ck_sma_appointment_type CHECK. Three copies of this set existed until 2026-08-28
#: (here, the router, and the 0057 migration), which is the duplicate-concept failure
#: this codebase tracks in docs/architecture/canonical_owners.yaml.
#:
#: Two axes live in one column. The first four say how a strata manager is ENGAGED;
#: the rest say what a team member DOES. The functional values map from
#: models.user.ManagerFunction.APPOINTMENT_TYPE and confer no authority of their own —
#: an appointee is a `strata_manager`, and under UTMA s 58 the owners corporation
#: delegates to the manager, never to a job title. See
#: docs/architecture/strata_management_staff_access_model.md.
VALID_APPOINTMENT_TYPES = frozenset({
    # How the manager is engaged
    "agency_strata_manager",
    "independent_strata_manager",
    "ec_internal_strata_manager",
    "owner_volunteer_manager",
    # What a team member does
    "building_manager",
    "caretaker",
    "levies_manager",
    "insurance_manager",
    "maintenance_manager",
})

#: Deprecated private alias. Kept so any in-flight import keeps working; new code
#: uses VALID_APPOINTMENT_TYPES.
_VALID_APPOINTMENT_TYPES = VALID_APPOINTMENT_TYPES

#: The subset that says HOW a manager is ENGAGED rather than what they do.
#: Declared here rather than in manager_function_service because it is a statement
#: about this vocabulary, and a second module restating these literals is exactly
#: the drift the manager-appointment-type registry entry exists to prevent.
#:
#: Holding one of these means "you are the appointed manager", which switches
#: function scoping off for that user — see services/manager_function_service.py.
ENGAGEMENT_APPOINTMENT_TYPES: frozenset[str] = frozenset({
    "agency_strata_manager",
    "independent_strata_manager",
    "ec_internal_strata_manager",
    "owner_volunteer_manager",
})


# ── Return types ──────────────────────────────────────────────────────────────

@dataclass
class ManagementEntityRecord:
    management_entity_id: str
    entity_type: str
    name: str
    status: str
    is_system_generated: bool
    is_self_managed: bool
    is_independent: bool
    source_agency_id: str | None = None
    tenant_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: ManagementEntityRecord.as_dict
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "management_entity_id": self.management_entity_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "status": self.status,
            "is_system_generated": self.is_system_generated,
            "is_self_managed": self.is_self_managed,
            "is_independent": self.is_independent,
            "source_agency_id": self.source_agency_id,
            "tenant_id": self.tenant_id,
        }


@dataclass
class ManagementAssignmentRecord:
    assignment_id: str
    scheme_id: str
    management_entity_id: str
    assignment_type: str
    status: str
    is_primary: bool
    start_date: date | None = None
    end_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: ManagementAssignmentRecord.as_dict
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "assignment_id": self.assignment_id,
            "scheme_id": self.scheme_id,
            "management_entity_id": self.management_entity_id,
            "assignment_type": self.assignment_type,
            "status": self.status,
            "is_primary": self.is_primary,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
        }


@dataclass
class ManagementHierarchyResult:
    entity: ManagementEntityRecord
    assignment: ManagementAssignmentRecord
    created: bool  # True when rows were newly created this call
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: ManagementHierarchyResult.as_dict
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "entity": self.entity.as_dict(),
            "assignment": self.assignment.as_dict(),
            "created": self.created,
            "warnings": self.warnings,
        }


@dataclass
class AppointmentRecord:
    appointment_id: str
    scheme_id: str
    user_id: str
    management_entity_id: str
    appointment_type: str
    role_title: str
    status: str
    approved_by_ec: bool
    start_date: date | None = None
    end_date: date | None = None
    approval_reference: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: AppointmentRecord.as_dict
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "appointment_id": self.appointment_id,
            "scheme_id": self.scheme_id,
            "user_id": self.user_id,
            "management_entity_id": self.management_entity_id,
            "appointment_type": self.appointment_type,
            "role_title": self.role_title,
            "status": self.status,
            "approved_by_ec": self.approved_by_ec,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "approval_reference": self.approval_reference,
        }


# ── Public API ─────────────────────────────────────────────────────────────────

async def ensure_scheme_management_hierarchy(
    *,
    scheme_id: str,
    building_id: str | None = None,
    mode: ManagementMode,
    actor_user_id: str,
    entity_name_hint: str | None = None,
    source_agency_id: str | None = None,
    is_test_data: bool = False,
) -> ManagementHierarchyResult:
    """Ensure every scheme has exactly one active primary management assignment.

    Idempotent — returns the existing assignment if one already exists.
    Creates correct management_entities + scheme_management_assignments rows
    when none exist.

    Args:
        scheme_id:        UUID of the scheme (core.schemes.scheme_id).
        building_id:      Human-readable building identifier for naming fallback.
        mode:             Management model to apply when creating new rows.
        actor_user_id:    User initiating the call (for audit trail).
        entity_name_hint: Override for the auto-generated entity name.
        source_agency_id: Required when mode='agency_managed'; links to core.agencies.
        is_test_data:     Mark newly created rows as test data.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
    except ImportError as exc:
        raise RuntimeError("PostgreSQL session unavailable") from exc

    warnings: list[str] = []

    async with async_session_context() as session:
        # ── 1. Check for existing active primary assignment ────────────────────
        existing = await _fetch_active_primary_assignment(session, scheme_id)
        if existing:
            entity = await _fetch_entity(session, existing["management_entity_id"])
            return ManagementHierarchyResult(
                entity=entity,
                assignment=ManagementAssignmentRecord(**existing),
                created=False,
                warnings=warnings,
            )

        # ── 2. Resolve or create management entity ────────────────────────────
        entity_record, entity_created = await _resolve_or_create_entity(
            session=session,
            mode=mode,
            scheme_id=scheme_id,
            building_id=building_id,
            source_agency_id=source_agency_id,
            entity_name_hint=entity_name_hint,
            actor_user_id=actor_user_id,
            is_test_data=is_test_data,
        )
        if entity_created:
            warnings.append(
                f"Auto-created management entity '{entity_record.name}' "
                f"(type={entity_record.entity_type}, is_system_generated=True)"
            )

        # ── 3. Create scheme_management_assignments row ────────────────────────
        assignment_type = _mode_to_assignment_type(mode)
        entity_status = "pending" if mode == "unknown" else "active"
        assignment = await _create_assignment(
            session=session,
            scheme_id=scheme_id,
            management_entity_id=entity_record.management_entity_id,
            assignment_type=assignment_type,
            status=entity_status,
            is_test_data=is_test_data,
        )

        # ── 4. Emit audit event ───────────────────────────────────────────────
        await _emit_hierarchy_audit_event(
            session=session,
            scheme_id=scheme_id,
            management_entity_id=entity_record.management_entity_id,
            assignment_id=assignment.assignment_id,
            mode=mode,
            actor_user_id=actor_user_id,
        )

        await session.commit()

    return ManagementHierarchyResult(
        entity=entity_record,
        assignment=assignment,
        created=True,
        warnings=warnings,
    )


async def get_scheme_hierarchy(scheme_id: str) -> dict[str, Any] | None:
    """Return the active primary management assignment + entity for a scheme."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT
                        sma.assignment_id::text,
                        sma.scheme_id::text,
                        sma.assignment_type,
                        sma.status       AS assignment_status,
                        sma.is_primary,
                        sma.start_date,
                        sma.end_date,
                        me.management_entity_id::text,
                        me.entity_type,
                        me.name          AS entity_name,
                        me.status        AS entity_status,
                        me.is_self_managed,
                        me.is_independent,
                        me.is_system_generated,
                        me.source_agency_id::text
                    FROM core.scheme_management_assignments sma
                    JOIN core.management_entities me
                      ON me.management_entity_id = sma.management_entity_id
                    WHERE sma.scheme_id = CAST(:sid AS UUID)
                      AND sma.status IN ('active', 'pending')
                      AND sma.is_primary = TRUE
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    ORDER BY sma.created_at DESC
                    LIMIT 1
                """),
                {"sid": scheme_id},
            )
            r = row.fetchone()
            if not r:
                return None
            return {
                "assignment": {
                    "assignment_id": r[0],
                    "scheme_id": r[1],
                    "assignment_type": r[2],
                    "status": r[3],
                    "is_primary": r[4],
                    "start_date": r[5].isoformat() if r[5] else None,
                    "end_date": r[6].isoformat() if r[6] else None,
                },
                "entity": {
                    "management_entity_id": r[7],
                    "entity_type": r[8],
                    "name": r[9],
                    "status": r[10],
                    "is_self_managed": r[11],
                    "is_independent": r[12],
                    "is_system_generated": r[13],
                    "source_agency_id": r[14],
                },
            }
    except Exception as exc:
        logger.warning("get_scheme_hierarchy(%s) failed: %s", scheme_id, exc)
        return None


async def get_entity_buildings(management_entity_id: str) -> list[str]:
    """Return scheme_number list for all schemes assigned to this management entity."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT s.scheme_number
                    FROM core.scheme_management_assignments sma
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE sma.management_entity_id = CAST(:eid AS UUID)
                      AND sma.status = 'active'
                      AND (s.is_archived IS NOT TRUE)
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    ORDER BY s.scheme_number
                """),
                {"eid": management_entity_id},
            )
            return [r[0] for r in rows.fetchall()]
    except Exception as exc:
        logger.warning("get_entity_buildings(%s) failed: %s", management_entity_id, exc)
        return []


async def get_manager_appointments(
    user_id: str,
    *,
    scheme_id: str | None = None,
) -> list[AppointmentRecord]:
    """Return active appointments for a user, optionally filtered by scheme."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            where_conditions = ["user_id = CAST(:uid AS UUID)", "status = 'active'"]
            params: dict[str, Any] = {"uid": user_id}
            if scheme_id:
                where_conditions.append("scheme_id = CAST(:sid AS UUID)")
                params["sid"] = scheme_id
            where_clause = " AND ".join(where_conditions)
            rows = await session.execute(
                text(f"""
                    SELECT
                        appointment_id::text,
                        scheme_id::text,
                        user_id::text,
                        management_entity_id::text,
                        appointment_type,
                        role_title,
                        status,
                        approved_by_ec,
                        start_date,
                        end_date,
                        approval_reference
                    FROM core.scheme_manager_appointments
                    WHERE {where_clause}
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    ORDER BY created_at DESC
                """),
                params,
            )
            return [
                AppointmentRecord(
                    appointment_id=r[0],
                    scheme_id=r[1],
                    user_id=r[2],
                    management_entity_id=r[3],
                    appointment_type=r[4],
                    role_title=r[5],
                    status=r[6],
                    approved_by_ec=r[7],
                    start_date=r[8],
                    end_date=r[9],
                    approval_reference=r[10],
                )
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("get_manager_appointments(%s) failed: %s", user_id, exc)
        return []


async def create_manager_appointment(
    *,
    scheme_id: str,
    user_id: str,
    management_entity_id: str,
    appointment_type: str,
    role_title: str,
    appointed_by: str,
    start_date: date | None = None,
    approved_by_ec: bool = False,
    approval_reference: str | None = None,
    is_test_data: bool = False,
) -> AppointmentRecord:
    """Create a new manager appointment for a scheme.

    Raises ValueError for invalid appointment_type.
    Raises RuntimeError if a duplicate active appointment already exists.
    """
    if appointment_type not in _VALID_APPOINTMENT_TYPES:
        raise ValueError(
            f"Invalid appointment_type {appointment_type!r}. "
            f"Must be one of: {sorted(_VALID_APPOINTMENT_TYPES)}"
        )

    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            # Check for duplicate
            dup = await session.execute(
                text("""
                    SELECT 1 FROM core.scheme_manager_appointments
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND user_id = CAST(:uid AS UUID)
                      AND appointment_type = :atype
                      AND status = 'active'
                    LIMIT 1
                """),
                {"sid": scheme_id, "uid": user_id, "atype": appointment_type},
            )
            if dup.fetchone():
                raise RuntimeError(
                    f"Active {appointment_type!r} appointment already exists "
                    f"for user={user_id} on scheme={scheme_id}"
                )

            row = await session.execute(
                text("""
                    INSERT INTO core.scheme_manager_appointments (
                        scheme_id, user_id, management_entity_id,
                        appointment_type, role_title, start_date,
                        appointed_by, approved_by_ec, approval_reference,
                        status, is_test_data
                    ) VALUES (
                        CAST(:sid AS UUID), CAST(:uid AS UUID),
                        CAST(:eid AS UUID),
                        :atype, :rtitle, COALESCE(:sdate, CURRENT_DATE),
                        CAST(:apptd_by AS UUID), :approved, :appr_ref,
                        'active', :is_test
                    )
                    RETURNING
                        appointment_id::text, scheme_id::text, user_id::text,
                        management_entity_id::text, appointment_type, role_title,
                        status, approved_by_ec, start_date, end_date, approval_reference
                """),
                {
                    "sid": scheme_id,
                    "uid": user_id,
                    "eid": management_entity_id,
                    "atype": appointment_type,
                    "rtitle": role_title,
                    "sdate": start_date,
                    "apptd_by": appointed_by,
                    "approved": approved_by_ec,
                    "appr_ref": approval_reference,
                    "is_test": is_test_data,
                },
            )
            r = row.fetchone()
            await session.commit()
            return AppointmentRecord(
                appointment_id=r[0],
                scheme_id=r[1],
                user_id=r[2],
                management_entity_id=r[3],
                appointment_type=r[4],
                role_title=r[5],
                status=r[6],
                approved_by_ec=r[7],
                start_date=r[8],
                end_date=r[9],
                approval_reference=r[10],
            )
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        logger.error("create_manager_appointment failed: %s", exc)
        raise


async def resolve_management_entity_for_building(
    building_id: str,
) -> ManagementEntityRecord | None:
    """Resolve the management entity for a building by its scheme_number.

    Returns None when no active primary assignment exists.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT
                        me.management_entity_id::text,
                        me.entity_type,
                        me.name,
                        me.status,
                        me.is_system_generated,
                        me.is_self_managed,
                        me.is_independent,
                        me.source_agency_id::text,
                        me.tenant_id::text
                    FROM core.management_entities me
                    JOIN core.scheme_management_assignments sma
                      ON sma.management_entity_id = me.management_entity_id
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE s.scheme_number = :bid
                      AND sma.status IN ('active', 'pending')
                      AND sma.is_primary = TRUE
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    ORDER BY sma.created_at DESC
                    LIMIT 1
                """),
                {"bid": building_id},
            )
            r = row.fetchone()
            if not r:
                return None
            return ManagementEntityRecord(
                management_entity_id=r[0],
                entity_type=r[1],
                name=r[2],
                status=r[3],
                is_system_generated=r[4],
                is_self_managed=r[5],
                is_independent=r[6],
                source_agency_id=r[7],
                tenant_id=r[8],
            )
    except Exception as exc:
        logger.warning(
            "resolve_management_entity_for_building(%s) failed: %s", building_id, exc
        )
        return None


# ── Access control ────────────────────────────────────────────────────────────

# Roles that may read management hierarchy data
_READ_ROLES = frozenset({"super_admin", "strata_admin", "strata_manager", "ec_member"})
# Roles that may write management hierarchy data
_WRITE_ROLES = frozenset({"super_admin", "strata_admin"})
# Scope labels (mirrors bi_toggle_service constants)
_SCOPE_PLATFORM = "platform"
_SCOPE_AGENCY = "agency"
_SCOPE_MANAGEMENT_ENTITY = "management_entity"
_SCOPE_SCHEME = "scheme"
_SCOPE_BUILDING = "building"

# Sensitive fields returned only to fully-authorised callers
_SENSITIVE_ENTITY_FIELDS = frozenset({"abn", "email", "phone", "legal_name"})


def parse_uuid_or_400(value: str, field_name: str) -> str:
    """Parse a UUID string or raise HTTP 400. Never lets a bad UUID reach the DB."""
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}: must be a valid UUID",
        )


def filter_entity_fields(entity: dict[str, Any], *, full_access: bool) -> dict[str, Any]:
    """Strip sensitive PII fields from entity dicts for restricted callers.

    full_access=True  → all fields (super_admin / agency admin / entity admin / assigned SM)
    full_access=False → limited public-profile fields only (EC members / internal managers)
    """
    if full_access:
        return entity
    return {
        k: v for k, v in entity.items()
        if k not in _SENSITIVE_ENTITY_FIELDS
    }


async def require_management_hierarchy_access(
    *,
    current_user: dict,
    scheme_id: str | None = None,
    management_entity_id: str | None = None,
    target_user_id: str | None = None,
    action: str = "read",
) -> dict[str, Any]:
    """Enforce explicit access control for management hierarchy operations.

    UUIDs are identifiers, not authorisation. Every management hierarchy
    read/write call must pass through this helper; it verifies real
    scheme/entity/agency membership before returning an access context.

    Access rules:
      1. super_admin              — unrestricted platform scope.
      2. strata_admin             — own agency/entity schemes; write permitted.
      3. strata_manager           — schemes where they hold an active appointment.
      4. ec_member (read-only)    — own scheme only; entity access only if entity
                                    is assigned to their own scheme.
      5. owner / tenant / others  — denied.

    Returns an access-context dict on success. Raises HTTPException(403) on denial.
    """
    effective_role = current_user.get("effective_role") or current_user.get("role", "guest")
    user_id = str(current_user.get("_id") or current_user.get("user_id", ""))
    user_building_id = str(current_user.get("building_id") or "")

    def _ctx(scope: str, reason: str) -> dict[str, Any]:
        """Generated function header.

        Function: _ctx
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "allowed": True,
            "scope": scope,
            "reason": reason,
            "user_id": user_id,
            "effective_role": effective_role,
            "scheme_id": scheme_id,
            "management_entity_id": management_entity_id,
        }

    async def _deny(reason: str) -> None:
        """Generated function header.

        Function: _deny
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        await _emit_access_audit_event(
            actor_user_id=user_id,
            effective_role=effective_role,
            scheme_id=scheme_id,
            management_entity_id=management_entity_id,
            target_user_id=target_user_id,
            action=action,
            allowed=False,
            reason=reason,
        )
        raise HTTPException(status_code=403, detail=reason)

    async def _allow(scope: str, reason: str) -> dict[str, Any]:
        """Generated function header.

        Function: _allow
        Path: backend/services/management_hierarchy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        ctx = _ctx(scope, reason)
        await _emit_access_audit_event(
            actor_user_id=user_id,
            effective_role=effective_role,
            scheme_id=scheme_id,
            management_entity_id=management_entity_id,
            target_user_id=target_user_id,
            action=action,
            allowed=True,
            reason=reason,
        )
        return ctx

    # ── 1. Super admin — unrestricted ─────────────────────────────────────────
    if effective_role == "super_admin":
        return await _allow(_SCOPE_PLATFORM, "super_admin: unrestricted platform access")

    # ── 2. Reject non-manager roles ──────────────────────────────────────────
    if effective_role not in _READ_ROLES:
        await _deny(
            f"role={effective_role!r}: management hierarchy access requires "
            "strata_admin, strata_manager, or ec_member"
        )

    # ── 3. Write requires strata_admin ────────────────────────────────────────
    if action == "write" and effective_role not in _WRITE_ROLES:
        await _deny(
            f"role={effective_role!r}: write access to management hierarchy "
            "requires strata_admin or super_admin"
        )

    # ── 4. Scheme-based access ────────────────────────────────────────────────
    if scheme_id:
        scheme_number = await _get_scheme_number(scheme_id)
        if not scheme_number:
            await _deny(f"Scheme {scheme_id} not found or not accessible")

        if effective_role == "ec_member":
            if user_building_id != scheme_number:
                await _deny(
                    f"ec_member: access denied — scheme {scheme_id} "
                    f"(scheme_number={scheme_number!r}) is not your scheme "
                    f"(your building_id={user_building_id!r})"
                )
            return await _allow(
                _SCOPE_SCHEME,
                f"ec_member: read access to own scheme {scheme_number}",
            )

        # strata_admin / strata_manager: appointment or entity/agency membership
        if await _user_has_scheme_appointment(user_id, scheme_id):
            return await _allow(
                _SCOPE_SCHEME,
                f"{effective_role}: active appointment for scheme {scheme_id}",
            )
        entity_id = await _get_scheme_entity_id(scheme_id)
        if entity_id:
            agency_id = await _get_agency_id_for_entity_pg(entity_id)
            if agency_id and await _user_belongs_to_agency_pg(user_id, agency_id):
                return await _allow(
                    _SCOPE_AGENCY,
                    f"{effective_role}: belongs to scheme's management agency {agency_id}",
                )
            if await _user_has_appointment_for_entity_pg(user_id, entity_id):
                return await _allow(
                    _SCOPE_MANAGEMENT_ENTITY,
                    f"{effective_role}: active appointment under scheme's entity {entity_id}",
                )
        await _deny(
            f"{effective_role}: no active appointment or agency membership for scheme {scheme_id}"
        )

    # ── 5. Management-entity-based access (no scheme_id) ─────────────────────
    if management_entity_id and not scheme_id:
        if effective_role == "ec_member":
            # EC member can only access an entity if it is assigned to their scheme
            linked = await _is_entity_linked_to_building(management_entity_id, user_building_id)
            if not linked:
                await _deny(
                    f"ec_member: management entity {management_entity_id} "
                    f"is not assigned to your scheme ({user_building_id!r})"
                )
            return await _allow(
                _SCOPE_BUILDING,
                f"ec_member: entity is assigned to own scheme {user_building_id}",
            )

        # strata_admin / strata_manager
        entity_type = await _get_management_entity_type_pg(management_entity_id)
        if entity_type == "agency":
            agency_id = await _get_agency_id_for_entity_pg(management_entity_id)
            if agency_id and await _user_belongs_to_agency_pg(user_id, agency_id):
                return await _allow(
                    _SCOPE_AGENCY,
                    f"{effective_role}: belongs to entity's agency {agency_id}",
                )
        if await _user_has_appointment_for_entity_pg(user_id, management_entity_id):
            return await _allow(
                _SCOPE_MANAGEMENT_ENTITY,
                f"{effective_role}: active appointment for entity {management_entity_id}",
            )
        await _deny(
            f"{effective_role}: no access to management entity {management_entity_id}"
        )

    # ── 6. Target-user-based access (GET /managers/{user_id}/buildings) ──────
    if target_user_id and not scheme_id and not management_entity_id:
        if user_id == target_user_id:
            return await _allow(_SCOPE_BUILDING, "self-access: querying own managed buildings")
        if effective_role == "strata_admin":
            return await _allow(
                _SCOPE_AGENCY,
                f"strata_admin: querying managed buildings for user {target_user_id}",
            )
        await _deny(
            f"{effective_role}: can only query your own managed buildings; "
            f"requested user={target_user_id}"
        )

    # ── 7. No resource context — deny (programmer error; never reach here in normal flow) ──
    await _deny(f"{effective_role}: access denied (no resource context provided)")


# ── Private access helpers ────────────────────────────────────────────────────

async def _get_scheme_number(scheme_id: str) -> str | None:
    """Resolve scheme_number from a scheme UUID."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT scheme_number FROM core.schemes
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND is_archived IS NOT TRUE
                    LIMIT 1
                """),
                {"sid": scheme_id},
            )
            r = row.fetchone()
            return r[0] if r else None
    except Exception as exc:
        logger.debug("_get_scheme_number(%s) error: %s", scheme_id, exc)
        return None


async def _get_scheme_entity_id(scheme_id: str) -> str | None:
    """Return primary management_entity_id for a scheme."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT management_entity_id::text
                    FROM core.scheme_management_assignments
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND status IN ('active', 'pending')
                      AND is_primary = TRUE
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"sid": scheme_id},
            )
            r = row.fetchone()
            return r[0] if r else None
    except Exception as exc:
        logger.debug("_get_scheme_entity_id(%s) error: %s", scheme_id, exc)
        return None


async def _is_entity_linked_to_building(
    management_entity_id: str, building_id: str
) -> bool:
    """True if this management entity has an active assignment for the given building (scheme_number)."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1
                    FROM core.scheme_management_assignments sma
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE sma.management_entity_id = CAST(:eid AS UUID)
                      AND s.scheme_number = :bid
                      AND sma.status IN ('active', 'pending')
                      AND (s.is_archived IS NOT TRUE)
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    LIMIT 1
                """),
                {"eid": management_entity_id, "bid": building_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug(
            "_is_entity_linked_to_building(%s, %s) error: %s",
            management_entity_id, building_id, exc,
        )
        return False


async def _user_has_scheme_appointment(user_id: str, scheme_id: str) -> bool:
    """True if the user has an active appointment for a specific scheme."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1 FROM core.scheme_manager_appointments
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND user_id = CAST(:uid AS UUID)
                      AND status = 'active'
                      AND approved_by_ec = TRUE
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    LIMIT 1
                """),
                {"sid": scheme_id, "uid": user_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug(
            "_user_has_scheme_appointment(%s, %s) error: %s", user_id, scheme_id, exc
        )
        return False


async def _user_belongs_to_agency_pg(user_id: str, agency_id: str) -> bool:
    """True if user is an active member of the specified agency."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1 FROM core.agency_memberships
                    WHERE agency_id = CAST(:aid AS UUID)
                      AND user_id = CAST(:uid AS UUID)
                      AND status = 'active'
                    LIMIT 1
                """),
                {"aid": agency_id, "uid": user_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug(
            "_user_belongs_to_agency_pg(%s, %s) error: %s", user_id, agency_id, exc
        )
        return False


async def _get_agency_id_for_entity_pg(management_entity_id: str) -> str | None:
    """Return source_agency_id for a management entity."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT source_agency_id::text FROM core.management_entities
                    WHERE management_entity_id = CAST(:eid AS UUID)
                    LIMIT 1
                """),
                {"eid": management_entity_id},
            )
            r = row.fetchone()
            return r[0] if r else None
    except Exception as exc:
        logger.debug(
            "_get_agency_id_for_entity_pg(%s) error: %s", management_entity_id, exc
        )
        return None


async def _user_has_appointment_for_entity_pg(
    user_id: str, management_entity_id: str
) -> bool:
    """True if user has an active EC-approved appointment under this management entity."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1 FROM core.scheme_manager_appointments
                    WHERE management_entity_id = CAST(:eid AS UUID)
                      AND user_id = CAST(:uid AS UUID)
                      AND status = 'active'
                      AND approved_by_ec = TRUE
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    LIMIT 1
                """),
                {"eid": management_entity_id, "uid": user_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug(
            "_user_has_appointment_for_entity_pg(%s, %s) error: %s",
            user_id, management_entity_id, exc,
        )
        return False


async def _get_management_entity_type_pg(management_entity_id: str) -> str:
    """Return entity_type for a management entity (safe default = 'unknown')."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT entity_type FROM core.management_entities
                    WHERE management_entity_id = CAST(:eid AS UUID)
                    LIMIT 1
                """),
                {"eid": management_entity_id},
            )
            r = row.fetchone()
            return r[0] if r else "unknown"
    except Exception as exc:
        logger.debug(
            "_get_management_entity_type_pg(%s) error: %s", management_entity_id, exc
        )
        return "unknown"


async def _emit_access_audit_event(
    *,
    actor_user_id: str,
    effective_role: str,
    scheme_id: str | None,
    management_entity_id: str | None,
    target_user_id: str | None,
    action: str,
    allowed: bool,
    reason: str,
) -> None:
    """Write a management_hierarchy access audit event to core.outbox.

    Failure must never block the calling request.
    """
    event_type = (
        "management_hierarchy.access_allowed"
        if allowed
        else "management_hierarchy.access_denied"
    )
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            await session.execute(
                text("""
                    INSERT INTO core.outbox (
                        event_type, aggregate_id, aggregate_type,
                        payload, created_at
                    ) VALUES (
                        :etype,
                        COALESCE(CAST(:sid AS UUID), gen_random_uuid()),
                        'management_hierarchy',
                        jsonb_build_object(
                            'actor_user_id', :uid,
                            'effective_role', :role,
                            'scheme_id', :sid,
                            'management_entity_id', :eid,
                            'target_user_id', :tuid,
                            'action', :action,
                            'allowed', :allowed,
                            'reason', :reason
                        ),
                        now()
                    )
                """),
                {
                    "etype": event_type,
                    "sid": scheme_id,
                    "uid": actor_user_id,
                    "role": effective_role,
                    "eid": management_entity_id,
                    "tuid": target_user_id,
                    "action": action,
                    "allowed": allowed,
                    "reason": reason,
                },
            )
            await session.commit()
    except Exception as exc:
        logger.warning("_emit_access_audit_event failed: %s", exc)


# ── Private helpers ────────────────────────────────────────────────────────────

async def _fetch_active_primary_assignment(
    session: Any, scheme_id: str
) -> dict[str, Any] | None:
    """Generated function header.

    Function: _fetch_active_primary_assignment
    Path: backend/services/management_hierarchy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    row = await session.execute(
        text("""
            SELECT
                assignment_id::text,
                scheme_id::text,
                management_entity_id::text,
                assignment_type,
                status,
                is_primary,
                start_date,
                end_date
            FROM core.scheme_management_assignments
            WHERE scheme_id = CAST(:sid AS UUID)
              AND status IN ('active', 'pending')
              AND is_primary = TRUE
              AND COALESCE(is_test_data, FALSE) = FALSE
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"sid": scheme_id},
    )
    r = row.fetchone()
    if not r:
        return None
    return {
        "assignment_id": r[0],
        "scheme_id": r[1],
        "management_entity_id": r[2],
        "assignment_type": r[3],
        "status": r[4],
        "is_primary": r[5],
        "start_date": r[6],
        "end_date": r[7],
    }


async def _fetch_entity(
    session: Any, management_entity_id: str
) -> ManagementEntityRecord:
    """Generated function header.

    Function: _fetch_entity
    Path: backend/services/management_hierarchy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    row = await session.execute(
        text("""
            SELECT
                management_entity_id::text, entity_type, name, status,
                is_system_generated, is_self_managed, is_independent,
                source_agency_id::text, tenant_id::text
            FROM core.management_entities
            WHERE management_entity_id = CAST(:eid AS UUID)
        """),
        {"eid": management_entity_id},
    )
    r = row.fetchone()
    return ManagementEntityRecord(
        management_entity_id=r[0],
        entity_type=r[1],
        name=r[2],
        status=r[3],
        is_system_generated=r[4],
        is_self_managed=r[5],
        is_independent=r[6],
        source_agency_id=r[7],
        tenant_id=r[8],
    )


async def _resolve_or_create_entity(
    *,
    session: Any,
    mode: ManagementMode,
    scheme_id: str,
    building_id: str | None,
    source_agency_id: str | None,
    entity_name_hint: str | None,
    actor_user_id: str,
    is_test_data: bool,
) -> tuple[ManagementEntityRecord, bool]:
    """Return (entity, was_created).

    For agency_managed mode: looks up existing management_entity linked to
    source_agency_id; creates one if absent (syncing from core.agencies).
    For all other modes: always creates a new system-generated entity.
    """
    from sqlalchemy import text

    label = entity_name_hint or building_id or scheme_id

    if mode == "agency_managed":
        if not source_agency_id:
            raise ValueError("source_agency_id is required for agency_managed mode")
        # Check for existing entity linked to this agency
        row = await session.execute(
            text("""
                SELECT management_entity_id::text, entity_type, name, status,
                       is_system_generated, is_self_managed, is_independent,
                       source_agency_id::text, tenant_id::text
                FROM core.management_entities
                WHERE source_agency_id = CAST(:aid AS UUID)
                  AND entity_type = 'agency'
                LIMIT 1
            """),
            {"aid": source_agency_id},
        )
        r = row.fetchone()
        if r:
            return ManagementEntityRecord(
                management_entity_id=r[0], entity_type=r[1], name=r[2],
                status=r[3], is_system_generated=r[4], is_self_managed=r[5],
                is_independent=r[6], source_agency_id=r[7], tenant_id=r[8],
            ), False

        # Fetch agency name from core.agencies
        agency_row = await session.execute(
            text("SELECT name FROM core.agencies WHERE agency_id = CAST(:aid AS UUID)"),
            {"aid": source_agency_id},
        )
        ar = agency_row.fetchone()
        entity_name = (ar[0] if ar else None) or f"Agency {source_agency_id[:8]}"
        entity_type = "agency"
        is_self_managed = False
        is_independent = False

    elif mode == "self_managed":
        entity_name = f"{label} Self-Managed OC"
        entity_type = "self_managed_oc"
        is_self_managed = True
        is_independent = False
        source_agency_id = None

    elif mode == "independent_manager":
        entity_name = f"{label} Independent Strata Manager"
        entity_type = "independent_manager"
        is_self_managed = False
        is_independent = True
        source_agency_id = None

    else:  # unknown
        entity_name = f"{label} Management Pending"
        entity_type = "unknown"
        is_self_managed = False
        is_independent = False
        source_agency_id = None

    new_row = await session.execute(
        text("""
            INSERT INTO core.management_entities (
                entity_type, name, status,
                is_system_generated, is_self_managed, is_independent,
                source, source_agency_id, is_test_data
            ) VALUES (
                :etype,
                :ename,
                CASE WHEN :etype = 'unknown' THEN 'pending' ELSE 'active' END,
                TRUE, :is_sm, :is_ind,
                'auto',
                CAST(:said AS UUID),
                :is_test
            )
            RETURNING
                management_entity_id::text, entity_type, name, status,
                is_system_generated, is_self_managed, is_independent,
                source_agency_id::text, tenant_id::text
        """),
        {
            "etype": entity_type,
            "ename": entity_name,
            "is_sm": is_self_managed,
            "is_ind": is_independent,
            "said": source_agency_id,
            "is_test": is_test_data,
        },
    )
    nr = new_row.fetchone()
    return ManagementEntityRecord(
        management_entity_id=nr[0], entity_type=nr[1], name=nr[2],
        status=nr[3], is_system_generated=nr[4], is_self_managed=nr[5],
        is_independent=nr[6], source_agency_id=nr[7], tenant_id=nr[8],
    ), True


async def _create_assignment(
    *,
    session: Any,
    scheme_id: str,
    management_entity_id: str,
    assignment_type: str,
    status: str,
    is_test_data: bool,
) -> ManagementAssignmentRecord:
    """Generated function header.

    Function: _create_assignment
    Path: backend/services/management_hierarchy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    row = await session.execute(
        text("""
            INSERT INTO core.scheme_management_assignments (
                scheme_id, management_entity_id, assignment_type,
                status, is_primary, is_test_data
            ) VALUES (
                CAST(:sid AS UUID),
                CAST(:eid AS UUID),
                :atype, :status, TRUE, :is_test
            )
            RETURNING
                assignment_id::text, scheme_id::text, management_entity_id::text,
                assignment_type, status, is_primary, start_date, end_date
        """),
        {
            "sid": scheme_id,
            "eid": management_entity_id,
            "atype": assignment_type,
            "status": status,
            "is_test": is_test_data,
        },
    )
    r = row.fetchone()
    return ManagementAssignmentRecord(
        assignment_id=r[0],
        scheme_id=r[1],
        management_entity_id=r[2],
        assignment_type=r[3],
        status=r[4],
        is_primary=r[5],
        start_date=r[6],
        end_date=r[7],
    )


async def _emit_hierarchy_audit_event(
    *,
    session: Any,
    scheme_id: str,
    management_entity_id: str,
    assignment_id: str,
    mode: str,
    actor_user_id: str,
) -> None:
    """Generated function header.

    Function: _emit_hierarchy_audit_event
    Path: backend/services/management_hierarchy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text

        await session.execute(
            text("""
                INSERT INTO core.outbox (
                    event_type, aggregate_id, aggregate_type,
                    payload, created_at
                ) VALUES (
                    'management_hierarchy.created',
                    CAST(:sid AS UUID),
                    'scheme',
                    jsonb_build_object(
                        'scheme_id', :sid,
                        'management_entity_id', :eid,
                        'assignment_id', :aid,
                        'mode', :mode,
                        'actor_user_id', :uid
                    ),
                    now()
                )
            """),
            {
                "sid": scheme_id,
                "eid": management_entity_id,
                "aid": assignment_id,
                "mode": mode,
                "uid": actor_user_id,
            },
        )
    except Exception as exc:
        # Audit failure must not block the main operation
        logger.warning("_emit_hierarchy_audit_event failed: %s", exc)


def _mode_to_assignment_type(mode: ManagementMode) -> str:
    """Generated function header.

    Function: _mode_to_assignment_type
    Path: backend/services/management_hierarchy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "agency_managed": "agency_managed",
        "self_managed": "self_managed",
        "independent_manager": "independent_manager",
        "unknown": "unknown",
    }[mode]
