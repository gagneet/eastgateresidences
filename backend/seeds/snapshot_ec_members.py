"""
Executive Committee members — roles, contacts, and positions.

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

# Removed FY2025 EC members (no longer serving as of FY2026):
#   - Daniel Smart (Committee Member) — id: 7170c77a-8de0-46a2-ae83-2d6180a3ecd1
#   - Kimberley Ruth Swords (Secretary) — id: 39c34489-5907-427e-958b-00fbfbe21d12

EC_MEMBERS = [{'id': '2ff50b6c-c3d8-4b05-8112-0a4ae65dcafe', 'name': 'Anthony McDonald', 'position': 'Chairman',
               'email': 'anthony@eastgateresidences.com.au', 'phone': '+61 412 345 678',
               'bio': 'Chairman of the Executive Committee.', 'image': None, 'order': 1,
               'created_at': '2026-02-14T13:22:26.404728+00:00', 'building_id': '13195'},
              {'id': '464994b4-2473-4672-addf-45e0bdb5c596', 'name': 'Gagneet Singh', 'position': 'Secretary',
               'email': 'gagneet@eastgateresidences.com.au', 'phone': '+61 452 062 644',
               'bio': 'Secretary of the Executive Committee.', 'image': '', 'order': 2,
               'created_at': '2026-02-14T13:22:26.404783+00:00', 'building_id': '13195'},
              {'id': '2542d2ad-7784-40f4-8e81-94e7bc0e2fe8', 'name': 'Marcelo Ramos da Silva', 'position': 'Treasurer',
               'email': 'marcelo.dasilva@eastgateresidences.com.au', 'phone': '+61 431 446 568',
               'bio': 'Treasurer of the Executive Committee.', 'image': '', 'order': 3,
               'created_at': '2026-02-14T13:22:26.404763+00:00', 'building_id': '13195'},
              {'id': '57008fee-e167-426a-acdc-672effbee46c', 'name': 'Brenda Thompson', 'position': 'Commitee Member',
               'email': 'brenda.thompson@eastgateresidences.com.au', 'phone': '+61 434 567 890',
               'bio': 'Committee Member of the Executive Committee.', 'image': '', 'order': 4,
               'created_at': '2026-02-14T13:22:26.404752+00:00', 'building_id': '13195'},
              {'id': 'df61f9e9-44c4-49fb-b489-b9eb2ded118d', 'name': 'Keith Edward Dears',
               'position': 'Committee Member', 'email': 'keith.dears@eastgateresidences.com.au',
               'phone': '+61 445 678 901', 'bio': 'Executive Committee Member.', 'image': '', 'order': 5,
               'created_at': '2026-02-14T13:22:26.404773+00:00', 'building_id': '13195'},
              {'id': '6499b4cd-d895-41c0-8e18-28d90014e735', 'name': 'Jessica Minichiello',
               'position': 'Strata Manager - Civium', 'email': 'jessica@civium.com.au', 'phone': '+61 2 6123 4568',
               'bio': 'Civium Property Group representative and strata manager for East Gate Residences.',
               'image': None, 'order': 6, 'created_at': '2026-02-14T13:22:26.404792+00:00', 'building_id': '13195'},
              {'id': '5864b170-817c-4fd5-b3b0-e0d47d07a58e', 'name': 'David Park', 'position': 'Treasurer',
               'email': 'david.park@sierra.com.au', 'phone': '+61 412 001 003', 'unit_number': 'S003',
               'term_start': '2024-07-01', 'term_end': '2026-06-30',
               'bio': 'Treasurer responsible for Sierra financial management and levy collections.',
               'building_id': '16244', 'created_at': '2026-03-29T23:22:00.981575+00:00'},
              {'id': '476e7767-6294-40f1-86fd-d93ac72d9213', 'name': 'James Chen', 'position': 'Chairperson',
               'email': 'james.chen@sierra.com.au', 'phone': '+61 412 001 001', 'unit_number': 'S001',
               'term_start': '2024-07-01', 'term_end': '2026-06-30',
               'bio': 'Chairperson of Sierra Owners Corporation. Passionate about sustainable building management.',
               'building_id': '16244', 'created_at': '2026-03-29T23:22:00.981575+00:00'},
              {'id': '97decc4c-40a2-4c63-bf40-4723b585730d', 'name': 'Lisa Wang', 'position': 'Secretary',
               'email': 'lisa.wang@sierra.com.au', 'phone': '+61 412 001 002', 'unit_number': 'S002',
               'term_start': '2024-07-01', 'term_end': '2026-06-30',
               'bio': 'Secretary of Sierra EC. Handles all official correspondence and minutes.',
               'building_id': '16244', 'created_at': '2026-03-29T23:22:00.981575+00:00'},
              {'id': '2c9abf83-dd08-41f5-84de-c51de36a350f', 'name': 'Helen Morris', 'position': 'Chairperson',
               'email': 'helen.morris@harbourview.com.au', 'phone': '+61 412 002 001', 'unit_number': 'H001',
               'term_start': '2024-07-01', 'term_end': '2026-06-30',
               'bio': 'Chairperson of Harbourview Residences OC. Brings extensive property management experience.',
               'building_id': '18932', 'created_at': '2026-03-29T23:22:03.716119+00:00'},
              {'id': '12e7d639-f13e-4b0e-bd5b-ec25498cf2c1', 'name': 'Peter Nguyen', 'position': 'Secretary/Treasurer',
               'email': 'peter.nguyen@harbourview.com.au', 'phone': '+61 412 002 002', 'unit_number': 'H002',
               'term_start': '2024-07-01', 'term_end': '2026-06-30',
               'bio': 'Secretary/Treasurer managing Harbourview finances and official correspondence.',
               'building_id': '18932', 'created_at': '2026-03-29T23:22:03.716119+00:00'}]


async def seed_ec_members():
    """Upsert all ec_members entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in EC_MEMBERS:
        result = await db.ec_members.update_one(
            {'id': entry['id']},
            {'$set': entry, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'ec_members: {upserted} upserted ({len(EC_MEMBERS)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_ec_members())
