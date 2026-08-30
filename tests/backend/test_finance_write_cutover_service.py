# @featuretrace:finance-postgres-write-cutover — Tests route-level PostgreSQL finance write gating and route inventory.
# Data flow: pytest → finance_write_cutover_service → cutover_status_service + feature toggles + domain_source_guard (building-scoped).
# Related: backend/services/finance_write_cutover_service.py
#          backend/routers/finance.py

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from models.cutover_status import CutoverMode, DataSource, DomainCutoverStatus, ReadinessStatus
from services.domain_source_guard import DomainSourceDecision
from services.finance_write_cutover_service import (
    get_finance_write_route_runtime_state,
    list_finance_write_route_policies,
)


def _status(mode: CutoverMode = CutoverMode.mongo_primary) -> DomainCutoverStatus:
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id="finance-status",
        building_id="13195",
        domain="finance_ledger",
        read_source=DataSource.postgres if mode != CutoverMode.mongo_primary else DataSource.mongo,
        write_source=DataSource.postgres if mode == CutoverMode.postgres_write else DataSource.mongo,
        mode=mode,
        readiness_status=ReadinessStatus.promoted,
        rollback_available=True,
        created_at=now,
        updated_at=now,
        p0_snapshot={"financial_onboarding": {"status": "pass"}},
    )


def _identity_status() -> DomainCutoverStatus:
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id="identity-status",
        building_id="13195",
        domain="identity_core",
        read_source=DataSource.postgres,
        write_source=DataSource.mongo,
        mode=CutoverMode.postgres_read,
        readiness_status=ReadinessStatus.promoted,
        rollback_available=True,
        created_at=now,
        updated_at=now,
        p0_snapshot={"identity_foundation": {"status": "pass"}},
    )


def _identity_status_preflight_only(
    *,
    status: str = "postgres_write_promoted",
    mode: CutoverMode = CutoverMode.postgres_write,
    readiness: ReadinessStatus = ReadinessStatus.promoted,
) -> DomainCutoverStatus:
    """East Gate's REAL identity_core: finalized under the 'identity_pg_write_preflight'
    p0_snapshot key (via finalize_identity_core_pg_write.py), NOT the canonical
    'identity_foundation' key. This is the exact shape that blocked all finance PG writes on
    2026-08-04 despite identity_core being genuinely promoted (GAP-CUTOVER-001)."""
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id="identity-status",
        building_id="13195",
        domain="identity_core",
        read_source=DataSource.postgres,
        write_source=DataSource.postgres,
        mode=mode,
        readiness_status=readiness,
        rollback_available=True,
        created_at=now,
        updated_at=now,
        p0_snapshot={"identity_pg_write_preflight": {"status": status}},
    )


def _decision(
    *,
    source: DataSource,
    postgres_allowed: bool,
    blocked_reason: str | None = None,
    mode: CutoverMode = CutoverMode.postgres_write,
    readiness_status: ReadinessStatus = ReadinessStatus.promoted,
    building_id: str = "13195",
) -> DomainSourceDecision:
    return DomainSourceDecision(
        building_id=building_id,
        requested_domain="finance",
        domain="finance_ledger",
        operation="write",
        source=source,
        postgres_allowed=postgres_allowed,
        shadow_enabled=False,
        blocked_reason=blocked_reason,
        mode=mode,
        readiness_status=readiness_status,
    )


def test_write_inventory_classifies_prompt7_slice_and_deferred_routes():
    """Prompt 7 + Prompt 8: create is postgres_write_supported; verify/reject/delete/adjustment are now also supported."""
    policies = {p.route_key: p for p in list_finance_write_route_policies()}

    # Prompt 7 — create still supported
    assert policies["finance.levy_payment_create"].postgres_write_supported is True
    assert policies["finance.levy_payment_create"].idempotency_required is True
    assert policies["finance.levy_payment_create"].audit_required is True
    assert "finance.receipts" in policies["finance.levy_payment_create"].postgres_tables_written

    # Prompt 8 — lifecycle routes now supported
    assert policies["finance.levy_payment_verify"].postgres_write_supported is True
    assert policies["finance.levy_payment_reject"].postgres_write_supported is True
    assert policies["finance.levy_payment_delete"].postgres_write_supported is True
    assert policies["finance.adjustment_journal"].postgres_write_supported is True

    # Still-deferred routes
    assert policies["finance.bank_reconciliation_create"].production_readiness_status == "unsafe until later"


@pytest.mark.asyncio
async def test_write_route_uses_mongo_when_domain_is_mongo_primary():
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.mongo_primary)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(return_value=_decision(
            source=DataSource.mongo,
            postgres_allowed=False,
            blocked_reason="mode/source do not allow postgres write: mongo_primary/mongo",
            mode=CutoverMode.mongo_primary,
        )),
    ):
        state = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_write"] is False
    assert "mongo_primary" in state["blocked_reason"]


@pytest.mark.asyncio
async def test_write_route_uses_postgres_when_all_gates_pass():
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(return_value=_decision(source=DataSource.postgres, postgres_allowed=True)),
    ):
        state = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_write"] is True


@pytest.mark.asyncio
async def test_write_route_postgres_when_identity_ready_via_preflight_key():
    """Regression (GAP-CUTOVER-001 key-mismatch): identity_core recorded readiness under
    'identity_pg_write_preflight' (not 'identity_foundation'). With every other write gate
    passing, the write route must resolve to postgres — the key-naming mismatch must NOT
    block it, because identity_core is genuinely promoted."""
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status_preflight_only()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(return_value=_decision(source=DataSource.postgres, postgres_allowed=True)),
    ):
        state = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_write"] is True


@pytest.mark.asyncio
async def test_write_route_blocks_when_identity_genuinely_not_ready():
    """A genuinely un-ready identity_core (preflight 'blocked', not promoted) still blocks —
    the fix accepts identity_core's real ready signals, it does NOT weaken the gate."""
    not_ready = _identity_status_preflight_only(
        status="blocked", mode=CutoverMode.mongo_primary, readiness=ReadinessStatus.blocked,
    )
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=not_ready),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(return_value=_decision(source=DataSource.postgres, postgres_allowed=True)),
    ):
        state = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_write"] is False
    assert "Identity foundation readiness" in state["blocked_reason"]


def test_is_identity_foundation_ready_accepts_all_ready_signals():
    from services.cutover_status_service import is_identity_foundation_ready

    # 1. canonical key
    assert is_identity_foundation_ready(_identity_status()) is True
    # 2. finalize preflight key — both ready statuses
    assert is_identity_foundation_ready(_identity_status_preflight_only(status="postgres_write_promoted")) is True
    assert is_identity_foundation_ready(
        _identity_status_preflight_only(status="eligible_for_postgres_write_promotion")
    ) is True
    # 3. authoritative promoted domain state even with an empty/unknown snapshot
    empty_snap_promoted = _identity_status_preflight_only(status="unknown")
    assert is_identity_foundation_ready(empty_snap_promoted) is True


def test_is_identity_foundation_ready_rejects_not_ready():
    from services.cutover_status_service import is_identity_foundation_ready

    assert is_identity_foundation_ready(None) is False
    # preflight 'blocked' + not promoted → not ready
    assert is_identity_foundation_ready(
        _identity_status_preflight_only(
            status="blocked", mode=CutoverMode.mongo_primary, readiness=ReadinessStatus.blocked,
        )
    ) is False
    # canonical key present but status 'fail'
    now = datetime.now(UTC)
    failed = DomainCutoverStatus(
        id="identity-status", building_id="13195", domain="identity_core",
        read_source=DataSource.mongo, write_source=DataSource.mongo,
        mode=CutoverMode.mongo_primary, readiness_status=ReadinessStatus.blocked,
        rollback_available=True, created_at=now, updated_at=now,
        p0_snapshot={"identity_foundation": {"status": "fail"}},
    )
    assert is_identity_foundation_ready(failed) is False


@pytest.mark.asyncio
async def test_enabled_global_toggle_cannot_override_failed_domain_readiness():
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(return_value=_decision(
            source=DataSource.mongo,
            postgres_allowed=False,
            blocked_reason="readiness is blocked",
            readiness_status=ReadinessStatus.blocked,
        )),
    ):
        state = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_write"] is False
    assert state["blocked_reason"] == "readiness is blocked"


@pytest.mark.asyncio
async def test_write_route_blocks_when_idempotency_key_missing():
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(return_value=_decision(source=DataSource.postgres, postgres_allowed=True)),
    ):
        state = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key=None,
        )

    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_write"] is False
    assert "Idempotency-Key" in state["blocked_reason"]


@pytest.mark.asyncio
async def test_write_promotion_does_not_affect_other_building():
    async def _status_by_building(building_id: str, domain: str):
        return _status(CutoverMode.postgres_write if building_id == "13195" else CutoverMode.mongo_primary)

    async def _decision_by_building(**kwargs):
        if kwargs["building_id"] == "13195":
            return _decision(source=DataSource.postgres, postgres_allowed=True, building_id="13195")
        return _decision(
            source=DataSource.mongo,
            postgres_allowed=False,
            blocked_reason="mode/source do not allow postgres write: mongo_primary/mongo",
            mode=CutoverMode.mongo_primary,
            building_id=kwargs["building_id"],
        )

    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(side_effect=_status_by_building),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_decision_by_building),
    ):
        east_gate = await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )
        other = await get_finance_write_route_runtime_state(
            building_id="16244",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    assert east_gate["source"] == "postgres"
    assert other["source"] == "mongo"


@pytest.mark.asyncio
async def test_finance_write_guard_receives_audit_context():
    guard = AsyncMock(return_value=_decision(
        source=DataSource.mongo,
        postgres_allowed=False,
        blocked_reason="mode/source do not allow postgres write: mongo_primary/mongo",
        mode=CutoverMode.mongo_primary,
    ))
    with patch(
        "services.finance_write_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.mongo_primary)),
    ), patch(
        "services.finance_write_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=False),
    ), patch(
        "services.finance_write_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "not_started", "critical_count": 0}),
    ), patch(
        "services.finance_write_cutover_service.get_cutover_status",
        new=AsyncMock(return_value=_identity_status()),
    ), patch(
        "services.finance_write_cutover_service.require_domain_source",
        new=guard,
    ):
        await get_finance_write_route_runtime_state(
            building_id="13195",
            route_key="finance.levy_payment_create",
            idempotency_key="idem-1",
        )

    context = guard.await_args.kwargs["audit_context"]
    assert context.route == "/levy-payments"
    assert context.source_service == "finance_write_cutover_service"
    assert context.feature_toggle_key == "financial_pg_writes_enabled"
    assert context.metadata == {"route_key": "finance.levy_payment_create"}
