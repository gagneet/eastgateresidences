"""Pydantic models for utility anomaly and sustainability workflows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UtilityReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: UUID | None = None
    meter_identifier: str | None = Field(None, max_length=160)
    asset_reference: str | None = Field(None, max_length=160)
    reading_taken_at: datetime
    reading_value: float
    reading_unit: str = Field(..., min_length=1, max_length=40)
    source_type: str = Field("import", min_length=1, max_length=40)


class UtilityBillImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: UUID | None = None
    utility_type: str = Field(..., min_length=1, max_length=80)
    billing_period_start: date
    billing_period_end: date
    usage_quantity: float | None = None
    usage_unit: str | None = Field(None, max_length=40)
    total_amount_cents: int = Field(..., ge=0)
    due_date: date | None = None
    payment_status: str = Field("unpaid", max_length=40)
    provider_name: str | None = Field(None, max_length=240)
    provider_reference: str | None = Field(None, max_length=240)
    document_id: str | None = Field(None, max_length=120)
    readings: list[UtilityReadingInput] = Field(default_factory=list)


class UtilityBillImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bills: list[UtilityBillImportItem] = Field(..., min_length=1, max_length=200)


class SustainabilityAssessmentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(None, max_length=2000)
    emissions_baseline_kg: float | None = None
    solar_readiness_level: str | None = Field(None, max_length=80)
    water_efficiency_level: str | None = Field(None, max_length=80)
    lighting_efficiency_level: str | None = Field(None, max_length=80)
    roof_condition: str | None = Field(None, max_length=80)
    switchboard_capacity: str | None = Field(None, max_length=80)
    metering_readiness: str | None = Field(None, max_length=80)
    shading_rating: str | None = Field(None, max_length=80)
    estimated_system_kw: float | None = None


class SustainabilityProjectApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_code: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=2000)


class UtilityReadingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reading_id: str
    utility_bill_id: str | None = None
    lot_id: str | None = None
    utility_type: str
    meter_identifier: str | None = None
    asset_reference: str | None = None
    reading_taken_at: datetime
    reading_value: float
    reading_unit: str
    source_type: str


class UtilityBillResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bill_id: str
    lot_id: str | None = None
    utility_type: str
    billing_period_start: date
    billing_period_end: date
    usage_quantity: float | None = None
    usage_unit: str | None = None
    total_amount_cents: int
    due_date: date | None = None
    payment_status: str
    provider_name: str | None = None
    provider_reference: str | None = None
    document_id: str | None = None
    receipt_id: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime
    reading_count: int = 0
    readings: list[UtilityReadingResponse] = Field(default_factory=list)


class UtilityAnomalyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    anomaly_id: str
    utility_bill_id: str | None = None
    reading_id: str | None = None
    anomaly_type: str
    severity: str
    status: str
    title: str
    description: str | None = None
    detected_at: datetime
    assigned_to: str | None = None
    approval_required: bool = False
    approval_request_id: str | None = None
    approval_status: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class UtilityBillImportResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    imported_count: int
    anomaly_count: int
    bills: list[UtilityBillResponse] = Field(default_factory=list)
    anomalies: list[UtilityAnomalyResponse] = Field(default_factory=list)


class UtilityAnomalyCaseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    anomaly_id: str
    case_id: str
    case_status: str
    anomaly_status: str


class SustainabilityRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendation_id: str
    assessment_id: str
    recommendation_type: str
    title: str
    recommendation_text: str
    status: str
    risk_level: str
    confidence_score: float | None = None
    approval_required: bool = False
    approval_request_id: str | None = None
    approval_status: str | None = None
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    problem_statement: str | None = None
    estimated_cost_range: str | None = None
    likely_owner_impact: str | None = None
    likely_disruption: str | None = None
    compliance_considerations: str | None = None
    suggested_approval_path: str | None = None
    suggested_communication_plan: str | None = None
    evidence_count: int = 0
    created_at: datetime
    updated_at: datetime


class SustainabilityAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assessment_id: str
    profile_id: str
    overall_score: float | None = None
    emissions_baseline_kg: float | None = None
    solar_readiness_level: str | None = None
    water_efficiency_level: str | None = None
    lighting_efficiency_level: str | None = None
    notes: str | None = None
    recommendations: list[SustainabilityRecommendationResponse] = Field(default_factory=list)


class SustainabilityProjectResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str
    profile_id: str | None = None
    project_type: str
    title: str
    description: str | None = None
    status: str
    capex_estimate_cents: int | None = None
    payback_months: int | None = None
    emissions_savings_kg: float | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime
    approval_request_id: str | None = None
    approval_status: str | None = None


class SustainabilityProjectApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_request_id: str
    project_id: str
    policy_code: str | None = None
    status: str
    requested_by: str | None = None
    requested_at: datetime
    required_roles: list[str] = Field(default_factory=list)
