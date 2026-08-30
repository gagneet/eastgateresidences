"""
tests/backend/test_write_postgres_ownership_period.py

Regression coverage for a 2026-07-24 live incident: unit TH078's owner-transfer
approval (2026-07-16) fired server.py's Postgres ownership sync via a bare
asyncio.create_task() with no reference kept — the task was silently lost to
garbage collection before it could run, leaving core.ownership_periods stale
for weeks with no error anywhere. Found while individually reviewing East Gate
13195's reconstruction queue, when 25 historical-payment ledger postings for
TH078 failed to resolve an owner.

Two fixes covered here:
  1. _fire_and_forget() keeps a strong reference to the task (server.py's own
     _PENDING_BACKGROUND_TASKS set) so it can't be garbage-collected mid-flight.
  2. _write_postgres_ownership_period() now checks open_ownership_period()'s
     return value and logs loudly (not the routine info line) when the insert
     was silently suppressed by ON CONFLICT DO NOTHING — the second half of
     the same failure mode (a stale still-open prior period blocks the new
     one via the EXCLUDE constraint, and the old code never noticed).
"""
from __future__ import annotations

import asyncio
import gc
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

try:
    from backend.server import _fire_and_forget, _PENDING_BACKGROUND_TASKS, _write_postgres_ownership_period
    SERVER_MODULE = "backend.server"
except ImportError:
    from server import _fire_and_forget, _PENDING_BACKGROUND_TASKS, _write_postgres_ownership_period
    SERVER_MODULE = "server"


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


# ── _fire_and_forget() keeps the task alive ────────────────────────────────

class TestFireAndForget:
    @pytest.mark.asyncio
    async def test_task_is_tracked_in_the_pending_set_until_it_completes(self):
        started = asyncio.Event()
        finished = asyncio.Event()

        async def _slow_coro():
            started.set()
            await asyncio.sleep(0.05)
            finished.set()

        task = _fire_and_forget(_slow_coro())
        assert task in _PENDING_BACKGROUND_TASKS

        await started.wait()
        assert task in _PENDING_BACKGROUND_TASKS, "task must still be tracked while running"

        await finished.wait()
        await task
        # add_done_callback() fires via loop.call_soon(), one iteration after
        # the awaited task itself resolves — yield once more so it actually runs.
        await asyncio.sleep(0)
        assert task not in _PENDING_BACKGROUND_TASKS, "completed task must be discarded, not leaked forever"

    @pytest.mark.asyncio
    async def test_task_survives_gc_pressure_while_running(self):
        """The actual regression: without a kept reference, a fire-and-forget
        asyncio.create_task() is eligible for garbage collection at any await
        point. This forces a gc.collect() mid-flight and asserts the coroutine
        still ran to completion — it would not, reliably, with a bare
        asyncio.create_task() and no reference held anywhere."""
        result = {"ran": False}

        async def _coro():
            await asyncio.sleep(0.01)
            result["ran"] = True

        _fire_and_forget(_coro())
        gc.collect()  # the exact class of event that silently ate the TH078 write
        await asyncio.sleep(0.05)

        assert result["ran"] is True


# ── _write_postgres_ownership_period() error visibility ────────────────────

def _patch_pg_layer(*, lot_id="lot-uuid-1", party_id="party-uuid-1",
                     closed_count=1, new_period_id="period-uuid-1"):
    session = MagicMock()
    return (
        patch(f"{SERVER_MODULE}.logger"),
        patch(
            "db_postgres.repos.identity_repo.get_scheme_by_number",
            AsyncMock(return_value={"scheme_id": "scheme-1", "tenant_id": "tenant-1"}),
        ),
        patch("db_postgres.session.async_session_context", MagicMock(return_value=_FakeSessionCtx(session))),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("db_postgres.repos.ownership_repo.get_lot_id_by_number", AsyncMock(return_value=lot_id)),
        patch("db_postgres.repos.ownership_repo.upsert_owner_party", AsyncMock(return_value=party_id)),
        patch("db_postgres.repos.ownership_repo.close_ownership_period", AsyncMock(return_value=closed_count)),
        patch("db_postgres.repos.ownership_repo.open_ownership_period", AsyncMock(return_value=new_period_id)),
    )


class TestWritePostgresOwnershipPeriod:
    @pytest.mark.asyncio
    async def test_logs_info_on_success(self):
        patches = _patch_pg_layer()
        with patches[0] as mock_logger, patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            await _write_postgres_ownership_period(
                building_id="13195", unit_number="TH078", new_owner_name="Tavis Christian Hamer",
                new_owner_email="owner-transfer+x@strataos.local", settlement_date="2026-07-02",
            )
            mock_logger.info.assert_called_once()
            mock_logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_logs_error_not_silence_when_insert_is_conflict_suppressed(self):
        """The exact TH078 failure mode: open_ownership_period() returns None
        (ON CONFLICT DO NOTHING fired, almost always because a prior period
        was never closed) — this must be loud, not indistinguishable from
        success."""
        patches = _patch_pg_layer(new_period_id=None, closed_count=0)
        with patches[0] as mock_logger, patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            await _write_postgres_ownership_period(
                building_id="13195", unit_number="TH078", new_owner_name="Tavis Christian Hamer",
                new_owner_email="owner-transfer+x@strataos.local", settlement_date="2026-07-02",
            )
            mock_logger.error.assert_called_once()
            mock_logger.info.assert_not_called()
            error_args = mock_logger.error.call_args[0]
            assert "TH078" in error_args[1:]  # unit_number is one of the %-format args
