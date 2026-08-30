"""Tests for Building Pulse endpoint (S5 - A3)."""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"
WEEK_AGO = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()


@pytest.mark.asyncio
async def test_building_pulse_returns_events_newest_first():
    """Events are sorted by timestamp descending."""
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    new_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    mock_db = MagicMock()
    mock_db.work_orders.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[{"title": "Lift repair", "updated_at": old_ts}]
    )
    mock_db.announcements.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[{"title": "New announcement", "created_at": new_ts}]
    )
    mock_db.volunteer_events.find.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.savings_events.find_one = AsyncMock(return_value=None)
    mock_db.workflow_runs.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

    with patch("routers.engagement.db", mock_db):
        from routers.engagement import get_building_pulse
        user = {"id": "u1", "role": "owner"}
        result = await get_building_pulse(current_user=user, building_id=BUILDING_ID)

    events = result["events"]
    assert len(events) >= 2
    timestamps = [e["timestamp"] for e in events if e["timestamp"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_building_pulse_empty_state():
    """Empty pulse returned when no activity this week."""
    mock_db = MagicMock()
    mock_db.work_orders.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.announcements.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.volunteer_events.find.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.savings_events.find_one = AsyncMock(return_value=None)
    mock_db.workflow_runs.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

    with patch("routers.engagement.db", mock_db):
        from routers.engagement import get_building_pulse
        user = {"id": "u1", "role": "owner"}
        result = await get_building_pulse(current_user=user, building_id=BUILDING_ID)

    assert result["events"] == []
    assert result["building_name"] == "East Gate Residences"


@pytest.mark.asyncio
async def test_automation_events_included_in_pulse():
    """Automation runs from workflow_runs appear in pulse."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    mock_db = MagicMock()
    mock_db.work_orders.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.announcements.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.volunteer_events.find.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.savings_events.find_one = AsyncMock(return_value=None)
    mock_db.workflow_runs.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[{"workflow_id": "sla_breach_check", "items_processed": 2, "started_at": ts}]
    )

    with patch("routers.engagement.db", mock_db):
        from routers.engagement import get_building_pulse
        user = {"id": "u1", "role": "owner"}
        result = await get_building_pulse(current_user=user, building_id=BUILDING_ID)

    automation_events = [e for e in result["events"] if e["type"] == "automation"]
    assert len(automation_events) == 1
    assert "SLA breach check" in automation_events[0]["text"]
