"""
Tests for guest/tenant registration and approval flows:
- Login endpoint present and working
- Admin approval sends notifications
- BCC email in registration notifications
- Auto-approve cron logic
"""
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────
# 1. Login endpoint
# ──────────────────────────────────────────────────────────────────
class TestLoginEndpoint:
    """Test that /api/auth/login exists and works correctly."""

    @pytest.mark.asyncio
    async def test_login_endpoint_exists(self):
        """Login endpoint must be registered in server.py api_router."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../backend'))
        from server import app
        routes = [r.path for r in app.routes]
        assert any('/api/auth/login' in r for r in routes), \
            "POST /api/auth/login must be registered"

    def test_login_live_valid_credentials(self):
        """Live test: valid credentials return a token."""
        import requests
        r = requests.post(
            'http://127.0.0.1:8003/api/auth/login',
            json={'email': 'administrator@eastgateresidences.com.au', 'password': os.environ.get("E2E_ADMIN_PASSWORD", "")},
            timeout=10
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert 'token' in data, "Response must contain 'token'"
        assert 'user' in data, "Response must contain 'user'"
        assert data['user']['role'] == 'super_admin'

    def test_login_live_wrong_password(self):
        """Live test: wrong password returns 401."""
        import requests
        r = requests.post(
            'http://127.0.0.1:8003/api/auth/login',
            json={'email': 'administrator@eastgateresidences.com.au', 'password': 'wrongpassword'},
            timeout=10
        )
        assert r.status_code == 401

    def test_login_live_unknown_email(self):
        """Live test: unknown email returns 401."""
        import requests
        r = requests.post(
            'http://127.0.0.1:8003/api/auth/login',
            json={'email': 'nobody@example.com', 'password': 'password'},
            timeout=10
        )
        assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# 2. BCC email constant in auth.py
# ──────────────────────────────────────────────────────────────────
class TestBccEmailConstant:
    """Ensure NOTIFY_CC_EMAIL was renamed to NOTIFY_BCC_EMAIL."""

    def test_notify_bcc_email_defined(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../backend'))
        import importlib
        spec = importlib.util.spec_from_file_location(
            "routers.auth",
            os.path.join(os.path.dirname(__file__), '../../backend/routers/auth.py')
        )
        import importlib.util
        mod = importlib.util.module_from_spec(spec)
        # We can't execute the module (it needs FastAPI deps) but we can read the source
        src = open(os.path.join(os.path.dirname(__file__), '../../backend/routers/auth.py')).read()
        assert 'NOTIFY_BCC_EMAIL' in src, "NOTIFY_BCC_EMAIL must be defined"
        assert 'NOTIFY_CC_EMAIL' not in src, "Old NOTIFY_CC_EMAIL must be removed"
        assert 'NOTIFY_BCC_NAME' in src, "NOTIFY_BCC_NAME must be defined"
        assert 'Building Administrator' in src, "BCC name must be 'Building Administrator'"

    def test_no_cc_email_references(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), '../../backend/routers/auth.py')).read()
        # Should not have old NOTIFY_CC_EMAIL
        assert 'NOTIFY_CC_EMAIL' not in src

    def test_bcc_support_in_email_util(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), '../../backend/utils/email.py')).read()
        assert 'bcc_email' in src, "send_email_async must accept bcc_email parameter"
        assert 'bcc_name' in src, "send_email_async must accept bcc_name parameter"


# ──────────────────────────────────────────────────────────────────
# 3. Registration approval settings in SiteSettingsUpdate model
# ──────────────────────────────────────────────────────────────────
class TestSiteSettingsModel:
    """SiteSettingsUpdate must include registration/approval timing fields."""

    def test_approval_fields_in_model(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), '../../backend/server.py')).read()
        assert 'admin_auto_approve_minutes' in src
        assert 'guest_escalation_hours' in src
        assert 'tenant_escalation_hours' in src
        assert 'token_validity_hours' in src
        assert 'notify_bcc_email' in src
        assert 'notify_bcc_name' in src

    def test_approval_defaults_in_get_settings(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), '../../backend/server.py')).read()
        # Defaults should be set in get_site_settings
        assert '"admin_auto_approve_minutes", 15' in src
        assert '"guest_escalation_hours", 2' in src
        assert '"tenant_escalation_hours", 48' in src
        assert '"token_validity_hours", 72' in src


# ──────────────────────────────────────────────────────────────────
# 4. Auto-approve cron
# ──────────────────────────────────────────────────────────────────
class TestAutoApproveCron:
    """Unit tests for the admin auto-approve cron job."""

    def _make_candidate(self, uid='u1', role='guest', owner_approved_at=None):
        """Build a mock user that is an auto-approve candidate."""
        if owner_approved_at is None:
            # Default: approved 20 minutes ago (past 15-minute threshold)
            owner_approved_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        return {
            'id': uid,
            'email': f'{uid}@test.com',
            'full_name': 'Test User',
            'role': role,
            'unit_number': 'UA001',
            'is_approved': False,
            'is_active': True,
            'status': 'active',
            'owner_approved_at': owner_approved_at,
        }

    @pytest.mark.asyncio
    async def test_approves_eligible_candidate(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../backend'))
        # Import only the run() function
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'cron_admin_auto_approve',
            os.path.join(os.path.dirname(__file__), '../../backend/cron/cron_admin_auto_approve.py')
        )
        mod = importlib.util.module_from_spec(spec)

        candidate = self._make_candidate()

        mock_users_coll = MagicMock()
        mock_users_coll.find.return_value.to_list = AsyncMock(side_effect=[
            [candidate],  # candidates query
            [],  # admins query
        ])
        mock_users_coll.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        mock_user_units_coll = MagicMock()
        mock_user_units_coll.update_many = AsyncMock()

        mock_notifications_coll = MagicMock()
        mock_notifications_coll.insert_one = AsyncMock()

        mock_db = MagicMock()
        mock_db.users = mock_users_coll
        mock_db.user_units = mock_user_units_coll
        mock_db.user_notifications = mock_notifications_coll

        # Dynamically load and run
        spec.loader.exec_module(mod)
        with patch.object(mod, 'send_email', new=AsyncMock()):
            await mod.run(mock_db)

        # Should have called update_one to approve
        mock_users_coll.update_one.assert_called_once()
        call_args = mock_users_coll.update_one.call_args
        update_doc = call_args[0][1]['$set']
        assert update_doc['is_approved'] is True
        assert update_doc['status'] == 'active'
        assert update_doc['auto_approved'] is True

    @pytest.mark.asyncio
    async def test_no_candidates_skips(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../backend'))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'cron_admin_auto_approve2',
            os.path.join(os.path.dirname(__file__), '../../backend/cron/cron_admin_auto_approve.py')
        )
        mod = importlib.util.module_from_spec(spec)

        mock_users_coll = MagicMock()
        mock_users_coll.find.return_value.to_list = AsyncMock(return_value=[])
        mock_users_coll.update_one = AsyncMock()

        mock_db = MagicMock()
        mock_db.users = mock_users_coll

        spec.loader.exec_module(mod)
        with patch.object(mod, 'send_email', new=AsyncMock()):
            await mod.run(mock_db)

        # Should NOT have called update_one
        mock_users_coll.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_owner_approved_at_field(self):
        """Cron must query on owner_approved_at, not owner_decision_at."""
        import os
        src = open(os.path.join(os.path.dirname(__file__),
                                '../../backend/cron/cron_admin_auto_approve.py')).read()
        assert 'owner_approved_at' in src
        assert 'owner_decision_at' not in src


# ──────────────────────────────────────────────────────────────────
# 5. Admin approval triggers notifications
# ──────────────────────────────────────────────────────────────────
class TestAdminApprovalNotification:
    """When is_approved=True is set, notifications must be queued."""

    def test_approval_notification_code_exists(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), '../../backend/server.py')).read()
        assert 'Account Approved' in src, "Approval email subject must appear in server.py"
        assert 'strata_manager' in src, "Must notify strata_manager on approval"
        assert 'create_user_notification' in src, "Must use create_user_notification for bell"

    def test_live_settings_includes_approval_fields(self):
        """Live test: GET /api/settings returns approval timing fields."""
        import requests
        r = requests.get('http://127.0.0.1:8003/api/settings', timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert 'admin_auto_approve_minutes' in data
        assert data['admin_auto_approve_minutes'] == 15
        assert 'guest_escalation_hours' in data
        assert 'tenant_escalation_hours' in data
        assert 'token_validity_hours' in data

# Duplicate-email registration paths are tested in test_register_duplicate_email.py
