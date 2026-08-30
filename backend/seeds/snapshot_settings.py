"""
Building settings — levy schedules, grace periods, contact info.

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

SETTINGS = [{'id': 'main', 'building_name': 'East Gate Residences',
             'building_address': '14 Hoolihan Street, Denman Prospect, ACT 2611', 'building_phone': '+61 2 6123 4567',
             'building_email': 'admin@eastgate.gagneet.com', 'manager_name': 'East Gate Strata Management',
             'manager_email': 'manager@eastgate.gagneet.com', 'manager_phone': '+61 2 6123 4568',
             'hero_image': '/images/east_gate_residences.jpg', 'logo_url': None, 'total_units': 87,
             'total_entitlements': 140.0, 'timezone': 'Australia/Canberra', 'financial_year_start': '07-01',
             'levy_payment_methods': ['DEFT', 'BPAY', 'Direct Deposit', 'Credit Card'],
             'directory_visible_to_residents': True, 'marketplace_enabled': True, 'rate_limit_register': 5,
             'rate_limit_login': 10, 'rate_limit_forgot_password': 5, 'rate_limit_reset_password': 5,
             'rate_limit_change_password': 10, 'rate_limit_registration_decision': 10, 'rate_limit_multiplier': 1.0,
             'created_at': '2026-04-01T05:46:23.638786+00:00', 'updated_at': '2026-04-01T05:46:23.638800+00:00'},
            {'building_id': '13195', 'updated_at': '2026-03-29T11:55:31.430303+00:00',
             'ip_string': 'A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au',
             'about_content': '', 'admin_auto_approve_minutes': 15, 'building_address': '', 'building_description': '',
             'building_name': '', 'contact_email': '', 'contact_phone': '', 'ec_contact_phone': '(02) 6285 0325',
             'ec_email': 'eastgatedenman13195@gmail.com', 'financial_year_start_month': 1, 'footer_text': '',
             'grace_period_days': 14, 'guest_escalation_hours': 2, 'handbook_url': '/user-guides/full_guide.html',
             'hero_image': '', 'interest_rate_per_month': 0.02, 'levy_collection_frequency': 'quarterly',
             'levy_due_custom_dates': {'3': 31, '6': 1, '9': 1, '12': 1}, 'levy_due_day': None,
             'levy_due_day_type': 'custom', 'levy_due_months': [3, 6, 9, 12],
             'notify_bcc_email': 'gagneet@eastgateresidences.com.au', 'notify_bcc_name': 'Building Administrator',
             'penalty_amount': 50.0, 'quick_links': [], 'resident_links': [], 'tenant_escalation_hours': 48,
             'timezone': 'Australia/Sydney', 'token_validity_hours': 72,
             'unit_plan_url': '/user-guides/units_plan.html',
             'inclusions_url': '/user-guides/east_gate_inclusions.html'},
            {'building_id': '16244', 'building_address': '70 Efkarpidis Street, Gungahlin ACT 2912',
             'building_description': 'Luxury living in the heart of Gungahlin.', 'building_name': 'Sierra Gungahlin',
             'contact_phone': '+61 2 6123 4567', 'created_at': '2026-03-29T23:22:00.981575+00:00',
             'manager_email': 'manager@eastgate.com'},
            {'building_id': '18932', 'building_address': '12 Marina Drive, Pyrmont NSW 2009',
             'building_description': 'Demo waterfront residence for multi-tenant testing.',
             'building_name': 'Harbourview Residences', 'contact_phone': '+61 2 9552 1000',
             'created_at': '2026-03-29T23:22:03.716119+00:00', 'manager_email': 'manager1@eastgate.com'}]


async def seed_settings():
    """Upsert all settings entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in SETTINGS:
        if 'building_id' in entry:
            query = {'building_id': entry['building_id']}
        else:
            query = {'id': entry['id']}
        set_payload = {k: v for k, v in entry.items() if k != 'created_at'}
        result = await db.settings.update_one(
            query,
            {'$set': set_payload, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'settings: {upserted} upserted ({len(SETTINGS)} total)')
    await client.close()


if __name__ == "__main__":
    asyncio.run(seed_settings())
