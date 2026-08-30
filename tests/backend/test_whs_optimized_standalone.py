import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add backend to path (conftest.py already handles this, but keep for standalone execution)
_backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from routers.whs import get_whs_dashboard


def _make_agg_mock(data):
    """Return a mock aggregate cursor whose .to_list() resolves to `data`."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=data)
    return cursor


@pytest.mark.asyncio
async def test_get_whs_dashboard_optimized_logic():
    building_id = "test-building"
    current_user = {"role": "super_admin", "_id": "admin_id"}

    today_str = date.today().isoformat()

    # Mock DB — data structure must match the $facet aggregation output
    mock_db = MagicMock()

    induction_res = [{
        "counts": [{"total": 10, "expired": 2, "current": 5}],
        "expiring_soon": [
            {
                "id": "1",
                "contractor_name": "C1",
                "expiry_date": today_str,
                "expiry_prefix": today_str,  # required field used for date parsing
            }
        ]
    }]

    swms_res = [{
        "total": [{"count": 20}],
        "draft": [{"count": 5}],
        "approved": [{"count": 10}],
        "high_risk_unapproved": [{"count": 2}]
    }]

    incident_res = [{
        "total": [{"count": 5}],
        "open": [{"count": 1}],
        "authority_pending": [{"count": 1}]
    }]

    mock_db.whs_inductions.aggregate.return_value = _make_agg_mock(induction_res)
    mock_db.whs_swms.aggregate.return_value = _make_agg_mock(swms_res)
    mock_db.whs_incidents.aggregate.return_value = _make_agg_mock(incident_res)

    with patch("routers.whs._get_db", return_value=mock_db):
        result = await get_whs_dashboard(building_id, current_user)

        # Verify structure and values
        assert result["building_id"] == building_id
        assert result["inductions"]["total"] == 10
        assert result["inductions"]["expired"] == 2
        assert result["inductions"]["current"] == 5
        assert result["inductions"]["pending_renewal"] == 3  # 10 - 2 - 5
        assert len(result["inductions"]["expiring_in_30_days"]) == 1
        assert result["inductions"]["expiring_in_30_days"][0]["days_until_expiry"] == 0

        assert result["swms"]["total"] == 20
        assert result["swms"]["draft"] == 5
        assert result["swms"]["approved"] == 10
        assert result["swms"]["high_risk_unapproved"] == 2

        assert result["incidents"]["total"] == 5
        assert result["incidents"]["open"] == 1
        assert result["incidents"]["authority_notification_pending"] == 1

        # Verify issues list logic (messages match whs.py issue construction)
        issue_messages = [i["message"] for i in result["issues"]]
        assert any("2 inductions have expired" in m for m in issue_messages)
        assert any("2 high-risk SWMS unapproved" in m for m in issue_messages)
        assert any("1 incidents require notification" in m for m in issue_messages)
        assert any("1 investigations still open" in m for m in issue_messages)

        # Compliant = False because there are critical issues
        assert result["compliant"] is False


@pytest.mark.asyncio
async def test_get_whs_dashboard_empty_data():
    building_id = "test-building"
    current_user = {"role": "super_admin", "_id": "admin_id"}

    mock_db = MagicMock()

    # Empty facet output — each collection returns an empty document
    empty_res = [{}]

    mock_db.whs_inductions.aggregate.return_value = _make_agg_mock(empty_res)
    mock_db.whs_swms.aggregate.return_value = _make_agg_mock(empty_res)
    mock_db.whs_incidents.aggregate.return_value = _make_agg_mock(empty_res)

    with patch("routers.whs._get_db", return_value=mock_db):
        result = await get_whs_dashboard(building_id, current_user)

        assert result["inductions"]["total"] == 0
        assert result["swms"]["total"] == 0
        assert result["incidents"]["total"] == 0
        assert result["issues"] == []
        assert result["compliant"] is True
