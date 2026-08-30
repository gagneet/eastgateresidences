# @featuretrace:financial_core — the DR sync is actually invoked.
# Layer: test
# Data flow: workers.scheduler.finance_dr_drift_check -> mongo_pg_finance_sync.run_finance_dr_sync (building-scoped).
# Related: backend/workers/scheduler.py
#          backend/services/mongo_pg_finance_sync.py
"""The sync existed, was tested, and was never called. This test is the thing that
would have caught that.

`services/mongo_pg_finance_sync.py` shipped complete and unit-tested, but its only
entry point was a manual `scripts/data_repair/` CLI — nothing in workers/, cron/,
routers/ or server.py referenced it, so the DR position it protects was never measured.
Unit tests on pure functions cannot see that; this asserts the WIRING.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_finance_dr_sync_wiring.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from workers import scheduler  # noqa: E402


class TestSyncIsWired:
    def test_the_job_is_registered_in_JOBS(self):
        """--run-once needs it here, and an unregistered job is unrunnable."""
        assert "finance_dr_drift_check" in scheduler.JOBS

    def test_the_job_is_scheduled_not_merely_defined(self):
        """A job that exists but is never added to the scheduler is the original bug."""
        source = inspect.getsource(scheduler.run)
        assert "scheduler.add_job(finance_dr_drift_check" in source

    def test_the_job_calls_the_canonical_sync_service(self):
        source = inspect.getsource(scheduler.finance_dr_drift_check)
        assert "from services.mongo_pg_finance_sync import run_finance_dr_sync" in source
        assert "run_finance_dr_sync" in source


class TestSyncIsSafeByDefault:
    @pytest.mark.asyncio
    async def test_the_scheduled_job_never_applies(self):
        """Emitting Demo Bank intake is financial evidence and stays a human action."""
        called: list[dict] = []

        async def _fake_sync(building_id, *, apply=False):
            called.append({"building_id": building_id, "apply": apply})
            return {"building_id": building_id, "status": "clean"}

        fake_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"id": "13195"}])
        fake_db._db.buildings.find = MagicMock(return_value=cursor)

        with patch.object(scheduler, "db", fake_db), \
             patch("services.mongo_pg_finance_sync.run_finance_dr_sync", new=_fake_sync):
            failures = await scheduler.finance_dr_drift_check()

        assert failures == 0
        assert called == [{"building_id": "13195", "apply": False}]

    @pytest.mark.asyncio
    async def test_drift_is_reported_not_treated_as_a_job_failure(self):
        """The point is daily visibility, not breaking the scheduler when stores differ."""
        async def _drifting(building_id, *, apply=False):
            return {"building_id": building_id, "status": "drift",
                    "lots_diverged": 3, "lots_compared": 87,
                    "net_gap_cents": -1234, "missing_in_pg": 2}

        fake_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"id": "13195"}])
        fake_db._db.buildings.find = MagicMock(return_value=cursor)

        with patch.object(scheduler, "db", fake_db), \
             patch("services.mongo_pg_finance_sync.run_finance_dr_sync", new=_drifting):
            assert await scheduler.finance_dr_drift_check() == 0

    @pytest.mark.asyncio
    async def test_a_measurement_failure_is_counted_and_does_not_stop_the_batch(self):
        async def _explode(building_id, *, apply=False):
            if building_id == "bad":
                raise RuntimeError("pg down")
            return {"building_id": building_id, "status": "clean"}

        fake_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"id": "bad"}, {"id": "13195"}])
        fake_db._db.buildings.find = MagicMock(return_value=cursor)

        with patch.object(scheduler, "db", fake_db), \
             patch("services.mongo_pg_finance_sync.run_finance_dr_sync", new=_explode):
            assert await scheduler.finance_dr_drift_check() == 1

    @pytest.mark.asyncio
    async def test_archived_buildings_are_excluded(self):
        fake_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        find = MagicMock(return_value=cursor)
        fake_db._db.buildings.find = find

        with patch.object(scheduler, "db", fake_db):
            await scheduler.finance_dr_drift_check()

        query = find.call_args[0][0]
        assert query["is_active"] is True
        assert query["is_archived"] == {"$ne": True}
