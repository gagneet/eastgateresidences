# @featuretrace:cutover-toggle-safety — Runtime guard for per-building/domain source-of-truth selection.
# Layer: service
# Data flow: finance/trust routers and cutover services → require_domain_source() → core.domain_cutover_status (building-scoped).
# Related: backend/services/cutover_status_service.py
#          backend/services/cutover_config_service.py
#          backend/alembic/versions/0049_cutover_control_plane.py
# Tests: tests/backend/test_domain_source_guard.py
"""Runtime source-of-truth guard for cutover-sensitive domains.

Global feature toggles only mean a PostgreSQL path is available. They never make
PostgreSQL canonical for a building/domain. Canonical read/write decisions come
from ``core.domain_cutover_status`` and fail closed to the legacy source when the
row is missing, blocked, disabled, or not in the required cutover mode.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal

from fastapi import HTTPException

from models.cutover_status import AuditAction, CutoverMode, DataSource, ReadinessStatus
from services.cutover_status_service import (
    canonical_domain,
    get_or_default_cutover_status,
    record_domain_source_guard_audit_event,
)

logger = logging.getLogger(__name__)

DomainOperation = Literal["read", "write", "shadow_read", "shadow_write"]

_READY_FOR_PG = {
    ReadinessStatus.shadow_active,
    ReadinessStatus.shadow_passing,
    ReadinessStatus.shadow_clean,
    ReadinessStatus.promoted,
    ReadinessStatus.identity_ready,
    ReadinessStatus.evidence_ready,
    ReadinessStatus.genesis_ready,
    ReadinessStatus.ready_for_shadow,
}

_BLOCKING_READINESS = {
    ReadinessStatus.unknown,
    ReadinessStatus.not_started,
    ReadinessStatus.blocked,
    ReadinessStatus.rolled_back,
}


@dataclass(frozen=True)
class DomainSourceAuditContext:
    actor_user_id: str | None = None
    actor_role: str | None = None
    organisation_id: str | None = None
    unit_id: str | None = None
    route: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    source_service: str | None = None
    feature_toggle_key: str | None = None
    global_toggle_value: bool | None = None
    environment: str | None = None
    app_version: str | None = None
    git_sha: str | None = None
    service_name: str | None = None
    metadata: dict[str, object] | None = None
    is_test_data: bool = False


@dataclass(frozen=True)
class DomainSourceDecision:
    building_id: str
    requested_domain: str
    domain: str
    operation: DomainOperation
    source: DataSource
    postgres_allowed: bool
    shadow_enabled: bool
    blocked_reason: str | None
    mode: CutoverMode
    readiness_status: ReadinessStatus

    @property
    def source_value(self) -> str:
        """Generated function header.

        Function: DomainSourceDecision.source_value
        Path: backend/services/domain_source_guard.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.source.value


def _blocked_decision(
    *,
    building_id: str,
    requested_domain: str,
    domain: str,
    operation: DomainOperation,
    mode: CutoverMode,
    readiness_status: ReadinessStatus,
    reason: str,
) -> DomainSourceDecision:
    """Generated function header.

    Function: _blocked_decision
    Path: backend/services/domain_source_guard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logger.warning(
        "domain_source_guard_blocked building_id=%s domain=%s operation=%s mode=%s readiness=%s reason=%s",
        building_id,
        domain,
        operation,
        mode.value,
        readiness_status.value,
        reason,
    )
    return DomainSourceDecision(
        building_id=building_id,
        requested_domain=requested_domain,
        domain=domain,
        operation=operation,
        source=DataSource.mongo,
        postgres_allowed=False,
        shadow_enabled=False,
        blocked_reason=reason,
        mode=mode,
        readiness_status=readiness_status,
    )


def _audit_action_for_decision(
    *,
    operation: DomainOperation,
    requested: DataSource | None,
    decision: DomainSourceDecision,
    status_id: str | None,
    raise_on_blocked_postgres: bool,
) -> AuditAction | None:
    """Generated function header.

    Function: _audit_action_for_decision
    Path: backend/services/domain_source_guard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not decision.blocked_reason and not (operation.startswith("shadow_") and not decision.shadow_enabled):
        return None
    if status_id == "default":
        return AuditAction.domain_source_missing_state_defaulted
    if operation.startswith("shadow_") and not decision.shadow_enabled:
        return AuditAction.domain_source_shadow_blocked
    if requested == DataSource.postgres and raise_on_blocked_postgres:
        return AuditAction.domain_source_blocked
    if decision.readiness_status in {ReadinessStatus.blocked, ReadinessStatus.rolled_back}:
        return AuditAction.domain_source_readiness_failed
    if requested == DataSource.postgres and decision.source == DataSource.mongo:
        return AuditAction.domain_source_downgraded
    return AuditAction.domain_source_operation_denied


async def _persist_guard_audit_event(
    *,
    decision: DomainSourceDecision,
    requested: DataSource | None,
    action: AuditAction | None,
    status_id: str | None,
    audit_context: DomainSourceAuditContext | None,
) -> None:
    """Generated function header.

    Function: _persist_guard_audit_event
    Path: backend/services/domain_source_guard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if action is None:
        return
    context = audit_context or DomainSourceAuditContext()
    try:
        await record_domain_source_guard_audit_event(
            building_id=decision.building_id,
            domain=decision.domain,
            action=action,
            operation=decision.operation,
            requested_source=requested.value if requested else None,
            resolved_source=decision.source.value,
            readiness_status=decision.readiness_status.value,
            cutover_status=decision.mode.value,
            reason=decision.blocked_reason,
            actor_user_id=context.actor_user_id,
            actor_role=context.actor_role,
            organisation_id=context.organisation_id,
            unit_id=context.unit_id,
            route=context.route,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            source_service=context.source_service,
            feature_toggle_key=context.feature_toggle_key,
            global_toggle_value=context.global_toggle_value,
            domain_state_id=status_id,
            environment=context.environment,
            app_version=context.app_version,
            git_sha=context.git_sha,
            service_name=context.service_name,
            metadata={
                **(context.metadata or {}),
                "guard": "require_domain_source",
                "requested_domain": decision.requested_domain,
            },
            is_test_data=context.is_test_data,
        )
    except Exception as exc:
        logger.warning(
            "domain_source_guard_audit_failed building_id=%s domain=%s operation=%s action=%s error=%s",
            decision.building_id,
            decision.domain,
            decision.operation,
            action.value,
            exc,
        )


async def require_domain_source(
    *,
    domain: str,
    building_id: str,
    operation: DomainOperation,
    requested_source: str | DataSource | None = None,
    raise_on_blocked_postgres: bool = False,
    audit_context: DomainSourceAuditContext | None = None,
) -> DomainSourceDecision:
    """Resolve the safe source for one building/domain/operation.

    Missing rows default to Mongo because finance/trust are still legacy-primary.
    When a caller asks for PostgreSQL and the building/domain state is not ready,
    the guard logs a structured warning and either returns Mongo or raises 503 if
    ``raise_on_blocked_postgres`` is requested.
    """
    if operation not in {"read", "write", "shadow_read", "shadow_write"}:
        raise ValueError(f"Unsupported domain source operation: {operation!r}")
    if not building_id:
        raise ValueError("building_id is required for domain source decisions")

    canonical = canonical_domain(domain)
    status = await get_or_default_cutover_status(building_id, canonical)
    if isinstance(requested_source, DataSource):
        requested = requested_source
    elif requested_source:
        requested = DataSource(requested_source)
    else:
        requested = None

    reason: str | None = None
    if status.mode == CutoverMode.disabled:
        reason = "domain is disabled"
    elif status.readiness_status in _BLOCKING_READINESS:
        reason = f"readiness is {status.readiness_status.value}"
    elif status.readiness_status not in _READY_FOR_PG:
        reason = f"readiness is not PG-ready: {status.readiness_status.value}"

    if reason:
        decision = _blocked_decision(
            building_id=building_id,
            requested_domain=domain,
            domain=canonical,
            operation=operation,
            mode=status.mode,
            readiness_status=status.readiness_status,
            reason=reason,
        )
        action = _audit_action_for_decision(
            operation=operation,
            requested=requested,
            decision=decision,
            status_id=status.id,
            raise_on_blocked_postgres=raise_on_blocked_postgres,
        )
        await _persist_guard_audit_event(
            decision=decision,
            requested=requested,
            action=action,
            status_id=status.id,
            audit_context=audit_context,
        )
        if requested == DataSource.postgres and raise_on_blocked_postgres:
            raise HTTPException(status_code=503, detail=f"PostgreSQL {operation} blocked for {canonical}: {reason}")
        return decision

    source = DataSource.mongo
    postgres_allowed = False
    shadow_enabled = False
    blocked_reason = None

    if operation == "read":
        postgres_allowed = status.mode in {
            CutoverMode.postgres_read,
            CutoverMode.postgres_write,
            CutoverMode.mongo_archive,
        } and status.read_source == DataSource.postgres
        source = DataSource.postgres if postgres_allowed else DataSource.mongo
        if not postgres_allowed and requested == DataSource.postgres:
            blocked_reason = f"mode/source do not allow postgres read: {status.mode.value}/{status.read_source.value}"
    elif operation == "write":
        postgres_allowed = status.mode in {
            CutoverMode.postgres_write,
            CutoverMode.mongo_archive,
        } and status.write_source == DataSource.postgres
        source = DataSource.postgres if postgres_allowed else DataSource.mongo
        if not postgres_allowed and requested == DataSource.postgres:
            blocked_reason = f"mode/source do not allow postgres write: {status.mode.value}/{status.write_source.value}"
    elif operation == "shadow_read":
        shadow_enabled = status.mode in {
            CutoverMode.postgres_shadow,
            CutoverMode.postgres_read,
            CutoverMode.postgres_write,
        }
        source = DataSource.dual if shadow_enabled else DataSource.mongo
    elif operation == "shadow_write":
        # Shadow writes are intentionally more restrictive than reads. A domain
        # must be explicitly in postgres_shadow before services may dual-write.
        shadow_enabled = status.mode == CutoverMode.postgres_shadow
        source = DataSource.dual if shadow_enabled else DataSource.mongo

    if blocked_reason:
        logger.warning(
            "domain_source_guard_blocked building_id=%s domain=%s operation=%s mode=%s readiness=%s reason=%s",
            building_id,
            canonical,
            operation,
            status.mode.value,
            status.readiness_status.value,
            blocked_reason,
        )

    decision = DomainSourceDecision(
        building_id=building_id,
        requested_domain=domain,
        domain=canonical,
        operation=operation,
        source=source,
        postgres_allowed=postgres_allowed,
        shadow_enabled=shadow_enabled,
        blocked_reason=blocked_reason,
        mode=status.mode,
        readiness_status=status.readiness_status,
    )
    action = _audit_action_for_decision(
        operation=operation,
        requested=requested,
        decision=decision,
        status_id=status.id,
        raise_on_blocked_postgres=raise_on_blocked_postgres,
    )
    await _persist_guard_audit_event(
        decision=decision,
        requested=requested,
        action=action,
        status_id=status.id,
        audit_context=audit_context,
    )
    if blocked_reason and raise_on_blocked_postgres:
        raise HTTPException(status_code=503, detail=f"PostgreSQL {operation} blocked for {canonical}: {blocked_reason}")

    return decision
