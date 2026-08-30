"""
Annual levy schedule — UOE rates and quarterly due dates per year.

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

ANNUAL_LEVIES = [{'id': '82a4898d-505f-42ef-9037-544a5a778879', 'plan_id': '13195', 'year': '2021', 'status': 'actual',
                  'data_origin': 'user_provided', 'is_seed_data': False, 'total_uoe': 10000,
                  'period_note': 'First strata year (Nov 2020–Dec 2021, ~14 months). Sinking Fund not yet established — left for first EC to provision. $23,761.63 admin deficit carried into 2022 opening balance.',
                  'admin_fund': {'levy_income': 138460.0, 'other_income': 0.0, 'total_income': 138460.0,
                                 'total_expenses': 162221.63, 'opening_balance': 0.0, 'closing_balance': -23761.63,
                                 'surplus_deficit': -23761.63}, 'admin_levy_per_uoe_annual': 13.846,
                  'admin_levy_per_uoe_quarterly': 3.4615,
                  'sinking_fund': {'levy_income': 0.0, 'other_income': 0.0, 'total_income': 0.0, 'total_expenses': 0.0,
                                   'opening_balance': 0.0, 'closing_balance': 0.0, 'surplus_deficit': 0.0},
                  'sinking_levy_per_uoe_annual': 0, 'sinking_levy_per_uoe_quarterly': 0,
                  'payment_schedule': [{'quarter': 'Q1', 'due_date': '2021-03-31'},
                                       {'quarter': 'Q2', 'due_date': '2021-06-30'},
                                       {'quarter': 'Q3', 'due_date': '2021-10-31'},
                                       {'quarter': 'Q4', 'due_date': '2022-01-31'}], 'notes': '',
                  'created_at': '2026-02-28T08:07:58.234010+00:00', 'updated_at': '2026-02-28T08:07:58.234010+00:00',
                  'proposed_admin_expenses': 138460.0, 'proposed_sinking_expenses': 0.0, 'building_id': '13195'},
                 {'id': 'a8e608d0-30ff-4e5e-8e5a-35d1e72dca4f', 'plan_id': '13195', 'year': '2022', 'status': 'actual',
                  'data_origin': 'user_provided', 'is_seed_data': False, 'total_uoe': 10000,
                  'period_note': 'First full calendar year. Sinking Fund established by EC. $15,000 sinking income includes prior-year contributions back-levied from 2021. Admin $23,761.63 deficit from 2021 rolled in as opening balance.',
                  'admin_fund': {'levy_income': 221900.0, 'other_income': 3004.27, 'total_income': 224904.27,
                                 'total_expenses': 216901.65, 'opening_balance': -23761.63,
                                 'closing_balance': -15759.01, 'surplus_deficit': 8002.62},
                  'admin_levy_per_uoe_annual': 22.19, 'admin_levy_per_uoe_quarterly': 5.5475,
                  'sinking_fund': {'levy_income': 51111.0, 'other_income': 15000.0, 'total_income': 25000.0,
                                   'total_expenses': 4350.0, 'opening_balance': 0.0, 'closing_balance': 46761.0,
                                   'surplus_deficit': 780.39}, 'sinking_levy_per_uoe_annual': 1.0,
                  'sinking_levy_per_uoe_quarterly': 0.25,
                  'payment_schedule': [{'quarter': 'Q1', 'due_date': '2022-03-31'},
                                       {'quarter': 'Q2', 'due_date': '2022-06-30'},
                                       {'quarter': 'Q3', 'due_date': '2022-09-30'},
                                       {'quarter': 'Q4', 'due_date': '2022-12-31'}], 'notes': '',
                  'created_at': '2026-02-28T08:07:58.234010+00:00', 'updated_at': '2026-02-28T08:07:58.234010+00:00',
                  'proposed_admin_expenses': 228089.17, 'proposed_sinking_expenses': 25000.0, 'building_id': '13195'},
                 {'id': '19899770-781f-4669-9edb-1dd58ceb119c', 'plan_id': '13195', 'year': '2023', 'status': 'actual',
                  'data_origin': 'civium_pdf', 'is_seed_data': False, 'total_uoe': 10000,
                  'period_note': "Civium Strata management period 02/05/2023–31/12/2023 (8-month partial year). Opening balances represent the fund position when Civium took over (May 2023). Jan–Apr 2023 (under previous manager) is not separately recorded. Closing = Dec 31, 2023 from Civium management report (real anchor for 2024 opening). Source: east_gate_proposed_budget_2024.pdf '2023 Actual' column.",
                  'admin_fund': {'levy_income': 165986.79, 'other_income': 2823.74, 'total_income': 168810.53,
                                 'total_expenses': 169266.08, 'opening_balance': 49718.78, 'closing_balance': 49263.23,
                                 'surplus_deficit': -455.55}, 'admin_levy_per_uoe_annual': 16.5987,
                  'admin_levy_per_uoe_quarterly': 4.1497,
                  'sinking_fund': {'levy_income': 60948.0, 'other_income': 517.89, 'total_income': 46235.97,
                                   'total_expenses': 8447.0, 'opening_balance': 46761.0, 'closing_balance': 99262.0,
                                   'surplus_deficit': 14895.19}, 'sinking_levy_per_uoe_annual': 4.5718,
                  'sinking_levy_per_uoe_quarterly': 1.143,
                  'payment_schedule': [{'quarter': 'Q3', 'due_date': '2023-08-01'},
                                       {'quarter': 'Q4', 'due_date': '2023-11-01'}], 'notes': '',
                  'created_at': '2026-02-28T08:07:58.234010+00:00', 'updated_at': '2026-02-28T08:07:58.234010+00:00',
                  'proposed_admin_expenses': 250945.87, 'proposed_sinking_expenses': 93400.0, 'building_id': '13195'},
                 {'id': 'f697a98a-5ed9-4425-a83f-fc03efa52f46', 'plan_id': '13195', 'year': '2024', 'status': 'actual',
                  'data_origin': 'derived_actual', 'is_seed_data': False, 'total_uoe': 10000,
                  'period_note': 'Full calendar year 01/01/2024–31/12/2024. Actual admin expenses $311,666.80 and sinking $1,618.18 from 5-year summary. Opening balances from Civium Dec-31-2023 management report. Closing = 2025 audited opening (real anchor). Income derived via: income = closing − opening + expenses. Per-category actuals sourced from 2024 budget comparison column.',
                  'admin_fund': {'levy_income': 223496.0, 'other_income': 1586.41, 'total_income': 225082.41,
                                 'total_expenses': 311666.8, 'opening_balance': 49263.23, 'closing_balance': -37321.16,
                                 'surplus_deficit': -86584.39}, 'admin_levy_per_uoe_annual': 22.3496,
                  'admin_levy_per_uoe_quarterly': 5.5874,
                  'sinking_fund': {'levy_income': 70785.0, 'other_income': 0.0, 'total_income': 68738.48,
                                   'total_expenses': 9815.0, 'opening_balance': 99262.0, 'closing_balance': 160232.0,
                                   'surplus_deficit': 67120.3}, 'sinking_levy_per_uoe_annual': 6.8738,
                  'sinking_levy_per_uoe_quarterly': 1.7185,
                  'payment_schedule': [{'quarter': 'Q1', 'due_date': '2024-04-01'},
                                       {'quarter': 'Q2', 'due_date': '2024-07-01'},
                                       {'quarter': 'Q3', 'due_date': '2024-10-01'},
                                       {'quarter': 'Q4', 'due_date': '2025-01-01'}], 'notes': '',
                  'created_at': '2026-02-28T08:07:58.234010+00:00', 'updated_at': '2026-02-28T08:07:58.234010+00:00',
                  'proposed_admin_expenses': 223496.0, 'proposed_sinking_expenses': 70795.0, 'building_id': '13195'},
                 {'id': 'ae136c64-9766-4e1b-ae41-a297d121b300', 'plan_id': '13195', 'year': '2025', 'status': 'actual',
                  'data_origin': 'audited_pdf', 'is_seed_data': False, 'total_uoe': 10000,
                  'period_note': 'Full year FY 2024-25 (01/07/2024–30/06/2025) under Civium Strata. Source: east_gate_audited_financial_report_2025.pdf.',
                  'admin_fund': {'levy_income': 317948.97, 'other_income': 31703.26, 'total_income': 349652.23,
                                 'total_expenses': 277998.8, 'opening_balance': -37321.16, 'closing_balance': 34332.27,
                                 'surplus_deficit': 71653.43}, 'admin_levy_per_uoe_annual': 31.7949,
                  'admin_levy_per_uoe_quarterly': 7.9487,
                  'sinking_fund': {'levy_income': 80622.0, 'other_income': 26865.04, 'total_income': 117324.04,
                                   'total_expenses': 14950.0, 'opening_balance': 160232.0, 'closing_balance': 225904.0,
                                   'surplus_deficit': 69284.35}, 'sinking_levy_per_uoe_annual': 9.0459,
                  'sinking_levy_per_uoe_quarterly': 2.2615,
                  'payment_schedule': [{'quarter': 'Q1', 'due_date': '2024-10-01'},
                                       {'quarter': 'Q2', 'due_date': '2025-01-01'},
                                       {'quarter': 'Q3', 'due_date': '2025-04-01'},
                                       {'quarter': 'Q4', 'due_date': '2025-07-01'}], 'notes': '',
                  'created_at': '2026-02-28T08:07:58.234010+00:00', 'updated_at': '2026-02-28T08:07:58.234010+00:00',
                  'proposed_admin_expenses': 262076.0, 'proposed_sinking_expenses': 99262.0, 'building_id': '13195'},
                 {'id': '574e140f-f800-4fd5-ab40-9afdc4dbf752', 'plan_id': '13195', 'year': '2026', 'status': 'actual',
                  'data_origin': 'proposed_budget', 'is_seed_data': False, 'total_uoe': 10000,
                  'period_note': 'Proposed budget FY 2025-26 (01/07/2025–30/06/2026). Source: east_gate_proposed_budget_2026.pdf. Actuals will be updated after year-end audit.',
                  'admin_fund': {'levy_income': 309882.0, 'other_income': 0.0, 'total_income': 309882.0,
                                 'total_expenses': 309882.0, 'opening_balance': 34332.27, 'closing_balance': 180000,
                                 'surplus_deficit': 0.0, 'current_balance': 9187.44},
                  # Compatibility per-UOE rates are owner-payable: admin=340870.20/10000=34.087,
                  # sinking=99504.90/10000=9.9505. Canonical fund totals below remain ex-GST.
                  'admin_levy_per_uoe_annual': 34.087, 'admin_levy_per_uoe_quarterly': 8.5218,
                  'sinking_fund': {'levy_income': 90459.0, 'other_income': 0.0, 'total_income': 90459.0,
                                   'total_expenses': 17965.0, 'opening_balance': 225904.0, 'closing_balance': 298398.0,
                                   'surplus_deficit': 0.0, 'current_balance': 193337.03},
                  'sinking_levy_per_uoe_annual': 9.9505, 'sinking_levy_per_uoe_quarterly': 2.4876,
                  'payment_schedule': [{'quarter': 'Q1', 'due_date': '2025-10-01'},
                                       {'quarter': 'Q2', 'due_date': '2026-01-01'},
                                       {'quarter': 'Q3', 'due_date': '2026-03-31'},
                                       {'quarter': 'Q4', 'due_date': '2026-07-01'}], 'notes': '',
                  'created_at': '2026-02-28T08:07:58.234010+00:00', 'updated_at': '2026-03-15T03:24:07.613265+00:00',
                  # Canonical fund totals are ex-GST. Compatibility per-UOE fields above are the
                  # owner-payable amounts derived from those ex-GST totals plus GST.
                  'proposed_admin_expenses': 309882.0, 'proposed_sinking_expenses': 90459.0, 'building_id': '13195'},
                 {'id': '4390379b-a6e1-4730-b128-6ba94302cde9', 'plan_id': '16244', 'building_id': '16244',
                  'year': '2025', 'status': 'approved', 'data_origin': 'seed', 'is_seed_data': True, 'total_uoe': 9.0,
                  'period_note': 'FY 2025–2026',
                  'admin_fund': {'levy_income': 22500.0, 'other_income': 0.0, 'total_income': 22500.0,
                                 'total_expenses': 18450.0, 'opening_balance': 2250.0, 'closing_balance': 6300.0,
                                 'surplus_deficit': 4050.0, 'current_balance': 6300.0},
                  'admin_levy_per_uoe_annual': 2500.0, 'admin_levy_per_uoe_quarterly': 625.0,
                  'sinking_fund': {'levy_income': 6750.0, 'other_income': 0.0, 'total_income': 6750.0,
                                   'total_expenses': 3375.0, 'opening_balance': 2025.0, 'closing_balance': 5400.0,
                                   'surplus_deficit': 3375.0, 'current_balance': 5400.0},
                  'sinking_levy_per_uoe_annual': 750.0, 'sinking_levy_per_uoe_quarterly': 187.5,
                  'payment_schedule': 'quarterly', 'notes': 'Sierra 2025 levy schedule — demo seed data',
                  'created_at': '2026-03-30T02:27:55.774598+00:00', 'updated_at': '2026-03-30T02:27:55.774598+00:00'},
                 {'id': 'd0b61e0c-e49c-42a3-a39a-cb37b5d3bd7f', 'plan_id': '16244', 'building_id': '16244',
                  'year': '2026', 'status': 'approved', 'data_origin': 'seed', 'is_seed_data': True, 'total_uoe': 9.0,
                  'period_note': 'FY 2026–2027',
                  'admin_fund': {'levy_income': 22500.0, 'other_income': 0.0, 'total_income': 22500.0,
                                 'total_expenses': 18450.0, 'opening_balance': 2250.0, 'closing_balance': 6300.0,
                                 'surplus_deficit': 4050.0, 'current_balance': 6300.0},
                  'admin_levy_per_uoe_annual': 2500.0, 'admin_levy_per_uoe_quarterly': 625.0,
                  'sinking_fund': {'levy_income': 6750.0, 'other_income': 0.0, 'total_income': 6750.0,
                                   'total_expenses': 3375.0, 'opening_balance': 2025.0, 'closing_balance': 5400.0,
                                   'surplus_deficit': 3375.0, 'current_balance': 5400.0},
                  'sinking_levy_per_uoe_annual': 750.0, 'sinking_levy_per_uoe_quarterly': 187.5,
                  'payment_schedule': 'quarterly', 'notes': 'Sierra 2026 levy schedule — demo seed data',
                  'created_at': '2026-03-30T02:27:55.774598+00:00', 'updated_at': '2026-03-30T02:27:55.774598+00:00'},
                 {'id': '50f73553-8aaf-4613-a930-30d88423787d', 'plan_id': '18932', 'building_id': '18932',
                  'year': '2025', 'status': 'approved', 'data_origin': 'seed', 'is_seed_data': True, 'total_uoe': 3.0,
                  'period_note': 'FY 2025–2026',
                  'admin_fund': {'levy_income': 10800.0, 'other_income': 0.0, 'total_income': 10800.0,
                                 'total_expenses': 8424.0, 'opening_balance': 1620.0, 'closing_balance': 3996.0,
                                 'surplus_deficit': 2376.0, 'current_balance': 3996.0},
                  'admin_levy_per_uoe_annual': 3600.0, 'admin_levy_per_uoe_quarterly': 900.0,
                  'sinking_fund': {'levy_income': 3600.0, 'other_income': 0.0, 'total_income': 3600.0,
                                   'total_expenses': 1620.0, 'opening_balance': 1440.0, 'closing_balance': 3420.0,
                                   'surplus_deficit': 1980.0, 'current_balance': 3420.0},
                  'sinking_levy_per_uoe_annual': 3600.0, 'sinking_levy_per_uoe_quarterly': 300.0,
                  'payment_schedule': 'quarterly', 'notes': 'Harbourview 2025 levy schedule — demo seed data',
                  'created_at': '2026-03-30T02:27:55.774598+00:00', 'updated_at': '2026-03-30T02:27:55.774598+00:00'},
                 {'id': '841de681-dbb0-4829-978c-af0ce00119f6', 'plan_id': '18932', 'building_id': '18932',
                  'year': '2026', 'status': 'approved', 'data_origin': 'seed', 'is_seed_data': True, 'total_uoe': 3.0,
                  'period_note': 'FY 2026–2027',
                  'admin_fund': {'levy_income': 10800.0, 'other_income': 0.0, 'total_income': 10800.0,
                                 'total_expenses': 8424.0, 'opening_balance': 1620.0, 'closing_balance': 3996.0,
                                 'surplus_deficit': 2376.0, 'current_balance': 3996.0},
                  'admin_levy_per_uoe_annual': 3600.0, 'admin_levy_per_uoe_quarterly': 900.0,
                  'sinking_fund': {'levy_income': 3600.0, 'other_income': 0.0, 'total_income': 3600.0,
                                   'total_expenses': 1620.0, 'opening_balance': 1440.0, 'closing_balance': 3420.0,
                                   'surplus_deficit': 1980.0, 'current_balance': 3420.0},
                  'sinking_levy_per_uoe_annual': 3600.0, 'sinking_levy_per_uoe_quarterly': 300.0,
                  'payment_schedule': 'quarterly', 'notes': 'Harbourview 2026 levy schedule — demo seed data',
                  'created_at': '2026-03-30T02:27:55.774598+00:00', 'updated_at': '2026-03-30T02:27:55.774598+00:00'}]


async def seed_annual_levies():
    """Upsert all annual_levies entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in ANNUAL_LEVIES:
        # Exclude created_at from $set — it must only appear in $setOnInsert.
        # MongoDB raises WriteError "conflict at 'created_at'" if the same field
        # path appears in both operators.
        set_fields = {k: v for k, v in entry.items() if k != 'created_at'}
        result = await db.annual_levies.update_one(
            {'plan_id': entry['plan_id'], 'year': entry['year']},
            {'$set': set_fields, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'annual_levies: {upserted} upserted ({len(ANNUAL_LEVIES)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_annual_levies())
