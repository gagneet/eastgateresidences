"""
Tests for the generalized Phase D scripts (backend/scripts/phase_d_activate.py and
backend/scripts/phase_d_shadow_status.py), added 2026-07-14 as part of the PostgreSQL
shadow-read expansion rollout.

Covers:
  1.  phase_d_activate: dry-run makes zero writes (promote_domain/rollback_domain not called)
  2.  phase_d_activate: --apply without --reason fails at the CLI layer
  3.  phase_d_activate: wildcard building_id rejected before any DB call
  4.  phase_d_activate: promote/rollback errors (HTTPException, ValueError) surface as
      an "error" key, not an unhandled exception
  5.  phase_d_activate: no cross-domain leakage — the domain param always flows through
  6.  phase_d_shadow_status: unregistered domain returns an error, not a crash
  7.  phase_d_shadow_status: phase_d_ready aggregates per-route classify_route_day results
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_BACKEND_DIR = str(Path(__file__).resolve().parents[2] / "backend")
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "backend" / "scripts")
for _p in (_BACKEND_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import phase_d_activate  # noqa: E402
import phase_d_shadow_status  # noqa: E402

from models.cutover_status import CutoverMode, DataSource, DomainCutoverStatus, ReadinessStatus  # noqa: E402


def _make_status(mode=CutoverMode.mongo_primary, readiness_status=ReadinessStatus.ready_for_shadow, domain="identity_core"):
    from datetime import UTC, datetime
    from uuid import uuid4
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id=str(uuid4()), building_id="13195", domain=domain,
        read_source=DataSource.mongo, write_source=DataSource.mongo,
        mode=mode, readiness_status=readiness_status, rollback_available=True,
        p0_snapshot={}, created_at=now, updated_at=now,
    )


# ---------------------------------------------------------------------------
# 1-5. phase_d_activate.py
# ---------------------------------------------------------------------------

class TestPhaseDActivateDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_makes_zero_writes(self):
        current = _make_status()
        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(phase_d_activate, "promote_domain", new=AsyncMock()) as mock_promote, \
             patch.object(phase_d_activate, "rollback_domain", new=AsyncMock()) as mock_rollback:
            result = await phase_d_activate.run(
                domain="identity_core", building_id="13195",
                dry_run=True, do_apply=False, do_rollback=False, reason=None,
            )
        assert result["dry_run"] is True
        assert result["proposed_mode"] == "postgres_shadow"
        mock_promote.assert_not_called()
        mock_rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_flags_defaults_to_dry_run_shape(self):
        """run() itself treats dry_run=False + do_apply=False + do_rollback=False as a preview too."""
        current = _make_status()
        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(phase_d_activate, "promote_domain", new=AsyncMock()) as mock_promote:
            result = await phase_d_activate.run(
                domain="identity_core", building_id="13195",
                dry_run=False, do_apply=False, do_rollback=False, reason=None,
            )
        assert result["dry_run"] is True
        mock_promote.assert_not_called()

    def test_apply_without_reason_fails_at_cli_layer(self):
        with patch("sys.argv", ["phase_d_activate.py", "--domain", "identity_core", "--apply"]):
            with pytest.raises(SystemExit):
                phase_d_activate.main()

    def test_rollback_without_reason_fails_at_cli_layer(self):
        with patch("sys.argv", ["phase_d_activate.py", "--domain", "identity_core", "--rollback"]):
            with pytest.raises(SystemExit):
                phase_d_activate.main()


class TestPhaseDActivateWildcardRejection:
    @pytest.mark.asyncio
    async def test_wildcard_building_id_rejected_before_any_status_lookup(self):
        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=AsyncMock()) as mock_lookup:
            result = await phase_d_activate.run(
                domain="identity_core", building_id="*",
                dry_run=True, do_apply=False, do_rollback=False, reason=None,
            )
        assert "error" in result
        assert "Global cutover is not permitted" in result["error"]
        mock_lookup.assert_not_called()


class TestPhaseDActivateErrorSurfacing:
    @pytest.mark.asyncio
    async def test_promote_domain_http_exception_becomes_error_key_not_crash(self):
        current = _make_status(readiness_status=ReadinessStatus.blocked)
        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(phase_d_activate, "_resolve_actor_user_id", new=AsyncMock(return_value="u-1")), \
             patch.object(phase_d_activate, "promote_domain",
                          new=AsyncMock(side_effect=HTTPException(status_code=409, detail="not ready"))):
            result = await phase_d_activate.run(
                domain="identity_core", building_id="13195",
                dry_run=False, do_apply=True, do_rollback=False, reason="test",
            )
        assert result["error"] == "not ready"
        assert result["status_code"] == 409

    @pytest.mark.asyncio
    async def test_promote_domain_value_error_becomes_error_key_not_crash(self):
        current = _make_status()
        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(phase_d_activate, "_resolve_actor_user_id", new=AsyncMock(return_value="u-1")), \
             patch.object(phase_d_activate, "promote_domain", new=AsyncMock(side_effect=ValueError("boom"))):
            result = await phase_d_activate.run(
                domain="identity_core", building_id="13195",
                dry_run=False, do_apply=True, do_rollback=False, reason="test",
            )
        assert "error" in result
        assert "boom" in result["error"]


class TestPhaseDActivateDomainFlowsThrough:
    @pytest.mark.asyncio
    async def test_alias_canonicalized_and_passed_to_promote_domain(self):
        current = _make_status(domain="finance_ledger")
        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=AsyncMock(return_value=current)) as mock_get, \
             patch.object(phase_d_activate, "_resolve_actor_user_id", new=AsyncMock(return_value="u-1")), \
             patch.object(phase_d_activate, "promote_domain",
                          new=AsyncMock(return_value=(current, "audit-1"))) as mock_promote:
            result = await phase_d_activate.run(
                domain="finance", building_id="13195",  # alias, not canonical
                dry_run=False, do_apply=True, do_rollback=False, reason="test",
            )
        assert result["domain"] == "finance_ledger"
        mock_get.assert_awaited_with("13195", "finance_ledger")
        assert mock_promote.await_args.kwargs["domain"] == "finance_ledger"

    @pytest.mark.asyncio
    async def test_building_a_and_b_never_cross_contaminate(self):
        status_a = _make_status(domain="identity_core")
        status_b = _make_status(domain="identity_core")
        calls = []

        async def fake_get(building_id, domain):
            calls.append(building_id)
            return status_a if building_id == "13195" else status_b

        with patch.object(phase_d_activate, "get_or_default_cutover_status", new=fake_get):
            await phase_d_activate.run(domain="identity_core", building_id="13195",
                                        dry_run=True, do_apply=False, do_rollback=False, reason=None)
            await phase_d_activate.run(domain="identity_core", building_id="16244",
                                        dry_run=True, do_apply=False, do_rollback=False, reason=None)

        assert calls == ["13195", "16244"]


# ---------------------------------------------------------------------------
# 6-7. phase_d_shadow_status.py
# ---------------------------------------------------------------------------

class TestPhaseDShadowStatusUnregisteredDomain:
    @pytest.mark.asyncio
    async def test_unregistered_domain_returns_error_not_crash(self):
        result = await phase_d_shadow_status.run(building_id="13195", domain="governance")
        assert "error" in result
        assert "No contract routes registered" in result["error"]


class TestPhaseDShadowStatusReadyAggregation:
    @pytest.mark.asyncio
    async def test_phase_d_ready_true_only_when_every_route_hits_target(self):
        routes = ["finance.summary", "finance.building_overview", "finance.unit_dashboard_overview",
                  "finance.levy_kpi", "finance.arrears_detail"]

        async def fake_classify(*, building_id, domain, route, coverage_date):
            return "clean"  # every day, every route: clean

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()
        mock_conn.close = AsyncMock()

        with patch.object(phase_d_shadow_status, "classify_route_day", new=fake_classify), \
             patch("asyncpg.connect", new=AsyncMock(return_value=mock_conn)):
            result = await phase_d_shadow_status.run(building_id="13195", domain="finance_ledger", last_hours=168)

        assert result["phase_d_ready"] is True
        for route in routes:
            assert result["consecutive_clean_days_per_route"][route]["streak"] == 30  # capped by the 30-day lookback

    @pytest.mark.asyncio
    async def test_phase_d_ready_false_when_one_route_never_clean(self):
        async def fake_classify(*, building_id, domain, route, coverage_date):
            if route == "finance.summary":
                return "no_traffic"
            return "clean"

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()
        mock_conn.close = AsyncMock()

        with patch.object(phase_d_shadow_status, "classify_route_day", new=fake_classify), \
             patch("asyncpg.connect", new=AsyncMock(return_value=mock_conn)):
            result = await phase_d_shadow_status.run(building_id="13195", domain="finance_ledger", last_hours=168)

        assert result["phase_d_ready"] is False
        assert result["consecutive_clean_days_per_route"]["finance.summary"]["streak"] == 0
        assert result["consecutive_clean_days_per_route"]["finance.building_overview"]["streak"] == 30
