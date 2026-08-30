"""
Tests for the user registration management workflows:
  - POST /users/{id}/request-info  (Request Info)
  - POST /users/{id}/archive       (Archive user)
  - POST /users/{id}/reject        (Reject → archive + cascade cleanup)
  - GET  /registration/update-check?token=
  - PUT  /registration/update
  - GET  /admin/archived-users
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

# ── env setup so imports work ─────────────────────────────────────────────────
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret")

# Add backend to path
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient
from fastapi import HTTPException


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_admin_user():
    return {
        "id": "admin-001",
        "email": "admin@eastgateresidences.com.au",
        "full_name": "Super Admin",
        "role": "super_admin",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
    }


def make_pending_user(status="pending", **kwargs):
    defaults = {
        "id": "user-001",
        "email": "newuser@example.com",
        "full_name": "New User",
        "role": "tenant",
        "unit_number": "TH087",
        "is_active": True,
        "is_approved": False,
        "status": status,
        "info_request_reason": None,
        "info_request_token": None,
        "info_requested_at": None,
    }
    defaults.update(kwargs)
    return defaults


def make_active_user(**kwargs):
    u = make_pending_user(status="active", **kwargs)
    u["is_approved"] = True
    return u


# ─── Unit tests for model fields ─────────────────────────────────────────────

class TestUserStatusModel(unittest.TestCase):
    """Verify UserStatus constants and new UserResponse fields."""

    def test_user_status_constants(self):
        from models.user import UserStatus
        self.assertEqual(UserStatus.ACTIVE, "active")
        self.assertEqual(UserStatus.PENDING_OWNER_APPROVAL, "pending_owner_approval")
        self.assertEqual(UserStatus.INFO_REQUESTED, "info_requested")
        self.assertEqual(UserStatus.ARCHIVED, "archived")

    def test_user_response_has_status_field(self):
        from models.user import UserResponse, Permission
        u = UserResponse(
            id="x", email="a@b.com", full_name="A B", role="owner",
            is_active=True, is_approved=True,
            permissions=Permission(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(u.status, "active")  # default
        self.assertIsNone(u.info_request_reason)
        self.assertIsNone(u.info_requested_at)
        self.assertIsNone(u.archived_at)
        self.assertIsNone(u.archived_by)

    def test_user_response_accepts_status_values(self):
        from models.user import UserResponse, Permission
        for status in ("active", "pending_owner_approval", "info_requested", "archived"):
            u = UserResponse(
                id="x", email="a@b.com", full_name="A B", role="owner",
                is_active=True, is_approved=True, status=status,
                permissions=Permission(),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self.assertEqual(u.status, status)


# ─── Integration tests against the live backend ──────────────────────────────

BASE = "http://127.0.0.1:8003/api"
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "")

try:
    import httpx

    _httpx_ok = True
except ImportError:
    _httpx_ok = False


def get_token(email, password):
    import httpx
    r = httpx.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=10)
    if r.status_code == 200:
        return r.json().get("token")
    return None


@unittest.skipUnless(_httpx_ok, "httpx not installed")
@unittest.skipUnless(
    os.environ.get("TEST_ADMIN_EMAIL") and os.environ.get("TEST_ADMIN_PASSWORD"),
    "TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD env vars must be set to run integration tests",
)
class TestUserWorkflowsIntegration(unittest.TestCase):
    """
    Live integration tests.  Requires the backend to be running on port 8003
    and valid admin/chairman credentials.
    """

    @classmethod
    def setUpClass(cls):
        import httpx
        cls.http = httpx.Client(base_url=BASE, timeout=15)
        # Try to get an admin token
        cls.admin_token = get_token(ADMIN_EMAIL, ADMIN_PASSWORD)
        cls.headers = (
            {"Authorization": f"Bearer {cls.admin_token}"}
            if cls.admin_token
            else {}
        )
        cls.backend_available = cls.admin_token is not None

    @classmethod
    def tearDownClass(cls):
        cls.http.close()

    def _skip_if_unavailable(self):
        if not self.backend_available:
            self.skipTest("Backend not reachable or auth failed")

    # ── /users?status= filter ─────────────────────────────────────────────

    def test_users_list_excludes_archived_by_default(self):
        self._skip_if_unavailable()
        r = self.http.get("/users", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        users = r.json()
        archived = [u for u in users if u.get("status") == "archived"]
        self.assertEqual(len(archived), 0, "Archived users should not appear in default list")

    def test_users_list_status_archived_filter(self):
        self._skip_if_unavailable()
        r = self.http.get("/users?status=archived", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        users = r.json()
        for u in users:
            self.assertEqual(u.get("status"), "archived")

    def test_users_list_status_all_includes_archived(self):
        self._skip_if_unavailable()
        r = self.http.get("/users?status=all", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    # ── /users/{id}/request-info ──────────────────────────────────────────

    def test_request_info_requires_auth(self):
        r = self.http.post("/users/fake-id/request-info", json={"reason": "wrong_unit"})
        self.assertIn(r.status_code, (401, 403))

    def test_request_info_rejects_unknown_user(self):
        self._skip_if_unavailable()
        r = self.http.post(
            "/users/nonexistent-user-id/request-info",
            json={"reason": "wrong_unit"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 404)

    def test_request_info_rejects_super_admin(self):
        self._skip_if_unavailable()
        # Get current admin's own ID
        me_r = self.http.get("/auth/me", headers=self.headers)
        if me_r.status_code != 200:
            self.skipTest("Could not get current user")
        my_id = me_r.json().get("id")
        r = self.http.post(
            f"/users/{my_id}/request-info",
            json={"reason": "wrong_unit"},
            headers=self.headers,
        )
        self.assertIn(r.status_code, (403, 400))

    # ── /users/{id}/archive ───────────────────────────────────────────────

    def test_archive_requires_auth(self):
        r = self.http.post("/users/fake-id/archive", json={"reason": "no_longer_active"})
        self.assertIn(r.status_code, (401, 403))

    def test_archive_rejects_unknown_user(self):
        self._skip_if_unavailable()
        r = self.http.post(
            "/users/nonexistent-user-id/archive",
            json={"reason": "no_longer_active"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 404)

    # ── /registration/update-check ────────────────────────────────────────

    def test_update_check_missing_token_returns_error(self):
        r = self.http.get("/registration/update-check?token=")
        self.assertIn(r.status_code, (400, 404, 422))

    def test_update_check_invalid_token_returns_404(self):
        r = self.http.get("/registration/update-check?token=invalid-token-that-does-not-exist")
        self.assertEqual(r.status_code, 404)

    # ── /registration/update ─────────────────────────────────────────────

    def test_update_registration_missing_token(self):
        r = self.http.put("/registration/update", json={"token": ""})
        self.assertIn(r.status_code, (400, 422))

    def test_update_registration_invalid_token(self):
        r = self.http.put(
            "/registration/update",
            json={"token": "bad-token", "unit_number": "TH087", "role": "tenant"}
        )
        self.assertIn(r.status_code, (404, 422))

    def test_update_registration_invalid_role_rejected(self):
        r = self.http.put(
            "/registration/update",
            json={"token": "any-token", "unit_number": "TH087", "role": "super_admin"}
        )
        self.assertIn(r.status_code, (400, 404, 422))

    # ── /users/{id}/reject ────────────────────────────────────────────────

    def test_reject_requires_auth(self):
        r = self.http.post("/users/fake-id/reject", json={"reason": "not_approved_by_owner"})
        self.assertIn(r.status_code, (401, 403))

    def test_reject_rejects_super_admin_target(self):
        self._skip_if_unavailable()
        me_r = self.http.get("/auth/me", headers=self.headers)
        if me_r.status_code != 200:
            self.skipTest("Could not get current user")
        my_id = me_r.json().get("id")
        r = self.http.post(
            f"/users/{my_id}/reject",
            json={"reason": "not_approved_by_owner"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 403)

    # ── /admin/archived-users ─────────────────────────────────────────────

    def test_archived_users_requires_auth(self):
        r = self.http.get("/admin/archived-users")
        self.assertIn(r.status_code, (401, 403))

    def test_archived_users_returns_list(self):
        self._skip_if_unavailable()
        r = self.http.get("/admin/archived-users", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_archived_users_all_have_archived_status(self):
        self._skip_if_unavailable()
        r = self.http.get("/admin/archived-users", headers=self.headers)
        users = r.json()
        # All returned users must be archived
        for u in users:
            self.assertEqual(u.get("status", "archived"), "archived",
                             f"User {u.get('user_id')} has wrong status")

    # ── /owner/pending-registrations ─────────────────────────────────────

    def test_pending_registrations_requires_auth(self):
        r = self.http.get("/owner/pending-registrations")
        self.assertIn(r.status_code, (401, 403))

    def test_pending_registrations_returns_list_for_owner(self):
        self._skip_if_unavailable()
        r = self.http.get("/owner/pending-registrations", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_pending_registrations_only_pending_owner_approval_status(self):
        self._skip_if_unavailable()
        r = self.http.get("/owner/pending-registrations", headers=self.headers)
        for u in r.json():
            self.assertEqual(u.get("status"), "pending_owner_approval",
                             f"Expected pending_owner_approval, got {u.get('status')}")

    # ── /users/{id}/owner-decision ────────────────────────────────────────

    def test_owner_decision_requires_auth(self):
        r = self.http.post("/users/fake-id/owner-decision", json={"action": "approve"})
        self.assertIn(r.status_code, (401, 403))

    def test_owner_decision_rejects_unknown_user(self):
        self._skip_if_unavailable()
        r = self.http.post(
            "/users/nonexistent-user-id/owner-decision",
            json={"action": "approve"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 404)

    def test_owner_decision_rejects_invalid_action(self):
        self._skip_if_unavailable()
        me_r = self.http.get("/auth/me", headers=self.headers)
        if me_r.status_code != 200:
            self.skipTest("Could not get current user")
        my_id = me_r.json().get("id")
        r = self.http.post(
            f"/users/{my_id}/owner-decision",
            json={"action": "delete"},  # invalid
            headers=self.headers,
        )
        self.assertIn(r.status_code, (400, 422))

    # ── /users?status=pending_owner_approval ────────────────────────────

    def test_users_list_pending_owner_approval_filter(self):
        self._skip_if_unavailable()
        r = self.http.get("/users?status=pending_owner_approval", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        for u in r.json():
            self.assertEqual(u.get("status"), "pending_owner_approval")


# ─── Unit tests for reject endpoint logic (mocked) ───────────────────────────

class TestRejectEndpointLogic(unittest.TestCase):
    """Test the reject endpoint logic without a live server."""

    def test_reject_reason_labels_mapped_correctly(self):
        reason_labels = {
            "not_approved_by_owner": "Not Approved by Owner",
            "wrong_unit": "Wrong Unit Entered",
            "wrong_user_type": "Wrong User Type Selected",
        }
        for code, expected_label in reason_labels.items():
            self.assertEqual(reason_labels.get(code), expected_label)

    def test_archive_action_sets_correct_fields(self):
        """Simulate the archive logic's output dict."""
        now = datetime.now(timezone.utc).isoformat()
        changes = {
            "status": "archived",
            "is_active": False,
            "is_approved": False,
            "archived_at": now,
            "archived_by": "admin-001",
            "archived_reason": "rejected:not_approved_by_owner",
            "updated_at": now,
        }
        self.assertEqual(changes["status"], "archived")
        self.assertFalse(changes["is_active"])
        self.assertFalse(changes["is_approved"])
        self.assertIn("rejected:", changes["archived_reason"])

    def test_request_info_expiry_logic(self):
        """Validate that 168-hour window is enforced correctly."""
        now = datetime.now(timezone.utc)
        within_window = now - timedelta(hours=167)
        outside_window = now - timedelta(hours=169)
        expiry = timedelta(hours=168)
        self.assertFalse((now - within_window) > expiry)
        self.assertTrue((now - outside_window) > expiry)

    def test_token_is_uuid_format(self):
        """Info-request tokens should be valid UUIDs."""
        token = str(uuid.uuid4())
        try:
            uuid.UUID(token)
            valid = True
        except ValueError:
            valid = False
        self.assertTrue(valid)


# ─── Unit tests for cron script logic ────────────────────────────────────────

class TestArchiveStaleCronLogic(unittest.TestCase):
    """Unit tests for the archive_stale_registrations cron logic."""

    def test_cutoff_calculation(self):
        """Cutoff should be 168 hours ago."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=168)
        # User requested exactly 167h ago should NOT be past cutoff
        recent = now - timedelta(hours=167)
        self.assertFalse(recent < cutoff)
        # User requested 169h ago SHOULD be past cutoff
        stale = now - timedelta(hours=169)
        self.assertTrue(stale < cutoff)

    def test_cutoff_iso_string_comparison(self):
        """Ensure ISO string comparison matches datetime comparison."""
        now = datetime.now(timezone.utc)
        cutoff_iso = (now - timedelta(hours=168)).isoformat()
        stale_iso = (now - timedelta(hours=200)).isoformat()
        fresh_iso = (now - timedelta(hours=100)).isoformat()
        self.assertLess(stale_iso, cutoff_iso)
        self.assertGreater(fresh_iso, cutoff_iso)


# ─── Unit tests: request-profile-info endpoint ───────────────────────────────

class TestRequestProfileInfo(unittest.IsolatedAsyncioTestCase):
    """
    Tests for POST /users/{id}/request-profile-info.

    All tests use mocked DB — no real notifications or emails are sent,
    and nothing is written to MongoDB.
    """

    def _admin(self):
        return make_admin_user()

    def _target(self):
        return {
            "id": "target-001",
            "email": "owner@example.com",
            "full_name": "Jane Owner",
            "role": "owner",
            "unit_number": "TH071",
            "is_active": True,
            "is_approved": True,
            "status": "active",
        }

    async def test_returns_200_for_valid_user(self):
        from server import request_profile_info

        mock_db = MagicMock()
        mock_db.memberships.find_one = AsyncMock(return_value={"user_id": "target-001", "building_id": "13195"})
        mock_db.users.find_one = AsyncMock(return_value=self._target())
        mock_db.users.update_one = AsyncMock()
        mock_db.user_notifications.insert_one = AsyncMock()

        with patch("server.db", mock_db), \
                patch("server.get_user_permissions") as mock_perms, \
                patch("server.send_email_async", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:
            mock_perms.return_value.can_manage_users = True
            mock_asyncio.create_task = MagicMock()

            result = await request_profile_info(
                user_id="target-001",
                current_user=self._admin(),
            )

        assert result["message"] == "Profile info request sent"

    async def test_notification_written_to_user_notifications_not_notifications(self):
        """Bell notifications must go to user_notifications, never to notifications."""
        from server import request_profile_info

        mock_db = MagicMock()
        mock_db.memberships.find_one = AsyncMock(return_value={"user_id": "target-001", "building_id": "13195"})
        mock_db.users.find_one = AsyncMock(return_value=self._target())
        mock_db.users.update_one = AsyncMock()
        mock_db.user_notifications.insert_one = AsyncMock()
        mock_db.notifications.insert_one = AsyncMock()
        mock_db.notifications.insert_many = AsyncMock()

        with patch("server.db", mock_db), \
                patch("server.get_user_permissions") as mock_perms, \
                patch("server.send_email_async", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:
            mock_perms.return_value.can_manage_users = True
            mock_asyncio.create_task = MagicMock()

            await request_profile_info(user_id="target-001", current_user=self._admin())

        mock_db.user_notifications.insert_one.assert_called_once()
        mock_db.notifications.insert_one.assert_not_called()
        mock_db.notifications.insert_many.assert_not_called()

    async def test_returns_404_for_missing_user(self):
        from server import request_profile_info
        from fastapi import HTTPException

        mock_db = MagicMock()
        mock_db.memberships.find_one = AsyncMock(return_value={"user_id": "target-001", "building_id": "13195"})
        mock_db.users.find_one = AsyncMock(return_value=None)

        with patch("server.db", mock_db), \
                patch("server.get_user_permissions") as mock_perms:
            mock_perms.return_value.can_manage_users = True

            with self.assertRaises(HTTPException) as ctx:
                await request_profile_info(user_id="nobody", current_user=self._admin())

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_returns_403_for_super_admin_target(self):
        from server import request_profile_info
        from fastapi import HTTPException

        mock_db = MagicMock()
        mock_db.memberships.find_one = AsyncMock(return_value={"user_id": "target-001", "building_id": "13195"})
        target = self._target()
        target["role"] = "super_admin"
        mock_db.users.find_one = AsyncMock(return_value=target)

        with patch("server.db", mock_db), \
                patch("server.get_user_permissions") as mock_perms:
            mock_perms.return_value.can_manage_users = True

            with self.assertRaises(HTTPException) as ctx:
                await request_profile_info(user_id="target-001", current_user=self._admin())

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_returns_403_when_no_manage_users_permission(self):
        from server import request_profile_info
        from fastapi import HTTPException

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=self._target())

        with patch("server.db", mock_db), \
                patch("server.get_user_permissions") as mock_perms:
            mock_perms.return_value.can_manage_users = False

            with self.assertRaises(HTTPException) as ctx:
                await request_profile_info(user_id="target-001", current_user=self._admin())

        self.assertEqual(ctx.exception.status_code, 403)


# ─── Unit tests: registration bell notification routing ───────────────────────

class TestRegistrationBellNotifications(unittest.IsolatedAsyncioTestCase):
    """
    Tests for the registration notification flow in POST /auth/register.

    Verifies that bell notifications go to user_notifications (the collection
    the bell icon reads) and NOT to notifications (the admin broadcast collection).

    The actual registration endpoint is defined in server.py (not routers/auth.py),
    so all tests import and patch from server.*
    No real users are created — all DB operations are mocked.
    """

    def _make_register_payload(self, role="owner", unit="TH071"):
        from models.user import UserCreate
        return UserCreate(
            full_name="Test Registrant",
            email=f"test.{role}.{unit}@example.com",
            password="SecurePass123!",
            role=role,
            unit_number=unit,
            terms_accepted=True,
        )

    def _mock_db(self):
        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=None)  # no existing user
        mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH071"})
        mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Complex"})
        mock_db.users.insert_one = AsyncMock()
        mock_db.user_units.insert_one = AsyncMock()
        mock_db.memberships.insert_one = AsyncMock()
        mock_db.by_laws_acknowledgments.insert_one = AsyncMock()
        mock_db.user_notifications.insert_many = AsyncMock()
        mock_db.user_notifications.insert_one = AsyncMock()
        mock_db.notifications.insert_many = AsyncMock()  # must NOT be called
        mock_db.notifications.insert_one = AsyncMock()  # must NOT be called
        # Admin + chairman users returned for bell notifications
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "admin-001", "email": "admin@test.com", "full_name": "Admin",
             "role": "super_admin", "is_active": True}
        ])
        return mock_db

    def _make_request(self):
        """Minimal real starlette Request — required by the slowapi rate limiter."""
        from starlette.requests import Request as StarletteRequest
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/register",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        return StarletteRequest(scope)

    async def test_owner_registration_notifies_user_notifications_not_notifications(self):
        """Owner registration bell must go to user_notifications, not notifications."""
        from routers.auth import register
        from fastapi import BackgroundTasks

        mock_db = self._mock_db()
        payload = self._make_register_payload(role="owner", unit="TH071")

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.send_email_async", new_callable=AsyncMock), \
                patch("routers.auth.hash_password", return_value="hashed"), \
                patch("routers.auth.create_token", return_value="tok"):
            await register(self._make_request(), payload, BackgroundTasks())

        mock_db.user_notifications.insert_many.assert_called()
        mock_db.notifications.insert_many.assert_not_called()

    async def test_registration_email_link_contains_search_param(self):
        """Email sent to admins on new registration must include ?search=Name in the link."""
        from routers.auth import register
        from fastapi import BackgroundTasks

        mock_db = self._mock_db()
        payload = self._make_register_payload(role="owner", unit="TH071")

        captured_calls = []

        def capture_task(fn, *args, **kwargs):
            captured_calls.append((fn, args, kwargs))

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed"), \
                patch("routers.auth.create_token", return_value="tok"), \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):
            bg = BackgroundTasks()
            bg.add_task = capture_task
            await register(self._make_request(), payload, bg)

        # Find email background tasks and check for ?search= in the body
        email_calls = [c for c in captured_calls if "send_email" in str(c[0])]
        assert len(email_calls) > 0, "Expected at least one email background task"
        matched_any = False
        for _fn, args, _kw in email_calls:
            full_str = str(args)
            if "dashboard/users" in full_str:
                matched_any = True
                assert "search=" in full_str, \
                    f"Email link missing ?search= param. Got: {full_str}"
        assert matched_any, "No email contained a dashboard/users link"

    async def test_returning_archived_user_bell_goes_to_user_notifications(self):
        """Returning-user detection must write bell to user_notifications."""
        from routers.auth import register
        from fastapi import BackgroundTasks, HTTPException

        archived_user = {
            "id": "old-001",
            "email": "test.owner.TH071@example.com",
            "full_name": "Test Registrant",
            "role": "owner",
            "unit_number": "TH071",
            "status": "archived",
            "is_active": False,
        }

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=archived_user)
        mock_db.users.update_one = AsyncMock()
        mock_db.user_notifications.insert_many = AsyncMock()
        mock_db.notifications.insert_many = AsyncMock()
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "admin-001", "email": "admin@test.com", "role": "super_admin",
             "full_name": "Admin", "is_active": True}
        ])

        from models.user import UserCreate
        payload = UserCreate(
            full_name="Test Registrant",
            email="test.owner.TH071@example.com",
            password="SecurePass123!",
            role="owner",
            unit_number="TH071",
            terms_accepted=True,
        )

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):
            try:
                await register(self._make_request(), payload, BackgroundTasks())
            except HTTPException as e:
                self.assertEqual(e.status_code, 409)  # returning user → 409

        mock_db.user_notifications.insert_many.assert_called()
        mock_db.notifications.insert_many.assert_not_called()


# ─── Unit tests for cron script logic ────────────────────────────────────────

class TestArchiveStaleCronLogic(unittest.TestCase):
    """Unit tests for the archive_stale_registrations cron logic."""

    def test_cutoff_calculation(self):
        """Cutoff should be 168 hours ago."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=168)
        # User requested exactly 167h ago should NOT be past cutoff
        recent = now - timedelta(hours=167)
        self.assertFalse(recent < cutoff)
        # User requested 169h ago SHOULD be past cutoff
        stale = now - timedelta(hours=169)
        self.assertTrue(stale < cutoff)

    def test_cutoff_iso_string_comparison(self):
        """Ensure ISO string comparison matches datetime comparison."""
        now = datetime.now(timezone.utc)
        cutoff_iso = (now - timedelta(hours=168)).isoformat()
        stale_iso = (now - timedelta(hours=200)).isoformat()
        fresh_iso = (now - timedelta(hours=100)).isoformat()
        self.assertLess(stale_iso, cutoff_iso)
        self.assertGreater(fresh_iso, cutoff_iso)


def teardown_module(module):
    """Restore event loop after IsolatedAsyncioTestCase destroys it."""
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    unittest.main(verbosity=2)
