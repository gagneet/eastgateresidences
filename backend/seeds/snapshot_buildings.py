"""
Buildings registry — East Gate (13195), Sierra (16244), Harbourside View.

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

BUILDINGS = [{'id': '13195', 'address': '14 Hoolihan Street, Denman Prospect ACT 2611', 'lots': 87,
              'name': 'East Gate Residences', 'year_built': 2018, 'is_active': True, 'slug': 'eastgate',
              'trust_config': {'current_financial_year': '2026-27',
                               # Budget in cents, stored ex-GST. Admin $309,882 ex-GST, Sinking $90,459 ex-GST.
                               'admin_fund_annual_budget_cents': 34087020,  # inc-GST levy billing target: $340,870.20
                               'sinking_fund_annual_budget_cents': 9950490,  # inc-GST levy billing target: $99,504.90
                               'quarterly_due_dates': ['2026-03-01', '2026-06-01', '2026-09-01', '2026-12-01'],
                               'grace_period_days': 14, 'arrears_interest_rate': 0.1,
                               'deft_biller_code_admin': 'MOCK-EG-452301', 'deft_biller_code_sinking': 'MOCK-EG-452302',
                               'bank_name': 'Commonwealth Bank',
                               'manager_alert_email': 'admin@eastgateresidences.com.au', 'arrears_reminder_days': 14,
                               'arrears_formal_notice_days': 21, 'arrears_interest_charge_days': 30,
                               'arrears_legal_flag_days': 60, 'is_trust_configured': True,
                               'trust_configured_at': '2026-03-24T11:11:00.862934+00:00',
                               'trust_configured_by': '69907712810bd33a2dc71c20'}},
             {'id': '16244', 'name': 'Sierra', 'address': '70 Efkarpidis Street, Gungahlin ACT 2912', 'lots': 120,
              'year_built': 2022, 'is_active': True, 'slug': 'sierra', 'description': 'Sierra Gungahlin strata complex',
              'is_demo': False, 'created_at': '2026-03-12T02:43:00.176507+00:00',
              'trust_config': {'current_financial_year': '2026-27', 'admin_fund_annual_budget_cents': 18000000,
                               'sinking_fund_annual_budget_cents': 6000000,
                               'quarterly_due_dates': ['2026-03-01', '2026-06-01', '2026-09-01', '2026-12-01'],
                               'grace_period_days': 14, 'arrears_interest_rate': 0.1,
                               'deft_biller_code_admin': 'MOCK-SG-456001', 'deft_biller_code_sinking': 'MOCK-SG-456002',
                               'bank_name': 'Westpac', 'manager_alert_email': 'manager@sierra.test',
                               'arrears_reminder_days': 14, 'arrears_formal_notice_days': 21,
                               'arrears_interest_charge_days': 30, 'arrears_legal_flag_days': 60,
                               'is_trust_configured': True, 'trust_configured_at': '2026-03-24T11:11:00.875413+00:00',
                               'trust_configured_by': '69907712810bd33a2dc71c20', 'total_uoe': 500}},
             {'id': '18932', 'name': 'Harbourview Residences', 'address': '12 Marina Drive, Pyrmont NSW 2009',
              'lots': 48, 'year_built': 2020, 'is_active': True, 'slug': 'harbourview',
              'description': 'Demo building for multi-tenant testing', 'is_demo': True,
              'created_at': '2026-03-12T02:43:00.176507+00:00',
              'trust_config': {'current_financial_year': '2026-27', 'admin_fund_annual_budget_cents': 32000000,
                               'sinking_fund_annual_budget_cents': 10000000,
                               'quarterly_due_dates': ['2026-03-15', '2026-06-15', '2026-09-15', '2026-12-15'],
                               'grace_period_days': 21, 'arrears_interest_rate': 0.1,
                               'deft_biller_code_admin': 'MOCK-HV-789001', 'deft_biller_code_sinking': 'MOCK-HV-789002',
                               'bank_name': 'ANZ', 'manager_alert_email': 'manager@harbourview.test',
                               'arrears_reminder_days': 14, 'arrears_formal_notice_days': 21,
                               'arrears_interest_charge_days': 30, 'arrears_legal_flag_days': 60,
                               'is_trust_configured': True, 'trust_configured_at': '2026-03-24T11:11:00.886659+00:00',
                               'trust_configured_by': '69907712810bd33a2dc71c20', 'total_uoe': 500},
              'plan_number': '18932'}]


async def seed_buildings():
    """Upsert all buildings entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in BUILDINGS:
        result = await db.buildings.update_one(
            {'id': entry['id']},
            {'$set': entry, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'buildings: {upserted} upserted ({len(BUILDINGS)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_buildings())
