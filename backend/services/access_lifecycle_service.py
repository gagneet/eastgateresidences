"""Postgres-backed access device lifecycle workflows."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text

from db_postgres.session import async_session_context, set_tenant
from models.access_lifecycle import (
    AccessDeviceCreate,
    AccessDeviceTypeCreate,
    AccessDeviceTypeUpdate,
    AccessDisableRequest,
    AccessIssueRequest,
    AccessRequestAction,
    AccessRequestCreate,
    AccessReturnRequest,
)
from models.user import UserRole
from utils.permissions import get_user_permissions
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
_REQUESTER_ROLES = _MANAGER_ROLES | {UserRole.OWNER, UserRole.TENANT, UserRole.REAL_ESTATE_AGENT}
_DEVICE_TYPE_MANAGER_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER}


def _assert_requester(role: str) -> None:
    """Generated function header.

    Function: _assert_requester
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if role not in _REQUESTER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot request access devices.")


def _assert_manager(role: str) -> None:
    """Generated function header.

    Function: _assert_manager
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager access required.")


def _assert_device_type_editor(ctx, current_user: dict) -> None:
    if ctx.role in _DEVICE_TYPE_MANAGER_ROLES:
        return
    if ctx.role == UserRole.ADMIN_STAFF and get_user_permissions(current_user).can_manage_access_device_settings:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access device settings permission required.")


def _json(payload: dict[str, Any] | None) -> str:
    """Generated function header.

    Function: _json
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return json.dumps(payload or {}, default=str)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "access_device"


def _policy_from_payload(values: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = dict(existing or {})
    for key in ("request_available", "replacement_lost_fee_cents", "max_quantity", "effective_date"):
        if key in values:
            value = values[key]
            policy[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return policy


def _normalize_device_type(row) -> dict[str, Any]:
    data = _row_to_dict(row)
    policy = data.get("lifecycle_policy") or {}
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except json.JSONDecodeError:
            policy = {}
    return {
        "device_type_id": str(data["device_type_id"]),
        "type_key": data.get("type_key"),
        "name": data.get("name"),
        "description": data.get("description"),
        "request_available": bool(policy.get("request_available", True)),
        "fee_cents": int(data.get("fee_cents") or 0),
        "deposit_cents": int(data.get("deposit_cents") or 0),
        "replacement_lost_fee_cents": int(policy.get("replacement_lost_fee_cents") or 0),
        "max_quantity": int(policy.get("max_quantity") or 1),
        "requires_approval": bool(data.get("requires_approval", False)),
        "requires_return": bool(data.get("requires_return", True)),
        "effective_date": policy.get("effective_date"),
        "is_active": bool(data.get("is_active", True)),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _mask(value: str | None) -> str | None:
    """Generated function header.

    Function: _mask
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _normalize_device(row, *, mask_sensitive: bool) -> dict[str, Any]:
    """Generated function header.

    Function: _normalize_device
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    return {
        "device_id": str(data["device_id"]),
        "device_type_id": str(data["device_type_id"]),
        "device_type_name": data.get("device_type_name"),
        "inventory_code": _mask(data.get("inventory_code")) if mask_sensitive else data.get("inventory_code"),
        "serial_number": _mask(data.get("serial_number")) if mask_sensitive else data.get("serial_number"),
        "status": data.get("status"),
        "current_holder_party_id": str(data["current_holder_party_id"]) if data.get("current_holder_party_id") else None,
        "issued_to_lot_id": str(data["issued_to_lot_id"]) if data.get("issued_to_lot_id") else None,
        "issued_at": data.get("issued_at"),
        "returned_at": data.get("returned_at"),
        "deactivated_at": data.get("deactivated_at"),
        "notes": data.get("notes"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _normalize_request(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalize_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    return {
        "request_id": str(data["request_id"]),
        "case_id": str(data["case_id"]) if data.get("case_id") else None,
        "device_type_id": str(data["device_type_id"]),
        "device_type_name": data.get("device_type_name"),
        "requester_user_id": str(data["requester_user_id"]) if data.get("requester_user_id") else None,
        "requester_party_id": str(data["requester_party_id"]) if data.get("requester_party_id") else None,
        "lot_id": str(data["lot_id"]) if data.get("lot_id") else None,
        "request_type": data.get("request_type"),
        "reason": data.get("reason"),
        "status": data.get("status"),
        "approval_required": bool(data.get("approval_required", False)),
        "approval_request_id": str(data["approval_request_id"]) if data.get("approval_request_id") else None,
        "fee_cents": int(data.get("fee_cents") or 0),
        "deposit_cents": int(data.get("deposit_cents") or 0),
        "requested_at": data.get("requested_at"),
        "due_at": data.get("due_at"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


async def _device_type_row(session, scheme_id, device_type_id):
    # RLS is tenant-scoped for access.* tables, so every building-facing lookup
    # also pins scheme_id to prevent cross-building reads within the same tenant.
    """Generated function header.

    Function: _device_type_row
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id, name, fee_cents, deposit_cents, requires_approval
            FROM access.access_device_types
            WHERE id = :device_type_id
              AND scheme_id = :scheme_id
              AND is_active = TRUE
            """
        ),
        {"device_type_id": device_type_id, "scheme_id": scheme_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access device type not found.")
    return row


async def list_device_types(*, building_id: str, current_user: dict, include_inactive: bool = False) -> list[dict[str, Any]]:
    """List configured device types for the current building.

    Normal requesters only receive active, requestable rows. The broader
    settings view, including archived rows, is restricted to settings editors.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_requester(ctx.role)
    if include_inactive:
        _assert_device_type_editor(ctx, current_user)
    where = "WHERE scheme_id = :scheme_id"
    if not include_inactive:
        where += " AND is_active = TRUE AND COALESCE((lifecycle_policy->>'request_available')::boolean, TRUE) = TRUE"
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                SELECT id AS device_type_id, type_key, name, description, fee_cents, deposit_cents,
                       requires_return, requires_approval, lifecycle_policy, is_active, created_at, updated_at
                FROM access.access_device_types
                {where}
                ORDER BY is_active DESC, name ASC
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalize_device_type(row) for row in result.fetchall()]


async def get_requestable_device_type_by_key(*, building_id: str, current_user: dict, type_key: str) -> dict[str, Any]:
    """Resolve the request form's type key to the active PostgreSQL config row."""
    ctx = await _resolve_context(building_id, current_user)
    _assert_requester(ctx.role)
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT id AS device_type_id, type_key, name, description, fee_cents, deposit_cents,
                       requires_return, requires_approval, lifecycle_policy, is_active, created_at, updated_at
                FROM access.access_device_types
                WHERE scheme_id = :scheme_id
                  AND type_key = :type_key
                  AND is_active = TRUE
                  AND COALESCE((lifecycle_policy->>'request_available')::boolean, TRUE) = TRUE
                """
            ),
            {"scheme_id": ctx.scheme_id, "type_key": type_key},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access device type is not available for requests.")
        return _normalize_device_type(row)


async def create_device_type(*, building_id: str, current_user: dict, payload: AccessDeviceTypeCreate) -> dict[str, Any]:
    """Create a building-scoped access device type in PostgreSQL."""
    ctx = await _resolve_context(building_id, current_user)
    _assert_device_type_editor(ctx, current_user)
    values = payload.model_dump()
    type_key = _slug(values.get("type_key") or values["name"])
    policy = _policy_from_payload(values)
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                INSERT INTO access.access_device_types
                    (tenant_id, scheme_id, type_key, name, description, fee_cents, deposit_cents,
                     requires_return, requires_approval, lifecycle_policy, is_active, created_by, updated_by)
                VALUES
                    (:tenant_id, :scheme_id, :type_key, :name, :description, :fee_cents, :deposit_cents,
                     :requires_return, :requires_approval, CAST(:lifecycle_policy AS jsonb), :is_active, :actor_user_id, :actor_user_id)
                RETURNING id AS device_type_id, type_key, name, description, fee_cents, deposit_cents,
                          requires_return, requires_approval, lifecycle_policy, is_active, created_at, updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "type_key": type_key,
                "name": values["name"],
                "description": values.get("description"),
                "fee_cents": values.get("fee_cents") or 0,
                "deposit_cents": values.get("deposit_cents") or 0,
                "requires_return": values.get("requires_return", True),
                "requires_approval": values.get("requires_approval", True),
                "lifecycle_policy": _json(policy),
                "is_active": values.get("is_active", True),
                "actor_user_id": ctx.actor_user_id,
            },
        )
        return _normalize_device_type(result.one())


async def update_device_type(*, building_id: str, current_user: dict, device_type_id: str, payload: AccessDeviceTypeUpdate) -> dict[str, Any]:
    """Update editable pricing/request-rule fields without touching inventory rows."""
    ctx = await _resolve_context(building_id, current_user)
    _assert_device_type_editor(ctx, current_user)
    device_type_uuid = _parse_uuid(device_type_id, label="device_type_id")
    values = payload.model_dump(exclude_unset=True)
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        existing_result = await session.execute(
            text(
                """
                SELECT lifecycle_policy
                FROM access.access_device_types
                WHERE id = :device_type_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"device_type_id": device_type_uuid, "scheme_id": ctx.scheme_id},
        )
        existing = existing_result.first()
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access device type not found.")
        existing_policy = _row_to_dict(existing).get("lifecycle_policy") or {}
        if isinstance(existing_policy, str):
            existing_policy = json.loads(existing_policy)
        policy = _policy_from_payload(values, existing_policy)
        result = await session.execute(
            text(
                """
                UPDATE access.access_device_types
                SET type_key = COALESCE(:type_key, type_key),
                    name = COALESCE(:name, name),
                    description = CASE WHEN :description_is_set THEN :description ELSE description END,
                    fee_cents = COALESCE(:fee_cents, fee_cents),
                    deposit_cents = COALESCE(:deposit_cents, deposit_cents),
                    requires_return = COALESCE(:requires_return, requires_return),
                    requires_approval = COALESCE(:requires_approval, requires_approval),
                    lifecycle_policy = CAST(:lifecycle_policy AS jsonb),
                    is_active = COALESCE(:is_active, is_active),
                    updated_by = :actor_user_id,
                    updated_at = now()
                WHERE id = :device_type_id
                  AND scheme_id = :scheme_id
                RETURNING id AS device_type_id, type_key, name, description, fee_cents, deposit_cents,
                          requires_return, requires_approval, lifecycle_policy, is_active, created_at, updated_at
                """
            ),
            {
                "device_type_id": device_type_uuid,
                "scheme_id": ctx.scheme_id,
                "type_key": _slug(values["type_key"]) if values.get("type_key") else None,
                "name": values.get("name"),
                "description": values.get("description") if "description" in values else None,
                "description_is_set": "description" in values,
                "fee_cents": values.get("fee_cents"),
                "deposit_cents": values.get("deposit_cents"),
                "requires_return": values.get("requires_return"),
                "requires_approval": values.get("requires_approval"),
                "lifecycle_policy": _json(policy),
                "is_active": values.get("is_active"),
                "actor_user_id": ctx.actor_user_id,
            },
        )
        return _normalize_device_type(result.one())


async def archive_device_type(*, building_id: str, current_user: dict, device_type_id: str) -> dict[str, Any]:
    """Archive a device type so it disappears from resident request options."""
    return await update_device_type(
        building_id=building_id,
        current_user=current_user,
        device_type_id=device_type_id,
        payload=AccessDeviceTypeUpdate(is_active=False, request_available=False),
    )


async def list_devices(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_devices
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT d.id AS device_id, d.device_type_id, dt.name AS device_type_name,
                       d.inventory_code, d.serial_number, d.status, d.current_holder_party_id,
                       d.issued_to_lot_id, d.issued_at, d.returned_at, d.deactivated_at,
                       d.notes, d.created_at, d.updated_at
                FROM access.access_devices d
                JOIN access.access_device_types dt ON dt.id = d.device_type_id
                WHERE d.scheme_id = :scheme_id
                ORDER BY d.created_at DESC
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalize_device(row, mask_sensitive=False) for row in result.fetchall()]


async def create_device(*, building_id: str, current_user: dict, payload: AccessDeviceCreate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: create_device
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    values = payload.model_dump()
    device_type_id = _parse_uuid(values["device_type_id"], label="device_type_id")
    procurement_batch_id = _parse_uuid(values["procurement_batch_id"], label="procurement_batch_id") if values.get("procurement_batch_id") else None
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        dt = await _device_type_row(session, ctx.scheme_id, device_type_id)
        result = await session.execute(
            text(
                """
                INSERT INTO access.access_devices
                    (tenant_id, scheme_id, device_type_id, procurement_batch_id, serial_number,
                     inventory_code, status, notes, created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_type_id, :procurement_batch_id, :serial_number,
                     :inventory_code, 'in_stock', :notes, :created_by, :updated_by, :correlation_id)
                RETURNING id AS device_id, device_type_id, serial_number, inventory_code, status,
                          current_holder_party_id, issued_to_lot_id, issued_at, returned_at,
                          deactivated_at, notes, created_at, updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_type_id": device_type_id,
                "procurement_batch_id": procurement_batch_id,
                "serial_number": values.get("serial_number"),
                "inventory_code": values.get("inventory_code"),
                "notes": values.get("notes"),
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        row = _normalize_device(result.one(), mask_sensitive=False)
        row["device_type_name"] = dt.name
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, device_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_id, 'device_created', CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_id": row["device_id"],
                "payload": _json({"inventory_code": values.get("inventory_code")}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=None,
            action="access.device.created",
            payload={"device_id": row["device_id"], "device_type_id": row["device_type_id"]},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return row


async def get_device(*, building_id: str, device_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_device
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    device_uuid = _parse_uuid(device_id, label="device_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT d.id AS device_id, d.device_type_id, dt.name AS device_type_name,
                       d.inventory_code, d.serial_number, d.status, d.current_holder_party_id,
                       d.issued_to_lot_id, d.issued_at, d.returned_at, d.deactivated_at,
                       d.notes, d.created_at, d.updated_at
                FROM access.access_devices d
                JOIN access.access_device_types dt ON dt.id = d.device_type_id
                WHERE d.id = :device_id
                  AND d.scheme_id = :scheme_id
                """
            ),
            {"device_id": device_uuid, "scheme_id": ctx.scheme_id},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access device not found.")
        return _normalize_device(row, mask_sensitive=False)


async def create_request(*, building_id: str, current_user: dict, payload: AccessRequestCreate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: create_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_requester(ctx.role)
    values = payload.model_dump()
    device_type_id = _parse_uuid(values["device_type_id"], label="device_type_id")
    lot_id = _parse_uuid(values["lot_id"], label="lot_id") if values.get("lot_id") else None
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        dt = await _device_type_row(session, ctx.scheme_id, device_type_id)
        initial_status = "awaiting_approval" if dt.requires_approval else "requested"
        case_result = await session.execute(
            text(
                """
                INSERT INTO ops.cases
                    (tenant_id, scheme_id, title, description, category, priority, risk_level, status,
                     source_type, visibility_scope, pii_classification, created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :title, :description, 'access_request', 'normal',
                     :risk_level, :status, 'access_request', 'manager_ec_only', 'internal',
                     :created_by, :updated_by, :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "title": f"{dt.name} {values['request_type'].replace('_', ' ')} request",
                "description": values.get("reason"),
                "risk_level": "high" if values["request_type"] in {"disablement", "lost", "stolen"} else "medium",
                "status": "waiting_for_approval" if dt.requires_approval else "new",
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
            to_status="waiting_for_approval" if dt.requires_approval else "new",
            reason="Access device request created",
            correlation_id=correlation_id,
        )
        req_result = await session.execute(
            text(
                """
                INSERT INTO access.access_device_requests
                    (tenant_id, scheme_id, case_id, device_type_id, requester_user_id, lot_id, request_type,
                     reason, status, approval_required, fee_cents, deposit_cents, due_at, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :device_type_id, :requester_user_id, :lot_id, :request_type,
                     :reason, :status, :approval_required, :fee_cents, :deposit_cents, :due_at, :created_by, :updated_by,
                     :correlation_id)
                RETURNING id AS request_id, case_id, device_type_id, requester_user_id, requester_party_id, lot_id,
                          request_type, reason, status, approval_required, approval_request_id, fee_cents, deposit_cents,
                          requested_at, due_at, created_at, updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "device_type_id": device_type_id,
                "requester_user_id": ctx.actor_user_id,
                "lot_id": lot_id,
                "request_type": values["request_type"],
                "reason": values.get("reason"),
                "status": initial_status,
                "approval_required": bool(dt.requires_approval),
                "fee_cents": int(dt.fee_cents or 0),
                "deposit_cents": int(dt.deposit_cents or 0),
                "due_at": values.get("due_at"),
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        request_row = _normalize_request(req_result.one())
        request_row["device_type_name"] = dt.name
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="access_request_created",
            payload={"request_id": request_row["request_id"], "request_type": request_row["request_type"], "approval_required": request_row["approval_required"]},
            correlation_id=correlation_id,
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, request_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :request_id, 'request_created', CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "request_id": request_row["request_id"],
                "payload": _json({"request_type": request_row["request_type"], "approval_required": request_row["approval_required"]}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="access.request.created",
            payload={"request_id": request_row["request_id"], "request_type": request_row["request_type"]},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return request_row


async def list_requests(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_requests
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_requester(ctx.role)
    where = "WHERE adr.scheme_id = :scheme_id"
    params: dict[str, Any] = {"scheme_id": ctx.scheme_id}
    if ctx.role not in _MANAGER_ROLES and ctx.actor_user_id:
        where += " AND adr.requester_user_id = :requester_user_id"
        params["requester_user_id"] = ctx.actor_user_id
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                SELECT adr.id AS request_id, adr.case_id, adr.device_type_id, dt.name AS device_type_name,
                       adr.requester_user_id, adr.requester_party_id, adr.lot_id, adr.request_type,
                       adr.reason, adr.status, adr.approval_required, adr.approval_request_id,
                       adr.fee_cents, adr.deposit_cents, adr.requested_at, adr.due_at, adr.created_at, adr.updated_at
                FROM access.access_device_requests adr
                JOIN access.access_device_types dt ON dt.id = adr.device_type_id
                {where}
                ORDER BY adr.created_at DESC
                """
            ),
            params,
        )
        return [_normalize_request(row) for row in result.fetchall()]


async def _fetch_request(session, scheme_id, request_uuid):
    """Generated function header.

    Function: _fetch_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT adr.id AS request_id, adr.case_id, adr.device_type_id, dt.name AS device_type_name,
                   adr.requester_user_id, adr.requester_party_id, adr.lot_id, adr.request_type,
                   adr.reason, adr.status, adr.approval_required, adr.approval_request_id,
                   adr.fee_cents, adr.deposit_cents, adr.requested_at, adr.due_at, adr.created_at, adr.updated_at
            FROM access.access_device_requests adr
            JOIN access.access_device_types dt ON dt.id = adr.device_type_id
            WHERE adr.id = :request_id
              AND adr.scheme_id = :scheme_id
            """
        ),
        {"request_id": request_uuid, "scheme_id": scheme_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found.")
    return _normalize_request(row)


async def _request_status_change(*, building_id: str, request_id: str, current_user: dict, new_status: str, note: str | None, event_type: str, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: _request_status_change
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    request_uuid = _parse_uuid(request_id, label="request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        req = await _fetch_request(session, ctx.scheme_id, request_uuid)
        await session.execute(
            text(
                """
                UPDATE access.access_device_requests
                SET status = :status,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :request_id
                  AND scheme_id = :scheme_id
                """
            ),
            {
                "status": new_status,
                "updated_by": ctx.actor_user_id,
                "request_id": request_uuid,
                "scheme_id": ctx.scheme_id,
            },
        )
        if req["case_id"]:
            case_uuid = _parse_uuid(req["case_id"], label="case_id")
            case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
            if new_status == "approved":
                case_status = "approved"
            elif new_status == "rejected":
                case_status = "rejected"
            else:
                case_status = case["status"]
            if case_status != case["status"]:
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
                        "status": case_status,
                        "updated_by": ctx.actor_user_id,
                        "case_id": case_uuid,
                        "scheme_id": ctx.scheme_id,
                    },
                )
                await _insert_status_history(
                    session,
                    ctx,
                    case_uuid=case_uuid,
                    from_status=case["status"],
                    to_status=case_status,
                    reason=note or f"Access request {new_status}",
                    correlation_id=correlation_id,
                )
            await _insert_case_event(
                session,
                ctx,
                case_uuid=case_uuid,
                event_type=event_type,
                payload={"request_id": request_id, "status": new_status, "note": note},
                correlation_id=correlation_id,
            )
            await _insert_audit_event(
                session,
                ctx,
                case_uuid=case_uuid,
                action=f"access.request.{new_status}",
                payload={"request_id": request_id, "status": new_status, "note": note},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, request_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :request_id, :event_type, CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "request_id": request_uuid,
                "event_type": event_type,
                "payload": _json({"status": new_status, "note": note}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        return await _fetch_request(session, ctx.scheme_id, request_uuid)


async def approve_request(*, building_id: str, request_id: str, current_user: dict, payload: AccessRequestAction, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: approve_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await _request_status_change(building_id=building_id, request_id=request_id, current_user=current_user, new_status="approved", note=payload.note, event_type="request_approved", correlation_id=correlation_id, ip_address=ip_address, user_agent=user_agent)


async def reject_request(*, building_id: str, request_id: str, current_user: dict, payload: AccessRequestAction, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: reject_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await _request_status_change(building_id=building_id, request_id=request_id, current_user=current_user, new_status="rejected", note=payload.note, event_type="request_rejected", correlation_id=correlation_id, ip_address=ip_address, user_agent=user_agent)


async def issue_request(*, building_id: str, request_id: str, current_user: dict, payload: AccessIssueRequest, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: issue_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    request_uuid = _parse_uuid(request_id, label="request_id")
    device_uuid = _parse_uuid(payload.device_id, label="device_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        req = await _fetch_request(session, ctx.scheme_id, request_uuid)
        if req["status"] not in {"approved", "allocated", "requested"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot issue from status '{req['status']}'.")
        device = (
            await session.execute(
                text(
                    """
                    SELECT id AS device_id, status
                    FROM access.access_devices
                    WHERE id = :device_id
                      AND scheme_id = :scheme_id
                    """
                ),
                {"device_id": device_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if device is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access device not found.")
        if device.status != "in_stock":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Access device is not in stock.")
        await session.execute(
            text(
                """
                UPDATE access.access_devices
                SET status = 'issued',
                    current_holder_party_id = COALESCE(:requester_party_id, current_holder_party_id),
                    issued_to_lot_id = COALESCE(:lot_id, issued_to_lot_id),
                    issued_at = now(),
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :device_id
                  AND scheme_id = :scheme_id
                """
            ),
            {
                "requester_party_id": _parse_uuid(req["requester_party_id"], label="requester_party_id") if req.get("requester_party_id") else None,
                "lot_id": _parse_uuid(req["lot_id"], label="lot_id") if req.get("lot_id") else None,
                "updated_by": ctx.actor_user_id,
                "device_id": device_uuid,
                "scheme_id": ctx.scheme_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_issuance
                    (tenant_id, scheme_id, request_id, device_id, issued_to_lot_id, issued_by, handoff_method,
                     acknowledgement_required, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :request_id, :device_id, :issued_to_lot_id, :issued_by, :handoff_method,
                     :acknowledgement_required, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "request_id": request_uuid,
                "device_id": device_uuid,
                "issued_to_lot_id": _parse_uuid(req["lot_id"], label="lot_id") if req.get("lot_id") else None,
                "issued_by": ctx.actor_user_id,
                "handoff_method": payload.handoff_method,
                "acknowledgement_required": payload.acknowledgement_required,
                "correlation_id": correlation_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE access.access_device_requests
                SET status = 'issued',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :request_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "request_id": request_uuid, "scheme_id": ctx.scheme_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, device_id, request_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_id, :request_id, 'device_issued', CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_id": device_uuid,
                "request_id": request_uuid,
                "payload": _json({"handoff_method": payload.handoff_method, "acknowledgement_required": payload.acknowledgement_required, "note": payload.note}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        if req["case_id"]:
            case_uuid = _parse_uuid(req["case_id"], label="case_id")
            await _insert_case_event(session, ctx, case_uuid=case_uuid, event_type="access_device_issued", payload={"request_id": request_id, "device_id": payload.device_id}, correlation_id=correlation_id)
        return await _fetch_request(session, ctx.scheme_id, request_uuid)


async def mark_lost_request(*, building_id: str, request_id: str, current_user: dict, payload: AccessRequestAction, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: mark_lost_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    request_uuid = _parse_uuid(request_id, label="request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        req = await _fetch_request(session, ctx.scheme_id, request_uuid)
        issuance = (
            await session.execute(
                text(
                    """
                    SELECT device_id
                    FROM access.access_device_issuance
                    WHERE request_id = :request_id
                      AND scheme_id = :scheme_id
                    ORDER BY issued_at DESC
                    LIMIT 1
                    """
                ),
                {"request_id": request_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if issuance is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request has no issued device to mark lost.")
        await session.execute(
            text(
                """
                UPDATE access.access_devices
                SET status = 'lost',
                    deactivated_at = now(),
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :device_id
                  AND scheme_id = :scheme_id
                """
            ),
            {
                "updated_by": ctx.actor_user_id,
                "device_id": issuance.device_id,
                "scheme_id": ctx.scheme_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE access.access_device_requests
                SET status = 'completed',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :request_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "request_id": request_uuid, "scheme_id": ctx.scheme_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, device_id, request_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_id, :request_id, 'device_marked_lost', CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_id": issuance.device_id,
                "request_id": request_uuid,
                "payload": _json({"reason": payload.note}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        if req["case_id"]:
            case_uuid = _parse_uuid(req["case_id"], label="case_id")
            await _insert_case_event(
                session,
                ctx,
                case_uuid=case_uuid,
                event_type="access_risk_event_created",
                payload={"request_id": request_id, "device_id": str(issuance.device_id), "reason": payload.note},
                correlation_id=correlation_id,
            )
            await _insert_audit_event(
                session,
                ctx,
                case_uuid=case_uuid,
                action="access.request.marked_lost",
                payload={"request_id": request_id, "device_id": str(issuance.device_id), "note": payload.note},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return await _fetch_request(session, ctx.scheme_id, request_uuid)


async def disable_request(*, building_id: str, request_id: str, current_user: dict, payload: AccessDisableRequest, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: disable_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    request_uuid = _parse_uuid(request_id, label="request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        req = await _fetch_request(session, ctx.scheme_id, request_uuid)
        issuance = (
            await session.execute(
                text(
                    """
                    SELECT device_id
                    FROM access.access_device_issuance
                    WHERE request_id = :request_id
                      AND scheme_id = :scheme_id
                    ORDER BY issued_at DESC
                    LIMIT 1
                    """
                ),
                {"request_id": request_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if issuance is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request has no issued device to disable.")
        await session.execute(
            text(
                """
                UPDATE access.access_devices
                SET status = 'deactivated',
                    current_holder_party_id = NULL,
                    deactivated_at = now(),
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :device_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "device_id": issuance.device_id, "scheme_id": ctx.scheme_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_deactivation
                    (tenant_id, scheme_id, device_id, request_id, deactivated_by, reason, external_reference, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_id, :request_id, :deactivated_by, :reason, :external_reference, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_id": issuance.device_id,
                "request_id": request_uuid,
                "deactivated_by": ctx.actor_user_id,
                "reason": payload.reason,
                "external_reference": payload.external_reference,
                "correlation_id": correlation_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE access.access_device_requests
                SET status = 'completed',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :request_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "request_id": request_uuid, "scheme_id": ctx.scheme_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, device_id, request_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_id, :request_id, 'device_disabled', CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_id": issuance.device_id,
                "request_id": request_uuid,
                "payload": _json({"reason": payload.reason, "external_reference": payload.external_reference}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        return await _fetch_request(session, ctx.scheme_id, request_uuid)


async def return_request(*, building_id: str, request_id: str, current_user: dict, payload: AccessReturnRequest, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: return_request
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    request_uuid = _parse_uuid(request_id, label="request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_request(session, ctx.scheme_id, request_uuid)
        issuance = (
            await session.execute(
                text(
                    """
                    SELECT id AS issuance_id, device_id
                    FROM access.access_device_issuance
                    WHERE request_id = :request_id
                      AND scheme_id = :scheme_id
                    ORDER BY issued_at DESC
                    LIMIT 1
                    """
                ),
                {"request_id": request_uuid, "scheme_id": ctx.scheme_id},
            )
        ).first()
        if issuance is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request has no issued device to return.")
        await session.execute(
            text(
                """
                UPDATE access.access_devices
                SET status = 'returned',
                    current_holder_party_id = NULL,
                    returned_at = now(),
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :device_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "device_id": issuance.device_id, "scheme_id": ctx.scheme_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_returns
                    (tenant_id, scheme_id, issuance_id, device_id, received_by_user_id, condition_note, refund_cents, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :issuance_id, :device_id, :received_by_user_id, :condition_note, :refund_cents, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "issuance_id": issuance.issuance_id,
                "device_id": issuance.device_id,
                "received_by_user_id": ctx.actor_user_id,
                "condition_note": payload.condition_note,
                "refund_cents": payload.refund_cents,
                "correlation_id": correlation_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE access.access_device_requests
                SET status = 'completed',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :request_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "request_id": request_uuid, "scheme_id": ctx.scheme_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO access.access_device_audit_events
                    (tenant_id, scheme_id, device_id, request_id, event_type, event_payload, actor_user_id, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :device_id, :request_id, 'device_returned', CAST(:payload AS jsonb), :actor_user_id, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "device_id": issuance.device_id,
                "request_id": request_uuid,
                "payload": _json({"condition_note": payload.condition_note, "refund_cents": payload.refund_cents}),
                "actor_user_id": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        return await _fetch_request(session, ctx.scheme_id, request_uuid)


async def get_request_audit(*, building_id: str, request_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: get_request_audit
    Path: backend/services/access_lifecycle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _assert_manager(ctx.role)
    request_uuid = _parse_uuid(request_id, label="request_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_request(session, ctx.scheme_id, request_uuid)
        result = await session.execute(
            text(
                """
                SELECT id AS event_id, event_type, actor_user_id, event_payload AS payload, created_at
                FROM access.access_device_audit_events
                WHERE request_id = :request_id
                  AND scheme_id = :scheme_id
                ORDER BY created_at DESC
                """
            ),
            {"request_id": request_uuid, "scheme_id": ctx.scheme_id},
        )
        rows = []
        for row in result.fetchall():
            data = _row_to_dict(row)
            data["event_id"] = str(data["event_id"])
            data["actor_user_id"] = str(data["actor_user_id"]) if data.get("actor_user_id") else None
            data["payload"] = data.get("payload") or {}
            rows.append(data)
        return rows
