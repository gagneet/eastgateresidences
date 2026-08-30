# @featuretrace:ai-review — API surface for building-health assessments and AI recommendation review.
# Layer: router
# Data flow: manager review UI → /ai/* → ai_assist.*, core.approval_requests, ops.cases (building-scoped).
# Related: services/ai_review_service.py
"""PG-backed AI building assessment and recommendation review APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from models.ai_review import (
    AIRecommendationApprovalRequest,
    AIRecommendationApprovalResponse,
    AIRecommendationCaseResponse,
    AIRecommendationDecisionRequest,
    AIRecommendationEvidenceResponse,
    AIRecommendationResponse,
    BuildingAssessmentResponse,
    BuildingAssessmentRunRequest,
)
from services import ai_review_service
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="/ai", tags=["AI Review"])



@router.post("/building-assessments/run", response_model=BuildingAssessmentResponse, status_code=status.HTTP_201_CREATED)
async def run_building_assessment(
    payload: BuildingAssessmentRunRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: run_building_assessment
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ai_review_service.run_building_assessment(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/building-assessments", response_model=list[BuildingAssessmentResponse])
async def list_building_assessments(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: list_building_assessments
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ai_review_service.list_building_assessments(
        building_id=building_id,
        current_user=current_user,
        limit=limit,
        offset=offset,
    )


@router.get("/building-assessments/{assessment_id}", response_model=BuildingAssessmentResponse)
async def get_building_assessment(
    assessment_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: get_building_assessment
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ai_review_service.get_building_assessment(
        building_id=building_id,
        assessment_id=assessment_id,
        current_user=current_user,
    )


@router.get("/recommendations", response_model=list[AIRecommendationResponse])
async def list_recommendations(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    recommendation_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: list_recommendations
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ai_review_service.list_recommendations(
        building_id=building_id,
        current_user=current_user,
        status_filter=status_filter,
        recommendation_type=recommendation_type,
        limit=limit,
        offset=offset,
    )


@router.post("/recommendations/{recommendation_id}/accept", response_model=AIRecommendationResponse)
async def accept_recommendation(
    recommendation_id: str,
    payload: AIRecommendationDecisionRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: accept_recommendation
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ai_review_service.accept_recommendation(
        building_id=building_id,
        recommendation_id=recommendation_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/recommendations/{recommendation_id}/reject", response_model=AIRecommendationResponse)
async def reject_recommendation(
    recommendation_id: str,
    payload: AIRecommendationDecisionRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: reject_recommendation
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ai_review_service.reject_recommendation(
        building_id=building_id,
        recommendation_id=recommendation_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/recommendations/{recommendation_id}/convert-to-case", response_model=AIRecommendationCaseResponse)
async def convert_recommendation_to_case(
    recommendation_id: str,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: convert_recommendation_to_case
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ai_review_service.convert_recommendation_to_case(
        building_id=building_id,
        recommendation_id=recommendation_id,
        current_user=current_user,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/recommendations/{recommendation_id}/request-approval", response_model=AIRecommendationApprovalResponse)
async def request_recommendation_approval(
    recommendation_id: str,
    payload: AIRecommendationApprovalRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: request_recommendation_approval
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await ai_review_service.request_recommendation_approval(
        building_id=building_id,
        recommendation_id=recommendation_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/recommendations/{recommendation_id}/evidence", response_model=list[AIRecommendationEvidenceResponse])
async def get_recommendation_evidence(
    recommendation_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("ai_review_panel_enabled")),
):
    """Generated function header.

    Function: get_recommendation_evidence
    Path: backend/routers/ai_review.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await ai_review_service.get_recommendation_evidence(
        building_id=building_id,
        recommendation_id=recommendation_id,
        current_user=current_user,
    )
