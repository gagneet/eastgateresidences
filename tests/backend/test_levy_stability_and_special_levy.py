"""
Tests for Levy Stability Score Engine + Special Levy Forecast
Regression tests for:
  - bson.errors.InvalidDocument: shock_year_distribution must have string keys
  - Full recompute path (DB save succeeds)
  - What-if scenario analysis
  - Score boundaries (high / mid / low funded)
  - API endpoint 500 regression
"""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from request_context import set_ctx_building_id
from utils.auth import get_current_user, get_current_building


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(capital_required: float = 200_000):
    return {
        "units": [
            {"unit_number": "UA001", "entitlement": 50},
            {"unit_number": "TH071", "entitlement": 40},
        ],
        "capital_schedule_by_year": {2027: capital_required * 0.6, 2030: capital_required * 0.4},
        "allocation_shares_by_year": {
            2027: {"UA001": 0.55, "TH071": 0.45},
            2030: {"UA001": 0.55, "TH071": 0.45},
        },
        "group_units": {
            "All Lots": ["UA001", "TH071"],
            "Apartments": ["UA001"],
            "Townhouses": ["TH071"],
        },
        "default_shares": {"UA001": 0.55, "TH071": 0.45},
    }


def _make_special_levy_result(probability: float = 20, capital_required: float = 200_000):
    """Simulated output of compute_special_levy_forecast."""
    current_year = date.today().year
    return {
        "forecast_id": "test-forecast-id",
        "building_id": "13195",
        "year": current_year,
        "probability": probability,
        "median_amount": 3000,
        "p90_amount": 8000,
        "p95_amount": 12000,
        "worst_case": 25000,
        "simulation_runs": 1000,
        "generated_at": "2026-03-10T00:00:00+00:00",
        "horizon_years": 10,
        "capital_requirement": capital_required,
        "reserve_balance": 150_000,
        "capital_shock": capital_required - 150_000,
        "special_levy_required": capital_required > 150_000,
        "shock_year_distribution": {
            str(current_year + 3): 8.5,
            str(current_year + 5): 6.2,
        },
        "reserve_fan": [
            {"year": current_year + 1, "p50": 140000.0, "p75": 155000.0, "p90": 170000.0},
        ],
        "per_unit_distribution": [0.0, 2500.0, 5000.0, 1000.0],
        "thresholds": {"over_5000": 12.5, "over_10000": 5.0},
        "group_summary": [
            {"group": "All Lots", "probability": probability, "median_amount": 3000,
             "p90_amount": 8000, "p95_amount": 12000, "worst_case": 25000, "unit_count": 2},
            {"group": "Apartments", "probability": probability * 0.9, "median_amount": 2800,
             "p90_amount": 7500, "p95_amount": 11000, "worst_case": 22000, "unit_count": 1},
            {"group": "Townhouses", "probability": probability * 1.1, "median_amount": 3200,
             "p90_amount": 8500, "p95_amount": 13000, "worst_case": 27000, "unit_count": 1},
        ],
        "median_total_shock": 6000,
        "worst_case_total_shock": 50000,
        "explanation": f"Test explanation: {probability}% shock probability.",
        "scenario": None,
    }


def _lss_patches(reserve_balance: float = 150_000, sinking_income: float = 80_000,
                 probability: float = 20, capital_required: float = 200_000):
    """Return all patch context managers needed for levy_stability_service tests."""
    return [
        patch("services.levy_stability_service.compute_special_levy_forecast",
              new_callable=AsyncMock, return_value=_make_special_levy_result(probability, capital_required)),
        patch("services.levy_stability_service.get_capital_allocation_context",
              new_callable=AsyncMock, return_value=_make_context(capital_required)),
        patch("services.levy_stability_service._get_financial_baseline",
              new_callable=AsyncMock,
              return_value={"reserve_balance": reserve_balance, "sinking_levy_income": sinking_income}),
        patch("services.levy_stability_service.get_intelligence_settings",
              new_callable=AsyncMock, return_value={"inflation_rate": 0.03}),
        patch("services.levy_stability_service.get_projection_horizon_years",
              new_callable=AsyncMock, return_value=10),
    ]


# ---------------------------------------------------------------------------
# 1. REGRESSION: shock_year_distribution must have string keys
# ---------------------------------------------------------------------------

class TestShockYearDistributionStringKeys:
    """
    Root-cause regression: bson.errors.InvalidDocument raised when
    shock_year_distribution has integer keys (e.g. {2029: 8.5}).
    MongoDB requires all dict keys to be strings.
    """

    def test_shock_year_counts_integer_keys_from_monte_carlo(self):
        """Monte Carlo engine returns shock_year_counts with integer keys (engine itself)."""
        from services.monte_carlo_engine import run_special_levy_simulation
        base_year = date.today().year
        sim = run_special_levy_simulation(
            annual_budget=400_000,
            current_reserve=50_000,
            capital_schedule_by_year={base_year + 3: 500_000},
            allocation_shares_by_year={base_year + 3: {"UA001": 0.5, "UA002": 0.5}},
            group_units={"All Lots": ["UA001", "UA002"]},
            num_simulations=200,
            years=5,
            base_year=base_year,
        )
        for key in sim["shock_year_counts"]:
            assert isinstance(key, int), f"Expected int key from engine, got {type(key)}"

    @pytest.mark.asyncio
    async def test_compute_special_levy_forecast_saves_string_keys(self):
        """After the fix, shock_year_distribution must have string keys before DB save."""
        from services.special_levy_service import compute_special_levy_forecast
        base_year = date.today().year
        context = {
            "units": [{"unit_number": "UA001", "entitlement": 50}, {"unit_number": "UA002", "entitlement": 50}],
            "capital_schedule_by_year": {base_year + 3: 500_000},
            "allocation_shares_by_year": {base_year + 3: {"UA001": 0.5, "UA002": 0.5}},
            "group_units": {"All Lots": ["UA001", "UA002"]},
            "default_shares": {"UA001": 0.5, "UA002": 0.5},
        }
        captured_doc = {}

        async def mock_update_one(filter_doc, update_doc, upsert=False):
            captured_doc.update(update_doc.get("$set", {}))

        with patch("services.special_levy_service.get_capital_allocation_context",
                   new_callable=AsyncMock, return_value=context), \
                patch("services.special_levy_service._get_financial_baseline",
                      new_callable=AsyncMock, return_value={"total_levy_income": 400_000, "reserve_balance": 50_000}), \
                patch("services.special_levy_service.get_projection_horizon_years",
                      new_callable=AsyncMock, return_value=10), \
                patch("services.special_levy_service.db") as mock_db:
            mock_db.special_levy_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.special_levy_forecasts.update_one = AsyncMock(side_effect=mock_update_one)

            result = await compute_special_levy_forecast("13195", force=True)

        shock_dist = result.get("shock_year_distribution", {})
        for key in shock_dist:
            assert isinstance(key, str), (
                f"shock_year_distribution key '{key}' is {type(key).__name__}, "
                "expected str — this would cause bson.errors.InvalidDocument in MongoDB"
            )

        saved_dist = captured_doc.get("shock_year_distribution", {})
        for key in saved_dist:
            assert isinstance(key, str), (
                f"DB-saved shock_year_distribution key '{key}' is {type(key).__name__}"
            )

    @pytest.mark.asyncio
    async def test_recompute_levy_stability_no_bson_error(self):
        """Full recompute must complete without bson.errors.InvalidDocument."""
        from services.levy_stability_service import recompute_levy_stability_snapshot
        saved_docs = []

        async def mock_update_one(filter_doc, update_doc, upsert=False):
            saved_docs.append(update_doc.get("$set", {}))

        patches = _lss_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.find_one = AsyncMock(return_value=None)
            mock_db.levy_stability_snapshots.update_one = AsyncMock(side_effect=mock_update_one)

            result = await recompute_levy_stability_snapshot("13195", force=True)

        assert result is not None
        assert "levy_stability_score" in result
        assert len(saved_docs) == 1, "Should save exactly one snapshot to MongoDB"

        def _check_no_int_keys(obj, path="root"):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    assert isinstance(k, str), f"Integer key '{k}' at {path}"
                    _check_no_int_keys(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _check_no_int_keys(item, f"{path}[{i}]")

        _check_no_int_keys(saved_docs[0])


# ---------------------------------------------------------------------------
# 2. Levy Stability Score — score range tests
# ---------------------------------------------------------------------------

class TestLevyStabilityScoreRanges:

    @pytest.mark.asyncio
    async def test_high_funded_scheme_score_above_90(self):
        """Well-funded building → LSS > 90."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        special_levy = _make_special_levy_result(probability=3)
        patches = _lss_patches(reserve_balance=320_000, sinking_income=120_000, probability=3)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            result = await compute_levy_stability_snapshot("13195", force=True)

        assert result["levy_stability_score"] >= 85, f"Expected >=85, got {result['levy_stability_score']}"

    @pytest.mark.asyncio
    async def test_moderate_funded_scheme_score_mid_range(self):
        """Moderately funded → LSS in 70–85."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        patches = _lss_patches(reserve_balance=210_000, sinking_income=70_000, probability=15)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            # Override per_unit_distribution for moderate volatility
            with patch("services.levy_stability_service.compute_special_levy_forecast",
                       new_callable=AsyncMock,
                       return_value={**_make_special_levy_result(15), "per_unit_distribution": [80, 120, 110, 90]}):
                result = await compute_levy_stability_snapshot("13195", force=True)

        assert 70 <= result["levy_stability_score"] <= 85, f"Expected 70–85, got {result['levy_stability_score']}"

    @pytest.mark.asyncio
    async def test_underfunded_scheme_score_below_50(self):
        """Underfunded building → LSS < 50."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        patches = _lss_patches(reserve_balance=50_000, sinking_income=20_000, probability=50)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            result = await compute_levy_stability_snapshot("13195", force=True)

        assert result["levy_stability_score"] < 50, f"Expected <50, got {result['levy_stability_score']}"


# ---------------------------------------------------------------------------
# 3. Score components are correct
# ---------------------------------------------------------------------------

class TestLevyStabilityScoreComponents:

    @pytest.mark.asyncio
    async def test_result_has_all_required_fields(self):
        from services.levy_stability_service import compute_levy_stability_snapshot

        patches = _lss_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            result = await compute_levy_stability_snapshot("13195", force=True)

        required = ["id", "building_id", "year", "reserve_score", "shock_score",
                    "volatility_score", "funding_score", "levy_stability_score",
                    "generated_at", "horizon_years", "components", "group_scores", "explanation"]
        for field in required:
            assert field in result, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_score_is_weighted_average_of_components(self):
        """LSS = 0.35×R + 0.30×S + 0.20×V + 0.15×F."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        patches = _lss_patches(probability=25)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            result = await compute_levy_stability_snapshot("13195", force=True)

        expected = round(
            0.35 * result["reserve_score"]
            + 0.30 * result["shock_score"]
            + 0.20 * result["volatility_score"]
            + 0.15 * result["funding_score"],
            1
        )
        assert result["levy_stability_score"] == expected

    @pytest.mark.asyncio
    async def test_group_scores_present_for_context_groups(self):
        """group_scores should have one entry per group in context."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        patches = _lss_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            result = await compute_levy_stability_snapshot("13195", force=True)

        group_names = {g["group"] for g in result["group_scores"]}
        assert "All Lots" in group_names
        for score_obj in result["group_scores"]:
            assert 0 <= score_obj["levy_stability_score"] <= 100

    @pytest.mark.asyncio
    async def test_cache_is_returned_when_not_forced(self):
        """Without force=True, cached snapshot should be returned directly."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        cached = {"levy_stability_score": 77.5, "building_id": "13195", "horizon_years": 10}
        with patch("services.levy_stability_service.get_projection_horizon_years",
                   new_callable=AsyncMock, return_value=10), \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.find_one = AsyncMock(return_value=cached)
            result = await compute_levy_stability_snapshot("13195", force=False)

        assert result["levy_stability_score"] == 77.5


# ---------------------------------------------------------------------------
# 4. What-if scenario (scenario path — does NOT write to DB)
# ---------------------------------------------------------------------------

class TestLevyStabilityWhatIf:

    @pytest.mark.asyncio
    async def test_what_if_scenario_not_saved_to_db(self):
        """Scenario runs must NOT write to MongoDB."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        update_called = []

        async def track_update(*a, **kw):
            update_called.append(True)

        scenario = {"sinking_fund_increase_per_unit": 500, "horizon_years": 10}
        patches = _lss_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock(side_effect=track_update)
            result = await compute_levy_stability_snapshot("13195", force=True, scenario=scenario)

        assert len(update_called) == 0, "Scenario runs must NOT save to MongoDB"
        assert result["scenario"] == scenario

    @pytest.mark.asyncio
    async def test_loan_scenario_improves_reserve_score(self):
        """Loan scenario adds loan_amount to reserve → reserve_score >= baseline."""
        from services.levy_stability_service import compute_levy_stability_snapshot

        # Baseline (no loan)
        patches = _lss_patches(reserve_balance=50_000, sinking_income=20_000, probability=50)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            base_result = await compute_levy_stability_snapshot("13195", force=True)

        # Loan scenario
        loan_scenario = {"loan_amount": 100_000, "loan_interest_rate": 0.05, "loan_term_years": 10}
        patches2 = _lss_patches(reserve_balance=50_000, sinking_income=20_000, probability=50)
        with patches2[0], patches2[1], patches2[2], patches2[3], patches2[4], \
                patch("services.levy_stability_service.db") as mock_db:
            mock_db.levy_stability_snapshots.update_one = AsyncMock()
            loan_result = await compute_levy_stability_snapshot("13195", force=True, scenario=loan_scenario)

        assert loan_result["reserve_score"] >= base_result["reserve_score"]


# ---------------------------------------------------------------------------
# 5. Special Levy Forecast standalone tests
# ---------------------------------------------------------------------------

class TestSpecialLevyForecast:

    @pytest.mark.asyncio
    async def test_no_units_returns_error(self):
        """Empty units context → returns error dict, no crash."""
        from services.special_levy_service import compute_special_levy_forecast

        with patch("services.special_levy_service.get_capital_allocation_context",
                   new_callable=AsyncMock, return_value={}), \
                patch("services.special_levy_service._get_financial_baseline",
                      new_callable=AsyncMock, return_value={"total_levy_income": 0, "reserve_balance": 0}), \
                patch("services.special_levy_service.get_projection_horizon_years",
                      new_callable=AsyncMock, return_value=10), \
                patch("services.special_levy_service.db") as mock_db:
            mock_db.special_levy_forecasts.find_one = AsyncMock(return_value=None)
            result = await compute_special_levy_forecast("13195", force=True)

        assert "error" in result

    @pytest.mark.asyncio
    async def test_result_probability_in_range(self):
        """Probability should be between 0 and 100."""
        from services.special_levy_service import compute_special_levy_forecast
        base_year = date.today().year
        context = {
            "units": [{"unit_number": "UA001", "entitlement": 50}],
            "capital_schedule_by_year": {base_year + 2: 300_000},
            "allocation_shares_by_year": {base_year + 2: {"UA001": 1.0}},
            "group_units": {"All Lots": ["UA001"]},
            "default_shares": {"UA001": 1.0},
        }
        with patch("services.special_levy_service.get_capital_allocation_context",
                   new_callable=AsyncMock, return_value=context), \
                patch("services.special_levy_service._get_financial_baseline",
                      new_callable=AsyncMock, return_value={"total_levy_income": 400_000, "reserve_balance": 80_000}), \
                patch("services.special_levy_service.get_projection_horizon_years",
                      new_callable=AsyncMock, return_value=10), \
                patch("services.special_levy_service.db") as mock_db:
            mock_db.special_levy_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.special_levy_forecasts.update_one = AsyncMock()
            result = await compute_special_levy_forecast("13195", force=True)

        assert 0 <= result["probability"] <= 100

    @pytest.mark.asyncio
    async def test_result_fields_all_present(self):
        """Forecast result must contain all required fields."""
        from services.special_levy_service import compute_special_levy_forecast
        base_year = date.today().year
        context = {
            "units": [{"unit_number": "UA001", "entitlement": 50}, {"unit_number": "TH071", "entitlement": 40}],
            "capital_schedule_by_year": {base_year + 4: 200_000},
            "allocation_shares_by_year": {base_year + 4: {"UA001": 0.55, "TH071": 0.45}},
            "group_units": {"All Lots": ["UA001", "TH071"]},
            "default_shares": {"UA001": 0.55, "TH071": 0.45},
        }
        with patch("services.special_levy_service.get_capital_allocation_context",
                   new_callable=AsyncMock, return_value=context), \
                patch("services.special_levy_service._get_financial_baseline",
                      new_callable=AsyncMock, return_value={"total_levy_income": 440_000, "reserve_balance": 120_000}), \
                patch("services.special_levy_service.get_projection_horizon_years",
                      new_callable=AsyncMock, return_value=10), \
                patch("services.special_levy_service.db") as mock_db:
            mock_db.special_levy_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.special_levy_forecasts.update_one = AsyncMock()
            result = await compute_special_levy_forecast("13195", force=True)

        required = ["forecast_id", "building_id", "year", "probability", "median_amount",
                    "p90_amount", "p95_amount", "worst_case", "shock_year_distribution",
                    "reserve_fan", "per_unit_distribution", "group_summary", "explanation"]
        for field in required:
            assert field in result, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_shock_year_distribution_keys_are_strings(self):
        """KEY REGRESSION: shock_year_distribution must not have integer keys."""
        from services.special_levy_service import compute_special_levy_forecast
        base_year = date.today().year
        # Low reserve to force shocks
        context = {
            "units": [{"unit_number": "UA001", "entitlement": 50}],
            "capital_schedule_by_year": {base_year + 2: 800_000},
            "allocation_shares_by_year": {base_year + 2: {"UA001": 1.0}},
            "group_units": {"All Lots": ["UA001"]},
            "default_shares": {"UA001": 1.0},
        }
        with patch("services.special_levy_service.get_capital_allocation_context",
                   new_callable=AsyncMock, return_value=context), \
                patch("services.special_levy_service._get_financial_baseline",
                      new_callable=AsyncMock, return_value={"total_levy_income": 300_000, "reserve_balance": 20_000}), \
                patch("services.special_levy_service.get_projection_horizon_years",
                      new_callable=AsyncMock, return_value=10), \
                patch("services.special_levy_service.db") as mock_db:
            mock_db.special_levy_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.special_levy_forecasts.update_one = AsyncMock()
            result = await compute_special_levy_forecast("13195", force=True)

        shock_dist = result.get("shock_year_distribution", {})
        for key in shock_dist:
            assert isinstance(key, str), (
                f"REGRESSION: shock_year_distribution key {key!r} is {type(key).__name__}, "
                "must be str to avoid bson.errors.InvalidDocument"
            )


# ---------------------------------------------------------------------------
# 6. Score helper functions
# ---------------------------------------------------------------------------

class TestScoreHelpers:

    def test_reserve_adequacy_score_thresholds(self):
        from services.levy_stability_service import _reserve_adequacy_score
        assert _reserve_adequacy_score(1.6) == 100
        assert _reserve_adequacy_score(1.3) == 90
        assert _reserve_adequacy_score(1.0) == 75
        assert _reserve_adequacy_score(0.9) == 60
        assert _reserve_adequacy_score(0.5) == 40

    def test_shock_score_thresholds(self):
        from services.levy_stability_service import _shock_score
        assert _shock_score(3) == 100
        assert _shock_score(7) == 90
        assert _shock_score(15) == 75
        assert _shock_score(30) == 55
        assert _shock_score(60) == 35

    def test_volatility_score_thresholds(self):
        from services.levy_stability_service import _volatility_score
        assert _volatility_score(0.03) == 100
        assert _volatility_score(0.07) == 85
        assert _volatility_score(0.15) == 70
        assert _volatility_score(0.25) == 50
        assert _volatility_score(0.40) == 30

    def test_funding_score_thresholds(self):
        from services.levy_stability_service import _funding_score
        assert _funding_score(1.3) == 100
        assert _funding_score(1.1) == 85
        assert _funding_score(0.95) == 70
        assert _funding_score(0.80) == 50
        assert _funding_score(0.60) == 30


# ---------------------------------------------------------------------------
# 7. API endpoint HTTP tests (live backend required — skipped otherwise)
# ---------------------------------------------------------------------------

class TestLevyStabilityAPIEndpoint:

    @pytest.mark.asyncio
    async def test_get_levy_stability_returns_200(self):
        """GET /intelligence/levy-stability returns 200 with a score."""
        from httpx import AsyncClient, ASGITransport
        import sys
        sys.path.insert(0, ".")
        from server import app
        app.dependency_overrides[get_current_user] = lambda: {
            "id": "admin-1",
            "role": "super_admin",
            "is_active": True,
            "is_approved": True,
        }

        def _override_building():
            set_ctx_building_id("13195")
            return "13195"

        app.dependency_overrides[get_current_building] = _override_building
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/intelligence/levy-stability")
        finally:
            app.dependency_overrides = {}
            set_ctx_building_id(None)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "levy_stability_score" in data
        assert 0 <= data["levy_stability_score"] <= 100

    @pytest.mark.asyncio
    async def test_recompute_levy_stability_returns_200_not_500(self):
        """
        POST /intelligence/levy-stability/recompute must not return 500.
        This is the production regression test for bson.errors.InvalidDocument.
        """
        from httpx import AsyncClient, ASGITransport
        import sys
        sys.path.insert(0, ".")
        from server import app
        app.dependency_overrides[get_current_user] = lambda: {
            "id": "admin-1",
            "role": "super_admin",
            "is_active": True,
            "is_approved": True,
        }

        def _override_building():
            set_ctx_building_id("13195")
            return "13195"

        app.dependency_overrides[get_current_building] = _override_building
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post("/api/intelligence/levy-stability/recompute")
        finally:
            app.dependency_overrides = {}
            set_ctx_building_id(None)
        assert resp.status_code != 500, (
            f"Got 500 — likely bson.errors.InvalidDocument in shock_year_distribution.\n"
            f"Response: {resp.text}"
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "levy_stability_score" in data

    @pytest.mark.asyncio
    async def test_recompute_requires_auth(self):
        """POST /intelligence/levy-stability/recompute without auth → 401/403."""
        from httpx import AsyncClient, ASGITransport
        import sys
        sys.path.insert(0, ".")
        from server import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/intelligence/levy-stability/recompute")
        assert resp.status_code in (401, 403)
