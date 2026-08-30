"""
test_multitenant_buildings.py — Integration tests for multi-tenant building
auth, membership resolution, and building seed data.

These tests require a running backend (RUN_INTEGRATION_TESTS=1).

Rate-limit strategy: conftest._pre_seed_test_rate_limits raises rate_limit_login
to 200 in MongoDB before any logins, and the session-scoped admin_token fixture
(in conftest) also calls PUT /api/settings to immediately apply the higher limit
via the app's rate-limit controller. chairman_token depends on admin_token so
the limit is guaranteed raised before the second login.
"""
import os
import sys

import pytest
import requests

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_project_root, "backend"))

BASE_URL = "http://127.0.0.1:8003/api"
RUN = os.getenv("RUN_INTEGRATION_TESTS") == "1"
LEGACY_MULTI_BUILDING = os.getenv("RUN_LEGACY_BUILDING_TESTS") == "1"
SKIP_MSG = "Set RUN_INTEGRATION_TESTS=1 and RUN_LEGACY_BUILDING_TESTS=1 to enable legacy multi-building integration tests"
pytestmark = pytest.mark.skipif(not RUN or not LEGACY_MULTI_BUILDING, reason=SKIP_MSG)

CHAIRMAN_EMAIL = "anthony@eastgateresidences.com.au"
CHAIRMAN_PASSWORD = os.environ.get("E2E_CHAIRMAN_PASSWORD", "")


def decode_jwt_payload(token: str) -> dict:
    import base64, json
    padded = token.split(".")[1] + "==="
    return json.loads(base64.b64decode(padded))


# ── Module fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def chairman_token(admin_token):
    """Chairman JWT. Depends on admin_token so the rate limit is raised first.

    The conftest admin_token fixture calls PUT /api/settings with
    rate_limit_login=200 immediately after its own login, so by the time this
    fixture runs the in-process rate limit is 200/minute. Uses the same
    _login_with_retry helper for resilience.
    """
    if not RUN or not admin_token:
        pytest.skip(SKIP_MSG)
    from tests.backend.conftest import _login_with_retry
    return _login_with_retry(CHAIRMAN_EMAIL, CHAIRMAN_PASSWORD)


# ── Public endpoint tests (no auth required) ──────────────────────────────────
# These run always (no RUN_INTEGRATION_TESTS required).

def test_public_buildings_endpoint():
    """GET /api/auth/buildings should return buildings without auth."""
    resp = requests.get(f"{BASE_URL}/auth/buildings")
    assert resp.status_code == 200
    buildings = resp.json()
    assert isinstance(buildings, list)
    assert len(buildings) >= 1
    ids = [b["id"] for b in buildings]
    assert "13195" in ids


def test_public_buildings_contains_correct_address():
    """East Gate Residences should have the correct name and address."""
    resp = requests.get(f"{BASE_URL}/auth/buildings")
    buildings = resp.json()
    eg = next((b for b in buildings if b["id"] == "13195"), None)
    assert eg is not None
    assert "Hoolihan" in eg["address"]
    assert "Denman Prospect" in eg["address"]
    assert eg["name"] == "East Gate Residences"


def test_public_buildings_contains_sierra():
    """Sierra demo building should be present.

    Harbourview (18932) was removed on 2026-08-20 and is no longer expected.
    """
    resp = requests.get(f"{BASE_URL}/auth/buildings")
    buildings = resp.json()
    ids = [b["id"] for b in buildings]
    assert "16244" in ids


def test_sierra_building_address():
    """Sierra should have the correct Gungahlin address."""
    resp = requests.get(f"{BASE_URL}/auth/buildings")
    buildings = resp.json()
    sierra = next((b for b in buildings if b["id"] == "16244"), None)
    assert sierra is not None
    assert "Efkarpidis" in sierra["address"]
    assert "Gungahlin" in sierra["address"]


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_units_all_no_auth():
    """GET /api/units/all works without a JWT when X-Building-ID header is supplied.

    The endpoint uses get_building_or_400 (added in the F-010 hardening pass) which
    raises HTTP 400 when no building context is present — the test must supply the
    header that a public browser client would send.
    """
    resp = requests.get(f"{BASE_URL}/units/all", headers={"X-Building-ID": "13195"})
    assert resp.status_code == 200
    units = resp.json()
    assert isinstance(units, list)
    assert len(units) >= 87


# ── Login JWT contains building_id ────────────────────────────────────────────

@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_login_jwt_contains_building_id_admin(admin_token):
    """Super admin login should include building_id in JWT."""
    payload = decode_jwt_payload(admin_token)
    assert "building_id" in payload
    assert payload["building_id"] == "13195"
    assert payload["role"] == "super_admin"


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_login_jwt_has_required_claims(admin_token):
    """JWT should include standard and building claims."""
    payload = decode_jwt_payload(admin_token)
    for claim in ("building_id", "role", "user_id", "email"):
        assert claim in payload, f"Missing JWT claim: {claim}"


# ── Authenticated endpoints no longer 403 ─────────────────────────────────────

@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_settings_returns_200_after_auth(admin_token):
    """GET /api/settings should return 200 for authenticated users."""
    resp = requests.get(
        f"{BASE_URL}/settings",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert "building_name" in resp.json()


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_years_returns_200_after_auth(admin_token):
    """GET /api/years should return 200 (not 403) after auth fix."""
    resp = requests.get(
        f"{BASE_URL}/years",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_notifications_unread_count_returns_200(admin_token):
    """Notifications endpoint should work with building-scoped JWT."""
    resp = requests.get(
        f"{BASE_URL}/notifications/unread-count",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_announcements_returns_200(chairman_token):
    """Announcements should return 200 for chairman."""
    resp = requests.get(
        f"{BASE_URL}/announcements",
        headers={"Authorization": f"Bearer {chairman_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_feature_toggles_access_summary(admin_token):
    """Feature toggle access summary should work without 403."""
    resp = requests.get(
        f"{BASE_URL}/feature-toggles/access-summary/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_events_endpoint_not_403(admin_token):
    """Events should return 200 not 403."""
    resp = requests.get(
        f"{BASE_URL}/events",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


# ── Buildings/me returns correct membership ───────────────────────────────────

@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_buildings_me_admin_sees_all(admin_token):
    """Super admin should see all active buildings."""
    resp = requests.get(
        f"{BASE_URL}/buildings/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    buildings = resp.json()
    ids = [b["id"] for b in buildings]
    assert "13195" in ids
    assert "16244" in ids


@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_buildings_me_chairman_sees_own_building(chairman_token):
    """Chairman should see at least their own building."""
    resp = requests.get(
        f"{BASE_URL}/buildings/me",
        headers={"Authorization": f"Bearer {chairman_token}"},
    )
    assert resp.status_code == 200
    buildings = resp.json()
    assert len(buildings) >= 1
    ids = [b["id"] for b in buildings]
    assert "13195" in ids


# ── Switch building ────────────────────────────────────────────────────────────

@pytest.mark.skipif(not RUN, reason=SKIP_MSG)
def test_switch_building_returns_scoped_token(admin_token):
    """POST /auth/switch-building should return a token with building_id."""
    resp = requests.post(
        f"{BASE_URL}/auth/switch-building",
        json={"building_id": "13195"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    payload = decode_jwt_payload(data["token"])
    assert payload["building_id"] == "13195"
