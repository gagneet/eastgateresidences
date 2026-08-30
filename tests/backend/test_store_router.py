# @featuretrace:cutover-toggle-safety — the general datastore dispatch seam.
# Layer: test
# Data flow: store_router.resolve_store -> domain_source_guard.require_domain_source (building-scoped).
# Related: backend/services/store_router.py
#          backend/services/documents_store.py
"""Contract tests for resolve_store — the one place a caller asks which store serves.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_store_router.py -q
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.cutover_status import DataSource  # noqa: E402
from services.store_router import StoreDecision, resolve_store  # noqa: E402


@dataclass
class _FakeDecision:
    source: DataSource
    shadow_enabled: bool = False
    blocked_reason: str | None = None


class TestResolveStore:
    @pytest.mark.asyncio
    async def test_postgres_decision_is_surfaced(self):
        with patch(
            "services.store_router.require_domain_source",
            new=AsyncMock(return_value=_FakeDecision(source=DataSource.postgres)),
        ):
            decision = await resolve_store(domain="documents", building_id="13195", operation="read")
        assert decision.source == "postgres"
        assert decision.use_postgres is True

    @pytest.mark.asyncio
    async def test_missing_control_plane_row_fails_closed_to_mongo(self):
        """A domain with no core.domain_cutover_status row must never serve Postgres."""
        with patch(
            "services.store_router.require_domain_source",
            new=AsyncMock(
                return_value=_FakeDecision(
                    source=DataSource.mongo, blocked_reason="readiness is unknown"
                )
            ),
        ):
            decision = await resolve_store(domain="brand_new_domain", building_id="13195")
        assert decision.source == "mongo"
        assert decision.use_postgres is False
        assert decision.blocked_reason == "readiness is unknown"

    @pytest.mark.asyncio
    async def test_control_plane_failure_degrades_to_mongo_instead_of_raising(self):
        """The control plane being unreachable must not take a page down."""
        with patch(
            "services.store_router.require_domain_source",
            new=AsyncMock(side_effect=RuntimeError("pg down")),
        ):
            decision = await resolve_store(domain="documents", building_id="13195")
        assert decision.source == "mongo"
        assert "control_plane_unavailable" in (decision.blocked_reason or "")

    @pytest.mark.asyncio
    async def test_write_to_postgres_requires_a_mongo_mirror(self):
        """While both stores are live, an unmirrored PG write destroys the DR position."""
        with patch(
            "services.store_router.require_domain_source",
            new=AsyncMock(return_value=_FakeDecision(source=DataSource.postgres)),
        ):
            decision = await resolve_store(
                domain="documents", building_id="13195", operation="write"
            )
        assert decision.mirror_to_mongo is True

    @pytest.mark.asyncio
    async def test_mongo_primary_write_needs_no_mirror(self):
        with patch(
            "services.store_router.require_domain_source",
            new=AsyncMock(return_value=_FakeDecision(source=DataSource.mongo)),
        ):
            decision = await resolve_store(
                domain="documents", building_id="13195", operation="write"
            )
        assert decision.mirror_to_mongo is False

    @pytest.mark.asyncio
    async def test_reads_never_mirror(self):
        with patch(
            "services.store_router.require_domain_source",
            new=AsyncMock(return_value=_FakeDecision(source=DataSource.postgres)),
        ):
            decision = await resolve_store(domain="documents", building_id="13195", operation="read")
        assert decision.mirror_to_mongo is False

    @pytest.mark.asyncio
    async def test_the_guard_is_asked_not_a_feature_toggle(self):
        """A toggle only means a PG path EXISTS; the control plane decides what serves."""
        guard = AsyncMock(return_value=_FakeDecision(source=DataSource.postgres))
        with patch("services.store_router.require_domain_source", new=guard):
            await resolve_store(domain="documents", building_id="13195", operation="read")
        guard.assert_awaited_once()
        kwargs = guard.await_args.kwargs
        assert kwargs["domain"] == "documents"
        assert kwargs["building_id"] == "13195"
        assert kwargs["operation"] == "read"
        # Never raise on a blocked domain — the caller must be able to fall back.
        assert kwargs["raise_on_blocked_postgres"] is False


class TestStoreDecision:
    def test_use_postgres_is_derived_from_source(self):
        assert StoreDecision("d", "13195", "read", "postgres", False, None).use_postgres
        assert not StoreDecision("d", "13195", "read", "mongo", False, None).use_postgres
