"""
Test Suite: Portfolio Dashboard API
=====================================
Tests for /api/portfolio/* endpoints.

Covers:
- GET /portfolio/summary       — cross-building metrics
- GET /portfolio/dashboard     — buildings list with health/arrears/WOs
- GET /portfolio/buildings     — detailed building list
- Role-based access control    — managers only
- Multi-tenant isolation       — buildings scoped correctly
- Lot count field normalisation — "lots" vs "lot_count" field bug

Run with:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_portfolio.py -v
"""

import os
import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"


def auth(token):
    return {"Authorization": f"Bearer {token}", "X-Building-ID": "13195"}


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_token():
    """Super-admin JWT from a real login against the running backend.

    Skips unless E2E_ADMIN_PASSWORD is set. Until 2026-08-26 this fixture carried the
    password as a literal, so a plain `pytest tests/backend` run authenticated against
    the live backend as a super_admin without anyone asking for it — sixteen times in
    this file alone. The credential has since been rotated and is supplied by
    environment; absent it, this suite has nothing to test and says so.

    The assert below is kept for the configured-but-wrong case: a supplied credential
    that fails is a real finding, not a reason to skip.
    """
    password = os.environ.get("E2E_ADMIN_PASSWORD", "")
    if not password:
        pytest.skip("E2E_ADMIN_PASSWORD not set — this suite performs a real login.")
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": os.environ.get("E2E_ADMIN_EMAIL", "administrator@strataos.live"),
        "password": password,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture(scope="module")
def chairman_token():
    """EC Chairman JWT (manager role — should access portfolio).

    This one logs in for real, so a purged account fails at the login itself rather than
    on the first request. East Gate's user records were removed 2026-08-21; skip with the
    reason instead of asserting a 200 that cannot happen until the backup is restored.
    """
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "anthony@eastgateresidences.com.au",
        "password": os.environ.get("E2E_CHAIRMAN_PASSWORD", ""),
    })
    if resp.status_code == 401:
        pytest.skip(
            "East Gate chairman account absent — data purged 2026-08-21; restore via "
            "scripts/data_repair/eastgate_export_restore.py to run this suite"
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture(scope="module")
def owner_token(mint_token, require_live_identity):
    """Owner JWT — should be denied portfolio endpoints (403).

    Guarded: a deleted account returns 401, not the 403 this asserts.
    """
    return require_live_identity(mint_token("avneet@eastgateresidences.com.au"))


# ─── GET /portfolio/summary ────────────────────────────────────────────────────

class TestPortfolioSummary:
    endpoint = f"{BASE_URL}/portfolio/summary"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_denied(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code in {401, 403}

    def test_super_admin_can_access(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        assert r.status_code == 200

    def test_returns_expected_fields(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert "active_buildings" in data
        assert "total_lots" in data
        assert "total_arrears_cents" in data
        assert "avg_building_health" in data
        assert "open_work_orders" in data

    def test_active_buildings_not_zero(self, admin_token):
        """At least East Gate must be present."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert data["active_buildings"] >= 1

    def test_total_lots_not_zero(self, admin_token):
        """East Gate has 87 lots — total must be >= 87 after lot_count fix."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert data["total_lots"] >= 87, (
            f"total_lots={data['total_lots']} is too low — buildings seed "
            "may not have lot_count field set"
        )

    def test_values_are_not_hardcoded(self, admin_token):
        """Ensure we're not returning the old hardcoded 2,450,000 arrears value."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert data["total_arrears_cents"] != 2450000 or data["active_buildings"] == 0, (
            "total_arrears_cents looks like the old hardcoded value (2450000)"
        )
        assert data["open_work_orders"] != 142 or data["active_buildings"] == 0, (
            "open_work_orders looks like the old hardcoded value (142)"
        )


# ─── GET /portfolio/dashboard ──────────────────────────────────────────────────

class TestPortfolioDashboard:
    endpoint = f"{BASE_URL}/portfolio/dashboard"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_denied(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code in {401, 403}

    def test_admin_can_access(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        assert r.status_code == 200

    def test_chairman_can_access(self, chairman_token):
        r = requests.get(self.endpoint, headers=auth(chairman_token))
        assert r.status_code == 200

    def test_response_shape(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert "buildings" in data
        assert "summary" in data
        assert "alerts" in data
        assert isinstance(data["buildings"], list)

    def test_east_gate_in_buildings(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        building_ids = [b["building_id"] for b in data["buildings"]]
        assert "13195" in building_ids, f"East Gate (13195) missing from buildings: {building_ids}"

    def test_lot_count_correct_for_east_gate(self, admin_token):
        """lot_count should be 87 for East Gate after the lots/lot_count fix."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        east_gate = next((b for b in data["buildings"] if b["building_id"] == "13195"), None)
        assert east_gate is not None, "East Gate (13195) not found in dashboard response"
        assert east_gate["lot_count"] == 87, (
            f"East Gate lot_count={east_gate['lot_count']}, expected 87. "
            "The 'lots' vs 'lot_count' field mismatch may not be fixed."
        )

    def test_health_score_is_a_valid_score_or_honestly_absent(self, admin_token):
        """A published health score must be in range; an absent one must stay absent.

        This asserted that East Gate always HAS a score. That premise died with its
        data on 2026-08-21 — the sibling test_last_computed_at_present below was
        updated for the same reason, this one was missed. It kept passing only
        because a stale building_summaries document still carried a score computed
        under the pre-2026-08-24 formula; the daily recompute at 02:03 AEST then
        rewrote it as status=insufficient_data (coverage 0.1, well under the 0.5
        minimum) and the assertion started failing.

        The recompute is CORRECT: East Gate has no units, work orders, proposals or
        volunteer events left in either store, so there is nothing to measure. A
        building that cannot be measured must report no score rather than a
        fabricated one — that is the invariant worth testing, so it is what this
        now tests. Restoring the data restores a score, and the range assertion
        still runs for every building that has one.
        """
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        east_gate = next((b for b in data["buildings"] if b["building_id"] == "13195"), None)
        assert east_gate is not None, "East Gate (13195) not found in dashboard response"

        score = east_gate["health_score"]
        if score is None:
            # Absent is a legitimate state; it must not be dressed up as a number.
            assert east_gate.get("health_status") in (None, "insufficient_data"), (
                f"health_score is None but health_status is "
                f"{east_gate.get('health_status')!r} — missing and measured must not blur"
            )
            pytest.skip(
                "East Gate has no measurable health data (purged 2026-08-21); "
                "None is the correct answer. Restore the backup or run "
                "seeds/seed_building_summaries.py to exercise the scored path."
            )
        assert 0 <= score <= 100

    def test_last_computed_at_present(self, admin_token):
        """Buildings with a computed summary should include last_computed_at.

        East Gate still appears in the portfolio list because its `buildings` row was
        deliberately kept, but its `building_summaries` document was removed with the rest
        of its data on 2026-08-21. The building being listed without a summary is now the
        expected state, not a regression, so skip rather than fail — the assertion still
        runs for any building that does have one.
        """
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        east_gate = next((b for b in data["buildings"] if b["building_id"] == "13195"), None)
        if not east_gate:
            pytest.skip("East Gate not present in the portfolio list")
        if east_gate.get("last_computed_at") is None:
            pytest.skip(
                "East Gate has no building_summaries document — data purged 2026-08-21; "
                "restore the backup or run seeds/seed_building_summaries.py"
            )
        assert east_gate.get("last_computed_at") is not None

    def test_summary_totals(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        summary = data["summary"]
        assert summary["total_buildings"] >= 1
        assert summary["total_lots"] >= 87
        assert 0 <= summary["avg_health_score"] <= 100


# ─── GET /portfolio/buildings ──────────────────────────────────────────────────

class TestPortfolioBuildings:
    endpoint = f"{BASE_URL}/portfolio/buildings"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_denied(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code in {401, 403}

    def test_super_admin_can_access(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        assert r.status_code == 200

    def test_returns_list(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        assert r.status_code == 200
        data = r.json()
        assert "buildings" in data
        assert isinstance(data["buildings"], list)

    def test_lot_count_field_normalised(self, admin_token):
        """All buildings should have lot_count > 0 after the lots/lot_count fix."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        for bld in r.json().get("buildings", []):
            assert bld.get("lot_count", 0) > 0, (
                f"Building {bld.get('id', '?')} has lot_count=0 — "
                "lots vs lot_count field normalisation failed"
            )

    def test_no_none_health_scores_after_seed(self, admin_token):
        """After seeding, health_score should not be 100 for all buildings (was default)."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        buildings = r.json().get("buildings", [])
        scores = [b.get("health_score") for b in buildings if b.get("health_score") is not None]
        if scores:
            assert any(s != 100 for s in scores), (
                "All buildings have health_score=100 (old default) — "
                "building_summaries may not be seeded properly"
            )
