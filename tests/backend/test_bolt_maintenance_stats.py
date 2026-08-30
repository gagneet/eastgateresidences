from unittest.mock import AsyncMock, patch

import pytest

from routers.analytics import get_maintenance_stats


@pytest.mark.asyncio
@patch("routers.analytics.db")
async def test_get_maintenance_stats_aggregation(mock_db):
    """Test that get_maintenance_stats uses the consolidated $facet aggregation correctly."""

    mock_user = {"id": "user123", "full_name": "Test User", "role": "admin"}

    mock_res = {
        "open_stats": [{"count": 10}],
        "closed_stats": [{"count": 5}],
        "avg_stats": [{"avg_days": 4.5}]
    }
    mock_db.maintenance_requests.aggregate.return_value.to_list = AsyncMock(return_value=[mock_res])

    result = await get_maintenance_stats(current_user=mock_user, building_id="bldg-1")

    assert result["open_requests"] == 10
    assert result["closed_this_month"] == 5
    assert result["avg_resolution_days"] == 4.5

    assert mock_db.maintenance_requests.aggregate.called
    # get_maintenance_stats makes 2 aggregate calls; the first contains open/closed/avg facets
    pipeline = mock_db.maintenance_requests.aggregate.call_args_list[0][0][0]

    assert "$facet" in pipeline[1]
    facet = pipeline[1]["$facet"]
    assert "open_stats" in facet
    assert "closed_stats" in facet
    assert "avg_stats" in facet


@pytest.mark.asyncio
@patch("routers.analytics.db")
async def test_get_maintenance_stats_returns_real_sla_breach_count(mock_db):
    """sla_breaches must reflect the $facet count, not a hardcoded/omitted value —
    BuildingStrengthCard's "Maintenance SLA" checklist item reads this field directly
    and previously could never turn red because the field was never returned at all."""

    mock_user = {"id": "user123", "full_name": "Test User", "role": "admin"}

    mock_res = {
        "open_stats": [{"count": 10}],
        "closed_stats": [{"count": 5}],
        "sla_breach_stats": [{"count": 3}],
        "avg_stats": [{"avg_days": 4.5}],
    }
    mock_db.maintenance_requests.aggregate.return_value.to_list = AsyncMock(return_value=[mock_res])

    result = await get_maintenance_stats(current_user=mock_user, building_id="bldg-1")

    assert result["sla_breaches"] == 3

    pipeline = mock_db.maintenance_requests.aggregate.call_args_list[0][0][0]
    facet = pipeline[1]["$facet"]
    assert "sla_breach_stats" in facet


@pytest.mark.asyncio
@patch("routers.analytics.db")
async def test_get_maintenance_stats_sla_breaches_defaults_to_zero_when_absent(mock_db):
    mock_user = {"id": "user123", "full_name": "Test User", "role": "admin"}

    mock_res = {
        "open_stats": [{"count": 0}],
        "closed_stats": [{"count": 0}],
        "sla_breach_stats": [],
        "avg_stats": [],
    }
    mock_db.maintenance_requests.aggregate.return_value.to_list = AsyncMock(return_value=[mock_res])

    result = await get_maintenance_stats(current_user=mock_user, building_id="bldg-1")

    assert result["sla_breaches"] == 0


@pytest.mark.asyncio
@patch("routers.analytics.db")
async def test_get_maintenance_stats_fallback(mock_db):
    """Test the fallback logic when no requests are found."""

    mock_user = {"id": "user123", "full_name": "Test User", "role": "admin"}

    mock_res = {
        "open_stats": [],
        "closed_stats": [],
        "avg_stats": []
    }
    mock_db.maintenance_requests.aggregate.return_value.to_list = AsyncMock(return_value=[mock_res])

    result = await get_maintenance_stats(current_user=mock_user, building_id="bldg-1")

    assert result["open_requests"] == 0
    assert result["closed_this_month"] == 0
    # None, not 5.2. That constant was a hardcoded placeholder: a building with
    # no resolution data displayed "Resolution Time 5.2d", indistinguishable on
    # screen from a real measurement. This test previously asserted the
    # placeholder, which is how it survived. Corrected 2026-08-24.
    assert result["avg_resolution_days"] is None


@pytest.mark.asyncio
@patch("routers.analytics.db")
async def test_get_maintenance_stats_aggregation_error(mock_db):
    """Test that get_maintenance_stats handles aggregation errors gracefully."""

    mock_user = {"id": "user123", "full_name": "Test User", "role": "admin"}

    mock_db.maintenance_requests.aggregate.side_effect = Exception("Aggregation error")

    result = await get_maintenance_stats(current_user=mock_user, building_id="bldg-1")

    assert result["open_requests"] == 0
    assert result["closed_this_month"] == 0
    # An aggregation FAILURE must read as unknown, never as a plausible default.
    # 5.2 days plus 94.5% SLA made an outage look like a healthy building.
    assert result["avg_resolution_days"] is None
    assert result["sla_compliance_rate"] is None
