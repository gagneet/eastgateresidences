"""
Emergency services — police, fire, ambulance, utilities, building contacts.

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

EMERGENCY_SERVICES = [
    {'id': 'fb7722d8-f647-4f3b-a157-0f868c9710bf', 'name': 'Lift Emergency – Electra Lift Co', 'category': 'building',
     'phone': '1300 725 290',
     'description': 'Call if trapped in lift. Manufacturer: Brilliant Lifts. Fire Brigade: 000.', 'is_24_7': True,
     'is_private': False, 'order': 40, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': 'b6500d0b-1b92-4d20-9f69-d6c2c7c99a51', 'name': 'Glass Repair – Capital Glass', 'category': 'building',
     'phone': '0409 070 224', 'is_24_7': False, 'is_private': False, 'order': 100,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '56ccdfcd-0fd8-40ab-af97-9e3f7055abe3', 'name': 'Glass Repair – EW Glass', 'category': 'building',
     'phone': '6280 5091', 'is_24_7': False, 'is_private': False, 'order': 101,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '1b690eb0-cdc3-44a0-9de0-fd716360d521', 'name': 'Painting – Lee Madden', 'category': 'contractor',
     'phone': '0400 913 440', 'is_24_7': False, 'is_private': False, 'order': 80,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '6261c9d8-1951-4820-922a-ce83ad6064c8', 'name': 'Painting – Joseph Corradi', 'category': 'contractor',
     'phone': '0451 991 986', 'is_24_7': False, 'is_private': False, 'order': 81,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '43e7c141-360c-46ae-bc95-9d665fe9203e', 'name': 'General Services – Lee Madden', 'category': 'contractor',
     'phone': '0400 913 440', 'description': 'Electrical, painting and general building maintenance.', 'is_24_7': False,
     'is_private': False, 'order': 110, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '65b7cd9c-b4dd-4370-b76b-ec75f88a5ac6', 'name': 'Fire / Police / Ambulance', 'category': 'fire',
     'phone': '000', 'description': 'Always dial Triple Zero in a life-threatening emergency.', 'is_24_7': True,
     'is_private': False, 'order': 1, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '86c94a59-25ee-4ef6-8ec3-c18566f2dc4c', 'name': 'Fire System Maintenance – 360 Degree Fire',
     'category': 'fire', 'phone': '02 6299 0006',
     'description': 'Fire alarm testing: every 2nd Thursday of the month, 9:00 AM.',
     'address': '90 High Street, Queanbeyan NSW 2620', 'is_24_7': False, 'is_private': False, 'order': 30,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '135960ea-27f0-4c6d-8dba-52107b1a8458', 'name': 'Civium Strata Manager – Jessica Minichiello',
     'category': 'management', 'phone': '1300 724 256',
     'description': 'Strata plan UP13195. Email: UP13195@civium.com.au', 'is_24_7': False, 'is_private': True,
     'order': 10, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '0649b6d0-0c5c-44ed-9071-020c9263fd2c', 'name': 'Civium After Hours Emergency', 'category': 'management',
     'phone': '1300 724 256', 'description': 'After-hours building emergencies. Additional charges may apply.',
     'is_24_7': True, 'is_private': True, 'order': 11, 'created_at': '2026-03-08T14:25:01.503037+00:00',
     'building_id': '13195'},
    {'id': '91dd4a11-f912-43fb-b231-5225a26e9754', 'name': 'Civium Maintenance (Business Hours)',
     'category': 'management', 'phone': '1300 724 256',
     'description': 'Non-urgent maintenance during business hours. Email: UP13195@civium.com.au', 'is_24_7': False,
     'is_private': True, 'order': 12, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '27575d8a-c0ce-4e1f-bad8-d44b60b8b47b', 'name': 'East Gate Owners Corporation', 'category': 'management',
     'phone': '0412 017 126', 'description': 'Chair: Anthony McDonald. Email: eastgatedenman13195@gmail.com',
     'is_24_7': False, 'is_private': True, 'order': 20, 'created_at': '2026-03-08T14:25:01.503037+00:00',
     'building_id': '13195'},
    {'id': '581531bf-3e15-4304-ada2-65afe8f2cb4f', 'name': 'Non-Emergency Police', 'category': 'police',
     'phone': '131 444', 'description': 'Police assistance for non-life-threatening situations.', 'is_24_7': True,
     'is_private': False, 'order': 2, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '6bee6c2d-7c55-4f20-bf0b-ae8de3c0f915', 'name': 'Security & CCTV – CXI', 'category': 'police',
     'phone': '1300 798 325', 'description': 'Alarm & CCTV monitoring and maintenance.', 'is_24_7': True,
     'is_private': False, 'order': 50, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '3d13c245-73f0-4ce8-8075-f31691f3c020', 'name': 'Electrician – GMH Electrical', 'category': 'utility',
     'phone': '0418 623 046', 'is_24_7': False, 'is_private': False, 'order': 60,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '1ea26ea2-48c3-4d6d-a87b-3a7b70337ac4', 'name': 'Electrician – Lee Madden', 'category': 'utility',
     'phone': '0400 913 440', 'is_24_7': False, 'is_private': False, 'order': 61,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '9cc5e384-8cbc-40bc-9173-ab05023fe53a', 'name': 'Plumbing – Level Plumbing', 'category': 'utility',
     'phone': '6185 0341', 'is_24_7': False, 'is_private': False, 'order': 62,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '227adf59-68eb-4f11-ab20-2aa7343969c9', 'name': "Plumbing – Jim's Plumbing", 'category': 'utility',
     'phone': '13 15 46', 'is_24_7': True, 'is_private': False, 'order': 63,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': 'f87a7c51-c356-41e9-9944-6e7c530c457c', 'name': 'Locksmith 24/7 – Canberra Locksmiths',
     'category': 'utility', 'phone': '6285 3544', 'is_24_7': True, 'is_private': False, 'order': 70,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': 'd7d59203-65dc-4f23-bd24-bf0d3edd4b69', 'name': 'Locksmith 24/7 – ACT Mobile Locksmith',
     'category': 'utility', 'phone': '1800 167 420', 'is_24_7': True, 'is_private': False, 'order': 71,
     'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': 'eb0a52e0-6e4f-4af8-85b4-6a37a54102c2', 'name': 'Hot Water System – Stiebel Eltron', 'category': 'utility',
     'phone': '1800 153 351', 'description': 'Heat pump hot water system support.', 'is_24_7': False,
     'is_private': False, 'order': 90, 'created_at': '2026-03-08T14:25:01.503037+00:00', 'building_id': '13195'},
    {'id': '10e93815-ef73-4b3e-8037-b2391d4843c0', 'name': 'Gungahlin Emergency Electrician', 'category': 'Electrical',
     'phone': '+61 2 6123 4000', 'email': 'electric@gungahlin.com.au',
     'address': '5 Hibberson Street, Gungahlin ACT 2912',
     'description': '24/7 emergency electrical services for Sierra residents.', 'is_emergency': True,
     'is_private': False, 'order': 1, 'building_id': '16244', 'created_at': '2026-03-29T23:22:00.981575+00:00'},
    {'id': '1939c8fc-9a42-4de5-a206-6f7f72b24884', 'name': 'Sierra Security & Locksmith', 'category': 'Locksmith',
     'phone': '+61 412 400 001', 'email': 'security@sierra-gungahlin.com.au',
     'address': '70 Efkarpidis Street, Gungahlin ACT 2912',
     'description': 'On-site security and locksmith for Sierra residents.', 'is_emergency': True, 'is_private': False,
     'order': 3, 'building_id': '16244', 'created_at': '2026-03-29T23:22:00.981575+00:00'},
    {'id': '24247a62-13a1-4b71-8326-96c83195588e', 'name': 'Gungahlin Plumbing Services', 'category': 'Plumbing',
     'phone': '+61 2 6123 4001', 'email': 'plumbing@gungahlin.com.au',
     'address': '12 Anthony Rolfe Avenue, Gungahlin ACT 2912',
     'description': 'Licensed plumber covering Sierra complex and surrounds.', 'is_emergency': True,
     'is_private': False, 'order': 2, 'building_id': '16244', 'created_at': '2026-03-29T23:22:00.981575+00:00'},
    {'id': 'f9659298-c8fe-4447-8993-e40ca4a0f031', 'name': 'Pyrmont Emergency Electrician', 'category': 'Electrical',
     'phone': '+61 2 9552 1000', 'email': 'electric@pyrmont-services.com.au',
     'address': '18 Harris Street, Pyrmont NSW 2009',
     'description': '24/7 licensed electrical services for Harbourview residents.', 'is_emergency': True,
     'is_private': False, 'order': 1, 'building_id': '18932', 'created_at': '2026-03-29T23:22:03.716119+00:00'},
    {'id': 'd5ff039c-d644-4cfe-b0e1-70f1fa9c0518', 'name': 'Pyrmont Plumbing & Gas', 'category': 'Plumbing',
     'phone': '+61 2 9552 1001', 'email': 'plumbing@pyrmont-services.com.au',
     'address': '22 Miller Street, Pyrmont NSW 2009',
     'description': 'Licensed plumber and gas fitter for Harbourview and surrounds.', 'is_emergency': True,
     'is_private': False, 'order': 2, 'building_id': '18932', 'created_at': '2026-03-29T23:22:03.716119+00:00'},
    {'id': '53484309-7189-44c8-a4a1-112449fa6dbf', 'name': 'Harbourview Concierge / Security', 'category': 'Security',
     'phone': '+61 2 9552 1002', 'email': 'security@harbourview.com.au', 'address': '12 Marina Drive, Pyrmont NSW 2009',
     'description': 'On-site concierge and security desk. Available 24/7.', 'is_emergency': False, 'is_private': False,
     'order': 3, 'building_id': '18932', 'created_at': '2026-03-29T23:22:03.716119+00:00'}]


async def seed_emergency_services():
    """Upsert all emergency_services entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in EMERGENCY_SERVICES:
        result = await db.emergency_services.update_one(
            {'id': entry['id']},
            {'$set': entry, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'emergency_services: {upserted} upserted ({len(EMERGENCY_SERVICES)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_emergency_services())
