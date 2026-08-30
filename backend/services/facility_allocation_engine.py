"""
Facility Allocation Engine — Absolute Dollar Value Cost Allocation

Computes fair levy allocation based on:
  - Facility cost centres (absolute dollars)
  - Benefit groups (membership)
  - Allocation drivers (unit_entitlement, equal_split, car_space_weighted, area_weighted, metered_usage, custom_weights)
"""
from typing import Dict, Any, List

from database import db


async def get_unit_attributes(building_id: str) -> Dict[str, Dict[str, Any]]:
    """Fetch unit attributes like car_spaces, internal_area, etc."""
    cursor = db.unit_attributes.find({"building_id": building_id}, {"_id": 0})
    attributes = await cursor.to_list(1000)
    return {a["unit_id"]: a for a in attributes}


def _safe_float(value: Any, default: float) -> float:
    """Generated function header.

    Function: _safe_float
    Path: backend/services/facility_allocation_engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def calculate_facility_allocation(
        facility: Dict[str, Any],
        units: List[Dict[str, Any]],
        benefit_groups: Dict[str, Dict[str, Any]],
        unit_attributes: Dict[str, Dict[str, Any]]
) -> Dict[str, float]:
    """
    Allocates a facility's annual cost across eligible units.
    Returns: Dict[unit_id, allocated_dollars]
    """
    cost = float(facility.get("annual_cost", 0))
    bg_id = facility.get("benefit_group_id")
    bg = benefit_groups.get(bg_id, {"name": "ALL_LOTS", "allocation_driver": "unit_entitlement"})

    driver = facility.get("allocation_driver") or bg.get("allocation_driver", "unit_entitlement")
    lot_numbers = bg.get("lot_numbers", [])

    # Filter eligible units using a 4-level priority chain:
    # 1. Explicit lot_numbers list (highest priority)
    # 2. unit_prefixes + optional unit_number_range (flexible per-building matching)
    # 3. Legacy name-based fallback (APARTMENTS_ONLY, TOWNHOUSES_ONLY, BASEMENT_USERS, GARAGE_USERS)
    # 4. ALL_LOTS (default)

    unit_prefixes = [p.upper() for p in bg.get("unit_prefixes") or []]
    unit_number_range = bg.get("unit_number_range")  # {"min": "UA001", "max": "UA070"}
    bg_name = (bg.get("name") or "").upper()

    if lot_numbers:
        # Priority 1: explicit membership list
        eligible_units = [u for u in units if u.get("unit_number") in lot_numbers or u.get("lot_number") in lot_numbers]

    elif unit_prefixes:
        # Priority 2: flexible prefix + optional range matching
        def _matches_flexible(unit_number: str) -> bool:
            """Generated function header.

            Function: _matches_flexible
            Path: backend/services/facility_allocation_engine.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            un = unit_number.upper()
            for pfx in unit_prefixes:
                if un.startswith(pfx):
                    if unit_number_range:
                        mn = (unit_number_range.get("min") or "").upper()
                        mx = (unit_number_range.get("max") or "").upper()
                        return (not mn or un >= mn) and (not mx or un <= mx)
                    return True
            return False

        eligible_units = [u for u in units if _matches_flexible(u.get("unit_number", ""))]

    elif "APARTMENT" in bg_name:
        # Priority 3a: legacy name → apartments
        eligible_units = [u for u in units if
                          "apartment" in (u.get("unit_type") or u.get("property_type") or "").lower()
                          or u.get("unit_number", "").upper().startswith("UA")]

    elif "TOWNHOUSE" in bg_name:
        # Priority 3b: legacy name → townhouses
        eligible_units = [u for u in units if
                          "townhouse" in (u.get("unit_type") or u.get("property_type") or "").lower()
                          or u.get("unit_number", "").upper().startswith("TH")]

    elif "BASEMENT" in bg_name or "GARAGE" in bg_name:
        # Priority 3c: legacy name → units with car spaces (apartments in this building)
        eligible_units = [u for u in units if
                          (unit_attributes.get(u.get("unit_number"), {}).get("car_spaces") or 0) > 0
                          or u.get("unit_number", "").upper().startswith("UA")]

    else:
        # Priority 4: ALL_LOTS
        eligible_units = units

    if not eligible_units or cost <= 0:
        return {}

    allocation = {}

    if driver == "equal_split":
        per_unit = cost / len(eligible_units)
        for u in eligible_units:
            allocation[u["unit_number"]] = per_unit

    elif driver == "unit_entitlement":
        total_ue = sum(float(u.get("entitlement", 0) or 0) for u in eligible_units)
        if total_ue > 0:
            for u in eligible_units:
                share = float(u.get("entitlement", 0) or 0) / total_ue
                allocation[u["unit_number"]] = cost * share

    elif driver == "car_space_weighted":
        total_spaces = sum(unit_attributes.get(u["unit_number"], {}).get("car_spaces", 0) for u in eligible_units)
        if total_spaces > 0:
            for u in eligible_units:
                spaces = unit_attributes.get(u["unit_number"], {}).get("car_spaces", 0)
                allocation[u["unit_number"]] = cost * (spaces / total_spaces)

    elif driver == "area_weighted":
        total_area = sum(unit_attributes.get(u["unit_number"], {}).get("internal_area", 0) for u in eligible_units)
        if total_area > 0:
            for u in eligible_units:
                area = unit_attributes.get(u["unit_number"], {}).get("internal_area", 0)
                allocation[u["unit_number"]] = cost * (area / total_area)

    elif driver == "metered_usage" or driver == "custom_weights":
        if driver == "metered_usage":
            attr_key = "metered_usage"
            default_weight = 0.0
        else:
            attr_key = "custom_weight"
            default_weight = 1.0

        total_weight = sum(
            _safe_float(
                unit_attributes.get(u["unit_number"], {}).get(attr_key, default_weight),
                default_weight
            ) for u in eligible_units
        )
        if total_weight > 0:
            for u in eligible_units:
                weight = _safe_float(
                    unit_attributes.get(u["unit_number"], {}).get(attr_key, default_weight),
                    default_weight
                )
                allocation[u["unit_number"]] = cost * (weight / total_weight)
    else:
        # Fallback to unit_entitlement
        total_ue = sum(float(u.get("entitlement", 0) or 0) for u in eligible_units)
        if total_ue > 0:
            for u in eligible_units:
                share = float(u.get("entitlement", 0) or 0) / total_ue
                allocation[u["unit_number"]] = cost * share

    return allocation
