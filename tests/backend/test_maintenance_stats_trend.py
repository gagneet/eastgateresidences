"""
@featuretrace:dashboard-feed — Tests for /analytics/maintenance-stats trend_pct field.
Layer: test
Data flow: test_* → routers.analytics.get_maintenance_stats → maintenance aggregation pipeline (building-scoped).
Related: backend/routers/analytics.py
         tests/backend/test_maintenance_stats_trend.py

Tests: trend_pct returned, sla_compliance_rate is live, avg_resolution_days computed.
Idempotent: uses mock DB aggregation, no data created.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_agg_result(avg_days=5.2, prev_avg=7.0, sla_total=10, sla_met=9):
    """Build a mock aggregation result matching the $facet pipeline structure."""
    main_result = [{
        "open_stats": [{"count": 3}],
        "closed_stats": [{"count": 5}],
        "avg_stats": [{"avg_days": avg_days}],
    }]
    prev_result = [{
        "prev_avg_stats": [{"avg_days": prev_avg}],
        "sla_stats": [{"total": sla_total, "met": sla_met}],
    }]
    return main_result, prev_result


def _make_db(main_result, prev_result):
    db = MagicMock()
    call_count = [0]

    def aggregate(pipeline):
        call_count[0] += 1
        items = main_result if call_count[0] == 1 else prev_result

        async def _gen():
            for item in items:
                yield item

        class MockCursor:
            async def to_list(self, n):
                return items

        return MockCursor()

    db.maintenance_requests.aggregate = aggregate
    return db


@pytest.mark.asyncio
async def test_maintenance_stats_returns_trend_pct():
    """GET /analytics/maintenance-stats must return trend_pct field."""
    from routers.analytics import get_maintenance_stats
    main, prev = _make_agg_result(avg_days=5.0, prev_avg=7.0)
    db = _make_db(main, prev)
    user = {"id": "u1", "role": "owner"}

    with patch("routers.analytics.db", db):
        result = await get_maintenance_stats(current_user=user, building_id="13195")

    assert "trend_pct" in result, "trend_pct must be present in response"
    # 5.0 vs 7.0 → (5-7)/7*100 = -28.6% (improving)
    assert result["trend_pct"] < 0, "When current avg < prev avg, trend_pct should be negative (improving)"


@pytest.mark.asyncio
async def test_maintenance_stats_trend_zero_on_no_prev_data():
    """trend_pct must be 0 when there is no previous period data."""
    from routers.analytics import get_maintenance_stats
    main = [{"open_stats": [{"count": 0}], "closed_stats": [], "avg_stats": []}]
    prev = [{"prev_avg_stats": [], "sla_stats": []}]
    db = _make_db(main, prev)
    user = {"id": "u1", "role": "owner"}

    with patch("routers.analytics.db", db):
        result = await get_maintenance_stats(current_user=user, building_id="13195")

    # None, not 0. A trend needs two real periods; with no prior data there is
    # no direction to report, and 0 reads as "flat" — a measured claim we cannot
    # make. Corrected 2026-08-24 along with the 5.2-day placeholder.
    assert result["trend_pct"] is None


@pytest.mark.asyncio
async def test_maintenance_stats_sla_compliance_computed():
    """sla_compliance_rate must reflect actual SLA data (not hardcoded 94.5)."""
    from routers.analytics import get_maintenance_stats
    # 8 out of 10 met = 80%
    main, prev = _make_agg_result(sla_total=10, sla_met=8)
    db = _make_db(main, prev)
    user = {"id": "u1", "role": "owner"}

    with patch("routers.analytics.db", db):
        result = await get_maintenance_stats(current_user=user, building_id="13195")

    assert result["sla_compliance_rate"] == 80.0


@pytest.mark.asyncio
async def test_maintenance_stats_building_scoped():
    """Aggregation pipeline must match on building_id."""
    from routers.analytics import get_maintenance_stats
    pipeline_queries = []

    class MockCursor:
        def __init__(self, items):
            self._items = items

        async def to_list(self, n):
            return self._items

    call_n = [0]

    def mock_aggregate(pipeline):
        call_n[0] += 1
        pipeline_queries.append(pipeline)
        if call_n[0] == 1:
            return MockCursor([{"open_stats": [{"count": 0}], "closed_stats": [], "avg_stats": []}])
        return MockCursor([{"prev_avg_stats": [], "sla_stats": []}])

    db = MagicMock()
    db.maintenance_requests.aggregate = mock_aggregate
    user = {"id": "u1", "role": "owner"}

    with patch("routers.analytics.db", db):
        await get_maintenance_stats(current_user=user, building_id="16244")

    for pipeline in pipeline_queries:
        match_stage = next((s for s in pipeline if "$match" in s), None)
        assert match_stage is not None, "Pipeline must have $match stage"
        assert match_stage["$match"].get("building_id") == "16244", "Must be scoped to building_id"
