"""
Levy Scenario Modeller — calculation service.

Core computation:
  For each group G with admin_share_pct and sinking_share_pct:
    group_admin_budget  = total_admin_budget  × G.admin_share_pct  / 100
    group_sinking_budget = total_sinking_budget × G.sinking_share_pct / 100
    group_total_adjusted_uoe = Σ(scenario_uoe for unit in G)

  For each unit U in group G:
    scenario_admin_levy   = group_admin_budget   × U.scenario_uoe / group_total_adjusted_uoe
    scenario_sinking_levy = group_sinking_budget × U.scenario_uoe / group_total_adjusted_uoe
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class UnitInput:
    unit_number: str
    original_uoe: float
    scenario_uoe: float  # may differ if a UOE override was provided
    current_admin_levy: float
    current_sinking_levy: float


@dataclass
class GroupInput:
    group_name: str
    group_color: str
    admin_share_pct: float  # 0–100
    sinking_share_pct: float  # 0–100
    units: List[UnitInput] = field(default_factory=list)


@dataclass
class ScenarioInputs:
    total_admin_budget: float
    total_sinking_budget: float
    groups: List[GroupInput]
    current_admin_rate_per_uoe: float  # for base-case reference
    current_sinking_rate_per_uoe: float


# ─────────────────────────────────────────────────────────────────────────────

def compute_scenario(inputs: ScenarioInputs) -> dict:
    """
    Compute per-unit levies for a scenario.
    Returns a dict matching ScenarioComputeResult schema.
    """
    from datetime import timezone, datetime

    unit_results = []
    group_summaries = []

    total_original_uoe = 0.0
    total_scenario_uoe = 0.0

    for grp in inputs.groups:
        group_admin_budget = inputs.total_admin_budget * grp.admin_share_pct / 100.0
        group_sinking_budget = inputs.total_sinking_budget * grp.sinking_share_pct / 100.0

        group_total_adjusted_uoe = sum(u.scenario_uoe for u in grp.units) or 1.0
        group_total_original_uoe = sum(u.original_uoe for u in grp.units)

        total_original_uoe += group_total_original_uoe
        total_scenario_uoe += group_total_adjusted_uoe

        group_admin_levy_sum = 0.0
        group_sinking_levy_sum = 0.0

        for unit in grp.units:
            s_admin = group_admin_budget * unit.scenario_uoe / group_total_adjusted_uoe
            s_sinking = group_sinking_budget * unit.scenario_uoe / group_total_adjusted_uoe

            admin_delta = s_admin - unit.current_admin_levy
            sinking_delta = s_sinking - unit.current_sinking_levy
            total_delta = admin_delta + sinking_delta
            current_total = unit.current_admin_levy + unit.current_sinking_levy
            total_delta_pct = (
                total_delta / current_total * 100.0 if current_total else 0.0
            )

            group_admin_levy_sum += s_admin
            group_sinking_levy_sum += s_sinking

            unit_results.append({
                "unit_number": unit.unit_number,
                "group_name": grp.group_name,
                "group_color": grp.group_color,
                "original_uoe": round(unit.original_uoe, 4),
                "scenario_uoe": round(unit.scenario_uoe, 4),
                "uoe_changed": abs(unit.original_uoe - unit.scenario_uoe) > 0.0001,
                "current_admin_levy": round(unit.current_admin_levy, 2),
                "current_sinking_levy": round(unit.current_sinking_levy, 2),
                "current_total_levy": round(unit.current_admin_levy + unit.current_sinking_levy, 2),
                "scenario_admin_levy": round(s_admin, 2),
                "scenario_sinking_levy": round(s_sinking, 2),
                "scenario_total_levy": round(s_admin + s_sinking, 2),
                "admin_delta": round(admin_delta, 2),
                "sinking_delta": round(sinking_delta, 2),
                "total_delta": round(total_delta, 2),
                "total_delta_pct": round(total_delta_pct, 2),
            })

        group_total = group_admin_levy_sum + group_sinking_levy_sum
        group_summaries.append({
            "group_name": grp.group_name,
            "group_color": grp.group_color,
            "unit_count": len(grp.units),
            "total_original_uoe": round(group_total_original_uoe, 4),
            "total_scenario_uoe": round(group_total_adjusted_uoe, 4),
            "admin_share_pct": grp.admin_share_pct,
            "sinking_share_pct": grp.sinking_share_pct,
            "group_admin_levy": round(group_admin_levy_sum, 2),
            "group_sinking_levy": round(group_sinking_levy_sum, 2),
            "group_total_levy": round(group_total, 2),
            "avg_levy_per_unit": round(group_total / len(grp.units), 2) if grp.units else 0.0,
        })

    uoe_diff = abs(total_scenario_uoe - total_original_uoe)

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "grand_total_admin": round(inputs.total_admin_budget, 2),
        "grand_total_sinking": round(inputs.total_sinking_budget, 2),
        "grand_total_levy": round(inputs.total_admin_budget + inputs.total_sinking_budget, 2),
        "total_original_uoe": round(total_original_uoe, 4),
        "total_scenario_uoe": round(total_scenario_uoe, 4),
        "uoe_total_preserved": uoe_diff < 0.01,
        "uoe_diff": round(uoe_diff, 4),
        "current_admin_rate_per_uoe": round(inputs.current_admin_rate_per_uoe, 4),
        "current_sinking_rate_per_uoe": round(inputs.current_sinking_rate_per_uoe, 4),
        "group_summaries": group_summaries,
        "unit_results": unit_results,
    }


def resolve_units_to_groups(
        groups_config: list,
        all_units: list,  # [{"unit_number": str, "entitlement": float, ...}]
        uoe_overrides_by_group: Dict[str, Dict[str, float]],
        current_levy_by_unit: Dict[str, dict],
) -> List[GroupInput]:
    """
    Resolve group configurations into GroupInput objects.

    groups_config: raw group dicts from the scenario document
    all_units: full unit list from DB
    uoe_overrides_by_group: {group_index_str: {unit_number: adjusted_uoe}}
    current_levy_by_unit: {unit_number: {current_admin_levy, current_sinking_levy}}
    """
    # Index units
    unit_map = {u["unit_number"]: u for u in all_units}
    assigned = set()

    result: List[GroupInput] = []

    for i, grp in enumerate(groups_config):
        group_name = grp.get("group_name", f"Group {i + 1}")
        color = grp.get("color", "#6366f1")
        admin_pct = float(grp.get("admin_share_pct", 0.0))
        sinking_pct = float(grp.get("sinking_share_pct", 0.0))

        # Resolve which units belong to this group
        explicit = set(grp.get("unit_numbers") or [])
        prefixes = grp.get("unit_type_prefixes") or []

        matched_unit_numbers = set(explicit)
        if prefixes:
            for unum in unit_map:
                if any(unum.startswith(pfx) for pfx in prefixes):
                    matched_unit_numbers.add(unum)

        # Remove already-assigned units (first-group wins)
        matched_unit_numbers -= assigned
        assigned |= matched_unit_numbers

        # Build per-unit overrides index for this group
        overrides = uoe_overrides_by_group.get(str(i), {})
        for item in grp.get("uoe_overrides") or []:
            overrides[item["unit_number"]] = float(item["adjusted_uoe"])

        units_in_group: List[UnitInput] = []
        for unum in sorted(matched_unit_numbers):
            unit_doc = unit_map.get(unum, {})
            original_uoe = float(unit_doc.get("entitlement") or 0.0)
            scenario_uoe = overrides.get(unum, original_uoe)
            levies = current_levy_by_unit.get(unum, {})
            units_in_group.append(UnitInput(
                unit_number=unum,
                original_uoe=original_uoe,
                scenario_uoe=scenario_uoe,
                current_admin_levy=float(levies.get("current_admin_levy", 0.0)),
                current_sinking_levy=float(levies.get("current_sinking_levy", 0.0)),
            ))

        result.append(GroupInput(
            group_name=group_name,
            group_color=color,
            admin_share_pct=admin_pct,
            sinking_share_pct=sinking_pct,
            units=units_in_group,
        ))

    return result
