"""Pydantic models for inbound-email intake and reviewable queue actions."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InboundEmailAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(..., min_length=1, max_length=255)
    content_type: str | None = Field(None, max_length=120)
    storage_key: str | None = Field(None, max_length=500)
    size_bytes: int | None = Field(None, ge=0)


class InboundEmailCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1, max_length=80)
    message_id: str = Field(..., min_length=1, max_length=255)
    from_email: str = Field(..., min_length=3, max_length=255)
    from_name: str | None = Field(None, max_length=255)
    subject: str = Field(..., min_length=1, max_length=500)
    text_body: str | None = Field(None, max_length=20000)
    html_body: str | None = Field(None, max_length=50000)
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    attachments: list[InboundEmailAttachment] = Field(default_factory=list)
    received_at: datetime | None = None


class IntakeQueueItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intake_id: str
    case_id: str
    scheme_id: str
    status: str
    category: str
    priority: str
    risk_level: str
    title: str
    description: str | None = None
    sender_email: str | None = None
    sender_name: str | None = None
    received_at: datetime
    classification_confidence: float | None = None
    classification_reason: str | None = None
    needs_review: bool = False
    duplicate_candidate_case_id: str | None = None
    duplicate_of_case_id: str | None = None
    provider: str | None = None
    message_id: str | None = None
    recipient_emails: list[str] = Field(default_factory=list)
    attachment_count: int = 0
    created_at: datetime
    updated_at: datetime


class IntakeClassificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=1, max_length=80)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=1000)
    priority: str = Field("normal", max_length=20)
    risk_level: str = Field("low", max_length=20)


class IntakeConvertToCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: str | None = Field(None, max_length=80)
    disturbance_level: str = Field("low", max_length=20)
    preferred_contact_method: str | None = Field(None, max_length=50)


class IntakeConvertToCaseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    service_request_id: str
    request_kind: str
    status: str


class IntakeMarkDuplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duplicate_of_case_id: str = Field(..., min_length=1, max_length=120)
    note: str | None = Field(None, max_length=1000)


class IntakeRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class IntakeRedactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(None, max_length=500)
    body: str | None = Field(None, max_length=20000)


class IntakeAuditEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry_type: str
    happened_at: datetime
    action: str
    actor_user_id: str | None = None
    actor_name: str | None = None
    payload: dict = Field(default_factory=dict)
