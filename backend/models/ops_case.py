"""Pydantic models for the Postgres-backed unified ops case APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OpsCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240)
    description: str | None = Field(None, max_length=5000)
    category: str = Field(..., min_length=1, max_length=80)
    priority: str = Field("normal", max_length=20)
    risk_level: str = Field("low", max_length=20)
    source_type: str | None = Field(None, max_length=80)
    source_id: str | None = Field(None, max_length=120)
    related_lot_ids: list[UUID] = Field(default_factory=list)
    related_owner_party_ids: list[UUID] = Field(default_factory=list)
    related_tenant_party_ids: list[UUID] = Field(default_factory=list)
    related_asset_ids: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    sla_policy_code: str | None = Field(None, max_length=120)
    approval_required: bool = False
    visibility_scope: str = Field("building", max_length=40)
    pii_classification: str = Field("internal", max_length=40)


class OpsCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=240)
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, min_length=1, max_length=80)
    priority: str | None = Field(None, max_length=20)
    risk_level: str | None = Field(None, max_length=20)
    source_type: str | None = Field(None, max_length=80)
    source_id: str | None = Field(None, max_length=120)
    related_lot_ids: list[UUID] | None = None
    related_owner_party_ids: list[UUID] | None = None
    related_tenant_party_ids: list[UUID] | None = None
    related_asset_ids: list[str] | None = None
    due_at: datetime | None = None
    sla_policy_code: str | None = Field(None, max_length=120)
    approval_required: bool | None = None
    visibility_scope: str | None = Field(None, max_length=40)
    pii_classification: str | None = Field(None, max_length=40)


class OpsCaseCommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=5000)
    is_internal: bool = True
    pii_classification: str = Field("internal", max_length=40)


class OpsCaseAssignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_to: UUID
    due_at: datetime | None = None
    note: str | None = Field(None, max_length=2000)


class OpsCaseStatusCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1, max_length=40)
    change_reason: str | None = Field(None, max_length=1000)
    comment: str | None = Field(None, max_length=2000)


class OpsCaseLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_type: str = Field(..., min_length=1, max_length=80)
    linked_case_id: UUID | None = None
    linked_entity_type: str | None = Field(None, max_length=80)
    linked_entity_id: str | None = Field(None, max_length=120)


class OpsCaseApprovalRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_code: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=2000)


class OpsCaseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    scheme_id: str
    title: str
    description: str | None = None
    category: str
    priority: str
    risk_level: str
    status: str
    source_type: str | None = None
    source_id: str | None = None
    related_lot_ids: list[str] = Field(default_factory=list)
    related_owner_party_ids: list[str] = Field(default_factory=list)
    related_tenant_party_ids: list[str] = Field(default_factory=list)
    related_asset_ids: list[str] = Field(default_factory=list)
    assigned_to: str | None = None
    assigned_to_name: str | None = None
    due_at: datetime | None = None
    sla_policy_code: str | None = None
    approval_required: bool = False
    approval_request_id: str | None = None
    approval_status: str | None = None
    visibility_scope: str
    pii_classification: str
    created_by: str | None = None
    created_by_name: str | None = None
    updated_by: str | None = None
    updated_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    comment_count: int = 0
    link_count: int = 0


class OpsCaseCommentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    case_id: str
    body: str
    is_internal: bool
    pii_classification: str
    created_by: str | None = None
    created_by_name: str | None = None
    created_at: datetime


class OpsCaseAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    case_id: str
    assigned_to: str
    assigned_to_name: str | None = None
    assigned_by: str | None = None
    due_at: datetime | None = None
    status: str
    created_at: datetime


class OpsCaseStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    from_status: str | None = None
    to_status: str
    change_reason: str | None = None
    changed_by: str | None = None
    changed_at: datetime


class OpsCaseLinkResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    case_id: str
    relationship_type: str
    linked_case_id: str | None = None
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    created_by: str | None = None
    created_at: datetime


class OpsCaseApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_request_id: str
    case_id: str
    approval_policy_id: str | None = None
    policy_code: str | None = None
    status: str
    requested_by: str | None = None
    requested_at: datetime
    required_roles: list[str] = Field(default_factory=list)


class OpsCaseTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry_type: str
    happened_at: datetime
    title: str
    actor_user_id: str | None = None
    actor_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class OpsCaseAuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    audit_event_id: str
    action: str
    actor_user_id: str | None = None
    actor_name: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    event_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
