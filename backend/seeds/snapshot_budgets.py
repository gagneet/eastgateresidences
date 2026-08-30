"""
Annual budgets — admin fund and sinking fund category breakdowns.

Generated from live DB on 2026-04-01. DO NOT EDIT MANUALLY.
Regenerate with:  cd backend && venv/bin/python3 ../scripts/db/snapshot_all.py
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

BUDGETS = [
    {'id': 'cb58fea8-63ff-4d21-a364-ef41ad07f334', 'financial_year': '2024-2025', 'admin_fund_opening': 140937.78,
     'sinking_fund_opening': 106039.46, 'admin_fund_income': 349652.23, 'admin_fund_expenses': 277998.8,
     'admin_fund_closing': 212591.21, 'sinking_fund_income': 117324.04, 'sinking_fund_expenses': 48039.69,
     'sinking_fund_closing': 175323.81, 'approved_at': '2026-02-19T11:27:26.512615+00:00',
     'approved_by': 'admin@eastgate.com', 'created_at': '2026-02-19T11:27:26.512630+00:00', 'categories': [
        {'name': 'Gardens', 'budgeted_amount': 32696.0, 'fund_type': 'administrative', 'actual_amount': 32696.0},
        {'name': 'Water', 'budgeted_amount': 34361.0, 'fund_type': 'administrative', 'actual_amount': 34361.0},
        {'name': 'Strata Management', 'budgeted_amount': 29294.0, 'fund_type': 'administrative',
         'actual_amount': 29294.0},
        {'name': 'Cleaning Services', 'budgeted_amount': 27738.0, 'fund_type': 'administrative',
         'actual_amount': 27738.0},
        {'name': 'Building Insurance', 'budgeted_amount': 25000.0, 'fund_type': 'administrative',
         'actual_amount': 25000.0},
        {'name': 'Electricity', 'budgeted_amount': 23631.0, 'fund_type': 'administrative', 'actual_amount': 23631.0},
        {'name': 'Repairs & Maintenance', 'budgeted_amount': 15000.0, 'fund_type': 'administrative',
         'actual_amount': 15000.0},
        {'name': 'Waste Management', 'budgeted_amount': 10200.0, 'fund_type': 'administrative',
         'actual_amount': 10200.0},
        {'name': 'Administrative Costs', 'budgeted_amount': 8000.0, 'fund_type': 'administrative',
         'actual_amount': 8000.0},
        {'name': 'Legal & Professional Fees', 'budgeted_amount': 6500.0, 'fund_type': 'administrative',
         'actual_amount': 6500.0},
        {'name': 'Security Monitoring', 'budgeted_amount': 5100.0, 'fund_type': 'administrative',
         'actual_amount': 5100.0},
        {'name': 'Emergency Repairs', 'budgeted_amount': 12180.0, 'fund_type': 'administrative',
         'actual_amount': 12180.0},
        {'name': 'Banking & Finance', 'budgeted_amount': 1000.0, 'fund_type': 'administrative',
         'actual_amount': 1000.0},
        {'name': 'AGM & Meeting Costs', 'budgeted_amount': 3200.0, 'fund_type': 'administrative',
         'actual_amount': 3200.0},
        {'name': 'Fire Safety Testing', 'budgeted_amount': 1650.0, 'fund_type': 'administrative',
         'actual_amount': 1650.0},
        {'name': 'Lift Inspection', 'budgeted_amount': 2850.0, 'fund_type': 'administrative', 'actual_amount': 2850.0},
        {'name': 'Building Signage', 'budgeted_amount': 980.0, 'fund_type': 'administrative', 'actual_amount': 980.0},
        {'name': 'Other Expenses', 'budgeted_amount': 38618.8, 'fund_type': 'administrative', 'actual_amount': 38618.8},
        {'name': 'Lift Maintenance', 'budgeted_amount': 24448.0, 'fund_type': 'sinking', 'actual_amount': 24448.0},
        {'name': 'Roof Repairs', 'budgeted_amount': 8500.0, 'fund_type': 'sinking', 'actual_amount': 8500.0},
        {'name': 'Car Park Maintenance', 'budgeted_amount': 6800.0, 'fund_type': 'sinking', 'actual_amount': 6800.0},
        {'name': 'Fire Safety Systems', 'budgeted_amount': 4200.0, 'fund_type': 'sinking', 'actual_amount': 4200.0},
        {'name': 'Security Systems', 'budgeted_amount': 2350.0, 'fund_type': 'sinking', 'actual_amount': 2350.0},
        {'name': 'Building Repairs', 'budgeted_amount': 1741.69, 'fund_type': 'sinking', 'actual_amount': 1741.69}],
     'building_id': '13195'}]


async def seed_budgets():
    """Upsert all budgets entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in BUDGETS:
        result = await db.budgets.update_one(
            {'building_id': entry['building_id'], 'financial_year': entry['financial_year']},
            {'$set': entry, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'budgets: {upserted} upserted ({len(BUDGETS)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_budgets())
