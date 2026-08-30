"""
Performance optimization tests for WHS dashboard — consolidated aggregation facets.
"""
import os
import sys
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Ensure backend is in path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, 'backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from routers.whs import get_whs_dashboard


@pytest.mark.asyncio
async def test_get_whs_dashboard_optimized():
    building_id = "b1"
    mock_user = {"id": "admin-1", "full_name": "Admin User", "role": "super_admin", "_id": "admin_oid"}

    # Setup mock data matching the new aggregation output format
    today_str = date.today().isoformat()
    pending_date = (date.today() + timedelta(days=15)).isoformat()

    induction_res = [{
        "counts": [{"total": 4, "expired": 1, "current": 1}],
        "expiring_soon": [
            {
                "id": "i3",
                "contractor_name": "C3",
                "expiry_date": pending_date,
                "expiry_prefix": pending_date
            }
        ]
    }]

    swms_res = [{
        "total": [{"count": 10}],
        "draft": [{"count": 3}],
        "approved": [{"count": 5}],
        "high_risk_unapproved": [{"count": 2}]
    }]

    incident_res = [{
        "total": [{"count": 5}],
        "open": [{"count": 1}],
        "authority_pending": [{"count": 1}]
    }]

    mock_db = MagicMock()

    mock_agg_ind = MagicMock()
    mock_agg_ind.to_list = AsyncMock(return_value=induction_res)
    mock_db.whs_inductions.aggregate.return_value = mock_agg_ind

    mock_agg_swms = MagicMock()
    mock_agg_swms.to_list = AsyncMock(return_value=swms_res)
    mock_db.whs_swms.aggregate.return_value = mock_agg_swms

    mock_agg_inc = MagicMock()
    mock_agg_inc.to_list = AsyncMock(return_value=incident_res)
    mock_db.whs_incidents.aggregate.return_value = mock_agg_inc

    with patch("routers.whs._get_db", return_value=mock_db), \
            patch("routers.whs._require_whs") as mock_req:
        mock_req.return_value = mock_user

        response = await get_whs_dashboard(building_id=building_id, current_user=mock_user)

        # Verification of logic parity
        assert response['inductions']['total'] == 4
        assert response['inductions']['expired'] == 1
        assert response['inductions']['current'] == 1
        # pending_renewal = total - current - expired = 4 - 1 - 1 = 2
        assert response['inductions']['pending_renewal'] == 2
        assert len(response['inductions']['expiring_in_30_days']) == 1
        assert response['inductions']['expiring_in_30_days'][0]['contractor_name'] == "C3"
        assert response['inductions']['expiring_in_30_days'][0]['days_until_expiry'] == 15

        assert response['swms']['total'] == 10
        assert response['swms']['draft'] == 3
        assert response['swms']['approved'] == 5
        assert response['swms']['high_risk_unapproved'] == 2

        assert response['incidents']['total'] == 5
        assert response['incidents']['open'] == 1
        assert response['incidents']['authority_notification_pending'] == 1

        # Verify that sequential calls were replaced by aggregate
        assert mock_db.whs_inductions.count_documents.call_count == 0
        assert mock_db.whs_inductions.find.call_count == 0
        assert mock_db.whs_swms.count_documents.call_count == 0
        assert mock_db.whs_incidents.count_documents.call_count == 0

        # Verify that parallel aggregation was used
        assert mock_db.whs_inductions.aggregate.call_count == 1
        assert mock_db.whs_swms.aggregate.call_count == 1
        assert mock_db.whs_incidents.aggregate.call_count == 1
