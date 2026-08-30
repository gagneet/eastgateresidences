from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from backend.server import (
        MeetingCreate,
        MeetingUpdate,
        MeetingNotesUpdate,
        OutstandingIssueCreate,
        create_meeting,
        create_outstanding_issue,
        delete_meeting,
        download_meeting_minutes_document,
        get_outstanding_issues,
        update_meeting,
        update_meeting_notes,
    )

    SERVER_MODULE = "backend.server"
except ImportError:
    from server import (  # type: ignore
        MeetingCreate,
        MeetingUpdate,
        MeetingNotesUpdate,
        OutstandingIssueCreate,
        create_meeting,
        create_outstanding_issue,
        delete_meeting,
        download_meeting_minutes_document,
        get_outstanding_issues,
        update_meeting,
        update_meeting_notes,
    )

    SERVER_MODULE = "server"


def _make_sorted_cursor(items):
    cursor = MagicMock()
    cursor.sort.return_value.to_list = AsyncMock(return_value=items)
    return cursor


@pytest.fixture
def mock_db():
    with patch(f"{SERVER_MODULE}.db") as mock:
        yield mock


@pytest.fixture
def ec_user():
    return {"id": "ec-1", "role": "ec_member", "full_name": "EC Member", "is_approved": True}


@pytest.fixture
def manager_user():
    return {"id": "mgr-1", "role": "strata_manager", "full_name": "Strata Manager", "is_approved": True}


@pytest.fixture
def owner_user():
    return {"id": "owner-1", "role": "owner", "full_name": "Owner User", "is_approved": True}


@pytest.mark.asyncio
async def test_create_outstanding_issue_normalizes_status_and_sanitizes_fields(mock_db, ec_user):
    payload = OutstandingIssueCreate(
        issue=" Garage Door ",
        details="<script>alert(1)</script><b>Door sticks</b>",
        status="In progress",
        updates_notes="  Initial review complete  ",
        strata_web_meeting_tba=" Next meeting ",
        chair_notes="<i>Chair update</i>",
    )
    mock_db.outstanding_issues.insert_one = AsyncMock()

    response = await create_outstanding_issue(payload, ec_user, "building-1")

    assert response.status == "in_progress"
    insert_doc = mock_db.outstanding_issues.insert_one.call_args[0][0]
    assert insert_doc["issue"] == "Garage Door"
    assert "script" not in insert_doc["details"].lower()
    assert insert_doc["updates_notes"] == "Initial review complete"
    assert insert_doc["strata_web_meeting_tba"] == "Next meeting"
    assert insert_doc["chair_notes"] == "<i>Chair update</i>"


@pytest.mark.asyncio
async def test_get_outstanding_issues_returns_building_entries_for_viewer(mock_db, owner_user):
    mock_db.outstanding_issues.find.return_value = _make_sorted_cursor([
        {
            "id": "issue-1",
            "issue": "Lift service",
            "details": "Awaiting parts",
            "status": "in_progress",
            "updates_notes": None,
            "strata_web_meeting_tba": None,
            "chair_notes": None,
            "created_by": "ec-1",
            "created_by_name": "EC Member",
            "updated_by": "ec-1",
            "updated_by_name": "EC Member",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
        }
    ])

    response = await get_outstanding_issues(owner_user, "building-1")

    assert len(response) == 1
    assert response[0].issue == "Lift service"
    mock_db.outstanding_issues.find.assert_called_once_with({"building_id": "building-1"}, {"_id": 0})


@pytest.mark.asyncio
async def test_create_meeting_sends_committee_notifications(mock_db, manager_user):
    meeting_doc = {
        "id": "meeting-1",
        "title": "EC Meeting",
        "description": "Budget review",
        "meeting_date": "2026-03-01T18:00:00+00:00",
        "location": "Clubhouse",
        "agenda": ["Budget review"],
        "attendees": ["Chair"],
        "minutes": None,
        "status": "scheduled",
        "created_by": manager_user["id"],
        "created_by_name": manager_user["full_name"],
        "created_at": "2026-03-01T17:00:00+00:00",
        "updated_at": "2026-03-01T17:00:00+00:00",
    }
    mock_db.meetings.insert_one = AsyncMock()

    with patch(f"{SERVER_MODULE}._notify_committee_meeting_recipients", new=AsyncMock()) as notify_mock:
        response = await create_meeting(
            MeetingCreate(
                title="EC Meeting",
                description="Budget review",
                meeting_date="2026-03-01T18:00:00+00:00",
                location="Clubhouse",
                agenda=["Budget review"],
                attendees=["Chair"],
            ),
            manager_user,
            "building-1",
        )

    assert response.title == meeting_doc["title"]
    notify_mock.assert_awaited_once()
    insert_doc = mock_db.meetings.insert_one.call_args[0][0]
    assert insert_doc["title"] == "EC Meeting"
    assert insert_doc["status"] == "scheduled"


@pytest.mark.asyncio
async def test_update_meeting_notes_saves_attendees_and_minutes(mock_db, manager_user):
    existing_meeting = {
        "id": "meeting-1",
        "title": "EC Meeting",
        "description": None,
        "meeting_date": "2026-03-01T18:00:00+00:00",
        "location": "Clubhouse",
        "agenda": ["Defects"],
        "attendees": [],
        "minutes": None,
        "status": "scheduled",
        "created_by": "ec-1",
        "created_at": "2026-03-01T17:00:00+00:00",
        "updated_at": "2026-03-01T17:00:00+00:00",
    }
    updated_meeting = {
        **existing_meeting,
        "agenda": ["Finance update", "Contractor review"],
        "attendees": ["Alice", "Bob"],
        "minutes": "<b>Decision recorded</b>",
        "updated_by": manager_user["id"],
        "updated_by_name": manager_user["full_name"],
    }
    mock_db.meetings.find_one = AsyncMock(side_effect=[existing_meeting, updated_meeting])
    mock_db.meetings.update_one = AsyncMock()

    response = await update_meeting_notes(
        "meeting-1",
        MeetingNotesUpdate(
            agenda=[" Finance update ", "", "Contractor review "],
            attendees=[" Alice ", "", "Bob "],
            minutes="<b>Decision recorded</b>",
        ),
        manager_user,
        "building-1",
    )

    update_doc = mock_db.meetings.update_one.call_args[0][1]["$set"]
    assert update_doc["agenda"] == ["Finance update", "Contractor review"]
    assert update_doc["attendees"] == ["Alice", "Bob"]
    assert response.agenda == ["Finance update", "Contractor review"]
    assert response.attendees == ["Alice", "Bob"]
    assert response.minutes == "<b>Decision recorded</b>"


@pytest.mark.asyncio
async def test_update_meeting_allows_chairman_ec_and_manager_fields(mock_db, manager_user):
    existing_meeting = {
        "id": "meeting-1",
        "title": "EC Meeting",
        "description": "Initial description",
        "meeting_date": "2026-03-01T18:00:00+00:00",
        "location": "Clubhouse",
        "agenda": ["Defects"],
        "attendees": ["Alice"],
        "minutes": None,
        "status": "scheduled",
        "created_by": "ec-1",
        "created_at": "2026-03-01T17:00:00+00:00",
        "updated_at": "2026-03-01T17:00:00+00:00",
    }
    updated_meeting = {
        **existing_meeting,
        "title": "EC Meeting - Revised",
        "description": "<b>Updated</b>",
        "meeting_date": "2026-03-02T18:30:00+00:00",
        "location": "Zoom",
        "agenda": ["Finance", "Defects"],
        "attendees": ["Alice", "Bob"],
        "updated_by": manager_user["id"],
        "updated_by_name": manager_user["full_name"],
    }
    mock_db.meetings.find_one = AsyncMock(side_effect=[existing_meeting, updated_meeting])
    mock_db.meetings.update_one = AsyncMock()

    with patch(f"{SERVER_MODULE}._notify_committee_meeting_recipients", new=AsyncMock()) as notify_mock:
        response = await update_meeting(
            "meeting-1",
            MeetingUpdate(
                title=" EC Meeting - Revised ",
                description="<b>Updated</b>",
                meeting_date="2026-03-02T18:30:00+00:00",
                location=" Zoom ",
                agenda=[" Finance ", "Defects"],
                attendees=["Alice ", " Bob "],
            ),
            None,
            None,
            manager_user,
            "building-1",
        )

    update_doc = mock_db.meetings.update_one.call_args[0][1]["$set"]
    assert update_doc["title"] == "EC Meeting - Revised"
    assert update_doc["location"] == "Zoom"
    assert update_doc["agenda"] == ["Finance", "Defects"]
    assert update_doc["attendees"] == ["Alice", "Bob"]
    assert response.title == "EC Meeting - Revised"
    notify_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_meeting_minutes_document_returns_word_payload(mock_db, manager_user):
    meeting = {
        "id": "meeting-1",
        "title": "Executive Committee Meeting",
        "description": None,
        "meeting_date": "2026-03-01T18:00:00+00:00",
        "location": "Clubhouse",
        "agenda": ["Finance update"],
        "attendees": ["Alice Example"],
        "minutes": "Resolved the pending contractor items.",
        "status": "completed",
        "created_by": "ec-1",
        "created_at": "2026-03-01T17:00:00+00:00",
        "updated_at": "2026-03-01T19:30:00+00:00",
    }
    mock_db.meetings.find_one = AsyncMock(return_value=meeting)

    with patch(f"{SERVER_MODULE}._get_general_settings_or_default", new=AsyncMock(return_value={
        "building_name": "Test Building",
        "building_address": "1 Example Street",
        "logo_url": "https://example.com/logo.png",
    })):
        response = await download_meeting_minutes_document("meeting-1", manager_user, "building-1")

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert response.media_type == "application/msword"
    assert 'attachment; filename="Executive_Committee_Meeting.doc"' in response.headers["Content-Disposition"]
    assert b"Minutes of the Meeting" in body
    assert b"Alice Example" in body
    assert b"Resolved the pending contractor items." in body


@pytest.mark.asyncio
async def test_delete_meeting_removes_matching_meeting(mock_db, manager_user):
    mock_db.meetings.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

    response = await delete_meeting("meeting-1", manager_user, "building-1")

    mock_db.meetings.delete_one.assert_called_once_with({"id": "meeting-1", "building_id": "building-1"})
    assert response["message"] == "Meeting deleted"
