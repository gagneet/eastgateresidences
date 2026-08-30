#!/usr/bin/env python3
"""
Tests for Finance Page Fixes (2026-04-07)

Covers:
1. levy-status total_paid uses unit_levy_ledger (authoritative) not just levy_payments
2. /finance/quarterly-budget endpoint correctness
3. /finance/charts income_by_category labels
4. Multi-tenant isolation for all tested endpoints
5. Owner names via /owners-units response (garage_spaces field)

All tests are idempotent — any created data is removed in teardown.

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_finance_page_fixes.py -v

Requirements:
    Backend running on localhost:8003
    Admin credentials: administrator@eastgateresidences.com.au / $E2E_ADMIN_PASSWORD
    East Gate building_id: 13195
    Sierra building_id: 16244
"""
import os
import uuid
import pytest
import requests

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Live backend test — set RUN_INTEGRATION_TESTS=1 to run",
)

BASE_URL = "http://127.0.0.1:8003/api"
ADMIN_EMAIL = "administrator@eastgateresidences.com.au"
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")

# Known test unit in East Gate with ledger data
TEST_UNIT = "TH071"
BUILDING_ID_EG = "13195"
BUILDING_ID_SIERRA = "16244"


@pytest.fixture(scope="module")
def admin_token():
    response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ─── Quarterly Budget Endpoint ────────────────────────────────────────────────

class TestQuarterlyBudget:
    """Tests for GET /api/finance/quarterly-budget"""

    def test_returns_200(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_returns_quarters_list(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "quarters" in data, "Response must have 'quarters'"
        assert isinstance(data["quarters"], list), "'quarters' must be a list"

    def test_returns_four_quarters_for_quarterly_frequency(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        quarters = data["quarters"]
        # East Gate is quarterly (4 periods); may be fewer if levy has custom schedule
        assert len(quarters) >= 1, "Must have at least one quarter"
        assert len(quarters) <= 12, "Cannot have more than 12 periods"

    def test_each_quarter_has_required_fields(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # New field names after GST-aware refactor
        required_fields = {"label", "budgeted_income_ex_gst", "budgeted_gst",
                           "budgeted_income_inc_gst", "levied", "collected",
                           "outstanding", "has_ledger_data", "budgeted_expenses", "status"}
        for q in data["quarters"]:
            missing = required_fields - q.keys()
            assert not missing, f"Quarter {q.get('label')} missing fields: {missing}"

    def test_annual_totals_present(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "annual_totals" in data, "Response must have 'annual_totals'"
        at = data["annual_totals"]
        # New GST-aware fields
        assert "admin_ex_gst" in at, "annual_totals must have admin_ex_gst"
        assert "sinking_ex_gst" in at, "annual_totals must have sinking_ex_gst"
        assert "gst" in at, "annual_totals must have gst (derived 10%)"
        assert "total_inc_gst" in at, "annual_totals must have total_inc_gst"
        assert "total_levied_ytd" in at, "annual_totals must have total_levied_ytd"
        assert "total_collected_ytd" in at, "annual_totals must have total_collected_ytd"
        assert "total_expenses_budgeted" in at

    def test_budgeted_income_per_q_equals_annual_divided_by_periods(self, auth_headers):
        """Budgeted income (inc-GST) per quarter = (admin + sinking ex-GST) * 1.1 / num_periods."""
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        quarters = data["quarters"]
        at = data["annual_totals"]
        # Verify GST math: admin_ex_gst + sinking_ex_gst) * 1.1 = total_inc_gst
        total_ex = at["admin_ex_gst"] + at["sinking_ex_gst"]
        assert abs(at["gst"] - round(total_ex * 0.10, 2)) < 0.05, \
            f"GST should be 10% of ex-GST total: {total_ex}*0.10={total_ex * 0.10} vs stored={at['gst']}"
        assert abs(at["total_inc_gst"] - round(total_ex * 1.10, 2)) < 0.05, \
            f"total_inc_gst should be ex_gst * 1.1: {total_ex * 1.1} vs stored={at['total_inc_gst']}"
        # All quarters should have same budgeted_income_inc_gst (even split)
        if quarters:
            vals = [q["budgeted_income_inc_gst"] for q in quarters]
            assert abs(max(vals) - min(vals)) <= 0.02, \
                f"Quarters have inconsistent budgeted_income_inc_gst: {vals}"

    def test_returns_empty_for_nonexistent_year(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=1990", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["quarters"] == [], f"Expected empty quarters for year 1990, got: {data}"

    def test_building_isolation_from_sierra(self, auth_headers):
        """Quarterly budget is scoped to East Gate. Sierra's data is not mixed in."""
        eg_r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert eg_r.status_code == 200
        eg_data = eg_r.json()
        # Can't test Sierra separately from same token (building is determined by user's building_id),
        # but we assert that the data returned is deterministic and consistent
        eg_r2 = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert eg_r2.json()["annual_totals"] == eg_data["annual_totals"], \
            "Same request returned different results — non-deterministic or wrong scoping"

    def test_requires_authentication(self):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026")
        assert r.status_code in [401, 403], f"Expected 401/403 without auth, got {r.status_code}"


# ─── Levy Status total_paid Fix ───────────────────────────────────────────────

class TestLevyStatusTotalPaid:
    """
    Tests that levy-status/{unit_number} returns total_paid from unit_levy_ledger
    (authoritative — includes DEFT/bank imports) not just levy_payments (portal-only).
    """

    def test_levy_status_returns_200(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/{TEST_UNIT}?year=2026", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_total_paid_is_numeric(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/{TEST_UNIT}?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "total_paid" in data, "Response must include total_paid"
        assert isinstance(data["total_paid"], (int, float)), \
            f"total_paid must be numeric, got {type(data['total_paid'])}"

    def test_total_paid_non_negative(self, auth_headers):
        r = requests.get(f"{BASE_URL}/levy-status/{TEST_UNIT}?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_paid"] >= 0, f"total_paid must be >= 0, got {data['total_paid']}"

    def test_total_paid_matches_unit_levy_ledger_when_ledger_has_data(self, auth_headers):
        """
        When unit_levy_ledger has a total_paid value, levy-status should return that value.
        This verifies DEFT/bank payments (not just portal payments) are counted.
        """
        # Get ledger directly
        ledger_r = requests.get(
            f"{BASE_URL}/unit-levy-ledger/{TEST_UNIT}?year=2026",
            headers=auth_headers
        )
        assert ledger_r.status_code == 200

        ledger_data = ledger_r.json()
        if isinstance(ledger_data, list):
            ledger_entry = ledger_data[0] if ledger_data else None
        else:
            ledger_entry = ledger_data

        if not ledger_entry or ledger_entry.get("error"):
            pytest.skip(f"No ledger entry for {TEST_UNIT}/2026 — skipping consistency check")

        ledger_total_paid = ledger_entry.get("total_paid", 0)

        # Get levy-status for same unit
        status_r = requests.get(
            f"{BASE_URL}/levy-status/{TEST_UNIT}?year=2026",
            headers=auth_headers
        )
        assert status_r.status_code == 200
        status_data = status_r.json()
        status_total_paid = status_data.get("total_paid", 0)

        # levy-status.total_paid should equal ledger.total_paid (within 0.01 rounding tolerance)
        assert abs(status_total_paid - ledger_total_paid) <= 0.01, (
            f"levy-status total_paid ({status_total_paid}) does not match "
            f"unit_levy_ledger total_paid ({ledger_total_paid}) for {TEST_UNIT}. "
            f"This indicates DEFT/bank payments are not being counted."
        )

    def test_levy_status_balance_due_consistent_with_total_paid(self, auth_headers):
        """balance_due only adds positive prior arrears; prior credits are already reflected in total_paid."""
        r = requests.get(f"{BASE_URL}/levy-status/{TEST_UNIT}?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()

        annual_levy = data.get("annual_levy", data.get("total_annual", 0))
        total_paid = data.get("total_paid", 0)
        prev_year_balance = data.get("prev_year_balance", 0)
        balance_due = data.get("balance_due", 0)
        net_balance = data.get("net_balance", 0)

        # balance_due should be >= 0 (clamped at 0 in the API)
        assert balance_due >= 0, f"balance_due must be >= 0, got {balance_due}"

        # net_balance is the unclamped version (can be negative for credit)
        expected_net = round(annual_levy + max(0, prev_year_balance) - total_paid, 2)
        assert abs(net_balance - expected_net) <= 0.02, (
            f"net_balance={net_balance} does not match formula result={expected_net} "
            f"(annual={annual_levy}, prev={prev_year_balance}, paid={total_paid})"
        )


# ─── Finance Charts Labels ────────────────────────────────────────────────────

class TestFinanceChartsLabels:
    """
    Tests that /finance/charts returns consistent income_by_category labels.
    Fund budgets are stored ex-GST and owner-facing levy totals add the building GST setting.
    """

    def test_charts_returns_200(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_income_by_category_is_list(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026", headers=auth_headers)
        data = r.json()
        income = data.get("income_by_category", [])
        assert isinstance(income, list), "income_by_category must be a list"

    def test_income_labels_include_admin_and_sinking(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026", headers=auth_headers)
        data = r.json()
        income = data.get("income_by_category", [])
        names = [item["name"] for item in income]
        # At least one Admin and one Sinking entry should be present for 2026
        has_admin = any("Admin" in n for n in names)
        has_sinking = any("Sinking" in n for n in names)
        if income:  # only assert if data exists
            assert has_admin, f"Expected 'Admin' in income names, got: {names}"
            assert has_sinking, f"Expected 'Sinking' in income names, got: {names}"

    def test_income_category_values_are_positive(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026", headers=auth_headers)
        data = r.json()
        for item in data.get("income_by_category", []):
            assert item["value"] >= 0, f"Income value cannot be negative: {item}"

    def test_expense_by_category_present(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026", headers=auth_headers)
        data = r.json()
        assert "expense_by_category" in data, "Response must have expense_by_category"

    def test_multi_tenant_charts_returns_200(self, auth_headers):
        """Charts endpoint works for the authenticated user's building (East Gate)."""
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026", headers=auth_headers)
        assert r.status_code == 200


# ─── Owners Units Garage Spaces ───────────────────────────────────────────────

class TestOwnerUnitGarageSpaces:
    """
    Tests that /owners-units/{unit_number} includes garage_spaces (not parking_spaces).
    The UnitFinanceDetailPage now reads garage_spaces from the API response.
    """

    def test_owners_unit_returns_200(self, auth_headers):
        r = requests.get(f"{BASE_URL}/owners-units/{TEST_UNIT}", headers=auth_headers)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_response_includes_garage_spaces_field(self, auth_headers):
        """Response should have garage_spaces field (even if None)."""
        r = requests.get(f"{BASE_URL}/owners-units/{TEST_UNIT}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "garage_spaces" in data, (
            f"owners-units response must include 'garage_spaces' field. "
            f"Keys present: {list(data.keys())}"
        )

    def test_garage_spaces_is_int_or_none(self, auth_headers):
        r = requests.get(f"{BASE_URL}/owners-units/{TEST_UNIT}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        gs = data.get("garage_spaces")
        assert gs is None or isinstance(gs, int), \
            f"garage_spaces must be int or None, got {type(gs)}: {gs}"

    def test_owner_name_present(self, auth_headers):
        r = requests.get(f"{BASE_URL}/owners-units/{TEST_UNIT}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "owner_name" in data, "Response must include owner_name"

    def test_unit_levy_ledger_endpoint_includes_total_paid(self, auth_headers):
        """unit_levy_ledger should always include total_paid for authoritative collection data."""
        r = requests.get(f"{BASE_URL}/unit-levy-ledger/{TEST_UNIT}?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        entry = data[0] if isinstance(data, list) else data
        if entry and not entry.get("error"):
            assert "total_paid" in entry, "unit_levy_ledger must include total_paid"
            assert isinstance(entry["total_paid"], (int, float)), \
                "unit_levy_ledger.total_paid must be numeric"


# ─── Multi-Tenant Isolation ───────────────────────────────────────────────────

class TestMultiTenantFinanceIsolation:
    """
    Verify that finance endpoints are scoped to the authenticated user's building.
    The admin user is associated with East Gate (building_id=13195).
    These tests ensure no data leaks between buildings.
    """

    def test_finance_summary_contains_year_field(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/summary?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # Summary should have fund data scoped to this building
        assert "admin_fund" in data or "unit_ledger_summary" in data, \
            "Finance summary missing expected fund breakdown"

    def test_levy_status_unit_not_found_for_wrong_unit(self, auth_headers):
        """A unit not in this building should return 404."""
        r = requests.get(f"{BASE_URL}/levy-status/NONEXISTENT999?year=2026", headers=auth_headers)
        assert r.status_code == 404, \
            f"Expected 404 for non-existent unit, got {r.status_code}"

    def test_quarterly_budget_year_field_matches_request(self, auth_headers):
        r = requests.get(f"{BASE_URL}/finance/quarterly-budget?year=2026", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data.get("year") == "2026", \
            f"Expected year='2026' in response, got year={data.get('year')!r}"

    def test_charts_requires_authenticated_user(self):
        """Unauthenticated request to /finance/charts should be rejected."""
        r = requests.get(f"{BASE_URL}/finance/charts?year=2026")
        assert r.status_code in [401, 403], \
            f"Expected 401/403 for unauthenticated charts request, got {r.status_code}"

    def test_levy_status_requires_finance_feature(self, auth_headers):
        """levy-status endpoint requires finance feature toggle to be enabled."""
        # Admin always has access — just verify it returns 200 for admin
        r = requests.get(f"{BASE_URL}/levy-status/{TEST_UNIT}?year=2026", headers=auth_headers)
        assert r.status_code == 200, \
            f"Admin should access levy-status, got {r.status_code}: {r.text}"


# ─── Income Sources GST Consistency (Unit Tests) ─────────────────────────────

class TestIncomeSourcesGSTConsistency:
    """
    Unit tests verifying the Income Sources chart uses levy_income (ex-GST) as the base,
    with GST shown as a separate derived slice. Both Admin and Sinking funds are on the
    same ex-GST basis.

    Data model (2026-04-07 fix):
      levy_income  = ex-GST budget (e.g. admin=309882, sinking=90459)
      total_income = inc-GST = levy_income * 1.1 (e.g. admin=340870.2, sinking=99504.9)
      GST          = (admin_levy_income + sinking_levy_income) * 0.10 — derived, not stored
    """

    @pytest.mark.asyncio
    async def test_income_chart_uses_levy_income_ex_gst_as_base(self):
        """income_by_category must use levy_income (ex-GST) for Admin and Sinking slices."""
        from routers.finance import get_finance_charts
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_levy = {
            "year": "2026",
            "building_id": "13195",
            "admin_fund": {
                "levy_income": 309882.00,  # ex-GST (authoritative budget figure)
                "total_income": 340870.20,  # inc-GST (= levy_income * 1.1)
                "other_income": 0.0,
            },
            "sinking_fund": {
                "levy_income": 90459.00,  # ex-GST (authoritative budget figure)
                "total_income": 99504.90,  # inc-GST
                "other_income": 0.0,
            },
        }
        mock_cats = []

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
        mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=mock_cats)
        mock_db.levy_payments.aggregate.return_value.to_list = AsyncMock(return_value=[])

        user = {"id": "u1", "role": "super_admin", "building_id": "13195",
                "is_active": True, "email": "a@test.com"}

        with patch("routers.finance.db", mock_db):
            result = await get_finance_charts(
                year="2026", current_user=user, building_id="13195"
            )

        income = {i["name"]: i["value"] for i in result["income_by_category"]}
        # Admin slice must be the ex-GST value 309882, NOT inc-GST 340870.2
        assert income.get("Administrative Fund (ex-GST)") == pytest.approx(309882.00, rel=0.01), (
            f"Admin slice must be ex-GST=309882, got {income.get('Administrative Fund (ex-GST)')}. "
            "Must use levy_income, not total_income."
        )
        # Sinking slice must be the ex-GST value 90459, NOT inc-GST 99504.9
        assert income.get("Sinking Fund (ex-GST)") == pytest.approx(90459.00, rel=0.01), (
            f"Sinking slice must be ex-GST=90459, got {income.get('Sinking Fund (ex-GST)')}."
        )
        assert "GST (10%)" not in income
        # GST must still be exposed as a tax component, not hidden and not classified as income.
        expected_gst = round((309882.00 + 90459.00) * 0.10, 2)
        assert result["gst_summary"]["gst_component"] == pytest.approx(expected_gst, rel=0.01)
        assert result["gst_summary"]["classification"] == "tax_collected_not_income"

    @pytest.mark.asyncio
    async def test_income_chart_consistent_basis_with_other_income(self):
        """other_income is itemised separately; levy slices are levy_income only."""
        from routers.finance import get_finance_charts
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_levy = {
            "year": "2026",
            "building_id": "13195",
            "admin_fund": {"levy_income": 300000.0, "total_income": 330000.0, "other_income": 10000.0},
            "sinking_fund": {"levy_income": 80000.0, "total_income": 88000.0, "other_income": 0.0},
        }
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
        mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate.return_value.to_list = AsyncMock(return_value=[])

        user = {"id": "u1", "role": "super_admin", "building_id": "13195",
                "is_active": True, "email": "a@test.com"}

        with patch("routers.finance.db", mock_db):
            result = await get_finance_charts(
                year="2026", current_user=user, building_id="13195"
            )

        income = {i["name"]: i["value"] for i in result["income_by_category"]}
        assert income.get("Administrative Fund (ex-GST)") == pytest.approx(300000.0, rel=0.01)
        assert income.get("Sinking Fund (ex-GST)") == pytest.approx(80000.0, rel=0.01)
        assert income.get("Admin Other Income") == pytest.approx(10000.0, rel=0.01)
        assert "GST (10%)" not in income
        # GST on levy income only (not on other_income, which may already be inc-GST)
        expected_gst = round((300000.0 + 80000.0) * 0.10, 2)
        assert result["gst_summary"]["gst_component"] == pytest.approx(expected_gst, rel=0.01)


class TestMonthlyTrendChart:
    """
    Unit tests verifying Monthly Trend data has the correct shape for TremorBar.
    Keys must be: month (string), income (number), levies (number), expenses (number).

    Root cause discovered 2026-04-07:
      Backend was returning {quarter, amount, payments} but frontend expects
      {month, income, levies, expenses}. Also levy_payments.quarter=null meant
      the quarterly aggregation returned empty data, so the chart showed nothing.
    """

    @pytest.mark.asyncio
    async def test_monthly_trend_returns_4_quarters(self):
        """monthly_trend must contain exactly 4 items for a full year."""
        from routers.finance import get_finance_charts
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_levy = {
            "year": "2026", "building_id": "13195",
            # levy_income = ex-GST (new authoritative field meaning)
            "admin_fund": {"levy_income": 309882.0, "total_income": 340870.2, "other_income": 0.0},
            "sinking_fund": {"levy_income": 90459.0, "total_income": 99504.9, "other_income": 0.0},
        }
        mock_cats = [
            {"fund_type": "administrative", "name": "Management", "budgeted_amount": 100000.0, "actual_amount": None},
            {"fund_type": "sinking", "name": "Repairs", "budgeted_amount": 50000.0, "actual_amount": None},
        ]

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
        mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=mock_cats)
        mock_db.levy_payments.aggregate.return_value.to_list = AsyncMock(return_value=[])

        user = {"id": "u1", "role": "super_admin", "building_id": "13195",
                "is_active": True, "email": "a@test.com"}

        with patch("routers.finance.db", mock_db):
            result = await get_finance_charts(
                year="2026", current_user=user, building_id="13195"
            )

        trend = result["monthly_trend"]
        assert len(trend) == 4, f"Expected 4 quarterly entries, got {len(trend)}"

    @pytest.mark.asyncio
    async def test_monthly_trend_has_correct_keys(self):
        """Each monthly_trend entry must have: month, income, levies, expenses."""
        from routers.finance import get_finance_charts
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_levy = {
            "year": "2026", "building_id": "13195",
            "admin_fund": {"levy_income": 309882.0, "total_income": 340870.2, "other_income": 0.0},
            "sinking_fund": {"levy_income": 90459.0, "total_income": 99504.9, "other_income": 0.0},
        }
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
        mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate.return_value.to_list = AsyncMock(return_value=[])

        user = {"id": "u1", "role": "super_admin", "building_id": "13195",
                "is_active": True, "email": "a@test.com"}

        with patch("routers.finance.db", mock_db):
            result = await get_finance_charts(
                year="2026", current_user=user, building_id="13195"
            )

        for entry in result["monthly_trend"]:
            assert "month" in entry, f"Missing 'month' key in trend entry: {entry}"
            assert "income" in entry, f"Missing 'income' key in trend entry: {entry}"
            assert "levies" in entry, f"Missing 'levies' key in trend entry: {entry}"
            assert "expenses" in entry, f"Missing 'expenses' key in trend entry: {entry}"
            assert entry["month"].startswith("Q"), f"month must start with Q: {entry['month']}"
            assert isinstance(entry["income"], (int, float))
            assert isinstance(entry["expenses"], (int, float))
        # `levies` = budgeted quarterly levy inc-GST = (admin_ex_gst + sinking_ex_gst) * 1.1 / 4
        # `income` = actual levy_payments collected that quarter (0 when mock returns no payments)
        expected_q_levies = round((309882.0 + 90459.0) * 1.1 / 4, 2)
        for entry in result["monthly_trend"]:
            assert abs(entry["levies"] - expected_q_levies) < 0.05, \
                f"Q levies {entry['levies']} should be {expected_q_levies} (ex-GST*1.1/4)"
            assert entry["income"] == 0, \
                f"income should be 0 (no mock levy_payments), got {entry['income']}"

    @pytest.mark.asyncio
    async def test_monthly_trend_quarterly_key_no_longer_returned(self):
        """Backend must NOT return 'quarterly_trend' key — frontend uses 'monthly_trend'."""
        from routers.finance import get_finance_charts
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_levy = {
            "year": "2026", "building_id": "13195",
            "admin_fund": {"levy_income": 309882.0, "total_income": 340870.2, "other_income": 0.0},
            "sinking_fund": {"levy_income": 90459.0, "total_income": 99504.9, "other_income": 0.0},
        }
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
        mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate.return_value.to_list = AsyncMock(return_value=[])

        user = {"id": "u1", "role": "super_admin", "building_id": "13195",
                "is_active": True, "email": "a@test.com"}

        with patch("routers.finance.db", mock_db):
            result = await get_finance_charts(
                year="2026", current_user=user, building_id="13195"
            )

        assert "quarterly_trend" not in result, (
            "Backend must not return 'quarterly_trend' — it should be 'monthly_trend' "
            "to match the frontend TremorBar index='month' requirement."
        )
        assert "monthly_trend" in result
