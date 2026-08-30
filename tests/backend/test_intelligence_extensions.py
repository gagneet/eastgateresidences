from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from services.capital_shock_service import detect_capital_shocks
from services.levy_fairness_service import simulate_levy_fairness

_BASE_URL = "http://127.0.0.1:8003/api"


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoint Tests — capital-works ObjectId fix + capital-shock regeneration
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalWorksEndpoint:
    """Verify /intelligence/capital-works returns JSON without ObjectId errors."""

    def test_capital_works_returns_200(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-works", headers=admin_headers, timeout=15)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    def test_capital_works_returns_list(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-works", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), "Should return a list"

    def test_capital_works_no_id_field(self, admin_headers):
        """Verify no MongoDB ObjectId/_id leaks into response."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-works", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert "_id" not in item, f"_id leaked into response for item: {item.get('asset_name')}"

    def test_capital_works_has_required_fields(self, admin_headers):
        """Verify each item has asset_name, replacement_year, estimated_cost."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-works", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        if data:
            item = data[0]
            assert "asset_name" in item
            assert "replacement_year" in item
            assert "estimated_cost" in item


class TestCapitalShockEndpoint:
    """Verify /intelligence/capital-shock returns valid data after stale cache clear."""

    def test_capital_shock_returns_200(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-shock", headers=admin_headers, timeout=30)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    def test_capital_shock_has_required_fields(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-shock", headers=admin_headers, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        for field in ["risk_level", "reserve_balance", "capital_shock_index", "reserve_collapse", "funding_adequacy"]:
            assert field in data, f"Missing field: {field}"

    def test_capital_shock_no_id_field(self, admin_headers):
        """Verify ObjectId is not present in response."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-shock", headers=admin_headers, timeout=30)
        assert resp.status_code == 200
        raw = resp.text
        assert '"_id"' not in raw, "ObjectId _id leaked into capital-shock response"

    def test_capital_shock_risk_level_valid(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/capital-shock", headers=admin_headers, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


class TestSinkingFundPlanEndpoint:
    """Verify /finance/sinking-fund-plan returns seeded 15-year plan."""

    def test_sinking_fund_plan_returns_200(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert resp.status_code == 200

    def test_sinking_fund_plan_has_15_rows(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("plan", [])) == 15, f"Expected 15 plan rows, got {len(data.get('plan', []))}"

    def test_sinking_fund_plan_has_major_capital_years(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        major = [r["year"] for r in data.get("plan", []) if r.get("is_major_capital_year")]
        assert 2029 in major, "2029 should be a major capital year (Building Repaint)"
        assert 2035 in major, "2035 should be a major capital year (Lift Replacement)"

    def test_sinking_fund_plan_has_capital_events(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("capital_events", [])) >= 3, "Should have at least 3 capital events"

    def test_sinking_fund_plan_summary_totals(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        summary = data.get("summary", {})
        assert summary.get("total_contributions", 0) > 0
        assert summary.get("total_expenditures", 0) > 0
        assert summary.get("plan_start") == 2021
        assert summary.get("plan_end") == 2035


class TestIntelligenceSummaryEndpoint:
    """Verify /intelligence/summary returns valid building-level intelligence."""

    def test_summary_returns_200(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/summary", headers=admin_headers, timeout=30)
        assert resp.status_code == 200

    def test_summary_has_kpi_fields(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/summary", headers=admin_headers, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        # Should have intelligence summary fields
        for field in ["asset_health_score", "high_risk_assets_count"]:
            assert field in data, f"Missing intelligence KPI field: {field}"


class TestMaintenanceForecastEndpoint:
    """Verify /intelligence/maintenance-forecast returns prediction data."""

    def test_forecast_returns_200(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/maintenance-forecast", headers=admin_headers, timeout=15)
        assert resp.status_code == 200

    def test_forecast_has_predicted_cost(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        resp = requests.get(f"{_BASE_URL}/intelligence/maintenance-forecast", headers=admin_headers, timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        if data:
            assert "predicted_cost" in data


@pytest.mark.asyncio
async def test_simulate_levy_fairness_returns_scores():
    units = [
        {"unit_number": "UA001", "lot_number": "LOT1", "entitlement": 100, "unit_type": "apartment", "car_spaces": 1},
        {"unit_number": "TH071", "lot_number": "LOT2", "entitlement": 120, "unit_type": "townhouse", "car_spaces": 2},
    ]
    facilities = [{"id": "fac-lift", "name": "Lift System", "benefit_group_id": "bg-tower"}]
    assets = [{
        "id": "asset-lift",
        "facility_id": "fac-lift",
        "risk_score": 10,
        "replacement_cost_estimate": 20000,
        "expected_lifespan_years": 20,
    }]
    benefit_groups = [{"id": "bg-tower", "name": "APARTMENTS_ONLY", "lot_numbers": []}]

    with patch("services.levy_fairness_service.db") as mock_db, \
            patch("services.levy_fairness_service.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
        mock_year.return_value = "2026"
        mock_db.units.find.return_value = _cursor(units)
        mock_db.facilities.find.return_value = _cursor(facilities)
        mock_db.building_assets.find.return_value = _cursor(assets)
        mock_db.benefit_groups.find.return_value = _cursor(benefit_groups)
        mock_db.maintenance_forecasts.find_one = AsyncMock(return_value={"predicted_cost": 1200})
        mock_db.work_orders.find.return_value = _cursor([])
        mock_db.capital_replacement_schedule.find.return_value = _cursor([])
        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "admin_levy_per_uoe_annual": 30,
            "sinking_levy_per_uoe_annual": 12,
        })
        mock_db.levy_fairness_overrides.find.return_value = _cursor([])
        mock_db.levy_fairness_results.update_one = AsyncMock()
        mock_db.levy_simulations.update_one = AsyncMock()

        result = await simulate_levy_fairness("13195")

    assert "current_fairness_score" in result
    assert "benefit_fairness_score" in result
    assert result["impact_by_group"]


@pytest.mark.asyncio
async def test_detect_capital_shocks_flags_deficit():
    shock_year = date.today().year + 1
    schedule = [{
        "asset_id": "asset-lift",
        "asset_name": "Lift Motor A",
        "replacement_year": shock_year,
        "estimated_cost": 220000,
    }]

    with patch("services.capital_shock_service.db") as mock_db, \
            patch("services.capital_shock_service.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
        mock_year.return_value = "2026"
        mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
        mock_db.maintenance_forecasts.find_one = AsyncMock(return_value={"predicted_cost": 2000})
        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "admin_fund": {"levy_income": 80000},
            "sinking_fund": {"levy_income": 15000, "closing_balance": 20000},
        })
        mock_db.sinking_fund_plan.find.return_value = _cursor([
            {
                "year": 2026,
                "contribution": 15000,
                "expenditure": 5000,
                "closing_balance": 20000,
                "is_actual": True,
                "is_major_capital_year": False,
            },
            {
                "year": shock_year,
                "contribution": 15000,
                "expenditure": 220000,
                "closing_balance": -185000,
                "is_actual": False,
                "is_major_capital_year": True,
            },
        ])
        mock_db.sinking_fund_capital_events.find.return_value = _cursor([])
        mock_db.capital_shock_risks.update_one = AsyncMock()

        result = await detect_capital_shocks("13195")

    assert result["risk_level"] in {"HIGH", "CRITICAL"}
    assert result["recommended_levy_increase_pct"] is not None
    assert "capital_shock_index" in result
    assert "reserve_collapse" in result
    assert "funding_adequacy" in result
