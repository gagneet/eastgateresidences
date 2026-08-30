"""
Regression tests for GET /meetings — limit param and scheduled-meeting sort order.

Dashboard "next meeting" widgets (OwnerDashboard v2, both /dashboard and
/owner-view entry points) call GET /meetings?status=scheduled&limit=1 and
take the first result as "the next meeting". Previously:
  - `limit` was accepted by FastAPI but silently dropped (not a handler parameter),
    so up to 100 rows were always returned regardless of the caller's limit.
  - Results were always sorted meeting_date descending, so `[0]` of a scheduled-only
    query was the FURTHEST-future scheduled meeting, not the nearest upcoming one.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.permissions import Permission


def _cursor(items):
    cur = MagicMock()
    cur.sort = MagicMock(return_value=cur)
    cur.to_list = AsyncMock(return_value=items)
    return cur


def _meeting(id_, meeting_date, status="scheduled"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": id_, "title": f"Meeting {id_}", "description": None,
        "meeting_date": meeting_date, "location": "Boardroom", "agenda": [],
        "attendees": [], "minutes": None, "status": status,
        "created_by": "u1", "created_at": now, "updated_at": now,
    }


@pytest.mark.asyncio
async def test_limit_param_is_respected():
    from routers.meetings import get_meetings

    mock_db = MagicMock()
    find_cursor = _cursor([_meeting("m1", "2027-01-01")])
    mock_db.meetings.find = MagicMock(return_value=find_cursor)

    with patch("routers.meetings.db", mock_db), \
         patch("routers.meetings.get_user_permissions",
               return_value=Permission(can_view_meetings=True)):
        result = await get_meetings(
            status="scheduled", limit=1,
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    assert len(result) == 1
    # to_list must be called with the caller's limit, not the old hardcoded 100.
    find_cursor.to_list.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_no_limit_defaults_to_100():
    from routers.meetings import get_meetings

    mock_db = MagicMock()
    find_cursor = _cursor([])
    mock_db.meetings.find = MagicMock(return_value=find_cursor)

    with patch("routers.meetings.db", mock_db), \
         patch("routers.meetings.get_user_permissions",
               return_value=Permission(can_view_meetings=True)):
        await get_meetings(
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    find_cursor.to_list.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_limit_over_100_is_clamped_to_100():
    """A caller passing limit > 100 must be clamped to 100 to protect against
    memory pressure from unbounded Mongo result sets."""
    from routers.meetings import get_meetings

    mock_db = MagicMock()
    find_cursor = _cursor([])
    mock_db.meetings.find = MagicMock(return_value=find_cursor)

    with patch("routers.meetings.db", mock_db), \
         patch("routers.meetings.get_user_permissions",
               return_value=Permission(can_view_meetings=True)):
        await get_meetings(
            status=None, limit=500,
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    find_cursor.to_list.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_scheduled_status_sorts_ascending_nearest_first():
    """status=scheduled must sort ascending so the nearest upcoming meeting is
    first — a "next meeting" query sorted descending would return the meeting
    furthest in the future instead."""
    from routers.meetings import get_meetings

    mock_db = MagicMock()
    find_cursor = _cursor([_meeting("m-near", "2026-08-01")])
    mock_db.meetings.find = MagicMock(return_value=find_cursor)

    with patch("routers.meetings.db", mock_db), \
         patch("routers.meetings.get_user_permissions",
               return_value=Permission(can_view_meetings=True)):
        await get_meetings(
            status="scheduled", limit=1,
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    find_cursor.sort.assert_called_once_with("meeting_date", 1)


@pytest.mark.asyncio
async def test_no_status_filter_keeps_descending_sort():
    """Unfiltered/history views (MeetingsPage etc.) keep the pre-existing
    most-recent-first order — only the scheduled "next meeting" case changes."""
    from routers.meetings import get_meetings

    mock_db = MagicMock()
    find_cursor = _cursor([])
    mock_db.meetings.find = MagicMock(return_value=find_cursor)

    with patch("routers.meetings.db", mock_db), \
         patch("routers.meetings.get_user_permissions",
               return_value=Permission(can_view_meetings=True)):
        await get_meetings(
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    find_cursor.sort.assert_called_once_with("meeting_date", -1)
