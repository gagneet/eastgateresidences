"""Postgres-backed repairs, vendor, and recurring maintenance APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from models.ops_repairs import (
    GeneratedRecurringTaskResponse,
    RecurringTaskTemplateCreate,
    RecurringTaskTemplateResponse,
    ServiceProviderCreate,
    ServiceProviderResponse,
    ServiceProviderUpdate,
    ServiceRequestCreate,
    ServiceRequestResponse,
    VendorAssignmentCreate,
    VendorAssignmentResponse,
)
from services import ops_repairs_service
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="/ops", tags=["Ops Repairs"])



@router.get("/service-providers", response_model=list[ServiceProviderResponse])
async def list_service_providers(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: list_service_providers
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ops_repairs_service.list_service_providers(building_id=building_id, current_user=current_user)


@router.post("/service-providers", response_model=ServiceProviderResponse, status_code=201)
async def create_service_provider(
    payload: ServiceProviderCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: create_service_provider
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_repairs_service.create_service_provider(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.patch("/service-providers/{vendor_id}", response_model=ServiceProviderResponse)
async def update_service_provider(
    vendor_id: str,
    payload: ServiceProviderUpdate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: update_service_provider
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_repairs_service.update_service_provider(
        building_id=building_id,
        vendor_id=vendor_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/service-requests", response_model=list[ServiceRequestResponse])
async def list_service_requests(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: list_service_requests
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ops_repairs_service.list_service_requests(
        building_id=building_id,
        current_user=current_user,
        status_filter=status_filter,
    )


@router.post("/service-requests", response_model=ServiceRequestResponse, status_code=201)
async def create_service_request(
    payload: ServiceRequestCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: create_service_request
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_repairs_service.create_service_request(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/service-requests/{service_request_id}", response_model=ServiceRequestResponse)
async def get_service_request(
    service_request_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: get_service_request
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ops_repairs_service.get_service_request(
        building_id=building_id,
        service_request_id=service_request_id,
        current_user=current_user,
    )


@router.post("/service-requests/{service_request_id}/assign-vendor", response_model=VendorAssignmentResponse, status_code=201)
async def assign_vendor(
    service_request_id: str,
    payload: VendorAssignmentCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: assign_vendor
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_repairs_service.assign_vendor(
        building_id=building_id,
        service_request_id=service_request_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/service-requests/{service_request_id}/vendor-assignments", response_model=list[VendorAssignmentResponse])
async def list_vendor_assignments(
    service_request_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: list_vendor_assignments
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ops_repairs_service.list_vendor_assignments(
        building_id=building_id,
        service_request_id=service_request_id,
        current_user=current_user,
    )


@router.get("/recurring-task-templates", response_model=list[RecurringTaskTemplateResponse])
async def list_recurring_task_templates(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: list_recurring_task_templates
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ops_repairs_service.list_recurring_templates(building_id=building_id, current_user=current_user)


@router.post("/recurring-task-templates", response_model=RecurringTaskTemplateResponse, status_code=201)
async def create_recurring_task_template(
    payload: RecurringTaskTemplateCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: create_recurring_task_template
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_repairs_service.create_recurring_template(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/recurring-task-templates/{template_id}/generate-now", response_model=GeneratedRecurringTaskResponse)
async def generate_recurring_task(
    template_id: str,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: generate_recurring_task
    Path: backend/routers/ops_repairs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_repairs_service.generate_recurring_task(
        building_id=building_id,
        template_id=template_id,
        current_user=current_user,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
