"""Pydantic models for PostgreSQL-backed AI review workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BuildingAssessmentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_type: str = Field("building_health", min_length=1, max_length=80)
    summary_hint: str | None = Field(None, max_length=2000)


class AIRecommendationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_reason: str | None = Field(None, max_length=2000)


class AIRecommendationApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_code: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=2000)


class AIRiskScoreResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    risk_score_id: str
    subject_type: str
    subject_id: str
    risk_category: str
    score: float
    score_band: str
    rationale: str | None = None
    created_at: datetime


class AIRecommendationEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_id: str
    recommendation_id: str
    evidence_type: str
    source_type: str
    source_id: str
    summary: str | None = None
    excerpt: str | None = None
    created_at: datetime
    source_system: str


class AIRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendation_id: str
    assessment_id: str
    case_id: str | None = None
    recommendation_type: str
    title: str
    recommendation_text: str
    status: str
    risk_level: str
    confidence_score: float | None = None
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
    approval_request_id: str | None = None
    approval_status: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime
    evidence_count: int = 0


class BuildingAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assessment_id: str
    case_id: str | None = None
    assessment_type: str
    subject_type: str
    subject_id: str
    summary: str | None = None
    status: str
    model_name: str | None = None
    prompt_version: str | None = None
    risk_level: str
    confidence_score: float | None = None
    pii_redacted: bool = True
    approval_required: bool = False
    approval_request_id: str | None = None
    approval_status: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime
    risk_scores: list[AIRiskScoreResponse] = Field(default_factory=list)
    recommendations: list[AIRecommendationResponse] = Field(default_factory=list)


class AIRecommendationApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_request_id: str
    recommendation_id: str
    policy_code: str | None = None
    status: str
    requested_by: str | None = None
    requested_at: datetime
    required_roles: list[str] = Field(default_factory=list)


class AIRecommendationCaseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendation_id: str
    case_id: str
    case_status: str
    recommendation_status: str
