import os
import uuid
from datetime import datetime, timezone, timedelta

import asyncio
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

# This seed targets East Gate Residences historical data exclusively.
_EAST_GATE_BUILDING_ID = "13195"


async def seed():
    # Load env from backend/.env or root .env
    """Generated function header.

    Function: seed
    Path: backend/seeds/enhanced_dashboard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    load_dotenv('backend/.env')
    load_dotenv()

    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27018')
    db_name = os.environ.get('DB_NAME', 'strata_production')
    client = AsyncMongoClient(mongo_url)
    db = client[db_name]

    print(f"Seeding enhanced dashboard data to {db_name}...")

    # 1. Seed Historical Levy Data (2021 - 2026)
    historical_years = [
        {
            "year": "2021",
            "admin_income": 280000,
            "admin_expenses": 220000,
            "sinking_income": 80000,
            "sinking_expenses": 30000,
            "admin_per_uoe": 28.00,
            "sinking_per_uoe": 8.00
        },
        {
            "year": "2022",
            "admin_income": 300000,
            "admin_expenses": 235000,
            "sinking_income": 90000,
            "sinking_expenses": 35000,
            "admin_per_uoe": 30.00,
            "sinking_per_uoe": 9.00
        },
        {
            "year": "2023",
            "admin_income": 320000,
            "admin_expenses": 250000,
            "sinking_income": 100000,
            "sinking_expenses": 40000,
            "admin_per_uoe": 32.00,
            "sinking_per_uoe": 10.00
        },
        {
            "year": "2024",
            "admin_income": 335000,
            "admin_expenses": 265000,
            "sinking_income": 110000,
            "sinking_expenses": 45000,
            "admin_per_uoe": 33.50,
            "sinking_per_uoe": 11.00
        },
        {
            "year": "2025",
            "admin_income": 350000,
            "admin_expenses": 280000,
            "sinking_income": 120000,
            "sinking_expenses": 50000,
            "admin_per_uoe": 35.00,
            "sinking_per_uoe": 12.00
        },
        {
            "year": "2026",
            "admin_income": 365000,
            "admin_expenses": 295000,
            "sinking_income": 130000,
            "sinking_expenses": 55000,
            "admin_per_uoe": 36.50,
            "sinking_per_uoe": 13.00
        }
    ]

    for data in historical_years:
        year = data["year"]
        existing = await db.annual_levies.find_one({"year": year, "plan_id": _EAST_GATE_BUILDING_ID})
        if existing:
            print(f"Year {year} already exists in annual_levies, skipping...")
        else:
            now = datetime.now(timezone.utc).isoformat()
            annual_levy = {
                "id": str(uuid.uuid4()),
                "year": year,
                "status": "actual",
                "plan_id": _EAST_GATE_BUILDING_ID,
                "building_id": _EAST_GATE_BUILDING_ID,
                "total_uoe": 10000,
                "admin_fund": {
                    "levy_income": data["admin_income"],
                    "other_income": 500,
                    "total_income": data["admin_income"] + 500,
                    "total_expenses": data["admin_expenses"],
                    "opening_balance": 150000,
                    "closing_balance": 150000 + data["admin_income"] + 500 - data["admin_expenses"],
                    "surplus_deficit": data["admin_income"] + 500 - data["admin_expenses"]
                },
                "sinking_fund": {
                    "levy_income": data["sinking_income"],
                    "other_income": 2000,
                    "total_income": data["sinking_income"] + 2000,
                    "total_expenses": data["sinking_expenses"],
                    "opening_balance": 120000,
                    "closing_balance": 120000 + data["sinking_income"] + 2000 - data["sinking_expenses"],
                    "surplus_deficit": data["sinking_income"] + 2000 - data["sinking_expenses"]
                },
                # East Gate 13195 levy schedule: Q1=Mar 31, Q2=Jun 1, Q3=Sep 1, Q4=Dec 1
                # These dates match db.settings levy_due_custom_dates for building 13195.
                # This is seed/demo data only (is_seed_data=True); live levy events are
                # generated dynamically from settings via POST /events/generate-levy-dates.
                "payment_schedule": [
                    {"quarter": "Q1", "due_date": f"{year}-03-31"},
                    {"quarter": "Q2", "due_date": f"{year}-06-01"},
                    {"quarter": "Q3", "due_date": f"{year}-09-01"},
                    {"quarter": "Q4", "due_date": f"{year}-12-01"},
                ],
                "admin_levy_per_uoe_annual": data["admin_per_uoe"],
                "admin_levy_per_uoe_quarterly": data["admin_per_uoe"] / 4,
                "sinking_levy_per_uoe_annual": data["sinking_per_uoe"],
                "sinking_levy_per_uoe_quarterly": data["sinking_per_uoe"] / 4,
                "is_seed_data": True,
                "data_origin": "modeled",
                "created_at": now,
                "updated_at": now
            }
            await db.annual_levies.insert_one(annual_levy)
            print(f"Seeded annual_levies for {year}")

        # Seed unit_levy_ledger for all 87 units if not exists
        existing_ledger = await db.unit_levy_ledger.find_one({"year": year, "plan_id": _EAST_GATE_BUILDING_ID})
        if existing_ledger:
            print(f"Year {year} already exists in unit_levy_ledger, skipping...")
        else:
            now = datetime.now(timezone.utc).isoformat()
            units = await db.units.find({},
                                        {"unit_number": 1, "lot_number": 1, "entitlement": 1, "unit_type": 1}).to_list(
                200)
            ledger_entries = []
            for unit in units:
                uoe = unit.get("entitlement", 115)
                admin_levied = data["admin_per_uoe"] * uoe
                sinking_levied = data["sinking_per_uoe"] * uoe

                ledger_entry = {
                    "id": str(uuid.uuid4()),
                    "plan_id": _EAST_GATE_BUILDING_ID,
                    "building_id": _EAST_GATE_BUILDING_ID,
                    "year": year,
                    "unit_number": unit["unit_number"],
                    "lot_number": unit["lot_number"],
                    "uoe": uoe,
                    "property_type": unit.get("unit_type", "apartment"),
                    "admin_opening": 0,
                    "admin_levied": admin_levied,
                    "admin_paid": admin_levied,  # Assume fully paid for historical
                    "admin_closing": 0,
                    "sinking_opening": 0,
                    "sinking_levied": sinking_levied,
                    "sinking_paid": sinking_levied,
                    "sinking_closing": 0,
                    "total_levied": admin_levied + sinking_levied,
                    "total_paid": admin_levied + sinking_levied,
                    "net_balance": 0,
                    "is_seed_data": True,
                    "data_origin": "modeled",
                    "created_at": now,
                    "updated_at": now
                }
                ledger_entries.append(ledger_entry)

            if ledger_entries:
                await db.unit_levy_ledger.insert_many(ledger_entries)
                print(f"Seeded unit_levy_ledger for {year} ({len(ledger_entries)} units)")

    # 2. Seed Market Snapshot
    market_snapshot = {
        "id": str(uuid.uuid4()),
        "suburb": "Denman Prospect",
        "median_price": 975000,
        "rental_yield": 4.2,
        "days_on_market": 34,
        "growth_yoy": 3.8,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.market_snapshots.update_one(
        {"suburb": "Denman Prospect"},
        {"$set": market_snapshot},
        upsert=True
    )
    print("Seeded market snapshot for Denman Prospect")

    # 3. Seed Initial Activities
    await db.activities.delete_many({"building_id": "eastgate", "is_seed_data": True})
    initial_activities = [
        {
            "type": "announcement",
            "title": "Welcome to the Enhanced East Gate Dashboard!",
            "priority": 1
        },
        {
            "type": "document",
            "title": "2025 AGM Minutes uploaded",
            "priority": 2
        },
        {
            "type": "maintenance",
            "title": "Scheduled Lift Maintenance Completed",
            "priority": 3
        },
        {
            "type": "marketplace",
            "title": "New listing: 3 Bedroom Townhouse for sale",
            "priority": 4
        }
    ]

    activity_docs = []
    for act in initial_activities:
        activity_docs.append({
            "id": str(uuid.uuid4()),
            "type": act["type"],
            "title": act["title"],
            "entity_id": None,
            "building_id": "eastgate",
            "visibility": "all",
            "priority": act["priority"],
            "metadata": {},
            "is_seed_data": True,
            "created_at": (datetime.now(timezone.utc) - timedelta(days=act["priority"])).isoformat()
        })

    if activity_docs:
        await db.activities.insert_many(activity_docs)
    print(f"Seeded {len(activity_docs)} initial activities")

    client.close()
    print("Seeding complete!")


if __name__ == "__main__":
    asyncio.run(seed())
