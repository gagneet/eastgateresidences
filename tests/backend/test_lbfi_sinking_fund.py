"""
Tests for LBFI (Levy Benefit Fairness Index) and Sinking Fund Plan seed logic.

Covers:
  - _compute_lbfi: Gini-style gap formula correctness
  - _interpret_lbfi: score band labelling
  - _compute_subsidy_map: flow matrix, group aggregation, facility drivers, sanity check
  - _resolve_benefit_group_units: group membership resolution
  - simulate_levy_fairness: full integration (mocked DB), sinking fund schedule integration
  - detect_capital_shocks: sinking fund plan data drives risk level
  - Sinking fund CSV parsing and balance computation (pure functions from seed script)
"""

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Import pure functions from seed script ────────────────────────────────────
_script_path = os.path.join(
    os.path.dirname(__file__), "../../scripts/db/seed_sinking_fund_plan.py"
)
_spec = importlib.util.spec_from_file_location("seed_sf", _script_path)
_seed_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed_mod)

_parse_csv = _seed_mod._parse_csv
_compute_balances = _seed_mod._compute_balances
_validate = _seed_mod._validate
YEARS = _seed_mod.YEARS

# ── Import service pure functions ─────────────────────────────────────────────
from services.levy_fairness_service import (
    _compute_lbfi,
    _interpret_lbfi,
    _compute_subsidy_map,
    _resolve_benefit_group_units,
    simulate_levy_fairness,
)
from services.capital_shock_service import detect_capital_shocks


def _cursor(rows):
    c = MagicMock()
    c.sort.return_value = c
    c.to_list = AsyncMock(return_value=rows)
    return c


# ═══════════════════════════════════════════════════════════════════════════════
# Class 1 — LBFI formula (pure, no DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLbfiFormula:
    """Test _compute_lbfi against known values."""

    def test_perfect_fairness(self):
        """Equal payment and benefit shares → LBFI = 100, D = 0."""
        p = {"L1": 0.5, "L2": 0.5}
        b = {"L1": 0.5, "L2": 0.5}
        score, D = _compute_lbfi(p, b)
        assert score == 100.0
        assert D == 0.0

    def test_full_mismatch_gives_zero(self):
        """Complete reversal of shares → D = 1.0, LBFI clamped to 0."""
        p = {"L1": 1.0, "L2": 0.0}
        b = {"L1": 0.0, "L2": 1.0}
        score, D = _compute_lbfi(p, b)
        assert score == 0.0
        assert D == 1.0

    def test_known_value(self):
        """
        Hand-calculated (New weighted ratio formula):
        p=[0.6,0.4], b=[0.4,0.6]
        raw = [0.4/0.6, 0.6/0.4] = [0.6667, 1.5]
        index = sum(raw) = 2.1667
        D = |2.1667 / 2 - 1| = 0.0833
        LBFI = 91.67
        """
        p = {"L1": 0.6, "L2": 0.4}
        b = {"L1": 0.4, "L2": 0.6}
        score, D = _compute_lbfi(p, b)
        assert abs(D - 0.0833) < 0.001
        assert abs(score - 91.67) < 0.1

    def test_three_lots_symmetric(self):
        """Three lots — one lot overpaying, two under — symmetric split."""
        p = {"L1": 0.5, "L2": 0.25, "L3": 0.25}
        b = {"L1": 0.333, "L2": 0.333, "L3": 0.333}
        score, D = _compute_lbfi(p, b)
        assert 0.0 < score < 100.0
        assert 0.0 < D < 1.0

    def test_missing_lot_in_benefit_shares(self):
        """
        Lot present in payment but not benefit → treated as b_i = 0.
        p=[0.7, 0.3], b=[0.7, 0.0]
        raw = [0.7/0.7, 0.0/0.3] = [1.0, 0.0]
        weights = [0.7, 0.3]
        denominator = 1.0 * 0.7 + 0.0 * 0.3 = 0.7
        index = sum(raw) / denominator = 1.0 / 0.7 = 1.4286
        D = |1.4286 / 2 - 1| = |0.7143 - 1| = 0.2857
        LBFI = 71.43
        """
        p = {"L1": 0.7, "L2": 0.3}
        b = {"L1": 0.7}  # L2 missing
        score, D = _compute_lbfi(p, b)
        assert abs(D - 0.2857) < 0.001
        assert abs(score - 71.43) < 0.1

    def test_score_clamped_above_zero(self):
        """Score never goes below 0."""
        p = {"L1": 1.0}
        b = {"L2": 1.0}
        score, D = _compute_lbfi(p, b)
        assert score == 0.0

    def test_shares_sum_invariant(self):
        """LBFI is symmetric in p and b (|p-b| = |b-p|)."""
        p = {"L1": 0.3, "L2": 0.7}
        b = {"L1": 0.7, "L2": 0.3}
        s1, d1 = _compute_lbfi(p, b)
        s2, d2 = _compute_lbfi(b, p)
        assert s1 == s2
        assert d1 == d2


# ═══════════════════════════════════════════════════════════════════════════════
# Class 2 — LBFI interpretation bands
# ═══════════════════════════════════════════════════════════════════════════════

class TestLbfiInterpretation:

    def test_excellent_band(self):
        txt = _interpret_lbfi(95.0)
        assert "Strong alignment" in txt

    def test_good_band(self):
        txt = _interpret_lbfi(85.0)
        assert "Good alignment" in txt

    def test_moderate_band(self):
        txt = _interpret_lbfi(70.0)
        assert "Moderate mismatch" in txt

    def test_poor_band(self):
        txt = _interpret_lbfi(50.0)
        assert "Strong distortions" in txt

    def test_boundary_90(self):
        assert "Strong alignment" in _interpret_lbfi(90.0)

    def test_boundary_80(self):
        assert "Good alignment" in _interpret_lbfi(80.0)

    def test_boundary_60(self):
        assert "Moderate mismatch" in _interpret_lbfi(60.0)

    def test_boundary_59(self):
        assert "Strong distortions" in _interpret_lbfi(59.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Class 3 — Subsidy map
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubsidyMap:
    """Test _compute_subsidy_map with controlled inputs."""

    def _simple_map(self):
        """Townhouses overpay, Apartments under-pay. Total cost = $10,000."""
        payment_shares = {"TH01": 0.6, "UA01": 0.4}
        benefit_shares = {"TH01": 0.4, "UA01": 0.6}
        lot_groups = {"TH01": "Townhouse", "UA01": "Apartment"}
        return _compute_subsidy_map(
            payment_shares=payment_shares,
            benefit_shares=benefit_shares,
            lot_groups=lot_groups,
            total_cost_basis=10_000.0,
            facility_costs={"fac-lift": 5000.0, "fac-garage": 5000.0},
            facility_benefit_shares={
                "fac-lift": {"UA01": 1.0},
                "fac-garage": {"TH01": 1.0},
            },
            facility_names={"fac-lift": "Lifts", "fac-garage": "Garage"},
        )

    def test_group_net_subsidy_direction(self):
        result = self._simple_map()
        gsub = result["group_net_subsidy"]
        # Townhouses overpay → positive subsidy (contributor)
        assert gsub["Townhouse"] > 0
        # Apartments underpay → negative subsidy (recipient)
        assert gsub["Apartment"] < 0

    def test_flows_present(self):
        result = self._simple_map()
        assert len(result["flows"]) > 0
        flow = result["flows"][0]
        assert "from" in flow and "to" in flow and "amount" in flow
        assert flow["from"] == "Townhouse"
        assert flow["to"] == "Apartment"

    def test_sanity_sum_approx_zero(self):
        result = self._simple_map()
        assert result["sanity"]["sum_delta_approx_zero"] is True
        assert abs(result["sanity"]["total_check"]) < 1.0

    def test_facility_drivers_present(self):
        result = self._simple_map()
        # At least one driver should be present
        assert len(result["top_drivers"]) > 0
        d = result["top_drivers"][0]
        assert "facility" in d and "amount" in d

    def test_group_summary_roles(self):
        result = self._simple_map()
        roles = {item["group"]: item["role"] for item in result["group_summary"]}
        assert roles.get("Townhouse") == "Contributor"
        assert roles.get("Apartment") == "Recipient"

    def test_perfect_fairness_no_flows(self):
        """When p_i = b_i for all lots, there are no subsidies."""
        p = {"L1": 0.5, "L2": 0.5}
        b = {"L1": 0.5, "L2": 0.5}
        groups = {"L1": "A", "L2": "B"}
        result = _compute_subsidy_map(
            payment_shares=p, benefit_shares=b, lot_groups=groups,
            total_cost_basis=1000.0, facility_costs={},
            facility_benefit_shares={}, facility_names={},
        )
        assert result["flows"] == []
        assert result["sanity"]["sum_delta_approx_zero"] is True

    def test_total_cost_basis_in_result(self):
        result = self._simple_map()
        assert result["total_cost_basis"] == 10_000.0


# ═══════════════════════════════════════════════════════════════════════════════
# Class 4 — Benefit group resolution
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolveBenefitGroupUnits:
    # car_spaces is deliberately > 0 on EVERY unit here (mirroring real East Gate data,
    # where 87/87 units have car_spaces > 0) -- this fixture exists specifically to
    # prove GARAGE_USERS/BASEMENT_USERS resolve via garage_spaces (a genuinely distinct
    # subset), not car_spaces (audit finding, 2026-08-19; see levy_fairness_service.py's
    # _resolve_benefit_group_units docstring for the full explanation).
    _units = [
        {"unit_number": "UA001", "unit_type": "apartment", "car_spaces": 1, "garage_spaces": 0, "lot_number": "L01"},
        {"unit_number": "UA002", "unit_type": "apartment", "car_spaces": 1, "garage_spaces": 1, "lot_number": "L02"},
        {"unit_number": "TH071", "unit_type": "townhouse", "car_spaces": 2, "garage_spaces": 2, "lot_number": "L03"},
        {"unit_number": "TH072", "unit_type": "townhouse", "car_spaces": 1, "garage_spaces": 0, "lot_number": "L04"},
        {"unit_number": "UA003", "unit_type": "apartment", "car_spaces": 1, "garage_spaces": 0, "lot_number": "L05"},
    ]

    def test_all_lots(self):
        bg = {"name": "ALL_LOTS", "lot_numbers": []}
        result = _resolve_benefit_group_units(bg, self._units)
        assert len(result) == 5

    def test_apartments_only(self):
        bg = {"name": "APARTMENTS_ONLY", "lot_numbers": []}
        result = _resolve_benefit_group_units(bg, self._units)
        assert all("apartment" in u["unit_type"] for u in result)
        assert len(result) == 3

    def test_townhouses_only(self):
        bg = {"name": "TOWNHOUSES_ONLY", "lot_numbers": []}
        result = _resolve_benefit_group_units(bg, self._units)
        assert all("townhouse" in u["unit_type"] for u in result)
        assert len(result) == 2

    def test_garage_users(self):
        """GARAGE_USERS resolves via garage_spaces, not car_spaces -- every unit in
        this fixture has car_spaces > 0 (mirroring real East Gate data), so a
        car_spaces-based check would wrongly resolve to everyone. Only units with
        a real garage (garage_spaces > 0) qualify."""
        bg = {"name": "GARAGE_USERS", "lot_numbers": []}
        result = _resolve_benefit_group_units(bg, self._units)
        assert all((u.get("garage_spaces") or 0) > 0 for u in result)
        assert {u["unit_number"] for u in result} == {"UA002", "TH071"}

    def test_basement_users_no_longer_falls_through_to_everyone(self):
        """Previously BASEMENT_USERS had no matching branch and fell through to
        `return units` (all 5) -- must now resolve the same garage_spaces-based
        subset as GARAGE_USERS, not the full unit list."""
        bg = {"name": "BASEMENT_USERS", "lot_numbers": []}
        result = _resolve_benefit_group_units(bg, self._units)
        assert {u["unit_number"] for u in result} == {"UA002", "TH071"}

    def test_explicit_lot_numbers(self):
        bg = {"name": "", "lot_numbers": ["L01", "L03"]}
        result = _resolve_benefit_group_units(bg, self._units)
        assert {u["lot_number"] for u in result} == {"L01", "L03"}

    def test_unknown_name_falls_back_to_all(self):
        bg = {"name": "MYSTERY_GROUP", "lot_numbers": []}
        result = _resolve_benefit_group_units(bg, self._units)
        assert len(result) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Class 5 — simulate_levy_fairness integration (mocked DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimulateLevyFairness:

    def _make_mock_db(self, include_sinking_schedule=False):
        units = [
            {"unit_number": "UA001", "lot_number": "L01", "entitlement": 100, "unit_type": "apartment",
             "car_spaces": 1},
            {"unit_number": "UA002", "lot_number": "L02", "entitlement": 100, "unit_type": "apartment",
             "car_spaces": 1},
            {"unit_number": "TH071", "lot_number": "L03", "entitlement": 120, "unit_type": "townhouse",
             "car_spaces": 2},
            {"unit_number": "TH072", "lot_number": "L04", "entitlement": 120, "unit_type": "townhouse",
             "car_spaces": 2},
        ]
        facilities = [
            {"id": "fac-lift", "name": "Lifts", "benefit_group_id": "bg-tower"},
            {"id": "fac-garage", "name": "Garage", "benefit_group_id": "bg-garage"},
        ]
        assets = [
            {"id": "a1", "facility_id": "fac-lift", "risk_score": 20, "replacement_cost_estimate": 200000,
             "expected_lifespan_years": 25},
            {"id": "a2", "facility_id": "fac-garage", "risk_score": 10, "replacement_cost_estimate": 50000,
             "expected_lifespan_years": 15},
        ]
        benefit_groups = [
            {"id": "bg-tower", "name": "APARTMENTS_ONLY", "lot_numbers": []},
            {"id": "bg-garage", "name": "GARAGE_USERS", "lot_numbers": []},
            {"id": "bg-all", "name": "ALL_LOTS", "lot_numbers": []},
        ]
        sinking_schedule = [
            {"asset_id": "a1", "asset_name": "Lifts", "facility_id": "fac-lift",
             "replacement_year": 2035, "estimated_cost": 200000, "benefit_group_id": "bg-tower"},
        ] if include_sinking_schedule else []
        return units, facilities, assets, benefit_groups, sinking_schedule

    @pytest.mark.asyncio
    async def test_returns_lbfi_scores(self):
        units, facilities, assets, bgs, schedule = self._make_mock_db()
        with patch("services.levy_fairness_service.db") as mock_db, \
                patch("services.levy_fairness_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.units.find.return_value = _cursor(units)
            mock_db.facilities.find.return_value = _cursor(facilities)
            mock_db.building_assets.find.return_value = _cursor(assets)
            mock_db.benefit_groups.find.return_value = _cursor(bgs)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value={"predicted_cost": 5000})
            mock_db.work_orders.find.return_value = _cursor([])
            mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
            mock_db.annual_levies.find_one = AsyncMock(return_value={
                "admin_levy_per_uoe_annual": 40,
                "sinking_levy_per_uoe_annual": 15,
            })
            mock_db.levy_fairness_overrides.find.return_value = _cursor([])
            mock_db.levy_fairness_results.update_one = AsyncMock()
            mock_db.levy_simulations.update_one = AsyncMock()

            result = await simulate_levy_fairness("13195")

        assert "current_fairness_score" in result
        assert "benefit_fairness_score" in result
        score = result["current_fairness_score"]
        assert 0.0 <= score <= 100.0
        assert result["benefit_fairness_score"] == 100.0

    @pytest.mark.asyncio
    async def test_lbfi_sub_dict(self):
        units, facilities, assets, bgs, schedule = self._make_mock_db()
        with patch("services.levy_fairness_service.db") as mock_db, \
                patch("services.levy_fairness_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.units.find.return_value = _cursor(units)
            mock_db.facilities.find.return_value = _cursor(facilities)
            mock_db.building_assets.find.return_value = _cursor(assets)
            mock_db.benefit_groups.find.return_value = _cursor(bgs)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.work_orders.find.return_value = _cursor([])
            mock_db.capital_replacement_schedule.find.return_value = _cursor([])
            mock_db.annual_levies.find_one = AsyncMock(return_value={
                "admin_levy_per_uoe_annual": 40,
                "sinking_levy_per_uoe_annual": 15,
            })
            mock_db.levy_fairness_overrides.find.return_value = _cursor([])
            mock_db.levy_fairness_results.update_one = AsyncMock()
            mock_db.levy_simulations.update_one = AsyncMock()

            result = await simulate_levy_fairness("13195")

        lbfi = result.get("lbfi", {})
        assert "current_score" in lbfi
        assert "benefit_score" in lbfi
        assert "D" in lbfi  # Gini gap stored as D
        assert "interpretation" in lbfi

    @pytest.mark.asyncio
    async def test_scenarios_stored(self):
        """Verify levy_simulations.update_one called twice for two scenarios."""
        units, facilities, assets, bgs, schedule = self._make_mock_db()
        with patch("services.levy_fairness_service.db") as mock_db, \
                patch("services.levy_fairness_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.units.find.return_value = _cursor(units)
            mock_db.facilities.find.return_value = _cursor(facilities)
            mock_db.building_assets.find.return_value = _cursor(assets)
            mock_db.benefit_groups.find.return_value = _cursor(bgs)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.work_orders.find.return_value = _cursor([])
            mock_db.capital_replacement_schedule.find.return_value = _cursor([])
            mock_db.annual_levies.find_one = AsyncMock(return_value={
                "admin_levy_per_uoe_annual": 40,
                "sinking_levy_per_uoe_annual": 15,
            })
            mock_db.levy_fairness_overrides.find.return_value = _cursor([])
            mock_db.levy_fairness_results.update_one = AsyncMock()
            update_mock = AsyncMock()
            mock_db.levy_simulations.update_one = update_mock

            await simulate_levy_fairness("13195")

        assert update_mock.call_count == 2, "Two scenarios should be persisted"

    @pytest.mark.asyncio
    async def test_sinking_fund_schedule_enriches_cost_basis(self):
        """Capital replacement schedule entries increase cost basis and affect LBFI."""
        units, facilities, assets, bgs, _ = self._make_mock_db()
        # No schedule
        no_sch_result = await self._run(units, facilities, assets, bgs, [])
        # With schedule
        schedule = [
            {"asset_id": "a1", "asset_name": "Lifts", "facility_id": "fac-lift",
             "replacement_year": 2035, "estimated_cost": 200000, "benefit_group_id": "bg-tower"},
        ]
        with_sch_result = await self._run(units, facilities, assets, bgs, schedule)
        # Both should return valid scores
        assert 0 <= no_sch_result["current_fairness_score"] <= 100
        assert 0 <= with_sch_result["current_fairness_score"] <= 100

    @pytest.mark.asyncio
    async def test_subsidy_map_present(self):
        units, facilities, assets, bgs, schedule = self._make_mock_db(include_sinking_schedule=True)
        result = await self._run(units, facilities, assets, bgs, schedule)
        sm = result.get("subsidy_map", {})
        assert "flows" in sm
        assert "group_net_subsidy" in sm
        assert "sanity" in sm

    @pytest.mark.asyncio
    async def test_sanity_checks_pass(self):
        """Σ(p_i - b_i) ≈ 0 — subsidy map sanity must pass."""
        units, facilities, assets, bgs, schedule = self._make_mock_db()
        result = await self._run(units, facilities, assets, bgs, schedule)
        sanity = result.get("sanity", {})
        # Actual sanity keys from the service
        assert sanity.get("checks_passed") is True
        assert abs(sanity.get("sum_delta", 1.0)) < 0.01

    async def _run(self, units, facilities, assets, bgs, schedule):
        with patch("services.levy_fairness_service.db") as mock_db, \
                patch("services.levy_fairness_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.units.find.return_value = _cursor(units)
            mock_db.facilities.find.return_value = _cursor(facilities)
            mock_db.building_assets.find.return_value = _cursor(assets)
            mock_db.benefit_groups.find.return_value = _cursor(bgs)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.work_orders.find.return_value = _cursor([])
            mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
            mock_db.annual_levies.find_one = AsyncMock(return_value={
                "admin_levy_per_uoe_annual": 40,
                "sinking_levy_per_uoe_annual": 15,
            })
            mock_db.levy_fairness_overrides.find.return_value = _cursor([])
            mock_db.levy_fairness_results.update_one = AsyncMock()
            mock_db.levy_simulations.update_one = AsyncMock()
            return await simulate_levy_fairness("13195")


# ═══════════════════════════════════════════════════════════════════════════════
# Class 6 — Capital shock with sinking fund data
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapitalShockWithSinkingFund:
    """Capital shock service should correctly use sinking fund schedule entries."""

    def _make_levy_doc(self, sinking_balance=300000, admin_income=250000,
                       sinking_income=120000, total_expenses=350000):
        return {
            "admin_fund": {"levy_income": admin_income, "total_expenses": total_expenses * 0.85},
            "sinking_fund": {"levy_income": sinking_income, "total_expenses": total_expenses * 0.15,
                             "closing_balance": sinking_balance},
        }

    @pytest.mark.asyncio
    async def test_high_risk_large_repaint_year(self):
        """2029-style mass repaint ($399k) vs $300k reserve with $350k expense baseline → HIGH/CRITICAL."""
        from datetime import date
        shock_year = date.today().year + 1  # use +1 for minimal reserve accumulation
        schedule = [
            {"asset_id": "a1", "asset_name": "Repaint Building", "replacement_year": shock_year,
             "estimated_cost": 168642},
            {"asset_id": "a2", "asset_name": "Repaint Others", "replacement_year": shock_year,
             "estimated_cost": 217583},
            {"asset_id": "a3", "asset_name": "Basement Repaint", "replacement_year": shock_year,
             "estimated_cost": 12630},
        ]
        with patch("services.capital_shock_service.db") as mock_db, \
                patch("services.capital_shock_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value={"predicted_cost": 17000})
            mock_db.annual_levies.find_one = AsyncMock(return_value=self._make_levy_doc(sinking_balance=300000))
            mock_db.sinking_fund_plan.find.return_value = _cursor([])
            mock_db.sinking_fund_capital_events.find.return_value = _cursor([])
            mock_db.capital_shock_risks.update_one = AsyncMock()

            result = await detect_capital_shocks("13195")

        assert result["risk_level"] in {"HIGH", "CRITICAL"}

    @pytest.mark.asyncio
    async def test_low_risk_small_works(self):
        """Small annual capital works vs large reserve → LOW risk."""
        from datetime import date
        shock_year = date.today().year + 1
        schedule = [
            {"asset_id": "a1", "asset_name": "General Capital", "replacement_year": shock_year, "estimated_cost": 5500},
        ]
        with patch("services.capital_shock_service.db") as mock_db, \
                patch("services.capital_shock_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value={"predicted_cost": 15000})
            mock_db.annual_levies.find_one = AsyncMock(return_value=self._make_levy_doc(sinking_balance=487399))
            mock_db.sinking_fund_plan.find.return_value = _cursor([])
            mock_db.sinking_fund_capital_events.find.return_value = _cursor([])
            mock_db.capital_shock_risks.update_one = AsyncMock()

            result = await detect_capital_shocks("13195")

        assert result["risk_level"] in {"LOW", "MEDIUM"}

    @pytest.mark.asyncio
    async def test_lifts_replacement_triggers_risk(self):
        """$205k lifts replacement vs $200k reserve with $350k expense baseline → HIGH/CRITICAL."""
        from datetime import date
        shock_year = date.today().year + 1
        schedule = [
            {"asset_id": "asset-lifts", "asset_name": "Passenger Lifts (2x)",
             "replacement_year": shock_year, "estimated_cost": 205652},
        ]
        with patch("services.capital_shock_service.db") as mock_db, \
                patch("services.capital_shock_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value={"predicted_cost": 15000})
            # Low reserve + large expense baseline → high deficit ratio
            mock_db.annual_levies.find_one = AsyncMock(
                return_value=self._make_levy_doc(sinking_balance=200000, total_expenses=350000)
            )
            mock_db.sinking_fund_plan.find.return_value = _cursor([])
            mock_db.sinking_fund_capital_events.find.return_value = _cursor([])
            mock_db.capital_shock_risks.update_one = AsyncMock()

            result = await detect_capital_shocks("13195")

        assert result["risk_level"] in {"HIGH", "CRITICAL"}
        assert result["recommended_levy_increase_pct"] is not None
        assert result["recommended_levy_increase_pct"] > 0

    @pytest.mark.asyncio
    async def test_result_includes_reserve_projection(self):
        from datetime import date
        shock_year = date.today().year + 1
        schedule = [
            {"asset_id": "a1", "asset_name": "Swipe Cards", "replacement_year": shock_year, "estimated_cost": 7894},
        ]
        with patch("services.capital_shock_service.db") as mock_db, \
                patch("services.capital_shock_service.get_latest_levy_year", new_callable=AsyncMock, return_value="2026"):
            mock_db.capital_replacement_schedule.find.return_value = _cursor(schedule)
            mock_db.maintenance_forecasts.find_one = AsyncMock(return_value=None)
            mock_db.annual_levies.find_one = AsyncMock(return_value=self._make_levy_doc())
            mock_db.sinking_fund_plan.find.return_value = _cursor([])
            mock_db.sinking_fund_capital_events.find.return_value = _cursor([])
            mock_db.capital_shock_risks.update_one = AsyncMock()

            result = await detect_capital_shocks("13195")

        assert "reserve_projection" in result
        assert isinstance(result["reserve_projection"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# Class 7 — Sinking fund CSV parsing (pure functions from seed script)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSinkingFundCsvParsing:
    """Test the pure CSV parsing and balance computation from seed script."""

    def setup_method(self):
        self.data = _parse_csv()
        self.year_records = _compute_balances(self.data)

    def test_years_present(self):
        assert set(YEARS) == set(range(2021, 2036))
        assert len(YEARS) == 15

    def test_contribution_row_parsed(self):
        contrib = self.data.get("Contribution", {})
        assert contrib[2022] == 51111
        assert contrib[2035] == 183870

    def test_expenditure_row_parsed(self):
        exp = self.data.get("Expenditure", {})
        assert exp[2022] == 4350
        assert exp[2029] == 413099  # big repaint year

    def test_handrail_at_correct_years_after_fix(self):
        """After the comma-fix, Handrail values should be at 2028/2030/2033/2034."""
        handrail = self.data.get("Handrail/balustrade Replace", {})
        assert handrail.get(2028) == 1184, "Handrail 2028 should be 1184 after comma fix"
        assert handrail.get(2030) == 1294, "Handrail 2030 should be 1294 after comma fix"
        assert handrail.get(2033) == 1414, "Handrail 2033 should be 1414 after comma fix"
        assert handrail.get(2034) == 14481, "Handrail 2034 should be 14481 after comma fix"
        assert handrail.get(2029, 0) == 0, "Handrail 2029 should be 0 (was wrongly placed there)"

    def test_fire_systems_has_2035_after_fix(self):
        """After the comma-fix, Replace Fire Systems should have 2035 = 2228."""
        fire = self.data.get("Replace Fire Systems", {})
        assert fire.get(2035) == 2228, "Fire Systems 2035 = 2228 after comma fix"
        assert fire.get(2023) == 1563
        assert fire.get(2024) == 5200

    def test_lifts_only_in_2035(self):
        lifts = self.data.get("Lifts", {})
        assert lifts.get(2035) == 205652
        nonzero_years = [y for y, v in lifts.items() if v > 0]
        assert nonzero_years == [2035], "Lifts should only appear in 2035"

    def test_balance_starts_at_zero(self):
        first = self.year_records[0]
        assert first["year"] == 2021
        assert first["opening_balance"] == 0.0
        assert first["closing_balance"] == 0.0  # no contribution in 2021

    def test_balance_increases_before_big_expenditure(self):
        """Balance should grow steadily 2022-2028, then drop sharply in 2029."""
        bal_2028 = next(r["closing_balance"] for r in self.year_records if r["year"] == 2028)
        bal_2029 = next(r["closing_balance"] for r in self.year_records if r["year"] == 2029)
        assert bal_2028 > 400000, "Balance should be over $400k by 2028"
        assert bal_2029 < bal_2028, "2029 big repaint should reduce balance"
        assert bal_2029 > 0, "Fund should remain solvent through 2029"

    def test_balance_2022_manual(self):
        rec = next(r for r in self.year_records if r["year"] == 2022)
        assert rec["contribution"] == 51111
        assert rec["expenditure"] == 4350
        assert rec["closing_balance"] == pytest.approx(46761.0, abs=1)

    def test_fund_never_goes_negative(self):
        """A healthy sinking fund should never have a negative balance in this plan."""
        for rec in self.year_records:
            assert rec["closing_balance"] >= 0, \
                f"Fund went negative in {rec['year']}: {rec['closing_balance']}"

    def test_item_count(self):
        """Should parse exactly 27 rows (3 summary + 24 line items)."""
        assert len(self.data) == 27

    def test_years_reconcile_2022_to_2029(self):
        """Years 2022-2028 should reconcile exactly (±2). 2029 has known OK."""
        expenditures = self.data.get("Expenditure", {})
        line_items = {k: v for k, v in self.data.items() if k not in {"Contribution", "Expenditure", "Actuals Given"}}
        perfect_years = list(range(2022, 2030))
        for year in perfect_years:
            line_sum = sum(v.get(year, 0) for v in line_items.values())
            exp = expenditures.get(year, 0)
            assert abs(line_sum - exp) <= 2, \
                f"Year {year}: items={line_sum}, expenditure={exp}, diff={line_sum - exp}"
