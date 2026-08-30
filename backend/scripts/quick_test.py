import os

import asyncio

os.environ["MONGO_URL"] = "mongodb://localhost:27017/test"
os.environ["DB_NAME"] = "test"

from services.facility_allocation_engine import calculate_facility_allocation
from services.levy_simulation_engine import apply_transition_caps


async def test_equal_split_allocation():
    """Generated function header.

    Function: test_equal_split_allocation
    Path: backend/scripts/quick_test.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    facility = {"annual_cost": 1000, "benefit_group_id": "bg1"}
    units = [{"unit_number": "U1"}, {"unit_number": "U2"}]
    benefit_groups = {"bg1": {"name": "G1", "allocation_driver": "equal_split"}}
    unit_attributes = {}

    alloc = await calculate_facility_allocation(facility, units, benefit_groups, unit_attributes)
    print(f"Equal split: {alloc}")
    assert alloc["U1"] == 500
    assert alloc["U2"] == 500


async def test_shortfall_redistribution():
    # Sum current = 500 + 1000 + 1950 = 3450
    """Generated function header.

    Function: test_shortfall_redistribution
    Path: backend/scripts/quick_test.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    current = {"U1": 500, "U2": 1000, "U3": 1950}
    # Sum fair = 1000 + 1050 + 1400 = 3450
    fair = {"U1": 1000, "U2": 1050, "U3": 1400}

    # Cap increase at $100
    # U1 fair increase = 500. Capped at 100 -> new U1 = 600. Shortfall = 400.
    # U2 fair increase = 50. Not capped.
    # U3 fair increase = -550. Not capped.
    # Uncapped increasing units: [U2]
    # U2 new = 1050 + 400 = 1450.

    capped = await apply_transition_caps(fair, current, max_change_amount=100)
    print(f"Capped: {capped}")
    assert capped["U1"] == 600
    assert capped["U2"] == 1450
    assert capped["U3"] == 1400
    assert sum(capped.values()) == sum(fair.values())


async def main():
    """Generated function header.

    Function: main
    Path: backend/scripts/quick_test.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await test_equal_split_allocation()
    await test_shortfall_redistribution()
    print("All quick tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
