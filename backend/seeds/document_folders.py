#!/usr/bin/env python3
"""
Seed system document folders for organizing documents
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncio
from pymongo import AsyncMongoClient

# Setup path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Load environment
from dotenv import load_dotenv

load_dotenv(backend_dir / '.env')

MONGO_URL = os.getenv('MONGO_URL', 'mongodb://localhost:27018')
DB_NAME = os.getenv('DB_NAME', 'strata_production')


async def seed_system_folders():
    """Generated function header.

    Function: seed_system_folders
    Path: backend/seeds/document_folders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]

    print(f"Connected to {DB_NAME}")

    # Get admin user for created_by
    admin = await db.users.find_one({"role": "super_admin"})
    if not admin:
        print("Error: No admin user found. Run user seeds first.")
        return

    admin_id = admin["id"]
    admin_name = admin["full_name"]
    now = datetime.now(timezone.utc).isoformat()

    # Check if system folders already exist
    existing_count = await db.document_folders.count_documents({"is_system": True})
    if existing_count > 0:
        print(f"✓ System folders already exist ({existing_count} folders)")
        return

    # Define system folder structure
    folders = [
        # Root level folders
        {
            "id": str(uuid.uuid4()),
            "name": "EC Documents",
            "parent_folder_id": None,
            "path": "/EC Documents",
            "description": "Executive Committee documents and correspondence",
            "permissions": {"access_level": "ec_view", "allowed_roles": [], "allowed_users": []},
            "color": "#3b82f6",  # Blue
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Public Documents",
            "parent_folder_id": None,
            "path": "/Public Documents",
            "description": "Documents accessible to all residents",
            "permissions": {"access_level": "all_members", "allowed_roles": [], "allowed_users": []},
            "color": "#22c55e",  # Green
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Meeting Minutes",
            "parent_folder_id": None,
            "path": "/Meeting Minutes",
            "description": "AGM and committee meeting minutes",
            "permissions": {"access_level": "all_members", "allowed_roles": [], "allowed_users": []},
            "color": "#eab308",  # Yellow
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Financial Reports",
            "parent_folder_id": None,
            "path": "/Financial Reports",
            "description": "Annual financial statements and audit reports",
            "permissions": {"access_level": "owners_view", "allowed_roles": [], "allowed_users": []},
            "color": "#f59e0b",  # Orange
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Contractor Quotes",
            "parent_folder_id": None,
            "path": "/Contractor Quotes",
            "description": "Quotes and proposals from service providers",
            "permissions": {"access_level": "ec_view", "allowed_roles": [], "allowed_users": []},
            "color": "#8b5cf6",  # Purple
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Insurance Documents",
            "parent_folder_id": None,
            "path": "/Insurance Documents",
            "description": "Insurance policies and claims",
            "permissions": {"access_level": "ec_view", "allowed_roles": [], "allowed_users": []},
            "color": "#06b6d4",  # Cyan
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
    ]

    # Insert root folders first
    await db.document_folders.insert_many(folders)
    root_folders = {f["name"]: f["id"] for f in folders}

    print(f"✓ Created {len(folders)} root-level system folders")

    # Create year-based subfolders for some categories
    current_year = datetime.now().year
    financial_year = f"{current_year}-{current_year + 1}"

    subfolders = [
        # EC Documents subfolders
        {
            "id": str(uuid.uuid4()),
            "name": financial_year,
            "parent_folder_id": root_folders["EC Documents"],
            "path": f"/EC Documents/{financial_year}",
            "description": f"EC documents for financial year {financial_year}",
            "permissions": {"access_level": "ec_view", "allowed_roles": [], "allowed_users": []},
            "color": None,
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        # Meeting Minutes subfolders
        {
            "id": str(uuid.uuid4()),
            "name": str(current_year),
            "parent_folder_id": root_folders["Meeting Minutes"],
            "path": f"/Meeting Minutes/{current_year}",
            "description": f"Meeting minutes for {current_year}",
            "permissions": {"access_level": "all_members", "allowed_roles": [], "allowed_users": []},
            "color": None,
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        # Financial Reports subfolders
        {
            "id": str(uuid.uuid4()),
            "name": financial_year,
            "parent_folder_id": root_folders["Financial Reports"],
            "path": f"/Financial Reports/{financial_year}",
            "description": f"Financial reports for {financial_year}",
            "permissions": {"access_level": "owners_view", "allowed_roles": [], "allowed_users": []},
            "color": None,
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        # Public Documents subfolders
        {
            "id": str(uuid.uuid4()),
            "name": "Rules & Bylaws",
            "parent_folder_id": root_folders["Public Documents"],
            "path": "/Public Documents/Rules & Bylaws",
            "description": "Strata bylaws and community rules",
            "permissions": {"access_level": "all_members", "allowed_roles": [], "allowed_users": []},
            "color": None,
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Community Information",
            "parent_folder_id": root_folders["Public Documents"],
            "path": "/Public Documents/Community Information",
            "description": "General information for residents",
            "permissions": {"access_level": "all_members", "allowed_roles": [], "allowed_users": []},
            "color": None,
            "is_system": True,
            "created_by": admin_id,
            "created_by_name": admin_name,
            "created_at": now,
            "updated_at": now
        },
    ]

    await db.document_folders.insert_many(subfolders)

    print(f"✓ Created {len(subfolders)} subfolders")
    print("\n" + "=" * 60)
    print("SYSTEM FOLDERS CREATED")
    print("=" * 60)
    print(f"Total folders: {len(folders) + len(subfolders)}")
    print("\nFolder Structure:")
    print("📁 EC Documents")
    print(f"  📁 {financial_year}")
    print("📁 Public Documents")
    print("  📁 Rules & Bylaws")
    print("  📁 Community Information")
    print("📁 Meeting Minutes")
    print(f"  📁 {current_year}")
    print("📁 Financial Reports")
    print(f"  📁 {financial_year}")
    print("📁 Contractor Quotes")
    print("📁 Insurance Documents")
    print("\nUsers can now organize documents in these folders via the Documents page.")
    print("=" * 60)

    client.close()


if __name__ == "__main__":
    asyncio.run(seed_system_folders())
