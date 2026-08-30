# @featuretrace:powerhouse-foundation — Tests for the command-foundation health diagnostic surface.
# Layer: test
# Related: backend/services/powerhouse_command_foundation_health.py
#          backend/routers/cutover_admin.py

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from models.cutover_status import CutoverMode, DataSource, DomainCutoverStatus, ReadinessStatus
from services.powerhouse_command_foundation_health import (
    POWERHOUSE_DOMAINS,
    get_powerhouse_command_foundation_health,
)


def _default_status(building_id: str, domain: str) -> DomainCutoverStatus:
    return DomainCutoverStatus(
        id="default",
        building_id=building_id,
        domain=domain,
        read_source=DataSource.mongo,
        write_source=DataSource.mongo,
        mode=CutoverMode.mongo_primary,
        readiness_status=ReadinessStatus.unknown,
        rollback_available=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestPowerhouseCommandFoundationHealth:
    @pytest.mark.asyncio
    async def test_returns_shell_when_no_scheme_context(self):
        import services.powerhouse_command_foundation_health as svc

        with patch.object(
            svc, "get_or_default_cutover_status",
            new=AsyncMock(side_effect=lambda bid, domain: _default_status(bid, domain)),
        ), patch.object(svc, "resolve_scheme_context", new=AsyncMock(return_value=None)):
            result = await get_powerhouse_command_foundation_health("UP-DEMO-001")

        assert result["scheme_found"] is False
        assert result["outbox"] is None
        assert result["idempotency"] is None
        assert len(result["domains"]) == len(POWERHOUSE_DOMAINS)
        assert {d["domain"] for d in result["domains"]} == set(POWERHOUSE_DOMAINS)

    @pytest.mark.asyncio
    async def test_returns_outbox_and_idempotency_counts_when_scheme_exists(self):
        import services.powerhouse_command_foundation_health as svc

        scheme = {"scheme_id": "11111111-1111-1111-1111-111111111111",
                  "tenant_id": "22222222-2222-2222-2222-222222222222"}

        outbox_result = SimpleNamespace(
            fetchone=lambda: SimpleNamespace(pending=3, oldest_pending=None, dead_lettered=1)
        )
        idempotency_result = SimpleNamespace(scalar=lambda: 2)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=[outbox_result, idempotency_result])

        with patch.object(
            svc, "get_or_default_cutover_status",
            new=AsyncMock(side_effect=lambda bid, domain: _default_status(bid, domain)),
        ), patch.object(svc, "resolve_scheme_context", new=AsyncMock(return_value=scheme)), \
                patch.object(svc, "set_tenant", new=AsyncMock()), \
                patch.object(svc, "async_session_context") as mock_ctx:
            mock_ctx.return_value = mock_session

            result = await get_powerhouse_command_foundation_health("13195")

        assert result["scheme_found"] is True
        assert result["outbox"] == {"pending": 3, "oldest_pending": None, "dead_lettered": 1}
        assert result["idempotency"] == {"incomplete_records": 2}
        assert len(result["domains"]) == len(POWERHOUSE_DOMAINS)

    @pytest.mark.asyncio
    async def test_domain_list_covers_all_four_registered_powerhouse_domains(self):
        # POWERHOUSE_DOMAINS must stay in sync with scripts/powerhouse_postgres_readiness.py
        # section [4] and seed_powerhouse_control_plane_statuses().
        assert POWERHOUSE_DOMAINS == [
            "powerhouse_conversations",
            "powerhouse_inbox",
            "powerhouse_workflows",
            "powerhouse_automation",
        ]
