"""Tests for portfolio dashboard (S3)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_portfolio_dashboard_returns_summary():
    """GET /portfolio/dashboard returns buildings list and portfolio summary."""
    mock_db = MagicMock()
    mock_db._db.organisations.find_one = AsyncMock(return_value={"id": "org-001"})
    mock_db._db.organisation_buildings.find.return_value.to_list = AsyncMock(
        return_value=[{"building_id": "13195"}, {"building_id": "test-bld-002"}]
    )
    mock_db._db.buildings.find.return_value.to_list = AsyncMock(
        return_value=[
            {"id": "13195", "name": "East Gate", "lot_count": 87, "is_active": True},
        ]
    )
    mock_db._db.building_summaries.find.return_value.to_list = AsyncMock(
        return_value=[
            {"building_id": "13195", "health_score": 84, "arrears_rate": 1.1}
        ]
    )
    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        from routers.portfolio import get_portfolio_dashboard
        user = {"id": "u1", "role": "strata_manager"}
        result = await get_portfolio_dashboard(current_user=user)
    assert "buildings" in result
    assert "summary" in result


@pytest.mark.asyncio
async def test_portfolio_dashboard_requires_manager():
    """Portfolio dashboard returns 403 for non-managers."""
    from fastapi import HTTPException
    from routers.portfolio import get_portfolio_dashboard

    with pytest.raises(HTTPException) as exc:
        await get_portfolio_dashboard(current_user={"id": "u1", "role": "owner"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_portfolio_workload_returns_per_building_stats():
    """GET /portfolio/workload returns work order and request counts per building."""
    mock_db = MagicMock()
    mock_db._db.organisation_buildings.find.return_value.to_list = AsyncMock(
        return_value=[{"building_id": "13195"}]
    )
    mock_db._db.buildings.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "13195", "name": "East Gate", "lot_count": 87}]
    )
    mock_db.work_orders.count_documents = AsyncMock(return_value=3)
    mock_db.workflow_requests.count_documents = AsyncMock(side_effect=[14, 2])
    with patch("routers.portfolio.db", mock_db):
        from routers.portfolio import get_workload
        user = {"id": "u1", "role": "strata_manager"}
        result = await get_workload(current_user=user)
    assert "buildings" in result
    assert len(result["buildings"]) == 1
