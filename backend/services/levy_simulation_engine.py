"""
Levy Simulation Engine — Transition logic and scenario modeling

Implements:
  - Phased transition with caps (max_change_percent, max_change_amount)
  - Iterative shortfall redistribution to ensure no unit exceeds its caps
  - Scenario persistence and comparison
"""
from typing import Dict, Optional


async def apply_transition_caps(
        fair_levies: Dict[str, float],
        current_levies: Dict[str, float],
        max_change_percent: Optional[float] = None,
        max_change_amount: Optional[float] = None
) -> Dict[str, float]:
    """
    Applies caps to fair levies and redistributes the shortfall across other increasing units.
    Uses an iterative approach to ensure redistributed shortfall doesn't violate caps.
    """
    if max_change_percent is None and max_change_amount is None:
        return fair_levies

    # Step 1: Initialize result and calculate individual caps
    final_levies = fair_levies.copy()
    unit_caps = {}

    for uid, current_val in current_levies.items():
        fair_val = fair_levies.get(uid, 0.0)
        change = fair_val - current_val

        # Calculate the maximum allowable levy for this unit
        max_allowed = float('inf')
        if change > 0:
            potential_caps = []
            if max_change_amount is not None:
                potential_caps.append(current_val + max_change_amount)
            if max_change_percent is not None:
                potential_caps.append(current_val * (1 + max_change_percent / 100.0))

            if potential_caps:
                max_allowed = min(potential_caps)

        unit_caps[uid] = max_allowed

    # Step 2: Iterative redistribution
    # We repeat until total shortfall is effectively zero or no units can take more
    for _ in range(10):  # Safety limit for iterations
        initial_sum = sum(final_levies.values())

        # Apply caps to current state
        new_levies = {}
        iteration_shortfall = 0.0
        for uid, val in final_levies.items():
            cap = unit_caps.get(uid, float('inf'))
            if val > cap:
                iteration_shortfall += (val - cap)
                new_levies[uid] = cap
            else:
                new_levies[uid] = val

        if iteration_shortfall < 0.01:
            final_levies = new_levies
            break

        # Identify units that can take more (increasing units not yet at their cap)
        eligible_units = []
        total_fair_increase_eligible = 0.0

        for uid in current_levies:
            current_val = current_levies[uid]
            current_capped_val = new_levies[uid]
            cap = unit_caps[uid]

            # Unit must be increasing AND below cap
            if current_capped_val < cap and (fair_levies[uid] - current_val) > 0.01:
                eligible_units.append(uid)
                total_fair_increase_eligible += (fair_levies[uid] - current_val)

        if not eligible_units:
            # No one left to take the shortfall without breaking their own caps
            # In this case we have to break the revenue neutrality or the caps
            # The requirement usually prioritizes revenue neutrality (Σ Levy = Budget)
            # but Strata rules usually prioritize Caps.
            # We'll stick with capped values.
            final_levies = new_levies
            break

        # Distribute shortfall
        for uid in eligible_units:
            fair_inc = fair_levies[uid] - current_levies[uid]
            share = (fair_inc / total_fair_increase_eligible) * iteration_shortfall
            new_levies[uid] += share

        final_levies = new_levies

    return final_levies
