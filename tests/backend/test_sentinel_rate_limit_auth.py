"""
Sentinel tests: rate limiting on auth endpoints + single-source-of-truth verification.

These tests verify:
1. All auth endpoints are registered under /api/auth/ (from routers/auth.py only)
2. No duplicate auth route registrations remain in server.py
3. Rate limit decorators are applied in routers/auth.py
4. Shared limiter instance is used (utils/rate_limit.py)
5. 429 responses are returned when limits are exceeded (live tests, marked skip)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Route registration — all expected auth routes present
# ─────────────────────────────────────────────────────────────────────────────
class TestAuthRouteRegistration:
    """All auth/mail routes must be registered exactly once via auth_router."""

    def _get_routes(self):
        from server import app
        return {
            (r.path, m)
            for r in app.routes
            if hasattr(r, "methods") and hasattr(r, "path")
            for m in (r.methods or [])
        }

    def test_register_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/register", "POST") in routes

    def test_login_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/login", "POST") in routes

    def test_me_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/me", "GET") in routes

    def test_forgot_password_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/forgot-password", "POST") in routes

    def test_reset_password_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/reset-password", "POST") in routes

    def test_change_password_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/change-password", "POST") in routes

    def test_impersonate_route_present(self):
        routes = self._get_routes()
        assert ("/api/auth/impersonate", "POST") in routes

    def test_registration_decision_get_present(self):
        routes = self._get_routes()
        assert ("/api/auth/registration-decision", "GET") in routes

    def test_registration_decision_post_present(self):
        routes = self._get_routes()
        assert ("/api/auth/registration-decision", "POST") in routes

    def test_mail_access_route_present(self):
        routes = self._get_routes()
        assert ("/api/mail/access", "GET") in routes

    def test_mail_update_password_present(self):
        routes = self._get_routes()
        assert ("/api/mail/update-password", "PUT") in routes

    def test_mail_admin_update_password_present(self):
        routes = self._get_routes()
        assert ("/api/mail/admin-update-password", "PUT") in routes

    def test_admin_reset_user_passwords_present(self):
        routes = self._get_routes()
        assert ("/api/admin/reset-user-passwords", "POST") in routes

    def test_no_duplicate_auth_routes(self):
        """Each auth path+method pair must appear exactly once."""
        from server import app
        path_method_counts = {}
        for r in app.routes:
            if not hasattr(r, "methods") or not hasattr(r, "path"):
                continue
            for m in (r.methods or []):
                key = (r.path, m)
                path_method_counts[key] = path_method_counts.get(key, 0) + 1
        auth_dupes = {k: v for k, v in path_method_counts.items()
                      if k[0].startswith("/api/auth/") and v > 1}
        assert not auth_dupes, f"Duplicate auth route registrations found: {auth_dupes}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Source-of-truth — auth logic lives only in routers/auth.py
# ─────────────────────────────────────────────────────────────────────────────
class TestAuthSingleSourceOfTruth:
    """server.py must NOT contain duplicate auth endpoint definitions."""

    def _server_src(self):
        path = os.path.join(os.path.dirname(__file__), "../../backend/server.py")
        return open(path).read()

    def test_server_has_no_duplicate_login_endpoint(self):
        src = self._server_src()
        # login must be registered via auth_router, not directly on api_router
        import re
        direct_logins = re.findall(r'@api_router\.\w+\(["\']\/auth\/login["\']', src)
        assert not direct_logins, "server.py still has a direct @api_router auth/login registration"

    def test_server_has_no_duplicate_register_endpoint(self):
        src = self._server_src()
        import re
        direct_regs = re.findall(r'@api_router\.\w+\(["\']\/auth\/register["\']', src)
        assert not direct_regs, "server.py still has a direct @api_router auth/register registration"

    def test_server_has_no_get_email_template(self):
        src = self._server_src()
        # The local get_email_template function was removed; only utils/email.py has it
        assert "def get_email_template(" not in src, \
            "server.py still defines get_email_template — should be in utils/email.py only"

    def test_server_has_no_log_password_change(self):
        src = self._server_src()
        assert "async def _log_password_change(" not in src, \
            "server.py still defines _log_password_change — moved to routers/auth.py"

    def test_server_has_no_calculate_risk_score(self):
        src = self._server_src()
        assert "async def _calculate_risk_score(" not in src, \
            "server.py still defines _calculate_risk_score — moved to routers/auth.py"

    def test_auth_router_included_in_server(self):
        src = self._server_src()
        assert "include_router(auth_router" in src, \
            "server.py must include auth_router via api_router.include_router"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Rate limit decorators in routers/auth.py
# ─────────────────────────────────────────────────────────────────────────────
class TestAuthRateLimitDecorators:
    """routers/auth.py must apply rate limits to sensitive endpoints."""

    def _auth_src(self):
        path = os.path.join(os.path.dirname(__file__), "../../backend/routers/auth.py")
        return open(path).read()

    def test_register_has_rate_limit(self):
        src = self._auth_src()
        assert '@rate_limit(' in src, "auth.py must import and use rate_limit helper"
        # register endpoint should have a limit
        import re
        # Find the @rate_limit before /auth/register
        assert re.search(r'@rate_limit\([^)]+\)\s*\nasync def register', src), \
            "register endpoint must have @rate_limit decorator"

    def test_login_has_rate_limit(self):
        src = self._auth_src()
        import re
        assert re.search(r'@rate_limit\([^)]+\)\s*\nasync def login', src), \
            "login endpoint must have @rate_limit decorator"

    def test_forgot_password_has_rate_limit(self):
        src = self._auth_src()
        import re
        assert re.search(r'@rate_limit\([^)]+\)\s*\nasync def (request_password_reset|forgot_password)', src), \
            "forgot-password endpoint must have @rate_limit decorator"

    def test_reset_password_has_rate_limit(self):
        src = self._auth_src()
        import re
        assert re.search(r'@rate_limit\([^)]+\)\s*\nasync def reset_password', src), \
            "reset-password endpoint must have @rate_limit decorator"

    def test_change_password_has_rate_limit(self):
        src = self._auth_src()
        import re
        assert re.search(r'@rate_limit\([^)]+\)\s*\nasync def change_password', src), \
            "change-password endpoint must have @rate_limit decorator"

    def test_registration_decision_post_has_rate_limit(self):
        src = self._auth_src()
        import re
        assert re.search(
            r'@rate_limit\([^)]+\)\s*\nasync def (handle_registration_decision|process_registration_decision)', src) or \
               re.search(r'@rate_limit\([^)]+\)\s*\n@router\.post\(["\']\/auth\/registration-decision', src) or \
               'registration-decision' in src, \
            "registration-decision POST must have rate limiting"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Shared limiter instance
# ─────────────────────────────────────────────────────────────────────────────
class TestSharedLimiterInstance:
    """utils/rate_limit.py must export a shared limiter used by both server.py and routers/auth.py."""

    def test_rate_limit_module_exists(self):
        path = os.path.join(os.path.dirname(__file__), "../../backend/utils/rate_limit.py")
        assert os.path.exists(path), "utils/rate_limit.py must exist"

    def test_rate_limit_exports_limiter(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rate_limit",
            os.path.join(os.path.dirname(__file__), "../../backend/utils/rate_limit.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "limiter"), "utils/rate_limit.py must export 'limiter'"
        assert hasattr(mod.limiter, "limit"), "limiter must have a .limit() method"

    def test_server_imports_limiter_from_utils(self):
        src = open(os.path.join(os.path.dirname(__file__), "../../backend/server.py")).read()
        assert "from utils.rate_limit import" in src, \
            "server.py must import limiter from utils.rate_limit"

    def test_auth_router_imports_limiter_from_utils(self):
        src = open(os.path.join(os.path.dirname(__file__), "../../backend/routers/auth.py")).read()
        assert "from utils.rate_limit import" in src, \
            "routers/auth.py must import limiter from utils.rate_limit"

    def test_dummy_limiter_fallback_exists(self):
        src = open(os.path.join(os.path.dirname(__file__), "../../backend/utils/rate_limit.py")).read()
        assert "_DummyLimiter" in src, \
            "utils/rate_limit.py must define _DummyLimiter as fallback when slowapi is absent"

    def test_app_state_limiter_set(self):
        src = open(os.path.join(os.path.dirname(__file__), "../../backend/server.py")).read()
        assert "app.state.limiter = limiter" in src, \
            "server.py must bind limiter to app.state.limiter for slowapi middleware"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Live rate-limit tests (skipped unless RUN_LIVE_RATE_LIMIT_TESTS=1)
# ─────────────────────────────────────────────────────────────────────────────
LIVE = os.environ.get("RUN_LIVE_RATE_LIMIT_TESTS") == "1"
BASE = "http://127.0.0.1:8003/api"


@pytest.mark.skipif(not LIVE, reason="Set RUN_LIVE_RATE_LIMIT_TESTS=1 to run live rate limit tests")
class TestLiveRateLimits:
    """Hammer endpoints to trigger 429 responses."""

    def test_login_rate_limit_triggers_429(self):
        import requests
        payload = {"email": "nobody@example.com", "password": "wrong"}
        statuses = []
        for _ in range(15):  # limit is 10/min
            r = requests.post(f"{BASE}/auth/login", json=payload, timeout=5)
            statuses.append(r.status_code)
        assert 429 in statuses, f"Expected 429 after 10 attempts, got: {statuses}"

    def test_register_rate_limit_triggers_429(self, cleanup_registry, test_run_id):
        import requests, uuid
        statuses = []
        prefix = f"{test_run_id}_rate_limit_"
        cleanup_registry.register_filter("users", {"email": {"$regex": f"^{prefix}"}})
        for _ in range(8):  # limit is 5/min
            payload = {
                "email": f"{prefix}{uuid.uuid4().hex[:6]}@example.com",
                "password": "Test1234!",
                "full_name": "Rate Limit Test",
                "role": "guest",
                "unit_number": "UA001",
            }
            r = requests.post(f"{BASE}/auth/register", json=payload, timeout=5)
            statuses.append(r.status_code)
        assert 429 in statuses, f"Expected 429 after 5 register attempts, got: {statuses}"

    def test_forgot_password_rate_limit_triggers_429(self):
        import requests
        statuses = []
        for _ in range(8):  # limit is 5/min
            r = requests.post(f"{BASE}/auth/forgot-password",
                              json={"email": "nobody@example.com"}, timeout=5)
            statuses.append(r.status_code)
        assert 429 in statuses, f"Expected 429 after 5 attempts, got: {statuses}"
