"""
Compliance items — fire safety, WHS, lift, pool, strata statutory obligations.

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

COMPLIANCE_ITEMS = [{'id': 'dc75c955-b9ff-494d-a399-64f7d25cb5a6', 'title': 'Annual Financial Audit',
                     'description': 'Complete annual financial audit by registered auditor. Prepare financial statements, reconcile accounts, and submit audit report to committee.',
                     'category': 'financial', 'status': 'pending', 'due_date': '2026-05-18T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'high', 'recurrence': 'annual',
                     'notes': None, 'created_by': 'system', 'created_at': '2026-02-17T14:32:13.801296+00:00',
                     'updated_at': '2026-02-17T14:32:13.801296+00:00', 'building_id': '13195'},
                    {'id': 'b6073972-fa1e-4dd2-a209-e209299f2216', 'title': 'End of Financial Year Reporting',
                     'description': 'Prepare end of financial year reports including balance sheet, income statement, cash flow, and budget comparison. Submit to committee and auditor.',
                     'category': 'financial', 'status': 'pending', 'due_date': '2026-07-17T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'high', 'recurrence': 'annual',
                     'notes': None, 'created_by': 'system', 'created_at': '2026-02-17T14:32:13.801296+00:00',
                     'updated_at': '2026-02-17T14:32:13.801296+00:00', 'building_id': '13195'},
                    {'id': 'bc8cde0a-d3ae-459b-8bff-c15b7f5744ef', 'title': 'Quarterly Levy Collection Report',
                     'description': 'Prepare quarterly levy collection report showing total levies raised, collected, and outstanding. Identify units in arrears and initiate follow-up actions.',
                     'category': 'financial', 'status': 'completed', 'due_date': '2025-12-19T14:32:13.801296+00:00',
                     'completed_date': '2026-01-03T14:32:13.801296+00:00', 'assigned_to': None, 'priority': 'high',
                     'recurrence': 'quarterly', 'notes': '95.42% collection rate achieved', 'created_by': 'system',
                     'created_at': '2026-02-17T14:32:13.801296+00:00', 'updated_at': '2026-02-17T14:32:13.801296+00:00',
                     'building_id': '13195'},
                    {'id': 'ded608fd-9923-439e-9ff7-b791509036bb', 'title': 'Annual Building Insurance Renewal',
                     'description': 'Renew comprehensive building insurance policy covering property damage, public liability, and strata liability. Review coverage limits and ensure all common areas are included.',
                     'category': 'insurance', 'status': 'pending', 'due_date': '2026-04-18T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'critical', 'recurrence': 'annual',
                     'notes': None, 'created_by': 'system', 'created_at': '2026-02-17T14:32:13.801296+00:00',
                     'updated_at': '2026-02-17T14:32:13.801296+00:00', 'building_id': '13195'},
                    {'id': '2f570c6f-d79b-41b6-a4fe-aaa78438491a', 'title': 'Annual General Meeting Planning',
                     'description': 'Schedule and prepare for Annual General Meeting. Prepare agenda, financial reports, committee reports, and notify all owners 21 days in advance.',
                     'category': 'regulatory', 'status': 'completed', 'due_date': '2026-02-23',
                     'completed_date': '2026-02-27T01:51:28.872613+00:00', 'assigned_to': None, 'priority': 'high',
                     'recurrence': 'annual', 'notes': 'AGM completed with 14 in attendance', 'created_by': 'system',
                     'created_at': '2026-02-17T14:32:13.801296+00:00', 'updated_at': '2026-02-27T01:51:28.872588+00:00',
                     'building_id': '13195'},
                    {'id': 'd47c3f1e-d40a-477c-9d90-dae8e93d481e', 'title': 'Strata Register Update',
                     'description': 'Update strata register with any ownership changes, lot entitlements, and contact details. Ensure compliance with Strata Schemes Management Act.',
                     'category': 'regulatory', 'status': 'completed', 'due_date': '2026-01-18T14:32:13.801296+00:00',
                     'completed_date': '2026-02-02T14:32:13.801296+00:00', 'assigned_to': None, 'priority': 'medium',
                     'recurrence': 'quarterly', 'notes': 'Updated with 3 new owner changes', 'created_by': 'system',
                     'created_at': '2026-02-17T14:32:13.801296+00:00', 'updated_at': '2026-02-17T14:32:13.801296+00:00',
                     'building_id': '13195'},
                    {'id': '1b3a1977-5293-4095-b684-eb66cc79e06d', 'title': 'Fire Safety Equipment Inspection',
                     'description': 'Annual inspection of all fire safety equipment including fire extinguishers, smoke alarms, fire hoses, and emergency lighting. Certification required by law.',
                     'category': 'safety', 'status': 'pending', 'due_date': '2026-03-19T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'critical', 'recurrence': 'annual',
                     'notes': None, 'created_by': 'system', 'created_at': '2026-02-17T14:32:13.801296+00:00',
                     'updated_at': '2026-02-17T14:32:13.801296+00:00', 'building_id': '13195'},
                    {'id': '12894f0a-d9a6-4051-b866-db5fbb77d0e3', 'title': 'Elevator Annual Inspection',
                     'description': 'Mandatory annual elevator safety inspection by certified inspector. Check brakes, cables, doors, emergency systems, and obtain compliance certificate.',
                     'category': 'safety', 'status': 'pending', 'due_date': '2026-04-03T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'high', 'recurrence': 'annual',
                     'notes': None, 'created_by': 'system', 'created_at': '2026-02-17T14:32:13.801296+00:00',
                     'updated_at': '2026-02-17T14:32:13.801296+00:00', 'building_id': '13195'},
                    {'id': '07413264-35b3-4d4f-8963-50e1114d038a', 'title': 'Pool Safety Certificate Renewal',
                     'description': 'Renew pool safety certificate. Inspect pool fence, gates, latches, and ensure compliance with Australian Standard AS 1926.1. Certificate valid for 3 years.',
                     'category': 'safety', 'status': 'in_progress', 'due_date': '2026-06-17T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'medium', 'recurrence': None,
                     'notes': 'Inspector scheduled for next week', 'created_by': 'system',
                     'created_at': '2026-02-17T14:32:13.801296+00:00', 'updated_at': '2026-02-17T14:32:13.801296+00:00',
                     'building_id': '13195'},
                    {'id': 'f6bcfdeb-3c96-4875-9aa4-94986bec36fe', 'title': 'WHS Risk Assessment Review',
                     'description': 'Review and update Workplace Health & Safety risk assessment. Identify hazards in common areas, assess risks, implement controls, and document findings.',
                     'category': 'safety', 'status': 'pending', 'due_date': '2026-05-03T14:32:13.801296+00:00',
                     'completed_date': None, 'assigned_to': None, 'priority': 'medium', 'recurrence': 'annual',
                     'notes': None, 'created_by': 'system', 'created_at': '2026-02-17T14:32:13.801296+00:00',
                     'updated_at': '2026-02-17T14:32:13.801296+00:00', 'building_id': '13195'}]


async def seed_compliance_items():
    """Upsert all compliance_items entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in COMPLIANCE_ITEMS:
        result = await db.compliance_items.update_one(
            {'id': entry['id']},
            {'$set': entry, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'compliance_items: {upserted} upserted ({len(COMPLIANCE_ITEMS)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_compliance_items())
