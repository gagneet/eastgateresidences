# @featuretrace:sustainability — API surface for utility-bill imports, anomaly workflows, and sustainability planning.
# Layer: router
# Data flow: manager operations UI → /utilities/* and /sustainability/* → finance.utility_*, sustainability.*, ai_assist.ai_recommendations (building-scoped).
# Related: services/sustainability_service.py
"""PG-backed utility workflow and sustainability planning APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from models.sustainability_workflows import (
    SustainabilityAssessmentResponse,
    SustainabilityAssessmentRunRequest,
    SustainabilityProjectApprovalRequest,
    SustainabilityProjectApprovalResponse,
    SustainabilityProjectResponse,
    SustainabilityRecommendationResponse,
    UtilityAnomalyCaseResponse,
    UtilityAnomalyResponse,
    UtilityBillImportRequest,
    UtilityBillImportResponse,
    UtilityBillResponse,
)
from services import sustainability_service
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="", tags=["Utility Workflows", "Sustainability"])



@router.post("/utilities/bills/import", response_model=UtilityBillImportResponse, status_code=status.HTTP_201_CREATED)
async def import_utility_bills(
    payload: UtilityBillImportRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: import_utility_bills
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await sustainability_service.import_utility_bills(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/utilities/bills", response_model=list[UtilityBillResponse])
async def list_utility_bills(
    utility_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: list_utility_bills
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await sustainability_service.list_utility_bills(
        building_id=building_id,
        current_user=current_user,
        utility_type=utility_type,
        limit=limit,
        offset=offset,
    )


@router.get("/utilities/anomalies", response_model=list[UtilityAnomalyResponse])
async def list_utility_anomalies(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: list_utility_anomalies
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await sustainability_service.list_utility_anomalies(
        building_id=building_id,
        current_user=current_user,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post("/utilities/anomalies/{anomaly_id}/convert-to-case", response_model=UtilityAnomalyCaseResponse)
async def convert_anomaly_to_case(
    anomaly_id: str,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: convert_anomaly_to_case
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await sustainability_service.convert_anomaly_to_case(
        building_id=building_id,
        anomaly_id=anomaly_id,
        current_user=current_user,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/sustainability/assessments/run", response_model=SustainabilityAssessmentResponse, status_code=status.HTTP_201_CREATED)
async def run_sustainability_assessment(
    payload: SustainabilityAssessmentRunRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: run_sustainability_assessment
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await sustainability_service.run_sustainability_assessment(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/sustainability/recommendations", response_model=list[SustainabilityRecommendationResponse])
async def list_sustainability_recommendations(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: list_sustainability_recommendations
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await sustainability_service.list_sustainability_recommendations(
        building_id=building_id,
        current_user=current_user,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post("/sustainability/recommendations/{recommendation_id}/convert-to-project", response_model=SustainabilityProjectResponse)
async def convert_recommendation_to_project(
    recommendation_id: str,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: convert_recommendation_to_project
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await sustainability_service.convert_recommendation_to_project(
        building_id=building_id,
        recommendation_id=recommendation_id,
        current_user=current_user,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/sustainability/projects/{project_id}/submit-approval", response_model=SustainabilityProjectApprovalResponse)
async def submit_project_approval(
    project_id: str,
    payload: SustainabilityProjectApprovalRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("sustainability_assessments_pg_enabled")),
):
    """Generated function header.

    Function: submit_project_approval
    Path: backend/routers/utilities_workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await sustainability_service.submit_project_approval(
        building_id=building_id,
        project_id=project_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
