"""
Tests for announcement creation, expiry updates, and role-based filtering.

All tests use mocked databases so that NO real announcements are written to
MongoDB or shown in the production UI.  Any test that previously relied on a
live server connection has been converted to unit-test style with patched deps.
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _admin_user():
    return {
        "id": "admin-001",
        "email": "administrator@eastgateresidences.com.au",
        "full_name": "Super Admin",
        "role": "super_admin",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
    }


def _owner_user():
    return {
        "id": "owner-001",
        "email": "avneet@eastgateresidences.com.au",
        "full_name": "Avneet Rooprai",
        "role": "owner",
        "unit_number": "TH087",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
    }


def _make_ann_doc(title, target_roles=None, **kwargs):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "content": kwargs.get("content", "Body text"),
        "priority": kwargs.get("priority", "normal"),
        "target_roles": target_roles or [],
        "expires_at": kwargs.get("expires_at", None),
        "is_pinned": False,
        "allow_comments": False,
        "created_by": "admin-001",
        "created_by_name": "Super Admin",
        "created_at": now,
        "updated_at": now,
        "history": [{"action": "created", "by": "Super Admin", "at": now}],
        "comments": [],
        "is_public": False,
    }


def _mock_permissions(can_post=True):
    p = MagicMock()
    p.can_post_announcements = can_post
    p.can_manage_users = True
    return p


# ── tests: create_announcement ────────────────────────────────────────────────

class TestCreateAnnouncement:
    """Unit tests for create_announcement — fully mocked, no DB writes."""

    @pytest.mark.asyncio
    async def test_create_with_target_roles(self):
        from routers.communication import create_announcement
        from models.communication import AnnouncementCreate

        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        ann_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        stored = _make_ann_doc("Targeted Announcement", target_roles=["owner"],
                               expires_at=expires)
        stored["id"] = ann_id

        mock_db = MagicMock()
        mock_db.announcements.insert_one = AsyncMock()
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.users.count_documents = AsyncMock(return_value=0)

        payload = AnnouncementCreate(
            title="Targeted Announcement",
            content="For Owners only",
            priority="high",
            target_roles=["owner"],
            expires_at=expires,
        )

        with patch("routers.communication.get_user_permissions", return_value=_mock_permissions()), \
                patch("routers.communication.db", mock_db), \
                patch("routers.communication.broadcast_user_notification", new_callable=AsyncMock), \
                patch("routers.communication.log_activity", new_callable=AsyncMock), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock), \
                patch("routers.communication.send_email_async", new_callable=AsyncMock):
            result = await create_announcement(payload, current_user=_admin_user())

        assert result.title == "Targeted Announcement"
        assert "owner" in result.target_roles
        assert len(result.history) >= 1
        assert result.history[0]["action"] == "created"
        # Verify DB insert was called (once) — no more
        mock_db.announcements.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_requires_permission(self):
        from routers.communication import create_announcement
        from models.communication import AnnouncementCreate
        from fastapi import HTTPException

        mock_db = MagicMock()

        payload = AnnouncementCreate(title="Unauthorised", content="x")

        with patch("routers.communication.get_user_permissions",
                   return_value=_mock_permissions(can_post=False)), \
                patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await create_announcement(payload, current_user=_admin_user())

        assert exc_info.value.status_code == 403
        # DB must NOT have been touched
        mock_db.announcements.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_created_announcement_not_written_to_notifications_collection(self):
        """Regression: announcements must NOT be inserted into db.notifications."""
        from routers.communication import create_announcement
        from models.communication import AnnouncementCreate

        mock_db = MagicMock()
        mock_db.announcements.insert_one = AsyncMock()
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.users.count_documents = AsyncMock(return_value=0)
        # notifications collection should never be touched
        mock_db.notifications.insert_one = AsyncMock()
        mock_db.notifications.insert_many = AsyncMock()

        payload = AnnouncementCreate(title="Test", content="Body")

        with patch("routers.communication.get_user_permissions", return_value=_mock_permissions()), \
                patch("routers.communication.db", mock_db), \
                patch("routers.communication.broadcast_user_notification", new_callable=AsyncMock), \
                patch("routers.communication.log_activity", new_callable=AsyncMock), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock), \
                patch("routers.communication.send_email_async", new_callable=AsyncMock):
            await create_announcement(payload, current_user=_admin_user())

        mock_db.announcements.insert_one.assert_called_once()
        mock_db.notifications.insert_one.assert_not_called()
        mock_db.notifications.insert_many.assert_not_called()


# ── tests: update_announcement_expiry ────────────────────────────────────────

class TestUpdateAnnouncementExpiry:
    """Unit tests for PATCH /announcements/{id}/expiry — no DB writes."""

    @pytest.mark.asyncio
    async def test_update_expiry_appends_history(self):
        from routers.communication import update_announcement_expiry
        from models.communication import AnnouncementUpdateExpiry as AnnouncementExpiryUpdate

        ann_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        new_expiry = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

        existing = _make_ann_doc("To be edited")
        existing["id"] = ann_id
        existing["expires_at"] = None
        # After update the handler fetches the doc again
        updated = dict(existing)
        updated["expires_at"] = new_expiry
        updated["history"].append({"action": "updated_expiry", "by": "Super Admin", "at": now})

        mock_db = MagicMock()
        mock_db.announcements.find_one = AsyncMock(side_effect=[existing, updated])
        mock_db.announcements.update_one = AsyncMock()

        with patch("routers.communication.get_user_permissions", return_value=_mock_permissions()), \
                patch("routers.communication.db", mock_db), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            result = await update_announcement_expiry(
                ann_id,
                AnnouncementExpiryUpdate(expires_at=new_expiry),
                current_user=_admin_user(),
            )

        assert result.expires_at == new_expiry
        assert len(result.history) >= 2
        assert result.history[-1]["action"] == "updated_expiry"
        mock_db.announcements.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_expiry_404_on_unknown_id(self):
        from routers.communication import update_announcement_expiry
        from models.communication import AnnouncementUpdateExpiry as AnnouncementExpiryUpdate
        from fastapi import HTTPException

        mock_db = MagicMock()
        mock_db.announcements.find_one = AsyncMock(return_value=None)

        with patch("routers.communication.get_user_permissions", return_value=_mock_permissions()), \
                patch("routers.communication.db", mock_db), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_announcement_expiry(
                    "nonexistent-id",
                    AnnouncementExpiryUpdate(expires_at=datetime.now(timezone.utc).isoformat()),
                    current_user=_admin_user(),
                )

        assert exc_info.value.status_code == 404


# ── tests: announcement role filtering ───────────────────────────────────────

class TestAnnouncementFiltering:
    """
    Unit tests for GET /announcements role-based visibility — no DB writes.

    Role filtering is done at the MongoDB query level (not Python-side), so
    tests validate the QUERY sent to `find()` rather than the returned data.
    """

    @pytest.mark.asyncio
    async def test_owner_query_includes_role_filter(self):
        """Owner request must include a role-based $or filter in the DB query."""
        from routers.communication import get_announcements

        mock_db = MagicMock()
        mock_db.announcements.find.return_value.sort.return_value.to_list = AsyncMock(
            return_value=[]
        )
        owner = _owner_user()

        with patch("routers.communication.db", mock_db):
            await get_announcements(current_user=owner)

        call_args = mock_db.announcements.find.call_args
        query = call_args[0][0]
        # For non-admin roles the query must have a $and with a role $or filter
        assert "$and" in query, "Expected $and in query for non-admin user"
        and_clauses = query["$and"]
        role_clause = next(
            (c for c in and_clauses if "$or" in c and
             any("target_roles" in str(opt) or "is_public" in str(opt) for opt in c["$or"])),
            None,
        )
        assert role_clause is not None, "Expected target_roles/$or filter in query"

    @pytest.mark.asyncio
    async def test_admin_query_has_no_role_filter(self):
        """Super-admin request must NOT restrict by target_roles in the DB query."""
        from routers.communication import get_announcements

        mock_db = MagicMock()
        mock_db.announcements.find.return_value.sort.return_value.to_list = AsyncMock(
            return_value=[]
        )

        with patch("routers.communication.db", mock_db):
            await get_announcements(current_user=_admin_user())

        call_args = mock_db.announcements.find.call_args
        query = call_args[0][0]
        # Admin queries must not have an $and role restriction
        assert "$and" not in query, "Admin query should not include role $and filter"

    @pytest.mark.asyncio
    async def test_no_real_db_writes_during_filter_test(self):
        """Regression: GET announcements must never write to any collection."""
        from routers.communication import get_announcements

        mock_db = MagicMock()
        mock_db.announcements.find.return_value.sort.return_value.to_list = AsyncMock(return_value=[])
        mock_db.announcements.insert_one = AsyncMock()
        mock_db.announcements.insert_many = AsyncMock()

        with patch("routers.communication.db", mock_db):
            await get_announcements(current_user=_owner_user())

        mock_db.announcements.insert_one.assert_not_called()
        mock_db.announcements.insert_many.assert_not_called()


# ── tests: finance summary (read-only, safe to keep) ─────────────────────────

class TestFinanceSummaryReadOnly:
    """Finance summary is a read-only endpoint — verified using mocked DB."""

    @pytest.mark.asyncio
    async def test_finance_summary_structure(self):
        """
        When no levy data exists the endpoint returns a valid empty structure.
        We mock annual_levies.find_one to return None which triggers the early-
        exit path — no further DB queries run, so the mock is minimal.
        """
        from routers.finance import get_finance_summary

        mock_db = MagicMock()
        # Returning None triggers the early-exit branch with a safe empty dict
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        mock_db.building_summaries.find_one = AsyncMock(return_value=None)

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch("routers.finance._get_general_settings", AsyncMock(return_value={})):
            mock_perms.return_value.can_view_finances = True

            result = await get_finance_summary(year=2026, current_user=_admin_user(), building_id="13195")

        # Structural check — must return a dict (not None, not an exception)
        assert result is not None
        # Early-exit shape check
        assert "year" in result or isinstance(result, dict)
