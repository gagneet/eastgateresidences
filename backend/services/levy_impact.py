"""
Levy impact calculator — computes per-lot levy impact using UOE-weighted allocation.
"""
import logging

logger = logging.getLogger(__name__)


async def compute_levy_impact_per_lot(
        building_id: str,
        cost_cents: int,
        db,
) -> list[dict]:
    """
    Compute levy impact per lot for a given total cost.

    Uses UOE (Unit of Entitlement) weighting from the units collection.
    Guarantees: sum of all impact_cents == cost_cents (rounding correction on largest lot).
    """
    from request_context import set_ctx_building_id
    set_ctx_building_id(building_id)

    units = await db.units.find(
        {"building_id": building_id},
        {"_id": 0, "id": 1, "unit_number": 1, "uoe": 1, "lot_number": 1},
    ).to_list(500)

    if not units:
        logger.warning("No units found for building %s", building_id)
        return []

    total_uoe = sum(u.get("uoe", 0) for u in units)
    if total_uoe == 0:
        logger.warning("Total UOE is 0 for building %s — cannot compute levy impact", building_id)
        return []

    allocations = []
    total_allocated = 0

    for unit in sorted(units, key=lambda u: u.get("uoe", 0)):
        uoe = unit.get("uoe", 0)
        if uoe == 0:
            continue
        impact_cents = round(cost_cents * uoe / total_uoe)
        total_allocated += impact_cents
        allocations.append({
            "lot_id": unit.get("id") or unit.get("unit_number"),
            "unit_number": unit.get("unit_number", ""),
            "lot_number": unit.get("lot_number", ""),
            "uoe": uoe,
            "impact_cents": impact_cents,
            "impact_dollars": round(impact_cents / 100, 2),
        })

    # Rounding correction: adjust the largest lot so total == cost_cents exactly
    if allocations and total_allocated != cost_cents:
        diff = cost_cents - total_allocated
        allocations[-1]["impact_cents"] += diff
        allocations[-1]["impact_dollars"] = round(allocations[-1]["impact_cents"] / 100, 2)

    return allocations
