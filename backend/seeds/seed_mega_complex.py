"""
Mega-Complex Seed Data for Levy Fairness Engine

Models a mixed-use strata scheme:
- 120 Apartments
- 30 Townhouses
- 10 Retail
- 5 Commercial
- Common facilities (Lifts, Pool, HVAC, Loading Dock)
"""
import uuid
from datetime import datetime, timezone

import asyncio

from database import db

MEGA_PLAN_ID = "13195"


def _now():
    """Generated function header.

    Function: _now
    Path: backend/seeds/seed_mega_complex.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


BENEFIT_GROUPS = [
    {"id": "bg-all", "name": "ALL_LOTS", "group_type": "global", "allocation_driver": "unit_entitlement",
     "description": "Shared by all owners"},
    {"id": "bg-apartments", "name": "APARTMENTS_ONLY", "group_type": "type", "allocation_driver": "unit_entitlement",
     "description": "Apartment specific costs"},
    {"id": "bg-townhouses", "name": "TOWNHOUSES_ONLY", "group_type": "type", "allocation_driver": "equal_split",
     "description": "Townhouse specific costs"},
    {"id": "bg-retail", "name": "RETAIL_ONLY", "group_type": "type", "allocation_driver": "area_weighted",
     "description": "Retail specific costs"},
    {"id": "bg-commercial", "name": "COMMERCIAL_ONLY", "group_type": "type", "allocation_driver": "unit_entitlement",
     "description": "Commercial specific costs"},
    {"id": "bg-garage", "name": "GARAGE_USERS", "group_type": "usage", "allocation_driver": "car_space_weighted",
     "description": "Parking related costs"},
    {"id": "bg-pool", "name": "POOL_USERS", "group_type": "usage", "allocation_driver": "custom_weights",
     "description": "Pool club members"},
]

FACILITIES = [
    {"facility_name": "Lifts (Tower A)", "annual_cost": 45000, "benefit_group_id": "bg-apartments",
     "allocation_driver": "unit_entitlement"},
    {"facility_name": "Building Insurance", "annual_cost": 85000, "benefit_group_id": "bg-all",
     "allocation_driver": "unit_entitlement"},
    {"facility_name": "Management Fees", "annual_cost": 40000, "benefit_group_id": "bg-all",
     "allocation_driver": "unit_entitlement"},
    {"facility_name": "Swimming Pool", "annual_cost": 15000, "benefit_group_id": "bg-pool",
     "allocation_driver": "custom_weights"},
    {"facility_name": "Garage Ventilation", "annual_cost": 12000, "benefit_group_id": "bg-garage",
     "allocation_driver": "car_space_weighted"},
    {"facility_name": "Retail Loading Dock", "annual_cost": 8000, "benefit_group_id": "bg-retail",
     "allocation_driver": "area_weighted"},
    {"facility_name": "Commercial HVAC", "annual_cost": 25000, "benefit_group_id": "bg-commercial",
     "allocation_driver": "unit_entitlement"},
    {"facility_name": "Landscaping (Townhouses)", "annual_cost": 10000, "benefit_group_id": "bg-townhouses",
     "allocation_driver": "equal_split"},
    {"facility_name": "Cleaning (Common)", "annual_cost": 30000, "benefit_group_id": "bg-all",
     "allocation_driver": "unit_entitlement"},
]


async def seed_mega_complex():
    """Generated function header.

    Function: seed_mega_complex
    Path: backend/seeds/seed_mega_complex.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print("Seeding Mega-Complex data...")

    # 1. Clear existing data for this plan
    await db.units.delete_many({"building_id": MEGA_PLAN_ID})
    await db.unit_attributes.delete_many({"building_id": MEGA_PLAN_ID})
    await db.facility_cost_centres.delete_many({"building_id": MEGA_PLAN_ID})
    await db.benefit_groups.delete_many({"building_id": MEGA_PLAN_ID})
    await db.unit_levy_ledger.delete_many({"building_id": MEGA_PLAN_ID})

    # 2. Seed Benefit Groups (Batch)
    bg_docs = []
    for bg in BENEFIT_GROUPS:
        bg_docs.append({**bg, "building_id": MEGA_PLAN_ID, "created_at": _now()})
    if bg_docs:
        await db.benefit_groups.insert_many(bg_docs)

    # 3. Seed Units and Attributes (Batch)
    units = []
    attributes = []
    ledger = []

    # Apartments (1-120)
    for i in range(1, 121):
        un = f"A{i:03}"
        units.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "unit_type": "Apartment", "entitlement": 100,
                      "owner_name": f"Owner {un}"})
        attributes.append(
            {"unit_id": un, "building_id": MEGA_PLAN_ID, "car_spaces": 1 if i < 100 else 2, "internal_area": 85,
             "custom_weight": 1.0})
        ledger.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "year": 2025, "total_levied": 5000})

    # Townhouses (T1-T30)
    for i in range(1, 31):
        un = f"T{i:02}"
        units.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "unit_type": "Townhouse", "entitlement": 150,
                      "owner_name": f"Owner {un}"})
        attributes.append(
            {"unit_id": un, "building_id": MEGA_PLAN_ID, "car_spaces": 2, "internal_area": 140, "custom_weight": 0.5})
        ledger.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "year": 2025, "total_levied": 7500})

    # Retail (R1-R10)
    for i in range(1, 11):
        un = f"R{i:02}"
        units.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "unit_type": "Retail", "entitlement": 200,
                      "owner_name": f"Retail Owner {un}"})
        attributes.append({"unit_id": un, "building_id": MEGA_PLAN_ID, "car_spaces": 0, "internal_area": 120 + (i * 10),
                           "custom_weight": 0.0})
        ledger.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "year": 2025, "total_levied": 10000})

    # Commercial (C1-C5)
    for i in range(1, 6):
        un = f"C{i:02}"
        units.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "unit_type": "Commercial", "entitlement": 300,
                      "owner_name": f"Corp Owner {un}"})
        attributes.append(
            {"unit_id": un, "building_id": MEGA_PLAN_ID, "car_spaces": 5, "internal_area": 250, "custom_weight": 0.2})
        ledger.append({"unit_number": un, "building_id": MEGA_PLAN_ID, "year": 2025, "total_levied": 15000})

    if units: await db.units.insert_many(units)
    if attributes: await db.unit_attributes.insert_many(attributes)
    if ledger: await db.unit_levy_ledger.insert_many(ledger)

    # 4. Seed Facility Cost Centres (Batch)
    fac_docs = []
    for fac in FACILITIES:
        fac_docs.append({
            **fac,
            "facility_id": f"fac-{uuid.uuid4().hex[:8]}",
            "building_id": MEGA_PLAN_ID,
            "enabled": True,
            "created_at": _now()
        })
    if fac_docs:
        await db.facility_cost_centres.insert_many(fac_docs)

    # 5. Financial Summary for Monte Carlo
    await db.financial_summary.update_one(
        {"building_id": MEGA_PLAN_ID},
        {"$set": {"reserve_balance": 250000, "last_updated": _now()}},
        upsert=True
    )

    print(f"Successfully seeded Mega-Complex with {len(units)} units and {len(fac_docs)} facilities.")


if __name__ == "__main__":
    asyncio.run(seed_mega_complex())
