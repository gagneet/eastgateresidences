"""
Unit Tests for Levy Allocation Engine
"""
import pytest

from services.facility_allocation_engine import calculate_facility_allocation


@pytest.mark.asyncio
async def test_equal_split_allocation():
    facility = {"annual_cost": 1000, "benefit_group_id": "bg1"}
    units = [{"unit_number": "U1"}, {"unit_number": "U2"}]
    benefit_groups = {"bg1": {"name": "G1", "allocation_driver": "equal_split"}}
    unit_attributes = {}

    alloc = await calculate_facility_allocation(facility, units, benefit_groups, unit_attributes)
    assert alloc["U1"] == 500
    assert alloc["U2"] == 500


@pytest.mark.asyncio
async def test_ue_allocation():
    facility = {"annual_cost": 1000, "benefit_group_id": "bg1"}
    units = [
        {"unit_number": "U1", "entitlement": 100},
        {"unit_number": "U2", "entitlement": 300}
    ]
    benefit_groups = {"bg1": {"name": "G1", "allocation_driver": "unit_entitlement"}}
    unit_attributes = {}

    alloc = await calculate_facility_allocation(facility, units, benefit_groups, unit_attributes)
    assert alloc["U1"] == 250
    assert alloc["U2"] == 750


@pytest.mark.asyncio
async def test_car_space_allocation():
    facility = {"annual_cost": 1000, "benefit_group_id": "bg1"}
    units = [{"unit_number": "U1"}, {"unit_number": "U2"}]
    benefit_groups = {"bg1": {"name": "G1", "allocation_driver": "car_space_weighted"}}
    unit_attributes = {
        "U1": {"car_spaces": 1},
        "U2": {"car_spaces": 4}
    }

    alloc = await calculate_facility_allocation(facility, units, benefit_groups, unit_attributes)
    assert alloc["U1"] == 200
    assert alloc["U2"] == 800
