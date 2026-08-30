"""
Unit Tests for Levy Simulation Engine
"""
import pytest

from services.levy_simulation_engine import apply_transition_caps


@pytest.mark.asyncio
async def test_levy_capping_and_redistribution():
    # U1 increases from 500 to 1000 (+100%)
    # U2 decreases from 1500 to 1000 (-33%)
    current_levies = {"U1": 500, "U2": 1500}
    fair_levies = {"U1": 1000, "U2": 1000}

    # Apply 10% cap to U1 increase -> Max increase is 50 -> New levy 550
    # Shortfall is 1000 - 550 = 450
    # But wait, U2 is decreasing, so it can't take the shortfall

    capped = await apply_transition_caps(fair_levies, current_levies, max_change_percent=10)
    assert capped["U1"] == 550
    assert capped["U2"] == 1000  # Decreasing unit remains at its fair value


@pytest.mark.asyncio
async def test_shortfall_redistribution():
    # Sum current = 500 + 1000 + 1950 = 3450
    current = {"U1": 500, "U2": 1000, "U3": 1950}
    # Sum fair = 1000 + 1050 + 1400 = 3450
    fair = {"U1": 1000, "U2": 1050, "U3": 1400}

    # Cap increase at $100
    # U1 fair increase = 500. Capped at 100 -> new U1 = 600. Shortfall = 400.
    # U2 fair increase = 50. Not capped.
    # U3 fair increase = -550. Not capped.
    # Uncapped increasing units: [U2], but U2 is capped at +$100, so shortfall
    # cannot be fully redistributed without breaking caps.

    capped = await apply_transition_caps(fair, current, max_change_amount=100)
    assert capped["U1"] == 600
    assert capped["U2"] == 1100
    assert capped["U3"] == 1400
    # Caps take precedence over revenue neutrality in this scenario
    assert abs(sum(capped.values()) - 3100) < 0.01
