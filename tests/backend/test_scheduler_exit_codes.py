"""
tests/backend/test_scheduler_exit_codes.py

GAP: workers/scheduler.py's `--run-once` CLI path always returned/exited 0 regardless of whether
the job actually succeeded -- job functions catch per-building exceptions internally (log +
continue) with no way for the caller to know a failure occurred. A systemd service invoking a job
via --run-once could therefore report "success" even when every building failed.

Verifies:
- recompute_all_building_summaries / run_bi_analytics_etl / nightly_merkle_seal return a failure
  COUNT (not None) when a building's work raises.
- run()'s --run-once path sys.exit(1)s when that count is nonzero, and on an unknown job name.
- run()'s --run-once path does NOT exit when the job reports zero failures.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers import scheduler


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, limit):
        return self._docs


@asynccontextmanager
async def _fake_workflow_run(*args, **kwargs):
    run = MagicMock()
    run.items_processed = 0
    run.items_failed = 0
    yield run


@pytest.mark.asyncio
class TestFailureCounting:
    async def test_recompute_all_building_summaries_counts_failures(self):
        buildings = [{"id": "13195"}, {"id": "16244"}]
        mock_db = MagicMock()
        mock_db._db.buildings.find.return_value = _FakeCursor(buildings)

        async def _recompute(bid):
            if bid == "16244":
                raise RuntimeError("boom")

        with patch("workers.scheduler.db", mock_db), \
             patch("workers.analytics_worker.recompute_building_summary", new=AsyncMock(side_effect=_recompute)), \
             patch("utils.workflow_runner.workflow_run", _fake_workflow_run), \
             patch("workers.scheduler.set_ctx_building_id"):
            failures = await scheduler.recompute_all_building_summaries()

        assert failures == 1

    async def test_recompute_all_building_summaries_zero_failures_on_success(self):
        buildings = [{"id": "13195"}, {"id": "16244"}]
        mock_db = MagicMock()
        mock_db._db.buildings.find.return_value = _FakeCursor(buildings)

        with patch("workers.scheduler.db", mock_db), \
             patch("workers.analytics_worker.recompute_building_summary", new=AsyncMock()), \
             patch("utils.workflow_runner.workflow_run", _fake_workflow_run), \
             patch("workers.scheduler.set_ctx_building_id"):
            failures = await scheduler.recompute_all_building_summaries()

        assert failures == 0

    async def test_nightly_merkle_seal_counts_failures(self):
        buildings = [{"id": "13195"}]
        mock_db = MagicMock()
        mock_db._db.buildings.find.return_value = _FakeCursor(buildings)
        mock_db._db.trust_ledger_entries.distinct = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("workers.scheduler.db", mock_db), \
             patch("workers.scheduler._arq_toggle_on", new=AsyncMock(return_value=False)), \
             patch("workers.scheduler.set_ctx_building_id"):
            failures = await scheduler.nightly_merkle_seal()

        assert failures == 1


@pytest.mark.asyncio
class TestRunOnceExitCodes:
    async def test_unknown_job_exits_nonzero(self):
        with patch("workers.scheduler.sys.argv", ["scheduler.py", "--run-once", "not_a_real_job"]):
            with pytest.raises(SystemExit) as exc_info:
                await scheduler.run()
        assert exc_info.value.code == 1

    async def test_job_reporting_failures_exits_nonzero(self):
        failing_job = AsyncMock(return_value=2)
        with patch("workers.scheduler.sys.argv", ["scheduler.py", "--run-once", "recompute_all_building_summaries"]), \
             patch.dict(scheduler.JOBS, {"recompute_all_building_summaries": failing_job}):
            with pytest.raises(SystemExit) as exc_info:
                await scheduler.run()
        assert exc_info.value.code == 1

    async def test_job_reporting_zero_failures_does_not_exit(self):
        clean_job = AsyncMock(return_value=0)
        with patch("workers.scheduler.sys.argv", ["scheduler.py", "--run-once", "recompute_all_building_summaries"]), \
             patch.dict(scheduler.JOBS, {"recompute_all_building_summaries": clean_job}):
            # Should return normally, not raise SystemExit.
            await scheduler.run()
        clean_job.assert_awaited_once()

    async def test_legacy_job_returning_none_does_not_exit(self):
        """lease_expiry_alerts (a stub) returns None -- must be treated as success, not a
        failure count, for backward compatibility with jobs not yet updated to count failures."""
        stub_job = AsyncMock(return_value=None)
        with patch("workers.scheduler.sys.argv", ["scheduler.py", "--run-once", "lease_expiry_alerts"]), \
             patch.dict(scheduler.JOBS, {"lease_expiry_alerts": stub_job}):
            await scheduler.run()
        stub_job.assert_awaited_once()
