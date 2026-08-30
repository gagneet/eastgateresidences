"""Inbound email intake APIs backed by the unified ops case layer."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from models.email_intake import (
    InboundEmailCreate,
    IntakeAuditEntry,
    IntakeClassificationUpdate,
    IntakeConvertToCaseRequest,
    IntakeConvertToCaseResponse,
    IntakeMarkDuplicateRequest,
    IntakeQueueItem,
    IntakeRedactRequest,
    IntakeRejectRequest,
)
from models.user import UserRole
from services import email_intake_service
from utils.auth import effective_role, get_current_building, get_current_user
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="/communications", tags=["Communications Intake"])

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.EC_MEMBER,
}


def _manager_only(current_user: dict) -> None:
    """Generated function header.

    Function: _manager_only
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager access required.")



@router.post("/inbound/email", response_model=IntakeQueueItem, status_code=201)
async def receive_inbound_email(payload: InboundEmailCreate, request: Request):
    """Generated function header.

    Function: receive_inbound_email
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw_body = await request.body()
    email_intake_service.verify_inbound_email_signature(raw_body, request.headers.get("X-StrataOS-Signature"))
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await email_intake_service.create_inbound_email_case(
        payload,
        header_building_id=request.headers.get("X-Building-ID"),
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/intake-queue", response_model=list[IntakeQueueItem])
async def get_intake_queue(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: get_intake_queue
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    return await email_intake_service.list_intake_queue(
        building_id=building_id,
        current_user=current_user,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post("/intake/{intake_id}/classify", response_model=IntakeQueueItem)
async def classify_intake_item(
    intake_id: str,
    payload: IntakeClassificationUpdate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: classify_intake_item
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await email_intake_service.classify_intake(
        building_id=building_id,
        intake_id=intake_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/intake/{intake_id}/convert-to-case", response_model=IntakeConvertToCaseResponse)
async def convert_intake_item(
    intake_id: str,
    payload: IntakeConvertToCaseRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: convert_intake_item
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await email_intake_service.convert_intake_to_case(
        building_id=building_id,
        intake_id=intake_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/intake/{intake_id}/mark-duplicate", response_model=IntakeQueueItem)
async def mark_intake_duplicate(
    intake_id: str,
    payload: IntakeMarkDuplicateRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: mark_intake_duplicate
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await email_intake_service.mark_intake_duplicate(
        building_id=building_id,
        intake_id=intake_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/intake/{intake_id}/reject", response_model=IntakeQueueItem)
async def reject_intake_item(
    intake_id: str,
    payload: IntakeRejectRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: reject_intake_item
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await email_intake_service.reject_intake(
        building_id=building_id,
        intake_id=intake_id,
        current_user=current_user,
        reason=payload.reason,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/intake/{intake_id}/redact", response_model=IntakeQueueItem)
async def redact_intake_item(
    intake_id: str,
    payload: IntakeRedactRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: redact_intake_item
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await email_intake_service.redact_intake(
        building_id=building_id,
        intake_id=intake_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/intake/{intake_id}/audit", response_model=list[IntakeAuditEntry])
async def get_intake_audit(
    intake_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ops_case_management_pg_enabled")),
):
    """Generated function header.

    Function: get_intake_audit
    Path: backend/routers/communications_intake.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _manager_only(current_user)
    return await email_intake_service.get_intake_audit(
        building_id=building_id,
        intake_id=intake_id,
        current_user=current_user,
    )
