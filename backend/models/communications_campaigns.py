"""Pydantic models for PostgreSQL-backed communications campaigns and newsletters."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CommunicationDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    newsletter_id: str | None = Field(None, max_length=120)
    campaign_id: str | None = Field(None, max_length=120)
    title: str = Field(..., min_length=1, max_length=255)
    subject: str | None = Field(None, max_length=255)
    body: str = Field(..., min_length=1)
    draft_kind: str = Field(..., min_length=1, max_length=80)
    is_template: bool = False
    risk_level: str = Field("low", max_length=20)
    pii_redacted: bool = False


class CommunicationDraftResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    draft_id: str
    newsletter_id: str | None = None
    campaign_id: str | None = None
    title: str
    subject: str | None = None
    body: str
    draft_kind: str
    is_template: bool
    status: str
    risk_level: str
    pii_redacted: bool
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class NewsletterSectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_type: str = Field(..., min_length=1, max_length=80)
    title: str | None = Field(None, max_length=255)
    body: str = Field(..., min_length=1)
    sort_order: int | None = Field(None, ge=0, le=1000)


class NewsletterSectionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_id: str
    section_type: str
    title: str | None = None
    body: str
    sort_order: int


class NewsletterRecipientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_id: str | None = Field(None, max_length=120)
    recipient_email: str = Field(..., min_length=3, max_length=255)
    recipient_type: str = Field(..., min_length=1, max_length=80)


class NewsletterRecipientResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recipient_id: str
    party_id: str | None = None
    recipient_email: str
    recipient_type: str
    delivery_status: str
    delivered_at: datetime | None = None
    opened_at: datetime | None = None
    acknowledged_at: datetime | None = None


class NewsletterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    summary: str | None = Field(None, max_length=1000)
    approval_required: bool = False
    sections: list[NewsletterSectionInput] = Field(default_factory=list)
    recipients: list[NewsletterRecipientInput] = Field(default_factory=list)


class NewsletterResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    newsletter_id: str
    title: str
    subject: str
    summary: str | None = None
    status: str
    scheduled_for: datetime | None = None
    sent_at: datetime | None = None
    approval_required: bool
    approval_request_id: str | None = None
    approval_status: str | None = None
    section_count: int = 0
    recipient_count: int = 0
    acknowledgement_count: int = 0
    delivery_event_count: int = 0
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class NewsletterDetailResponse(NewsletterResponse):
    sections: list[NewsletterSectionResponse] = Field(default_factory=list)
    recipients: list[NewsletterRecipientResponse] = Field(default_factory=list)


class CampaignAudienceSegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_type: str = Field(..., min_length=1, max_length=80)
    segment_definition: dict = Field(default_factory=dict)
    estimated_recipient_count: int | None = Field(None, ge=0)


class CampaignAudienceSegmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    segment_id: str
    segment_type: str
    segment_definition: dict = Field(default_factory=dict)
    estimated_recipient_count: int | None = None


class CommunicationCampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_type: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=255)
    subject: str | None = Field(None, max_length=255)
    body: str | None = None
    audience_scope: str = Field(..., min_length=1, max_length=80)
    approval_required: bool = False
    audience_segments: list[CampaignAudienceSegmentInput] = Field(default_factory=list)


class CommunicationCampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_type: str | None = Field(None, min_length=1, max_length=80)
    title: str | None = Field(None, min_length=1, max_length=255)
    subject: str | None = Field(None, max_length=255)
    body: str | None = None
    audience_scope: str | None = Field(None, min_length=1, max_length=80)
    approval_required: bool | None = None
    audience_segments: list[CampaignAudienceSegmentInput] | None = None


class CommunicationCampaignResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    campaign_id: str
    campaign_type: str
    title: str
    subject: str | None = None
    body: str | None = None
    status: str
    audience_scope: str
    scheduled_for: datetime | None = None
    sent_at: datetime | None = None
    approval_required: bool
    approval_request_id: str | None = None
    approval_status: str | None = None
    audience_segment_count: int = 0
    acknowledgement_count: int = 0
    delivery_event_count: int = 0
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CommunicationCampaignDetailResponse(CommunicationCampaignResponse):
    audience_segments: list[CampaignAudienceSegmentResponse] = Field(default_factory=list)


class CommunicationScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_for: datetime


class CommunicationPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    campaign_id: str
    title: str
    subject: str | None = None
    body: str | None = None
    audience_scope: str
    approval_required: bool
    needs_human_approval: bool
    preview_channels: list[str] = Field(default_factory=lambda: ["email"])
    audience_segments: list[CampaignAudienceSegmentResponse] = Field(default_factory=list)


class CommunicationSendResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    campaign_id: str
    status: str
    sent_at: datetime

class CommunicationApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_code: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=1000)


class CommunicationApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_request_id: str
    policy_code: str | None = None
    required_roles: list[str] = Field(default_factory=list)
    status: str
    requested_by: str | None = None
    requested_at: datetime


class CommunicationDeliveryEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_party_id: str | None = Field(None, max_length=120)
    recipient_email: str | None = Field(None, max_length=255)
    channel: str = Field(..., min_length=1, max_length=40)
    provider_message_id: str | None = Field(None, max_length=255)
    event_type: str = Field(..., min_length=1, max_length=80)
    occurred_at: datetime | None = None
    event_payload: dict = Field(default_factory=dict)


class CommunicationDeliveryEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    draft_id: str | None = None
    newsletter_id: str | None = None
    campaign_id: str | None = None
    recipient_party_id: str | None = None
    recipient_email: str | None = None
    channel: str
    provider_message_id: str | None = None
    event_type: str
    occurred_at: datetime
    event_payload: dict = Field(default_factory=dict)


class CommunicationAcknowledgementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_id: str = Field(..., min_length=1, max_length=120)
    acknowledgement_type: str = Field(..., min_length=1, max_length=80)
    response_text: str | None = Field(None, max_length=2000)


class CommunicationAcknowledgementResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    acknowledgement_id: str
    newsletter_id: str | None = None
    campaign_id: str | None = None
    party_id: str
    acknowledgement_type: str
    response_text: str | None = None
    acknowledged_at: datetime
