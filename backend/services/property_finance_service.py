"""
Property Finance Service — calculations for investment tracking.
Provides data for the owner property-finance dashboard.
"""
from typing import Dict, Any

from database import db


async def get_property_finance_metrics(unit_number: str, building_id: str) -> Dict[str, Any]:
    """
    Computes key investment metrics for a unit:
    - Total Cost of Ownership (TCO)
    - Yield estimate
    - Capital growth (mocked)
    """
    # 1. Fetch current levy total
    # Use latest ledger
    ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        sort=[("year", -1)]
    )

    annual_levy = ledger.get("total_levied", 0) if ledger else 0

    # 2. Mock some market data (would normally come from market_snapshots)
    market_value = 850000  # Mocked for TH017
    estimated_rent = 850  # weekly

    # 3. Compute Yields
    gross_yield = (estimated_rent * 52) / market_value * 100
    net_yield = (estimated_rent * 52 - annual_levy) / market_value * 100

    return {
        "unit_number": unit_number,
        "market_value_estimate": market_value,
        "annual_levy": annual_levy,
        "estimated_weekly_rent": estimated_rent,
        "gross_yield_pct": round(gross_yield, 2),
        "net_yield_pct": round(net_yield, 2),
        "total_cost_of_ownership_annual": annual_levy + 2500  # Adding mock rates/water
    }
