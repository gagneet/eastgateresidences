"""
Powerhouse conversation, inbox, and workflow shell models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ParticipantRef(BaseModel):
    user_id: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)


class AttachmentMetadata(BaseModel):
    file_name: str
    mime_type: str
    size_bytes: int = Field(..., ge=0)
    storage_key: str | None = None


class LinkedEntityRef(BaseModel):
    entity_type: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)


class AIPlaceholderPayload(BaseModel):
    summary: str | None = None
    suggested_next_actions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human_approval: bool = True
    model_version: str = "placeholder-v1"


class ConversationThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(..., min_length=3, max_length=240)
    source_channel: Literal[
        "email",
        "portal_message",
        "internal_note",
        "maintenance",
        "levy",
        "meeting",
        "compliance",
        "document_request",
        "vendor",
    ] = "portal_message"
    body: str = Field(..., min_length=1, max_length=8000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    visibility: Literal["building_public", "participants_only", "internal_only"] = "participants_only"
    participant_ids: list[str] = Field(default_factory=list)
    watcher_ids: list[str] = Field(default_factory=list)
    linked_entity: LinkedEntityRef | None = None
    unit_id: str | None = None
    lot_id: str | None = None
    source_external_id: str | None = None
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    assigned_to: str | None = None
    sla_due_at: datetime | None = None
    is_test_data: bool = False


class ConversationThreadStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "in_progress", "waiting_on_owner", "waiting_on_vendor", "resolved", "closed", "archived"]
    is_test_data: bool = False


class ConversationThreadAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_to: str = Field(..., min_length=1)


class ConversationThreadSlaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sla_due_at: datetime | None = None


class ConversationMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=8000)
    message_type: Literal["message", "internal_note"] = "message"
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    is_test_data: bool = False


class ConversationWatcherUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watcher_id: str = Field(..., min_length=1)


class ConversationParticipantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_id: str = Field(..., min_length=1)


class ConversationLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)


class ConvertThreadToWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_template_key: str = Field(..., min_length=1)
    reason: str = Field(default="Converted from conversation thread", max_length=500)


class ConvertMessageToWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_template_key: str = Field(..., min_length=1)
    reason: str = Field(default="Converted from conversation message", max_length=500)


class InboxConfigUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbox_name: str = Field(..., min_length=2, max_length=120)
    address: str = Field(..., min_length=3, max_length=240)
    provider_key: str = Field(default="mock", min_length=1, max_length=80)
    enabled: bool = True
    allowed_roles: list[str] = Field(default_factory=list)


class InboundEmailEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(..., min_length=1, max_length=255)
    references: list[str] = Field(default_factory=list)
    subject: str = Field(..., min_length=1, max_length=240)
    from_email: str = Field(..., min_length=3, max_length=320)
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    text_body: str = Field(default="", max_length=12000)
    html_body: str | None = None
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    source_external_id: str | None = None


class OutboundDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(..., min_length=1, max_length=240)
    body: str = Field(..., min_length=1, max_length=12000)
    recipients: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    thread_id: str | None = None


class WorkflowInstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., min_length=1, max_length=240)
    linked_entity: LinkedEntityRef | None = None
    initial_payload: dict[str, Any] = Field(default_factory=dict)
    is_test_data: bool = False


class WorkflowEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(..., min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    is_test_data: bool = False


class WorkflowStepCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str = Field(..., min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    is_test_data: bool = False


class WorkflowAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_user_id: str = Field(..., min_length=1)
    step_key: str | None = Field(default=None, max_length=120)
    is_test_data: bool = False


class AutomationRuleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_key: str = Field(..., min_length=1, max_length=120)
    dry_run: bool = True
    is_test_data: bool = False
