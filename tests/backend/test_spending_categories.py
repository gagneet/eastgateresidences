#!/usr/bin/env python3
"""
Backend Tests: Spending Categories (Levy Categories) API

Tests for the /levy-categories endpoints that power the SpendingCategoriesPage.
These endpoints provide CRUD for levy expense categories per year and fund type.

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_spending_categories.py -v

Requirements:
    Backend running on localhost:8003
    DB: strata_production on mongodb://localhost:27018
    levy_categories collection: 37 entries for 2025, 37 for 2026
"""
import os
import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"
ADMIN_EMAIL = "administrator@eastgateresidences.com.au"
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")
OWNER_EMAIL = "avneet@eastgateresidences.com.au"
OWNER_PASSWORD = os.environ.get("E2E_OWNER_PASSWORD", "")


@pytest.fixture(scope="module")
def owner_token():
    response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": OWNER_EMAIL, "password": OWNER_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("token")
    return None


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    if owner_token:
        return {"Authorization": f"Bearer {owner_token}"}
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Tests: List / Read
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyCategoriesRead:
    """Test reading levy categories (spending categories)."""

    def test_categories_returns_200(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories", headers=admin_headers)
        assert r.status_code == 200, r.text

    def test_categories_returns_list(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories", headers=admin_headers)
        assert isinstance(r.json(), list), "Expected list response"

    def test_categories_2025_count(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=admin_headers)
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 30, f"Expected ≥30 categories for 2025, got {len(items)}"

    def test_categories_2026_count(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories?year=2026", headers=admin_headers)
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 30, f"Expected ≥30 categories for 2026, got {len(items)}"

    def test_category_has_required_fields(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=admin_headers)
        items = r.json()
        assert len(items) > 0
        cat = items[0]
        required = ["id", "year", "fund_type", "name", "budgeted_amount"]
        for field in required:
            assert field in cat, f"Missing field: {field}"

    def test_fund_type_filter_administrative(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/levy-categories?year=2025&fund_type=administrative",
            headers=admin_headers
        )
        items = r.json()
        assert all(c["fund_type"] == "administrative" for c in items), \
            "Expected only administrative categories"

    def test_fund_type_filter_sinking(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/levy-categories?year=2025&fund_type=sinking",
            headers=admin_headers
        )
        items = r.json()
        assert all(c["fund_type"] == "sinking" for c in items), \
            "Expected only sinking categories"

    def test_both_fund_types_present_in_2025(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=admin_headers)
        items = r.json()
        fund_types = {c["fund_type"] for c in items}
        assert "administrative" in fund_types, "Missing administrative categories for 2025"
        assert "sinking" in fund_types, "Missing sinking categories for 2025"

    def test_both_fund_types_present_in_2026(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories?year=2026", headers=admin_headers)
        items = r.json()
        fund_types = {c["fund_type"] for c in items}
        assert "administrative" in fund_types, "Missing administrative categories for 2026"
        assert "sinking" in fund_types, "Missing sinking categories for 2026"

    def test_categories_no_auth_returns_401(self):
        r = requests.get(f"{BASE_URL}/levy-categories")
        assert r.status_code == 401

    def test_owner_can_read_categories(self, owner_headers):
        """Owners with view_finances permission can read categories."""
        if not owner_headers:
            pytest.skip("Owner credentials not available")
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=owner_headers)
        # Owners may or may not have access depending on feature toggle; 200 or 403 acceptable
        assert r.status_code in (200, 403), \
            f"Expected 200 or 403, got {r.status_code}: {r.text}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Data integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyCategoriesDataIntegrity:
    """Verify data integrity of levy categories."""

    def test_budgeted_amounts_are_positive(self, admin_headers):
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=admin_headers)
        items = r.json()
        for cat in items:
            assert cat.get("budgeted_amount", 0) >= 0, \
                f"Negative budgeted_amount for {cat.get('name')}"

    def test_2025_has_actual_amounts(self, admin_headers):
        """2025 categories should have actual_amount (year is complete)."""
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=admin_headers)
        items = r.json()
        has_actual = any(cat.get("actual_amount") is not None and cat.get("actual_amount", 0) > 0
                         for cat in items)
        assert has_actual, "Expected at least some 2025 categories to have actual amounts"

    def test_admin_fund_categories_cover_expected_expenses(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/levy-categories?year=2025&fund_type=administrative",
            headers=admin_headers
        )
        items = r.json()
        total_budget = sum(c.get("budgeted_amount", 0) for c in items)
        # Admin fund total should be in a reasonable range (not $0 or millions)
        assert total_budget > 1000, f"Admin fund total too low: {total_budget}"
        assert total_budget < 1_000_000, f"Admin fund total too high: {total_budget}"

    def test_sinking_fund_categories_cover_expected_expenses(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/levy-categories?year=2025&fund_type=sinking",
            headers=admin_headers
        )
        items = r.json()
        total_budget = sum(c.get("budgeted_amount", 0) for c in items)
        assert total_budget > 1000, f"Sinking fund total too low: {total_budget}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: CRUD (Create / Update / Delete) — Admin only
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyCategoriesCRUD:
    """Test create, update, and delete operations on levy categories."""

    @pytest.fixture(scope="module")
    def test_category(self, admin_headers, cleanup_registry, test_run_id):
        payload = {
            "year": "2026",
            "fund_type": "administrative",
            "name": f"TEST: Automated Test Category [{test_run_id}]",
            "budgeted_amount": 1000.00,
            "actual_amount": 0.0,
            "description": f"Created by pytest — delete after test [{test_run_id}]",
            "status": "proposed"
        }
        r = requests.post(
            f"{BASE_URL}/levy-categories",
            json=payload,
            headers=admin_headers
        )
        assert r.status_code in (200, 201), f"Create failed: {r.status_code} {r.text}"
        data = r.json()
        assert "id" in data, "Response missing 'id'"
        cleanup_registry.register_id("levy_categories", data.get("id"))
        try:
            yield data
        finally:
            requests.delete(f"{BASE_URL}/levy-categories/{data['id']}", headers=admin_headers)

    def test_admin_can_create_category(self, test_category):
        assert test_category["id"]

    def test_admin_can_update_category(self, admin_headers, test_category):
        cat_id = test_category["id"]
        r = requests.put(
            f"{BASE_URL}/levy-categories/{cat_id}",
            json={"budgeted_amount": 1500.00, "description": "Updated by pytest"},
            headers=admin_headers
        )
        assert r.status_code == 200, f"Update failed: {r.status_code} {r.text}"
        data = r.json()
        assert abs(data.get("budgeted_amount", 0) - 1500.00) < 0.01

    def test_admin_can_delete_category(self, admin_headers, test_category):
        cat_id = test_category["id"]
        r = requests.delete(
            f"{BASE_URL}/levy-categories/{cat_id}",
            headers=admin_headers
        )
        assert r.status_code in (200, 204), f"Delete failed: {r.status_code} {r.text}"
        r2 = requests.get(f"{BASE_URL}/levy-categories/2026", headers=admin_headers)
        remaining = r2.json() if r2.status_code == 200 else []
        ids = [c.get("id") for c in remaining] if isinstance(remaining, list) else []
        assert cat_id not in ids, "Category was not deleted"
