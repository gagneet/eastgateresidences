"""
Tests for cron.cron_maintenance_intelligence.run_recompute().

Covers:
  - All four services called per building (audit Step 4a fix)
  - generate_levy_projection receives (year_str, bid) — not just year (Bug 2 regression)
  - Per-building error isolation: one building failing does not skip remaining buildings
  - Empty building list: early return, no service calls
  - Lock already held: returns without doing work
  - Multi-tenant isolation: each building_id dispatched independently
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


def _make_db_mock(building_ids=None):
    """Return a mock db suitable for run_recompute():
    - db.locks.update_one → successful acquire (modified_count=1)
    - db.locks.delete_one → no-op
    - db._db.buildings.find().to_list() → list of building dicts
    """
    mock_db = MagicMock()

    lock_result = MagicMock()
    lock_result.modified_count = 1
    lock_result.upserted_id = None
    mock_db.locks.update_one = AsyncMock(return_value=lock_result)
    mock_db.locks.delete_one = AsyncMock()

    buildings_data = [{"building_id": bid} for bid in (["13195"] if building_ids is None else building_ids)]
    buildings_cursor = MagicMock()
    buildings_cursor.to_list = AsyncMock(return_value=buildings_data)
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = buildings_cursor

    return mock_db


_PATCH_BASE = "cron.cron_maintenance_intelligence"


class TestRunRecomputeServiceCalls:
    """Verify all four services are wired into the per-building loop."""

    @pytest.mark.asyncio
    async def test_all_four_services_called_for_single_building(self):
        """recompute, simulate_levy_fairness, detect_capital_shocks, and generate_levy_projection
        must all be called exactly once when there is one active building."""
        year_str = str(date.today().year)
        mock_db = _make_db_mock(["13195"])

        with patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", True), \
                patch(f"{_PATCH_BASE}.recompute_all_maintenance_intelligence", new_callable=AsyncMock) as m_recompute, \
                patch(f"{_PATCH_BASE}.simulate_levy_fairness", new_callable=AsyncMock) as m_fairness, \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", new_callable=AsyncMock) as m_shocks, \
                patch(f"{_PATCH_BASE}.generate_levy_projection", new_callable=AsyncMock) as m_projection:
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()

        m_recompute.assert_called_once_with("13195")
        m_fairness.assert_called_once_with("13195")
        m_shocks.assert_called_once_with("13195")
        m_projection.assert_called_once_with(year_str, "13195")

    @pytest.mark.asyncio
    async def test_all_four_services_called_for_each_building(self):
        """With two active buildings each service is called once per building."""
        year_str = str(date.today().year)
        mock_db = _make_db_mock(["13195", "16244"])

        with patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", True), \
                patch(f"{_PATCH_BASE}.recompute_all_maintenance_intelligence", new_callable=AsyncMock) as m_recompute, \
                patch(f"{_PATCH_BASE}.simulate_levy_fairness", new_callable=AsyncMock) as m_fairness, \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", new_callable=AsyncMock) as m_shocks, \
                patch(f"{_PATCH_BASE}.generate_levy_projection", new_callable=AsyncMock) as m_projection:
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()

        assert m_recompute.call_count == 2
        assert m_fairness.call_count == 2
        assert m_shocks.call_count == 2
        assert m_projection.call_count == 2

        m_recompute.assert_any_call("13195")
        m_recompute.assert_any_call("16244")
        m_shocks.assert_any_call("13195")
        m_shocks.assert_any_call("16244")
        m_projection.assert_any_call(year_str, "13195")
        m_projection.assert_any_call(year_str, "16244")


class TestRunRecomputeBug2Regression:
    """Regression tests for Bug 2: generate_levy_projection called without building_id."""

    @pytest.mark.asyncio
    async def test_generate_levy_projection_receives_two_args(self):
        """generate_levy_projection must always receive (year_str, building_id).
        Previously called as generate_levy_projection(year_str) — TypeError at runtime."""
        year_str = str(date.today().year)
        mock_db = _make_db_mock(["13195", "16244"])

        with patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", True), \
                patch(f"{_PATCH_BASE}.recompute_all_maintenance_intelligence", new_callable=AsyncMock), \
                patch(f"{_PATCH_BASE}.simulate_levy_fairness", new_callable=AsyncMock), \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", new_callable=AsyncMock), \
                patch(f"{_PATCH_BASE}.generate_levy_projection", new_callable=AsyncMock) as m_projection:
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()

        assert call(year_str, "13195") in m_projection.call_args_list
        assert call(year_str, "16244") in m_projection.call_args_list
        # Ensure no call was made with only the year argument
        for c in m_projection.call_args_list:
            assert len(c.args) == 2, (
                f"generate_levy_projection called with {len(c.args)} positional args; "
                f"expected 2 (year_str, building_id). Call: {c}"
            )


class TestRunRecomputeErrorIsolation:
    """Per-building error isolation — one failure must not skip remaining buildings."""

    @pytest.mark.asyncio
    async def test_one_building_failure_does_not_skip_others(self):
        """If recompute_all_maintenance_intelligence raises for building 16244,
        buildings 13195 and 18932 must still complete all four service calls."""
        year_str = str(date.today().year)
        mock_db = _make_db_mock(["13195", "16244", "18932"])
        processed = []

        async def mock_recompute(bid):
            if bid == "16244":
                raise RuntimeError("Simulated DB failure for 16244")
            processed.append(("recompute", bid))

        async def mock_shocks(bid):
            processed.append(("shocks", bid))

        async def mock_projection(year, bid):
            processed.append(("projection", bid))

        with patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", True), \
                patch(f"{_PATCH_BASE}.recompute_all_maintenance_intelligence", side_effect=mock_recompute), \
                patch(f"{_PATCH_BASE}.simulate_levy_fairness", new_callable=AsyncMock), \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", side_effect=mock_shocks), \
                patch(f"{_PATCH_BASE}.generate_levy_projection", side_effect=mock_projection):
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()  # must not raise

        # 13195 and 18932 fully processed
        assert ("recompute", "13195") in processed
        assert ("recompute", "18932") in processed
        assert ("shocks", "13195") in processed
        assert ("shocks", "18932") in processed
        assert ("projection", "13195") in processed
        assert ("projection", "18932") in processed
        # 16244 recompute raised — detect_capital_shocks not called for it (error breaks inner try)
        assert ("shocks", "16244") not in processed
        assert ("projection", "16244") not in processed


class TestRunRecomputeEarlyExits:
    """Early-exit conditions."""

    @pytest.mark.asyncio
    async def test_no_active_buildings_calls_no_services(self):
        """Empty buildings cursor → no service calls, no error."""
        mock_db = _make_db_mock(building_ids=[])

        with patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", True), \
                patch(f"{_PATCH_BASE}.recompute_all_maintenance_intelligence", new_callable=AsyncMock) as m_recompute, \
                patch(f"{_PATCH_BASE}.simulate_levy_fairness", new_callable=AsyncMock) as m_fairness, \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", new_callable=AsyncMock) as m_shocks, \
                patch(f"{_PATCH_BASE}.generate_levy_projection", new_callable=AsyncMock) as m_projection:
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()

        m_recompute.assert_not_called()
        m_fairness.assert_not_called()
        m_shocks.assert_not_called()
        m_projection.assert_not_called()

    @pytest.mark.asyncio
    async def test_database_not_available_returns_immediately(self):
        """When DATABASE_AVAILABLE=False, run_recompute exits before any DB call."""
        mock_db = MagicMock()

        with patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", False), \
                patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", new_callable=AsyncMock) as m_shocks:
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()

        mock_db.locks.update_one.assert_not_called()
        m_shocks.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_already_held_skips_all_work(self):
        """When another worker holds the lock (modified_count=0, upserted_id=None),
        run_recompute returns without calling any service."""
        mock_db = MagicMock()
        locked_result = MagicMock()
        locked_result.modified_count = 0
        locked_result.upserted_id = None
        mock_db.locks.update_one = AsyncMock(return_value=locked_result)
        mock_db.locks.delete_one = AsyncMock()

        with patch(f"{_PATCH_BASE}.db", mock_db), \
                patch(f"{_PATCH_BASE}.DATABASE_AVAILABLE", True), \
                patch(f"{_PATCH_BASE}.recompute_all_maintenance_intelligence", new_callable=AsyncMock) as m_recompute, \
                patch(f"{_PATCH_BASE}.detect_capital_shocks", new_callable=AsyncMock) as m_shocks, \
                patch(f"{_PATCH_BASE}.generate_levy_projection", new_callable=AsyncMock) as m_projection:
            from cron.cron_maintenance_intelligence import run_recompute
            await run_recompute()

        m_recompute.assert_not_called()
        m_shocks.assert_not_called()
        m_projection.assert_not_called()
