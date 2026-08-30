# @featuretrace:cutover-control-plane — Core service for per-building/domain cutover mode tracking.
# Layer: service
# Data flow: cutover admin router → this service → core.domain_cutover_status / core.shadow_diffs / core.cutover_audit_log (building-scoped).
# Related: backend/routers/cutover_admin.py
#          backend/models/cutover_status.py
#          backend/services/cutover_config_service.py
#          docs/architecture/feature-toggle-governance.md
#          docs/architecture/source-of-truth-matrix.md
# Toggle: N/A — the control plane itself is not feature-gated; individual domain modes are.

"""Per-building/domain source-of-truth control plane.

Safety rules enforced here (not in the router):
  1. Cannot promote write before read.
  2. Cannot promote read before shadow mode has been active.
  3. Cannot promote any domain if P0 readiness fails (unless skip_p0_check=True with super_admin audit).
  4. Cannot promote globally — building_id must always be non-empty.
  5. Cannot promote building A and affect building B (all queries are building-scoped).
  6. All promotions and rollbacks are written to core.cutover_audit_log before returning.
  7. Rollback restores the previous mode only (no arbitrary mode jumps).
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from utils.test_data_flag import under_pytest
from models.cutover_status import (
    AuditAction,
    CutoverAuditEntry,
    CutoverMode,
    DataSource,
    DomainCutoverStatus,
    DomainCutoverStatusSummary,
    P0ReadinessReport,
    ReadinessStatus,
    ShadowDiffRecord,
    VALID_FORWARD_TRANSITIONS,
    VALID_ROLLBACK_TRANSITIONS,
)

logger = logging.getLogger(__name__)

BYPASS_UUID = "00000000-0000-0000-0000-000000000000"

# Source routing: what read_source/write_source each mode implies
_MODE_SOURCES: dict[CutoverMode, tuple[DataSource, DataSource]] = {
    CutoverMode.mongo_primary:   (DataSource.mongo,    DataSource.mongo),
    CutoverMode.postgres_shadow: (DataSource.mongo,    DataSource.mongo),   # reads still Mongo; PG gets a copy
    CutoverMode.postgres_read:   (DataSource.postgres, DataSource.mongo),
    CutoverMode.postgres_write:  (DataSource.postgres, DataSource.postgres),
    CutoverMode.mongo_archive:   (DataSource.postgres, DataSource.postgres),
    CutoverMode.disabled:        (DataSource.none,     DataSource.none),
}

# ---------------------------------------------------------------------------
# Domain name canonicalisation
# ---------------------------------------------------------------------------
# Lives here (not in domain_source_guard.py) because domain_source_guard.py
# imports from this module — defining it there would create a circular import
# if this module needs it too (record_shadow_diff does). domain_source_guard.py
# re-exports this symbol for backward compatibility.
_DOMAIN_ALIASES: dict[str, str] = {
    "finance": "finance_ledger",
    "financial": "finance_ledger",
    "ledger": "finance_ledger",
    "trust": "trust_ledger",
    "trust_accounting": "trust_ledger",
    "trust_reconciliation": "trust_reconciliation",
    "identity": "identity_core",
    "ownership": "identity_core",
}


def canonical_domain(domain: str) -> str:
    """Generated function header.

    Function: canonical_domain
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    key = str(domain or "").strip().lower()
    return _DOMAIN_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# Mode / readiness consistency validation
# ---------------------------------------------------------------------------
# Not a DB CHECK constraint yet — existing rows haven't all been inspected
# against this. Used as a guard in promote_domain() (hard 409) and as a
# defensive, logged-only check elsewhere, so a future edit to a readiness-
# writing function can't silently reintroduce an inconsistent row like
# identity_core's mongo_primary + shadow_clean (found live, 2026-07-14).
_PRE_SHADOW_READY: set[ReadinessStatus] = {
    ReadinessStatus.ready_for_shadow,
    ReadinessStatus.identity_ready,
    ReadinessStatus.evidence_ready,
    ReadinessStatus.genesis_ready,
}

# Expected readiness families per mode. `rolled_back` is valid alongside any
# mode except mongo_archive, since rollback_domain() tags the landing row
# rolled_back regardless of which mode it lands on (mongo_archive is never a
# rollback landing mode — see VALID_ROLLBACK_TRANSITIONS).
_EXPECTED_READINESS_BY_MODE: dict[CutoverMode, set[ReadinessStatus]] = {
    CutoverMode.mongo_primary: {
        ReadinessStatus.unknown, ReadinessStatus.not_started, ReadinessStatus.blocked,
        ReadinessStatus.identity_ready, ReadinessStatus.evidence_ready,
        ReadinessStatus.genesis_ready, ReadinessStatus.ready_for_shadow,
        ReadinessStatus.rolled_back,
    },
    CutoverMode.postgres_shadow: {
        ReadinessStatus.shadow_active, ReadinessStatus.shadow_passing,
        ReadinessStatus.shadow_clean, ReadinessStatus.rolled_back,
    },
    CutoverMode.postgres_read: {
        ReadinessStatus.shadow_passing, ReadinessStatus.shadow_clean,
        ReadinessStatus.promoted, ReadinessStatus.rolled_back,
    },
    CutoverMode.postgres_write: {ReadinessStatus.promoted, ReadinessStatus.rolled_back},
    CutoverMode.mongo_archive: {ReadinessStatus.promoted},
}


def validate_mode_readiness_pair(
    mode: CutoverMode, readiness_status: ReadinessStatus
) -> str | None:
    """Return a violation description if (mode, readiness_status) is inconsistent, else None.

    Pure validation — does not raise or log. Callers decide whether an
    inconsistency is fatal (promote_domain: raise 409) or worth logging only
    (readiness-update functions, whose own mode/readiness pair is always
    self-consistent by construction, but which call this defensively so a
    future edit can't quietly break that invariant).
    """
    expected = _EXPECTED_READINESS_BY_MODE.get(mode)
    if expected is not None and readiness_status not in expected:
        return (
            f"Inconsistent cutover mode/readiness state: mode={mode.value} "
            f"readiness_status={readiness_status.value} is not an expected combination "
            "(repair the row before promoting)."
        )
    return None


def _utc_now() -> datetime:
    """Generated function header.

    Function: _utc_now
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(UTC)


def _str_or_none(v: Any) -> str | None:
    """Generated function header.

    Function: _str_or_none
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(v) if v is not None else None


# ---------------------------------------------------------------------------
# Internal DB helpers — all use bypass tenant to query control plane tables
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _get_bypass_session_context():
    """Return an async_session_context pre-set to the bypass tenant UUID.

    Control-plane tables (domain_cutover_status, shadow_diffs, cutover_audit_log)
    store their own building/tenant scoping. Querying them always uses the bypass
    UUID so that operators can inspect any building without needing to set the
    correct tenant GUC each time.

    This helper applies the bypass tenant UUID on every transaction before any
    control-plane query so RLS policy checks are deterministic.
    """
    async with async_session_context() as session:
        await set_tenant(session, BYPASS_UUID)
        yield session


async def _fetch_status_row(
    session: Any, building_id: str, domain: str
) -> dict[str, Any] | None:
    """Generated function header.

    Function: _fetch_status_row
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await session.execute(
        text(
            """
            SELECT
                id::text, tenant_id::text, organisation_id,
                building_id, scheme_id::text, domain, route_group,
                read_source, write_source, mode, toggle_name,
                readiness_status, last_readiness_check_at,
                last_shadow_diff_at, last_promoted_at,
                promoted_by::text, previous_mode, rollback_available,
                continuity_source, continuity_policy,
                notes, p0_snapshot, created_at, updated_at
            FROM core.domain_cutover_status
            WHERE building_id = :building_id
              AND domain = :domain
              AND is_test_data = FALSE
            LIMIT 1
            """
        ),
        {"building_id": building_id, "domain": domain},
    )
    r = row.fetchone()
    return dict(r._mapping) if r else None


async def _upsert_status_row(
    session: Any,
    *,
    building_id: str,
    domain: str,
    mode: CutoverMode,
    read_source: DataSource,
    write_source: DataSource,
    readiness_status: ReadinessStatus,
    previous_mode: str | None,
    rollback_available: bool,
    promoted_by: str | None,
    toggle_name: str | None,
    notes: str | None,
    p0_snapshot: dict[str, Any],
    last_readiness_check_at: datetime | None = None,
    last_shadow_diff_at: datetime | None = None,
    is_test_data: bool = False,
) -> str:
    """Upsert a cutover status row; return the row id."""
    now = _utc_now()
    row_id = str(uuid4())

    # Resolve tenant_id for this building (best-effort; may be NULL for new buildings)
    scheme = await resolve_scheme_context(building_id)
    tenant_id = str(scheme["tenant_id"]) if scheme and scheme.get("tenant_id") else BYPASS_UUID
    scheme_id = str(scheme["scheme_id"]) if scheme and scheme.get("scheme_id") else None

    await session.execute(
        text(
            """
            INSERT INTO core.domain_cutover_status (
                id, tenant_id, building_id, scheme_id, domain,
                read_source, write_source, mode, toggle_name,
                readiness_status, previous_mode, rollback_available,
                promoted_by, notes, p0_snapshot,
                last_readiness_check_at, last_shadow_diff_at,
                last_promoted_at,
                is_test_data, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, CAST(:scheme_id AS UUID), :domain,
                :read_source, :write_source, :mode, :toggle_name,
                :readiness_status, :previous_mode, :rollback_available,
                CAST(:promoted_by AS UUID), :notes, CAST(:p0_snapshot AS JSONB),
                :last_readiness_check_at, :last_shadow_diff_at,
                :last_promoted_at,
                :is_test_data, :now, :now
            )
            ON CONFLICT (building_id, domain)
            DO UPDATE SET
                read_source = EXCLUDED.read_source,
                write_source = EXCLUDED.write_source,
                mode = EXCLUDED.mode,
                toggle_name = COALESCE(EXCLUDED.toggle_name, core.domain_cutover_status.toggle_name),
                readiness_status = EXCLUDED.readiness_status,
                previous_mode = EXCLUDED.previous_mode,
                rollback_available = EXCLUDED.rollback_available,
                promoted_by = EXCLUDED.promoted_by,
                notes = COALESCE(EXCLUDED.notes, core.domain_cutover_status.notes),
                p0_snapshot = EXCLUDED.p0_snapshot,
                last_readiness_check_at = COALESCE(
                    EXCLUDED.last_readiness_check_at,
                    core.domain_cutover_status.last_readiness_check_at
                ),
                last_shadow_diff_at = COALESCE(
                    EXCLUDED.last_shadow_diff_at,
                    core.domain_cutover_status.last_shadow_diff_at
                ),
                last_promoted_at = CASE
                    WHEN EXCLUDED.mode != core.domain_cutover_status.mode THEN EXCLUDED.last_promoted_at
                    ELSE core.domain_cutover_status.last_promoted_at
                END,
                updated_at = EXCLUDED.updated_at
            RETURNING id::text
            """
        ),
        {
            "id": row_id,
            "tenant_id": tenant_id,
            "building_id": building_id,
            "scheme_id": scheme_id,
            "domain": domain,
            "read_source": read_source.value,
            "write_source": write_source.value,
            "mode": mode.value,
            "toggle_name": toggle_name,
            "readiness_status": readiness_status.value,
            "previous_mode": previous_mode,
            "rollback_available": rollback_available,
            "promoted_by": promoted_by if promoted_by else None,
            "notes": notes,
            "p0_snapshot": json.dumps(p0_snapshot),
            "last_readiness_check_at": last_readiness_check_at,
            "last_shadow_diff_at": last_shadow_diff_at,
            "last_promoted_at": now if promoted_by else None,
            "is_test_data": is_test_data,
            "now": now,
        },
    )
    return row_id


async def _write_audit_entry(
    session: Any,
    *,
    building_id: str,
    domain: str,
    action: AuditAction,
    from_mode: str | None,
    to_mode: str | None,
    actor_user_id: str | None,
    actor_role: str | None,
    reason: str | None,
    p0_snapshot: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    is_test_data: bool = False,
) -> str:
    """Generated function header.

    Function: _write_audit_entry
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    audit_id = str(uuid4())
    scheme = await resolve_scheme_context(building_id)
    tenant_id = str(scheme["tenant_id"]) if scheme and scheme.get("tenant_id") else BYPASS_UUID

    await session.execute(
        text(
            """
            INSERT INTO core.cutover_audit_log (
                id, tenant_id, building_id, domain, action,
                from_mode, to_mode, actor_user_id, actor_role,
                reason, p0_snapshot, metadata, is_test_data, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, :domain, :action,
                :from_mode, :to_mode, CAST(:actor_user_id AS UUID), :actor_role,
                :reason, CAST(:p0_snapshot AS JSONB), CAST(:metadata AS JSONB), :is_test_data, NOW()
            )
            """
        ),
        {
            "id": audit_id,
            "tenant_id": tenant_id,
            "building_id": building_id,
            "domain": domain,
            "action": action.value,
            "from_mode": from_mode,
            "to_mode": to_mode,
            "actor_user_id": actor_user_id or None,
            "actor_role": actor_role,
            "reason": reason,
            "p0_snapshot": json.dumps(p0_snapshot),
            "metadata": json.dumps(metadata or {}),
            "is_test_data": is_test_data,
        },
    )
    return audit_id


def _safe_audit_metadata_value(value: Any) -> Any:
    """Generated function header.

    Function: _safe_audit_metadata_value
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_audit_metadata_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(k): _safe_audit_metadata_value(v) for k, v in list(value.items())[:50]}
    return str(value)


def _sanitize_guard_metadata_value(value: Any, sensitive_fragments: tuple[str, ...]) -> Any:
    """Generated function header.

    Function: _sanitize_guard_metadata_value
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_guard_metadata_value(item, sensitive_fragments) for item in value[:20]]
    if isinstance(value, dict):
        safe_dict: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            key_s = str(key)
            lowered = key_s.lower()
            if any(fragment in lowered for fragment in sensitive_fragments):
                continue
            safe_dict[key_s] = _sanitize_guard_metadata_value(item, sensitive_fragments)
        return safe_dict
    return str(value)


def _sanitize_guard_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Generated function header.

    Function: _sanitize_guard_metadata
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    sensitive_fragments = (
        "password",
        "secret",
        "token",
        "cookie",
        "session",
        "bank_account",
        "account_number",
        "bsb",
        "payment_file",
        "identity_document",
        "request_payload",
        "raw_payload",
        "payload",
    )
    sanitized = _sanitize_guard_metadata_value(metadata, sensitive_fragments)
    if isinstance(sanitized, dict):
        return sanitized
    return {}


async def record_domain_source_guard_audit_event(
    *,
    building_id: str,
    domain: str,
    action: AuditAction,
    operation: str,
    requested_source: str | None,
    resolved_source: str,
    readiness_status: str | None,
    cutover_status: str | None,
    reason: str | None,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    organisation_id: str | None = None,
    unit_id: str | None = None,
    route: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    source_service: str | None = None,
    feature_toggle_key: str | None = None,
    global_toggle_value: bool | None = None,
    domain_state_id: str | None = None,
    environment: str | None = None,
    app_version: str | None = None,
    git_sha: str | None = None,
    service_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    is_test_data: bool = False,
) -> str:
    """Persist one best-effort audit event for a source guard decision.

    The schema stores guard-specific fields in ``metadata`` so this reuses the
    existing cutover audit table without adding sensitive request payloads.
    """
    event_metadata = _sanitize_guard_metadata({
        **(metadata or {}),
        "event_type": action.value,
        "domain": domain,
        "operation": operation,
        "organisation_id": organisation_id,
        "building_id": building_id,
        "unit_id": unit_id,
        "requested_source": requested_source,
        "resolved_source": resolved_source,
        "readiness_status": readiness_status,
        "cutover_status": cutover_status,
        "reason": reason,
        "actor_user_id": actor_user_id,
        "route": route,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "source_service": source_service,
        "feature_toggle_key": feature_toggle_key,
        "global_toggle_value": global_toggle_value,
        "domain_state_id": domain_state_id,
        "environment": environment,
        "app_version": app_version,
        "git_sha": git_sha,
        "service_name": service_name,
    })
    async with _get_bypass_session_context() as session:
        return await _write_audit_entry(
            session,
            building_id=building_id,
            domain=domain,
            action=action,
            from_mode=requested_source,
            to_mode=resolved_source,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason,
            p0_snapshot={},
            metadata=event_metadata,
            is_test_data=is_test_data,
        )


def _row_to_status(row: dict[str, Any]) -> DomainCutoverStatus:
    """Generated function header.

    Function: _row_to_status
    Path: backend/services/cutover_status_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return DomainCutoverStatus(
        id=str(row["id"]),
        tenant_id=_str_or_none(row.get("tenant_id")),
        organisation_id=row.get("organisation_id"),
        building_id=row["building_id"],
        scheme_id=_str_or_none(row.get("scheme_id")),
        domain=row["domain"],
        route_group=row.get("route_group"),
        read_source=DataSource(row["read_source"]),
        write_source=DataSource(row["write_source"]),
        mode=CutoverMode(row["mode"]),
        toggle_name=row.get("toggle_name"),
        readiness_status=ReadinessStatus(row["readiness_status"]),
        last_readiness_check_at=row.get("last_readiness_check_at"),
        last_shadow_diff_at=row.get("last_shadow_diff_at"),
        last_promoted_at=row.get("last_promoted_at"),
        promoted_by=_str_or_none(row.get("promoted_by")),
        previous_mode=row.get("previous_mode"),
        rollback_available=bool(row.get("rollback_available", True)),
        continuity_source=DataSource(row["continuity_source"]) if row.get("continuity_source") else None,
        continuity_policy=row.get("continuity_policy"),
        notes=row.get("notes"),
        p0_snapshot=row.get("p0_snapshot") or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# P0 readiness check integration
# ---------------------------------------------------------------------------

async def _run_p0_check(building_id: str) -> P0ReadinessReport:
    """Run a lightweight P0 readiness check for a building.

    This is a subset of the full postgres_cutover_p0_readiness.py script,
    checking only the gates relevant to the control plane (schema presence
    and umbrella toggle state). The full script is the authoritative checker
    for finance-specific gates.
    """
    from services.cutover_config_service import (
        UMBRELLA_FEATURE_KEY,
        is_cutover_feature_enabled,
    )

    gates: dict[str, Any] = {}
    overall = "pass"

    # Gate 1: umbrella toggle
    try:
        umbrella_on = await is_cutover_feature_enabled(building_id, UMBRELLA_FEATURE_KEY)
        gates["umbrella_toggle"] = {
            "status": "pass" if umbrella_on else "warn",
            "detail": f"financial_integration_layer_v2 = {umbrella_on}",
        }
        if not umbrella_on and overall == "pass":
            overall = "warn"
    except Exception as exc:
        gates["umbrella_toggle"] = {"status": "warn", "detail": str(exc)}
        overall = "warn"

    # Gate 2: core schema tables present
    try:
        async with _get_bypass_session_context() as session:
            result = await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS n
                    FROM information_schema.tables
                    WHERE table_schema = 'core'
                      AND table_name IN (
                          'domain_cutover_status', 'shadow_diffs', 'cutover_audit_log'
                      )
                    """
                )
            )
            row = result.fetchone()
            n = row.n if row else 0
            gates["cutover_tables"] = {
                "status": "pass" if n == 3 else "fail",
                "detail": f"found {n}/3 control-plane tables",
            }
            if n < 3:
                overall = "fail"
    except Exception as exc:
        gates["cutover_tables"] = {"status": "fail", "detail": str(exc)}
        overall = "fail"

    # Gate 3: scheme context resolvable
    try:
        scheme = await resolve_scheme_context(building_id)
        gates["scheme_context"] = {
            "status": "pass" if scheme else "warn",
            "detail": f"scheme_id = {scheme.get('scheme_id') if scheme else 'not found'}",
        }
        if not scheme and overall == "pass":
            overall = "warn"
    except Exception as exc:
        gates["scheme_context"] = {"status": "warn", "detail": str(exc)}
        if overall == "pass":
            overall = "warn"

    return P0ReadinessReport(
        building_id=building_id,
        overall=overall,
        gates=gates,
        checked_at=_utc_now(),
    )


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------

async def get_cutover_status(
    building_id: str, domain: str
) -> DomainCutoverStatus | None:
    """Return the current cutover status for (building_id, domain), or None if not registered."""
    if not building_id or not domain:
        raise ValueError("building_id and domain are required")
    domain = canonical_domain(domain)

    try:
        async with _get_bypass_session_context() as session:
            row = await _fetch_status_row(session, building_id, domain)
            return _row_to_status(row) if row else None
    except Exception as exc:
        logger.warning("cutover_status: get_cutover_status failed (%s), returning None", exc)
        return None


async def get_or_default_cutover_status(
    building_id: str, domain: str
) -> DomainCutoverStatus:
    """Return status or a synthetic mongo_primary default if not yet registered.

    Used by service callers that need a guaranteed answer even for un-registered domains.
    """
    status = await get_cutover_status(building_id, domain)
    if status:
        return status
    now = _utc_now()
    return DomainCutoverStatus(
        id="default",
        building_id=building_id,
        domain=domain,
        read_source=DataSource.mongo,
        write_source=DataSource.mongo,
        mode=CutoverMode.mongo_primary,
        readiness_status=ReadinessStatus.unknown,
        rollback_available=False,
        created_at=now,
        updated_at=now,
    )


async def assert_read_allowed(building_id: str, domain: str) -> None:
    """Raise HTTPException(503) if PG reads are not yet safe for this domain.

    Call this from any route that wants to gate reads on cutover readiness.
    """
    status = await get_or_default_cutover_status(building_id, domain)
    if status.mode in (CutoverMode.disabled,):
        raise HTTPException(
            status_code=503,
            detail=f"Domain '{domain}' is disabled for building {building_id}",
        )


async def assert_write_allowed(building_id: str, domain: str) -> None:
    """Raise HTTPException(503) if PG writes are not yet safe for this domain."""
    status = await get_or_default_cutover_status(building_id, domain)
    if status.mode not in (
        CutoverMode.postgres_write,
        CutoverMode.mongo_archive,
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Domain '{domain}' for building {building_id} is in mode "
                f"'{status.mode.value}' — PG writes not yet enabled"
            ),
        )


async def resolve_read_source(
    building_id: str, domain: str, route: str | None = None
) -> DataSource:
    """Return the authoritative read source for this domain and building."""
    status = await get_or_default_cutover_status(building_id, domain)
    return status.read_source


async def resolve_write_source(
    building_id: str, domain: str, route: str | None = None
) -> DataSource:
    """Return the authoritative write source for this domain and building."""
    status = await get_or_default_cutover_status(building_id, domain)
    return status.write_source


async def record_shadow_diff(
    *,
    building_id: str,
    domain: str,
    route: str | None = None,
    diff_type: str,
    mongo_value: dict[str, Any] | None = None,
    pg_value: dict[str, Any] | None = None,
    divergence_score: float = 1.0,
    notes: str | None = None,
    is_test_data: bool = False,
) -> str:
    """Append a shadow divergence record and update last_shadow_diff_at on the status row.

    Returns the new diff record id.
    """
    domain = canonical_domain(domain)
    scheme = await resolve_scheme_context(building_id)
    tenant_id = str(scheme["tenant_id"]) if scheme and scheme.get("tenant_id") else BYPASS_UUID

    diff_id = str(uuid4())
    now = _utc_now()

    # A test run reaches this writer through the real production code path and the
    # real DATABASE_URL — there is no test double for core.shadow_diffs. Unflagged,
    # its fixture payloads land as permanent production divergence and block the
    # cutover gate they have nothing to say about (260 such rows on building 13195,
    # found 2026-08-29). Flag them here so the conftest sweep can reclaim them, the
    # same backstop identity_repo.create_user already applies.
    is_test_data = bool(is_test_data) or under_pytest()

    try:
        async with _get_bypass_session_context() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO core.shadow_diffs (
                        id, tenant_id, building_id, domain, route,
                        diff_type, mongo_value, pg_value,
                        divergence_score, is_test_data, created_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, :domain, :route,
                        :diff_type, CAST(:mongo_value AS JSONB), CAST(:pg_value AS JSONB),
                        :divergence_score, :is_test_data, :now
                    )
                    """
                ),
                {
                    "id": diff_id,
                    "tenant_id": tenant_id,
                    "building_id": building_id,
                    "domain": domain,
                    "route": route,
                    "diff_type": diff_type,
                    "mongo_value": json.dumps(mongo_value) if mongo_value is not None else None,
                    "pg_value": json.dumps(pg_value) if pg_value is not None else None,
                    "divergence_score": float(divergence_score),
                    "is_test_data": is_test_data,
                    "now": now,
                },
            )
            # Update last_shadow_diff_at on the status row (if it exists)
            await session.execute(
                text(
                    """
                    UPDATE core.domain_cutover_status
                    SET last_shadow_diff_at = :now, updated_at = :now
                    WHERE building_id = :building_id AND domain = :domain
                    """
                ),
                {"building_id": building_id, "domain": domain, "now": now},
            )
    except Exception as exc:
        logger.error("cutover_status: record_shadow_diff failed: %s", exc)
        raise

    return diff_id


def block_unsafe_global_cutover(building_id: str | None) -> None:
    """Raise ValueError if building_id is empty or looks like a wildcard.

    Called at the start of every promote/rollback to prevent accidental
    global cutover. This is the primary guard against the most dangerous
    class of operator error.
    """
    if not building_id or str(building_id).strip() in ("", "*", "all", "global", "None", "none"):
        raise ValueError(
            "block_unsafe_global_cutover: building_id must be a specific building identifier. "
            "Global cutover is not permitted. Specify a single building_id."
        )


async def record_identity_foundation_readiness(
    *,
    building_id: str,
    validation_passed: bool,
    summary: dict[str, Any],
    reason: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = "system",
    is_test_data: bool = False,
    runtime_exceptions: dict[str, Any] | None = None,
) -> DomainCutoverStatus:
    """Record identity foundation readiness without promoting a domain mode.

    This updates/creates the `identity_core` status row in mongo_primary mode so
    operators can see whether identity data is ready for entering shadow later.
    It never calls promote_domain and never transitions mode.

    A passing validation sets `identity_ready` (a pre-shadow readiness state), not
    `shadow_clean` — that value means "shadow comparisons ran and matched," which has
    never happened for identity_core (0 rows in core.shadow_diffs for this domain as of
    2026-07-14). Setting shadow_clean here was a real bug: it let a backfill-completeness
    check claim a level of validation identity_core had never actually undergone.

    `runtime_exceptions`, when provided, is merged into p0_snapshot so pre-control-plane
    behavior (e.g. POST /auth/login's unconditional Postgres-first path, live since
    2026-05-01, commit b13e8955 — see docs/migration/identity_auth_pg_call_audit.md) is a
    queryable, audited fact instead of only documented in prose.
    """
    block_unsafe_global_cutover(building_id)
    readiness = ReadinessStatus.identity_ready if validation_passed else ReadinessStatus.blocked
    violation = validate_mode_readiness_pair(CutoverMode.mongo_primary, readiness)
    if violation:
        logger.error("record_identity_foundation_readiness: %s (this should be unreachable)", violation)
    p0_snap: dict[str, Any] = {
        "identity_foundation": {
            "status": "pass" if validation_passed else "fail",
            "readiness": readiness.value,
            "summary": summary,
            "checked_at": _utc_now().isoformat(),
        }
    }
    if runtime_exceptions:
        p0_snap["runtime_exceptions"] = runtime_exceptions
    async with _get_bypass_session_context() as session:
        await _upsert_status_row(
            session,
            building_id=building_id,
            domain="identity_core",
            mode=CutoverMode.mongo_primary,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
            readiness_status=readiness,
            previous_mode=CutoverMode.mongo_primary.value,
            rollback_available=True,
            promoted_by=None,
            toggle_name=None,
            notes=reason,
            p0_snapshot=p0_snap,
            last_readiness_check_at=_utc_now(),
            is_test_data=is_test_data,
        )
        await _write_audit_entry(
            session,
            building_id=building_id,
            domain="identity_core",
            action=AuditAction.readiness_checked,
            from_mode=CutoverMode.mongo_primary.value,
            to_mode=CutoverMode.mongo_primary.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason or "identity foundation readiness snapshot updated",
            p0_snapshot=p0_snap,
            is_test_data=is_test_data,
        )
    return await get_or_default_cutover_status(building_id, "identity_core")


_POWERHOUSE_DOMAINS: tuple[str, ...] = (
    "powerhouse_conversations",
    "powerhouse_inbox",
    "powerhouse_workflows",
    "powerhouse_automation",
)


async def record_powerhouse_foundation_readiness(
    *,
    building_id: str,
    domain: str,
    validation_passed: bool,
    summary: dict[str, Any],
    reason: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = "system",
    is_test_data: bool = False,
) -> DomainCutoverStatus:
    """Record Powerhouse domain foundation readiness without promoting a domain mode.

    Mirrors record_identity_foundation_readiness's shape: writes/creates the
    domain's status row in mongo_primary mode so promote_domain()'s
    _PRE_SHADOW_READY gate (mongo_primary -> postgres_shadow) can be satisfied.
    Never calls promote_domain and never transitions mode itself.

    `domain` must be one of the four Powerhouse domains — unlike identity_core
    (a single hardcoded domain), Powerhouse's schema/RLS readiness is uniform
    across all four, but each has its own core.domain_cutover_status row and
    must be checked/promoted independently.
    """
    if domain not in _POWERHOUSE_DOMAINS:
        raise ValueError(f"Unknown Powerhouse domain '{domain}'; expected one of {_POWERHOUSE_DOMAINS}")

    block_unsafe_global_cutover(building_id)
    readiness = ReadinessStatus.ready_for_shadow if validation_passed else ReadinessStatus.blocked
    violation = validate_mode_readiness_pair(CutoverMode.mongo_primary, readiness)
    if violation:
        logger.error("record_powerhouse_foundation_readiness: %s (this should be unreachable)", violation)
    p0_snap: dict[str, Any] = {
        "powerhouse_foundation": {
            "status": "pass" if validation_passed else "fail",
            "readiness": readiness.value,
            "summary": summary,
            "checked_at": _utc_now().isoformat(),
        }
    }
    async with _get_bypass_session_context() as session:
        await _upsert_status_row(
            session,
            building_id=building_id,
            domain=domain,
            mode=CutoverMode.mongo_primary,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
            readiness_status=readiness,
            previous_mode=CutoverMode.mongo_primary.value,
            rollback_available=True,
            promoted_by=None,
            toggle_name=None,
            notes=reason,
            p0_snapshot=p0_snap,
            last_readiness_check_at=_utc_now(),
            is_test_data=is_test_data,
        )
        await _write_audit_entry(
            session,
            building_id=building_id,
            domain=domain,
            action=AuditAction.readiness_checked,
            from_mode=CutoverMode.mongo_primary.value,
            to_mode=CutoverMode.mongo_primary.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason or "powerhouse foundation readiness snapshot updated",
            p0_snapshot=p0_snap,
            is_test_data=is_test_data,
        )
    return await get_or_default_cutover_status(building_id, domain)


async def record_domain_foundation_readiness(
    *,
    building_id: str,
    domain: str,
    validation_passed: bool,
    summary: dict[str, Any],
    reason: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = "system",
    is_test_data: bool = False,
) -> DomainCutoverStatus:
    """Record pre-shadow readiness for ANY domain that has no bespoke foundation check.

    Why this exists (added 2026-08-29)
    ----------------------------------
    Readiness could previously only be recorded by three hardcoded, domain-specific
    functions — identity, powerhouse, financial. Every other domain was therefore
    permanently stuck: `promote_domain`'s `mongo_primary -> postgres_shadow` gate
    requires `readiness_status` to be in `_PRE_SHADOW_READY`, a fresh domain defaults to
    `unknown`, and nothing in the codebase could move it. So a domain like `documents`
    could not enter the cutover lifecycle AT ALL, regardless of how ready its data was.

    That is a large part of why 89 routers are still Mongo-only: the control plane had
    no on-ramp for them.

    This is the generic on-ramp. It is deliberately as safe as its three predecessors:

    * It NEVER promotes and never transitions mode — it writes `mongo_primary` with
      Mongo as both read and write source, exactly like the others. Promotion stays a
      separate, gated, audited `promote_domain` call.
    * `validation_passed=False` records `blocked`, not a silent no-op, so a failed
      readiness check leaves an auditable trail rather than nothing.
    * The caller supplies `summary` — the evidence for the claim. There is no default,
      because "ready" with no evidence behind it is how a domain gets promoted onto
      data that was never checked.
    """
    if not domain or not str(domain).strip():
        raise ValueError("domain is required for a readiness snapshot")

    block_unsafe_global_cutover(building_id)
    canonical = canonical_domain(domain)
    readiness = ReadinessStatus.ready_for_shadow if validation_passed else ReadinessStatus.blocked
    violation = validate_mode_readiness_pair(CutoverMode.mongo_primary, readiness)
    if violation:
        logger.error("record_domain_foundation_readiness: %s (this should be unreachable)", violation)

    p0_snap: dict[str, Any] = {
        "domain_foundation": {
            "status": "pass" if validation_passed else "fail",
            "readiness": readiness.value,
            "summary": summary,
            "checked_at": _utc_now().isoformat(),
        }
    }
    async with _get_bypass_session_context() as session:
        await _upsert_status_row(
            session,
            building_id=building_id,
            domain=canonical,
            mode=CutoverMode.mongo_primary,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
            readiness_status=readiness,
            previous_mode=CutoverMode.mongo_primary.value,
            rollback_available=True,
            promoted_by=None,
            toggle_name=None,
            notes=reason,
            p0_snapshot=p0_snap,
            last_readiness_check_at=_utc_now(),
            is_test_data=is_test_data,
        )
        await _write_audit_entry(
            session,
            building_id=building_id,
            domain=canonical,
            action=AuditAction.readiness_checked,
            from_mode=CutoverMode.mongo_primary.value,
            to_mode=CutoverMode.mongo_primary.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason or f"{canonical} foundation readiness snapshot updated",
            p0_snapshot=p0_snap,
            is_test_data=is_test_data,
        )
    return await get_or_default_cutover_status(building_id, canonical)


async def record_financial_foundation_readiness(
    *,
    building_id: str,
    validation_passed: bool,
    summary: dict[str, Any],
    reason: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = "system",
    is_test_data: bool = False,
    readiness_status: ReadinessStatus | None = None,
) -> DomainCutoverStatus:
    """Record finance onboarding readiness without promoting the finance domain."""
    block_unsafe_global_cutover(building_id)
    readiness = readiness_status or (ReadinessStatus.ready_for_shadow if validation_passed else ReadinessStatus.blocked)
    p0_snap = {
        "financial_onboarding": {
            "status": "pass" if validation_passed else "fail",
            "summary": summary,
            "checked_at": _utc_now().isoformat(),
        }
    }
    async with _get_bypass_session_context() as session:
        await _upsert_status_row(
            session,
            building_id=building_id,
            domain="finance_ledger",
            mode=CutoverMode.mongo_primary,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
            readiness_status=readiness,
            previous_mode=CutoverMode.mongo_primary.value,
            rollback_available=True,
            promoted_by=None,
            toggle_name=None,
            notes=reason,
            p0_snapshot=p0_snap,
            last_readiness_check_at=_utc_now(),
            is_test_data=is_test_data,
        )
        await _write_audit_entry(
            session,
            building_id=building_id,
            domain="finance_ledger",
            action=AuditAction.readiness_checked,
            from_mode=CutoverMode.mongo_primary.value,
            to_mode=CutoverMode.mongo_primary.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason or "financial onboarding readiness snapshot updated",
            p0_snapshot=p0_snap,
            is_test_data=is_test_data,
        )
    return await get_or_default_cutover_status(building_id, "finance_ledger")


# Identity readiness is recorded by two different promotion paths under two different
# p0_snapshot keys:
#   - record_identity_foundation_readiness() writes ["identity_foundation"].status == "pass"
#   - scripts/finalize_identity_core_pg_write.py writes ["identity_pg_write_preflight"].status
#     in {"postgres_write_promoted", "eligible_for_postgres_write_promotion"}
# East Gate's identity_core was finalized via the latter, so ["identity_foundation"] was never
# populated — a key-naming mismatch (NOT a real readiness gap) that blocked ALL finance PG
# writes on 2026-08-04 even though identity_core was genuinely live (mode=postgres_write,
# readiness=promoted, 0 unresolved shadow diffs, 87/87 owner parity). See GAP-CUTOVER-001 and
# the promote scripts' own key-mismatch notes. This helper accepts any of the equivalent
# ready signals so the mismatch cannot re-block writes/promotion.
_IDENTITY_PREFLIGHT_READY_STATUSES = frozenset(
    {"postgres_write_promoted", "eligible_for_postgres_write_promotion"}
)


def is_identity_foundation_ready(identity_status: DomainCutoverStatus | None) -> bool:
    """Return True when identity_core is proven ready to back finance PostgreSQL writes/reads.

    Ready when ANY of:
      1. canonical  p0_snapshot["identity_foundation"].status == "pass"
      2. finalize    p0_snapshot["identity_pg_write_preflight"].status in the ready set
      3. authoritative domain state: identity_core promoted to postgres_read/postgres_write.
    A missing status row (None) is not ready.
    """
    if identity_status is None:
        return False
    snap = identity_status.p0_snapshot or {}
    if (snap.get("identity_foundation") or {}).get("status") == "pass":
        return True
    if (snap.get("identity_pg_write_preflight") or {}).get("status") in _IDENTITY_PREFLIGHT_READY_STATUSES:
        return True
    if (
        identity_status.mode in {CutoverMode.postgres_read, CutoverMode.postgres_write}
        and identity_status.readiness_status == ReadinessStatus.promoted
    ):
        return True
    return False


async def promote_domain(
    *,
    building_id: str,
    domain: str,
    actor_user_id: str,
    actor_role: str,
    reason: str | None = None,
    skip_p0_check: bool = False,
    skip_finance_identity_gate: bool = False,
    is_test_data: bool = False,
) -> tuple[DomainCutoverStatus, str]:
    """Advance a domain one step forward in the cutover lifecycle.

    Returns (updated_status, audit_id).

    Safety checks (in order):
      1. block_unsafe_global_cutover — building_id must be specific
      2. Current mode must have a valid forward transition
      3. P0 readiness must pass (unless skip_p0_check=True and actor is super_admin)
      4. Mode-specific pre-conditions (e.g. cannot promote to read before shadow)
      5. Write the new mode, log the action
    """
    block_unsafe_global_cutover(building_id)

    current = await get_or_default_cutover_status(building_id, domain)
    current_mode = current.mode

    # 1. Validate transition
    next_mode = VALID_FORWARD_TRANSITIONS.get(current_mode)
    if next_mode is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Domain '{domain}' in building {building_id} is in mode "
                f"'{current_mode.value}' which has no valid forward transition."
            ),
        )

    # 1a. Reject a corrupted/inconsistent current row before evaluating anything else.
    # E.g. mongo_primary + shadow_clean (found live for identity_core, 2026-07-14) implies
    # shadow observation happened while never actually leaving mongo_primary — impossible
    # under a correctly-functioning control plane, so the row needs repair, not promotion.
    consistency_violation = validate_mode_readiness_pair(current_mode, current.readiness_status)
    if consistency_violation:
        raise HTTPException(status_code=409, detail=consistency_violation)

    # 1b. mongo_primary -> postgres_shadow additionally requires the domain to have
    # actually finished its own pre-shadow readiness work — the generic P0 check below
    # is platform-level (umbrella toggle, control-plane tables, scheme context) and does
    # NOT look at this domain's own readiness_status, so without this check a domain
    # sitting at blocked/unknown/not_started could enter shadow mode regardless.
    if next_mode == CutoverMode.postgres_shadow and current.readiness_status not in _PRE_SHADOW_READY:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Domain '{domain}' in building {building_id} is not ready to enter shadow mode: "
                f"readiness_status={current.readiness_status.value}. Expected one of "
                f"{sorted(s.value for s in _PRE_SHADOW_READY)}."
            ),
        )

    # 2. P0 readiness gate
    p0 = await _run_p0_check(building_id)
    if p0.overall == "fail" and not skip_p0_check:
        raise HTTPException(
            status_code=409,
            detail=(
                f"P0 readiness check failed for building {building_id}: "
                f"{[k for k,v in p0.gates.items() if v.get('status')=='fail']}. "
                "Resolve P0 blockers before promoting, or use skip_p0_check=True with super_admin."
            ),
        )

    # 3. Mode-specific pre-conditions
    if next_mode == CutoverMode.postgres_read:
        # Must have been in shadow mode long enough to have at least one diff recorded
        if current_mode != CutoverMode.postgres_shadow:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot promote to postgres_read without first passing through postgres_shadow.",
            )

    if next_mode == CutoverMode.postgres_write:
        if current_mode != CutoverMode.postgres_read:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot promote to postgres_write without first promoting to postgres_read.",
            )

    if domain == "finance_ledger" and next_mode == CutoverMode.postgres_read:
        # Prompt 6 safety gate: finance read promotion requires BOTH:
        # 1) financial genesis/onboarding readiness pass
        # 2) identity foundation readiness pass
        #
        # These are checked explicitly from control-plane snapshots so promotion
        # cannot proceed on partial readiness evidence.
        financial_snapshot = (current.p0_snapshot or {}).get("financial_onboarding", {})
        if financial_snapshot.get("status") != "pass":
            raise HTTPException(
                status_code=409,
                detail=(
                    "finance_ledger cannot be promoted to postgres_read: "
                    "financial onboarding readiness snapshot is not pass."
                ),
            )

        # GAP-FIN-030 (2026-08-02): identity_core's readiness gate here is the
        # canonical cross-domain check (identity_core is aliased from "ownership" --
        # see _DOMAIN_ALIASES -- and governs core.lots/core.parties, which finance's
        # per-unit Postgres reads resolve unit/lot mappings against). Do NOT remove
        # or weaken this check for the default path. skip_finance_identity_gate is a
        # narrow, explicit, super_admin-only override for this one sub-check ONLY --
        # it does not affect the financial_onboarding check above or the general P0
        # gate. Live-verified before use (2026-08-02): core.lots row count for East
        # Gate matches the current 87-unit roster, though the table has had no writes
        # since the 2026-05-04 bulk seed (no live sync path yet) -- an explicit,
        # informed risk acceptance, not a data-integrity guarantee. If this override
        # is ever used, the resulting audit log entry (reason field) is the permanent
        # record of that decision -- keep it descriptive.
        if not (skip_finance_identity_gate and actor_role == "super_admin"):
            identity_status = await get_cutover_status(building_id, "identity_core")
            # Accepts identity_core's readiness under either snapshot key (or a promoted
            # domain state) — see is_identity_foundation_ready(): a key-naming mismatch must
            # not force operators to reach for skip_finance_identity_gate when identity_core
            # is genuinely live on Postgres.
            if not is_identity_foundation_ready(identity_status):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "finance_ledger cannot be promoted to postgres_read: "
                        "identity foundation readiness snapshot is not pass."
                    ),
                )

    # 4. Determine new source routing
    read_src, write_src = _MODE_SOURCES[next_mode]

    # 5. Determine new readiness status
    readiness_map = {
        CutoverMode.postgres_shadow: ReadinessStatus.shadow_active,
        CutoverMode.postgres_read: ReadinessStatus.shadow_passing,
        CutoverMode.postgres_write: ReadinessStatus.promoted,
        CutoverMode.mongo_archive: ReadinessStatus.promoted,
    }
    new_readiness = readiness_map.get(next_mode, ReadinessStatus.unknown)

    p0_snap = p0.model_dump(mode="json")
    if current.p0_snapshot:
        merged = dict(current.p0_snapshot)
        merged["cutover_runtime"] = p0_snap
        p0_snap = merged

    async with _get_bypass_session_context() as session:

        await _upsert_status_row(
            session,
            building_id=building_id,
            domain=domain,
            mode=next_mode,
            read_source=read_src,
            write_source=write_src,
            readiness_status=new_readiness,
            previous_mode=current_mode.value,
            rollback_available=True,
            promoted_by=actor_user_id,
            toggle_name=current.toggle_name,
            notes=reason,
            p0_snapshot=p0_snap,
            last_readiness_check_at=_utc_now(),
            is_test_data=is_test_data,
        )

        action_map = {
            CutoverMode.postgres_shadow: AuditAction.entered_shadow,
            CutoverMode.postgres_read: AuditAction.promoted_read,
            CutoverMode.postgres_write: AuditAction.promoted_write,
            CutoverMode.mongo_archive: AuditAction.archived_mongo,
        }
        audit_id = await _write_audit_entry(
            session,
            building_id=building_id,
            domain=domain,
            action=action_map[next_mode],
            from_mode=current_mode.value,
            to_mode=next_mode.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason,
            p0_snapshot=p0_snap,
            is_test_data=is_test_data,
        )

    logger.info(
        "cutover: promoted %s/%s  %s → %s  by %s",
        building_id, domain, current_mode.value, next_mode.value, actor_user_id,
    )

    updated = await get_or_default_cutover_status(building_id, domain)
    return updated, audit_id


async def rollback_domain(
    *,
    building_id: str,
    domain: str,
    actor_user_id: str,
    actor_role: str,
    reason: str,
    is_test_data: bool = False,
) -> tuple[DomainCutoverStatus, str]:
    """Roll a domain back by one step.

    Returns (updated_status, audit_id).
    """
    block_unsafe_global_cutover(building_id)

    current = await get_or_default_cutover_status(building_id, domain)
    current_mode = current.mode

    prev_mode = VALID_ROLLBACK_TRANSITIONS.get(current_mode)
    if prev_mode is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Domain '{domain}' in building {building_id} is in mode "
                f"'{current_mode.value}' which cannot be rolled back further."
            ),
        )

    if not current.rollback_available:
        raise HTTPException(
            status_code=409,
            detail=f"Rollback is not available for domain '{domain}' in building {building_id}.",
        )

    read_src, write_src = _MODE_SOURCES[prev_mode]

    async with _get_bypass_session_context() as session:

        await _upsert_status_row(
            session,
            building_id=building_id,
            domain=domain,
            mode=prev_mode,
            read_source=read_src,
            write_source=write_src,
            readiness_status=ReadinessStatus.rolled_back,
            previous_mode=current_mode.value,
            rollback_available=True,
            promoted_by=actor_user_id,
            toggle_name=current.toggle_name,
            notes=f"[ROLLBACK] {reason}",
            p0_snapshot={},
            is_test_data=is_test_data,
        )

        audit_id = await _write_audit_entry(
            session,
            building_id=building_id,
            domain=domain,
            action=AuditAction.rolled_back,
            from_mode=current_mode.value,
            to_mode=prev_mode.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason,
            p0_snapshot={},
            is_test_data=is_test_data,
        )

    logger.info(
        "cutover: rolled back %s/%s  %s → %s  by %s",
        building_id, domain, current_mode.value, prev_mode.value, actor_user_id,
    )

    updated = await get_or_default_cutover_status(building_id, domain)
    return updated, audit_id


async def list_all_cutover_status(
    *,
    building_id_filter: str | None = None,
    domain_filter: str | None = None,
    limit: int = 200,
) -> list[DomainCutoverStatusSummary]:
    """List all registered domain cutover statuses (super_admin view)."""
    try:
        async with _get_bypass_session_context() as session:

            filters = ["is_test_data = FALSE"]
            params: dict[str, Any] = {"limit": limit}

            if building_id_filter:
                filters.append("building_id = :building_id")
                params["building_id"] = building_id_filter

            if domain_filter:
                filters.append("domain = :domain")
                params["domain"] = domain_filter

            where = " AND ".join(filters)
            rows = await session.execute(
                text(
                    f"""
                    SELECT building_id, domain, mode, readiness_status,
                           read_source, write_source, last_readiness_check_at,
                           last_promoted_at, last_shadow_diff_at, rollback_available,
                           toggle_name, notes, p0_snapshot
                    FROM core.domain_cutover_status
                    WHERE {where}
                    ORDER BY building_id, domain
                    LIMIT :limit
                    """
                ),
                params,
            )
            return [
                DomainCutoverStatusSummary(
                    building_id=r.building_id,
                    domain=r.domain,
                    mode=CutoverMode(r.mode),
                    readiness_status=ReadinessStatus(r.readiness_status),
                    read_source=DataSource(r.read_source),
                    write_source=DataSource(r.write_source),
                    last_readiness_check_at=r.last_readiness_check_at,
                    last_promoted_at=r.last_promoted_at,
                    last_shadow_diff_at=r.last_shadow_diff_at,
                    rollback_available=bool(r.rollback_available),
                    toggle_name=r.toggle_name,
                    notes=r.notes,
                    p0_snapshot=r.p0_snapshot or {},
                )
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("cutover_status: list_all_cutover_status failed (%s)", exc)
        return []


async def list_shadow_diffs(
    *,
    building_id: str,
    domain: str,
    resolved: bool | None = None,
    limit: int = 50,
) -> list[ShadowDiffRecord]:
    """Retrieve shadow diff records for a domain."""
    domain = canonical_domain(domain)
    try:
        async with _get_bypass_session_context() as session:

            filters = [
                "building_id = :building_id",
                "domain = :domain",
                "is_test_data = FALSE",
            ]
            params: dict[str, Any] = {
                "building_id": building_id,
                "domain": domain,
                "limit": limit,
            }
            if resolved is not None:
                filters.append("resolved = :resolved")
                params["resolved"] = resolved

            where = " AND ".join(filters)
            rows = await session.execute(
                text(
                    f"""
                    SELECT id::text, building_id, domain, route, diff_type,
                           mongo_value, pg_value, divergence_score,
                           resolved, resolved_at, notes, created_at
                    FROM core.shadow_diffs
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
            return [
                ShadowDiffRecord(
                    id=str(r.id),
                    building_id=r.building_id,
                    domain=r.domain,
                    route=r.route,
                    diff_type=r.diff_type,
                    mongo_value=r.mongo_value,
                    pg_value=r.pg_value,
                    divergence_score=float(r.divergence_score),
                    resolved=bool(r.resolved),
                    resolved_at=r.resolved_at,
                    notes=r.notes,
                    created_at=r.created_at,
                )
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("cutover_status: list_shadow_diffs failed (%s)", exc)
        return []


async def list_audit_log(
    *,
    building_id: str,
    domain: str,
    limit: int = 50,
) -> list[CutoverAuditEntry]:
    """Retrieve audit log entries for a domain."""
    try:
        async with _get_bypass_session_context() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT id::text, building_id, domain, action,
                           from_mode, to_mode, actor_user_id::text,
                           actor_role, reason, p0_snapshot, metadata, created_at
                    FROM core.cutover_audit_log
                    WHERE building_id = :building_id
                      AND domain = :domain
                      AND is_test_data = FALSE
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"building_id": building_id, "domain": domain, "limit": limit},
            )
            return [
                CutoverAuditEntry(
                    id=str(r.id),
                    building_id=r.building_id,
                    domain=r.domain,
                    action=AuditAction(r.action),
                    from_mode=r.from_mode,
                    to_mode=r.to_mode,
                    actor_user_id=_str_or_none(r.actor_user_id),
                    actor_role=r.actor_role,
                    reason=r.reason,
                    p0_snapshot=r.p0_snapshot or {},
                    metadata=r.metadata or {},
                    created_at=r.created_at,
                )
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("cutover_status: list_audit_log failed (%s)", exc)
        return []
