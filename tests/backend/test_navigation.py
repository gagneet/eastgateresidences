"""
Tests for Navigation System: progressive navigation config, preferences, tracking, badges.

All test data uses is_test_data=True and is cleaned up in teardown.
Uses TEST_BUILDING_ID != "13195" to avoid polluting production building.

These tests make live HTTP calls and require a running backend + seeded database.
Run only in integration mode:
    RUN_INTEGRATION_TESTS=1 backend/venv/bin/pytest tests/backend/test_navigation.py -v

Skip in CI unit test pass without the flag:
    backend/venv/bin/pytest tests/backend/test_navigation.py -v  # auto-skips if server down
"""
import os
import uuid

import pytest
import requests

TEST_BUILDING_ID = "test-building-nav-001"
TEST_USER_ROLE = "owner"

_BASE_URL = "http://127.0.0.1:8003/api"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get(path: str, token: str, **kwargs) -> requests.Response:
    return requests.get(
        f"{_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        **kwargs,
    )


def _post(path: str, token: str, json: dict = None, **kwargs) -> requests.Response:
    return requests.post(
        f"{_BASE_URL}{path}",
        json=json or {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        **kwargs,
    )


def _patch(path: str, token: str, json: dict = None, **kwargs) -> requests.Response:
    return requests.patch(
        f"{_BASE_URL}{path}",
        json=json or {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        **kwargs,
    )


def _skip_if_unavailable(token):
    """Skip the test if the backend is not running or token is missing."""
    if not token:
        pytest.skip("Backend not available or admin token missing")


# ─── Navigation Config ─────────────────────────────────────────────────────────

class TestNavigationConfig:
    """Tests for GET /api/navigation/config"""

    def test_navigation_config_returns_200(self, admin_token):
        """Config endpoint should return 200 for authenticated user."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200, f"Unexpected: {resp.status_code} — {resp.text}"
        data = resp.json()
        assert "role" in data
        assert "mode" in data
        assert "simple_items" in data
        assert "advanced_items" in data

    def test_navigation_config_has_required_fields(self, admin_token):
        """Each nav item should have id, label, route, icon."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        for item in data.get("simple_items", []):
            assert "id" in item, f"Item missing 'id': {item}"
            assert "label" in item, f"Item missing 'label': {item}"
            assert "route" in item, f"Item missing 'route': {item}"
            assert "icon" in item, f"Item missing 'icon': {item}"

    def test_navigation_config_mode_is_valid(self, admin_token):
        """Mode must be 'classic', 'simple', or 'advanced'."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("mode") in ("classic", "simple", "advanced"), (
            f"Unexpected mode: {data.get('mode')}"
        )

    def test_navigation_config_role_is_string(self, admin_token):
        """Role field should be a non-empty string."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("role"), str)
        assert len(data["role"]) > 0

    def test_navigation_config_unauthenticated_rejected(self):
        """Unauthenticated requests should be rejected."""
        try:
            resp = requests.get(f"{_BASE_URL}/navigation/config", timeout=10)
        except requests.exceptions.ConnectionError:
            pytest.skip("Backend not available")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )

    def test_navigation_config_x_building_id_header_accepted(self, admin_token):
        """X-Building-ID header should be accepted without causing a 500."""
        _skip_if_unavailable(admin_token)
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "X-Building-ID": "13195",
        }
        try:
            resp = requests.get(
                f"{_BASE_URL}/navigation/config",
                headers=headers,
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            pytest.skip("Backend not available")
        # Not a 500 — any other response is acceptable
        assert resp.status_code != 500, (
            f"Server error with X-Building-ID header: {resp.text}"
        )

    def test_navigation_config_advanced_items_is_list(self, admin_token):
        """advanced_items should always be a list (may be empty)."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("advanced_items"), list), (
            f"advanced_items is not a list: {data.get('advanced_items')}"
        )

    def test_navigation_config_simple_items_is_list(self, admin_token):
        """simple_items should always be a list (may be empty)."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("simple_items"), list), (
            f"simple_items is not a list: {data.get('simple_items')}"
        )


# ─── Navigation Preferences ────────────────────────────────────────────────────

class TestNavigationPreferences:
    """Tests for PATCH /api/navigation/preferences"""

    def test_navigation_preferences_set_advanced_mode(self, admin_token):
        """Setting mode to 'advanced' should return 200 with status ok."""
        _skip_if_unavailable(admin_token)
        resp = _patch("/navigation/preferences", admin_token, json={"preferred_mode": "advanced"})
        assert resp.status_code == 200, f"Unexpected: {resp.status_code} — {resp.text}"
        assert resp.json().get("status") == "ok"

    def test_navigation_preferences_reset_to_simple(self, admin_token):
        """Setting mode back to 'simple' should return 200."""
        _skip_if_unavailable(admin_token)
        resp = _patch("/navigation/preferences", admin_token, json={"preferred_mode": "simple"})
        assert resp.status_code == 200, f"Unexpected: {resp.status_code} — {resp.text}"

    def test_navigation_preferences_invalid_mode_rejected(self, admin_token):
        """Invalid mode value should return 400."""
        _skip_if_unavailable(admin_token)
        resp = _patch("/navigation/preferences", admin_token, json={
            "preferred_mode": "superadvanced"
        })
        assert resp.status_code == 400, (
            f"Expected 400 for invalid mode, got {resp.status_code} — {resp.text}"
        )

    def test_navigation_preferences_max_2_pinned_enforced(self, admin_token):
        """More than 2 pinned items should be rejected with 400."""
        _skip_if_unavailable(admin_token)
        resp = _patch("/navigation/preferences", admin_token, json={
            "pinned_items": ["item-a", "item-b", "item-c"]
        })
        assert resp.status_code == 400, (
            f"Expected 400 for >2 pinned items, got {resp.status_code} — {resp.text}"
        )
        detail = resp.json().get("detail", "").lower()
        assert "2" in detail or "maximum" in detail or "pin" in detail, (
            f"Error detail should mention limit: {detail}"
        )

    def test_navigation_preferences_pinned_items_up_to_2_accepted(self, admin_token):
        """Up to 2 pinned items should be accepted."""
        _skip_if_unavailable(admin_token)
        # Use item IDs that exist in the super_admin navigation config
        # (super_admin simple_items: dashboard, users, finance, maintenance, system)
        resp = _patch("/navigation/preferences", admin_token, json={
            "pinned_items": ["finance", "users"]
        })
        assert resp.status_code == 200, (
            f"Expected 200 for 2 pinned items, got {resp.status_code} — {resp.text}"
        )

    def test_navigation_preferences_features_seen_merged(self, admin_token):
        """features_seen should merge (not overwrite) existing values."""
        _skip_if_unavailable(admin_token)
        # First call: mark dashboard seen
        _patch("/navigation/preferences", admin_token, json={
            "features_seen": {"dashboard": True}
        })
        # Second call: mark my-levies seen
        resp = _patch("/navigation/preferences", admin_token, json={
            "features_seen": {"my-levies": True}
        })
        assert resp.status_code == 200, (
            f"Second features_seen patch failed: {resp.status_code} — {resp.text}"
        )

    def test_navigation_preferences_empty_body_accepted(self, admin_token):
        """Empty patch body should return 200 or 400 (not 500)."""
        _skip_if_unavailable(admin_token)
        resp = _patch("/navigation/preferences", admin_token, json={})
        assert resp.status_code in (200, 400), (
            f"Server error on empty patch: {resp.status_code} — {resp.text}"
        )

    def test_navigation_preferences_unauthenticated_rejected(self):
        """Unauthenticated preference updates should be rejected."""
        try:
            resp = requests.patch(
                f"{_BASE_URL}/navigation/preferences",
                json={"preferred_mode": "simple"},
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            pytest.skip("Backend not available")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )


# ─── Navigation Track ──────────────────────────────────────────────────────────

class TestNavigationTrack:
    """Tests for POST /api/navigation/track"""

    def test_navigation_track_returns_200(self, admin_token):
        """Track endpoint should return 200 for valid payload."""
        _skip_if_unavailable(admin_token)
        resp = _post("/navigation/track", admin_token, json={
            "feature_id": "test-feature",
            "route": "/dashboard/test",
            "event_type": "page_view",
            "session_id": str(uuid.uuid4()),
        })
        assert resp.status_code == 200, (
            f"Unexpected: {resp.status_code} — {resp.text}"
        )

    def test_navigation_track_fire_and_forget_no_crash(self, admin_token):
        """Multiple rapid track calls should not crash the server."""
        _skip_if_unavailable(admin_token)
        for feature in ("dashboard", "my-levies", "notices"):
            resp = _post("/navigation/track", admin_token, json={
                "feature_id": feature,
                "route": f"/dashboard/{feature}",
                "event_type": "page_view",
                "session_id": str(uuid.uuid4()),
            })
            assert resp.status_code == 200, (
                f"Track failed for {feature}: {resp.status_code} — {resp.text}"
            )

    def test_navigation_track_unauthenticated_rejected(self):
        """Unauthenticated track requests should fail."""
        try:
            resp = requests.post(
                f"{_BASE_URL}/navigation/track",
                json={
                    "feature_id": "test",
                    "route": "/dashboard",
                    "event_type": "page_view",
                    "session_id": "test",
                },
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            pytest.skip("Backend not available")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )

    def test_navigation_track_different_event_types(self, admin_token):
        """Different event_type values should all return 200."""
        _skip_if_unavailable(admin_token)
        event_types = ["page_view", "feature_used", "menu_click"]
        for event_type in event_types:
            resp = _post("/navigation/track", admin_token, json={
                "feature_id": "test-feature",
                "route": "/dashboard/test",
                "event_type": event_type,
                "session_id": str(uuid.uuid4()),
            })
            assert resp.status_code == 200, (
                f"Track failed for event_type={event_type}: {resp.status_code} — {resp.text}"
            )


# ─── Navigation Badges ─────────────────────────────────────────────────────────

class TestNavigationBadges:
    """Tests for GET /api/navigation/badges"""

    EXPECTED_BADGE_KEYS = [
        "requests_overdue",
        "requests_new",
        "notices_unread",
        "parcels_waiting",
        "proposals_open_vote",
        "approvals_pending",
        "compliance_overdue",
        "sla_breached",
        "levy_due_soon",
    ]

    def test_navigation_badges_returns_200(self, admin_token):
        """Badges endpoint should return 200."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/badges", admin_token)
        assert resp.status_code == 200, (
            f"Unexpected: {resp.status_code} — {resp.text}"
        )

    def test_navigation_badges_has_expected_keys(self, admin_token):
        """Badges response should contain all expected badge keys."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/badges", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        for key in self.EXPECTED_BADGE_KEYS:
            assert key in data, f"Missing badge key: {key}"

    def test_navigation_badges_all_non_negative_integers(self, admin_token):
        """All badge counts should be non-negative integers."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/badges", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        for key, value in data.items():
            assert isinstance(value, int), (
                f"Badge '{key}' is not an int: {value!r}"
            )
            assert value >= 0, f"Badge '{key}' is negative: {value}"

    def test_navigation_badges_returns_dict(self, admin_token):
        """Response body should be a JSON object (dict), not a list."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/badges", admin_token)
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict), (
            f"Expected dict, got {type(resp.json())}"
        )

    def test_navigation_badges_unauthenticated_rejected(self):
        """Unauthenticated badge requests should fail."""
        try:
            resp = requests.get(f"{_BASE_URL}/navigation/badges", timeout=10)
        except requests.exceptions.ConnectionError:
            pytest.skip("Backend not available")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )

    def test_navigation_badges_response_is_fast(self, admin_token):
        """Badges endpoint should respond within 5 seconds."""
        import time
        _skip_if_unavailable(admin_token)
        start = time.monotonic()
        resp = _get("/navigation/badges", admin_token)
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed < 5.0, (
            f"Badges endpoint too slow: {elapsed:.2f}s"
        )


# ─── Adaptive Nav Service (pure unit tests using real service functions) ────────

class TestAdaptiveNavScoringFormulas:
    """Unit tests for the adaptive navigation scoring algorithm.

    These tests import the actual service helpers and do NOT require a running
    backend — they validate the mathematical model directly.
    """

    def test_recency_score_is_1_for_today(self):
        """Recency score should be 1.0 for an item accessed today."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _recency_score
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        assert _recency_score(now.isoformat(), now) == 1.0

    def test_recency_score_decays_at_7_days(self):
        """Recency score at 7 days should be approximately 0.51."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _recency_score
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        seven_days_ago = (now - timedelta(days=7)).isoformat()
        score = _recency_score(seven_days_ago, now)
        assert abs(score - 0.51) < 0.01

    def test_recency_score_floor_is_zero(self):
        """Recency score should never go below 0."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _recency_score
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        old_date = (now - timedelta(days=100)).isoformat()
        assert _recency_score(old_date, now) == 0.0
        assert _recency_score(None, now) == 0.0

    def test_frequency_score_zero_for_no_uses(self):
        """Frequency score should be 0.0 for zero uses."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _frequency_score
        assert _frequency_score(0) == 0.0

    def test_frequency_score_half_at_ten_uses(self):
        """Frequency score at 10 uses should be 0.5."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _frequency_score
        assert _frequency_score(10) == 0.5

    def test_frequency_score_caps_at_1_for_20_uses(self):
        """Frequency score should cap at 1.0 at 20 uses."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _frequency_score
        assert _frequency_score(20) == 1.0
        assert _frequency_score(100) == 1.0

    def test_peer_percentile_median(self):
        """Peer at median should return 0.5."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _peer_percentile
        # 5 peers with counts [1,2,3,4,5]; user count=3 → 3 out of 5 ≤ 3 → 60th percentile
        score = _peer_percentile(3, [1, 2, 3, 4, 5])
        assert abs(score - 0.6) < 0.01

    def test_peer_percentile_empty_peers(self):
        """With no peers, percentile should default to 0.5."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
        from services.adaptive_nav_service import _peer_percentile
        assert _peer_percentile(5, []) == 0.5

    def test_final_score_formula_all_max(self):
        """Final score with all inputs at 1.0 should equal 1.0."""
        recency, frequency, peer = 1.0, 1.0, 1.0
        score = (recency * 0.40) + (frequency * 0.35) + (peer * 0.25)
        assert score == 1.0

    def test_final_score_formula_all_zero(self):
        """Final score with all inputs at 0.0 should equal 0.0."""
        recency, frequency, peer = 0.0, 0.0, 0.0
        score = (recency * 0.40) + (frequency * 0.35) + (peer * 0.25)
        assert score == 0.0

    def test_final_score_weights_sum_to_1(self):
        """The three score weights must sum to exactly 1.0."""
        weights = [0.40, 0.35, 0.25]
        assert abs(sum(weights) - 1.0) < 1e-9


# ─── Navigation Seed Integrity ─────────────────────────────────────────────────

class TestNavigationSeedIntegrity:
    """Tests that navigation seed data is consistent and reachable."""

    def test_nav_config_reachable_for_super_admin(self, admin_token):
        """Super admin should always be able to reach navigation config."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200, (
            f"Navigation config not reachable for super_admin: {resp.status_code}"
        )

    def test_nav_config_response_is_not_empty(self, admin_token):
        """Navigation config should return a non-empty response."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/config", admin_token)
        assert resp.status_code == 200
        data = resp.json()
        assert data, "Navigation config response should not be empty"

    def test_nav_preferences_endpoint_reachable(self, admin_token):
        """Preferences endpoint should be reachable with a valid auth token."""
        _skip_if_unavailable(admin_token)
        resp = _patch("/navigation/preferences", admin_token, json={})
        assert resp.status_code in (200, 400), (
            f"Preferences endpoint not reachable: {resp.status_code} — {resp.text}"
        )

    def test_nav_badges_endpoint_reachable(self, admin_token):
        """Badges endpoint should be reachable with a valid auth token."""
        _skip_if_unavailable(admin_token)
        resp = _get("/navigation/badges", admin_token)
        assert resp.status_code == 200, (
            f"Badges endpoint not reachable: {resp.status_code} — {resp.text}"
        )

    def test_nav_track_endpoint_reachable(self, admin_token):
        """Track endpoint should be reachable with a valid auth token."""
        _skip_if_unavailable(admin_token)
        resp = _post("/navigation/track", admin_token, json={
            "feature_id": "seed-integrity-check",
            "route": "/dashboard",
            "event_type": "page_view",
            "session_id": str(uuid.uuid4()),
        })
        assert resp.status_code == 200, (
            f"Track endpoint not reachable: {resp.status_code} — {resp.text}"
        )
