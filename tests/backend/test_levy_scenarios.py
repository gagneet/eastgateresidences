"""
Tests for the Levy Scenario Modeller service and models.

Coverage:
  - ScenarioInputs computation (levy distribution per group/unit)
  - Single group (all units in one group)
  - Two groups with different admin/sinking percentages
  - UOE override detection
  - Grand total conservation
  - Zero-balance group guard (no ZeroDivisionError)
  - resolve_units_to_groups prefix matching and explicit assignment
  - Pydantic model validation (share totals must sum to 100%)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from services.levy_scenario_service import (
    GroupInput,
    ScenarioInputs,
    UnitInput,
    compute_scenario,
    resolve_units_to_groups,
)
from models.levy_scenarios import LevyScenarioCreate, ScenarioGroupCreate


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _unit(num: str, uoe: float, scenario_uoe: float | None = None) -> UnitInput:
    s_uoe = scenario_uoe if scenario_uoe is not None else uoe
    return UnitInput(
        unit_number=num,
        original_uoe=uoe,
        scenario_uoe=s_uoe,
        current_admin_levy=100.0,
        current_sinking_levy=50.0,
    )


def _grp(name: str, admin_pct: float, sinking_pct: float, units: list, color="#6366f1") -> GroupInput:
    return GroupInput(
        group_name=name,
        group_color=color,
        admin_share_pct=admin_pct,
        sinking_share_pct=sinking_pct,
        units=units,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests: compute_scenario
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeScenario:

    def test_single_group_all_units_proportional(self):
        """One group, 100% of budget — each unit's levy ∝ its UOE."""
        # UA001 has 2× UOE of UA002, so gets 2× levy
        units = [_unit("UA001", 2.0), _unit("UA002", 1.0)]
        grp = _grp("Apartments", 100.0, 100.0, units)
        inputs = ScenarioInputs(
            total_admin_budget=3000.0,
            total_sinking_budget=900.0,
            groups=[grp],
            current_admin_rate_per_uoe=50.0,
            current_sinking_rate_per_uoe=25.0,
        )
        result = compute_scenario(inputs)
        ur = {u["unit_number"]: u for u in result["unit_results"]}
        # group UOE = 3.0; UA001 share = 2/3; UA002 share = 1/3
        assert abs(ur["UA001"]["scenario_admin_levy"] - 2000.0) < 0.01
        assert abs(ur["UA002"]["scenario_admin_levy"] - 1000.0) < 0.01
        assert abs(ur["UA001"]["scenario_sinking_levy"] - 600.0) < 0.01
        assert abs(ur["UA002"]["scenario_sinking_levy"] - 300.0) < 0.01

    def test_grand_total_preserved(self):
        """Σ(unit_admin_levy) == total_admin_budget regardless of group config."""
        units_a = [_unit("UA001", 2.0), _unit("UA002", 1.5)]
        units_b = [_unit("TH001", 3.0), _unit("TH002", 2.0)]
        grp_a = _grp("Apartments", 60.0, 40.0, units_a)
        grp_b = _grp("Townhouses", 40.0, 60.0, units_b)
        inputs = ScenarioInputs(
            total_admin_budget=10000.0,
            total_sinking_budget=5000.0,
            groups=[grp_a, grp_b],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        total_admin = sum(u["scenario_admin_levy"] for u in result["unit_results"])
        total_sinking = sum(u["scenario_sinking_levy"] for u in result["unit_results"])
        assert abs(total_admin - 10000.0) < 0.05
        assert abs(total_sinking - 5000.0) < 0.05

    def test_two_group_split_different_admin_sinking_pcts(self):
        """Apartments pay 70% admin / 30% sinking; Townhouses pay 30% admin / 70% sinking."""
        units_a = [_unit("UA001", 1.0)]
        units_b = [_unit("TH001", 1.0)]
        inputs = ScenarioInputs(
            total_admin_budget=1000.0,
            total_sinking_budget=1000.0,
            groups=[
                _grp("Apartments", 70.0, 30.0, units_a),
                _grp("Townhouses", 30.0, 70.0, units_b),
            ],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        ur = {u["unit_number"]: u for u in result["unit_results"]}
        assert abs(ur["UA001"]["scenario_admin_levy"] - 700.0) < 0.01
        assert abs(ur["UA001"]["scenario_sinking_levy"] - 300.0) < 0.01
        assert abs(ur["TH001"]["scenario_admin_levy"] - 300.0) < 0.01
        assert abs(ur["TH001"]["scenario_sinking_levy"] - 700.0) < 0.01

    def test_uoe_override_detection(self):
        """uoe_changed is True if scenario_uoe differs from original_uoe."""
        units = [
            _unit("UA001", 1.0, scenario_uoe=1.5),  # changed
            _unit("UA002", 2.0, scenario_uoe=2.0),  # unchanged
        ]
        inputs = ScenarioInputs(
            total_admin_budget=100.0,
            total_sinking_budget=100.0,
            groups=[_grp("All", 100.0, 100.0, units)],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        ur = {u["unit_number"]: u for u in result["unit_results"]}
        assert ur["UA001"]["uoe_changed"] is True
        assert ur["UA002"]["uoe_changed"] is False

    def test_uoe_preservation_check(self):
        """uoe_total_preserved is True when scenario UOE == original UOE."""
        # UA001 gains 0.5, UA002 loses 0.5 → total preserved
        units = [
            _unit("UA001", 1.0, scenario_uoe=1.5),
            _unit("UA002", 2.0, scenario_uoe=1.5),
        ]
        inputs = ScenarioInputs(
            total_admin_budget=100.0,
            total_sinking_budget=100.0,
            groups=[_grp("All", 100.0, 100.0, units)],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        assert result["total_original_uoe"] == pytest.approx(3.0)
        assert result["total_scenario_uoe"] == pytest.approx(3.0)
        assert result["uoe_total_preserved"] is True
        assert result["uoe_diff"] < 0.01

    def test_uoe_not_preserved_flag(self):
        """uoe_total_preserved is False when total UOE changes."""
        units = [
            _unit("UA001", 1.0, scenario_uoe=2.0),  # gains 1.0, no offsetting unit
        ]
        inputs = ScenarioInputs(
            total_admin_budget=100.0,
            total_sinking_budget=100.0,
            groups=[_grp("All", 100.0, 100.0, units)],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        assert result["uoe_total_preserved"] is False

    def test_zero_uoe_group_no_division_error(self):
        """If a group has all zero UOE units, no ZeroDivisionError is raised."""
        units = [_unit("UA001", 0.0)]
        inputs = ScenarioInputs(
            total_admin_budget=1000.0,
            total_sinking_budget=500.0,
            groups=[_grp("Zero UOE Group", 100.0, 100.0, units)],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        ur = result["unit_results"][0]
        assert ur["scenario_admin_levy"] == 0.0  # 0 UOE → 0 levy

    def test_delta_calculation(self):
        """total_delta = scenario_total - current_total."""
        unit = UnitInput(
            unit_number="UA001",
            original_uoe=1.0,
            scenario_uoe=1.0,
            current_admin_levy=400.0,
            current_sinking_levy=100.0,
        )
        inputs = ScenarioInputs(
            total_admin_budget=600.0,
            total_sinking_budget=200.0,
            groups=[_grp("All", 100.0, 100.0, [unit])],
            current_admin_rate_per_uoe=400.0,
            current_sinking_rate_per_uoe=100.0,
        )
        result = compute_scenario(inputs)
        ur = result["unit_results"][0]
        # current total = 500, scenario = 800
        assert ur["current_total_levy"] == pytest.approx(500.0)
        assert ur["scenario_total_levy"] == pytest.approx(800.0)
        assert ur["total_delta"] == pytest.approx(300.0)
        assert ur["total_delta_pct"] == pytest.approx(60.0)

    def test_group_summaries_count(self):
        """One group_summary per group."""
        inputs = ScenarioInputs(
            total_admin_budget=100.0,
            total_sinking_budget=50.0,
            groups=[
                _grp("A", 60.0, 40.0, [_unit("UA001", 1.0)]),
                _grp("B", 40.0, 60.0, [_unit("TH001", 1.0)]),
            ],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        assert len(result["group_summaries"]) == 2
        names = [g["group_name"] for g in result["group_summaries"]]
        assert "A" in names and "B" in names

    def test_three_equal_groups(self):
        """Three groups each with 33.33% — grand total still preserved."""
        units_per_group = [_unit(f"U{i:03d}", 1.0) for i in range(3)]
        inputs = ScenarioInputs(
            total_admin_budget=3000.0,
            total_sinking_budget=900.0,
            groups=[
                _grp("G1", 33.33, 33.33, [units_per_group[0]]),
                _grp("G2", 33.33, 33.33, [units_per_group[1]]),
                _grp("G3", 33.34, 33.34, [units_per_group[2]]),
            ],
            current_admin_rate_per_uoe=0.0,
            current_sinking_rate_per_uoe=0.0,
        )
        result = compute_scenario(inputs)
        total_admin = sum(u["scenario_admin_levy"] for u in result["unit_results"])
        # 33.33 + 33.33 + 33.34 = 100 → sum should be 3000
        assert abs(total_admin - 3000.0) < 0.10


# ─────────────────────────────────────────────────────────────────────────────
# Tests: resolve_units_to_groups
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveUnitsToGroups:

    def _all_units(self):
        return [
            {"unit_number": "UA001", "entitlement": 2.0},
            {"unit_number": "UA002", "entitlement": 1.5},
            {"unit_number": "TH001", "entitlement": 3.0},
            {"unit_number": "TH002", "entitlement": 2.5},
        ]

    def test_prefix_matching(self):
        """UA prefix assigns UA001 and UA002; TH prefix assigns TH001 and TH002."""
        groups_config = [
            {"group_name": "Apartments", "color": "#6366f1", "unit_type_prefixes": ["UA"],
             "unit_numbers": [], "admin_share_pct": 50, "sinking_share_pct": 50, "uoe_overrides": []},
            {"group_name": "Townhouses", "color": "#f59e0b", "unit_type_prefixes": ["TH"],
             "unit_numbers": [], "admin_share_pct": 50, "sinking_share_pct": 50, "uoe_overrides": []},
        ]
        groups = resolve_units_to_groups(groups_config, self._all_units(), {}, {})
        by_name = {g.group_name: g for g in groups}
        assert {u.unit_number for u in by_name["Apartments"].units} == {"UA001", "UA002"}
        assert {u.unit_number for u in by_name["Townhouses"].units} == {"TH001", "TH002"}

    def test_explicit_unit_numbers(self):
        """Explicit unit_numbers override prefix matching."""
        groups_config = [
            {"group_name": "Special", "color": "#6366f1", "unit_type_prefixes": [],
             "unit_numbers": ["UA001", "TH001"], "admin_share_pct": 60, "sinking_share_pct": 60, "uoe_overrides": []},
            {"group_name": "Rest", "color": "#10b981", "unit_type_prefixes": ["UA", "TH"],
             "unit_numbers": [], "admin_share_pct": 40, "sinking_share_pct": 40, "uoe_overrides": []},
        ]
        groups = resolve_units_to_groups(groups_config, self._all_units(), {}, {})
        by_name = {g.group_name: g for g in groups}
        # UA001 and TH001 go to Special; UA002 and TH002 go to Rest
        special_nums = {u.unit_number for u in by_name["Special"].units}
        rest_nums = {u.unit_number for u in by_name["Rest"].units}
        assert special_nums == {"UA001", "TH001"}
        assert rest_nums == {"UA002", "TH002"}

    def test_first_group_wins_for_overlapping_units(self):
        """If a unit matches both groups, it goes to the first group only."""
        groups_config = [
            {"group_name": "G1", "color": "#6366f1", "unit_type_prefixes": ["UA", "TH"],
             "unit_numbers": [], "admin_share_pct": 60, "sinking_share_pct": 60, "uoe_overrides": []},
            {"group_name": "G2", "color": "#f59e0b", "unit_type_prefixes": ["TH"],
             "unit_numbers": [], "admin_share_pct": 40, "sinking_share_pct": 40, "uoe_overrides": []},
        ]
        groups = resolve_units_to_groups(groups_config, self._all_units(), {}, {})
        by_name = {g.group_name: g for g in groups}
        # All units taken by G1; G2 gets nothing
        assert len(by_name["G1"].units) == 4
        assert len(by_name["G2"].units) == 0

    def test_uoe_override_applied(self):
        """UOE overrides embedded in group config are applied to the correct unit."""
        groups_config = [
            {"group_name": "Apartments", "color": "#6366f1", "unit_type_prefixes": ["UA"],
             "unit_numbers": [], "admin_share_pct": 100, "sinking_share_pct": 100,
             "uoe_overrides": [{"unit_number": "UA001", "adjusted_uoe": 5.0}]},
        ]
        groups = resolve_units_to_groups(groups_config, self._all_units(), {}, {})
        grp = groups[0]
        ua001 = next(u for u in grp.units if u.unit_number == "UA001")
        assert ua001.scenario_uoe == pytest.approx(5.0)
        ua002 = next(u for u in grp.units if u.unit_number == "UA002")
        assert ua002.scenario_uoe == pytest.approx(1.5)  # unchanged

    def test_original_uoe_from_db(self):
        """original_uoe comes from the entitlement field in unit DB document."""
        groups_config = [
            {"group_name": "All", "color": "#6366f1", "unit_type_prefixes": ["UA"],
             "unit_numbers": [], "admin_share_pct": 100, "sinking_share_pct": 100, "uoe_overrides": []},
        ]
        groups = resolve_units_to_groups(groups_config, self._all_units(), {}, {})
        ua001 = next(u for u in groups[0].units if u.unit_number == "UA001")
        assert ua001.original_uoe == pytest.approx(2.0)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Pydantic model validation
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyScenarioModelValidation:

    def _base_group(self, admin_pct: float, sinking_pct: float) -> dict:
        return {
            "group_name": "Test Group",
            "admin_share_pct": admin_pct,
            "sinking_share_pct": sinking_pct,
            "unit_type_prefixes": ["UA"],
        }

    def test_valid_two_groups_sum_to_100(self):
        """Two groups summing to exactly 100% passes validation."""
        scenario = LevyScenarioCreate(
            scenario_name="Test",
            financial_year="2026-2027",
            total_admin_budget=10000.0,
            total_sinking_budget=5000.0,
            groups=[
                ScenarioGroupCreate(**self._base_group(60.0, 40.0)),
                ScenarioGroupCreate(
                    **{**self._base_group(40.0, 60.0), "group_name": "B", "unit_type_prefixes": ["TH"]}),
            ],
        )
        assert scenario.scenario_name == "Test"

    def test_admin_shares_not_100_raises(self):
        """admin_share_pct not summing to 100 raises ValueError."""
        with pytest.raises(Exception) as exc:
            LevyScenarioCreate(
                scenario_name="Bad",
                financial_year="2026-2027",
                total_admin_budget=10000.0,
                total_sinking_budget=5000.0,
                groups=[
                    ScenarioGroupCreate(**self._base_group(60.0, 50.0)),
                    ScenarioGroupCreate(
                        **{**self._base_group(50.0, 50.0), "group_name": "B", "unit_type_prefixes": ["TH"]}),
                ],
            )
        assert "100" in str(exc.value)

    def test_sinking_shares_not_100_raises(self):
        """sinking_share_pct not summing to 100 raises ValueError."""
        with pytest.raises(Exception) as exc:
            LevyScenarioCreate(
                scenario_name="Bad",
                financial_year="2026-2027",
                total_admin_budget=10000.0,
                total_sinking_budget=5000.0,
                groups=[
                    ScenarioGroupCreate(**self._base_group(50.0, 30.0)),
                    ScenarioGroupCreate(
                        **{**self._base_group(50.0, 30.0), "group_name": "B", "unit_type_prefixes": ["TH"]}),
                ],
            )
        assert "100" in str(exc.value)

    def test_invalid_financial_year_raises(self):
        """Invalid financial_year format raises ValueError."""
        with pytest.raises(Exception):
            LevyScenarioCreate(
                scenario_name="Test",
                financial_year="2026",  # wrong format, should be "2026-2027"
                total_admin_budget=1000.0,
                total_sinking_budget=500.0,
                groups=[ScenarioGroupCreate(**self._base_group(100.0, 100.0))],
            )

    def test_three_equal_groups_100_each(self):
        """Three groups each with 33.33 + 33.33 + 33.34 = 100 passes."""
        scenario = LevyScenarioCreate(
            scenario_name="Three Equal",
            financial_year="2026-2027",
            total_admin_budget=3000.0,
            total_sinking_budget=900.0,
            groups=[
                ScenarioGroupCreate(
                    **{**self._base_group(33.33, 33.33), "group_name": "G1", "unit_type_prefixes": ["UA"]}),
                ScenarioGroupCreate(
                    **{**self._base_group(33.33, 33.33), "group_name": "G2", "unit_numbers": ["TH001"]}),
                ScenarioGroupCreate(
                    **{**self._base_group(33.34, 33.34), "group_name": "G3", "unit_numbers": ["TH002"]}),
            ],
        )
        assert len(scenario.groups) == 3
