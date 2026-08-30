"""Pydantic models for Postgres-backed ops repairs, vendors, and recurring tasks."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(..., min_length=1, max_length=255)
    preferred_name: str | None = Field(None, max_length=255)
    primary_email: str | None = Field(None, max_length=255)
    primary_mobile: str | None = Field(None, max_length=50)
    abn: str | None = Field(None, max_length=50)
    trade_categories: list[str] = Field(default_factory=list)
    abn_status: str | None = Field(None, max_length=80)
    gst_registered: bool = False
    insurance_expiry: date | None = None
    licence_expiry: date | None = None
    preferred_payment_method: str | None = Field(None, max_length=80)
    metadata: dict = Field(default_factory=dict)


class ServiceProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = Field(None, min_length=1, max_length=255)
    preferred_name: str | None = Field(None, max_length=255)
    primary_email: str | None = Field(None, max_length=255)
    primary_mobile: str | None = Field(None, max_length=50)
    abn: str | None = Field(None, max_length=50)
    trade_categories: list[str] | None = None
    abn_status: str | None = Field(None, max_length=80)
    gst_registered: bool | None = None
    insurance_expiry: date | None = None
    licence_expiry: date | None = None
    preferred_payment_method: str | None = Field(None, max_length=80)
    status: str | None = Field(None, max_length=30)
    metadata: dict | None = None


class ServiceProviderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vendor_id: str
    party_id: str
    legal_name: str
    preferred_name: str | None = None
    primary_email: str | None = None
    primary_mobile: str | None = None
    abn: str | None = None
    trade_categories: list[str] = Field(default_factory=list)
    abn_status: str | None = None
    gst_registered: bool = False
    insurance_expiry: date | None = None
    licence_expiry: date | None = None
    preferred_payment_method: str | None = None
    status: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class ServiceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1, max_length=120)
    request_kind: str = Field(..., min_length=1, max_length=80)
    lot_id: str | None = Field(None, max_length=120)
    asset_reference: str | None = Field(None, max_length=255)
    disturbance_level: str = Field("low", max_length=20)
    preferred_contact_method: str | None = Field(None, max_length=50)


class ServiceRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    service_request_id: str
    case_id: str | None = None
    request_kind: str
    status: str
    lot_id: str | None = None
    asset_reference: str | None = None
    current_vendor_id: str | None = None
    current_vendor_name: str | None = None
    disturbance_level: str
    preferred_contact_method: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class VendorAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: str = Field(..., min_length=1, max_length=120)
    response_due_at: datetime | None = None
    response_note: str | None = Field(None, max_length=1000)


class VendorAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assignment_id: str
    service_request_id: str | None = None
    vendor_id: str
    vendor_name: str | None = None
    assignment_status: str
    assigned_by: str | None = None
    response_due_at: datetime | None = None
    responded_at: datetime | None = None
    response_note: str | None = None
    created_at: datetime
    updated_at: datetime


class RecurringTaskTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240)
    description: str | None = Field(None, max_length=5000)
    category: str = Field(..., min_length=1, max_length=80)
    cadence_unit: str = Field(..., max_length=20)
    cadence_interval: int = Field(1, ge=1, le=365)
    priority: str = Field("normal", max_length=20)
    default_assignee: str | None = Field(None, max_length=120)
    vendor_id: str | None = Field(None, max_length=120)
    next_due_at: datetime | None = None
    is_active: bool = True


class RecurringTaskTemplateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    template_id: str
    title: str
    description: str | None = None
    category: str
    cadence_unit: str
    cadence_interval: int
    priority: str
    default_assignee: str | None = None
    vendor_id: str | None = None
    vendor_name: str | None = None
    next_due_at: datetime | None = None
    last_run_at: datetime | None = None
    is_active: bool
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class GeneratedRecurringTaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    template_id: str
    case_id: str
    service_request_id: str
    next_due_at: datetime | None = None
