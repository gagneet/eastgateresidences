"""
Document folders — access-controlled folder hierarchy.

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

DOCUMENT_FOLDERS = [
    {'id': '0a1aaa82-dea0-465c-992a-194145692478', 'name': 'Executive Committee', 'parent_folder_id': None,
     'path': '/Executive Committee', 'description': 'EC internal documents',
     'permissions': {'access_level': 'ec_view', 'allowed_roles': [], 'allowed_users': []}, 'color': '#3b82f6',
     'is_system': True, 'created_by': 'f26f3caf-710f-4a61-84b1-26d8bcac7042', 'created_by_name': 'System Administrator',
     'created_at': '2026-02-15T11:24:14.721385+00:00', 'updated_at': '2026-02-15T11:24:14.721385+00:00',
     'building_id': '13195'},
    {'id': 'b4acca43-e487-4be2-88df-33b05f294d3b', 'name': 'Financial', 'parent_folder_id': None, 'path': '/Financial',
     'description': 'Financial reports and budgets',
     'permissions': {'access_level': 'owners_view', 'allowed_roles': [], 'allowed_users': []}, 'color': '#ef4444',
     'is_system': True, 'created_by': 'f26f3caf-710f-4a61-84b1-26d8bcac7042', 'created_by_name': 'System Administrator',
     'created_at': '2026-02-15T11:24:14.721385+00:00', 'updated_at': '2026-02-15T11:24:14.721385+00:00',
     'building_id': '13195'},
    {'id': '2bd6ff27-c3e4-4efe-9d71-7b2464a68b51', 'name': 'General/Public Documents', 'parent_folder_id': None,
     'path': '/General/Public Documents', 'description': 'General community documents',
     'permissions': {'access_level': 'all_members', 'allowed_roles': [], 'allowed_users': []}, 'color': '#22c55e',
     'is_system': True, 'created_by': 'f26f3caf-710f-4a61-84b1-26d8bcac7042', 'created_by_name': 'System Administrator',
     'created_at': '2026-02-15T11:24:14.721385+00:00', 'updated_at': '2026-02-15T11:24:14.721385+00:00',
     'building_id': '13195'},
    {'id': '7744e78f-3833-4a37-9750-8a7decc35202', 'name': 'Owners', 'parent_folder_id': None, 'path': '/Owners',
     'description': 'Documents for owners',
     'permissions': {'access_level': 'owners_view', 'allowed_roles': [], 'allowed_users': []}, 'color': '#eab308',
     'is_system': True, 'created_by': 'f26f3caf-710f-4a61-84b1-26d8bcac7042', 'created_by_name': 'System Administrator',
     'created_at': '2026-02-15T11:24:14.721385+00:00', 'updated_at': '2026-02-15T11:24:14.721385+00:00',
     'building_id': '13195'},
    {'id': '86f5a183-6bb4-41f4-bbc4-4f0f23b1b162', 'name': 'Services', 'parent_folder_id': None, 'path': '/Services',
     'description': 'Service provider documents',
     'permissions': {'access_level': 'ec_view', 'allowed_roles': [], 'allowed_users': []}, 'color': '#8b5cf6',
     'is_system': True, 'created_by': 'f26f3caf-710f-4a61-84b1-26d8bcac7042', 'created_by_name': 'System Administrator',
     'created_at': '2026-02-15T11:24:14.721385+00:00', 'updated_at': '2026-02-15T11:24:14.721385+00:00',
     'building_id': '13195'},
    {'id': '77e4c04b-12f1-49f2-9b2a-1adb66c2bbd5', 'name': 'Tenants', 'parent_folder_id': None, 'path': '/Tenants',
     'description': 'Documents for tenants',
     'permissions': {'access_level': 'all_members', 'allowed_roles': [], 'allowed_users': []}, 'color': '#f97316',
     'is_system': True, 'created_by': 'f26f3caf-710f-4a61-84b1-26d8bcac7042', 'created_by_name': 'System Administrator',
     'created_at': '2026-02-15T11:24:14.721385+00:00', 'updated_at': '2026-02-15T11:24:14.721385+00:00',
     'building_id': '13195'}]


async def seed_document_folders():
    """Upsert all document_folders entries. Safe to re-run."""
    client = AsyncMongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    upserted = 0
    for entry in DOCUMENT_FOLDERS:
        result = await db.document_folders.update_one(
            {'id': entry['id']},
            {'$set': entry, '$setOnInsert': {'created_at': entry.get('created_at', entry.get('updated_at', ''))}},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            upserted += 1
    print(f'document_folders: {upserted} upserted ({len(DOCUMENT_FOLDERS)} total)')
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_document_folders())
