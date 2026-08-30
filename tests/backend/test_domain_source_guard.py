# @featuretrace:cutover-toggle-safety — Tests building/domain source-of-truth guard behavior.
# Data flow: pytest → domain_source_guard → cutover_status_service/core.domain_cutover_status.
# Related: backend/services/domain_source_guard.py
#          backend/services/finance_route_cutover_service.py

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from models.cutover_status import AuditAction, CutoverMode, DataSource, DomainCutoverStatus, ReadinessStatus
from services.cutover_status_service import _sanitize_guard_metadata
from services.domain_source_guard import DomainSourceAuditContext, require_domain_source


def _status(
    *,
    id: str = "status-1",
    domain: str = "finance_ledger",
    mode: CutoverMode = CutoverMode.mongo_primary,
    readiness_status: ReadinessStatus = ReadinessStatus.unknown,
    read_source: DataSource = DataSource.mongo,
    write_source: DataSource = DataSource.mongo,
) -> DomainCutoverStatus:
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id=id,
        building_id="13195",
        domain=domain,
        read_source=read_source,
        write_source=write_source,
        mode=mode,
        readiness_status=readiness_status,
        rollback_available=True,
        created_at=now,
        updated_at=now,
    )



@pytest.fixture(autouse=True)
def audit_recorder():
    with patch(
        "services.domain_source_guard.record_domain_source_guard_audit_event",
        new=AsyncMock(return_value="audit-1"),
    ) as recorder:
        yield recorder


@pytest.mark.asyncio
async def test_missing_domain_state_defaults_to_legacy_mongo_source(caplog, audit_recorder):
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(id="default")),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="read",
            requested_source="postgres",
        )

    assert decision.source == DataSource.mongo
    assert decision.postgres_allowed is False
    assert decision.blocked_reason == "readiness is unknown"
    assert "domain_source_guard_blocked" in caplog.text
    audit_recorder.assert_awaited_once()
    audit_kwargs = audit_recorder.await_args.kwargs
    assert audit_kwargs["action"] == AuditAction.domain_source_missing_state_defaulted
    assert audit_kwargs["domain"] == "finance_ledger"
    assert audit_kwargs["operation"] == "read"
    assert audit_kwargs["building_id"] == "13195"
    assert audit_kwargs["requested_source"] == "postgres"
    assert audit_kwargs["resolved_source"] == "mongo"


@pytest.mark.asyncio
async def test_failed_readiness_blocks_postgres_write_even_when_mode_is_write():
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.postgres_write,
            readiness_status=ReadinessStatus.blocked,
            read_source=DataSource.postgres,
            write_source=DataSource.postgres,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="write",
            requested_source="postgres",
        )

    assert decision.source == DataSource.mongo
    assert decision.postgres_allowed is False
    assert decision.blocked_reason == "readiness is blocked"


@pytest.mark.asyncio
async def test_postgres_read_requires_ready_status_mode_and_read_source():
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.postgres_read,
            readiness_status=ReadinessStatus.shadow_clean,
            read_source=DataSource.postgres,
            write_source=DataSource.mongo,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="read",
            requested_source="postgres",
        )

    assert decision.source == DataSource.postgres
    assert decision.postgres_allowed is True
    assert decision.blocked_reason is None


@pytest.mark.asyncio
async def test_shadow_read_disabled_unless_domain_is_in_shadow_or_promoted_mode():
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.mongo_primary,
            readiness_status=ReadinessStatus.shadow_clean,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="shadow_read",
        )

    assert decision.source == DataSource.mongo
    assert decision.shadow_enabled is False


@pytest.mark.asyncio
async def test_shadow_read_enabled_in_postgres_shadow_mode_only_after_readiness():
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.postgres_shadow,
            readiness_status=ReadinessStatus.ready_for_shadow,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="shadow_read",
        )

    assert decision.source == DataSource.dual
    assert decision.shadow_enabled is True


@pytest.mark.asyncio
async def test_blocked_postgres_request_can_raise_503():
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(readiness_status=ReadinessStatus.blocked)),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_domain_source(
                domain="finance",
                building_id="13195",
                operation="read",
                requested_source="postgres",
                raise_on_blocked_postgres=True,
            )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_requested_source_accepts_datasource_enum():
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.postgres_read,
            readiness_status=ReadinessStatus.shadow_clean,
            read_source=DataSource.postgres,
            write_source=DataSource.mongo,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="read",
            requested_source=DataSource.postgres,
        )

    assert decision.source == DataSource.postgres

@pytest.mark.asyncio
async def test_failed_readiness_creates_audit_event(audit_recorder):
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.postgres_write,
            readiness_status=ReadinessStatus.blocked,
            read_source=DataSource.postgres,
            write_source=DataSource.postgres,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="write",
            requested_source="postgres",
        )

    assert decision.blocked_reason == "readiness is blocked"
    audit_kwargs = audit_recorder.await_args.kwargs
    assert audit_kwargs["action"] == AuditAction.domain_source_readiness_failed
    assert audit_kwargs["operation"] == "write"
    assert audit_kwargs["readiness_status"] == "blocked"


@pytest.mark.asyncio
async def test_blocked_postgres_raise_creates_blocked_audit_event(audit_recorder):
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(readiness_status=ReadinessStatus.blocked)),
    ):
        with pytest.raises(HTTPException):
            await require_domain_source(
                domain="finance",
                building_id="13195",
                operation="read",
                requested_source="postgres",
                raise_on_blocked_postgres=True,
            )

    audit_kwargs = audit_recorder.await_args.kwargs
    assert audit_kwargs["action"] == AuditAction.domain_source_blocked
    assert audit_kwargs["reason"] == "readiness is blocked"


@pytest.mark.asyncio
async def test_downgraded_postgres_request_creates_audit_event(audit_recorder):
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            mode=CutoverMode.mongo_primary,
            readiness_status=ReadinessStatus.shadow_clean,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
        )),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="read",
            requested_source="postgres",
        )

    assert decision.source == DataSource.mongo
    audit_kwargs = audit_recorder.await_args.kwargs
    assert audit_kwargs["action"] == AuditAction.domain_source_downgraded
    assert audit_kwargs["resolved_source"] == "mongo"


@pytest.mark.asyncio
async def test_disabled_shadow_read_creates_audit_event(audit_recorder):
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(
            domain="trust_ledger",
            mode=CutoverMode.mongo_primary,
            readiness_status=ReadinessStatus.shadow_clean,
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
        )),
    ):
        decision = await require_domain_source(
            domain="trust",
            building_id="13195",
            operation="shadow_read",
        )

    assert decision.shadow_enabled is False
    audit_kwargs = audit_recorder.await_args.kwargs
    assert audit_kwargs["action"] == AuditAction.domain_source_shadow_blocked
    assert audit_kwargs["domain"] == "trust_ledger"
    assert audit_kwargs["operation"] == "shadow_read"


@pytest.mark.asyncio
async def test_audit_persistence_failure_does_not_break_guard(caplog, audit_recorder):
    audit_recorder.side_effect = RuntimeError("audit store unavailable")
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(id="default")),
    ):
        decision = await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="read",
            requested_source="postgres",
            audit_context=DomainSourceAuditContext(request_id="req-1", metadata={"bank_account_number": "123"}),
        )

    assert decision.source == DataSource.mongo
    assert "domain_source_guard_audit_failed" in caplog.text


@pytest.mark.asyncio
async def test_guard_metadata_enforces_internal_keys(audit_recorder):
    with patch(
        "services.domain_source_guard.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(id="default")),
    ):
        await require_domain_source(
            domain="finance",
            building_id="13195",
            operation="read",
            requested_source="postgres",
            audit_context=DomainSourceAuditContext(
                metadata={"guard": "spoofed", "requested_domain": "spoofed", "trace_id": "abc-123"},
            ),
        )

    metadata = audit_recorder.await_args.kwargs["metadata"]
    assert metadata["guard"] == "require_domain_source"
    assert metadata["requested_domain"] == "finance"
    assert metadata["trace_id"] == "abc-123"


def test_guard_audit_metadata_sanitizer_excludes_sensitive_payloads():
    safe = _sanitize_guard_metadata({
        "request_payload": {"amount": 100},
        "password": "secret",
        "session_cookie": "cookie",
        "bank_account_number": "123456",
        "route": "/api/v1/finance/levies",
        "source_record_id": "levy-1",
    })

    assert "request_payload" not in safe
    assert "password" not in safe
    assert "session_cookie" not in safe
    assert "bank_account_number" not in safe
    assert safe == {"route": "/api/v1/finance/levies", "source_record_id": "levy-1"}


def test_guard_audit_metadata_sanitizer_excludes_nested_sensitive_payloads():
    safe = _sanitize_guard_metadata({
        "route": "/api/v1/finance/levies",
        "request": {
            "payload": {"amount": 100},
            "metadata": {"token": "secret-token", "reason": "ok"},
            "safe_value": "retained",
        },
        "items": [
            {"session_cookie": "remove-me", "id": 1},
            {"note": "keep-me"},
        ],
    })

    assert safe == {
        "route": "/api/v1/finance/levies",
        "request": {
            "metadata": {"reason": "ok"},
            "safe_value": "retained",
        },
        "items": [
            {"id": 1},
            {"note": "keep-me"},
        ],
    }
