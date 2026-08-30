"""Tests for nav badges endpoint (S5 - A7)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

BUILDING_ID = "13195"
USER_ID = "user-test-badges-001"


@pytest.mark.asyncio
async def test_nav_badges_parcel_count():
    """Badge count for parcels reflects uncollected parcels for user's unit."""
    mock_db = MagicMock()
    mock_db.parcels.count_documents = AsyncMock(return_value=2)
    mock_db.proposals.find.return_value.to_list = AsyncMock(return_value=[])
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.user_notifications.count_documents = AsyncMock(return_value=0)

    with patch("routers.nav_badges.db", mock_db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        from routers.nav_badges import get_nav_badges
        user = {"id": USER_ID, "role": "owner", "unit_number": "TH087"}
        result = await get_nav_badges(current_user=user, building_id=BUILDING_ID)

    assert result["badges"].get("parcels") == 2


@pytest.mark.asyncio
async def test_nav_badges_sla_count_manager_only():
    """SLA breach badge only shown to managers, not owners."""
    mock_db = MagicMock()
    mock_db.parcels.count_documents = AsyncMock(return_value=0)
    mock_db.proposals.find.return_value.to_list = AsyncMock(return_value=[])
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=5)
    mock_db.user_notifications.count_documents = AsyncMock(return_value=0)

    with patch("routers.nav_badges.db", mock_db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        from routers.nav_badges import get_nav_badges
        # Manager should see SLA badge
        manager = {"id": USER_ID, "role": "strata_manager"}
        result_mgr = await get_nav_badges(current_user=manager, building_id=BUILDING_ID)
        # Owner should NOT see SLA badge
        owner = {"id": USER_ID, "role": "owner", "unit_number": "TH087"}
        result_owner = await get_nav_badges(current_user=owner, building_id=BUILDING_ID)

    assert result_mgr["badges"].get("requests") == 5
    assert "requests" not in result_owner["badges"]


@pytest.mark.asyncio
async def test_nav_badges_empty_when_nothing_pending():
    """Empty badges dict when no counts."""
    mock_db = MagicMock()
    mock_db.parcels.count_documents = AsyncMock(return_value=0)
    mock_db.proposals.find.return_value.to_list = AsyncMock(return_value=[])
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.user_notifications.count_documents = AsyncMock(return_value=0)

    with patch("routers.nav_badges.db", mock_db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        from routers.nav_badges import get_nav_badges
        user = {"id": USER_ID, "role": "owner", "unit_number": "TH087"}
        result = await get_nav_badges(current_user=user, building_id=BUILDING_ID)

    assert result["badges"] == {}
