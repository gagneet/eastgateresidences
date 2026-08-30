#!/usr/bin/env python3
"""
Initialize default Document folders and Chat groups as requested.
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

MONGO_URL = os.getenv('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.getenv('DB_NAME', 'strata_production')


async def initialize_defaults():
    # Try 27017 first, then 27018
    """Generated function header.

    Function: initialize_defaults
    Path: backend/seeds/initialize_defaults.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        client = AsyncMongoClient(MONGO_URL, serverSelectionTimeoutMS=2000)
        await client.admin.command('ping')
    except Exception as e:
        print(f"Warning: Failed to connect to {MONGO_URL}, trying fallback port: {e}")
        fallback_url = MONGO_URL.replace(":27017", ":27018")
        try:
            client = AsyncMongoClient(fallback_url, serverSelectionTimeoutMS=2000)
            await client.admin.command('ping')
        except Exception as fallback_error:
            print(f"Error: Failed to connect to database: {fallback_error}")
            sys.exit(1)

    db = client[DB_NAME]

    print(f"Connected to {DB_NAME}")

    # Get admin user for created_by
    admin = await db.users.find_one({"role": "super_admin"})
    if not admin:
        print("Error: No admin user found. Run user seeds first.")
        # Fallback to a system ID if no admin
        admin_id = "00000000-0000-0000-0000-000000000000"
        admin_name = "System"
    else:
        admin_id = str(admin["id"])
        admin_name = admin["full_name"]

    now = datetime.now(timezone.utc).isoformat()

    # 1. Initialize Document Folders
    # Requested: General/Public Documents, Executive Committee, Owners, Tenants, Services, Financial
    requested_folders = [
        {"name": "General/Public Documents", "description": "General community documents", "access": "all_members",
         "color": "#22c55e"},
        {"name": "Executive Committee", "description": "EC internal documents", "access": "ec_view",
         "color": "#3b82f6"},
        {"name": "Owners", "description": "Documents for owners", "access": "owners_view", "color": "#eab308"},
        {"name": "Tenants", "description": "Documents for tenants", "access": "all_members", "color": "#f97316"},
        {"name": "Services", "description": "Service provider documents", "access": "ec_view", "color": "#8b5cf6"},
        {"name": "Financial", "description": "Financial reports and budgets", "access": "owners_view",
         "color": "#ef4444"}
    ]

    print("\n📁 Initializing Document Folders...")
    for f_data in requested_folders:
        existing = await db.document_folders.find_one({"name": f_data["name"]})
        if not existing:
            folder_id = str(uuid.uuid4())
            folder_doc = {
                "id": folder_id,
                "name": f_data["name"],
                "parent_folder_id": None,
                "path": f"/{f_data['name']}",
                "description": f_data["description"],
                "permissions": {"access_level": f_data["access"], "allowed_roles": [], "allowed_users": []},
                "color": f_data["color"],
                "is_system": True,
                "created_by": admin_id,
                "created_by_name": admin_name,
                "created_at": now,
                "updated_at": now
            }
            await db.document_folders.insert_one(folder_doc)
            print(f"  ✓ Created folder: {f_data['name']}")
        else:
            print(f"  ⊘ Folder already exists: {f_data['name']}")

    # 2. Initialize Chat Groups
    # Requested: Apartments, Townhouses, General Chat, Executive Committee, Service Requests
    requested_chat_groups = [
        {
            "name": "General Chat",
            "description": "Community-wide discussions",
            "type": "general",
            "criteria": {"type": "all_approved"}
        },
        {
            "name": "Apartments",
            "description": "Discussion for apartment residents",
            "type": "unit_based",
            "criteria": {"type": "unit_type", "unit_pattern": "^[0-9]+[A-Z]*$"}
        },
        {
            "name": "Townhouses",
            "description": "Discussion for townhouse residents",
            "type": "unit_based",
            "criteria": {"type": "unit_type", "unit_pattern": "^T[0-9]+$"}
        },
        {
            "name": "Executive Committee",
            "description": "Private group for EC members",
            "type": "role_based",
            "criteria": {"type": "role", "roles": ["super_admin", "chairman", "strata_admin", "ec_member"]}
        },
        {
            "name": "Service Requests",
            "description": "Group for maintenance and service tracking",
            "type": "role_based",
            "criteria": {"type": "role",
                         "roles": ["super_admin", "chairman", "strata_admin", "strata_manager", "service_provider"]}
        }
    ]

    print("\n💬 Initializing Chat Groups...")
    for g_data in requested_chat_groups:
        existing = await db.chat_groups.find_one({"name": g_data["name"]})
        if not existing:
            group_doc = {
                "id": str(uuid.uuid4()),
                "name": g_data["name"],
                "description": g_data["description"],
                "type": g_data["type"],
                "is_system": True,
                "created_by": admin_id,
                "created_at": now,
                "updated_at": now,
                "members": [],
                "settings": {
                    "auto_join_criteria": g_data["criteria"],
                    "who_can_post": ["all"] if g_data["name"] == "General Chat" else ["member"],
                    "who_can_delete": ["admin", "chairman", "strata_admin"],
                    "notifications_enabled": True
                },
                "message_count": 0,
                "last_message_at": None
            }
            await db.chat_groups.insert_one(group_doc)
            print(f"  ✓ Created chat group: {g_data['name']}")
        else:
            print(f"  ⊘ Chat group already exists: {g_data['name']}")

    client.close()
    print("\n✅ Initialization complete.")


if __name__ == "__main__":
    asyncio.run(initialize_defaults())
