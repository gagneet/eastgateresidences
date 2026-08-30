# @featuretrace:cutover-control-plane — Pydantic models and enums for the cutover control plane.
# Layer: model
# Data flow: cutover admin API request/response ↔ cutover_status_service ↔ core.domain_cutover_status (building-scoped).
# Related: backend/services/cutover_status_service.py
#          backend/routers/cutover_admin.py
#          docs/architecture/source-of-truth-matrix.md

"""Pydantic models, enums, and response schemas for the cutover control plane."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CutoverMode(str, Enum):
    """Lifecycle stages for a domain's source-of-truth."""

    mongo_primary = "mongo_primary"
    postgres_shadow = "postgres_shadow"
    postgres_read = "postgres_read"
    postgres_write = "postgres_write"
    mongo_archive = "mongo_archive"
    disabled = "disabled"


# Strict forward-only transition table.
# Rollback always goes back one step in the same sequence.
VALID_FORWARD_TRANSITIONS: dict[CutoverMode, CutoverMode] = {
    CutoverMode.mongo_primary: CutoverMode.postgres_shadow,
    CutoverMode.postgres_shadow: CutoverMode.postgres_read,
    CutoverMode.postgres_read: CutoverMode.postgres_write,
    CutoverMode.postgres_write: CutoverMode.mongo_archive,
}

VALID_ROLLBACK_TRANSITIONS: dict[CutoverMode, CutoverMode] = {
    CutoverMode.postgres_shadow: CutoverMode.mongo_primary,
    CutoverMode.postgres_read: CutoverMode.postgres_shadow,
    CutoverMode.postgres_write: CutoverMode.postgres_read,
    CutoverMode.mongo_archive: CutoverMode.postgres_write,
}


class ReadinessStatus(str, Enum):
    """How far along the readiness checks are for a domain."""

    unknown = "unknown"
    not_started = "not_started"
    identity_ready = "identity_ready"
    evidence_ready = "evidence_ready"
    genesis_ready = "genesis_ready"
    ready_for_shadow = "ready_for_shadow"
    shadow_active = "shadow_active"
    shadow_passing = "shadow_passing"
    shadow_clean = "shadow_clean"
    promoted = "promoted"
    rolled_back = "rolled_back"
    blocked = "blocked"


class DataSource(str, Enum):
    mongo = "mongo"
    postgres = "postgres"
    dual = "dual"
    none = "none"


class AuditAction(str, Enum):
    entered_shadow = "entered_shadow"
    promoted_read = "promoted_read"
    promoted_write = "promoted_write"
    archived_mongo = "archived_mongo"
    rolled_back = "rolled_back"
    readiness_checked = "readiness_checked"
    shadow_diff_recorded = "shadow_diff_recorded"
    domain_source_blocked = "domain_source.blocked"
    domain_source_downgraded = "domain_source.downgraded"
    domain_source_shadow_blocked = "domain_source.shadow_blocked"
    domain_source_missing_state_defaulted = "domain_source.missing_state_defaulted"
    domain_source_readiness_failed = "domain_source.readiness_failed"
    domain_source_operation_denied = "domain_source.operation_denied"


# ---------------------------------------------------------------------------
# Core status model
# ---------------------------------------------------------------------------

class DomainCutoverStatus(BaseModel):
    """Full status record for one (building_id, domain) pair."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str | None = None
    organisation_id: str | None = None
    building_id: str
    scheme_id: str | None = None
    domain: str
    route_group: str | None = None
    read_source: DataSource
    write_source: DataSource
    mode: CutoverMode
    toggle_name: str | None = None
    readiness_status: ReadinessStatus
    last_readiness_check_at: datetime | None = None
    last_shadow_diff_at: datetime | None = None
    last_promoted_at: datetime | None = None
    promoted_by: str | None = None
    previous_mode: str | None = None
    rollback_available: bool
    continuity_source: DataSource | None = None
    continuity_policy: str | None = None
    notes: str | None = None
    p0_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DomainCutoverStatusSummary(BaseModel):
    """Lightweight row for list views."""

    building_id: str
    domain: str
    mode: CutoverMode
    readiness_status: ReadinessStatus
    read_source: DataSource
    write_source: DataSource
    last_readiness_check_at: datetime | None = None
    last_promoted_at: datetime | None = None
    last_shadow_diff_at: datetime | None = None
    rollback_available: bool
    toggle_name: str | None = None
    notes: str | None = None
    p0_snapshot: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shadow diff model
# ---------------------------------------------------------------------------

class ShadowDiffRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    building_id: str
    domain: str
    route: str | None = None
    diff_type: str
    mongo_value: dict[str, Any] | None = None
    pg_value: dict[str, Any] | None = None
    divergence_score: float = 1.0
    resolved: bool = False
    resolved_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Audit log model
# ---------------------------------------------------------------------------

class CutoverAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    building_id: str
    domain: str
    action: AuditAction
    from_mode: str | None = None
    to_mode: str | None = None
    actor_user_id: str | None = None
    actor_role: str | None = None
    reason: str | None = None
    p0_snapshot: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------------
# Request / response models for API endpoints
# ---------------------------------------------------------------------------

class CutoverPromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)
    skip_p0_check: bool = Field(
        default=False,
        description="Only super_admin may set this; used for emergency overrides with explicit audit trail",
    )


class CutoverRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=500)


class RecordShadowDiffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str | None = None
    diff_type: str = Field(..., min_length=1, max_length=80)
    mongo_value: dict[str, Any] | None = None
    pg_value: dict[str, Any] | None = None
    divergence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    notes: str | None = Field(default=None, max_length=500)


class CutoverStatusListResponse(BaseModel):
    items: list[DomainCutoverStatusSummary]
    total: int


class CutoverStatusDetailResponse(BaseModel):
    status: DomainCutoverStatus
    recent_diffs: list[ShadowDiffRecord] = Field(default_factory=list)
    recent_audits: list[CutoverAuditEntry] = Field(default_factory=list)


class CutoverActionResponse(BaseModel):
    """Returned by all mutating endpoints."""

    building_id: str
    domain: str
    previous_mode: str
    new_mode: str
    action: str
    audit_id: str
    message: str


class P0ReadinessReport(BaseModel):
    """Snapshot of P0 gate results at time of check."""

    building_id: str
    overall: str  # "pass" | "warn" | "fail"
    gates: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime
