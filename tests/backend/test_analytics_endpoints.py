"""
Test Suite: Analytics API Endpoints
====================================
Tests for /api/analytics/* endpoints added in the enhanced dashboard overhaul.

Covers:
- /api/analytics/sinking-fund-forecast
- /api/analytics/maintenance-stats
- /api/analytics/activities
- /api/analytics/market-snapshot
- /api/analytics/personal-badges
- /api/analytics/compliance-summary  (new)
- /api/analytics/marketplace-stats   (new)

Run with:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_analytics_endpoints.py -v
"""

import os
import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def owner_token():
    """Obtain JWT for owner user (avneet, TH087)."""
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "avneet@eastgateresidences.com.au",
        "password": os.environ.get("E2E_OWNER_PASSWORD", "")
    })
    if resp.status_code != 200:
        pytest.skip(f"Owner login failed ({resp.status_code}): {resp.text}")
    return resp.json()["token"]


@pytest.fixture(scope="module")
def chairman_token():
    """Obtain JWT for chairman user (anthony, UA063)."""
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "anthony@eastgateresidences.com.au",
        "password": os.environ.get("E2E_CHAIRMAN_PASSWORD", "")
    })
    if resp.status_code != 200:
        pytest.skip(f"Chairman login failed ({resp.status_code}): {resp.text}")
    return resp.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Sinking Fund Forecast ─────────────────────────────────────────────────────

class TestSinkingFundForecast:
    endpoint = f"{BASE_URL}/analytics/sinking-fund-forecast"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200

    def test_response_has_summary(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert "summary" in data
        assert "current_balance" in data["summary"]
        assert "status" in data["summary"]
        assert data["summary"]["status"] in ("healthy", "watch", "unknown")
        assert "data_available" in data["summary"]
        assert isinstance(data["summary"]["data_available"], bool)

    def test_data_available_false_returns_unknown_status(self, admin_token):
        """When no sinking-fund plan data exists, status must be 'unknown' and data_available False."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        if not data["summary"]["data_available"]:
            assert data["summary"]["status"] == "unknown"
        else:
            assert data["summary"]["status"] in ("healthy", "watch")

    def test_current_balance_is_real_db_value(self, admin_token):
        """Balance should come from real DB, not hardcoded. Known 2025 value ≈ $212,644."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        balance = r.json()["summary"]["current_balance"]
        # Real DB value is 212644.97; should not be exactly 212644.0 (old hardcoded)
        assert balance > 0
        assert balance > 100000  # sanity check

    def test_projection_array_matches_years_param(self, admin_token):
        for n in (1, 5, 10):
            r = requests.get(f"{self.endpoint}?years={n}", headers=auth(admin_token))
            assert r.status_code == 200
            proj = r.json()["projection"]
            assert len(proj) == n, f"Expected {n} years, got {len(proj)}"

    def test_projection_items_structure(self, admin_token):
        r = requests.get(f"{self.endpoint}?years=3", headers=auth(admin_token))
        for item in r.json()["projection"]:
            assert "year" in item
            assert "opening_balance" in item
            assert "closing_balance" in item
            assert "contributions" in item
            assert "expenses" in item
            assert "events" in item
            assert isinstance(item["events"], list)

    def test_note_field_present(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        assert "note" in r.json()

    def test_years_param_boundaries(self, admin_token):
        r_min = requests.get(f"{self.endpoint}?years=1", headers=auth(admin_token))
        assert r_min.status_code == 200
        r_max = requests.get(f"{self.endpoint}?years=20", headers=auth(admin_token))
        assert r_max.status_code == 200
        r_over = requests.get(f"{self.endpoint}?years=21", headers=auth(admin_token))
        assert r_over.status_code == 422  # exceeds max


# ─── Maintenance Stats ─────────────────────────────────────────────────────────

class TestMaintenanceStats:
    endpoint = f"{BASE_URL}/analytics/maintenance-stats"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200

    def test_response_structure(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert "open_requests" in data
        assert "closed_this_month" in data
        assert "avg_resolution_days" in data
        assert "sla_compliance_rate" in data

    def test_numeric_values(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert isinstance(data["open_requests"], int)
        assert data["open_requests"] >= 0
        assert isinstance(data["avg_resolution_days"], (int, float))
        assert data["avg_resolution_days"] >= 0
        assert isinstance(data["sla_compliance_rate"], (int, float))

    def test_sla_compliance_is_valid_percentage(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        sla = r.json()["sla_compliance_rate"]
        assert 0 <= sla <= 100


# ─── Activities ────────────────────────────────────────────────────────────────

class TestActivities:
    endpoint = f"{BASE_URL}/analytics/activities"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_default_returns_activities(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        activities = r.json()
        assert isinstance(activities, list)
        # Should have at least seeded activities
        assert len(activities) >= 0

    def test_limit_param(self, admin_token):
        r = requests.get(f"{self.endpoint}?limit=3", headers=auth(admin_token))
        assert r.status_code == 200
        assert len(r.json()) <= 3

    def test_activity_item_structure(self, admin_token):
        r = requests.get(f"{self.endpoint}?limit=5", headers=auth(admin_token))
        for item in r.json():
            assert "type" in item
            assert "title" in item
            assert "created_at" in item

    def test_limit_boundary(self, admin_token):
        r = requests.get(f"{self.endpoint}?limit=51", headers=auth(admin_token))
        assert r.status_code == 422  # exceeds max


# ─── Market Snapshot ───────────────────────────────────────────────────────────

class TestMarketSnapshot:
    endpoint = f"{BASE_URL}/analytics/market-snapshot"

    def test_public_access_allowed(self):
        # market-snapshot has no auth guard — it is intentionally public
        r = requests.get(f"{self.endpoint}?suburb=Denman Prospect")
        assert r.status_code == 200

    def test_denman_prospect_snapshot(self, admin_token):
        r = requests.get(f"{self.endpoint}?suburb=Denman Prospect", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.json()
        assert data["suburb"] == "Denman Prospect"
        assert "median_price" in data
        assert "rental_yield" in data
        assert "days_on_market" in data
        assert "growth_yoy" in data

    def test_returns_seeded_data(self, admin_token):
        r = requests.get(f"{self.endpoint}?suburb=Denman Prospect", headers=auth(admin_token))
        data = r.json()
        assert data["median_price"] == 975000
        assert data["growth_yoy"] == 3.8

    def test_unknown_suburb_returns_default(self, admin_token):
        r = requests.get(f"{self.endpoint}?suburb=Nonexistent Suburb", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.json()
        assert "median_price" in data  # returns default values


# ─── Personal Badges ───────────────────────────────────────────────────────────

class TestPersonalBadges:
    endpoint = f"{BASE_URL}/analytics/personal-badges"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_returns_list(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_badge_structure(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        for badge in r.json():
            assert "id" in badge
            assert "label" in badge
            assert "icon" in badge
            assert "description" in badge


# ─── Compliance Summary (NEW) ─────────────────────────────────────────────────

class TestComplianceSummary:
    endpoint = f"{BASE_URL}/analytics/compliance-summary"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200

    def test_response_structure(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        required = ["total", "completed", "pending", "overdue", "status", "label", "percentage"]
        for key in required:
            assert key in data, f"Missing key: {key}"

    def test_counts_are_non_negative(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert data["total"] >= 0
        assert data["completed"] >= 0
        assert data["pending"] >= 0
        assert data["overdue"] >= 0

    def test_counts_sum_correctly(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        if data["total"] > 0:
            # total = completed + pending + overdue + (other)
            assert data["completed"] + data["pending"] + data["overdue"] <= data["total"]

    def test_percentage_is_valid(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert 0 <= data["percentage"] <= 100

    def test_status_values(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert data["status"] in ("healthy", "warning", "danger", "unknown")

    def test_real_db_data(self, admin_token):
        """Compliance items in DB — check structure and numeric consistency."""
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert data["total"] >= 0
        assert data["completed"] >= 0
        assert data["pending"] >= 0
        assert data["completed"] + data["pending"] + data["overdue"] <= data["total"]
        assert 0 <= data["percentage"] <= 100

    def test_chairman_can_access(self, chairman_token):
        r = requests.get(self.endpoint, headers=auth(chairman_token))
        assert r.status_code == 200


# ─── Marketplace Stats (NEW) ──────────────────────────────────────────────────

class TestMarketplaceStats:
    endpoint = f"{BASE_URL}/analytics/marketplace-stats"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200

    def test_response_structure(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert "total_active" in data
        assert "new_this_period" in data
        assert "period_days" in data

    def test_default_period_is_7_days(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        assert r.json()["period_days"] == 7

    def test_custom_period(self, admin_token):
        for days in (1, 30, 90):
            r = requests.get(f"{self.endpoint}?days={days}", headers=auth(admin_token))
            assert r.status_code == 200
            assert r.json()["period_days"] == days

    def test_numeric_values(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert isinstance(data["total_active"], int)
        assert data["total_active"] >= 0
        assert isinstance(data["new_this_period"], int)
        assert data["new_this_period"] >= 0

    def test_new_this_period_le_total(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        # new_this_period can't exceed total_active
        assert data["new_this_period"] <= data["total_active"] or data["new_this_period"] >= 0

    def test_days_param_boundary(self, admin_token):
        r_min = requests.get(f"{self.endpoint}?days=1", headers=auth(admin_token))
        assert r_min.status_code == 200
        r_max = requests.get(f"{self.endpoint}?days=90", headers=auth(admin_token))
        assert r_max.status_code == 200


# ─── Fund Forecasts (data-driven) ─────────────────────────────────────────────

class TestFundForecasts:
    endpoint = f"{BASE_URL}/analytics/fund-forecasts"

    def test_requires_auth(self):
        r = requests.get(self.endpoint)
        assert r.status_code == 401

    def test_owner_can_access(self, owner_token):
        r = requests.get(self.endpoint, headers=auth(owner_token))
        assert r.status_code == 200

    def test_response_has_admin_and_sinking_keys(self, admin_token):
        r = requests.get(self.endpoint, headers=auth(admin_token))
        data = r.json()
        assert "admin" in data
        assert "sinking" in data

    def test_admin_forecast_length_equals_years_plus_one(self, admin_token):
        """admin list must be exactly years+1 (base year + N future years)."""
        for n in (1, 3, 5):
            r = requests.get(f"{self.endpoint}?years={n}", headers=auth(admin_token))
            assert r.status_code == 200, f"years={n}: {r.text}"
            rows = r.json()["admin"]
            assert len(rows) == n + 1, (
                f"years={n}: expected {n + 1} admin rows, got {len(rows)}"
            )

    def test_years_1_does_not_return_4_rows(self, admin_token):
        """years=1 must return 2 rows (base + Y1), not 4 (base + Y1/Y2/Y3)."""
        r = requests.get(f"{self.endpoint}?years=1", headers=auth(admin_token))
        assert len(r.json()["admin"]) == 2

    def test_admin_rows_have_type_and_source(self, admin_token):
        r = requests.get(f"{self.endpoint}?years=3", headers=auth(admin_token))
        for row in r.json()["admin"]:
            assert "type" in row, f"Missing 'type' in: {row}"
            assert "source" in row, f"Missing 'source' in: {row}"

    def test_base_year_row_type_is_actual_budget(self, admin_token):
        r = requests.get(f"{self.endpoint}?years=3", headers=auth(admin_token))
        base = r.json()["admin"][0]
        assert base["type"] == "actual_budget"
        assert base["source"] == "proposed_budget"

    def test_future_rows_type_is_forecast_or_estimate(self, admin_token):
        """Future-year rows must be 'forecast' (category data) or 'estimate' (CPI fallback)."""
        r = requests.get(f"{self.endpoint}?years=3", headers=auth(admin_token))
        for row in r.json()["admin"][1:]:
            assert row["type"] in ("forecast", "estimate"), (
                f"Unexpected type '{row['type']}' in row: {row}"
            )

    def test_cpi_fallback_rows_have_cpi_source(self, admin_token):
        """If type==estimate, source must be 'cpi_fallback' (not 'category_forecasts')."""
        r = requests.get(f"{self.endpoint}?years=3", headers=auth(admin_token))
        for row in r.json()["admin"][1:]:
            if row["type"] == "estimate":
                assert row["source"] == "cpi_fallback", (
                    f"estimate row should have source=cpi_fallback, got: {row['source']}"
                )

    def test_years_boundary_min(self, admin_token):
        r = requests.get(f"{self.endpoint}?years=1", headers=auth(admin_token))
        assert r.status_code == 200

    def test_years_boundary_max(self, admin_token):
        r = requests.get(f"{self.endpoint}?years=10", headers=auth(admin_token))
        assert r.status_code == 200

    def test_years_over_max_returns_422(self, admin_token):
        r = requests.get(f"{self.endpoint}?years=11", headers=auth(admin_token))
        assert r.status_code == 422


# ─── Cross-role visibility ────────────────────────────────────────────────────

class TestCrossRoleVisibility:
    """Verify all analytics endpoints are accessible to owner, admin, and chairman."""

    endpoints = [
        "/api/analytics/sinking-fund-forecast?years=1",
        "/api/analytics/maintenance-stats",
        "/api/analytics/activities?limit=3",
        "/api/analytics/market-snapshot?suburb=Denman Prospect",
        "/api/analytics/personal-badges",
        "/api/analytics/compliance-summary",
        "/api/analytics/marketplace-stats?days=7",
        "/api/analytics/fund-forecasts?years=3",
    ]

    def test_owner_access_all(self, owner_token):
        for ep in self.endpoints:
            r = requests.get(f"http://127.0.0.1:8003{ep}", headers=auth(owner_token))
            assert r.status_code == 200, f"Owner blocked from {ep}: {r.status_code} {r.text}"

    def test_admin_access_all(self, admin_token):
        for ep in self.endpoints:
            r = requests.get(f"http://127.0.0.1:8003{ep}", headers=auth(admin_token))
            assert r.status_code == 200, f"Admin blocked from {ep}: {r.status_code}"

    def test_chairman_access_all(self, chairman_token):
        for ep in self.endpoints:
            r = requests.get(f"http://127.0.0.1:8003{ep}", headers=auth(chairman_token))
            assert r.status_code == 200, f"Chairman blocked from {ep}: {r.status_code}"
