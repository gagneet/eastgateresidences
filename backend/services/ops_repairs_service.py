"""Postgres-backed repairs, vendor, and recurring maintenance workflows."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text

from db_postgres.session import async_session_context, set_tenant
from models.ops_repairs import (
    RecurringTaskTemplateCreate,
    ServiceProviderCreate,
    ServiceProviderUpdate,
    ServiceRequestCreate,
    VendorAssignmentCreate,
)
from models.user import UserRole
from services.ops_case_service import (
    _get_case_for_access,
    _insert_audit_event,
    _insert_case_event,
    _insert_status_history,
    _parse_uuid,
    _resolve_context,
    _row_to_dict,
)

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.EC_MEMBER,
}


def _assert_manager(role: str, detail: str = "Manager access required.") -> None:
    """Generated function header.

    Function: _assert_manager
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _string(value: Any) -> str | None:
    """Generated function header.

    Function: _string
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value) if value is not None else None


def _row_map(row) -> dict[str, Any]:
    """Generated function header.

    Function: _row_map
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _valid_case_assignment_transition(current_status: str) -> str:
    # Keep shared ops.cases transitions inside the migration guardrail. Vendor
    # assignment is a valid move to "assigned", not directly to "scheduled".
    """Generated function header.

    Function: _valid_case_assignment_transition
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if current_status in {"new", "triaged", "waiting_for_info"}:
        return "assigned"
    return current_status


def _normalize_vendor(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalize_vendor
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_map(row)
    return {
        "vendor_id": _string(data.get("vendor_id")),
        "party_id": _string(data.get("party_id")),
        "legal_name": data.get("legal_name") or "",
        "preferred_name": data.get("preferred_name"),
        "primary_email": data.get("primary_email"),
        "primary_mobile": data.get("primary_mobile"),
        "abn": data.get("abn"),
        "trade_categories": list(data.get("trade_categories") or []),
        "abn_status": data.get("abn_status"),
        "gst_registered": bool(data.get("gst_registered", False)),
        "insurance_expiry": data.get("insurance_expiry"),
        "licence_expiry": data.get("licence_expiry"),
        "preferred_payment_method": data.get("preferred_payment_method"),
        "status": data.get("status"),
        "metadata": data.get("metadata") or {},
        "created_at": data.get("created_at"),
    }


def _normalize_service_request(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalize_service_request
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_map(row)
    return {
        "service_request_id": _string(data.get("service_request_id")),
        "case_id": _string(data.get("case_id")),
        "request_kind": data.get("request_kind"),
        "status": data.get("status"),
        "lot_id": _string(data.get("lot_id")),
        "asset_reference": data.get("asset_reference"),
        "current_vendor_id": _string(data.get("current_vendor_id")),
        "current_vendor_name": data.get("current_vendor_name"),
        "disturbance_level": data.get("disturbance_level"),
        "preferred_contact_method": data.get("preferred_contact_method"),
        "created_by": _string(data.get("created_by")),
        "updated_by": _string(data.get("updated_by")),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _normalize_assignment(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalize_assignment
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_map(row)
    created_at = data.get("assigned_at") or data.get("created_at")
    return {
        "assignment_id": _string(data.get("assignment_id") or data.get("id")),
        "service_request_id": _string(data.get("service_request_id")),
        "vendor_id": _string(data.get("vendor_id")),
        "vendor_name": data.get("vendor_name"),
        "assignment_status": data.get("assignment_status"),
        "assigned_by": _string(data.get("assigned_by")),
        "response_due_at": data.get("response_due_at"),
        "responded_at": data.get("responded_at"),
        "response_note": data.get("response_note"),
        "created_at": created_at,
        "updated_at": data.get("updated_at") or created_at,
    }


def _normalize_template(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalize_template
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_map(row)
    return {
        "template_id": _string(data.get("template_id") or data.get("id")),
        "title": data.get("title"),
        "description": data.get("description"),
        "category": data.get("category"),
        "cadence_unit": data.get("cadence_unit"),
        "cadence_interval": int(data.get("cadence_interval") or 1),
        "priority": data.get("priority"),
        "default_assignee": _string(data.get("default_assignee")),
        "vendor_id": _string(data.get("vendor_id")),
        "vendor_name": data.get("vendor_name"),
        "next_due_at": data.get("next_due_at"),
        "last_run_at": data.get("last_run_at"),
        "is_active": bool(data.get("is_active", False)),
        "created_by": _string(data.get("created_by")),
        "updated_by": _string(data.get("updated_by")),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


async def list_service_providers(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_service_providers
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT v.vendor_id, v.party_id, p.legal_name, p.preferred_name, p.primary_email,
                       p.primary_mobile, p.abn, p.metadata, v.trade_categories, v.abn_status,
                       v.gst_registered, v.insurance_expiry, v.licence_expiry,
                       v.preferred_payment_method, v.status, v.created_at
                FROM ops.vendors v
                JOIN core.parties p ON p.party_id = v.party_id
                WHERE v.scheme_id = :scheme_id
                  AND v.status = 'active'
                ORDER BY p.legal_name
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalize_vendor(row) for row in result.fetchall()]


async def create_service_provider(*, building_id: str, current_user: dict, payload: ServiceProviderCreate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: create_service_provider
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    values = payload.model_dump()
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        party_result = await session.execute(
            text(
                """
                INSERT INTO core.parties
                    (tenant_id, party_type, legal_name, preferred_name, abn, primary_email,
                     primary_mobile, metadata, status, updated_at)
                VALUES
                    (:tenant_id, 'vendor', :legal_name, :preferred_name, :abn, :primary_email,
                     :primary_mobile, CAST(:metadata AS jsonb), 'active', now())
                RETURNING party_id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "legal_name": values["legal_name"],
                "preferred_name": values.get("preferred_name"),
                "abn": values.get("abn"),
                "primary_email": values.get("primary_email"),
                "primary_mobile": values.get("primary_mobile"),
                "metadata": json.dumps(values.get("metadata") or {}),
            },
        )
        party_id = party_result.scalar_one()
        vendor_result = await session.execute(
            text(
                """
                INSERT INTO ops.vendors
                    (tenant_id, party_id, trade_categories, abn_status, gst_registered,
                     insurance_expiry, licence_expiry, preferred_payment_method, status)
                VALUES
                    (:tenant_id, :party_id, :trade_categories, :abn_status, :gst_registered,
                     :insurance_expiry, :licence_expiry, :preferred_payment_method, 'active')
                RETURNING vendor_id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "party_id": party_id,
                "trade_categories": values["trade_categories"],
                "abn_status": values.get("abn_status"),
                "gst_registered": values["gst_registered"],
                "insurance_expiry": values.get("insurance_expiry"),
                "licence_expiry": values.get("licence_expiry"),
                "preferred_payment_method": values.get("preferred_payment_method"),
            },
        )
        vendor_id = vendor_result.scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO core.audit_events
                    (tenant_id, scheme_id, entity_type, entity_id, action, actor_user_id, ip_address, user_agent, event_payload)
                VALUES
                    (:tenant_id, :scheme_id, 'ops_vendor', :entity_id, 'ops.vendor.created',
                     :actor_user_id, :ip_address, :user_agent, CAST(:payload AS jsonb))
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "entity_id": vendor_id,
                "actor_user_id": ctx.actor_user_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "payload": json.dumps({"party_id": str(party_id), "trade_categories": values["trade_categories"], "correlation_id": correlation_id}),
            },
        )
        row = (
            await session.execute(
                text(
                    """
                    SELECT v.vendor_id, v.party_id, p.legal_name, p.preferred_name, p.primary_email,
                           p.primary_mobile, p.abn, p.metadata, v.trade_categories, v.abn_status,
                           v.gst_registered, v.insurance_expiry, v.licence_expiry,
                           v.preferred_payment_method, v.status, v.created_at
                    FROM ops.vendors v
                    JOIN core.parties p ON p.party_id = v.party_id
                    WHERE v.vendor_id = :vendor_id
                      AND v.scheme_id = :scheme_id
                    """
                ),
                {"vendor_id": vendor_id, "scheme_id": ctx.scheme_id},
            )
        ).one()
        return _normalize_vendor(row)


async def update_service_provider(*, building_id: str, vendor_id: str, current_user: dict, payload: ServiceProviderUpdate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: update_service_provider
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No vendor fields were provided.")
    vendor_uuid = _parse_uuid(vendor_id, label="vendor_id")

    vendor_fields = {"trade_categories", "abn_status", "gst_registered", "insurance_expiry", "licence_expiry", "preferred_payment_method", "status"}
    party_fields = {"legal_name", "preferred_name", "primary_email", "primary_mobile", "abn", "metadata"}

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        existing = (
            await session.execute(
                text(
                    """
                    SELECT v.vendor_id, v.party_id
                    FROM ops.vendors v
                    WHERE v.vendor_id = :vendor_id
                      AND v.scheme_id = :scheme_id
                    """
                ),
                {"vendor_id": vendor_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service provider not found.")

        vendor_sets = []
        vendor_params = {"vendor_id": vendor_uuid, "scheme_id": ctx.scheme_id}
        for key in vendor_fields:
            if key in updates:
                vendor_sets.append(f"{key} = :{key}")
                vendor_params[key] = updates[key]
        if vendor_sets:
            await session.execute(
                text(f"UPDATE ops.vendors SET {', '.join(vendor_sets)} WHERE vendor_id = :vendor_id AND scheme_id = :scheme_id"),
                vendor_params,
            )

        party_sets = []
        party_params = {"party_id": existing.party_id}
        for key in party_fields:
            if key in updates:
                party_sets.append(f"{key} = :{key}")
                party_params[key] = json.dumps(updates[key]) if key == "metadata" else updates[key]
        if party_sets:
            party_sets.append("updated_at = now()")
            await session.execute(text(f"UPDATE core.parties SET {', '.join(party_sets)} WHERE party_id = :party_id"), party_params)

        await session.execute(
            text(
                """
                INSERT INTO core.audit_events
                    (tenant_id, scheme_id, entity_type, entity_id, action, actor_user_id, ip_address, user_agent, event_payload)
                VALUES
                    (:tenant_id, :scheme_id, 'ops_vendor', :entity_id, 'ops.vendor.updated',
                     :actor_user_id, :ip_address, :user_agent, CAST(:payload AS jsonb))
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "entity_id": vendor_uuid,
                "actor_user_id": ctx.actor_user_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "payload": json.dumps({"changed_fields": sorted(updates.keys()), "correlation_id": correlation_id}),
            },
        )
        row = (
            await session.execute(
                text(
                    """
                    SELECT v.vendor_id, v.party_id, p.legal_name, p.preferred_name, p.primary_email,
                           p.primary_mobile, p.abn, p.metadata, v.trade_categories, v.abn_status,
                           v.gst_registered, v.insurance_expiry, v.licence_expiry,
                           v.preferred_payment_method, v.status, v.created_at
                    FROM ops.vendors v
                    JOIN core.parties p ON p.party_id = v.party_id
                    WHERE v.vendor_id = :vendor_id
                      AND v.scheme_id = :scheme_id
                    """
                ),
                {"vendor_id": vendor_uuid, "scheme_id": ctx.scheme_id},
            )
        ).one()
        return _normalize_vendor(row)


async def list_service_requests(*, building_id: str, current_user: dict, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_service_requests
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    params: dict[str, Any] = {"scheme_id": ctx.scheme_id}
    clause = ""
    if status_filter:
        clause = "AND sr.status = :status_filter"
        params["status_filter"] = status_filter
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                SELECT sr.id AS service_request_id, sr.case_id, sr.request_kind, sr.status, sr.lot_id,
                       sr.asset_reference, sr.current_vendor_id, p.legal_name AS current_vendor_name,
                       sr.disturbance_level, sr.preferred_contact_method, sr.created_by, sr.updated_by,
                       sr.created_at, sr.updated_at
                FROM ops.service_requests sr
                LEFT JOIN ops.vendors v ON v.vendor_id = sr.current_vendor_id
                LEFT JOIN core.parties p ON p.party_id = v.party_id
                WHERE sr.scheme_id = :scheme_id
                  AND sr.deleted_at IS NULL
                  {clause}
                ORDER BY sr.created_at DESC
                """
            ),
            params,
        )
        return [_normalize_service_request(row) for row in result.fetchall()]


async def create_service_request(*, building_id: str, current_user: dict, payload: ServiceRequestCreate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: create_service_request
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    values = payload.model_dump()
    case_uuid = _parse_uuid(values["case_id"], label="case_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id
                    FROM ops.service_requests
                    WHERE case_id = :case_id
                      AND scheme_id = :scheme_id
                      AND deleted_at IS NULL
                    LIMIT 1
                    """
                ),
                {"case_id": case_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A service request already exists for this case.")
        result = await session.execute(
            text(
                """
                INSERT INTO ops.service_requests
                    (tenant_id, scheme_id, case_id, request_kind, status, lot_id, asset_reference,
                     disturbance_level, preferred_contact_method, created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :request_kind, 'triaged', :lot_id, :asset_reference,
                     :disturbance_level, :preferred_contact_method, :created_by, :updated_by, :correlation_id)
                RETURNING id AS service_request_id, case_id, request_kind, status, lot_id,
                          asset_reference, current_vendor_id, disturbance_level,
                          preferred_contact_method, created_by, updated_by, created_at, updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "request_kind": values["request_kind"],
                "lot_id": _parse_uuid(values["lot_id"], label="lot_id") if values.get("lot_id") else None,
                "asset_reference": values.get("asset_reference"),
                "disturbance_level": values["disturbance_level"],
                "preferred_contact_method": values.get("preferred_contact_method"),
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        if case["status"] not in {"assigned", "in_progress", "scheduled"}:
            await session.execute(
                text("UPDATE ops.cases SET status = 'triaged', updated_by = :updated_by, updated_at = now() WHERE id = :case_id"),
                {"updated_by": ctx.actor_user_id, "case_id": case_uuid},
            )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="service_request_created",
            payload={"request_kind": values["request_kind"], "disturbance_level": values["disturbance_level"]},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.service_request.created",
            payload={"request_kind": values["request_kind"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row = result.one()
        return _normalize_service_request(row)


async def get_service_request(*, building_id: str, service_request_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_service_request
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    sr_uuid = _parse_uuid(service_request_id, label="service_request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT sr.id AS service_request_id, sr.case_id, sr.request_kind, sr.status, sr.lot_id,
                       sr.asset_reference, sr.current_vendor_id, p.legal_name AS current_vendor_name,
                       sr.disturbance_level, sr.preferred_contact_method, sr.created_by, sr.updated_by,
                       sr.created_at, sr.updated_at
                FROM ops.service_requests sr
                LEFT JOIN ops.vendors v ON v.vendor_id = sr.current_vendor_id
                LEFT JOIN core.parties p ON p.party_id = v.party_id
                WHERE sr.id = :service_request_id
                  AND sr.scheme_id = :scheme_id
                  AND sr.deleted_at IS NULL
                """
            ),
            {"service_request_id": sr_uuid, "scheme_id": ctx.scheme_id},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service request not found.")
        return _normalize_service_request(row)


async def assign_vendor(*, building_id: str, service_request_id: str, current_user: dict, payload: VendorAssignmentCreate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: assign_vendor
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    sr_uuid = _parse_uuid(service_request_id, label="service_request_id")
    vendor_uuid = _parse_uuid(payload.vendor_id, label="vendor_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        sr_row = (
            await session.execute(
                text(
                    """
                    SELECT id, case_id
                    FROM ops.service_requests
                    WHERE id = :id
                      AND scheme_id = :scheme_id
                      AND deleted_at IS NULL
                    """
                ),
                {"id": sr_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if sr_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service request not found.")
        vendor_row = (
            await session.execute(
                text(
                    """
                     SELECT v.vendor_id, p.legal_name
                     FROM ops.vendors v
                     JOIN core.parties p ON p.party_id = v.party_id
                     WHERE v.vendor_id = :vendor_id
                       AND v.scheme_id = :scheme_id
                       AND v.status = 'active'
                     """
                ),
                {"vendor_id": vendor_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if vendor_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service provider not found.")
        result = await session.execute(
            text(
                """
                INSERT INTO ops.vendor_assignments
                    (tenant_id, scheme_id, service_request_id, vendor_id, assignment_status, assigned_by,
                     response_due_at, response_note, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :service_request_id, :vendor_id, 'assigned', :assigned_by,
                     :response_due_at, :response_note, :updated_by, :correlation_id)
                RETURNING id AS assignment_id, service_request_id, vendor_id, assignment_status,
                          assigned_by, response_due_at, responded_at, response_note, assigned_at, updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "service_request_id": sr_uuid,
                "vendor_id": vendor_uuid,
                "assigned_by": ctx.actor_user_id,
                "response_due_at": payload.response_due_at,
                "response_note": payload.response_note,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE ops.service_requests
                SET current_vendor_id = :vendor_id,
                    status = 'scheduled',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :service_request_id
                  AND scheme_id = :scheme_id
                """
            ),
            {
                "vendor_id": vendor_uuid,
                "updated_by": ctx.actor_user_id,
                "service_request_id": sr_uuid,
                "scheme_id": ctx.scheme_id,
            },
        )
        if sr_row.case_id:
            case = await _get_case_for_access(session, ctx, sr_row.case_id, mode="view")
            next_case_status = _valid_case_assignment_transition(case["status"])
            if next_case_status != case["status"]:
                await session.execute(
                    text(
                        """
                        UPDATE ops.cases
                        SET status = :status,
                            updated_by = :updated_by,
                            updated_at = now()
                        WHERE id = :case_id
                          AND scheme_id = :scheme_id
                        """
                    ),
                    {
                        "status": next_case_status,
                        "updated_by": ctx.actor_user_id,
                        "case_id": sr_row.case_id,
                        "scheme_id": ctx.scheme_id,
                    },
                )
                await _insert_status_history(
                    session,
                    ctx,
                    case_uuid=sr_row.case_id,
                    from_status=case["status"],
                    to_status=next_case_status,
                    reason="Vendor assigned",
                    correlation_id=correlation_id,
                )
            await _insert_case_event(
                session,
                ctx,
                case_uuid=sr_row.case_id,
                event_type="vendor_assigned",
                payload={"vendor_id": str(vendor_uuid), "vendor_name": vendor_row.legal_name},
                correlation_id=correlation_id,
            )
            await _insert_audit_event(
                session,
                ctx,
                case_uuid=sr_row.case_id,
                action="ops.vendor_assignment.created",
                payload={"vendor_id": str(vendor_uuid), "service_request_id": str(sr_uuid), "correlation_id": correlation_id},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        assignment = _normalize_assignment(result.one())
        assignment["vendor_name"] = vendor_row.legal_name
        return assignment


async def list_vendor_assignments(*, building_id: str, service_request_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_vendor_assignments
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    sr_uuid = _parse_uuid(service_request_id, label="service_request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT va.id AS assignment_id, va.service_request_id, va.vendor_id, p.legal_name AS vendor_name,
                       va.assignment_status, va.assigned_by, va.response_due_at, va.responded_at,
                       va.response_note, va.assigned_at, va.updated_at
                FROM ops.vendor_assignments va
                JOIN ops.vendors v ON v.vendor_id = va.vendor_id
                JOIN core.parties p ON p.party_id = v.party_id
                WHERE va.service_request_id = :service_request_id
                  AND va.scheme_id = :scheme_id
                ORDER BY va.assigned_at DESC
                """
            ),
            {"service_request_id": sr_uuid, "scheme_id": ctx.scheme_id},
        )
        return [_normalize_assignment(row) for row in result.fetchall()]


def _next_due(current_due: datetime | None, cadence_unit: str, cadence_interval: int) -> datetime:
    """Generated function header.

    Function: _next_due
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    base = current_due or datetime.now(timezone.utc)
    if cadence_unit == "day":
        return base + timedelta(days=cadence_interval)
    if cadence_unit == "week":
        return base + timedelta(weeks=cadence_interval)
    if cadence_unit == "month":
        return base + timedelta(days=30 * cadence_interval)
    if cadence_unit == "quarter":
        return base + timedelta(days=90 * cadence_interval)
    if cadence_unit == "year":
        return base + timedelta(days=365 * cadence_interval)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported cadence unit.")


async def list_recurring_templates(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_recurring_templates
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT rtt.id AS template_id, rtt.title, rtt.description, rtt.category, rtt.cadence_unit,
                       rtt.cadence_interval, rtt.priority, rtt.default_assignee, rtt.vendor_id,
                       p.legal_name AS vendor_name, rtt.next_due_at, rtt.last_run_at, rtt.is_active,
                       rtt.created_by, rtt.updated_by, rtt.created_at, rtt.updated_at
                FROM ops.recurring_task_templates rtt
                LEFT JOIN ops.vendors v ON v.vendor_id = rtt.vendor_id
                LEFT JOIN core.parties p ON p.party_id = v.party_id
                WHERE rtt.scheme_id = :scheme_id
                  AND rtt.deleted_at IS NULL
                ORDER BY rtt.title
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalize_template(row) for row in result.fetchall()]


async def create_recurring_template(*, building_id: str, current_user: dict, payload: RecurringTaskTemplateCreate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: create_recurring_template
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    values = payload.model_dump()
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                INSERT INTO ops.recurring_task_templates
                    (tenant_id, scheme_id, title, description, category, cadence_unit, cadence_interval,
                     priority, default_assignee, vendor_id, next_due_at, is_active, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :title, :description, :category, :cadence_unit, :cadence_interval,
                     :priority, :default_assignee, :vendor_id, :next_due_at, :is_active, :created_by, :updated_by,
                     :correlation_id)
                RETURNING id AS template_id, title, description, category, cadence_unit, cadence_interval,
                          priority, default_assignee, vendor_id, next_due_at, last_run_at, is_active,
                          created_by, updated_by, created_at, updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "title": values["title"],
                "description": values.get("description"),
                "category": values["category"],
                "cadence_unit": values["cadence_unit"],
                "cadence_interval": values["cadence_interval"],
                "priority": values["priority"],
                "default_assignee": _parse_uuid(values["default_assignee"], label="default_assignee") if values.get("default_assignee") else None,
                "vendor_id": _parse_uuid(values["vendor_id"], label="vendor_id") if values.get("vendor_id") else None,
                "next_due_at": values.get("next_due_at"),
                "is_active": values["is_active"],
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        template = _normalize_template(result.one())
        await session.execute(
            text(
                """
                INSERT INTO core.audit_events
                    (tenant_id, scheme_id, entity_type, entity_id, action, actor_user_id, ip_address, user_agent, event_payload)
                VALUES
                    (:tenant_id, :scheme_id, 'ops_recurring_template', :entity_id, 'ops.recurring_template.created',
                     :actor_user_id, :ip_address, :user_agent, CAST(:payload AS jsonb))
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "entity_id": template["template_id"],
                "actor_user_id": ctx.actor_user_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "payload": json.dumps({"category": values["category"], "cadence_unit": values["cadence_unit"], "cadence_interval": values["cadence_interval"], "correlation_id": correlation_id}),
            },
        )
        return template


async def generate_recurring_task(*, building_id: str, template_id: str, current_user: dict, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: generate_recurring_task
    Path: backend/services/ops_repairs_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    template_uuid = _parse_uuid(template_id, label="template_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        template_row = (
            await session.execute(
                text(
                    """
                     SELECT id AS template_id, title, description, category, cadence_unit, cadence_interval,
                            priority, default_assignee, vendor_id, next_due_at, last_run_at, is_active
                     FROM ops.recurring_task_templates
                     WHERE id = :template_id
                       AND scheme_id = :scheme_id
                       AND deleted_at IS NULL
                     """
                ),
                {"template_id": template_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if template_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring task template not found.")
        if not template_row.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recurring task template is inactive.")

        case_result = await session.execute(
            text(
                """
                INSERT INTO ops.cases
                    (tenant_id, scheme_id, title, description, category, priority, risk_level, status,
                     source_type, source_id, assigned_to, visibility_scope, pii_classification,
                     created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :title, :description, :category, :priority, 'low', 'scheduled',
                     'recurring_template', :source_id, :assigned_to, 'building', 'internal',
                     :created_by, :updated_by, :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "title": template_row.title,
                "description": template_row.description,
                "category": template_row.category,
                "priority": template_row.priority,
                "source_id": str(template_uuid),
                "assigned_to": template_row.default_assignee,
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        case_uuid = case_result.scalar_one()
        await _insert_status_history(
            session,
            ctx,
            case_uuid=case_uuid,
            from_status=None,
            to_status="scheduled",
            reason="Generated from recurring task template",
            correlation_id=correlation_id,
        )
        service_result = await session.execute(
            text(
                """
                INSERT INTO ops.service_requests
                    (tenant_id, scheme_id, case_id, request_kind, status, disturbance_level,
                     created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :request_kind, 'scheduled', 'low',
                     :created_by, :updated_by, :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "request_kind": template_row.category,
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        service_request_uuid = service_result.scalar_one()
        if template_row.vendor_id:
            await session.execute(
                text(
                    """
                    INSERT INTO ops.vendor_assignments
                        (tenant_id, scheme_id, service_request_id, vendor_id, assignment_status, assigned_by, updated_by, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :service_request_id, :vendor_id, 'assigned', :assigned_by, :updated_by, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "service_request_id": service_request_uuid,
                    "vendor_id": template_row.vendor_id,
                    "assigned_by": ctx.actor_user_id,
                    "updated_by": ctx.actor_user_id,
                    "correlation_id": correlation_id,
                },
            )
            await session.execute(
                text(
                    """
                    UPDATE ops.service_requests
                    SET current_vendor_id = :vendor_id
                    WHERE id = :service_request_id
                      AND scheme_id = :scheme_id
                    """
                ),
                {
                    "vendor_id": template_row.vendor_id,
                    "service_request_id": service_request_uuid,
                    "scheme_id": ctx.scheme_id,
                },
            )
        next_due_at = _next_due(template_row.next_due_at, template_row.cadence_unit, template_row.cadence_interval)
        await session.execute(
            text(
                """
                UPDATE ops.recurring_task_templates
                SET last_run_at = now(),
                    next_due_at = :next_due_at,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :template_id
                  AND scheme_id = :scheme_id
                """
            ),
            {
                "next_due_at": next_due_at,
                "updated_by": ctx.actor_user_id,
                "template_id": template_uuid,
                "scheme_id": ctx.scheme_id,
            },
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="recurring_task_generated",
            payload={"template_id": str(template_uuid), "service_request_id": str(service_request_uuid)},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.recurring_task.generated",
            payload={"template_id": str(template_uuid), "service_request_id": str(service_request_uuid), "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "template_id": str(template_uuid),
            "case_id": str(case_uuid),
            "service_request_id": str(service_request_uuid),
            "next_due_at": next_due_at,
        }
