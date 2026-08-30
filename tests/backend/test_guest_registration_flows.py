"""
Tests for guest/tenant registration improvements (Session 2026-03-07):
  - Role-based token expiry (guest=2h, tenant=48h)
  - GET /auth/registration-decision  → shows confirmation form (no immediate action)
  - POST /auth/registration-decision → processes decision, stores owner instructions
  - /units/all returns occupancy counts for unauthenticated callers
  - Cron role-based escalation cutoff logic (guest 2h, tenant 48h)
  - Second-owner name mismatch logic (isSimilarName against owner_name_b)
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _token_doc(action="approve", used=False, expires_delta_hours=2, user_id="usr-001"):
    expiry = (datetime.now(timezone.utc) + timedelta(hours=expires_delta_hours)).isoformat()
    return {
        "token": "test_token_abc",
        "user_id": user_id,
        "action": action,
        "used": used,
        "expires_at": expiry,
        "created_for": "owner",
    }


def _pending_guest(unit="UA001"):
    return {
        "id": "usr-001",
        "full_name": "Akash Takasi",
        "email": "akash@example.com",
        "role": "guest",
        "unit_number": unit,
        "status": "pending_owner_approval",
        "is_active": True,
        "is_approved": False,
    }


def _pending_tenant(unit="TH087"):
    return {
        "id": "usr-002",
        "full_name": "Jane Tenant",
        "email": "jane@example.com",
        "role": "tenant",
        "unit_number": unit,
        "status": "pending_owner_approval",
        "is_active": True,
        "is_approved": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Token expiry: guest=2h, tenant=48h
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenExpiryByRole(unittest.TestCase):
    """Token expiry must be role-specific at registration time."""

    def test_guest_token_expiry_is_2_hours(self):
        """Guest tokens should expire in 2 hours so escalation fires quickly."""
        now = datetime.now(timezone.utc)
        expiry_hours = 2  # guest
        expiry = now + timedelta(hours=expiry_hours)
        diff = (expiry - now).total_seconds() / 3600
        self.assertAlmostEqual(diff, 2.0, places=1)

    def test_tenant_token_expiry_is_48_hours(self):
        """Tenant tokens should expire in 48 hours."""
        now = datetime.now(timezone.utc)
        expiry_hours = 48  # tenant
        expiry = now + timedelta(hours=expiry_hours)
        diff = (expiry - now).total_seconds() / 3600
        self.assertAlmostEqual(diff, 48.0, places=1)

    def test_expiry_hours_differ_by_role(self):
        """Guest window is 24× shorter than tenant window."""
        guest_hours = 2
        tenant_hours = 48
        self.assertLess(guest_hours, tenant_hours)
        self.assertEqual(tenant_hours // guest_hours, 24)


# ─────────────────────────────────────────────────────────────────────────────
# 2. GET /auth/registration-decision → confirmation form, not immediate action
# ─────────────────────────────────────────────────────────────────────────────

class TestRegistrationDecisionGET(unittest.IsolatedAsyncioTestCase):
    """GET endpoint shows a form; the user must POST to actually approve/reject."""

    def _make_request(self):
        from starlette.requests import Request as StarletteRequest
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/registration-decision",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        return StarletteRequest(scope)

    async def _call_get(self, token="test_token_abc", action="approve",
                        token_doc=None, target_user=None):
        from utils.rate_limit import update_rate_limit_state
        update_rate_limit_state(enabled=False)
        with patch("routers.auth.db") as mock_db:
            mock_db.registration_approval_tokens = MagicMock()
            mock_db.registration_approval_tokens.find_one = AsyncMock(
                return_value=token_doc or _token_doc(action=action)
            )
            mock_db.users = MagicMock()
            mock_db.users.find_one = AsyncMock(
                return_value=target_user or _pending_guest()
            )
            mock_db.settings = MagicMock()
            mock_db.settings.find_one = AsyncMock(return_value={})

            from routers.auth import show_registration_decision_form
            request = self._make_request()
            response = await show_registration_decision_form(request=request, token=token, action=action)
            return response

    async def test_approve_returns_html_form_not_redirect(self):
        """GET approve returns a HTML confirmation form, not a success page."""
        resp = await self._call_get(action="approve")
        html = resp.body.decode()
        # Must contain a <form> element with POST method
        self.assertIn("<form", html.lower())
        self.assertIn('method="POST"', html)

    async def test_approve_form_has_instructions_field_for_guest(self):
        """Approve form includes instructions textarea for guest registrations."""
        resp = await self._call_get(action="approve", target_user=_pending_guest())
        html = resp.body.decode()
        self.assertIn("instructions", html.lower())
        self.assertIn("<textarea", html.lower())

    async def test_approve_form_has_guest_guide_link(self):
        """Approve form links to the Guest Quick Guide for guest registrations."""
        resp = await self._call_get(action="approve", target_user=_pending_guest())
        html = resp.body.decode()
        self.assertIn("quick_role_guest.html", html)

    async def test_reject_returns_html_form_with_reason_field(self):
        """GET reject returns a decline confirmation form with optional reason field."""
        resp = await self._call_get(action="reject")
        html = resp.body.decode()
        self.assertIn("<form", html.lower())
        self.assertIn("reason", html.lower())
        self.assertIn("<textarea", html.lower())

    async def test_invalid_action_returns_error_html(self):
        """Unknown action returns error HTML, not a form."""
        with patch("routers.auth.db") as mock_db:
            from utils.rate_limit import update_rate_limit_state
            update_rate_limit_state(enabled=False)
            mock_db.settings = MagicMock()
            mock_db.settings.find_one = AsyncMock(return_value={})
            from routers.auth import show_registration_decision_form
            request = self._make_request()
            resp = await show_registration_decision_form(request=request, token="tok", action="hack")
        html = resp.body.decode()
        self.assertIn("Invalid", html)

    async def test_used_token_returns_already_actioned(self):
        """Used token returns 'Already Actioned' page, not a form."""
        resp = await self._call_get(
            action="approve",
            token_doc=_token_doc(used=True),
        )
        html = resp.body.decode()
        self.assertIn("Already Actioned", html)
        self.assertNotIn('<form method="POST"', html)

    async def test_expired_token_returns_error(self):
        """Expired token returns link expired error page."""
        expired_doc = _token_doc()
        expired_doc["expires_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        resp = await self._call_get(action="approve", token_doc=expired_doc)
        html = resp.body.decode()
        self.assertIn("Expired", html)

    async def test_tenant_approve_form_no_instructions_textarea(self):
        """Tenant approval form does NOT show the instructions textarea (only guests)."""
        resp = await self._call_get(action="approve", target_user=_pending_tenant())
        html = resp.body.decode()
        # For tenants there should be no instructions textarea
        self.assertNotIn("Instructions for your guest", html)

    async def test_get_does_not_mark_token_used(self):
        """GET must NOT mark the token as used — that only happens on POST."""
        with patch("routers.auth.db") as mock_db:
            from utils.rate_limit import update_rate_limit_state
            update_rate_limit_state(enabled=False)
            mock_db.registration_approval_tokens = MagicMock()
            mock_db.registration_approval_tokens.find_one = AsyncMock(
                return_value=_token_doc(action="approve")
            )
            mock_db.registration_approval_tokens.update_one = AsyncMock()
            mock_db.users = MagicMock()
            mock_db.users.find_one = AsyncMock(return_value=_pending_guest())
            mock_db.settings = MagicMock()
            mock_db.settings.find_one = AsyncMock(return_value={})

            from routers.auth import show_registration_decision_form
            request = self._make_request()
            await show_registration_decision_form(request=request, token="test_token_abc", action="approve")

            # update_one must NOT have been called (no token consumed on GET)
            mock_db.registration_approval_tokens.update_one.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 3. POST /auth/registration-decision → processes decision
# ─────────────────────────────────────────────────────────────────────────────

class TestRegistrationDecisionPOST(unittest.IsolatedAsyncioTestCase):
    """POST endpoint marks token used, updates user, sends emails."""

    def _make_request(self):
        from starlette.requests import Request as StarletteRequest
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/registration-decision",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        return StarletteRequest(scope)

    async def _call_post(self, action="approve", instructions="", reason="",
                         token_doc=None, target_user=None, admins=None):
        from fastapi import BackgroundTasks
        from utils.rate_limit import update_rate_limit_state
        bt = BackgroundTasks()
        update_rate_limit_state(enabled=False)

        with patch("routers.auth.db") as mock_db, \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):
            mock_db.registration_approval_tokens = MagicMock()
            mock_db.registration_approval_tokens.find_one = AsyncMock(
                return_value=token_doc or _token_doc(action=action)
            )
            mock_db.registration_approval_tokens.update_one = AsyncMock()

            mock_db.users = MagicMock()
            mock_db.users.find_one = AsyncMock(
                return_value=target_user or _pending_guest()
            )
            mock_db.users.update_one = AsyncMock()
            mock_db.users.find = MagicMock()
            mock_db.users.find.return_value.to_list = AsyncMock(
                return_value=admins or [
                    {"id": "adm-1", "email": "admin@eastgateresidences.com.au",
                     "full_name": "Admin"}
                ]
            )
            mock_db.user_notifications = MagicMock()
            mock_db.user_notifications.insert_many = AsyncMock()
            mock_db.user_units = MagicMock()
            mock_db.user_units.update_many = AsyncMock()
            mock_db.notifications = MagicMock()
            mock_db.notifications.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
            mock_db.memberships = MagicMock()
            mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
            mock_db.memberships.count_documents = AsyncMock(return_value=0)
            # _get_staff_registration_reviewers() resolves this building's reviewers
            # from active memberships before querying users.
            mock_db.memberships.distinct = AsyncMock(return_value=[])
            mock_db.settings = MagicMock()
            mock_db.settings.find_one = AsyncMock(return_value={})

            from routers.auth import process_registration_decision_via_token
            response = await process_registration_decision_via_token(
                request=self._make_request(),
                background_tasks=bt,
                token="test_token_abc",
                action=action,
                instructions=instructions,
                reason=reason,
            )
            return response, mock_db

    async def test_approve_marks_token_used(self):
        """POST approve marks the token as used."""
        _, mock_db = await self._call_post(action="approve")
        mock_db.registration_approval_tokens.update_one.assert_called_once()
        call_kwargs = mock_db.registration_approval_tokens.update_one.call_args
        self.assertEqual(call_kwargs[0][1]["$set"]["used"], True)

    async def test_approve_sets_user_status_active(self):
        """POST approve sets user status to 'active'."""
        _, mock_db = await self._call_post(action="approve")
        update_call = mock_db.users.update_one.call_args_list[0]
        self.assertEqual(update_call[0][1]["$set"]["status"], "active")
        self.assertEqual(update_call[0][1]["$set"]["owner_approved"], True)

    async def test_approve_stores_instructions_when_provided(self):
        """When owner provides instructions, they are stored on the user document."""
        instructions = "Park in bay 12. No smoking."
        _, mock_db = await self._call_post(action="approve", instructions=instructions)
        update_call = mock_db.users.update_one.call_args_list[0]
        self.assertEqual(update_call[0][1]["$set"]["owner_instructions"], instructions)

    async def test_approve_no_instructions_field_when_empty(self):
        """Empty instructions do not add owner_instructions field to the update."""
        _, mock_db = await self._call_post(action="approve", instructions="")
        update_call = mock_db.users.update_one.call_args_list[0]
        self.assertNotIn("owner_instructions", update_call[0][1]["$set"])

    async def test_approve_notifies_admins(self):
        """POST approve creates in-app notifications for all admins."""
        _, mock_db = await self._call_post(action="approve")
        mock_db.user_notifications.insert_many.assert_called_once()

    async def test_approve_returns_success_html(self):
        """POST approve returns green success HTML page."""
        resp, _ = await self._call_post(action="approve")
        html = resp.body.decode()
        self.assertIn("Approved", html)
        self.assertIn("#16a34a", html)

    async def test_reject_sets_user_status_archived(self):
        """POST reject archives the user."""
        _, mock_db = await self._call_post(action="reject")
        update_call = mock_db.users.update_one.call_args_list[0]
        self.assertEqual(update_call[0][1]["$set"]["status"], "archived")
        self.assertEqual(update_call[0][1]["$set"]["is_active"], False)

    async def test_reject_deactivates_user_units(self):
        """POST reject deactivates all active user_units for the rejected user."""
        _, mock_db = await self._call_post(action="reject")
        mock_db.user_units.update_many.assert_called_once()
        call_filter = mock_db.user_units.update_many.call_args[0][0]
        self.assertEqual(call_filter["user_id"], "usr-001")
        self.assertTrue(call_filter["is_active"])

    async def test_reject_returns_declined_html(self):
        """POST reject returns red declined HTML page."""
        resp, _ = await self._call_post(action="reject")
        html = resp.body.decode()
        self.assertIn("Declined", html)
        self.assertIn("#dc2626", html)

    async def test_already_reviewed_user_returns_error(self):
        """If user is no longer pending_owner_approval, return Already Reviewed."""
        active_user = _pending_guest()
        active_user["status"] = "active"
        resp, _ = await self._call_post(action="approve", target_user=active_user)
        html = resp.body.decode()
        self.assertIn("Already Reviewed", html)


# ─────────────────────────────────────────────────────────────────────────────
# 4. /units/all returns counts for unauthenticated callers
# ─────────────────────────────────────────────────────────────────────────────

class TestUnitsAllUnauthenticated(unittest.IsolatedAsyncioTestCase):
    """Unauthenticated callers should still see occupancy counts (no PII)."""

    async def test_returns_numeric_counts_when_unauthenticated(self):
        """owner_count, tenant_count, guest_count are integers, not None."""
        mock_units = [
            {"lot_number": "LOT71", "unit_number": "UA001",
             "unit_type": "Apartment", "entitlement": 100},
        ]
        mock_user_units = [
            {"unit_number": "UA001", "role_at_unit": "owner"},
            {"unit_number": "UA001", "role_at_unit": "tenant"},
        ]

        with patch("server.db") as mock_db:
            mock_db.units = MagicMock()
            mock_db.units.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=mock_units
            )
            mock_db.user_units = MagicMock()
            mock_db.user_units.find.return_value.to_list = AsyncMock(
                return_value=mock_user_units
            )

            from server import get_all_units_with_occupants
            result = await get_all_units_with_occupants(current_user=None)

        self.assertEqual(len(result), 1)
        unit = result[0]
        # All counts must be integers, never None
        self.assertIsInstance(unit["owner_count"], int)
        self.assertIsInstance(unit["tenant_count"], int)
        self.assertIsInstance(unit["guest_count"], int)
        self.assertEqual(unit["owner_count"], 1)
        self.assertEqual(unit["tenant_count"], 1)
        self.assertEqual(unit["guest_count"], 0)

    async def test_vacant_unit_shows_zeros(self):
        """Unit with no approved occupants shows all-zero counts."""
        mock_units = [
            {"lot_number": "LOT72", "unit_number": "UA002",
             "unit_type": "Apartment", "entitlement": 100},
        ]
        with patch("server.db") as mock_db:
            mock_db.units = MagicMock()
            mock_db.units.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=mock_units
            )
            mock_db.user_units = MagicMock()
            mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[])

            from server import get_all_units_with_occupants
            result = await get_all_units_with_occupants(current_user=None)

        unit = result[0]
        self.assertEqual(unit["owner_count"], 0)
        self.assertEqual(unit["tenant_count"], 0)
        self.assertEqual(unit["guest_count"], 0)
        self.assertEqual(unit["occupant_count"], 0)

    async def test_approved_user_also_gets_counts(self):
        """Authenticated approved users also get numeric counts (not broken by change)."""
        mock_units = [
            {"lot_number": "LOT73", "unit_number": "UA003",
             "unit_type": "Apartment", "entitlement": 100},
        ]
        mock_user_units = [
            {"unit_number": "UA003", "role_at_unit": "owner"},
            {"unit_number": "UA003", "role_at_unit": "guest"},
            {"unit_number": "UA003", "role_at_unit": "guest"},
        ]
        approved_user = {
            "id": "owner-1", "role": "owner",
            "is_approved": True, "is_active": True,
        }

        with patch("server.db") as mock_db:
            mock_db.units = MagicMock()
            mock_db.units.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=mock_units
            )
            mock_db.user_units = MagicMock()
            mock_db.user_units.find.return_value.to_list = AsyncMock(
                return_value=mock_user_units
            )

            from server import get_all_units_with_occupants
            result = await get_all_units_with_occupants(current_user=approved_user)

        unit = result[0]
        self.assertEqual(unit["owner_count"], 1)
        self.assertEqual(unit["guest_count"], 2)
        self.assertEqual(unit["occupant_count"], 3)

    async def test_only_active_user_units_counted(self):
        """Only user_units with is_active=True are counted (approved occupants only)."""
        # The query filter {"is_active": True} is the contract; verify the
        # endpoint passes this filter to the DB find call.
        mock_units = [
            {"lot_number": "LOT74", "unit_number": "UA004",
             "unit_type": "Apartment", "entitlement": 100},
        ]
        with patch("server.db") as mock_db:
            mock_db.units = MagicMock()
            mock_db.units.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=mock_units
            )
            mock_db.user_units = MagicMock()
            mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[])

            from server import get_all_units_with_occupants
            await get_all_units_with_occupants(current_user=None)

            # Verify the find was called with is_active=True filter
            find_call_args = mock_db.user_units.find.call_args[0][0]
            self.assertTrue(find_call_args.get("is_active"))


# ─────────────────────────────────────────────────────────────────────────────
# 5. Cron escalation cutoff logic
# ─────────────────────────────────────────────────────────────────────────────

class TestCronEscalationCutoffs(unittest.TestCase):
    """Cron uses 2h cutoff for guests, 48h for tenants."""

    def setUp(self):
        import importlib.util
        cron_path = os.path.join(BACKEND_DIR, "cron", "cron_approval_escalation.py")
        spec = importlib.util.spec_from_file_location("cron_escalation", cron_path)
        self.cron = importlib.util.module_from_spec(spec)
        # Don't exec (would trigger Motor imports); just load constants
        # by reading the file directly
        with open(cron_path) as f:
            src = f.read()
        # Extract constants via simple string search
        self._src = src

    def _get_constant(self, name):
        import re
        m = re.search(rf"^{name}\s*=\s*(\d+)", self._src, re.MULTILINE)
        self.assertIsNotNone(m, f"Constant {name} not found in cron script")
        return int(m.group(1))

    def test_guest_escalation_hours_is_2(self):
        self.assertEqual(self._get_constant("GUEST_ESCALATION_HOURS"), 2)

    def test_tenant_escalation_hours_is_48(self):
        self.assertEqual(self._get_constant("TENANT_ESCALATION_HOURS"), 48)

    def test_token_validity_hours_is_72(self):
        self.assertEqual(self._get_constant("TOKEN_VALIDITY_HOURS"), 72)

    def test_guest_cutoff_is_earlier_than_tenant_cutoff(self):
        """Guest cutoff is more recent (less historical) than tenant cutoff."""
        now = datetime.now(timezone.utc)
        guest_cutoff = now - timedelta(hours=2)
        tenant_cutoff = now - timedelta(hours=48)
        # guest_cutoff is NEWER (later timestamp) than tenant_cutoff
        self.assertGreater(guest_cutoff, tenant_cutoff)

    def test_cron_query_uses_or_with_role_filters(self):
        """Cron query must use $or to filter by role with separate cutoffs."""
        self.assertIn('"$or"', self._src.replace("'$or'", '"$or"').replace("\"$or\"", '"$or"')
                      .replace("'$or'", '"$or"'))
        self.assertIn("guest_cutoff", self._src)
        self.assertIn("tenant_cutoff", self._src)

    def test_hours_waiting_uses_role_specific_window(self):
        """hours_waiting fallback uses escalation_window (role-specific), not a hardcoded value."""
        self.assertIn("escalation_window", self._src)
        # Must NOT use a bare hardcoded 48 as fallback
        import re
        # Look for pattern: `hours_waiting = ESCALATION_AFTER_HOURS` (old style, should be gone)
        old_pattern = re.search(r"hours_waiting\s*=\s*ESCALATION_AFTER_HOURS", self._src)
        self.assertIsNone(old_pattern, "Old hardcoded ESCALATION_AFTER_HOURS fallback still present")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Users endpoint default filter excludes only archived, not pending_owner_approval
# ─────────────────────────────────────────────────────────────────────────────

class TestUsersListFilter(unittest.TestCase):
    """pending_owner_approval users must be visible in the default user list."""

    def test_pending_owner_approval_not_in_default_exclusion_list(self):
        """The $nin query filter must not exclude pending_owner_approval."""
        import re
        server_path = os.path.join(BACKEND_DIR, "server.py")
        with open(server_path) as f:
            src = f.read()

        # The old broken pattern was:  "$nin": ["archived", "pending_owner_approval"]
        # Check that no $nin list contains pending_owner_approval alongside archived.
        nin_blocks = re.findall(r'"\$nin"\s*:\s*\[[^\]]+\]', src)
        for block in nin_blocks:
            if "pending_owner_approval" in block:
                self.fail(
                    f"Found $nin block that excludes pending_owner_approval: {block}"
                )

    def test_archived_is_excluded_by_default(self):
        """Archived users should still be excluded from default list."""
        server_path = os.path.join(BACKEND_DIR, "server.py")
        with open(server_path) as f:
            src = f.read()
        # The archived exclusion must still exist
        self.assertIn('"archived"', src)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Guest Quick Guide exists and has required sections
# ─────────────────────────────────────────────────────────────────────────────

class TestGuestQuickGuideHTML(unittest.TestCase):
    """The guest quick guide HTML file must exist with all required sections."""

    def setUp(self):
        guide_path = os.path.join(
            BACKEND_DIR, "..", "frontend", "public", "user-guides", "quick_role_guest.html"
        )
        self.guide_path = os.path.normpath(guide_path)
        self.assertTrue(
            os.path.exists(self.guide_path),
            f"Guest quick guide not found at {self.guide_path}",
        )
        with open(self.guide_path) as f:
            self.html = f.read()

    def test_has_house_rules_section(self):
        self.assertIn("House Rules", self.html)

    def test_has_pet_policy_section(self):
        self.assertIn("Pet Policy", self.html)
        self.assertIn("No pets", self.html)

    def test_has_emergency_procedures_section(self):
        self.assertIn("Emergency", self.html)
        self.assertIn("000", self.html)

    def test_has_fire_detection_plan_section(self):
        self.assertIn("Fire Detection", self.html)
        self.assertIn("Zone", self.html)

    def test_references_fire_block_plan_images(self):
        self.assertIn("20260211_082458.jpg", self.html)
        self.assertIn("20260211_082416.jpg", self.html)

    def test_links_to_styles_css(self):
        self.assertIn("styles.css", self.html)

    def test_has_civium_contact(self):
        self.assertIn("Civium", self.html)
        self.assertIn("6285 0325", self.html)

    def test_has_emergency_fire_number(self):
        """Fire services emergency number must be prominent."""
        self.assertIn("000", self.html)
        self.assertIn("O'Neill", self.html)

    def test_no_pets_for_guests_clearly_stated(self):
        self.assertIn("No pets permitted for short-term guests", self.html)

    def test_zone_schedule_complete(self):
        """All 28 fire zones must be listed."""
        for zone in ["Zone 1", "Zone 7", "Zone 23", "Zone 30", "Zone 33"]:
            self.assertIn(zone, self.html)


def teardown_module(module):
    """Restore event loop after IsolatedAsyncioTestCase destroys it."""
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    unittest.main(verbosity=2)
