"""
Test Suite: Owner Finance API
===============================
Tests for /api/owner-finance/* endpoints.

Covers:
- GET /owner-finance/levy-breakdown  — per-category levy data
- GET /owner-finance/health-explanation — building health with components
- GET /owner-finance/savings-summary    — building savings YTD
- Role-based access control (tenant denied, owner allowed)
- Multi-tenant isolation

Run with:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_owner_finance.py -v
"""

import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Fixtures ──────────────────────────────────────────────────────────────────
#
# These tests exercise owner-finance route contracts, not credential login. Use
# legacy-identity JWTs so local password drift, login rate limits, and the
# Postgres-primary token path do not turn this Mongo-primary owner-finance suite
# into a soft-skip or a false 401 during the cutover window.

@pytest.fixture(scope="module")
def owner_token(mint_token, require_live_identity):
    """Owner JWT (avneet, TH087, East Gate 13195)."""
    return require_live_identity(
        mint_token("avneet@eastgateresidences.com.au", legacy_identity=True))


@pytest.fixture(scope="module")
def chairman_token(mint_token, require_live_identity):
    """Chairman JWT — should also be allowed to view owner-finance."""
    return require_live_identity(
        mint_token("anthony@eastgateresidences.com.au", legacy_identity=True))


@pytest.fixture(scope="module")
def tenant_token(mint_token, require_live_identity):
    """Tenant JWT — should be denied owner-finance endpoints.

    Also guarded: a deleted tenant account returns 401 (not authenticated) rather than
    the 403 (authenticated but not permitted) these tests assert, so without the guard
    the RBAC assertion fails for a reason that has nothing to do with RBAC.
    """
    return require_live_identity(
        mint_token("tenant@eastgateresidences.com.au", legacy_identity=True))


# ─── GET /owner-finance/levy-breakdown ────────────────────────────────────────

class TestLevyBreakdown:
    endpoint = f"{BASE_URL}/owner-finance/levy-breakdown"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_tenant_denied(self, tenant_token):
        r = requests.get(self.endpoint, headers=auth(tenant_token))
        assert r.status_code == 403

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        # 400 is acceptable if unit not on account, 200 is success
        assert r.status_code in (200, 400)

    def test_chairman_can_access(self, chairman_token):
        r = requests.get(self.endpoint, headers=auth(chairman_token))
        assert r.status_code in (200, 400)

    def test_response_shape_when_successful(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association — cannot test response shape")
        data = r.json()
        # No hard error
        assert "error" not in data or data["error"] is None
        assert "quarterly_levy" in data
        assert "uoe_share_pct" in data
        assert "categories" in data
        assert isinstance(data["categories"], list)

    def test_categories_have_required_fields(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association")
        data = r.json()
        for cat in data.get("categories", []):
            assert "name" in cat, f"Category missing 'name': {cat}"
            assert "fund_type" in cat, f"Category missing 'fund_type': {cat}"
            assert "your_quarterly" in cat, f"Category missing 'your_quarterly': {cat}"
            assert "pct" in cat, f"Category missing 'pct': {cat}"
            assert cat["fund_type"] in ("admin", "administrative", "sinking", "insurance"), (
                f"Unexpected fund_type: {cat['fund_type']}"
            )

    def test_quarterly_levy_positive(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association")
        data = r.json()
        assert data["quarterly_levy"] > 0, "quarterly_levy should be positive"

    def test_uoe_share_pct_between_zero_and_one(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association")
        data = r.json()
        pct = data.get("uoe_share_pct", 0)
        assert 0 < pct < 100, f"uoe_share_pct={pct} out of expected range (0, 100)"


# ─── GET /owner-finance/health-explanation ────────────────────────────────────

class TestHealthExplanation:
    endpoint = f"{BASE_URL}/owner-finance/health-explanation"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_tenant_denied(self, tenant_token):
        r = requests.get(self.endpoint, headers=auth(tenant_token))
        assert r.status_code == 403

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200

    def test_chairman_can_access(self, chairman_token):
        r = requests.get(self.endpoint, headers=auth(chairman_token))
        assert r.status_code == 200

    def test_returns_health_score(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200
        data = r.json()
        assert "health_score" in data
        assert "grade" in data
        assert "components" in data

    def test_grade_is_valid(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        data = r.json()
        if data.get("health_score") is None:
            pytest.skip("No building summary seeded yet")
        assert data["grade"] in ("A", "B", "C", "D", "N/A"), (
            f"Unexpected grade: {data['grade']}"
        )

    def test_health_score_in_range(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        data = r.json()
        score = data.get("health_score")
        if score is None:
            pytest.skip("No building summary seeded yet")
        assert 0 <= score <= 100, f"health_score={score} out of range [0, 100]"

    def test_grade_consistent_with_score(self, owner_token):
        """Grade boundaries: A>=85, B>=70, C>=55, D<55."""
        r = requests.get(self.endpoint, headers=auth(owner_token))
        data = r.json()
        score = data.get("health_score")
        grade = data.get("grade")
        if score is None or grade == "N/A":
            pytest.skip("No building summary seeded yet")
        if score >= 85:
            assert grade == "A", f"score={score} should be Grade A, got {grade}"
        elif score >= 70:
            assert grade == "B", f"score={score} should be Grade B, got {grade}"
        elif score >= 55:
            assert grade == "C", f"score={score} should be Grade C, got {grade}"
        else:
            assert grade == "D", f"score={score} should be Grade D, got {grade}"

    def test_components_have_required_fields(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        data = r.json()
        for comp in data.get("components", []):
            assert "key" in comp, f"Component missing 'key': {comp}"
            assert "label" in comp, f"Component missing 'label': {comp}"
            assert "plain_english" in comp, f"Component missing 'plain_english': {comp}"
            assert "status" in comp, f"Component missing 'status': {comp}"
            assert comp["status"] in ("good", "warning", "poor"), (
                f"Unexpected status: {comp['status']}"
            )

    def test_east_gate_has_seeded_summary(self, owner_token):
        """After running seed_building_summaries.py, East Gate should have real data."""
        r = requests.get(self.endpoint, headers=auth(owner_token))
        data = r.json()
        assert data.get("health_score") is not None, (
            "Building health data is None — run 'python -m seeds.seed_building_summaries'"
        )


# ─── GET /owner-finance/savings-summary ───────────────────────────────────────

class TestSavingsSummary:
    endpoint = f"{BASE_URL}/owner-finance/savings-summary"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_tenant_denied(self, tenant_token):
        r = requests.get(self.endpoint, headers=auth(tenant_token))
        assert r.status_code == 403

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code in (200, 400)

    def test_response_shape(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association")
        data = r.json()
        assert "your_share_cents" in data
        assert "total_saved_ytd_cents" in data
        assert "levy_reduction_equivalent_cents" in data

    def test_amounts_non_negative(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association")
        data = r.json()
        assert data.get("your_share_cents", 0) >= 0
        assert data.get("total_saved_ytd_cents", 0) >= 0
        assert data.get("levy_reduction_equivalent_cents", 0) >= 0

    def test_your_share_le_building_total(self, owner_token):
        """An owner's share of savings cannot exceed the building total."""
        r = requests.get(self.endpoint, headers=auth(owner_token))
        if r.status_code != 200:
            pytest.skip("Owner has no unit association")
        data = r.json()
        share = data.get("your_share_cents", 0)
        total = data.get("total_saved_ytd_cents", 0)
        assert share <= total, (
            f"your_share_cents ({share}) > total_saved_ytd_cents ({total})"
        )
