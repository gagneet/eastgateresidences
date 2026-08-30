"""Pydantic models for PG-backed access device lifecycle APIs."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AccessDeviceTypeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_key: str | None = Field(None, min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(None, max_length=1000)
    request_available: bool = True
    fee_cents: int = Field(0, ge=0)
    deposit_cents: int = Field(0, ge=0)
    replacement_lost_fee_cents: int = Field(0, ge=0)
    max_quantity: int = Field(1, ge=1, le=50)
    requires_approval: bool = True
    requires_return: bool = True
    effective_date: date | None = None
    is_active: bool = True


class AccessDeviceTypeCreate(AccessDeviceTypeBase):
    pass


class AccessDeviceTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_key: str | None = Field(None, min_length=1, max_length=80)
    name: str | None = Field(None, min_length=1, max_length=160)
    description: str | None = Field(None, max_length=1000)
    request_available: bool | None = None
    fee_cents: int | None = Field(None, ge=0)
    deposit_cents: int | None = Field(None, ge=0)
    replacement_lost_fee_cents: int | None = Field(None, ge=0)
    max_quantity: int | None = Field(None, ge=1, le=50)
    requires_approval: bool | None = None
    requires_return: bool | None = None
    effective_date: date | None = None
    is_active: bool | None = None


class AccessDeviceTypeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_type_id: str
    type_key: str
    name: str
    description: str | None = None
    request_available: bool
    fee_cents: int
    deposit_cents: int
    replacement_lost_fee_cents: int
    max_quantity: int
    requires_approval: bool
    requires_return: bool
    effective_date: date | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AccessDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_type_id: str = Field(..., min_length=1, max_length=120)
    procurement_batch_id: str | None = Field(None, max_length=120)
    serial_number: str | None = Field(None, max_length=255)
    inventory_code: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=2000)


class AccessDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_id: str
    device_type_id: str
    device_type_name: str | None = None
    inventory_code: str | None = None
    serial_number: str | None = None
    status: str
    current_holder_party_id: str | None = None
    issued_to_lot_id: str | None = None
    issued_at: datetime | None = None
    returned_at: datetime | None = None
    deactivated_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class AccessRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_type_id: str = Field(..., min_length=1, max_length=120)
    lot_id: str | None = Field(None, max_length=120)
    request_type: str = Field(..., min_length=1, max_length=40)
    reason: str | None = Field(None, max_length=2000)
    due_at: datetime | None = None


class AccessRequestAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, max_length=1000)


class AccessIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1, max_length=120)
    handoff_method: str | None = Field(None, max_length=80)
    acknowledgement_required: bool = True
    note: str | None = Field(None, max_length=1000)


class AccessReturnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_note: str | None = Field(None, max_length=1000)
    refund_cents: int = Field(0, ge=0)


class AccessDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)
    external_reference: str | None = Field(None, max_length=120)


class AccessRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str
    case_id: str | None = None
    device_type_id: str
    device_type_name: str | None = None
    requester_user_id: str | None = None
    requester_party_id: str | None = None
    lot_id: str | None = None
    request_type: str
    reason: str | None = None
    status: str
    approval_required: bool
    approval_request_id: str | None = None
    fee_cents: int
    deposit_cents: int
    requested_at: datetime
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AccessAuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    event_type: str
    actor_user_id: str | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime
