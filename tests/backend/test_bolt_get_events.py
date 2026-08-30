from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server import get_events


def make_cursor(data):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=data)
    return cursor


@pytest.mark.asyncio
@patch("server.db")
async def test_get_events_parallelized(mock_db):
    """Test that get_events uses parallel fetching and returns combined results."""

    mock_user = {"role": "super_admin", "id": "admin1", "full_name": "Admin"}
    now = datetime.now(timezone.utc).isoformat()

    event_data = {
        "id": "e1", "title": "Event 1", "start_date": "2026-03-01",
        "description": "desc", "event_type": "community", "end_date": None,
        "location": "loc", "is_recurring": False, "recurrence_rule": None,
        "source": "manual", "source_url": None, "is_public": True,
        "created_by": "admin1", "created_at": now
    }
    meeting_data = {"id": "m1", "title": "Meeting 1", "meeting_date": "2026-03-02", "created_at": now,
                    "created_by": "admin1"}
    agm_data = {"id": "a1", "date": "2026-03-03", "created_at": now}
    ann_data = {"id": "an1", "title": "Ann 1", "expires_at": "2026-03-04", "message": "msg", "is_public": True,
                "created_at": now, "created_by": "admin1"}

    mock_db.events.find.return_value = make_cursor([event_data])
    mock_db.meetings.find.return_value = make_cursor([meeting_data])
    mock_db.agm.find.return_value = make_cursor([agm_data])
    mock_db.announcements.find.return_value = make_cursor([ann_data])

    result = await get_events(month="2026-03", current_user=mock_user)

    assert len(result) == 4
    titles = [e.title for e in result]
    assert "Event 1" in titles
    assert "Meeting 1" in titles
    assert "Annual General Meeting" in titles
    assert "[Notice] Ann 1" in titles

    assert mock_db.events.find.called
    assert mock_db.meetings.find.called
    assert mock_db.agm.find.called
    assert mock_db.announcements.find.called


@pytest.mark.asyncio
@patch("server.db")
async def test_get_events_accepts_sparse_direct_events_and_skips_archived_meetings(mock_db):
    """Sparse direct events should not fail response validation, and archived meetings stay hidden."""

    mock_user = {
        "role": "tenant",
        "id": "tenant1",
        "full_name": "Tenant User",
        "is_approved": True,
    }
    now = datetime.now(timezone.utc).isoformat()

    sparse_event = {
        "id": "e2",
        "title": "Sparse Event",
        "start_date": "2026-04-10",
        "event_type": "community",
        "is_recurring": False,
        "source": "manual",
        "is_public": True,
        "created_by": "admin1",
        "created_at": now,
    }
    active_meeting = {
        "id": "m-active",
        "title": "Executive Committee Meeting",
        "meeting_date": "2026-04-26T15:00:00",
        "status": "scheduled",
        "created_at": now,
        "created_by": "admin1",
    }
    archived_meeting = {
        "id": "m-archived",
        "title": "Archived Meeting",
        "meeting_date": "2026-04-27T15:00:00",
        "status": "archived",
        "created_at": now,
        "created_by": "admin1",
    }

    mock_db.events.find.return_value = make_cursor([sparse_event])
    mock_db.meetings.find.return_value = make_cursor([active_meeting])
    mock_db.agm.find.return_value = make_cursor([])
    mock_db.announcements.find.return_value = make_cursor([])

    result = await get_events(month="2026-04", current_user=mock_user, building_id="13195")

    titles = [event.title for event in result]
    assert "Sparse Event" in titles
    assert "Executive Committee Meeting" in titles
    assert "Archived Meeting" not in titles

    sparse = next(event for event in result if event.title == "Sparse Event")
    assert sparse.location is None
    assert sparse.recurrence_rule is None
    assert sparse.source_url is None

    meeting_query = mock_db.meetings.find.call_args.args[0]
    assert meeting_query["status"] == {"$ne": "archived"}
