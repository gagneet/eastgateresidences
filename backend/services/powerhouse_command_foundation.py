"""Powerhouse PostgreSQL command foundation.

P2A deliberately stops short of implementing every conversation/workflow
command. This module supplies the shared primitives those commands must use:
typed read outcomes, canonical command results, idempotency, and one
PostgreSQL transaction for domain mutation + audit + outbox.
"""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — Atomic PG command unit of work and typed source outcomes.
# Layer: service
# Data flow: Powerhouse command service → PowerhouseCommandUnitOfWork → domain table + core.audit_events + core.outbox.
# Related: backend/alembic/versions/0071_powerhouse_cmd_foundation.py
#          backend/services/powerhouse_conversation_service.py

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text


class RepositoryReadStatus(StrEnum):
    """Typed PostgreSQL read outcomes used before any Mongo continuity decision."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    SCHEMA_NOT_READY = "schema_not_ready"
    PERMISSION_FAILURE = "permission_failure"
    APPLICATION_ERROR = "application_error"


class ContinuityReason(StrEnum):
    """Reasons allowed to activate read-only Mongo continuity."""

    POSTGRES_UNAVAILABLE = "postgres_unavailable"
    POSTGRES_TIMEOUT = "postgres_timeout"
    POSTGRES_SCHEMA_NOT_READY = "postgres_schema_not_ready"
    OPERATOR_FORCED_CONTINUITY = "operator_forced_continuity"


class CommandStatus(StrEnum):
    """Canonical command result states returned by PostgreSQL command services."""

    CREATED = "created"
    UPDATED = "updated"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    CONFLICT = "conflict"
    INVALID_TRANSITION = "invalid_transition"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


READ_STATUSES_ALLOWED_FOR_CONTINUITY = {
    RepositoryReadStatus.UNAVAILABLE,
    RepositoryReadStatus.TIMEOUT,
    RepositoryReadStatus.SCHEMA_NOT_READY,
}


@dataclass(frozen=True)
class RepositoryReadResult:
    """PostgreSQL repository result with explicit source-state classification."""

    status: RepositoryReadStatus
    value: Any = None
    error_reference: str | None = None
    detail: str | None = None

    @property
    def can_activate_continuity(self) -> bool:
        """Return True only for infrastructure conditions allowed to use Mongo."""
        return self.status in READ_STATUSES_ALLOWED_FOR_CONTINUITY


@dataclass(frozen=True)
class CommandResult:
    """Stable command result returned by P2 command services."""

    status: CommandStatus
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    result_reference: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandContext:
    """Tenant/scheme/idempotency context required for every Powerhouse command."""

    tenant_id: str
    scheme_id: str
    building_id: str
    command_scope: str
    command_type: str
    idempotency_key: str
    actor_user_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    is_test_data: bool = False


@dataclass(frozen=True)
class AuditEventSpec:
    """Audit row fields written in the same transaction as the command."""

    entity_type: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboxEventSpec:
    """Outbox row fields written in the same transaction as the command."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_version: int = 1


MutationCallable = Callable[[Any, CommandContext], Awaitable[CommandResult]]
AuditFactory = Callable[[CommandResult], AuditEventSpec]
OutboxFactory = Callable[[CommandResult], OutboxEventSpec]


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _uuid_or_none(value: str | None) -> str | None:
    """Return a UUID string or None for SQL CAST calls."""
    if not value:
        return None
    return str(UUID(str(value)))


def continuity_reason_for_read_result(result: RepositoryReadResult) -> ContinuityReason | None:
    """Map a typed PG read outcome to a permitted Mongo continuity reason."""
    if result.status == RepositoryReadStatus.UNAVAILABLE:
        return ContinuityReason.POSTGRES_UNAVAILABLE
    if result.status == RepositoryReadStatus.TIMEOUT:
        return ContinuityReason.POSTGRES_TIMEOUT
    if result.status == RepositoryReadStatus.SCHEMA_NOT_READY:
        return ContinuityReason.POSTGRES_SCHEMA_NOT_READY
    return None


# SQLSTATE codes/classes used to separate infrastructure failures (Mongo
# continuity permitted) from programming/application errors (continuity
# forbidden — must surface as a 500). asyncpg sets ``sqlstate`` on its
# exceptions; SQLAlchemy wraps them and exposes the driver exception as
# ``.orig``. Classifying by SQLSTATE avoids importing asyncpg at module load.
_SQLSTATE_CONNECTION_CLASS = "08"  # connection_exception family → UNAVAILABLE
_SQLSTATE_TIMEOUT = {"57014"}  # query_canceled → TIMEOUT
_SQLSTATE_PERMISSION = {"42501", "28000", "28P01"}  # privilege / authorization
_SQLSTATE_SCHEMA_NOT_READY = {"3F000", "42P01"}  # invalid_schema_name / undefined_table
_CONNECTION_ERROR_CLASS_NAMES = {
    "OperationalError",
    "InterfaceError",
    "DBAPIError",
    "DisconnectionError",
    "PostgresConnectionError",
    "ConnectionDoesNotExistError",
    "ConnectionFailureError",
    "TooManyConnectionsError",
    "CannotConnectNowError",
}


def classify_pg_exception(exc: BaseException) -> RepositoryReadStatus:
    """Classify a PostgreSQL read exception into a typed source outcome.

    Only genuine infrastructure conditions — connection loss, timeout, or a
    not-yet-migrated schema/table — return a status that permits Mongo
    continuity. Programming defects (a bad column, a syntax error, a result
    mapping bug) return ``APPLICATION_ERROR`` so they surface as a 500 instead
    of silently activating the fallback. This is the guardrail the Finding 3 /
    Finding 4 class of bug needs: a plain SQL typo must never be mistaken for
    "PostgreSQL is down".
    """
    orig = getattr(exc, "orig", None)
    candidate = orig if orig is not None else exc

    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or isinstance(
        candidate, (TimeoutError, asyncio.TimeoutError)
    ):
        return RepositoryReadStatus.TIMEOUT

    sqlstate = getattr(candidate, "sqlstate", None) or getattr(exc, "sqlstate", None)
    if sqlstate:
        if sqlstate in _SQLSTATE_TIMEOUT:
            return RepositoryReadStatus.TIMEOUT
        if sqlstate in _SQLSTATE_PERMISSION:
            return RepositoryReadStatus.PERMISSION_FAILURE
        if sqlstate in _SQLSTATE_SCHEMA_NOT_READY:
            return RepositoryReadStatus.SCHEMA_NOT_READY
        if str(sqlstate)[:2] == _SQLSTATE_CONNECTION_CLASS:
            return RepositoryReadStatus.UNAVAILABLE
        # Any other server-assigned SQLSTATE (42703 undefined_column,
        # 42601 syntax_error, 23xxx integrity, …) is a programming error.
        return RepositoryReadStatus.APPLICATION_ERROR

    if isinstance(exc, (ConnectionError, OSError)) or isinstance(candidate, (ConnectionError, OSError)):
        return RepositoryReadStatus.UNAVAILABLE

    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        # The PG session/context layer could not be imported/resolved — treat as
        # not-ready infrastructure rather than an application bug.
        return RepositoryReadStatus.SCHEMA_NOT_READY

    if (
        type(exc).__name__ in _CONNECTION_ERROR_CLASS_NAMES
        or type(candidate).__name__ in _CONNECTION_ERROR_CLASS_NAMES
    ):
        return RepositoryReadStatus.UNAVAILABLE

    # Unknown/unclassified: fail loud, never silently fall back.
    return RepositoryReadStatus.APPLICATION_ERROR


async def execute_pg_read(
    read_callable: Callable[[], Awaitable[Any]],
    *,
    wrap_value: Callable[[Any], Any] | None = None,
    none_status: RepositoryReadStatus = RepositoryReadStatus.UNAVAILABLE,
    none_detail: str = "PostgreSQL read returned no result",
) -> RepositoryReadResult:
    """Run a PostgreSQL read and classify its outcome without swallowing bugs.

    A returned value becomes ``SUCCESS`` — an empty list/dict is a valid success
    and must not trigger fallback. ``None`` maps to ``none_status`` (an
    infrastructure/not-configured signal; default ``UNAVAILABLE`` to preserve the
    established fallback behaviour for a building with no PostgreSQL context).
    Any raised exception is classified by :func:`classify_pg_exception`;
    ``HTTPException`` is re-raised unchanged so deliberate 403/404 semantics from
    the repository survive.
    """
    from fastapi import HTTPException  # local import: keep module import surface minimal

    try:
        value = await read_callable()
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - classified, never silently swallowed
        status = classify_pg_exception(exc)
        return RepositoryReadResult(
            status=status,
            detail=str(exc) or type(exc).__name__,
            error_reference=type(exc).__name__,
        )
    if value is None:
        return RepositoryReadResult(status=none_status, detail=none_detail)
    return RepositoryReadResult(
        status=RepositoryReadStatus.SUCCESS,
        value=wrap_value(value) if wrap_value is not None else value,
    )


class PowerhouseCommandUnitOfWork:
    """Execute a Powerhouse command atomically in PostgreSQL.

    The supplied mutation callback performs exactly the domain write(s). This
    unit of work wraps that mutation with idempotency, `core.audit_events` and
    `core.outbox`, all under the same session context. If any insert fails, the
    surrounding `async_session_context()` rolls back the whole command.
    """

    def __init__(
        self,
        *,
        session_context_factory: Callable[[], Any] | None = None,
        set_tenant_func: Callable[[Any, str], Awaitable[None]] | None = None,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._set_tenant = set_tenant_func

    async def execute(
        self,
        *,
        context: CommandContext,
        mutate: MutationCallable,
        audit_factory: AuditFactory,
        outbox_factory: OutboxFactory,
    ) -> CommandResult:
        """Run mutation, audit and outbox writes in one PostgreSQL transaction."""
        from db_postgres.session import async_session_context, set_tenant

        session_context_factory = self._session_context_factory or async_session_context
        set_tenant_func = self._set_tenant or set_tenant

        async with session_context_factory() as session:
            await set_tenant_func(session, context.tenant_id)

            replay = await self._claim_idempotency(session, context)
            if replay is not None:
                return replay

            result = await mutate(session, context)
            if result.status not in {CommandStatus.CREATED, CommandStatus.UPDATED}:
                await self._finalise_idempotency(session, context, result)
                return result

            audit = audit_factory(result)
            outbox = outbox_factory(result)
            aggregate_id = _uuid_or_none(result.aggregate_id)
            await self._write_audit_event(session, context, result, audit, aggregate_id)
            await self._write_outbox_event(session, context, result, outbox, aggregate_id)
            await self._finalise_idempotency(session, context, result)
            return result

    async def _claim_idempotency(self, session: Any, context: CommandContext) -> CommandResult | None:
        """Insert or lock an idempotency row and return replayed result when complete."""
        row = await session.execute(
            text(
                """
                INSERT INTO core.command_idempotency_records (
                    tenant_id, scheme_id, building_id, command_scope,
                    command_type, idempotency_key, result_status,
                    result_reference, is_test_data
                ) VALUES (
                    CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), :building_id, :command_scope,
                    :command_type, :idempotency_key, 'started',
                    CAST(:result_reference AS JSONB), :is_test_data
                )
                ON CONFLICT (tenant_id, scheme_id, command_scope, command_type, idempotency_key)
                DO UPDATE SET
                    replay_count = core.command_idempotency_records.replay_count + 1,
                    last_seen_at = NOW()
                RETURNING
                    result_status,
                    aggregate_type,
                    aggregate_id::text AS aggregate_id,
                    result_reference,
                    completed_at
                """
            ),
            {
                "tenant_id": context.tenant_id,
                "scheme_id": context.scheme_id,
                "building_id": context.building_id,
                "command_scope": context.command_scope,
                "command_type": context.command_type,
                "idempotency_key": context.idempotency_key,
                "result_reference": json.dumps({}),
                "is_test_data": context.is_test_data,
            },
        )
        record = row.fetchone()
        if record and record.completed_at is not None:
            return CommandResult(
                status=CommandStatus.IDEMPOTENT_REPLAY,
                aggregate_type=record.aggregate_type,
                aggregate_id=record.aggregate_id,
                result_reference=dict(record.result_reference or {}),
            )
        return None

    async def _write_audit_event(
        self,
        session: Any,
        context: CommandContext,
        result: CommandResult,
        audit: AuditEventSpec,
        aggregate_id: str | None,
    ) -> None:
        """Insert the canonical audit event for a successful command mutation."""
        await session.execute(
            text(
                """
                INSERT INTO core.audit_events (
                    tenant_id, scheme_id, entity_type, entity_id, action,
                    actor_user_id, event_payload, created_at
                ) VALUES (
                    CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), :entity_type,
                    CAST(:entity_id AS UUID), :action, CAST(:actor_user_id AS UUID),
                    CAST(:event_payload AS JSONB), :created_at
                )
                """
            ),
            {
                "tenant_id": context.tenant_id,
                "scheme_id": context.scheme_id,
                "entity_type": audit.entity_type,
                "entity_id": aggregate_id,
                "action": audit.action,
                "actor_user_id": _uuid_or_none(context.actor_user_id),
                "event_payload": json.dumps(
                    {
                        **audit.payload,
                        "command_type": context.command_type,
                        "command_status": result.status.value,
                        "correlation_id": context.correlation_id,
                    }
                ),
                "created_at": _utc_now(),
            },
        )

    async def _write_outbox_event(
        self,
        session: Any,
        context: CommandContext,
        result: CommandResult,
        outbox: OutboxEventSpec,
        aggregate_id: str | None,
    ) -> None:
        """Insert the transactional outbox row for a successful command mutation."""
        await session.execute(
            text(
                """
                INSERT INTO core.outbox (
                    id, tenant_id, scheme_id, event_type, aggregate_id,
                    aggregate_type, payload, correlation_id, causation_id,
                    event_version, created_at, published_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                    :event_type, CAST(:aggregate_id AS UUID), :aggregate_type,
                    CAST(:payload AS JSONB), :correlation_id, :causation_id,
                    :event_version, :created_at, NULL
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": context.tenant_id,
                "scheme_id": context.scheme_id,
                "event_type": outbox.event_type,
                "aggregate_id": aggregate_id,
                "aggregate_type": result.aggregate_type,
                "payload": json.dumps(
                    {
                        **outbox.payload,
                        "building_id": context.building_id,
                        "command_type": context.command_type,
                        "idempotency_key": context.idempotency_key,
                    }
                ),
                "correlation_id": context.correlation_id,
                "causation_id": context.causation_id,
                "event_version": outbox.event_version,
                "created_at": _utc_now(),
            },
        )

    async def _finalise_idempotency(self, session: Any, context: CommandContext, result: CommandResult) -> None:
        """Persist the command result so later retries replay without mutation."""
        await session.execute(
            text(
                """
                UPDATE core.command_idempotency_records
                SET
                    aggregate_type = :aggregate_type,
                    aggregate_id = CAST(:aggregate_id AS UUID),
                    result_status = :result_status,
                    result_reference = CAST(:result_reference AS JSONB),
                    completed_at = COALESCE(completed_at, NOW()),
                    last_seen_at = NOW()
                WHERE tenant_id = CAST(:tenant_id AS UUID)
                  AND scheme_id = CAST(:scheme_id AS UUID)
                  AND command_scope = :command_scope
                  AND command_type = :command_type
                  AND idempotency_key = :idempotency_key
                """
            ),
            {
                "aggregate_type": result.aggregate_type,
                "aggregate_id": _uuid_or_none(result.aggregate_id),
                "result_status": result.status.value,
                "result_reference": json.dumps(result.result_reference),
                "tenant_id": context.tenant_id,
                "scheme_id": context.scheme_id,
                "command_scope": context.command_scope,
                "command_type": context.command_type,
                "idempotency_key": context.idempotency_key,
            },
        )


async def seed_powerhouse_control_plane_statuses(
    session: Any,
    *,
    tenant_id: str,
    scheme_id: str,
    building_id: str,
    is_test_data: bool = False,
) -> list[str]:
    """Seed Powerhouse domains as PostgreSQL-primary with read-only Mongo continuity."""
    domains = [
        "powerhouse_conversations",
        "powerhouse_inbox",
        "powerhouse_workflows",
        "powerhouse_automation",
    ]
    for domain in domains:
        await session.execute(
            text(
                """
                INSERT INTO core.domain_cutover_status (
                    tenant_id, building_id, scheme_id, domain,
                    read_source, write_source, mode, readiness_status,
                    rollback_available, continuity_source, continuity_policy,
                    notes, p0_snapshot, is_test_data, created_at, updated_at
                ) VALUES (
                    CAST(:tenant_id AS UUID), :building_id, CAST(:scheme_id AS UUID), :domain,
                    'postgres', 'postgres', 'postgres_write', 'promoted',
                    TRUE, 'mongo', 'classified_read_only',
                    :notes, CAST(:p0_snapshot AS JSONB), :is_test_data, NOW(), NOW()
                )
                ON CONFLICT (building_id, domain)
                DO UPDATE SET
                    read_source = 'postgres',
                    write_source = 'postgres',
                    mode = 'postgres_write',
                    readiness_status = 'promoted',
                    continuity_source = 'mongo',
                    continuity_policy = 'classified_read_only',
                    notes = EXCLUDED.notes,
                    p0_snapshot = EXCLUDED.p0_snapshot,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "building_id": building_id,
                "domain": domain,
                "notes": "Powerhouse P2A seed: PostgreSQL primary with classified read-only Mongo continuity.",
                "p0_snapshot": json.dumps({"phase": "P2A", "continuity_source": "mongo"}),
                "is_test_data": is_test_data,
            },
        )
    return domains
