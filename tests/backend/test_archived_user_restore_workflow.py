"""
Tests for Archived User Restore Workflow:

1. _get_staff_registration_reviewers() in routers/auth.py
   - building-scoped two-step query (memberships.distinct → users.find)
   - super_admin always included; building-scoped strata roles filtered by membership
   - deduplication, inactive exclusion, cross-building isolation

2. pending_return_details storage during archived user re-registration (register())
   - stored fields: full_name, phone, unit_number, by_laws_acknowledged, requested_at
   - optional end_date included when provided
   - original archived fields NOT overwritten
   - password_hash IS updated
   - returns 409 with {"code": "archived_user_return_request"}
   - multi-tenant isolation: building 16244 queries use building-scoped reviewer fetch

3. restore_user_endpoint() in server.py (POST /users/{user_id}/restore)
   - super_admin, strata_manager, admin_staff allowed; 403 for all other roles
   - 404 for unknown user; 400 if user is not archived AND has no inactive user_units
   - applies pending_return_details fields (full_name, phone, by_laws_acknowledged, end_date, unit_number)
   - $unset pending_return_details after restore
   - unit change: deactivates old user_units, inserts new record for pending unit
   - unit same: reactivates existing user_units
   - no pending unit: reactivates existing user_units
   - 409 on unit conflict (active primary occupant of same role in new unit)
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch, call

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


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_cursor(docs):
    """Return a Motor-like cursor mock whose .to_list() resolves to docs."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


def _make_starlette_request(path="/api/auth/register", ip="127.0.0.1", port=12345):
    from starlette.requests import Request as StarletteRequest
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [],
        "client": (ip, port),
    }
    return StarletteRequest(scope)


# ═════════════════════════════════════════════════════════════════════════════
# Class 1 — _get_staff_registration_reviewers
# ═════════════════════════════════════════════════════════════════════════════

class TestGetStaffRegistrationReviewers(unittest.IsolatedAsyncioTestCase):
    """Unit tests for _get_staff_registration_reviewers() in routers/auth.py."""

    async def _call(self, mock_db):
        """Import and call the helper with routers.auth.db patched."""
        from routers.auth import _get_staff_registration_reviewers
        return await _get_staff_registration_reviewers("13195")

    # ── 1. Super admin always included ────────────────────────────────────────

    async def test_returns_super_admin_always(self):
        """super_admin is returned even if they have no building membership."""
        super_admin = {
            "id": "sa-001",
            "email": "admin@example.com",
            "full_name": "Super Admin",
            "role": "super_admin",
            "building_id": "13195",
            "is_test_data": True,
        }
        with patch("routers.auth.db") as mock_db:
            mock_db.memberships.distinct = AsyncMock(return_value=[])
            mock_db.users.find = MagicMock(return_value=_make_cursor([super_admin]))

            result = await self._call(mock_db)

        ids = [r["id"] for r in result]
        self.assertIn("sa-001", ids, "super_admin must always be returned")

    # ── 2. Building-scoped strata_manager returned when member ────────────────

    async def test_returns_building_scoped_strata_manager(self):
        """strata_manager who is a member of building 13195 is included."""
        strata_mgr = {
            "id": "sm-001",
            "email": "manager@example.com",
            "full_name": "Strata Manager",
            "role": "strata_manager",
            "building_id": "13195",
            "is_active": True,
            "is_test_data": True,
        }
        with patch("routers.auth.db") as mock_db:
            mock_db.memberships.distinct = AsyncMock(return_value=["sm-001"])
            mock_db.users.find = MagicMock(return_value=_make_cursor([strata_mgr]))

            result = await self._call(mock_db)

        ids = [r["id"] for r in result]
        self.assertIn("sm-001", ids, "strata_manager member of building 13195 must be returned")

    # ── 3. Cross-building isolation ───────────────────────────────────────────

    async def test_excludes_reviewers_from_different_building(self):
        """Member of building 16244 must NOT appear in results for building 13195 query."""
        # The memberships.distinct call for building_id=13195 returns only 13195 members.
        # A user who is ONLY a member of 16244 should not be in member_user_ids.
        strata_13195 = {
            "id": "sm-13195",
            "email": "mgr13195@example.com",
            "full_name": "Manager 13195",
            "role": "strata_manager",
            "building_id": "13195",
            "is_active": True,
            "is_test_data": True,
        }
        with patch("routers.auth.db") as mock_db:
            # Only 13195 member IDs returned — 16244 member is absent
            mock_db.memberships.distinct = AsyncMock(return_value=["sm-13195"])
            mock_db.users.find = MagicMock(return_value=_make_cursor([strata_13195]))

            from routers.auth import _get_staff_registration_reviewers
            result = await _get_staff_registration_reviewers("13195")

        ids = [r["id"] for r in result]
        self.assertIn("sm-13195", ids)
        self.assertNotIn("sm-16244", ids,
                         "member of building 16244 must NOT appear in 13195 reviewer query")

    async def test_cross_building_distinct_uses_building_id_param(self):
        """memberships.distinct must be called with the requested building_id, not a hardcoded value."""
        with patch("routers.auth.db") as mock_db:
            mock_db.memberships.distinct = AsyncMock(return_value=[])
            mock_db.users.find = MagicMock(return_value=_make_cursor([]))

            from routers.auth import _get_staff_registration_reviewers
            await _get_staff_registration_reviewers("16244")

        call_args = mock_db.memberships.distinct.call_args
        # Second positional/keyword arg is the filter dict
        filter_arg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("filter", {})
        self.assertEqual(filter_arg.get("building_id"), "16244",
                         "memberships.distinct must filter by the requested building_id, not hardcoded")

    # ── 4. Deduplication ──────────────────────────────────────────────────────

    async def test_deduplicates_results(self):
        """Same user appearing twice in the cursor output is deduplicated."""
        reviewer = {
            "id": "sm-dup",
            "email": "dup@example.com",
            "full_name": "Dup Manager",
            "role": "strata_manager",
            "building_id": "13195",
            "is_active": True,
            "is_test_data": True,
        }
        with patch("routers.auth.db") as mock_db:
            mock_db.memberships.distinct = AsyncMock(return_value=["sm-dup"])
            # Simulate a cursor returning the same user twice (e.g. index artefact)
            mock_db.users.find = MagicMock(return_value=_make_cursor([reviewer, reviewer]))

            result = await self._call(mock_db)

        ids = [r["id"] for r in result]
        self.assertEqual(ids.count("sm-dup"), 1, "duplicate user must be deduplicated")

    # ── 5. Empty result ───────────────────────────────────────────────────────

    async def test_returns_empty_when_no_reviewers(self):
        """Returns empty list when no matching users exist."""
        with patch("routers.auth.db") as mock_db:
            mock_db.memberships.distinct = AsyncMock(return_value=[])
            mock_db.users.find = MagicMock(return_value=_make_cursor([]))

            result = await self._call(mock_db)

        self.assertEqual(result, [], "should return empty list when no reviewers match")

    # ── 6. Inactive users excluded ────────────────────────────────────────────

    async def test_excludes_inactive_users(self):
        """Inactive users (is_active=False) must not be returned by the DB query."""
        # The DB query itself filters is_active=True; we verify the helper
        # only returns what the DB gives it (no inactive records sneak through).
        active_user = {
            "id": "sm-active",
            "email": "active@example.com",
            "full_name": "Active Manager",
            "role": "strata_manager",
            "building_id": "13195",
            "is_active": True,
            "is_test_data": True,
        }
        with patch("routers.auth.db") as mock_db:
            mock_db.memberships.distinct = AsyncMock(return_value=["sm-active", "sm-inactive"])
            # DB returns only the active user (is_active filter applied server-side)
            mock_db.users.find = MagicMock(return_value=_make_cursor([active_user]))

            result = await self._call(mock_db)

        ids = [r["id"] for r in result]
        self.assertIn("sm-active", ids)
        self.assertNotIn("sm-inactive", ids, "inactive user must not appear in reviewer list")


# ═════════════════════════════════════════════════════════════════════════════
# Class 2 — pending_return_details storage during register()
# ═════════════════════════════════════════════════════════════════════════════

class TestPendingReturnDetailsStorage(unittest.IsolatedAsyncioTestCase):
    """Tests that register() correctly stores pending_return_details for archived users."""

    def _make_archived_user(self, building_id="13195"):
        return {
            "id": "old-100",
            "full_name": "John Archived",
            "email": "john.archived@example.com",
            "role": "owner",
            "status": "archived",
            "unit_number": "UA001",
            "building_id": building_id,
            "is_test_data": True,
        }

    def _make_admin(self, building_id="13195"):
        return {
            "id": "adm-100",
            "email": "admin@example.com",
            "full_name": "Admin User",
            "role": "super_admin",
            "building_id": building_id,
            "is_test_data": True,
        }

    def _make_user_data(self, **kwargs):
        defaults = dict(
            email="john.archived@example.com",
            full_name="John Archived New Name",
            password="NewPass1234!",
            role="owner",
            unit_number="UA002",
            by_laws_acknowledged=True,
            end_date=None,
            phone="0400123456",
            address=None,
            # register() reads this to resolve a building-scoped resident invite;
            # None means ordinary self-service registration.
            invite_token=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    async def _invoke_register_409(self, archived_user, user_data, building_id="13195"):
        """Run register() and return (update_call_args, notifications_inserted).
        Always raises HTTPException(409) for archived path; we capture the side-effects.
        """
        from fastapi import BackgroundTasks, HTTPException
        bt = BackgroundTasks()
        req = _make_starlette_request(ip="10.0.0.1")

        update_calls = []
        notifications_inserted = []

        with patch("routers.auth.db") as mock_db, \
                patch("routers.auth.send_email_async", new_callable=AsyncMock), \
                patch("utils.rate_limit.limiter.limit", return_value=lambda f: f):
            mock_db.users.find_one = AsyncMock(return_value=archived_user)
            mock_db.users.update_one = AsyncMock(
                side_effect=lambda *a, **kw: update_calls.append(a)
            )
            mock_db.memberships.distinct = AsyncMock(return_value=["adm-100"])
            admin = self._make_admin(building_id)
            mock_db.users.find = MagicMock(return_value=_make_cursor([admin]))
            mock_db.user_notifications = MagicMock()
            mock_db.user_notifications.insert_many = AsyncMock(
                side_effect=lambda docs: notifications_inserted.extend(docs)
            )

            from routers.auth import register
            with self.assertRaises(HTTPException) as ctx:
                await register(req, user_data, bt, _building_id=building_id)

        return ctx.exception, update_calls, notifications_inserted

    # ── 1. pending_return_details stored ──────────────────────────────────────

    async def test_pending_return_details_stored_on_archived_reregister(self):
        """When an archived user re-registers, pending_return_details is written to the DB."""
        archived = self._make_archived_user()
        user_data = self._make_user_data()

        exc, update_calls, _ = await self._invoke_register_409(archived, user_data)

        self.assertEqual(exc.status_code, 409)
        self.assertTrue(update_calls, "db.users.update_one must be called for archived path")

        # Extract the $set payload from the update_one call
        filter_arg, update_arg = update_calls[0]
        set_payload = update_arg.get("$set", {})
        self.assertIn("pending_return_details", set_payload,
                      "pending_return_details must be written to the DB")

    # ── 2. Field contents ─────────────────────────────────────────────────────

    async def test_pending_return_details_contains_expected_fields(self):
        """pending_return_details contains full_name, phone, unit_number, by_laws_acknowledged, requested_at."""
        archived = self._make_archived_user()
        user_data = self._make_user_data(
            full_name="New Full Name",
            phone="0411000111",
            unit_number="UA099",
            by_laws_acknowledged=True,
        )

        _, update_calls, _ = await self._invoke_register_409(archived, user_data)

        set_payload = update_calls[0][1]["$set"]
        pending = set_payload["pending_return_details"]

        self.assertEqual(pending["full_name"], "New Full Name")
        self.assertEqual(pending["phone"], "0411000111")
        self.assertEqual(pending["unit_number"], "UA099")
        self.assertTrue(pending["by_laws_acknowledged"])
        self.assertIn("requested_at", pending, "requested_at must be included in pending_return_details")

    # ── 3. end_date included when provided ────────────────────────────────────

    async def test_pending_return_details_with_end_date(self):
        """end_date is included in pending_return_details when the registration provides one."""
        archived = self._make_archived_user()
        user_data = self._make_user_data(end_date="2027-03-31")

        _, update_calls, _ = await self._invoke_register_409(archived, user_data)

        pending = update_calls[0][1]["$set"]["pending_return_details"]
        self.assertIn("end_date", pending, "end_date must be stored in pending_return_details when provided")
        self.assertEqual(pending["end_date"], "2027-03-31")

    # ── 4. Original archived fields NOT overwritten ───────────────────────────

    async def test_original_fields_not_overwritten(self):
        """The $set only updates password_hash, pending_return_details, return_requested_at, updated_at —
        NOT full_name or unit_number directly on the user document."""
        archived = self._make_archived_user()
        user_data = self._make_user_data(full_name="Overwrite Attempt", unit_number="UA999")

        _, update_calls, _ = await self._invoke_register_409(archived, user_data)

        set_payload = update_calls[0][1]["$set"]
        allowed_keys = {"password_hash", "pending_return_details", "return_requested_at", "updated_at"}
        for key in set_payload:
            self.assertIn(key, allowed_keys,
                          f"Unexpected key '{key}' in $set — original fields must not be overwritten directly. "
                          f"Full $set: {set_payload}")

    # ── 5. password_hash updated ──────────────────────────────────────────────

    async def test_password_hash_updated(self):
        """password_hash IS updated during archived user re-registration."""
        archived = self._make_archived_user()
        user_data = self._make_user_data(password="BrandNew9999!")

        _, update_calls, _ = await self._invoke_register_409(archived, user_data)

        set_payload = update_calls[0][1]["$set"]
        self.assertIn("password_hash", set_payload,
                      "password_hash must be updated so the user can log in after restore")
        self.assertTrue(set_payload["password_hash"],
                        "password_hash must be a non-empty hash string")

    # ── 6. 409 response code ──────────────────────────────────────────────────

    async def test_returns_409_with_archived_user_return_request_code(self):
        """register() must raise HTTPException 409 with code=archived_user_return_request."""
        archived = self._make_archived_user()
        user_data = self._make_user_data()

        exc, _, _ = await self._invoke_register_409(archived, user_data)

        self.assertEqual(exc.status_code, 409)
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        self.assertEqual(detail.get("code"), "archived_user_return_request",
                         "409 detail must have code=archived_user_return_request")

    # ── 7. Multi-tenant: 16244 archived user triggers 16244-scoped reviewer fetch ──

    async def test_multi_tenant_isolation_building_16244(self):
        """Re-registration for a building 16244 archived user must fetch reviewers scoped to 16244."""
        archived = self._make_archived_user(building_id="16244")
        user_data = self._make_user_data(email="john.archived@example.com")

        from fastapi import BackgroundTasks, HTTPException
        bt = BackgroundTasks()
        req = _make_starlette_request(ip="10.0.0.2")

        distinct_calls = []

        # register() receives _building_id via Depends(get_optional_building).
        # When called directly (no ASGI machinery), the Depends object is passed as-is
        # unless we override get_optional_building to return the expected building_id.
        # The archived user's building_id drives the reviewer scope, so we set the
        # dependency to return "16244" to match the archived record.
        with patch("routers.auth.db") as mock_db, \
                patch("routers.auth.send_email_async", new_callable=AsyncMock), \
                patch("utils.rate_limit.limiter.limit", return_value=lambda f: f), \
                patch("routers.auth.get_optional_building", return_value="16244"):
            mock_db.users.find_one = AsyncMock(return_value=archived)
            mock_db.users.update_one = AsyncMock()
            mock_db.memberships.distinct = AsyncMock(
                side_effect=lambda field, filter_: distinct_calls.append(filter_) or []
            )
            mock_db.users.find = MagicMock(return_value=_make_cursor([]))
            mock_db.user_notifications = MagicMock()
            mock_db.user_notifications.insert_many = AsyncMock()

            from routers.auth import register
            with self.assertRaises(HTTPException):
                await register(req, user_data, bt, _building_id="16244")

        # Verify memberships.distinct was called with building_id from the archived record
        buildings_queried = [c.get("building_id") for c in distinct_calls]
        self.assertIn("16244", buildings_queried,
                      "reviewer fetch must use the archived user's building_id (16244), not hardcoded 13195")
        self.assertNotIn("13195", buildings_queried,
                         "reviewer fetch for 16244 user must not query 13195 memberships")


# ═════════════════════════════════════════════════════════════════════════════
# Class 3 — restore_user_endpoint: pending_return_details application
# ═════════════════════════════════════════════════════════════════════════════

class TestRestoreUserWithPendingDetails(unittest.IsolatedAsyncioTestCase):
    """Tests that restore_user_endpoint() correctly applies pending_return_details."""

    def _make_super_admin(self):
        return {
            "id": "sa-restore",
            "full_name": "Super Admin",
            "email": "admin@example.com",
            "role": "super_admin",
            "building_id": "13195",
        }

    def _make_archived_target(self, pending=None):
        user = {
            "id": "user-restore-001",
            "full_name": "Original Name",
            "email": "target@example.com",
            "role": "owner",
            "status": "archived",
            "unit_number": "UA010",
            "building_id": "13195",
            "is_test_data": True,
        }
        if pending is not None:
            user["pending_return_details"] = pending
        return user

    async def _invoke_restore(self, target_user, current_user=None, building_id="13195"):
        """Call restore_user_endpoint() with mocked DB. Returns (result, update_call_args)."""
        if current_user is None:
            current_user = self._make_super_admin()

        update_calls = []

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value=building_id), \
                patch("server.create_audit_log", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:

            mock_asyncio.create_task = MagicMock()

            mock_db.users.find_one = AsyncMock(return_value=target_user)
            mock_db.users.update_one = AsyncMock(
                side_effect=lambda *a, **kw: update_calls.append(a)
            )
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            mock_db.user_units.update_many = AsyncMock()
            mock_db.user_units.insert_one = AsyncMock()

            # Building-scope checks query is_active=False; conflict checks query
            # is_active=True. Return an inactive unit for scope checks so archived
            # users pass the association guard; return None for conflict checks.
            def _unit_find_one_se(query, *_a, **_kw):
                if query.get("is_active") is False:
                    return {"user_id": target_user["id"], "is_active": False,
                            "building_id": building_id}
                return None

            mock_db.user_units.find_one = AsyncMock(side_effect=_unit_find_one_se)
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            result = await restore_user_endpoint(
                user_id=target_user["id"],
                restore_data=UserRestoreData(reason="test restore"),
                current_user=current_user,
                building_id=building_id,
            )

        return result, update_calls

    # ── 1. Applies pending full_name ──────────────────────────────────────────

    async def test_restore_applies_pending_full_name(self):
        """If pending_return_details has full_name, it is applied to the $set on restore."""
        pending = {"full_name": "New Full Name", "unit_number": "UA010"}
        target = self._make_archived_target(pending=pending)

        _, update_calls = await self._invoke_restore(target)

        set_payload = update_calls[0][1]["$set"]
        self.assertEqual(set_payload.get("full_name"), "New Full Name",
                         "pending full_name must be applied to the user document on restore")

    # ── 2. Applies pending phone ──────────────────────────────────────────────

    async def test_restore_applies_pending_phone(self):
        """If pending_return_details has phone, it is applied to the $set on restore."""
        pending = {"phone": "0499888777", "unit_number": "UA010"}
        target = self._make_archived_target(pending=pending)

        _, update_calls = await self._invoke_restore(target)

        set_payload = update_calls[0][1]["$set"]
        self.assertEqual(set_payload.get("phone"), "0499888777",
                         "pending phone must be applied to the user document on restore")

    # ── 3. Applies pending end_date ───────────────────────────────────────────

    async def test_restore_applies_pending_end_date(self):
        """If pending_return_details has end_date, it is applied to the $set on restore."""
        pending = {"end_date": "2027-12-31", "unit_number": "UA010"}
        target = self._make_archived_target(pending=pending)

        _, update_calls = await self._invoke_restore(target)

        set_payload = update_calls[0][1]["$set"]
        self.assertEqual(set_payload.get("end_date"), "2027-12-31",
                         "pending end_date must be applied to the user document on restore")

    # ── 3b. Empty strings in pending_return_details are NOT applied ───────────

    async def test_restore_ignores_empty_phone_in_pending_details(self):
        """An empty-string phone in pending_return_details must NOT overwrite the stored value."""
        pending = {"phone": "", "full_name": "", "unit_number": "UA010"}
        target = self._make_archived_target(pending=pending)
        target["phone"] = "0400111222"  # existing stored value

        _, update_calls = await self._invoke_restore(target)

        set_payload = update_calls[0][1]["$set"]
        self.assertNotIn("phone", set_payload,
                         "empty phone in pending_return_details must not appear in $set")
        self.assertNotIn("full_name", set_payload,
                         "empty full_name in pending_return_details must not appear in $set")

    async def test_restore_ignores_empty_end_date_in_pending_details(self):
        """An empty-string end_date in pending_return_details must NOT be applied."""
        pending = {"end_date": "", "unit_number": "UA010"}
        target = self._make_archived_target(pending=pending)

        _, update_calls = await self._invoke_restore(target)

        set_payload = update_calls[0][1]["$set"]
        self.assertNotIn("end_date", set_payload,
                         "empty end_date in pending_return_details must not appear in $set")

    # ── 4. $unset pending_return_details after restore ────────────────────────

    async def test_restore_unsets_pending_return_details(self):
        """After restore, pending_return_details must be $unset on the user document."""
        pending = {"full_name": "Updated Name", "unit_number": "UA010"}
        target = self._make_archived_target(pending=pending)

        _, update_calls = await self._invoke_restore(target)

        unset_payload = update_calls[0][1].get("$unset", {})
        self.assertIn("pending_return_details", unset_payload,
                      "$unset must include pending_return_details to clear it after restore")

    # ── 5. Restore works without pending_return_details ───────────────────────

    async def test_restore_without_pending_details(self):
        """Restore works normally when no pending_return_details is set."""
        target = self._make_archived_target(pending=None)

        result, update_calls = await self._invoke_restore(target)

        self.assertIn("message", result, "restore must return a success message")
        self.assertTrue(update_calls, "db.users.update_one must be called")
        set_payload = update_calls[0][1]["$set"]
        self.assertEqual(set_payload.get("status"), "active",
                         "user must be restored to active status")

    # ── 6. 400 for non-archived user ──────────────────────────────────────────

    async def test_restore_rejects_fully_active_user(self):
        """Restore must return 400 if user.status is not 'archived' AND they have no inactive units."""
        from fastapi import HTTPException
        target = {
            "id": "user-active-001",
            "full_name": "Active User",
            "email": "active@example.com",
            "role": "owner",
            "status": "active",
            "unit_number": "UA010",
            "building_id": "13195",
            "is_test_data": True,
        }
        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"):
            mock_db.users.find_one = AsyncMock(return_value=target)
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            # No inactive units → user is fully active → reject
            mock_db.user_units.find_one = AsyncMock(return_value=None)

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id=target["id"],
                    restore_data=UserRestoreData(),
                    current_user=current_user,
                    building_id="13195",
                )

        self.assertEqual(ctx.exception.status_code, 400,
                         "fully active user with no inactive units must return 400")

    async def test_restore_accepts_active_user_with_inactive_units(self):
        """Restore must succeed for status='active' users whose user_units are inactive (appear in archived tab)."""
        target = {
            "id": "user-active-002",
            "full_name": "Deactivated Unit User",
            "email": "deactivated@example.com",
            "role": "owner",
            "status": "active",
            "unit_number": "UA020",
            "building_id": "13195",
            "is_test_data": True,
        }
        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"), \
                patch("server.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            mock_db.users.find_one = AsyncMock(return_value=target)
            mock_db.users.update_one = AsyncMock()
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            # Inactive unit exists → user appears in archived tab → allow restore
            mock_db.user_units.find_one = AsyncMock(
                return_value={"user_id": target["id"], "is_active": False}
            )
            mock_db.user_units.update_many = AsyncMock()
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            result = await restore_user_endpoint(
                user_id=target["id"],
                restore_data=UserRestoreData(reason="unit deactivated, now restoring"),
                current_user=current_user,
                building_id="13195",
            )

        self.assertEqual(result["user_id"], target["id"])

    # ── 7. 403 for non-super_admin caller ─────────────────────────────────────

    async def test_restore_rejects_non_super_admin(self):
        """Only super_admin can call restore; other roles get 403."""
        from fastapi import HTTPException
        target = self._make_archived_target()
        chairman = {
            "id": "chair-001",
            "full_name": "EC Chairman",
            "email": "chairman@example.com",
            "role": "chairman",
            "building_id": "13195",
        }

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=chairman), \
                patch("server.get_current_building", return_value="13195"):
            mock_db.users.find_one = AsyncMock(return_value=target)

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id=target["id"],
                    restore_data=UserRestoreData(),
                    current_user=chairman,
                    building_id="13195",
                )

        self.assertEqual(ctx.exception.status_code, 403,
                         "non-super_admin caller must receive 403")

    # ── 7b. strata_manager and admin_staff are allowed ────────────────────────

    async def test_restore_allows_strata_manager(self):
        """strata_manager must be able to restore an archived user."""
        target = self._make_archived_target()
        strata_manager = {
            "id": "sm-001",
            "full_name": "Strata Manager",
            "email": "sm@example.com",
            "role": "strata_manager",
            "building_id": "13195",
        }
        result, _ = await self._invoke_restore(target, current_user=strata_manager)
        self.assertIn("user_id", result, "strata_manager must be allowed to restore archived users")

    async def test_restore_allows_admin_staff(self):
        """admin_staff must be able to restore an archived user."""
        target = self._make_archived_target()
        admin_staff = {
            "id": "as-001",
            "full_name": "Admin Staff",
            "email": "staff@example.com",
            "role": "admin_staff",
            "building_id": "13195",
        }
        result, _ = await self._invoke_restore(target, current_user=admin_staff)
        self.assertIn("user_id", result, "admin_staff must be allowed to restore archived users")

    async def test_restore_allows_owner_with_temp_elevation_to_strata_manager(self):
        """An owner whose effective_role is strata_manager (temp elevation) must be allowed to restore."""
        target = self._make_archived_target()
        elevated_owner = {
            "id": "owner-elev-001",
            "full_name": "Elevated Owner",
            "email": "elevated@example.com",
            "role": "owner",
            "effective_role": "strata_manager",  # temp elevation active
            "building_id": "13195",
        }
        result, _ = await self._invoke_restore(target, current_user=elevated_owner)
        self.assertIn("user_id", result,
                      "owner with effective_role=strata_manager must be allowed to restore via temp elevation")

    async def test_restore_rejects_ec_member(self):
        """ec_member is not in _RESTORE_ROLES and must receive 403."""
        from fastapi import HTTPException
        target = self._make_archived_target()
        ec_member = {
            "id": "ec-001",
            "full_name": "EC Member",
            "email": "ec@example.com",
            "role": "ec_member",
            "building_id": "13195",
        }

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=ec_member), \
                patch("server.get_current_building", return_value="13195"):
            mock_db.users.find_one = AsyncMock(return_value=target)

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id=target["id"],
                    restore_data=UserRestoreData(),
                    current_user=ec_member,
                    building_id="13195",
                )

        self.assertEqual(ctx.exception.status_code, 403,
                         "ec_member must not be allowed to restore archived users")

    # ── 7c. Cross-building association guard ──────────────────────────────────

    async def test_restore_rejects_archived_user_with_no_building_association(self):
        """Restoring an archived user into a building they never belonged to must return 403."""
        from fastapi import HTTPException
        target = self._make_archived_target()
        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="16244"):
            mock_db.users.find_one = AsyncMock(return_value=target)
            # No inactive OR active user_units in building 16244
            mock_db.user_units.find_one = AsyncMock(return_value=None)

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id=target["id"],
                    restore_data=UserRestoreData(),
                    current_user=current_user,
                    building_id="16244",
                )

        self.assertEqual(ctx.exception.status_code, 403,
                         "Restoring into a building with no prior association must return 403")

    # ── 8. 404 for unknown user ───────────────────────────────────────────────

    async def test_restore_returns_404_for_unknown_user(self):
        """Restore must return 404 when the user_id does not exist."""
        from fastapi import HTTPException
        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"):
            mock_db.users.find_one = AsyncMock(return_value=None)
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id="does-not-exist",
                    restore_data=UserRestoreData(),
                    current_user=current_user,
                    building_id="13195",
                )

        self.assertEqual(ctx.exception.status_code, 404,
                         "unknown user must return 404")

    # ── 9. Success response structure ─────────────────────────────────────────

    async def test_restore_returns_success_with_user_id(self):
        """Successful restore returns a dict with 'message' and 'user_id'."""
        target = self._make_archived_target()
        result, _ = await self._invoke_restore(target)

        self.assertIn("message", result)
        self.assertIn("user_id", result)
        self.assertEqual(result["user_id"], target["id"])


# ═════════════════════════════════════════════════════════════════════════════
# Class 4 — restore_user_endpoint: unit change handling
# ═════════════════════════════════════════════════════════════════════════════

class TestRestoreUnitChange(unittest.IsolatedAsyncioTestCase):
    """Tests for unit change / reactivation logic inside restore_user_endpoint()."""

    def _make_super_admin(self):
        return {
            "id": "sa-unit-change",
            "full_name": "Super Admin",
            "email": "admin@example.com",
            "role": "super_admin",
            "building_id": "13195",
        }

    def _make_archived_target(self, old_unit="UA010", pending_unit=None):
        user = {
            "id": "user-unit-001",
            "full_name": "Unit Change User",
            "email": "unitchange@example.com",
            "role": "owner",
            "status": "archived",
            "unit_number": old_unit,
            "building_id": "13195",
            "is_test_data": True,
        }
        if pending_unit is not None:
            user["pending_return_details"] = {"unit_number": pending_unit}
        return user

    async def _invoke_restore_unit(
            self,
            target_user,
            existing_user_units=None,
            new_unit_conflict=None,
            building_id="13195",
    ):
        """
        Invoke restore_user_endpoint() with configurable user_units state.

        existing_user_units: list of user_unit docs returned for pre-check (find with is_primary=True)
        new_unit_conflict: doc returned by find_one for the new-unit conflict check (None = no conflict)
        """
        current_user = self._make_super_admin()
        if existing_user_units is None:
            existing_user_units = []

        update_many_calls = []
        insert_one_calls = []

        find_one_calls = []

        async def _find_one_side_effect(filter_=None, *args, **kwargs):
            # Dispatch based on query content
            if filter_ is None:
                filter_ = {}
            if "id" in filter_ and filter_["id"] == target_user["id"]:
                return target_user
            # Conflict check for new unit
            if new_unit_conflict is not None:
                return new_unit_conflict
            return None

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value=building_id), \
                patch("server.create_audit_log", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:

            mock_asyncio.create_task = MagicMock()

            mock_db.users.find_one = AsyncMock(side_effect=_find_one_side_effect)
            mock_db.users.update_one = AsyncMock()

            # Pre-check find() for is_primary=True records
            mock_db.user_units.find = MagicMock(return_value=_make_cursor(existing_user_units))

            # Scope checks query is_active=False; conflict checks query is_active=True.
            # Return an inactive unit for scope checks so the building-association
            # guard passes, and delegate to new_unit_conflict for conflict checks.
            def _unit_fone_se(query, *_a, **_kw):
                if query.get("is_active") is False or "is_active" not in query:
                    return {"user_id": target_user["id"], "is_active": False,
                            "building_id": building_id}
                return new_unit_conflict

            mock_db.user_units.find_one = AsyncMock(side_effect=_unit_fone_se)
            mock_db.user_units.update_many = AsyncMock(
                side_effect=lambda *a, **kw: update_many_calls.append(a)
            )
            mock_db.user_units.insert_one = AsyncMock(
                side_effect=lambda doc: insert_one_calls.append(doc)
            )
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            result = await restore_user_endpoint(
                user_id=target_user["id"],
                restore_data=UserRestoreData(reason="unit change test"),
                current_user=current_user,
                building_id=building_id,
            )

        return result, update_many_calls, insert_one_calls

    # ── 1. Same unit — reactivates existing ───────────────────────────────────

    async def test_restore_same_unit_reactivates_existing(self):
        """If pending unit == current unit (or no pending unit), update_many reactivates existing records."""
        # pending_unit matches old_unit → else branch
        target = self._make_archived_target(old_unit="UA010", pending_unit="UA010")

        _, update_many_calls, insert_one_calls = await self._invoke_restore_unit(target)

        # Should call update_many to reactivate, NOT insert_one for a new unit
        self.assertTrue(update_many_calls,
                        "update_many must be called to reactivate existing user_units")
        # Check at least one call sets is_active=True (reactivation path)
        reactivate_calls = [
            c for c in update_many_calls
            if c[1].get("$set", {}).get("is_active") is True
        ]
        self.assertTrue(reactivate_calls,
                        "At least one update_many must set is_active=True for same-unit restore")
        self.assertEqual(len(insert_one_calls), 0,
                         "No new user_units record should be inserted for same-unit restore")

    # ── 2. New unit — deactivates old user_units ──────────────────────────────

    async def test_restore_new_unit_deactivates_old(self):
        """If pending unit differs from current unit, old user_units are deactivated."""
        target = self._make_archived_target(old_unit="UA010", pending_unit="UA099")

        _, update_many_calls, _ = await self._invoke_restore_unit(
            target, new_unit_conflict=None
        )

        deactivate_calls = [
            c for c in update_many_calls
            if c[1].get("$set", {}).get("is_active") is False
        ]
        self.assertTrue(deactivate_calls,
                        "Old user_units must be deactivated (is_active=False) when unit changes")

    # ── 3. New unit — inserts new user_units record ───────────────────────────

    async def test_restore_new_unit_inserts_new_record(self):
        """A new user_units record is inserted for the new unit when unit changes."""
        target = self._make_archived_target(old_unit="UA010", pending_unit="UA099")

        _, _, insert_one_calls = await self._invoke_restore_unit(
            target, new_unit_conflict=None
        )

        self.assertEqual(len(insert_one_calls), 1,
                         "Exactly one new user_units record must be inserted for the new unit")
        new_rec = insert_one_calls[0]
        self.assertEqual(new_rec.get("unit_number"), "UA099",
                         "New user_units record must reference the pending unit number")
        self.assertEqual(new_rec.get("user_id"), target["id"])
        self.assertTrue(new_rec.get("is_active"), "New user_units record must be active")
        self.assertTrue(new_rec.get("is_primary"), "New user_units record must be primary")
        self.assertEqual(new_rec.get("building_id"), "13195")

    # ── 4. New unit conflict — raises 409 ─────────────────────────────────────

    async def test_restore_new_unit_conflict_raises_409(self):
        """If new unit already has an active primary occupant of same role, raise 409."""
        from fastapi import HTTPException
        target = self._make_archived_target(old_unit="UA010", pending_unit="UA099")
        conflict_doc = {
            "id": "uu-conflict-001",
            "user_id": "other-user-001",
            "unit_number": "UA099",
            "role_at_unit": "owner",
            "is_active": True,
            "is_primary": True,
            "building_id": "13195",
            "is_test_data": True,
        }

        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"), \
                patch("server.create_audit_log", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            mock_db.users.find_one = AsyncMock(side_effect=lambda f, *a, **kw: (
                target if f.get("id") == target["id"] else {"full_name": "Conflict User"}
            ))
            mock_db.users.update_one = AsyncMock()
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            mock_db.user_units.find_one = AsyncMock(return_value=conflict_doc)
            mock_db.user_units.update_many = AsyncMock()
            mock_db.user_units.insert_one = AsyncMock()
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id=target["id"],
                    restore_data=UserRestoreData(),
                    current_user=current_user,
                    building_id="13195",
                )

        self.assertEqual(ctx.exception.status_code, 409,
                         "Unit conflict on new unit must raise 409")

    # ── 5. Conflict message mentions unit and role ─────────────────────────────

    async def test_restore_new_unit_conflict_message_includes_unit_and_role(self):
        """409 detail message for unit conflict must mention the unit number and role."""
        from fastapi import HTTPException
        target = self._make_archived_target(old_unit="UA010", pending_unit="UA099")
        conflict_doc = {
            "id": "uu-conflict-002",
            "user_id": "other-user-002",
            "unit_number": "UA099",
            "role_at_unit": "owner",
            "is_active": True,
            "is_primary": True,
            "building_id": "13195",
            "is_test_data": True,
        }

        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"), \
                patch("server.create_audit_log", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            mock_db.users.find_one = AsyncMock(side_effect=lambda f, *a, **kw: (
                target if f.get("id") == target["id"] else {"full_name": "Existing Owner"}
            ))
            mock_db.users.update_one = AsyncMock()
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            mock_db.user_units.find_one = AsyncMock(return_value=conflict_doc)
            mock_db.user_units.update_many = AsyncMock()
            mock_db.user_units.insert_one = AsyncMock()
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            with self.assertRaises(HTTPException) as ctx:
                await restore_user_endpoint(
                    user_id=target["id"],
                    restore_data=UserRestoreData(),
                    current_user=current_user,
                    building_id="13195",
                )

        detail = ctx.exception.detail
        self.assertIsInstance(detail, str, "409 detail should be a string message")
        self.assertIn("UA099", detail, "Conflict message must mention the contested unit number")
        self.assertIn("owner", detail, "Conflict message must mention the contested role")

    # ── 6. No pending unit — reactivates existing ─────────────────────────────

    async def test_restore_no_pending_unit_reactivates_existing(self):
        """If no pending unit is set, existing user_units are reactivated (no insert)."""
        target = self._make_archived_target(old_unit="UA010", pending_unit=None)
        # No pending_return_details at all (key may already be absent; pop is safe either way)
        target.pop("pending_return_details", None)

        _, update_many_calls, insert_one_calls = await self._invoke_restore_unit(target)

        reactivate_calls = [
            c for c in update_many_calls
            if c[1].get("$set", {}).get("is_active") is True
        ]
        self.assertTrue(reactivate_calls,
                        "Existing user_units must be reactivated when no pending unit is set")
        self.assertEqual(len(insert_one_calls), 0,
                         "No new user_units record must be inserted when there is no pending unit change")

    # ── 7. Restored user is set to active status ──────────────────────────────

    async def test_restore_sets_user_status_to_active(self):
        """After restore, the user document $set must include status=active."""
        target = self._make_archived_target(old_unit="UA010")
        target.pop("pending_return_details", None)

        update_calls = []

        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"), \
                patch("server.create_audit_log", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            mock_db.users.find_one = AsyncMock(return_value=target)
            mock_db.users.update_one = AsyncMock(
                side_effect=lambda *a, **kw: update_calls.append(a)
            )
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            mock_db.user_units.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
                {"user_id": target["id"], "is_active": False, "building_id": "13195"}
                if q.get("is_active") is False or "is_active" not in q else None
            ))
            mock_db.user_units.update_many = AsyncMock()
            mock_db.user_units.insert_one = AsyncMock()
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            await restore_user_endpoint(
                user_id=target["id"],
                restore_data=UserRestoreData(reason="status check test"),
                current_user=current_user,
                building_id="13195",
            )

        self.assertTrue(update_calls, "db.users.update_one must be called")
        set_payload = update_calls[0][1]["$set"]
        self.assertEqual(set_payload.get("status"), "active",
                         "Restored user must have status=active")
        self.assertTrue(set_payload.get("is_active"),
                        "Restored user must have is_active=True")
        self.assertTrue(set_payload.get("is_approved"),
                        "Restored user must have is_approved=True")

    # ── 8. Multi-tenant: user_units scoped to correct building ────────────────

    async def test_restore_user_units_scoped_to_building(self):
        """user_units operations during restore must be scoped to the correct building_id."""
        target = self._make_archived_target(old_unit="UA010", pending_unit="UA020")

        update_many_calls = []
        insert_one_calls = []
        current_user = self._make_super_admin()

        with patch("server.db") as mock_db, \
                patch("server.get_current_user", return_value=current_user), \
                patch("server.get_current_building", return_value="13195"), \
                patch("server.create_audit_log", new_callable=AsyncMock), \
                patch("server.asyncio") as mock_asyncio:

            mock_asyncio.create_task = MagicMock()
            mock_db.users.find_one = AsyncMock(return_value=target)
            mock_db.users.update_one = AsyncMock()
            mock_db.user_units.find = MagicMock(return_value=_make_cursor([]))
            mock_db.user_units.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
                {"user_id": target["id"], "is_active": False, "building_id": "13195"}
                if q.get("is_active") is False or "is_active" not in q else None
            ))
            mock_db.user_units.update_many = AsyncMock(
                side_effect=lambda *a, **kw: update_many_calls.append(a)
            )
            mock_db.user_units.insert_one = AsyncMock(
                side_effect=lambda doc: insert_one_calls.append(doc)
            )
            mock_db.memberships.update_one = AsyncMock()

            from server import restore_user_endpoint, UserRestoreData
            await restore_user_endpoint(
                user_id=target["id"],
                restore_data=UserRestoreData(),
                current_user=current_user,
                building_id="13195",
            )

        # Check that update_many filter includes building_id
        for call_args in update_many_calls:
            filter_arg = call_args[0]
            self.assertEqual(filter_arg.get("building_id"), "13195",
                             "user_units update_many must be scoped to building_id=13195")

        # Check that insert_one doc includes correct building_id
        for doc in insert_one_calls:
            self.assertEqual(doc.get("building_id"), "13195",
                             "new user_units record must have building_id=13195")


# ─────────────────────────────────────────────────────────────────────────────


def teardown_module(module):
    """Restore event loop after IsolatedAsyncioTestCase teardown."""
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    unittest.main(verbosity=2)
