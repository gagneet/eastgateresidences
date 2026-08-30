"""
Tests for Registration Workflow Fixes (Session 2026-03-09):

Bugs Fixed:
  1. Guest/Tenant registration: owner bell notification missing 'link' field
     → Owner couldn't navigate to tenant-approvals page from bell notification
  2. `admin_users` NameError when archived user tries to re-register
     → Server 500 crash on archived-user return path
  3. Strata Manager excluded from admin/FYI notifications on all registration paths
     → Strata manager not notified for guest/tenant/owner registrations
  4. owner_registration_decision (portal) wrote to db.notifications instead of db.user_notifications
     → Admin bell notifications never appeared after owner portal approval
  5. Missing 'link' field in admin notifications from owner portal approval
     → Admins couldn't navigate directly from bell notification to user record
"""

import asyncio
import os
import re
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

# ── env setup ─────────────────────────────────────────────────────────────────
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-tests")
os.environ.setdefault("FRONTEND_URL", "https://www.eastgateresidences.com.au")

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
sys.path.insert(0, BACKEND_DIR)


def _read_auth():
    with open(os.path.join(BACKEND_DIR, "routers", "auth.py")) as f:
        return f.read()


def _read_server():
    with open(os.path.join(BACKEND_DIR, "server.py")) as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Bug Fix: owner bell notification has 'link' field (Bug 2)
# ─────────────────────────────────────────────────────────────────────────────

class TestOwnerBellNotificationLinkField(unittest.TestCase):
    """Owner bell notification must have link=/requests/tenant-approvals."""

    def test_owner_notifications_dict_has_link_key(self):
        """The owner_notifications list comprehension must include 'link' key."""
        src = _read_auth()
        # Find the owner_notifications list comprehension
        match = re.search(r'owner_notifications\s*=\s*\[', src)
        self.assertIsNotNone(match, "owner_notifications list not found in auth.py")
        # Extract from that point until the closing ] for owner_user in unit_owners
        start = match.start()
        # Find the section up to the end of the comprehension
        section = src[start:start + 800]
        self.assertIn('"link"', section,
                      "owner_notifications must include 'link' field")

    def test_owner_notification_link_points_to_tenant_approvals(self):
        """Owner notification link must point to /requests/tenant-approvals."""
        src = _read_auth()
        match = re.search(r'owner_notifications\s*=\s*\[', src)
        self.assertIsNotNone(match)
        section = src[match.start():match.start() + 800]
        self.assertIn("tenant-approvals", section,
                      "owner_notifications link must contain 'tenant-approvals'")

    def test_owner_notification_type_is_tenant_approval_required(self):
        """Owner notification type must be tenant_approval_required."""
        src = _read_auth()
        match = re.search(r'owner_notifications\s*=\s*\[', src)
        self.assertIsNotNone(match)
        section = src[match.start():match.start() + 800]
        self.assertIn("tenant_approval_required", section)

    def test_link_is_relative_path_not_full_url(self):
        """The link must be a relative /dashboard/... path, not a full URL."""
        src = _read_auth()
        # Extract the link value in owner_notifications
        match = re.search(r'"link"\s*:\s*"(/requests/tenant-approvals)"', src)
        self.assertIsNotNone(match,
                             'owner_notifications link must be "/requests/tenant-approvals"')
        link_val = match.group(1)
        self.assertFalse(link_val.startswith("http"),
                         f"Link must be relative, got: {link_val}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Bug Fix: admin_users defined before use in archived-user path (Bug 1)
# ─────────────────────────────────────────────────────────────────────────────

class TestArchivedUserAdminUsersDefined(unittest.TestCase):
    """admin_users must be fetched from DB before being referenced."""

    def test_admin_users_assigned_before_if_check_in_archived_block(self):
        """In the archived user block, admin_users must be assigned before 'if admin_users:'."""
        src = _read_auth()
        archived_match = re.search(
            r'if existing\.get\("status"\) == "archived":(.*?)raise HTTPException',
            src, re.DOTALL
        )
        self.assertIsNotNone(archived_match, "Archived user block not found in auth.py")
        block = archived_match.group(1)

        # admin_users is assigned either via direct await or via asyncio.gather unpacking
        define_match = re.search(
            r'(?:admin_users\s*=\s*await\s+|_,\s*admin_users\s*=\s*await\s+asyncio\.gather)',
            block
        )
        check_match = re.search(r'if admin_users:', block)

        self.assertIsNotNone(define_match,
                             "admin_users must be assigned in archived block (direct await or gather)")
        self.assertIsNotNone(check_match,
                             "'if admin_users:' check must exist in archived block")
        self.assertLess(define_match.start(), check_match.start(),
                        "admin_users must be fetched BEFORE the 'if admin_users:' check")

    def test_archived_user_admin_query_includes_strata_manager(self):
        """The archived-user block must use the building-scoped reviewer helper (which includes strata_manager)."""
        src = _read_auth()
        archived_match = re.search(
            r'if existing\.get\("status"\) == "archived":(.*?)raise HTTPException',
            src, re.DOTALL
        )
        self.assertIsNotNone(archived_match)
        block = archived_match.group(1)

        # Either a raw db.users.find including strata_manager, or the building-scoped helper
        # (_get_staff_registration_reviewers) which explicitly includes strata_manager.
        uses_scoped_helper = re.search(r'_get_staff_registration_reviewers', block)
        raw_query = re.search(
            r'admin_users\s*=\s*await\s+db\.users\.find\s*\(\s*\{([^}]+)\}', block
        )
        self.assertTrue(
            uses_scoped_helper or (raw_query and "strata_manager" in raw_query.group(1)),
            "Archived-user block must use _get_staff_registration_reviewers or a raw query including strata_manager"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Bug Fix: Strata Manager included in all admin notification queries (Bugs 3, 6)
# ─────────────────────────────────────────────────────────────────────────────

class TestStrataManagerInAllAdminQueries(unittest.TestCase):
    """All admin notification queries must include strata_manager."""

    # The three inline db.users.find(...) queries these tests used to regex out of
    # auth.py were consolidated into _get_staff_registration_reviewers(), so
    # scanning the source for them now finds nothing and fails for the wrong
    # reason. The contract they were protecting — strata_manager is notified —
    # is asserted directly against the shared reviewer set and helper below,
    # which is both stronger and refactor-proof.

    def test_reviewer_roles_include_strata_manager(self):
        """The single reviewer set feeding every registration notification path."""
        from routers.auth import _STAFF_REVIEWER_ROLES
        from models.user import UserRole

        self.assertIn(UserRole.STRATA_MANAGER, _STAFF_REVIEWER_ROLES)
        self.assertIn(UserRole.STRATA_ADMIN, _STAFF_REVIEWER_ROLES)
        self.assertIn(UserRole.ADMIN_STAFF, _STAFF_REVIEWER_ROLES)

    def test_registration_notifications_route_through_the_shared_reviewer_helper(self):
        """No path may hand-roll its own admin query and drift from the reviewer set.

        Both the FYI branch and the else branch now assign from
        _get_staff_registration_reviewers(...); if someone reintroduces an inline
        db.users.find({...role...}) for notifications, this catches it.
        """
        src = _read_auth()
        self.assertGreaterEqual(
            len(re.findall(r'=\s*await\s+_get_staff_registration_reviewers\(', src)), 2,
            "registration notification paths should resolve reviewers via the shared helper",
        )

    def test_reviewer_query_scopes_building_staff_and_includes_super_admin(self):
        """Building staff are membership-scoped; super_admin is matched separately.

        super_admin holds no per-building membership, so a membership-only query
        would silently never notify them.
        """
        from models.user import UserRole
        import routers.auth as auth_module

        captured = {}

        class _Cursor:
            async def to_list(self, *_a, **_kw):
                return []

        class _Users:
            def find(self, query, *_a, **_kw):
                captured["query"] = query
                return _Cursor()

        class _Memberships:
            async def distinct(self, *_a, **_kw):
                return ["staff-1"]

        class _DB:
            users = _Users()
            memberships = _Memberships()

        # Own loop: asyncio.get_event_loop() picks up pytest-asyncio's session
        # loop, which is already closed by the time this sync test runs in a
        # full-suite pass (it passes in isolation, fails in the suite).
        loop = asyncio.new_event_loop()
        try:
            with patch.object(auth_module, "db", _DB()):
                loop.run_until_complete(
                    auth_module._get_staff_registration_reviewers("13195")
                )
        finally:
            loop.close()

        clauses = captured["query"]["$or"]
        building_clause = next(c for c in clauses if "id" in c)
        super_clause = next(c for c in clauses if "id" not in c)

        self.assertIn(UserRole.STRATA_MANAGER, building_clause["role"]["$in"])
        self.assertEqual(building_clause["id"], {"$in": ["staff-1"]})
        self.assertEqual(super_clause["role"], UserRole.SUPER_ADMIN)

    def test_server_py_owner_decision_admin_query_includes_strata_manager(self):
        """server.py owner_registration_decision must include strata_manager in admin query."""
        src = _read_server()
        match = re.search(
            r'async def owner_registration_decision(.*?)(?=\n@api_router|\Z)',
            src, re.DOTALL
        )
        self.assertIsNotNone(match, "owner_registration_decision function not found")
        func_body = match.group(0)
        # Find the admin_users query in the function
        query_match = re.search(
            r'admin_users\s*=\s*await\s+db\.users\.find\(\s*(\{[\s\S]*?\})\s*\)\.to_list',
            func_body,
            re.DOTALL,
        )
        self.assertIsNotNone(query_match, "admin_users query not found in owner_registration_decision")
        self.assertIn("strata_manager", query_match.group(1),
                      "owner_registration_decision admin query must include strata_manager")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Bug Fix: owner_registration_decision uses correct collection (Bug 4)
# ─────────────────────────────────────────────────────────────────────────────

class TestOwnerDecisionCorrectCollection(unittest.TestCase):
    """owner_registration_decision must write to user_notifications, not notifications."""

    def test_no_db_notifications_insert_in_owner_decision(self):
        """server.py owner_registration_decision must NOT use db.notifications.insert_many."""
        src = _read_server()
        match = re.search(
            r'async def owner_registration_decision(.*?)(?=\n@api_router|\Z)',
            src, re.DOTALL
        )
        self.assertIsNotNone(match, "owner_registration_decision function not found")
        func_body = match.group(0)
        self.assertNotIn("db.notifications.insert_many", func_body,
                         "Must NOT write to db.notifications — wrong collection!")

    def test_db_user_notifications_insert_in_owner_decision(self):
        """server.py owner_registration_decision must use db.user_notifications.insert_many."""
        src = _read_server()
        match = re.search(
            r'async def owner_registration_decision(.*?)(?=\n@api_router|\Z)',
            src, re.DOTALL
        )
        self.assertIsNotNone(match)
        func_body = match.group(0)
        self.assertIn("db.user_notifications.insert_many", func_body,
                      "Must write to db.user_notifications")

    def test_owner_decision_notification_has_link_field(self):
        """Notifications created in owner_registration_decision must have 'link' field."""
        src = _read_server()
        match = re.search(
            r'async def owner_registration_decision(.*?)(?=\n@api_router|\Z)',
            src, re.DOTALL
        )
        self.assertIsNotNone(match)
        func_body = match.group(0)
        # Find the notifications list building block
        notif_match = re.search(r'notifications\s*=\s*\[\](.*?)await db\.user_notifications',
                                func_body, re.DOTALL)
        self.assertIsNotNone(notif_match,
                             "notifications list building + user_notifications insert not found")
        notif_block = notif_match.group(0)
        self.assertIn('"link"', notif_block,
                      "notifications in owner_registration_decision must have 'link' field")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Functional test: owner_registration_decision approve (mocked DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestOwnerDecisionFunctional(unittest.IsolatedAsyncioTestCase):
    """Functional verification of owner_registration_decision with mocked DB."""

    async def _run_approve(self):
        from fastapi import BackgroundTasks
        bt = BackgroundTasks()

        current_user = {
            "id": "owner-001",
            "full_name": "Unit Owner",
            "email": "owner@example.com",
            "role": "owner",
            "unit_number": "TH087",
        }
        target = {
            "id": "tenant-001",
            "full_name": "Test Tenant",
            "email": "tenant@example.com",
            "role": "tenant",
            "unit_number": "TH087",
            "status": "pending_owner_approval",
            "is_active": True,
            "is_approved": False,
        }
        admins = [
            {"id": "admin-001", "email": "admin@eastgateresidences.com.au",
             "role": "super_admin", "full_name": "Admin"},
            {"id": "strata-001", "email": "manager@eastgateresidences.com.au",
             "role": "strata_manager", "full_name": "Manager"},
            {"id": "ec-001", "email": "ec@eastgateresidences.com.au",
             "role": "ec_member", "full_name": "EC Member"},
        ]

        user_notif_inserts = []
        wrong_notif_inserts = []

        with patch("server.db") as mock_db, \
                patch("server.send_email_async", new_callable=AsyncMock), \
                patch("server.get_current_user", return_value=current_user):
            mock_db.users.find_one = AsyncMock(return_value=target)
            mock_db.users.update_one = AsyncMock()
            mock_db.user_units.update_many = AsyncMock()
            mock_db.memberships.find_one = AsyncMock(return_value={
                "building_id": "13195",
                "user_id": target["id"],
                "is_active": True,
            })

            admin_result = MagicMock()
            admin_result.to_list = AsyncMock(return_value=admins)
            mock_db.users.find = MagicMock(return_value=admin_result)
            mock_db.memberships.distinct = AsyncMock(return_value=[
                "strata-001",
                "ec-001",
            ])

            mock_db.user_notifications = MagicMock()
            mock_db.user_notifications.insert_many = AsyncMock(
                side_effect=lambda docs: user_notif_inserts.extend(docs)
            )
            mock_db.notifications = MagicMock()
            mock_db.notifications.insert_many = AsyncMock(
                side_effect=lambda docs: wrong_notif_inserts.extend(docs)
            )
            # The approval path now resolves the building's display name for the
            # notification/email copy instead of hardcoding "East Gate Residences".
            mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Building"})

            from server import owner_registration_decision, OwnerApprovalData
            decision = OwnerApprovalData(action="approve", notes="Looks good")
            result = await owner_registration_decision(
                user_id="tenant-001",
                decision=decision,
                background_tasks=bt,
                current_user=current_user,
                building_id="13195",
            )

        return result, user_notif_inserts, wrong_notif_inserts

    async def test_approve_writes_to_user_notifications(self):
        """Approve must write to db.user_notifications."""
        _, user_notif_inserts, _ = await self._run_approve()
        # Filter to user_approval type notifications
        approval_notifs = [n for n in user_notif_inserts if n.get("type") == "user_approval"]
        self.assertGreater(len(approval_notifs), 0,
                           "Expected user_approval notifications in user_notifications")

    async def test_approve_does_not_write_to_wrong_collection(self):
        """Approve must NOT write to db.notifications."""
        _, _, wrong_notif_inserts = await self._run_approve()
        self.assertEqual(len(wrong_notif_inserts), 0,
                         "Must NOT write to db.notifications (wrong collection!)")

    async def test_approve_notifications_have_link(self):
        """Notifications from approve must have 'link' field."""
        _, user_notif_inserts, _ = await self._run_approve()
        approval_notifs = [n for n in user_notif_inserts if n.get("type") == "user_approval"]
        for notif in approval_notifs:
            self.assertIn("link", notif,
                          f"Notification missing 'link' field: {notif}")
            # Admin recipients get a deep link straight to the pending registration
            # in the user-management UI (commit 6de9acf1), not a generic /dashboard.
            self.assertTrue(notif["link"].startswith("/admin/users"),
                            f"Link must start with /admin/users, got: {notif['link']}")

    async def test_approve_notifies_strata_manager(self):
        """Approve must notify the strata_manager user."""
        _, user_notif_inserts, _ = await self._run_approve()
        notified_ids = {n.get("user_id") for n in user_notif_inserts}
        self.assertIn("strata-001", notified_ids,
                      "strata_manager (id=strata-001) must be notified on approve")

    async def test_approve_notifies_ec_member(self):
        """Approve must notify the ec_member reviewer too."""
        _, user_notif_inserts, _ = await self._run_approve()
        notified_ids = {n.get("user_id") for n in user_notif_inserts}
        self.assertIn("ec-001", notified_ids,
                      "ec_member (id=ec-001) must be notified on approve")

    async def test_approve_returns_success_message(self):
        """Approve returns success message dict."""
        result, _, _ = await self._run_approve()
        self.assertIn("message", result,
                      "approve must return a dict with 'message'")
        self.assertIn("approved", result["message"].lower())


# ─────────────────────────────────────────────────────────────────────────────
# 6. Archived User Functional Test
# ─────────────────────────────────────────────────────────────────────────────

class TestArchivedUserFunctional(unittest.IsolatedAsyncioTestCase):
    """Archived user re-registration must return 409, not 500."""

    async def test_archived_user_returns_409_not_500(self):
        """Re-registering an archived email must return 409 (not 500 NameError)."""
        from fastapi import BackgroundTasks, HTTPException
        bt = BackgroundTasks()

        archived_user = {
            "id": "old-001",
            "full_name": "John Archived",
            "email": "john.archived@example.com",
            "role": "owner",
            "status": "archived",
            "unit_number": "UA001",
        }
        admins = [
            {"id": "adm-1", "email": "admin@eastgateresidences.com.au",
             "role": "super_admin", "full_name": "Admin"},
        ]

        with patch("routers.auth.db") as mock_db, \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):

            mock_db.users.find_one = AsyncMock(return_value=archived_user)
            mock_db.users.update_one = AsyncMock()

            # _get_staff_registration_reviewers calls memberships.distinct then users.find
            mock_db.memberships.distinct = AsyncMock(return_value=["adm-1"])
            admin_result = MagicMock()
            admin_result.to_list = AsyncMock(return_value=admins)
            mock_db.users.find = MagicMock(return_value=admin_result)

            mock_db.user_notifications = MagicMock()
            mock_db.user_notifications.insert_many = AsyncMock()

            from routers.auth import register
            from starlette.requests import Request as StarletteRequest
            scope = {
                "type": "http", "method": "POST",
                "path": "/api/auth/register",
                "query_string": b"", "headers": [],
                "client": ("127.0.0.1", 12345),
            }
            req = StarletteRequest(scope)

            from types import SimpleNamespace
            user_data = SimpleNamespace(
                email=archived_user["email"],
                full_name="John Archived",
                password="Test1234!",
                role="owner",
                unit_number="UA001",
                by_laws_acknowledged=False,
                end_date=None,
                phone=None,
                address=None,
                invite_token=None,
            )

            # Patch the rate limiter to avoid 5/minute limit during test
            with patch("utils.rate_limit.limiter.limit", return_value=lambda f: f):
                try:
                    await register(req, user_data, bt)
                    self.fail("Expected HTTPException but none raised")
                except HTTPException as exc:
                    self.assertEqual(exc.status_code, 409,
                                     f"Expected 409, got {exc.status_code} — possible NameError crash")
                    detail = exc.detail
                    self.assertEqual(detail.get("code"), "archived_user_return_request",
                                     "Expected archived_user_return_request code")
                except NameError as exc:
                    self.fail(f"NameError raised — admin_users not defined before use: {exc}")

    async def test_archived_user_admin_notified(self):
        """When archived user returns, admin notifications must be created."""
        from fastapi import BackgroundTasks, HTTPException
        bt = BackgroundTasks()

        archived_user = {
            "id": "old-002",
            "full_name": "Jane Archived",
            "email": "jane.archived@example.com",
            "role": "tenant",
            "status": "archived",
            "unit_number": "TH071",
        }
        admins = [
            {"id": "adm-1", "email": "admin@eastgateresidences.com.au",
             "role": "super_admin", "full_name": "Admin"},
            {"id": "strata-001", "email": "manager@eastgateresidences.com.au",
             "role": "strata_manager", "full_name": "Manager"},
        ]
        inserted = []

        with patch("routers.auth.db") as mock_db, \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):

            mock_db.users.find_one = AsyncMock(return_value=archived_user)
            mock_db.users.update_one = AsyncMock()

            # _get_staff_registration_reviewers calls memberships.distinct then users.find
            mock_db.memberships.distinct = AsyncMock(return_value=["adm-1", "strata-001"])
            admin_result = MagicMock()
            admin_result.to_list = AsyncMock(return_value=admins)
            mock_db.users.find = MagicMock(return_value=admin_result)

            mock_db.user_notifications = MagicMock()
            mock_db.user_notifications.insert_many = AsyncMock(
                side_effect=lambda docs: inserted.extend(docs)
            )

            from routers.auth import register
            from starlette.requests import Request as StarletteRequest
            scope = {
                "type": "http", "method": "POST",
                "path": "/api/auth/register",
                "query_string": b"", "headers": [],
                "client": ("127.0.0.2", 12345),  # different IP to avoid rate limit
            }
            req = StarletteRequest(scope)

            from types import SimpleNamespace
            user_data = SimpleNamespace(
                email=archived_user["email"],
                full_name="Jane Archived",
                password="Test1234!",
                role="tenant",
                unit_number="TH071",
                by_laws_acknowledged=True,
                end_date=None,
                phone=None,
                address=None,
                invite_token=None,
            )

            with patch("utils.rate_limit.limiter.limit", return_value=lambda f: f):
                try:
                    await register(req, user_data, bt)
                except HTTPException:
                    pass

        # Should have created notifications for both admin and strata_manager
        self.assertGreater(len(inserted), 0, "Expected admin notifications to be created")
        notified_ids = {n.get("user_id") for n in inserted}
        self.assertIn("strata-001", notified_ids,
                      "strata_manager must be notified when archived user returns")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Frontend Route Exists
# ─────────────────────────────────────────────────────────────────────────────

class TestFrontendRoutes(unittest.TestCase):
    """Verify required frontend pages and routes exist."""

    def test_tenant_approvals_next_page_exists(self):
        """Next.js /requests/tenant-approvals page must exist."""
        # Moved from (dashboard)/dashboard/... to (app)/requests/... in commit
        # 05b6ccf6 ("move feature pages to product namespaces").
        page = os.path.normpath(os.path.join(
            BACKEND_DIR, "..", "frontend", "src",
            "app", "(app)", "requests", "tenant-approvals", "page.tsx"
        ))
        self.assertTrue(os.path.exists(page),
                        f"tenant-approvals page.tsx not found at {page}")

    def test_owner_tenant_approvals_component_exists(self):
        """OwnerTenantApprovalsPage.jsx component must exist."""
        comp = os.path.normpath(os.path.join(
            BACKEND_DIR, "..", "frontend", "src",
            "pages", "dashboard", "OwnerTenantApprovalsPage.jsx"
        ))
        self.assertTrue(os.path.exists(comp),
                        f"OwnerTenantApprovalsPage.jsx not found at {comp}")

    def test_dashboard_layout_has_tenant_approvals_nav(self):
        """DashboardLayout must have a nav entry for /requests/tenant-approvals."""
        layout = os.path.normpath(os.path.join(
            BACKEND_DIR, "..", "frontend", "src",
            "components", "layout", "DashboardLayout.tsx"
        ))
        with open(layout) as f:
            src = f.read()
        self.assertIn("/requests/tenant-approvals", src,
                      "DashboardLayout must have nav link to /requests/tenant-approvals")

    def test_dashboard_layout_notification_handler_uses_notif_link(self):
        """DashboardLayout bell notification click must route via notif.link."""
        layout = os.path.normpath(os.path.join(
            BACKEND_DIR, "..", "frontend", "src",
            "components", "layout", "DashboardLayout.tsx"
        ))
        with open(layout) as f:
            src = f.read()
        self.assertIn("notif.link", src,
                      "Bell notification click handler must use notif.link")
        self.assertIn("router.push(notif.link)", src,
                      "Must call router.push(notif.link) on notification click")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Source Code — All Notification Queries Use Correct Roles
# ─────────────────────────────────────────────────────────────────────────────

class TestAllAdminQueriesConsistent(unittest.TestCase):
    """All admin DB queries for registration notifications must be consistent."""

    def test_all_super_admin_queries_also_include_strata_manager(self):
        """Every DB query that includes super_admin must also include strata_manager."""
        src = _read_auth()
        # Find all $in role arrays that include super_admin
        matches = re.findall(r'"role"\s*:\s*\{\s*"\$in"\s*:\s*\[([^\]]+)\]', src)
        for roles_str in matches:
            if '"super_admin"' in roles_str or "'super_admin'" in roles_str:
                self.assertIn("strata_manager", roles_str,
                              f"Admin role query includes super_admin but not strata_manager: {roles_str}")

    def test_server_py_super_admin_queries_in_auth_context_include_strata_manager(self):
        """server.py owner_registration_decision admin query includes strata_manager."""
        src = _read_server()
        # Find admin queries inside owner_registration_decision
        func_match = re.search(
            r'async def owner_registration_decision(.*?)(?=\n@api_router|\Z)',
            src, re.DOTALL
        )
        self.assertIsNotNone(func_match)
        func_body = func_match.group(0)
        matches = re.findall(r'"role"\s*:\s*\{\s*"\$in"\s*:\s*\[([^\]]+)\]', func_body)
        for roles_str in matches:
            if '"super_admin"' in roles_str:
                self.assertIn("strata_manager", roles_str,
                              f"Admin query in owner_registration_decision missing strata_manager: {roles_str}")


def teardown_module(module):
    """Restore event loop after IsolatedAsyncioTestCase destroys it."""
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    unittest.main(verbosity=2)
