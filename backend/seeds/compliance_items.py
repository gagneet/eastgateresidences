"""
Compliance Items Seed Data

Creates initial compliance checklist items for regulatory, insurance, safety, and maintenance requirements.
"""
import uuid
from datetime import datetime, timezone, timedelta


def get_compliance_items_seed_data():
    """
    Returns list of compliance items to seed into the database.

    Categories:
    - regulatory: Government and legal compliance
    - insurance: Insurance-related requirements
    - safety: Safety inspections and certifications
    - maintenance: Scheduled maintenance compliance
    - financial: Financial reporting and audits
    - operational: Operational certifications and licenses
    """

    # Calculate due dates
    now = datetime.now(timezone.utc)
    today = now.isoformat()

    # Annual items due in different months
    insurance_due = (now + timedelta(days=60)).isoformat()  # 2 months
    audit_due = (now + timedelta(days=90)).isoformat()  # 3 months
    fire_safety_due = (now + timedelta(days=30)).isoformat()  # 1 month
    elevator_due = (now + timedelta(days=45)).isoformat()  # 1.5 months
    agm_due = (now + timedelta(days=180)).isoformat()  # 6 months
    pool_safety_due = (now + timedelta(days=120)).isoformat()  # 4 months
    eofy_due = (now + timedelta(days=150)).isoformat()  # 5 months
    whs_due = (now + timedelta(days=75)).isoformat()  # 2.5 months

    items = [
        {
            'id': str(uuid.uuid4()),
            'title': 'Annual Building Insurance Renewal',
            'description': 'Renew comprehensive building insurance policy covering property damage, public liability, and strata liability. Review coverage limits and ensure all common areas are included.',
            'category': 'insurance',
            'status': 'pending',
            'due_date': insurance_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'critical',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Annual Financial Audit',
            'description': 'Complete annual financial audit by registered auditor. Prepare financial statements, reconcile accounts, and submit audit report to committee.',
            'category': 'financial',
            'status': 'pending',
            'due_date': audit_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'high',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Fire Safety Equipment Inspection',
            'description': 'Annual inspection of all fire safety equipment including fire extinguishers, smoke alarms, fire hoses, and emergency lighting. Certification required by law.',
            'category': 'safety',
            'status': 'pending',
            'due_date': fire_safety_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'critical',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Elevator Annual Inspection',
            'description': 'Mandatory annual elevator safety inspection by certified inspector. Check brakes, cables, doors, emergency systems, and obtain compliance certificate.',
            'category': 'safety',
            'status': 'pending',
            'due_date': elevator_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'high',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Annual General Meeting Planning',
            'description': 'Schedule and prepare for Annual General Meeting. Prepare agenda, financial reports, committee reports, and notify all owners 21 days in advance.',
            'category': 'regulatory',
            'status': 'pending',
            'due_date': agm_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'high',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Pool Safety Certificate Renewal',
            'description': 'Renew pool safety certificate. Inspect pool fence, gates, latches, and ensure compliance with Australian Standard AS 1926.1. Certificate valid for 3 years.',
            'category': 'safety',
            'status': 'in_progress',
            'due_date': pool_safety_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'medium',
            'recurrence': None,  # Every 3 years
            'notes': 'Inspector scheduled for next week',
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'End of Financial Year Reporting',
            'description': 'Prepare end of financial year reports including balance sheet, income statement, cash flow, and budget comparison. Submit to committee and auditor.',
            'category': 'financial',
            'status': 'pending',
            'due_date': eofy_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'high',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'WHS Risk Assessment Review',
            'description': 'Review and update Workplace Health & Safety risk assessment. Identify hazards in common areas, assess risks, implement controls, and document findings.',
            'category': 'safety',
            'status': 'pending',
            'due_date': whs_due,
            'completed_date': None,
            'assigned_to': None,
            'priority': 'medium',
            'recurrence': 'annual',
            'notes': None,
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Strata Register Update',
            'description': 'Update strata register with any ownership changes, lot entitlements, and contact details. Ensure compliance with Strata Schemes Management Act.',
            'category': 'regulatory',
            'status': 'completed',
            'due_date': (now - timedelta(days=30)).isoformat(),
            'completed_date': (now - timedelta(days=15)).isoformat(),
            'assigned_to': None,
            'priority': 'medium',
            'recurrence': 'quarterly',
            'notes': 'Updated with 3 new owner changes',
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Quarterly Levy Collection Report',
            'description': 'Prepare quarterly levy collection report showing total levies raised, collected, and outstanding. Identify units in arrears and initiate follow-up actions.',
            'category': 'financial',
            'status': 'completed',
            'due_date': (now - timedelta(days=60)).isoformat(),
            'completed_date': (now - timedelta(days=45)).isoformat(),
            'assigned_to': None,
            'priority': 'high',
            'recurrence': 'quarterly',
            'notes': '95.42% collection rate achieved',
            'created_by': 'system',
            'created_at': today,
            'updated_at': today
        }
    ]

    return items


if __name__ == "__main__":
    # For testing purposes
    import asyncio
    from pymongo import AsyncMongoClient
    import os
    from dotenv import load_dotenv

    load_dotenv()


    async def seed():
        """Generated function header.

        Function: seed
        Path: backend/seeds/compliance_items.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        client = AsyncMongoClient(os.getenv('MONGO_URL'))
        db = client[os.getenv('DB_NAME')]

        items = get_compliance_items_seed_data()

        # Clear existing items
        await db.compliance_items.delete_many({})

        # Insert new items
        if items:
            await db.compliance_items.insert_many(items)

        count = await db.compliance_items.count_documents({})
        print(f"✅ Seeded {count} compliance items")

        client.close()


    asyncio.run(seed())
