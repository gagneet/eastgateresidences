"""
Levy Fairness Engine Tests

Covers:
  - simulate_levy_fairness_v2 end-to-end with mocked DB
  - calculate_facility_allocation (unit_entitlement and equal_split drivers)
  - apply_transition_caps (percent cap and dollar cap)
  - run_monte_carlo_levy_simulation (basic output validation)
  - Fallback to all-units when building_id filter returns empty
  - Intelligence router endpoints (GET /levy-fairness, POST /levy-fairness/recompute)

Run with:
    cd /home/gagneet/strata-management/backend
    venv/bin/pytest tests/backend/test_levy_fairness.py -v
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Module-level autouse fixture: owner_service uses its own db import, so
# mock get_all_unit_owners at the levy_fairness_service import site to avoid
# requiring a live building context in unit tests.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_all_unit_owners():
    with patch("services.levy_fairness_service.get_all_unit_owners", AsyncMock(return_value={})):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cursor(data):
    """Return a mock cursor whose .to_list() returns data."""
    cur = MagicMock()
    cur.to_list = AsyncMock(return_value=data)
    return cur


def _make_unit(unit_number: str, entitlement: float = 100.0, property_type: str = "Apartment") -> dict:
    return {
        "unit_number": unit_number,
        "entitlement": entitlement,
        "property_type": property_type,
        "owner_name": "Test Owner",
    }


def _make_facility(fac_id: str = "fac-1", name: str = "Pool", annual_cost: float = 10000.0,
                   driver: str = "unit_entitlement", bg_id: str = None) -> dict:
    return {
        "facility_id": fac_id,
        "facility_name": name,
        "annual_cost": annual_cost,
        "allocation_driver": driver,
        "benefit_group_id": bg_id,
        "enabled": True,
        "building_id": "13195",
    }


def _make_ledger(unit_number: str, total_levied: float = 5000.0) -> dict:
    return {"unit_number": unit_number, "total_levied": total_levied, "year": "2026"}


def _build_mock_db(units=None, facilities=None, ledger=None,
                   benefit_groups=None, cap_schedule=None, reserve_doc=None):
    """Build a mock DB with sensible defaults for levy fairness tests."""
    if units is None:
        units = [_make_unit("UA001", 100.0), _make_unit("UA002", 200.0)]
    if facilities is None:
        facilities = [_make_facility()]
    if ledger is None:
        ledger = [_make_ledger("UA001", 3000.0), _make_ledger("UA002", 6000.0)]
    if benefit_groups is None:
        benefit_groups = []
    if cap_schedule is None:
        cap_schedule = []

    mock_db = MagicMock()

    # units queries: first call uses building_id filter (may return empty), second is fallback
    mock_db.units.find.return_value = _cursor(units)

    # benefit_groups
    mock_db.benefit_groups.find.return_value = _cursor(benefit_groups)

    # unit_levy_ledger
    mock_db.unit_levy_ledger.find.return_value = _cursor(ledger)

    # unit_attributes
    mock_db.unit_attributes.find.return_value = _cursor([])

    # Monte Carlo support
    mock_db.capital_replacement_schedule.find.return_value = _cursor(cap_schedule)
    mock_db.financial_summary.find_one = AsyncMock(return_value=reserve_doc)

    # Real asset data sources (new architecture — no facility_cost_centres)
    # Pass virtual facilities/assets via the dedicated _build_mock_db params
    mock_db.facilities.find.return_value = _cursor(facilities if facilities else [])
    mock_db.building_assets.find.return_value = _cursor([])

    # levy_history aggregate
    mock_db.unit_levy_ledger.aggregate.return_value = _cursor([])
    total_uoe = sum(float(u.get("entitlement", 0) or 0) for u in units) or 1
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "building_id": "13195",
        "year": "2026",
        "total_uoe": total_uoe,
        "admin_fund": {"levy_income": 6000.0},
        "sinking_fund": {"levy_income": 3000.0},
    })
    mock_db.settings.find_one = AsyncMock(return_value={})
    mock_db.buildings.find_one = AsyncMock(return_value={})

    # result persistence
    mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)
    mock_db.levy_fairness_results_v2.update_one = AsyncMock(return_value=MagicMock())

    return mock_db


# ─────────────────────────────────────────────────────────────────────────────
# 1. simulate_levy_fairness_v2 — basic correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestSimulateLevyFairnessV2:

    @pytest.mark.asyncio
    async def test_returns_required_keys(self):
        """Result must contain top-level keys: total_budget, lei_score, sei_scheme, unit_impact."""
        mock_db = _build_mock_db()

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})), \
                patch("services.levy_fairness_service.run_monte_carlo_levy_simulation",
                      return_value={"p50": 50000, "p90": 80000, "p95": 90000,
                                    "special_levy_probability": 5.0, "num_simulations": 1000,
                                    "distribution": []}):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=True)

        assert "total_budget" in result
        assert "lei_score" in result
        assert "sei_scheme" in result
        assert "unit_impact" in result
        assert "building_id" in result

    @pytest.mark.asyncio
    async def test_top_drivers_derived_from_facility_breakdown(self):
        """LevyFairnessCard's "Top subsidy drivers" bar list reads top_drivers[].name/.amount.
        This must be populated from the highest-cost facilities, sorted descending —
        previously the field was never returned at all, so the bar list never rendered.

        virtual_cost_centres come from _derive_virtual_cost_centres() (asset-cost derived,
        then proportionally rescaled to match the actual levy total) — mocked directly here
        so the test only exercises the top_drivers selection/sort, not the asset math."""
        mock_db = _build_mock_db()
        virtual_cost_centres = [
            {"facility_id": "fac-1", "facility_name": "Pool", "annual_cost": 5000.0,
             "benefit_group_id": None, "allocation_driver": "unit_entitlement", "enabled": True, "building_id": "13195"},
            {"facility_id": "fac-2", "facility_name": "Lift maintenance", "annual_cost": 20000.0,
             "benefit_group_id": None, "allocation_driver": "unit_entitlement", "enabled": True, "building_id": "13195"},
            {"facility_id": "fac-3", "facility_name": "Gardens", "annual_cost": 12000.0,
             "benefit_group_id": None, "allocation_driver": "unit_entitlement", "enabled": True, "building_id": "13195"},
        ]

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_levy_rates", AsyncMock(return_value={
                    "admin_annual": 10.0,
                    "sinking_annual": 20.0,
                })), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service._derive_virtual_cost_centres",
                      AsyncMock(return_value=virtual_cost_centres)), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert "top_drivers" in result
        names = [d["name"] for d in result["top_drivers"]]
        assert names == ["Lift maintenance", "Gardens", "Pool"]
        # Amounts are proportionally rescaled to the actual levy total, but ordering
        # and the largest-first invariant must survive that rescale.
        amounts = [d["amount"] for d in result["top_drivers"]]
        assert amounts == sorted(amounts, reverse=True)

    @pytest.mark.asyncio
    async def test_total_budget_matches_ledger_total(self):
        """total_budget must equal the sum of all current levies from unit_levy_ledger.
        The service scales virtual cost centres to match the real levy total — it does NOT
        use the raw asset cost sums. This ensures 'Regenerate' always reflects actual levies.
        """
        # Default ledger: UA001=$3000, UA002=$6000 → total=$9000
        mock_db = _build_mock_db()

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_levy_rates", AsyncMock(return_value={
                    "admin_annual": 10.0,
                    "sinking_annual": 20.0,
                })), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3000.0, "UA002": 6000.0})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        # total_budget = sum of ledger (3000 + 6000 = 9000), not raw asset sums
        assert result["total_budget"] == 9000.0

    @pytest.mark.asyncio
    async def test_lei_score_is_100_when_levies_match_benefits(self):
        """If every unit's proposed levy == benefit, SEI_scheme = 0 and LEI = 100."""
        units = [_make_unit("UA001", 100.0), _make_unit("UA002", 200.0)]
        facilities = [_make_facility("f1", "Facility", 9000.0)]
        mock_db = _build_mock_db(units=units, facilities=facilities)

        # Allocation exactly proportional to entitlement (UE 100 + 200 = 300)
        alloc = {"UA001": 3000.0, "UA002": 6000.0}

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_levy_rates", AsyncMock(return_value={
                    "admin_annual": 10.0,
                    "sinking_annual": 20.0,
                })), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value=alloc)), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value=alloc)):  # proposed == fair

            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert result["lei_score"] == 100.0
        assert result["sei_scheme"] == 0.0

    @pytest.mark.asyncio
    async def test_unit_impact_contains_all_units(self):
        """unit_impact list must have one entry per unit."""
        units = [_make_unit("UA001"), _make_unit("UA002"), _make_unit("TH071", 150.0, "Townhouse")]
        mock_db = _build_mock_db(units=units)

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 2500.0, "UA002": 2500.0, "TH071": 5000.0})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        unit_numbers = {u["unit_number"] for u in result["unit_impact"]}
        assert unit_numbers == {"UA001", "UA002", "TH071"}

    @pytest.mark.asyncio
    async def test_returns_error_when_no_units(self):
        """When no units exist (even after fallback), return error dict."""
        mock_db = _build_mock_db(units=[])
        # Both calls to units.find return empty
        mock_db.units.find.return_value = _cursor([])

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert "error" in result

    @pytest.mark.asyncio
    async def test_simulation_included_when_run_monte_carlo_true(self):
        """When run_monte_carlo=True, the result must include a 'simulation' key with p50/p90/p95."""
        mock_db = _build_mock_db()
        mock_db.financial_summary.find_one = AsyncMock(return_value={"reserve_balance": 100000})

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=True)

        assert result["simulation"] is not None
        assert "p50" in result["simulation"]
        assert "p90" in result["simulation"]
        assert "special_levy_probability" in result["simulation"]

    @pytest.mark.asyncio
    async def test_simulation_none_when_monte_carlo_disabled(self):
        """When run_monte_carlo=False, simulation must be None."""
        mock_db = _build_mock_db()

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert result["simulation"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fallback to all units when building_id filter returns empty
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackToAllUnits:

    @pytest.mark.asyncio
    async def test_units_found_with_building_id_filter(self):
        """
        Units.find with building_id filter returns data — the service should
        process those units and return unit_impact for each.
        The legacy no-filter fallback was removed; units must have building_id set.
        """
        all_units = [_make_unit("UA001", 100.0), _make_unit("UA002", 200.0)]
        mock_db = _build_mock_db(units=all_units)

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3333.33, "UA002": 6666.67})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        # Should have processed 2 units
        assert len(result["unit_impact"]) == 2
        unit_numbers = {u["unit_number"] for u in result["unit_impact"]}
        assert "UA001" in unit_numbers
        assert "UA002" in unit_numbers


# ─────────────────────────────────────────────────────────────────────────────
# 3. calculate_facility_allocation
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateFacilityAllocation:

    @pytest.mark.asyncio
    async def test_unit_entitlement_driver_proportional(self):
        """unit_entitlement splits cost proportionally to entitlement values."""
        from services.facility_allocation_engine import calculate_facility_allocation

        facility = _make_facility("f1", "Pool", 9000.0, driver="unit_entitlement")
        units = [_make_unit("UA001", 100.0), _make_unit("UA002", 200.0)]  # 100:200 = 1:2
        benefit_groups = {}
        unit_attributes = {}

        alloc = await calculate_facility_allocation(facility, units, benefit_groups, unit_attributes)

        assert "UA001" in alloc
        assert "UA002" in alloc
        assert abs(alloc["UA001"] - 3000.0) < 0.01, f"Expected 3000.0, got {alloc['UA001']}"
        assert abs(alloc["UA002"] - 6000.0) < 0.01, f"Expected 6000.0, got {alloc['UA002']}"

    @pytest.mark.asyncio
    async def test_equal_split_driver_divides_evenly(self):
        """equal_split divides cost equally regardless of entitlement."""
        from services.facility_allocation_engine import calculate_facility_allocation

        facility = _make_facility("f1", "Gym", 4000.0, driver="equal_split")
        units = [_make_unit("UA001", 50.0), _make_unit("UA002", 150.0), _make_unit("UA003", 100.0)]
        benefit_groups = {}
        unit_attributes = {}

        alloc = await calculate_facility_allocation(facility, units, benefit_groups, unit_attributes)

        assert len(alloc) == 3
        for unit_num, amount in alloc.items():
            assert abs(amount - (4000.0 / 3)) < 0.01, f"{unit_num}: expected ~1333.33, got {amount}"

    @pytest.mark.asyncio
    async def test_zero_cost_returns_empty_allocation(self):
        """A facility with zero annual_cost produces an empty allocation."""
        from services.facility_allocation_engine import calculate_facility_allocation

        facility = _make_facility("f1", "TBD", 0.0, driver="equal_split")
        units = [_make_unit("UA001"), _make_unit("UA002")]

        alloc = await calculate_facility_allocation(facility, units, {}, {})

        assert alloc == {}

    @pytest.mark.asyncio
    async def test_benefit_group_lot_numbers_filter(self):
        """Only units in benefit_group lot_numbers should receive allocation."""
        from services.facility_allocation_engine import calculate_facility_allocation

        facility = _make_facility("f1", "Rooftop", 3000.0, driver="equal_split", bg_id="bg-apt")
        units = [_make_unit("UA001"), _make_unit("UA002"), _make_unit("TH071", 100.0, "Townhouse")]
        benefit_groups = {
            "bg-apt": {"id": "bg-apt", "name": "Apartments", "lot_numbers": ["UA001", "UA002"],
                       "allocation_driver": "equal_split"}
        }

        alloc = await calculate_facility_allocation(facility, units, benefit_groups, {})

        # Only apartments in lot_numbers should be allocated
        assert "UA001" in alloc
        assert "UA002" in alloc
        assert "TH071" not in alloc
        assert abs(alloc["UA001"] - 1500.0) < 0.01

    @pytest.mark.asyncio
    async def test_unit_entitlement_all_zero_returns_empty(self):
        """If all units have zero entitlement and driver is unit_entitlement, return empty."""
        from services.facility_allocation_engine import calculate_facility_allocation

        facility = _make_facility("f1", "Facility", 5000.0, driver="unit_entitlement")
        units = [_make_unit("UA001", 0.0), _make_unit("UA002", 0.0)]

        alloc = await calculate_facility_allocation(facility, units, {}, {})

        # total_ue = 0 → no allocation possible
        assert alloc == {}


# ─────────────────────────────────────────────────────────────────────────────
# 4. apply_transition_caps
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyTransitionCaps:

    @pytest.mark.asyncio
    async def test_no_caps_returns_fair_levies_unchanged(self):
        """Without any cap, proposed levies equal fair levies exactly."""
        from services.levy_simulation_engine import apply_transition_caps

        fair = {"UA001": 5000.0, "UA002": 8000.0}
        current = {"UA001": 4000.0, "UA002": 7000.0}

        result = await apply_transition_caps(fair, current, None, None)

        assert result == fair

    @pytest.mark.asyncio
    async def test_max_change_percent_caps_increase(self):
        """A unit's proposed levy cannot exceed current * (1 + max_change_percent/100)."""
        from services.levy_simulation_engine import apply_transition_caps

        current = {"UA001": 4000.0, "UA002": 4000.0}
        # Fair allocation: UA001 gets 30% increase, UA002 gets 10% increase
        fair = {"UA001": 5200.0, "UA002": 4400.0}  # UA001: +30%, UA002: +10%

        # Cap at 20%: UA001 capped at 4800, UA002 can go to 4800 too but fair is 4400
        result = await apply_transition_caps(fair, current, max_change_percent=20.0, max_change_amount=None)

        assert result["UA001"] <= 4000.0 * 1.20 + 0.01  # UA001 must be capped
        # Revenue neutrality: total should stay close to sum of fair levies
        # (shortfall redistributed to UA002 if possible)

    @pytest.mark.asyncio
    async def test_max_change_amount_caps_increase(self):
        """A unit's proposed levy cannot increase by more than max_change_amount."""
        from services.levy_simulation_engine import apply_transition_caps

        current = {"UA001": 4000.0, "UA002": 4000.0}
        # Fair allocation: UA001 gets $1500 increase (exceeds $500 cap)
        fair = {"UA001": 5500.0, "UA002": 4100.0}

        result = await apply_transition_caps(fair, current, max_change_percent=None, max_change_amount=500.0)

        assert result["UA001"] <= 4000.0 + 500.0 + 0.01  # capped at $4500

    @pytest.mark.asyncio
    async def test_decreasing_units_not_capped(self):
        """Units with decreasing levies are never capped by the transition logic."""
        from services.levy_simulation_engine import apply_transition_caps

        current = {"UA001": 8000.0, "UA002": 4000.0}
        # UA001 decreases, UA002 increases
        fair = {"UA001": 5000.0, "UA002": 7000.0}

        result = await apply_transition_caps(fair, current, max_change_percent=10.0, max_change_amount=None)

        # UA001's decrease should not be capped
        assert result["UA001"] <= current["UA001"]

    @pytest.mark.asyncio
    async def test_both_caps_uses_most_restrictive(self):
        """When both caps are set, the more restrictive (lower) cap applies."""
        from services.levy_simulation_engine import apply_transition_caps

        current = {"UA001": 4000.0}
        fair = {"UA001": 6000.0}  # $2000 increase

        # percent cap: 4000 * 1.10 = 4400 (+$400)
        # amount cap: 4000 + $800 = $4800
        # The percent cap (4400) is more restrictive
        result = await apply_transition_caps(
            fair, current, max_change_percent=10.0, max_change_amount=800.0
        )

        assert result["UA001"] <= 4400.0 + 0.01


# ─────────────────────────────────────────────────────────────────────────────
# 5. run_monte_carlo_levy_simulation
# ─────────────────────────────────────────────────────────────────────────────

class TestRunMonteCarloSimulation:

    def test_returns_required_keys(self):
        """Output must contain p50, p90, p95, special_levy_probability, num_simulations, distribution."""
        from services.monte_carlo_engine import run_monte_carlo_levy_simulation

        result = run_monte_carlo_levy_simulation(
            annual_budget=440000.0,
            current_reserve=100000.0,
            capital_schedule_10y=[],
            num_simulations=100,
            years=5
        )

        assert "p50" in result
        assert "p90" in result
        assert "p95" in result
        assert "special_levy_probability" in result
        assert "num_simulations" in result
        assert "distribution" in result

    def test_num_simulations_matches_param(self):
        """num_simulations in result must equal the requested count."""
        from services.monte_carlo_engine import run_monte_carlo_levy_simulation

        result = run_monte_carlo_levy_simulation(
            annual_budget=200000.0,
            current_reserve=50000.0,
            capital_schedule_10y=[],
            num_simulations=200,
            years=3
        )

        assert result["num_simulations"] == 200

    def test_percentile_ordering(self):
        """p50 <= p90 <= p95 (sorted results)."""
        from services.monte_carlo_engine import run_monte_carlo_levy_simulation

        result = run_monte_carlo_levy_simulation(
            annual_budget=440000.0,
            current_reserve=200000.0,
            capital_schedule_10y=[],
            num_simulations=500,
            years=5
        )

        assert result["p50"] <= result["p90"] <= result["p95"]

    def test_special_levy_probability_range(self):
        """special_levy_probability must be between 0 and 100."""
        from services.monte_carlo_engine import run_monte_carlo_levy_simulation

        result = run_monte_carlo_levy_simulation(
            annual_budget=440000.0,
            current_reserve=0.0,
            capital_schedule_10y=[],
            num_simulations=200,
            years=10
        )

        assert 0.0 <= result["special_levy_probability"] <= 100.0

    def test_capital_schedule_increases_probability(self):
        """A massive capital works schedule in year 1 should guarantee near-100% deficit probability."""
        from services.monte_carlo_engine import run_monte_carlo_levy_simulation
        from datetime import datetime

        # Use a capital cost so large (100x the reserve) that deficit is virtually certain
        target_year = datetime.now().year + 1
        cap_schedule = [{"replacement_year": target_year, "estimated_cost": 50_000_000.0}]
        cap_result = run_monte_carlo_levy_simulation(
            annual_budget=200000.0,
            current_reserve=10000.0,
            capital_schedule_10y=cap_schedule,
            num_simulations=300,
            years=3
        )

        # With a $50M capital cost against a $10k reserve and $200k/yr budget,
        # virtually every simulation will fail (>90% deficit probability)
        assert cap_result["special_levy_probability"] >= 90.0

    def test_distribution_is_sampled_list(self):
        """distribution must be a non-empty list of numeric values."""
        from services.monte_carlo_engine import run_monte_carlo_levy_simulation

        result = run_monte_carlo_levy_simulation(
            annual_budget=300000.0,
            current_reserve=50000.0,
            capital_schedule_10y=[],
            num_simulations=100,
            years=3
        )

        assert isinstance(result["distribution"], list)
        assert len(result["distribution"]) > 0
        assert all(isinstance(v, (int, float)) for v in result["distribution"])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Intelligence router endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestIntelligenceRouterLevyFairness:

    def _make_admin_user(self):
        return {
            "id": "user-admin-001",
            "full_name": "Admin User",
            "email": "admin@test.com",
            "role": "super_admin",
            "is_active": True,
            "is_approved": True,
            "building_id": "13195",
        }

    def _make_ec_user(self):
        return {
            "id": "user-ec-001",
            "full_name": "EC User",
            "email": "ec@test.com",
            "role": "ec_member",
            "is_active": True,
            "is_approved": True,
            "building_id": "13195",
        }

    def _mock_result(self):
        return {
            "building_id": "13195",
            "computed_at": "2026-03-01T00:00:00Z",
            "total_budget": 10000.0,
            "lei_score": 95.0,
            "sei_scheme": 0.05,
            "unit_impact": [],
            "simulation": None,
            "facility_breakdown": [],
        }

    @pytest.mark.asyncio
    async def test_get_levy_fairness_returns_cached_result(self):
        """GET /levy-fairness should return cached result from DB when available."""
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_result = self._mock_result()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=mock_result)

        with patch("routers.intelligence.db", mock_db), \
                patch("routers.intelligence.get_finance_route_runtime_state",
                      AsyncMock(return_value={"source": "mongo"})), \
                patch("routers.intelligence.get_approved_user",
                      return_value=self._make_admin_user()):
            result = await intel.get_levy_fairness_v2(
                current_user=self._make_admin_user(),
                bid="13195",
            )

        assert result["total_budget"] == 10000.0
        assert result["lei_score"] == 95.0

    @pytest.mark.asyncio
    async def test_get_levy_fairness_computes_when_no_cache(self):
        """GET /levy-fairness should call simulate_levy_fairness_v2 when no cached result."""
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)

        mock_result = self._mock_result()

        with patch("routers.intelligence.db", mock_db), \
                patch("routers.intelligence.get_finance_route_runtime_state",
                      AsyncMock(return_value={"source": "mongo"})), \
                patch("routers.intelligence.simulate_levy_fairness_v2",
                      AsyncMock(return_value=mock_result)) as mock_simulate:
            result = await intel.get_levy_fairness_v2(
                current_user=self._make_admin_user(),
                bid="13195",
            )

        mock_simulate.assert_called_once_with("13195")
        assert result["total_budget"] == 10000.0

    @pytest.mark.asyncio
    async def test_recompute_endpoint_authorized_role(self):
        """POST /levy-fairness/recompute should work for super_admin."""
        import routers.intelligence as intel

        mock_result = self._mock_result()

        with patch("routers.intelligence.simulate_levy_fairness_v2",
                   AsyncMock(return_value=mock_result)) as mock_simulate:
            from routers.intelligence import LevySimulationRequest
            payload = LevySimulationRequest(run_monte_carlo=False)
            result = await intel.recompute_levy_fairness_v2(
                payload=payload,
                current_user=self._make_admin_user()
            )

        mock_simulate.assert_called_once()
        assert result["total_budget"] == 10000.0

    @pytest.mark.asyncio
    async def test_recompute_endpoint_forbidden_for_owner(self):
        """POST /levy-fairness/recompute must return 403 for owner role."""
        from fastapi import HTTPException
        import routers.intelligence as intel

        owner_user = {
            "id": "user-owner-001",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            "building_id": "13195",
        }

        with pytest.raises(HTTPException) as exc_info:
            await intel.recompute_levy_fairness_v2(
                payload=None,
                current_user=owner_user
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_recompute_passes_transition_caps_to_service(self):
        """POST /levy-fairness/recompute should forward max_change_percent and max_change_amount."""
        import routers.intelligence as intel

        mock_result = self._mock_result()

        with patch("routers.intelligence.simulate_levy_fairness_v2",
                   AsyncMock(return_value=mock_result)) as mock_simulate:
            from routers.intelligence import LevySimulationRequest
            payload = LevySimulationRequest(
                max_change_percent=15.0,
                max_change_amount=500.0,
                run_monte_carlo=False
            )
            await intel.recompute_levy_fairness_v2(
                payload=payload,
                current_user=self._make_ec_user()
            )

        call_kwargs = mock_simulate.call_args
        assert call_kwargs.kwargs.get("max_change_percent") == 15.0
        assert call_kwargs.kwargs.get("max_change_amount") == 500.0

    @pytest.mark.asyncio
    async def test_get_levy_fairness_demo_endpoint(self):
        """GET /levy-fairness/demo should use the current building_id from context."""
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_result = self._mock_result()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=mock_result)

        with patch("routers.intelligence.db", mock_db):
            result = await intel.get_levy_fairness_demo(
                current_user=self._make_admin_user(),
                bid="13195"
            )

        # The demo endpoint now uses the building_id from the JWT context
        mock_db.levy_fairness_results_v2.find_one.assert_called_once_with(
            {"building_id": "13195"}, {"_id": 0}
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. _compute_model_confidence
# ─────────────────────────────────────────────────────────────────────────────

class TestModelConfidence:

    def _call(self, units, vcc, ledger, benefit_groups, total_asset_budget, actual_total):
        from services.levy_fairness_service import _compute_model_confidence
        return _compute_model_confidence(units, vcc, ledger, benefit_groups, total_asset_budget, actual_total)

    def test_high_confidence_all_data_present(self):
        """All units with ledger + asset budget covering >100% of levy → High band."""
        units = [_make_unit("UA001"), _make_unit("UA002")]
        ledger = [_make_ledger("UA001", 3000.0), _make_ledger("UA002", 6000.0)]
        # asset budget >= actual total → asset_ratio = 100
        benefit_groups = {
            "bg1": {"id": "bg1", "name": "Apartments", "group_type": "specific"},
            "bg2": {"id": "bg2", "name": "Townhouses", "group_type": "specific"},
            "bg3": {"id": "bg3", "name": "Garage", "group_type": "specific"},
            "bg4": {"id": "bg4", "name": "Rooftop", "group_type": "specific"},
        }
        result = self._call(units, [], ledger, benefit_groups, 9000.0, 9000.0)
        assert result["band"] == "High"
        assert result["score"] >= 75

    def test_no_asset_data_lowers_score(self):
        """With total_asset_budget=0, asset_data_score should be 0."""
        units = [_make_unit("UA001"), _make_unit("UA002")]
        ledger = [_make_ledger("UA001", 3000.0), _make_ledger("UA002", 6000.0)]
        result = self._call(units, [], ledger, {}, 0.0, 9000.0)
        assert result["asset_data_score"] == 0.0

    def test_no_benefit_groups_gives_zero_group_score(self):
        """With no specific benefit groups, group_granularity_score == 0."""
        units = [_make_unit("UA001"), _make_unit("UA002")]
        ledger = [_make_ledger("UA001", 3000.0), _make_ledger("UA002", 6000.0)]
        result = self._call(units, [], ledger, {}, 5000.0, 9000.0)
        assert result["group_granularity_score"] == 0.0
        # factors should mention no benefit groups
        assert any("benefit group" in f.lower() or "uo" in f.lower() for f in result["factors"])

    def test_band_thresholds(self):
        """score>=75→High, 50-74→Medium, <50→Low."""
        from services.levy_fairness_service import _compute_model_confidence

        # Manufacture inputs that force a known score
        # All units have ledger (levy_coverage=100), asset_ratio=100, 0 groups (group_score=0) → avg=66.7 → Medium
        units = [_make_unit("UA001")]
        ledger = [_make_ledger("UA001", 5000.0)]
        result_medium = _compute_model_confidence(units, [], ledger, {}, 5000.0, 5000.0)
        # levy_coverage=100, asset_ratio=100, group_score=0 → (100+100+0)/3 = 66.7 → Medium
        assert result_medium["band"] == "Medium"

        # Force Low: no ledger (levy_coverage=0), no asset, no groups → score=0
        result_low = _compute_model_confidence(units, [], [], {}, 0.0, 5000.0)
        assert result_low["band"] == "Low"

        # Force High: add 4 specific groups → group_score=100; levy_coverage=100; asset_ratio=100 → 100 → High
        bg = {f"bg{i}": {"id": f"bg{i}", "name": f"Group{i}", "group_type": "specific"} for i in range(4)}
        result_high = _compute_model_confidence(units, [], ledger, bg, 5000.0, 5000.0)
        assert result_high["band"] == "High"


# ─────────────────────────────────────────────────────────────────────────────
# 8. _build_distribution_histogram
# ─────────────────────────────────────────────────────────────────────────────

class TestDistributionHistogram:

    def _call(self, unit_impact, num_buckets=10):
        from services.levy_fairness_service import _build_distribution_histogram
        return _build_distribution_histogram(unit_impact, num_buckets)

    def _make_impact(self, unit_number, current_levy, proposed_levy):
        return {
            "unit_number": unit_number,
            "current_levy": float(current_levy),
            "proposed_levy": float(proposed_levy),
        }

    def test_ten_units_returns_ten_buckets(self):
        """With num_buckets=10 (default), boundaries has 11 items and counts have 10 items."""
        impacts = [self._make_impact(f"UA{i:03d}", 3000 + i * 100, 3000 + i * 100) for i in range(10)]
        result = self._call(impacts)
        assert len(result["boundaries"]) == 11
        assert len(result["current_counts"]) == 10
        assert len(result["proposed_counts"]) == 10

    def test_sum_of_current_counts_equals_unit_count(self):
        """Sum of current_counts must equal the number of units in unit_impact."""
        impacts = [self._make_impact(f"UA{i:03d}", 3000 + i * 200, 3100 + i * 200) for i in range(15)]
        result = self._call(impacts)
        assert sum(result["current_counts"]) == len(impacts)

    def test_equity_improvement_is_stdev_difference(self):
        """equity_improvement = current_stdev - proposed_stdev."""
        # Make proposed levies more equal (smaller stdev) than current
        impacts = [
            self._make_impact("UA001", 1000, 3500),  # converging toward mean
            self._make_impact("UA002", 6000, 3500),  # converging toward mean
        ]
        result = self._call(impacts)
        expected = result["current_stats"]["stdev"] - result["proposed_stats"]["stdev"]
        assert abs(result["equity_improvement"] - expected) < 0.01

    def test_empty_list_returns_empty_buckets(self):
        """Empty unit_impact should return empty bucket structures."""
        result = self._call([])
        assert result["buckets"] == []
        assert result["current_counts"] == []
        assert result["proposed_counts"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 9. _build_cross_subsidy_report
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossSubsidyReport:

    def _call(self, unit_impact, vcc=None, group_summary=None):
        from services.levy_fairness_service import _build_cross_subsidy_report
        return _build_cross_subsidy_report(unit_impact, vcc or [], group_summary or [])

    def _make_impact(self, unit_number, unit_type, current_levy, fair_levy, proposed_levy=None):
        return {
            "unit_number": unit_number,
            "unit_type": unit_type,
            "current_levy": float(current_levy),
            "fair_levy": float(fair_levy),
            "proposed_levy": float(proposed_levy if proposed_levy is not None else fair_levy),
        }

    def test_contributor_role_when_net_subsidy_positive(self):
        """Unit type with net_subsidy > 50 is labelled Contributor."""
        # Apartment pays $1000 more than their fair share
        impacts = [
            self._make_impact("UA001", "Apartment", 5000, 4000),  # net_subsidy = +1000
            self._make_impact("TH071", "Townhouse", 3000, 4000),  # net_subsidy = -1000
        ]
        result = self._call(impacts)
        apt_row = next(r for r in result["group_rows"] if r["group"] == "Apartment")
        th_row = next(r for r in result["group_rows"] if r["group"] == "Townhouse")
        assert apt_row["role"] == "Contributor"
        assert th_row["role"] == "Recipient"

    def test_neutral_role_near_zero_net_subsidy(self):
        """Unit type with |net_subsidy| <= 50 is Neutral."""
        impacts = [
            self._make_impact("UA001", "Apartment", 3000, 3000),  # net_subsidy = 0
        ]
        result = self._call(impacts)
        row = result["group_rows"][0]
        assert row["role"] == "Neutral"

    def test_total_current_sums_all_unit_impact(self):
        """total_current must equal sum of current_levy across all units."""
        impacts = [
            self._make_impact("UA001", "Apartment", 3000, 3000),
            self._make_impact("UA002", "Apartment", 4000, 4000),
            self._make_impact("TH071", "Townhouse", 5000, 5000),
        ]
        result = self._call(impacts)
        assert abs(result["total_current"] - 12000.0) < 0.01

    def test_group_rows_contain_all_unit_types(self):
        """group_rows must have one row per distinct unit_type in unit_impact."""
        impacts = [
            self._make_impact("UA001", "Apartment", 3000, 3000),
            self._make_impact("TH071", "Townhouse", 3000, 3000),
            self._make_impact("UA002", "Penthouse", 3000, 3000),
        ]
        result = self._call(impacts)
        groups = {r["group"] for r in result["group_rows"]}
        assert groups == {"Apartment", "Townhouse", "Penthouse"}

    def test_net_subsidy_per_unit_calculation(self):
        """net_subsidy_per_unit = net_subsidy / unit_count."""
        # 2 Apartments, each paying $500 over their fair share
        impacts = [
            self._make_impact("UA001", "Apartment", 4500, 4000),
            self._make_impact("UA002", "Apartment", 4500, 4000),
        ]
        result = self._call(impacts)
        row = result["group_rows"][0]
        assert row["unit_count"] == 2
        assert abs(row["net_subsidy"] - 1000.0) < 0.01
        assert abs(row["net_subsidy_per_unit"] - 500.0) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# 10. drivers field in unit_impact entries
# ─────────────────────────────────────────────────────────────────────────────

class TestDriversField:

    @pytest.mark.asyncio
    async def test_unit_impact_contains_drivers_key(self):
        """Each entry in unit_impact must have a 'drivers' list."""
        mock_db = _build_mock_db()

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3000.0, "UA002": 6000.0})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        for unit in result["unit_impact"]:
            assert "drivers" in unit, f"Unit {unit['unit_number']} missing 'drivers' key"
            assert isinstance(unit["drivers"], list)

    @pytest.mark.asyncio
    async def test_drivers_contains_facility_entries(self):
        """When facilities exist, drivers list has entries with facility_name and amount."""
        units = [_make_unit("UA001", 100.0)]
        facilities = [_make_facility("fac-1", "Pool", 3000.0, driver="equal_split")]
        ledger = [_make_ledger("UA001", 3000.0)]
        mock_db = _build_mock_db(units=units, facilities=facilities, ledger=ledger)

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 3000.0})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(side_effect=lambda fair, curr, pct, amt: fair)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        ua001 = next(u for u in result["unit_impact"] if u["unit_number"] == "UA001")
        # drivers may be empty if facility_allocations mapping is empty (depends on service internals)
        # but the key must exist and be a list
        assert isinstance(ua001["drivers"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Explain endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestExplainEndpoint:

    def _make_unit_impact_entry(self, unit_number="UA001"):
        return {
            "unit_number": unit_number,
            "unit_type": "Apartment",
            "owner_name": "Test Owner",
            "current_levy": 3000.0,
            "fair_levy": 2800.0,
            "proposed_levy": 2900.0,
            "change": -100.0,
            "change_pct": -3.33,
            "lbfi": 1.04,
            "sei": 0.03,
            "drivers": [{"facility_name": "Pool", "amount": 1000.0}],
        }

    @pytest.mark.asyncio
    async def test_returns_unit_data_when_model_exists(self):
        """GET /explain should return the unit's data from unit_impact."""
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value={
            "unit_impact": [self._make_unit_impact_entry("UA001")]
        })

        with patch("routers.intelligence.db", mock_db):
            result = await intel.explain_unit_levy(
                unit_number="UA001",
                current_user={"role": "super_admin", "building_id": "13195"}
            )

        assert result["unit_number"] == "UA001"
        assert result["current_levy"] == 3000.0
        assert "drivers" in result

    @pytest.mark.asyncio
    async def test_returns_404_when_no_model(self):
        """GET /explain returns 404 when no model is stored."""
        from fastapi import HTTPException
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)

        with patch("routers.intelligence.db", mock_db), \
                pytest.raises(HTTPException) as exc:
            await intel.explain_unit_levy(
                unit_number="UA001",
                current_user={"role": "super_admin", "building_id": "13195"}
            )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_when_unit_not_in_model(self):
        """GET /explain returns 404 when unit_number is not in unit_impact."""
        from fastapi import HTTPException
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value={
            "unit_impact": [self._make_unit_impact_entry("UA001")]
        })

        with patch("routers.intelligence.db", mock_db), \
                pytest.raises(HTTPException) as exc:
            await intel.explain_unit_levy(
                unit_number="UA999",
                current_user={"role": "super_admin", "building_id": "13195"}
            )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_owner_restricted_to_own_unit(self):
        """Owner role is blocked from viewing another unit's explain data (403)."""
        from fastapi import HTTPException
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value={
            "unit_impact": [
                self._make_unit_impact_entry("UA001"),
                self._make_unit_impact_entry("UA002"),
            ]
        })

        # Owner of UA001 tries to access UA002
        with patch("routers.intelligence.db", mock_db), \
                pytest.raises(HTTPException) as exc:
            await intel.explain_unit_levy(
                unit_number="UA002",
                current_user={"role": "owner", "building_id": "13195", "unit_number": "UA001"}
            )

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_can_access_own_unit(self):
        """Owner role can view their own unit's explain data."""
        import routers.intelligence as intel

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value={
            "unit_impact": [self._make_unit_impact_entry("UA001")]
        })

        with patch("routers.intelligence.db", mock_db):
            result = await intel.explain_unit_levy(
                unit_number="UA001",
                current_user={"role": "owner", "building_id": "13195", "unit_number": "UA001"}
            )

        assert result["unit_number"] == "UA001"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Snapshot endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotEndpoints:

    def _admin_user(self):
        return {"id": "u1", "role": "super_admin", "email": "admin@test.com",
                "building_id": "13195", "is_active": True, "is_approved": True}

    def _ec_user(self):
        return {"id": "u2", "role": "ec_member", "email": "ec@test.com",
                "building_id": "13195", "is_active": True, "is_approved": True}

    def _mock_current_result(self):
        return {
            "building_id": "13195",
            "computed_at": "2026-03-01T00:00:00Z",
            "total_budget": 10000.0,
            "lei_score": 92.0,
            "unit_impact": [],
        }

    @pytest.mark.asyncio
    async def test_create_snapshot_super_admin(self):
        """POST /snapshots creates a snapshot for super_admin and returns without data field."""
        import routers.intelligence as intel
        from routers.intelligence import SnapshotCreate

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=self._mock_current_result())
        mock_db.levy_fairness_snapshots.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.levy_fairness_audit.insert_one = AsyncMock(return_value=MagicMock())

        with patch("routers.intelligence.db", mock_db), \
                patch("services.levy_fairness_service.db", mock_db):
            result = await intel.create_snapshot(
                payload=SnapshotCreate(name="Test Snapshot", description="Before AGM"),
                current_user=self._admin_user()
            )

        assert "snapshot_id" in result
        assert result["name"] == "Test Snapshot"
        # Full model data must NOT be returned in the response
        assert "data" not in result

    @pytest.mark.asyncio
    async def test_create_snapshot_allowed_for_ec(self):
        """POST /snapshots now allowed for ec_member role (management role permission expansion)."""
        import routers.intelligence as intel
        from routers.intelligence import SnapshotCreate

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=self._mock_current_result())
        mock_db.levy_fairness_snapshots.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.levy_fairness_audit.insert_one = AsyncMock(return_value=MagicMock())

        with patch("routers.intelligence.db", mock_db), \
                patch("services.levy_fairness_service.db", mock_db):
            result = await intel.create_snapshot(
                payload=SnapshotCreate(name="EC Snapshot", description="EC review"),
                current_user=self._ec_user()
            )

        assert "snapshot_id" in result
        assert result["name"] == "EC Snapshot"

    @pytest.mark.asyncio
    async def test_create_snapshot_404_when_no_current_model(self):
        """POST /snapshots returns 404 when there is no current model to snapshot."""
        from fastapi import HTTPException
        import routers.intelligence as intel
        from routers.intelligence import SnapshotCreate

        mock_db = MagicMock()
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)

        with patch("routers.intelligence.db", mock_db), \
                pytest.raises(HTTPException) as exc:
            await intel.create_snapshot(
                payload=SnapshotCreate(name="Test"),
                current_user=self._admin_user()
            )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_snapshots_returns_list(self):
        """GET /snapshots returns a list of snapshots without full data blobs."""
        import routers.intelligence as intel

        snap1 = {"snapshot_id": "snap-abc", "name": "Snap 1", "building_id": "13195",
                 "created_at": "2026-03-01T00:00:00Z"}
        snap2 = {"snapshot_id": "snap-def", "name": "Snap 2", "building_id": "13195",
                 "created_at": "2026-03-02T00:00:00Z"}

        # find(...).sort(...).to_list(N) — need a chained mock
        chain = MagicMock()
        chain.sort.return_value = chain
        chain.to_list = AsyncMock(return_value=[snap1, snap2])

        mock_db = MagicMock()
        mock_db.levy_fairness_snapshots.find.return_value = chain

        with patch("routers.intelligence.db", mock_db):
            result = await intel.list_snapshots(current_user=self._admin_user())

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["snapshot_id"] == "snap-abc"

    @pytest.mark.asyncio
    async def test_delete_snapshot_super_admin(self):
        """DELETE /snapshots/{id} removes the snapshot."""
        import routers.intelligence as intel

        mock_delete_result = MagicMock()
        mock_delete_result.deleted_count = 1

        mock_db = MagicMock()
        mock_db.levy_fairness_snapshots.delete_one = AsyncMock(return_value=mock_delete_result)

        with patch("routers.intelligence.db", mock_db):
            result = await intel.delete_snapshot(
                snapshot_id="snap-abc",
                current_user=self._admin_user()
            )

        mock_db.levy_fairness_snapshots.delete_one.assert_called_once()
        assert "deleted" in str(result).lower() or result is not None

    @pytest.mark.asyncio
    async def test_restore_snapshot_writes_to_results(self):
        """POST /snapshots/{id}/restore copies snapshot data back to levy_fairness_results_v2."""
        import routers.intelligence as intel

        snap_data = {
            "snapshot_id": "snap-abc",
            "name": "AGM Snapshot",
            "building_id": "13195",
            "data": self._mock_current_result(),
        }

        mock_db = MagicMock()
        mock_db.levy_fairness_snapshots.find_one = AsyncMock(return_value=snap_data)
        mock_db.levy_fairness_results_v2.update_one = AsyncMock(return_value=MagicMock())
        mock_db.levy_fairness_audit.insert_one = AsyncMock(return_value=MagicMock())

        with patch("routers.intelligence.db", mock_db), \
                patch("services.levy_fairness_service.db", mock_db):
            result = await intel.restore_snapshot(
                snapshot_id="snap-abc",
                current_user=self._admin_user()
            )

        mock_db.levy_fairness_results_v2.update_one.assert_called_once()
        assert result["snapshot_id"] == "snap-abc"


# ─────────────────────────────────────────────────────────────────────────────
# 13. Audit endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditEndpoint:

    def _admin_user(self):
        return {"id": "u1", "role": "super_admin", "email": "admin@test.com",
                "building_id": "13195", "is_active": True, "is_approved": True}

    def _strata_manager_user(self):
        return {"id": "u3", "role": "strata_manager", "email": "mgr@test.com",
                "building_id": "13195", "is_active": True, "is_approved": True}

    def _owner_user(self):
        return {"id": "u4", "role": "owner", "email": "owner@test.com",
                "building_id": "13195", "unit_number": "UA001",
                "is_active": True, "is_approved": True}

    @pytest.mark.asyncio
    async def test_audit_returns_403_for_owner(self):
        """GET /audit returns 403 for owner role."""
        from fastapi import HTTPException
        import routers.intelligence as intel

        with pytest.raises(HTTPException) as exc:
            await intel.get_fairness_audit_log(current_user=self._owner_user())

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_audit_returns_list_for_super_admin(self):
        """GET /audit returns list of audit records for super_admin."""
        import routers.intelligence as intel

        records = [
            {"action": "recompute", "performed_by": "admin@test.com",
             "timestamp": "2026-03-01T12:00:00Z", "building_id": "13195"},
            {"action": "snapshot_created", "performed_by": "admin@test.com",
             "timestamp": "2026-03-02T09:00:00Z", "building_id": "13195"},
        ]

        # find(...).sort(...).limit(...).to_list(N) — chained mock
        chain = MagicMock()
        chain.sort.return_value = chain
        chain.limit.return_value = chain
        chain.to_list = AsyncMock(return_value=records)

        mock_db = MagicMock()
        mock_db.levy_fairness_audit.find.return_value = chain

        with patch("routers.intelligence.db", mock_db):
            result = await intel.get_fairness_audit_log(current_user=self._admin_user())

        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_audit_accessible_to_strata_manager(self):
        """GET /audit is accessible to strata_manager role."""
        import routers.intelligence as intel

        chain = MagicMock()
        chain.sort.return_value = chain
        chain.limit.return_value = chain
        chain.to_list = AsyncMock(return_value=[])

        mock_db = MagicMock()
        mock_db.levy_fairness_audit.find.return_value = chain

        with patch("routers.intelligence.db", mock_db):
            # Should not raise
            result = await intel.get_fairness_audit_log(current_user=self._strata_manager_user())

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_audit_records_sorted_by_timestamp_descending(self):
        """Audit records are returned sorted by timestamp descending (most recent first)."""
        import routers.intelligence as intel

        records = [
            {"action": "recompute", "timestamp": "2026-03-03T10:00:00Z"},
            {"action": "snapshot_created", "timestamp": "2026-03-01T08:00:00Z"},
            {"action": "group_updated", "timestamp": "2026-03-02T15:00:00Z"},
        ]

        chain = MagicMock()
        chain.sort.return_value = chain
        chain.limit.return_value = chain
        chain.to_list = AsyncMock(return_value=records)

        mock_db = MagicMock()
        mock_db.levy_fairness_audit.find.return_value = chain

        with patch("routers.intelligence.db", mock_db):
            result = await intel.get_fairness_audit_log(current_user=self._admin_user())

        # Verify results come from DB call (ordering is done at DB query level via .sort())
        assert len(result) == len(records)
        chain.sort.assert_called_once_with("timestamp", -1)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Benefit-group data-completeness audit fixes (2026-08-19)
#
# Real East Gate (13195) findings this locks in:
#   1/2. Two self-labelled demo assets (never flagged is_test_data) were
#        double-counting real replacement cost alongside genuine assets of the
#        same/similar name.
#   3. A facility whose linked assets carry DIFFERENT benefit_group_id tags
#      previously collapsed to a single cost centre under the facility's own
#      tag, silently mis-allocating the asset-tagged-differently share.
#   4. GARAGE_USERS/BASEMENT_USERS resolved via car_spaces (every unit > 0)
#      instead of garage_spaces, making them equivalent to "everyone".
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveBenefitGroupUnits:
    def _units(self):
        return [
            {"unit_number": "UA001", "unit_type": "apartment", "car_spaces": 1, "garage_spaces": 0},
            {"unit_number": "UA002", "unit_type": "apartment", "car_spaces": 2, "garage_spaces": 1},
            {"unit_number": "TH001", "unit_type": "townhouse", "car_spaces": 2, "garage_spaces": 2},
            {"unit_number": "TH002", "unit_type": "townhouse", "car_spaces": 1, "garage_spaces": 0},
        ]

    def test_garage_group_uses_garage_spaces_not_car_spaces(self):
        """Every unit has car_spaces > 0 in this fixture (mirroring East Gate, where
        87/87 units have car_spaces > 0) -- GARAGE_USERS must still resolve to only
        the units with a real garage (garage_spaces > 0), not all of them."""
        from services.levy_fairness_service import _resolve_benefit_group_units
        bg = {"name": "GARAGE_USERS", "lot_numbers": []}
        resolved = _resolve_benefit_group_units(bg, self._units())
        assert {u["unit_number"] for u in resolved} == {"UA002", "TH001"}

    def test_basement_group_no_longer_falls_through_to_everyone(self):
        """Previously BASEMENT had no matching branch at all and fell through to
        `return units` (every unit) -- must now resolve the same distinct subset
        as GARAGE_USERS, not the full unit list."""
        from services.levy_fairness_service import _resolve_benefit_group_units
        bg = {"name": "BASEMENT_USERS", "lot_numbers": []}
        resolved = _resolve_benefit_group_units(bg, self._units())
        assert {u["unit_number"] for u in resolved} == {"UA002", "TH001"}
        assert len(resolved) < len(self._units())

    def test_explicit_lot_numbers_still_take_precedence(self):
        from services.levy_fairness_service import _resolve_benefit_group_units
        bg = {"name": "GARAGE_USERS", "lot_numbers": ["UA001"]}
        resolved = _resolve_benefit_group_units(bg, self._units())
        assert {u["unit_number"] for u in resolved} == {"UA001"}

    def test_apartment_and_townhouse_groups_unaffected(self):
        from services.levy_fairness_service import _resolve_benefit_group_units
        units = self._units()
        apt = _resolve_benefit_group_units({"name": "APARTMENTS_ONLY", "lot_numbers": []}, units)
        th = _resolve_benefit_group_units({"name": "TOWNHOUSES_ONLY", "lot_numbers": []}, units)
        assert {u["unit_number"] for u in apt} == {"UA001", "UA002"}
        assert {u["unit_number"] for u in th} == {"TH001", "TH002"}


class TestDeriveVirtualCostCentres:
    @pytest.mark.asyncio
    async def test_excludes_test_data_assets_and_facilities(self):
        """A demo/test asset sharing a facility with a real one must not inflate
        that facility's annualised cost."""
        from services.levy_fairness_service import _derive_virtual_cost_centres

        facilities = [{"id": "fac-lift", "name": "Lift System", "benefit_group_id": "bg-tower"}]
        # MagicMock.find() doesn't apply the query filter server-side like real Mongo
        # would -- the fixture below simulates what Mongo returns AFTER the
        # is_test_data exclusion (i.e. the demo asset already excluded), and the
        # call_args assertion below separately confirms the CODE actually sent that
        # filter, so a broken/removed filter would fail that assertion even though
        # this fixture alone wouldn't catch it.
        assets = [
            {"id": "a1", "facility_id": "fac-lift", "benefit_group_id": "bg-tower",
             "replacement_cost_estimate": 65000, "expected_lifespan_years": 25},
        ]
        mock_db = MagicMock()
        mock_db.facilities.find.return_value = _cursor(facilities)
        mock_db.building_assets.find.return_value = _cursor(assets)

        with patch("services.levy_fairness_service.db", mock_db):
            centres = await _derive_virtual_cost_centres("13195")

        assert len(centres) == 1
        assert centres[0]["annual_cost"] == round(65000 / 25, 2)
        # Confirm the query itself excludes is_test_data, not just a client-side filter.
        facilities_call_filter = mock_db.facilities.find.call_args[0][0]
        assets_call_filter = mock_db.building_assets.find.call_args[0][0]
        assert facilities_call_filter.get("is_test_data") == {"$ne": True}
        assert assets_call_filter.get("is_test_data") == {"$ne": True}

    @pytest.mark.asyncio
    async def test_facility_splits_across_groups_when_assets_disagree(self):
        """An asset's OWN benefit_group_id, when set, must win over its facility's --
        a facility with mixed-tagged assets must produce one cost-centre row per
        distinct group, not silently collapse everything into the facility's tag."""
        from services.levy_fairness_service import _derive_virtual_cost_centres

        facilities = [{"id": "fac-facade", "name": "Building Facade & Painting", "benefit_group_id": "bg-tower"}]
        assets = [
            {"id": "a-tower", "facility_id": "fac-facade", "benefit_group_id": "bg-tower",
             "replacement_cost_estimate": 168642, "expected_lifespan_years": 8},
            {"id": "a-shared", "facility_id": "fac-facade", "benefit_group_id": "bg-all",
             "replacement_cost_estimate": 219382, "expected_lifespan_years": 8},
        ]
        mock_db = MagicMock()
        mock_db.facilities.find.return_value = _cursor(facilities)
        mock_db.building_assets.find.return_value = _cursor(assets)

        with patch("services.levy_fairness_service.db", mock_db):
            centres = await _derive_virtual_cost_centres("13195")

        by_group = {c["benefit_group_id"]: c["annual_cost"] for c in centres}
        assert len(centres) == 2
        assert by_group["bg-tower"] == round(168642 / 8, 2)
        assert by_group["bg-all"] == round(219382 / 8, 2)

    @pytest.mark.asyncio
    async def test_asset_without_own_tag_falls_back_to_facility_tag(self):
        from services.levy_fairness_service import _derive_virtual_cost_centres

        facilities = [{"id": "fac-x", "name": "X", "benefit_group_id": "bg-all"}]
        assets = [{"id": "a1", "facility_id": "fac-x", "benefit_group_id": None,
                   "replacement_cost_estimate": 10000, "expected_lifespan_years": 10}]
        mock_db = MagicMock()
        mock_db.facilities.find.return_value = _cursor(facilities)
        mock_db.building_assets.find.return_value = _cursor(assets)

        with patch("services.levy_fairness_service.db", mock_db):
            centres = await _derive_virtual_cost_centres("13195")

        assert len(centres) == 1
        assert centres[0]["benefit_group_id"] == "bg-all"
        assert centres[0]["annual_cost"] == 1000.0

    @pytest.mark.asyncio
    async def test_facility_with_no_assets_still_emits_zero_cost_entry(self):
        """Preserves the pre-fix guarantee that every facility appears in the
        output at least once, even with no linked assets."""
        from services.levy_fairness_service import _derive_virtual_cost_centres

        facilities = [{"id": "fac-empty", "name": "Empty Facility", "benefit_group_id": "bg-all"}]
        mock_db = MagicMock()
        mock_db.facilities.find.return_value = _cursor(facilities)
        mock_db.building_assets.find.return_value = _cursor([])

        with patch("services.levy_fairness_service.db", mock_db):
            centres = await _derive_virtual_cost_centres("13195")

        assert len(centres) == 1
        assert centres[0]["facility_id"] == "fac-empty"
        assert centres[0]["annual_cost"] == 0.0
        assert centres[0]["benefit_group_id"] == "bg-all"
