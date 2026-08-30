"""Unified ops case APIs backed by PostgreSQL `ops.*` tables."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from models.ops_case import (
    OpsCaseApprovalRequestCreate,
    OpsCaseApprovalResponse,
    OpsCaseAssignCreate,
    OpsCaseAssignmentResponse,
    OpsCaseAuditEvent,
    OpsCaseCommentCreate,
    OpsCaseCommentResponse,
    OpsCaseCreate,
    OpsCaseLinkCreate,
    OpsCaseLinkResponse,
    OpsCaseResponse,
    OpsCaseStatusCreate,
    OpsCaseStatusResponse,
    OpsCaseTimelineEntry,
    OpsCaseUpdate,
)
from models.user import UserRole
from services import ops_case_service
from utils.auth import effective_role, get_current_building
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="/ops/cases", tags=["Ops Cases"])

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.EC_MEMBER,
}

_CREATE_ROLES = _MANAGER_ROLES | {
    UserRole.OWNER,
    UserRole.TENANT,
    UserRole.SERVICE_PROVIDER,
    UserRole.REAL_ESTATE_AGENT,
}


def _ensure_role(current_user: dict, allowed_roles: set[str], detail: str) -> None:
    """Generated function header.

    Function: _ensure_role
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)



@router.post("/", response_model=OpsCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_ops_case(
    payload: OpsCaseCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: create_ops_case
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to create cases.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.create_case(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/", response_model=list[OpsCaseResponse])
async def list_ops_cases(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    category: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: list_ops_cases
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to list cases.")
    return await ops_case_service.list_cases(
        building_id=building_id,
        current_user=current_user,
        status_filter=status_filter,
        category=category,
        priority=priority,
        assigned_to=assigned_to,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/{case_id}", response_model=OpsCaseResponse)
async def get_ops_case(
    case_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: get_ops_case
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to view cases.")
    return await ops_case_service.get_case(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
    )


@router.patch("/{case_id}", response_model=OpsCaseResponse)
async def update_ops_case(
    case_id: str,
    payload: OpsCaseUpdate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: update_ops_case
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to update cases.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.update_case(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/{case_id}/comments", response_model=OpsCaseCommentResponse, status_code=status.HTTP_201_CREATED)
async def add_ops_case_comment(
    case_id: str,
    payload: OpsCaseCommentCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: add_ops_case_comment
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to comment on cases.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.add_case_comment(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/{case_id}/assign", response_model=OpsCaseAssignmentResponse, status_code=status.HTTP_201_CREATED)
async def assign_ops_case(
    case_id: str,
    payload: OpsCaseAssignCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: assign_ops_case
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _MANAGER_ROLES, "Only managers can assign cases.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.assign_case(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/{case_id}/status", response_model=OpsCaseStatusResponse)
async def change_ops_case_status(
    case_id: str,
    payload: OpsCaseStatusCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: change_ops_case_status
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to change case status.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.change_case_status(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/{case_id}/link", response_model=OpsCaseLinkResponse, status_code=status.HTTP_201_CREATED)
async def link_ops_case(
    case_id: str,
    payload: OpsCaseLinkCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: link_ops_case
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _MANAGER_ROLES, "Only managers can link cases.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.link_case(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/{case_id}/request-approval", response_model=OpsCaseApprovalResponse)
async def request_ops_case_approval(
    case_id: str,
    payload: OpsCaseApprovalRequestCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: request_ops_case_approval
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _MANAGER_ROLES, "Only managers can request case approvals.")
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ops_case_service.request_case_approval(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/{case_id}/timeline", response_model=list[OpsCaseTimelineEntry])
async def get_ops_case_timeline(
    case_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: get_ops_case_timeline
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _CREATE_ROLES, "You do not have access to view case timelines.")
    return await ops_case_service.get_case_timeline(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
    )


@router.get("/{case_id}/audit", response_model=list[OpsCaseAuditEvent])
async def get_ops_case_audit(
    case_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: get_ops_case_audit
    Path: backend/routers/ops_cases.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_role(current_user, _MANAGER_ROLES, "Only managers can view case audit events.")
    return await ops_case_service.get_case_audit(
        building_id=building_id,
        case_id=case_id,
        current_user=current_user,
    )
