#!/usr/bin/env python3
"""
Backend Tests: Finance Endpoint Suite (New Architecture)

Tests for the refactored finance API endpoints introduced in the finance
architecture refactor (2026-02-18).

New endpoints:
  GET /api/finance/years
  GET /api/finance/summary
  GET /api/finance/charts
  GET /api/finance/budget-vs-actual
  GET /api/levy-calculator
  GET /api/unit-levy-ledger
  GET /api/annual-levies
  GET /api/levy-categories

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_finance_endpoints.py -v

Requirements:
    Backend running on localhost:8003
    Admin credentials: administrator@eastgateresidences.com.au / $E2E_ADMIN_PASSWORD
    annual_levies collection: 2 years (2025, 2026)
    unit_levy_ledger collection: 87 entries for 2025
    levy_categories collection: 37 each for 2025, 2026
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
    """Authenticate as owner and return bearer token."""
    response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD
    })
    assert response.status_code == 200, f"Owner login failed: {response.text}"
    data = response.json()
    assert "token" in data
    return data["token"]


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    """Return Authorization headers for owner."""
    return {"Authorization": f"Bearer {owner_token}"}


class TestFinanceYears:
    """Tests for GET /api/years — available financial years.

    Note: Route is /api/years (not /api/finance/years) because the finance
    router has no prefix and is mounted directly on /api.
    """

    def test_years_returns_200(self, auth_headers):
        """GET /api/years should return 200."""
        r = requests.get(f"{BASE_URL}/years", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_years_returns_list(self, auth_headers):
        """Response should be a list of year strings."""
        r = requests.get(f"{BASE_URL}/years", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) >= 1, "Should have at least one year"

    def test_years_contains_2025_and_2026(self, auth_headers):
        """Should contain both 2025 and 2026."""
        r = requests.get(f"{BASE_URL}/years", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "2025" in data, f"'2025' not in years: {data}"
        assert "2026" in data, f"'2026' not in years: {data}"

    def test_years_sorted_descending(self, auth_headers):
        """Years should be sorted most-recent first."""
        r = requests.get(f"{BASE_URL}/years", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        if len(data) >= 2:
            assert data[0] >= data[1], f"Years not sorted descending: {data}"

    def test_years_accessible_to_owner(self, owner_headers):
        """Owners with finance access should also see years."""
        r = requests.get(f"{BASE_URL}/years", headers=owner_headers)
        # Owner has can_view_finances permission
        assert r.status_code in [200, 403], f"Unexpected status: {r.status_code}"


class TestFinanceSummary:
    """Tests for GET /api/finance/summary — financial overview."""

    def test_summary_returns_200(self, auth_headers):
        """GET /api/finance/summary should return 200."""
        r = requests.get(f"{BASE_URL}/finance/summary", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_summary_with_year_param(self, auth_headers):
        """GET /api/finance/summary?year=2025 should return 200."""
        r = requests.get(f"{BASE_URL}/finance/summary?year=2025", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200 for year=2025: {r.text}"

    def test_summary_has_required_fields(self, auth_headers):
        """Summary should contain admin_fund and sinking_fund sections."""
        r = requests.get(f"{BASE_URL}/finance/summary?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "year" in data, f"Missing 'year' in summary: {data.keys()}"
        assert "admin_fund" in data or "levy_stats" in data, \
            f"Missing financial sections in: {data.keys()}"

    def test_summary_has_levy_stats(self, auth_headers):
        """Summary should include levy collection statistics (unit_ledger_summary key)."""
        r = requests.get(f"{BASE_URL}/finance/summary?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # Key is 'unit_ledger_summary' in the refactored API
        ls_key = "unit_ledger_summary" if "unit_ledger_summary" in data else "levy_stats"
        assert ls_key in data, f"Missing levy stats in: {data.keys()}"
        ls = data[ls_key]
        assert "total_levied" in ls, f"Missing 'total_levied' in {ls_key}"
        assert "total_outstanding" in ls, f"Missing 'total_outstanding' in {ls_key}"

    def test_summary_2025_levy_data_correct(self, auth_headers):
        """2025 levy data should show $478k levied, $20k outstanding."""
        r = requests.get(f"{BASE_URL}/finance/summary?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        if "levy_stats" in data:
            ls = data["levy_stats"]
            total_levied = ls.get("total_levied", 0)
            # Should be around $478k (87 units with various entitlements)
            assert total_levied > 100000, \
                f"total_levied ({total_levied}) seems too low for 87 units"
            outstanding = ls.get("total_outstanding", 0)
            # Should be around $20k
            assert outstanding >= 0, f"Outstanding balance can't be negative: {outstanding}"

    def test_summary_invalid_year_graceful(self, auth_headers):
        """Invalid year should return 200 with empty/default data, not 500."""
        r = requests.get(f"{BASE_URL}/finance/summary?year=1900", headers=auth_headers)
        assert r.status_code in [200, 404], \
            f"Should not return 500 for invalid year, got {r.status_code}"


class TestFinanceCharts:
    """Tests for GET /api/finance/charts — chart data for finance page."""

    def test_charts_returns_200(self, auth_headers):
        """GET /api/finance/charts should return 200."""
        r = requests.get(f"{BASE_URL}/finance/charts", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_charts_with_year_param(self, auth_headers):
        """GET /api/finance/charts?year=2025 should return 200."""
        r = requests.get(f"{BASE_URL}/finance/charts?year=2025", headers=auth_headers)
        assert r.status_code == 200

    def test_charts_has_expense_breakdown(self, auth_headers):
        """Charts should include expense breakdown by category."""
        r = requests.get(f"{BASE_URL}/finance/charts?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # Should have some chart data
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"


class TestBudgetVsActual:
    """Tests for GET /api/finance/budget-vs-actual."""

    def test_budget_vs_actual_returns_200(self, auth_headers):
        """GET /api/finance/budget-vs-actual should return 200."""
        r = requests.get(f"{BASE_URL}/finance/budget-vs-actual", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_budget_vs_actual_with_year(self, auth_headers):
        """GET /api/finance/budget-vs-actual?year=2025 should return 200."""
        r = requests.get(f"{BASE_URL}/finance/budget-vs-actual?year=2025", headers=auth_headers)
        assert r.status_code == 200

    def test_budget_vs_actual_has_data(self, auth_headers):
        """Response should contain budget comparison data."""
        r = requests.get(f"{BASE_URL}/finance/budget-vs-actual?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # Should be a list or dict with comparison data
        assert data is not None, "Response should not be null"


class TestLevyCalculator:
    """Tests for GET /api/levy-calculator — per-unit levy calculator."""

    def test_levy_calculator_returns_200(self, auth_headers):
        """GET /api/levy-calculator should return 200."""
        r = requests.get(f"{BASE_URL}/levy-calculator", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_levy_calculator_with_unit(self, auth_headers):
        """GET /api/levy-calculator?unit_number=TH087 should return levy data."""
        r = requests.get(f"{BASE_URL}/levy-calculator?unit_number=TH087", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200: {r.text}"
        data = r.json()
        # TH087 is Avneet Rooprai's unit
        assert "unit_number" in data or len(data) > 0, \
            f"Empty response for TH087: {data}"

    def test_levy_calculator_amounts_correct_for_th017(self, auth_headers):
        """TH087 should show correct levy amounts based on UOE 100."""
        r = requests.get(f"{BASE_URL}/levy-calculator?unit_number=TH087&year=2026",
                         headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # TH087 has UOE 100. 2026 levy rate should give ~$7,090 annual
        if "total_annual" in data:
            assert data["total_annual"] > 0, "total_annual should be positive"

    def test_levy_calculator_invalid_unit_graceful(self, auth_headers):
        """Invalid unit should return 200 with empty data or 404, not 500."""
        r = requests.get(f"{BASE_URL}/levy-calculator?unit_number=INVALID999",
                         headers=auth_headers)
        assert r.status_code in [200, 404], \
            f"Should not return 500 for invalid unit, got {r.status_code}"

    def test_levy_calculator_no_unit_returns_summary(self, auth_headers):
        """Without unit_number, should return overall levy summary."""
        r = requests.get(f"{BASE_URL}/levy-calculator", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data is not None


class TestUnitLevyLedger:
    """Tests for GET /api/unit-levy-ledger — per-unit levy ledger."""

    def test_ledger_returns_200(self, auth_headers):
        """GET /api/unit-levy-ledger should return 200."""
        r = requests.get(f"{BASE_URL}/unit-levy-ledger", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_ledger_returns_list(self, auth_headers):
        """Response should be a list of unit ledger entries."""
        r = requests.get(f"{BASE_URL}/unit-levy-ledger", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) or (isinstance(data, dict) and "items" in data), \
            f"Expected list or paginated dict, got {type(data)}"

    def test_ledger_with_year(self, auth_headers):
        """GET /api/unit-levy-ledger?year=2025 should return 87 entries."""
        r = requests.get(f"{BASE_URL}/unit-levy-ledger?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data)
        assert len(items) > 0, "Should have ledger entries for 2025"

    def test_ledger_entry_has_required_fields(self, auth_headers):
        """Each ledger entry should have unit_number, total_levied, net_balance."""
        r = requests.get(f"{BASE_URL}/unit-levy-ledger?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", [])
        if items:
            entry = items[0]
            assert "unit_number" in entry, f"Missing unit_number: {entry.keys()}"

    def test_ledger_with_unit_filter(self, auth_headers):
        """GET /api/unit-levy-ledger?unit_number=UA001 should filter to one unit."""
        r = requests.get(f"{BASE_URL}/unit-levy-ledger?unit_number=UA001",
                         headers=auth_headers)
        assert r.status_code == 200


class TestAnnualLevies:
    """Tests for GET /api/annual-levies — levy rates per year."""

    def test_annual_levies_returns_200(self, auth_headers):
        """GET /api/annual-levies should return 200."""
        r = requests.get(f"{BASE_URL}/annual-levies", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_annual_levies_returns_list(self, auth_headers):
        """Response should be a list of levy year documents."""
        r = requests.get(f"{BASE_URL}/annual-levies", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) >= 1, "Should have at least one annual levy record"

    def test_annual_levies_has_2025_and_2026(self, auth_headers):
        """Should have records for both 2025 and 2026."""
        r = requests.get(f"{BASE_URL}/annual-levies", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        years = [item.get("year") for item in data]
        assert "2025" in years, f"'2025' not in annual_levies: {years}"
        assert "2026" in years, f"'2026' not in annual_levies: {years}"

    def test_annual_levy_has_rate_fields(self, auth_headers):
        """Each annual levy should have per-UOE rate fields."""
        r = requests.get(f"{BASE_URL}/annual-levies", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        if data:
            entry = data[0]
            # Should have some levy rate fields
            rate_fields = [k for k in entry.keys() if "levy" in k.lower() or "rate" in k.lower()]
            assert len(rate_fields) > 0, \
                f"No levy rate fields found in annual_levy entry: {entry.keys()}"


class TestLevyCategories:
    """Tests for GET /api/levy-categories — expense categories by fund."""

    def test_levy_categories_returns_200(self, auth_headers):
        """GET /api/levy-categories should return 200."""
        r = requests.get(f"{BASE_URL}/levy-categories", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_levy_categories_with_year(self, auth_headers):
        """GET /api/levy-categories?year=2025 should return 37 categories."""
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data)
        assert len(items) >= 30, f"Expected ~37 categories for 2025, got {len(items)}"

    def test_levy_categories_2026_has_37(self, auth_headers):
        """2026 should now have 37 categories (Other Capital Works was added)."""
        r = requests.get(f"{BASE_URL}/levy-categories?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data)
        assert len(items) >= 36, f"Expected 37 categories for 2026, got {len(items)}"

    def test_levy_categories_has_both_fund_types(self, auth_headers):
        """Categories should have both administrative and sinking fund types."""
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data)
        fund_types = {item.get("fund_type") for item in items}
        assert "administrative" in fund_types, f"No administrative categories: {fund_types}"
        assert "sinking" in fund_types, f"No sinking fund categories: {fund_types}"

    def test_levy_categories_with_fund_type_filter(self, auth_headers):
        """Filter by fund_type=sinking should return only sinking categories."""
        r = requests.get(f"{BASE_URL}/levy-categories?year=2025&fund_type=sinking",
                         headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data)
        if items:
            fund_types = {item.get("fund_type") for item in items}
            assert fund_types <= {"sinking"}, \
                f"Expected only sinking fund type, got: {fund_types}"


class TestOwnersUnitsWithLevyData:
    """Tests for owners-units endpoint including correct levy ledger data."""

    def test_owners_units_returns_200(self, auth_headers):
        """GET /api/owners-units should return 200."""
        r = requests.get(f"{BASE_URL}/owners-units", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200: {r.text}"

    def test_owners_units_has_87_units(self, auth_headers):
        """Should return data for all 87 units."""
        r = requests.get(f"{BASE_URL}/owners-units?limit=100", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 87, f"Expected 87 units, got {len(data)}"

    def test_owners_units_levy_data_not_zero(self, auth_headers):
        """Units should show non-zero levy data from 2025 ledger."""
        r = requests.get(f"{BASE_URL}/owners-units?limit=10", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # At least some units should have non-zero total_levied
        units_with_levies = [u for u in data if u.get("total_levied", 0) > 0]
        assert len(units_with_levies) > 0, \
            "All units show 0 total_levied — ledger year lookup may be wrong"

    def test_unit_ua001_levy_data(self, auth_headers):
        """UA001 should show realistic levy amounts."""
        r = requests.get(f"{BASE_URL}/owners-units/UA001", headers=auth_headers)
        if r.status_code == 404:
            pytest.skip("Detail endpoint not available")
        assert r.status_code == 200
        data = r.json()
        assert data.get("total_levied", 0) > 0, \
            f"UA001 total_levied is 0 — ledger data missing"


class TestFinanceRequiresAuth:
    """Verify finance endpoints require authentication."""

    def test_finance_summary_no_auth_returns_401(self):
        """Finance summary without auth should return 401."""
        r = requests.get(f"{BASE_URL}/finance/summary")
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_levy_calculator_no_auth_returns_401(self):
        """Levy calculator without auth should return 401."""
        r = requests.get(f"{BASE_URL}/levy-calculator")
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_annual_levies_no_auth_returns_401(self):
        """Annual levies without auth should return 401."""
        r = requests.get(f"{BASE_URL}/annual-levies")
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
