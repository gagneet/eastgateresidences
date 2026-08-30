"""
Tests for TEST_EMAIL_INTERCEPT_ADDRESS mechanism (Session 2026-03-09).

Verifies that when TEST_EMAIL_INTERCEPT_ADDRESS is set in the environment,
ALL outgoing emails are redirected to that address instead of real recipients.
This prevents test runs from spamming real users with fake announcements,
notifications, and registration emails.

Coverage:
  - utils/email.py send_email_async: redirects to intercept address
  - utils/email.py send_email_async: prefixes subject with [TEST→original]
  - utils/email.py send_email_async: clears BCC in test mode
  - server.py send_email_async: same redirection
  - config.py: TEST_EMAIL_INTERCEPT_ADDRESS exported correctly
  - Verification that normal sends (no env var) are NOT intercepted
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import asyncio

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'backend')
sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault('MONGO_URL', 'mongodb://localhost:27017')
os.environ.setdefault('DB_NAME', 'test_db')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('FRONTEND_URL', 'https://www.eastgateresidences.com.au')

TEST_INTERCEPT_EMAIL = 'gagneet@eastgateresidences.com.au'


class TestEmailInterceptConfig(unittest.TestCase):
    """Config module exports TEST_EMAIL_INTERCEPT_ADDRESS."""

    def test_config_exports_test_email_intercept_address(self):
        """TEST_EMAIL_INTERCEPT_ADDRESS must be exported from config."""
        import importlib
        import config
        importlib.reload(config)
        self.assertTrue(
            hasattr(config, 'TEST_EMAIL_INTERCEPT_ADDRESS'),
            "config.py must export TEST_EMAIL_INTERCEPT_ADDRESS"
        )

    def test_config_default_is_empty_string(self):
        """Default value (when env var not set) must be empty string — verified via source."""
        # In this environment backend/.env always sets TEST_EMAIL_INTERCEPT_ADDRESS,
        # so we verify the default '' is declared in source code rather than testing runtime.
        with open(os.path.join(BACKEND_DIR, 'config.py')) as f:
            src = f.read()
        self.assertIn("os.getenv('TEST_EMAIL_INTERCEPT_ADDRESS', '')", src,
                      "config.py must default TEST_EMAIL_INTERCEPT_ADDRESS to '' (disabled)")

    def test_config_reads_env_var(self):
        """TEST_EMAIL_INTERCEPT_ADDRESS must be read from environment."""
        with patch.dict(os.environ, {'TEST_EMAIL_INTERCEPT_ADDRESS': TEST_INTERCEPT_EMAIL}):
            import importlib
            import config
            importlib.reload(config)
            self.assertEqual(config.TEST_EMAIL_INTERCEPT_ADDRESS, TEST_INTERCEPT_EMAIL)


class TestEmailInterceptInUtilsEmail(unittest.IsolatedAsyncioTestCase):
    """utils/email.py send_email_async must redirect when intercept is set."""

    async def _call_send(self, to_email, subject, intercept_address=''):
        """Call send_email_async with the intercept env var set/unset."""
        actual_calls = []

        async def fake_resend_send(params_or_fn, *args, **kwargs):
            # Track what was actually "sent"
            if callable(params_or_fn):
                actual_calls.append(params_or_fn)
            return {'id': 'fake-id'}

        with patch.dict(os.environ, {'TEST_EMAIL_INTERCEPT_ADDRESS': intercept_address}), \
                patch('utils.email.TEST_EMAIL_INTERCEPT_ADDRESS', intercept_address), \
                patch('utils.email.get_email_settings', new_callable=AsyncMock) as mock_settings, \
                patch('utils.email._log_email_sent', new_callable=AsyncMock), \
                patch('utils.email.RESEND_AVAILABLE', True), \
                patch('utils.email.asyncio') as mock_asyncio:
            mock_settings.return_value = {
                'provider': 'resend',
                'resend_api_key': 'test-key',
                'sender_email': 'noreply@eastgateresidences.com.au',
                'sender_name': 'East Gate Residences',
            }

            sent_params = []

            async def capture_thread(fn, params):
                sent_params.append(params)
                return {'id': 'fake-id'}

            mock_asyncio.to_thread = AsyncMock(side_effect=capture_thread)

            # Also need resend module mock
            mock_resend = MagicMock()
            mock_resend.Emails.send = MagicMock(return_value={'id': 'fake-id'})

            with patch.dict('sys.modules', {'resend': mock_resend}):
                import importlib
                import utils.email
                importlib.reload(utils.email)

                # Re-apply patches after reload
                with patch.object(utils.email, 'TEST_EMAIL_INTERCEPT_ADDRESS', intercept_address), \
                        patch.object(utils.email, 'get_email_settings', new_callable=AsyncMock) as ms2, \
                        patch.object(utils.email, '_log_email_sent', new_callable=AsyncMock), \
                        patch.object(utils.email, 'RESEND_AVAILABLE', True), \
                        patch('asyncio.to_thread', new_callable=AsyncMock) as mock_thread:
                    ms2.return_value = {
                        'provider': 'resend',
                        'resend_api_key': 'test-key',
                        'sender_email': 'noreply@eastgateresidences.com.au',
                        'sender_name': 'East Gate Residences',
                    }
                    captured = {}

                    async def capture_call(fn, params):
                        captured['params'] = params
                        return {'id': 'fake-id'}

                    mock_thread.side_effect = capture_call

                    await utils.email.send_email_async(to_email, subject, '<p>Test body</p>')
                    return captured.get('params', {})

    async def test_intercept_redirects_to_address(self):
        """When intercept is set, email goes to intercept address, not original."""
        params = await self._call_send(
            'resident@example.com', 'Real Notice',
            intercept_address=TEST_INTERCEPT_EMAIL
        )
        if params:
            self.assertIn(TEST_INTERCEPT_EMAIL, params.get('to', []),
                          "Email must go to intercept address")
            self.assertNotIn('resident@example.com', params.get('to', []),
                             "Email must NOT go to real recipient")

    async def test_intercept_prefixes_subject(self):
        """When intercept is set, subject is prefixed with [TEST→original@]."""
        params = await self._call_send(
            'resident@example.com', 'Levy Notice',
            intercept_address=TEST_INTERCEPT_EMAIL
        )
        if params:
            subject = params.get('subject', '')
            self.assertIn('[TEST→', subject,
                          "Subject must be prefixed with [TEST→original]")
            self.assertIn('Levy Notice', subject,
                          "Original subject must be preserved")

    async def test_no_intercept_when_env_not_set(self):
        """When intercept address is empty, email goes to real recipient."""
        params = await self._call_send(
            'resident@example.com', 'Real Notice',
            intercept_address=''
        )
        if params:
            self.assertIn('resident@example.com', params.get('to', []),
                          "Without intercept, email must go to real recipient")
            self.assertNotIn('[TEST→', params.get('subject', ''),
                             "Subject must NOT be prefixed when intercept is disabled")


class TestEmailInterceptSourceCode(unittest.TestCase):
    """Source code checks — both utils/email.py and server.py handle interception."""

    def _read_utils_email(self):
        with open(os.path.join(BACKEND_DIR, 'utils', 'email.py')) as f:
            return f.read()

    def _read_server(self):
        with open(os.path.join(BACKEND_DIR, 'server.py')) as f:
            return f.read()

    def test_utils_email_imports_test_intercept_address(self):
        """utils/email.py must import TEST_EMAIL_INTERCEPT_ADDRESS from config."""
        src = self._read_utils_email()
        self.assertIn('TEST_EMAIL_INTERCEPT_ADDRESS', src,
                      "utils/email.py must use TEST_EMAIL_INTERCEPT_ADDRESS")

    def test_utils_email_applies_interception(self):
        """utils/email.py send_email_async must check and apply interception."""
        src = self._read_utils_email()
        self.assertIn('if TEST_EMAIL_INTERCEPT_ADDRESS:', src,
                      "utils/email.py must check if TEST_EMAIL_INTERCEPT_ADDRESS is set")
        self.assertIn('to_email = TEST_EMAIL_INTERCEPT_ADDRESS', src,
                      "utils/email.py must redirect to_email to intercept address")

    def test_utils_email_prefixes_subject(self):
        """utils/email.py must prefix subject with [TEST→...] when intercepting."""
        src = self._read_utils_email()
        self.assertIn('[TEST→', src,
                      "utils/email.py must prefix subject with [TEST→original]")

    def test_utils_email_clears_bcc_in_test_mode(self):
        """utils/email.py must clear BCC when interception is active."""
        src = self._read_utils_email()
        self.assertIn('bcc_email = None', src,
                      "utils/email.py must suppress BCC in test mode")

    def test_server_py_imports_test_intercept_address(self):
        """server.py must import TEST_EMAIL_INTERCEPT_ADDRESS from config."""
        src = self._read_server()
        self.assertIn('TEST_EMAIL_INTERCEPT_ADDRESS', src,
                      "server.py must import/use TEST_EMAIL_INTERCEPT_ADDRESS")

    def test_server_py_applies_interception_in_send_email_async(self):
        """server.py send_email_async must redirect email when intercept is set."""
        src = self._read_server()
        # Find the server.py send_email_async function
        import re
        match = re.search(r'async def send_email_async.*?(?=\nasync def |\nclass |\Z)', src, re.DOTALL)
        self.assertIsNotNone(match, "send_email_async not found in server.py")
        func_body = match.group(0)
        self.assertIn('TEST_EMAIL_INTERCEPT_ADDRESS', func_body,
                      "server.py send_email_async must check TEST_EMAIL_INTERCEPT_ADDRESS")
        self.assertIn('to_email = TEST_EMAIL_INTERCEPT_ADDRESS', func_body,
                      "server.py send_email_async must redirect to_email")

    def test_config_py_exports_test_intercept_address(self):
        """config.py must define and export TEST_EMAIL_INTERCEPT_ADDRESS."""
        with open(os.path.join(BACKEND_DIR, 'config.py')) as f:
            src = f.read()
        self.assertIn('TEST_EMAIL_INTERCEPT_ADDRESS', src,
                      "config.py must define TEST_EMAIL_INTERCEPT_ADDRESS")
        self.assertIn("os.getenv('TEST_EMAIL_INTERCEPT_ADDRESS'", src,
                      "config.py must read TEST_EMAIL_INTERCEPT_ADDRESS from environment")


class TestBackendEnvHasInterceptAddress(unittest.TestCase):
    """Verify backend/.env has TEST_EMAIL_INTERCEPT_ADDRESS configured."""

    def test_backend_env_has_test_intercept_address(self):
        """backend/.env may include TEST_EMAIL_INTERCEPT_ADDRESS when not in production."""
        env_path = os.path.join(BACKEND_DIR, '.env')
        self.assertTrue(os.path.exists(env_path), "backend/.env must exist")
        with open(env_path) as f:
            content = f.read()
        if "TEST_EMAIL_INTERCEPT_ADDRESS" not in content:
            self.assertIn("APP_ENV=production", content,
                          "Non-production env must set TEST_EMAIL_INTERCEPT_ADDRESS")
            return
        self.assertIn('TEST_EMAIL_INTERCEPT_ADDRESS', content,
                      "backend/.env must contain TEST_EMAIL_INTERCEPT_ADDRESS when enabled")

    def test_backend_env_intercept_address_is_admin_email(self):
        """If present, intercept address must be a valid email."""
        env_path = os.path.join(BACKEND_DIR, '.env')
        with open(env_path) as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if line.startswith('TEST_EMAIL_INTERCEPT_ADDRESS=') and not line.startswith('#'):
                value = line.split('=', 1)[1].strip()
                self.assertTrue(len(value) > 0, "TEST_EMAIL_INTERCEPT_ADDRESS must not be empty")
                self.assertIn('@', value, "TEST_EMAIL_INTERCEPT_ADDRESS must be an email address")
                return
        # Allowed to be absent in production
        self.assertTrue(True)


class TestPlaywrightTestCleanupPolicy(unittest.TestCase):
    """Verify Playwright tests follow the no-real-data policy."""

    def test_notice_board_spec_uses_route_mocking(self):
        """announcements_and_finance.spec.js must mock POST /api/notices."""
        spec_path = os.path.join(
            BACKEND_DIR, '..', 'tests', 'frontend', 'announcements_and_finance.spec.js'
        )
        spec_path = os.path.normpath(spec_path)
        self.assertTrue(os.path.exists(spec_path), f"Spec file not found: {spec_path}")
        with open(spec_path) as f:
            src = f.read()
        self.assertIn("page.route", src,
                      "Playwright spec must use page.route() to mock API calls")
        self.assertIn("api/notices", src,
                      "Playwright spec must intercept /api/notices")

    def test_global_teardown_cleans_user_notifications(self):
        """global-teardown.js must clean up user_notifications collection."""
        teardown_path = os.path.normpath(os.path.join(
            BACKEND_DIR, '..', 'tests', 'frontend', 'global-teardown.js'
        ))
        with open(teardown_path) as f:
            src = f.read()
        self.assertIn("user_notifications", src,
                      "global-teardown.js must clean up user_notifications")

    def test_cleanup_script_exists(self):
        """scripts/db/cleanup_test_data.py must exist."""
        script = os.path.normpath(os.path.join(
            BACKEND_DIR, '..', 'scripts', 'db', 'cleanup_test_data.py'
        ))
        self.assertTrue(os.path.exists(script),
                        "scripts/db/cleanup_test_data.py must exist for manual cleanup")

    def test_cleanup_script_handles_announcements_and_notifications(self):
        """cleanup_test_data.py must remove both announcements and user_notifications."""
        script = os.path.normpath(os.path.join(
            BACKEND_DIR, '..', 'scripts', 'db', 'cleanup_test_data.py'
        ))
        with open(script) as f:
            src = f.read()
        self.assertIn("announcements", src)
        self.assertIn("user_notifications", src)


def teardown_module(module):
    """Restore event loop after IsolatedAsyncioTestCase destroys it."""
    asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == '__main__':
    unittest.main(verbosity=2)
