"""Tests for GAP-CMS-006: Announcement segmentation by lot/unit number.

Covers _create_announcement_notifications and _send_announcement_emails
helper functions, plus the target_lots field on AnnouncementCreate/Response
models.

All tests use mock DB — no live connection needed.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

# ── Constants ─────────────────────────────────────────────────────────────────

BUILDING_A = "16244"
BUILDING_B = "13195"

_ANN_BASE = {
    "id": "ann-001",
    "building_id": BUILDING_A,
    "title": "Parking Restriction Notice",
    "content": "<p>Please note parking changes.</p>",
    "priority": "normal",
    "is_public": False,
    "target_roles": [],
    "target_users": [],
    "created_by": "mgr-1",
    "created_by_name": "Test Manager",
    "created_at": "2026-04-23T00:00:00+00:00",
}

_USER_UNIT_LOT_1 = {"user_id": "user-lot1", "unit_number": "1", "building_id": BUILDING_A}
_USER_UNIT_LOT_2 = {"user_id": "user-lot2", "unit_number": "2", "building_id": BUILDING_A}
_USER_UNIT_LOT_5 = {"user_id": "user-lot5", "unit_number": "5", "building_id": BUILDING_A}


def _make_db(unit_assignments=None, memberships=None, users=None, email_prefs=None):
    db = MagicMock()

    ua_cursor = MagicMock()
    ua_cursor.to_list = AsyncMock(return_value=unit_assignments or [])
    db.user_units.find = MagicMock(return_value=ua_cursor)

    mb_cursor = MagicMock()
    mb_cursor.to_list = AsyncMock(return_value=memberships or [])
    db.memberships.find = MagicMock(return_value=mb_cursor)

    usr_cursor = MagicMock()
    usr_cursor.to_list = AsyncMock(return_value=users or [])
    db.users.find = MagicMock(return_value=usr_cursor)

    pref_cursor = MagicMock()
    pref_cursor.to_list = AsyncMock(return_value=email_prefs or [])
    db.email_notification_preferences.find = MagicMock(return_value=pref_cursor)

    return db


# ═══════════════════════════════════════════════════════════════════════════════
# Model validation — target_lots field
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnnouncementTargetLotsModel:

    def test_create_without_target_lots_defaults_to_none(self):
        from models.communication import AnnouncementCreate
        ann = AnnouncementCreate(
            title="All-building notice",
            content="Hello everyone",
            priority="normal",
            is_public=True,
        )
        assert ann.target_lots is None

    def test_create_with_target_lots_list(self):
        from models.communication import AnnouncementCreate
        ann = AnnouncementCreate(
            title="Lot notice",
            content="Only for lots 1 and 2",
            priority="normal",
            is_public=False,
            target_lots=["1", "2"],
        )
        assert ann.target_lots == ["1", "2"]

    def test_create_with_empty_target_lots(self):
        from models.communication import AnnouncementCreate
        ann = AnnouncementCreate(
            title="Notice",
            content="Content",
            priority="normal",
            is_public=False,
            target_lots=[],
        )
        # Empty list is falsy — treated as no filter (broadcast)
        assert ann.target_lots == []

    def test_response_includes_target_lots(self):
        from models.communication import AnnouncementResponse
        resp = AnnouncementResponse(
            id="ann-001",
            title="Notice",
            content="Content",
            priority="normal",
            is_public=False,
            building_id=BUILDING_A,
            created_by="mgr-1",
            created_by_name="Manager",
            created_at="2026-04-23T00:00:00+00:00",
            target_lots=["3", "4"],
        )
        assert resp.target_lots == ["3", "4"]

    def test_response_target_lots_defaults_none(self):
        from models.communication import AnnouncementResponse
        resp = AnnouncementResponse(
            id="ann-002",
            title="General Notice",
            content="For everyone",
            priority="normal",
            is_public=True,
            building_id=BUILDING_A,
            created_by="mgr-1",
            created_by_name="Manager",
            created_at="2026-04-23T00:00:00+00:00",
        )
        assert resp.target_lots is None


# ═══════════════════════════════════════════════════════════════════════════════
# _create_announcement_notifications — broadcast path (no target_lots)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateAnnouncementNotificationsBroadcast:

    @pytest.mark.asyncio
    async def test_no_target_lots_calls_broadcast(self):
        """When target_lots is absent, broadcast_user_notification must be called."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE}  # no target_lots key

        mock_broadcast = AsyncMock()
        mock_create = AsyncMock()
        db = _make_db()

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", mock_broadcast), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        mock_broadcast.assert_called_once()
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_target_lots_calls_broadcast(self):
        """target_lots=None is the same as no filter — should broadcast."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE, "target_lots": None}

        mock_broadcast = AsyncMock()
        mock_create = AsyncMock()
        db = _make_db()

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", mock_broadcast), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        mock_broadcast.assert_called_once()
        mock_create.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# _create_announcement_notifications — targeted lot path
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateAnnouncementNotificationsTargetLots:

    @pytest.mark.asyncio
    async def test_target_lots_calls_per_user_notification(self):
        """With target_lots set, create_user_notification called for each matched user."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE, "target_lots": ["1", "2"]}

        mock_broadcast = AsyncMock()
        mock_create = AsyncMock()
        db = _make_db(unit_assignments=[_USER_UNIT_LOT_1, _USER_UNIT_LOT_2])

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", mock_broadcast), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        assert mock_create.call_count == 2
        mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_target_lots_user_units_query_includes_building_id(self):
        """user_units query must be scoped to the building — no cross-tenant leak."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE, "target_lots": ["5"]}

        mock_broadcast = AsyncMock()
        mock_create = AsyncMock()
        db = _make_db(unit_assignments=[_USER_UNIT_LOT_5])

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", mock_broadcast), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        find_call_args = db.user_units.find.call_args[0][0]
        assert find_call_args["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_target_lots_unit_number_filter_correct(self):
        """user_units query must filter by the specified lot numbers."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE, "target_lots": ["1", "5"]}

        mock_broadcast = AsyncMock()
        mock_create = AsyncMock()
        db = _make_db(unit_assignments=[])

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", mock_broadcast), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        find_call_args = db.user_units.find.call_args[0][0]
        assert set(find_call_args["unit_number"]["$in"]) == {"1", "5"}

    @pytest.mark.asyncio
    async def test_no_matching_users_sends_no_notifications(self):
        """If no units match target_lots, no notifications are sent."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE, "target_lots": ["99"]}  # lot 99 has no users

        mock_broadcast = AsyncMock()
        mock_create = AsyncMock()
        db = _make_db(unit_assignments=[])

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", mock_broadcast), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        mock_create.assert_not_called()
        mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_notification_title_and_type_correct(self):
        """Targeted notification must include the announcement title and correct type."""
        from routers.communication import _create_announcement_notifications
        ann = {**_ANN_BASE, "target_lots": ["1"]}

        mock_create = AsyncMock()
        db = _make_db(unit_assignments=[_USER_UNIT_LOT_1])

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", AsyncMock()), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("notification_type") == "announcement"
        assert "Announcement" in call_kwargs.get("title", "")

    @pytest.mark.asyncio
    async def test_target_lots_multi_tenant_building_b(self):
        """Notifications for BUILDING_B announcements are scoped to BUILDING_B."""
        from routers.communication import _create_announcement_notifications
        ann_b = {
            **_ANN_BASE,
            "building_id": BUILDING_B,
            "target_lots": ["10"],
        }
        mock_create = AsyncMock()
        db = _make_db(unit_assignments=[{"user_id": "user-b10", "unit_number": "10", "building_id": BUILDING_B}])

        with patch("routers.communication.db", db), \
                patch("routers.communication.broadcast_user_notification", AsyncMock()), \
                patch("routers.communication.create_user_notification", mock_create):
            await _create_announcement_notifications(ann_b)

        find_call_args = db.user_units.find.call_args[0][0]
        assert find_call_args["building_id"] == BUILDING_B
        mock_create.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# _send_announcement_emails — targeted lot path
# ═══════════════════════════════════════════════════════════════════════════════

class TestSendAnnouncementEmailsTargetLots:

    @pytest.mark.asyncio
    async def test_target_lots_filters_email_recipients(self):
        """Only users assigned to target_lots should receive emails."""
        from routers.communication import _send_announcement_emails
        ann = {**_ANN_BASE, "target_lots": ["1"]}

        # building has 3 members, but only lot-1 user is in target
        memberships = [
            {"user_id": "user-lot1"},
            {"user_id": "user-lot2"},
            {"user_id": "user-lot5"},
        ]
        unit_assignments = [_USER_UNIT_LOT_1]  # only lot-1 matched
        users = [
            {"id": "user-lot1", "email": "lot1@test.com", "mail_username": None,
             "full_name": "Lot One User", "is_active": True},
        ]

        mock_send = AsyncMock()
        db = _make_db(
            unit_assignments=unit_assignments,
            memberships=memberships,
            users=users,
            email_prefs=[],
        )

        with patch("routers.communication.db", db), \
                patch("routers.communication.send_email_async", mock_send), \
                patch("routers.communication.get_email_template", return_value=("<p>html</p>", "text")):
            await _send_announcement_emails(ann)

        # Only the lot-1 user should be in the user query
        user_query_filter = db.users.find.call_args[0][0]
        assert user_query_filter["id"]["$in"] == ["user-lot1"]

    @pytest.mark.asyncio
    async def test_no_target_lots_sends_to_all_members(self):
        """Without target_lots, all building members receive emails."""
        from routers.communication import _send_announcement_emails
        ann = {**_ANN_BASE}  # no target_lots

        memberships = [
            {"user_id": "user-lot1"},
            {"user_id": "user-lot2"},
        ]
        users = [
            {"id": "user-lot1", "email": "lot1@test.com", "mail_username": None,
             "full_name": "Lot One", "is_active": True},
            {"id": "user-lot2", "email": "lot2@test.com", "mail_username": None,
             "full_name": "Lot Two", "is_active": True},
        ]

        mock_send = AsyncMock()
        db = _make_db(memberships=memberships, users=users, email_prefs=[])

        with patch("routers.communication.db", db), \
                patch("routers.communication.send_email_async", mock_send), \
                patch("routers.communication.get_email_template", return_value=("<p>html</p>", "text")):
            await _send_announcement_emails(ann)

        # Both user IDs should be in the query
        user_query_filter = db.users.find.call_args[0][0]
        assert set(user_query_filter["id"]["$in"]) == {"user-lot1", "user-lot2"}

    @pytest.mark.asyncio
    async def test_target_lots_email_query_scoped_to_building(self):
        """user_units lookup for email filtering must include building_id."""
        from routers.communication import _send_announcement_emails
        ann = {**_ANN_BASE, "target_lots": ["2"]}

        db = _make_db(
            unit_assignments=[_USER_UNIT_LOT_2],
            memberships=[{"user_id": "user-lot2"}],
            users=[],
        )

        with patch("routers.communication.db", db), \
                patch("routers.communication.send_email_async", AsyncMock()), \
                patch("routers.communication.get_email_template", return_value=("<p></p>", "")):
            await _send_announcement_emails(ann)

        find_call_args = db.user_units.find.call_args[0][0]
        assert find_call_args["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_no_users_after_lot_filter_sends_no_emails(self):
        """If target_lots filters out all members, no email is dispatched."""
        from routers.communication import _send_announcement_emails
        ann = {**_ANN_BASE, "target_lots": ["99"]}

        memberships = [{"user_id": "user-lot1"}]
        # lot 99 has no assignments so the intersection is empty
        unit_assignments = []

        mock_send = AsyncMock()
        db = _make_db(
            unit_assignments=unit_assignments,
            memberships=memberships,
            users=[],
        )

        with patch("routers.communication.db", db), \
                patch("routers.communication.send_email_async", mock_send), \
                patch("routers.communication.get_email_template", return_value=("", "")):
            await _send_announcement_emails(ann)

        mock_send.assert_not_called()
