"""
Unit tests for optimized get_maintenance_analytics endpoint.
Verifies that the $facet consolidation and parallelization work correctly.
"""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from models.user import UserRole
from routers.analytics import get_maintenance_analytics


@pytest.mark.asyncio
async def test_get_maintenance_analytics_optimized():
    mock_user = {"id": "admin-1", "full_name": "Admin User", "role": UserRole.SUPER_ADMIN}

    # Mock results from consolidated maintenance aggregation
    mock_maint_res = [{
        "spent": [{"_id": None, "total": 5000.0}],
        "heatmap": [
            {"_id": "plumbing", "count": 10},
            {"_id": "electrical", "count": 5}
        ],
        "counts": [{"_id": None, "total": 20, "completed": 15, "in_progress": 3}]
    }]

    # Mock results from vendor performance aggregation
    mock_vendor_res = [
        {"_id": "vendor-1", "vendor_name": "Vendor One", "jobs_count": 5, "avg_cost": 1000.0}
    ]

    with patch("routers.analytics.db") as mock_db, \
            patch("routers.analytics.get_approved_user") as mock_auth:
        # Setup mocks for maintenance aggregation
        mock_maint_agg = MagicMock()
        mock_maint_agg.to_list = AsyncMock(return_value=mock_maint_res)
        mock_db.maintenance_requests.aggregate.return_value = mock_maint_agg

        # Setup mocks for vendor aggregation
        mock_vendor_agg = MagicMock()
        mock_vendor_agg.to_list = AsyncMock(return_value=mock_vendor_res)
        mock_db.work_orders.aggregate.return_value = mock_vendor_agg

        # Call the endpoint function
        response = await get_maintenance_analytics(current_user=mock_user)

        # Verify response structure and data
        assert response["total_requests"] == 20
        assert response["completed"] == 15
        assert response["in_progress"] == 3
        assert response["total_spent"] == 5000.0
        assert len(response["vendors"]) == 1
        assert response["vendors"][0]["vendor_name"] == "Vendor One"
        assert len(response["heatmap"]) == 2
        assert response["heatmap"][0]["category"] == "plumbing"
        assert response["heatmap"][0]["count"] == 10

        # Verify both aggregations were called exactly once
        mock_db.maintenance_requests.aggregate.assert_called_once()
        mock_db.work_orders.aggregate.assert_called_once()

        # Verify maintenance aggregation used $facet
        m_args, _ = mock_db.maintenance_requests.aggregate.call_args
        pipeline = m_args[0]
        assert any("$facet" in stage for stage in pipeline)
        facet_stage = next(stage["$facet"] for stage in pipeline if "$facet" in stage)
        assert "spent" in facet_stage
        assert "heatmap" in facet_stage
        assert "counts" in facet_stage


@pytest.mark.asyncio
async def test_get_maintenance_analytics_empty_db():
    mock_user = {"id": "admin-1", "full_name": "Admin User", "role": UserRole.SUPER_ADMIN}

    # Mock empty results
    mock_maint_res = [{
        "spent": [],
        "heatmap": [],
        "counts": []
    }]
    mock_vendor_res = []

    with patch("routers.analytics.db") as mock_db:
        mock_db.maintenance_requests.aggregate.return_value.to_list = AsyncMock(return_value=mock_maint_res)
        mock_db.work_orders.aggregate.return_value.to_list = AsyncMock(return_value=mock_vendor_res)

        response = await get_maintenance_analytics(current_user=mock_user)

        assert response["total_requests"] == 0
        assert response["completed"] == 0
        assert response["in_progress"] == 0
        assert response["total_spent"] == 0
        assert response["vendors"] == []
        assert response["heatmap"] == []
