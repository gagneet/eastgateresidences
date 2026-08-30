"""PG-backed access device lifecycle APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from models.access_lifecycle import (
    AccessAuditEvent,
    AccessDeviceCreate,
    AccessDeviceResponse,
    AccessDeviceTypeCreate,
    AccessDeviceTypeResponse,
    AccessDeviceTypeUpdate,
    AccessDisableRequest,
    AccessIssueRequest,
    AccessRequestAction,
    AccessRequestCreate,
    AccessRequestResponse,
    AccessReturnRequest,
)
from services import access_lifecycle_service
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="/access", tags=["Access Lifecycle"])



@router.get("/device-types", response_model=list[AccessDeviceTypeResponse])
async def list_device_types(
    include_inactive: bool = False,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Return the building's PG access-device catalog.

    Residents use the default active/requestable view. Settings editors pass
    include_inactive=true to manage archived device types from Building Settings.
    """
    return await access_lifecycle_service.list_device_types(
        building_id=building_id,
        current_user=current_user,
        include_inactive=include_inactive,
    )


@router.post("/device-types", response_model=AccessDeviceTypeResponse, status_code=201)
async def create_device_type(
    payload: AccessDeviceTypeCreate,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Create a building-scoped device type for request pricing/rules."""
    return await access_lifecycle_service.create_device_type(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
    )


@router.put("/device-types/{device_type_id}", response_model=AccessDeviceTypeResponse)
async def update_device_type(
    device_type_id: str,
    payload: AccessDeviceTypeUpdate,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Update request-facing device metadata without changing issued devices."""
    return await access_lifecycle_service.update_device_type(
        building_id=building_id,
        current_user=current_user,
        device_type_id=device_type_id,
        payload=payload,
    )


@router.delete("/device-types/{device_type_id}", response_model=AccessDeviceTypeResponse)
async def archive_device_type(
    device_type_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Soft-archive a device type so residents can no longer request it."""
    return await access_lifecycle_service.archive_device_type(
        building_id=building_id,
        current_user=current_user,
        device_type_id=device_type_id,
    )


@router.post("/devices", response_model=AccessDeviceResponse, status_code=201)
async def create_device(
    payload: AccessDeviceCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: create_device
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.create_device(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/devices", response_model=list[AccessDeviceResponse])
async def list_devices(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: list_devices
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await access_lifecycle_service.list_devices(building_id=building_id, current_user=current_user)


@router.get("/devices/{device_id}", response_model=AccessDeviceResponse)
async def get_device(
    device_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: get_device
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await access_lifecycle_service.get_device(building_id=building_id, device_id=device_id, current_user=current_user)


@router.post("/requests", response_model=AccessRequestResponse, status_code=201)
async def create_request(
    payload: AccessRequestCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: create_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.create_request(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/requests", response_model=list[AccessRequestResponse])
async def list_requests(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: list_requests
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await access_lifecycle_service.list_requests(building_id=building_id, current_user=current_user)


@router.post("/requests/{request_id}/approve", response_model=AccessRequestResponse)
async def approve_request(
    request_id: str,
    payload: AccessRequestAction,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: approve_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.approve_request(
        building_id=building_id,
        request_id=request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/requests/{request_id}/reject", response_model=AccessRequestResponse)
async def reject_request(
    request_id: str,
    payload: AccessRequestAction,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: reject_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.reject_request(
        building_id=building_id,
        request_id=request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/requests/{request_id}/issue", response_model=AccessRequestResponse)
async def issue_request(
    request_id: str,
    payload: AccessIssueRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: issue_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.issue_request(
        building_id=building_id,
        request_id=request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/requests/{request_id}/mark-lost", response_model=AccessRequestResponse)
async def mark_lost_request(
    request_id: str,
    payload: AccessRequestAction,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: mark_lost_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.mark_lost_request(
        building_id=building_id,
        request_id=request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/requests/{request_id}/disable", response_model=AccessRequestResponse)
async def disable_request(
    request_id: str,
    payload: AccessDisableRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: disable_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.disable_request(
        building_id=building_id,
        request_id=request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/requests/{request_id}/return", response_model=AccessRequestResponse)
async def return_request(
    request_id: str,
    payload: AccessReturnRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: return_request
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await access_lifecycle_service.return_request(
        building_id=building_id,
        request_id=request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/requests/{request_id}/audit", response_model=list[AccessAuditEvent])
async def get_request_audit(
    request_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("access_device_lifecycle_pg_enabled")),
):
    """Generated function header.

    Function: get_request_audit
    Path: backend/routers/access_lifecycle.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await access_lifecycle_service.get_request_audit(building_id=building_id, request_id=request_id, current_user=current_user)
