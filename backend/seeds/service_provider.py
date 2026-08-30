#!/usr/bin/env python3
"""
Seed a test service provider user linked to a contractor
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncio
from pymongo import AsyncMongoClient
from passlib.context import CryptContext

# Setup path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Load environment
from dotenv import load_dotenv

def _seed_password() -> str:
    """Password for seeded fixture accounts, from SEED_TEST_USER_PASSWORD.

    No default: a default is what turns "seeded once" into "the production password",
    which is what happened to the super-admin account (GAP-SEC-013).
    """
    pw = os.environ.get("SEED_TEST_USER_PASSWORD")
    if not pw:
        raise SystemExit("SEED_TEST_USER_PASSWORD is not set. This seed will not invent a password.")
    return pw



load_dotenv(backend_dir / '.env')

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MONGO_URL = os.getenv('MONGO_URL', 'mongodb://localhost:27018')
DB_NAME = os.getenv('DB_NAME', 'strata_production')


async def seed_service_provider():
    """Generated function header.

    Function: seed_service_provider
    Path: backend/seeds/service_provider.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]

    print(f"Connected to {DB_NAME}")

    # Check if service provider user already exists
    existing = await db.users.find_one({"email": "service@eastgate.com"})
    if existing:
        print("✓ Service provider user already exists")
        service_provider_id = existing["id"]
    else:
        # Create service provider user
        service_provider_id = str(uuid.uuid4())
        hashed_password = pwd_context.hash(_seed_password())

        service_provider = {
            "id": service_provider_id,
            "email": "service@eastgate.com",
            "password": hashed_password,
            "full_name": "Test Service Provider",
            "role": "service_provider",
            "phone": "0400 123 456",
            "unit_number": None,  # Service providers don't have units
            "is_approved": True,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "permissions": {
                "can_view_documents": True,
                "can_upload_documents": True,
                "can_view_finances": False,
                "can_manage_finances": False,
                "can_create_listings": False,
                "can_chat": True,
                "can_view_meetings": False,
                "can_manage_meetings": False,
                "can_manage_users": False,
                "can_post_announcements": False,
                "can_view_schedule": True,
                "can_manage_maintenance": False
            }
        }

        await db.users.insert_one(service_provider)
        print(f"✓ Created service provider user: service@eastgate.com / $SEED_TEST_USER_PASSWORD")

    # Check if contractor exists for this user
    existing_contractor = await db.contractors.find_one({"email": "service@eastgate.com"})

    if existing_contractor:
        print("✓ Contractor record already exists")
        contractor_id = existing_contractor["id"]
    else:
        # Create contractor record linked to service provider
        contractor_id = str(uuid.uuid4())
        contractor = {
            "id": contractor_id,
            "name": "Test Service Provider Company",
            "name": "Test Service Provider Company [TEST DATA]",
            "abn": "12345678901",  # TEST DATA ONLY - Not a real ABN
            "email": "service@eastgate.com",
            "address": "123 Service St, Canberra ACT 2600",  # TEST DATA ONLY
            "specialty": "general",
            "services": ["general", "plumbing", "electrical", "hvac"],
            "notes": "Test contractor for development and testing",
            "total_jobs": 0,
            "total_spent": 0.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True
        }

        await db.contractors.insert_one(contractor)
        print(f"✓ Created contractor record: {contractor['name']}")

    # Link contractor ID to user record
    await db.users.update_one(
        {"id": service_provider_id},
        {"$set": {"contractor_id": contractor_id}}
    )
    print(f"✓ Linked contractor to user")

    print("\n" + "=" * 60)
    print("SERVICE PROVIDER TEST ACCOUNT CREATED")
    print("=" * 60)
    print(f"Email: service@eastgate.com")
    print(f"Password: $SEED_TEST_USER_PASSWORD")
    print(f"Role: service_provider")
    print(f"Contractor ID: {contractor_id}")
    print(f"\nYou can now:")
    print("1. Login with these credentials")
    print("2. View assigned maintenance requests in ServiceProviderDashboard")
    print("3. Admin can assign maintenance requests to this contractor")
    print("=" * 60)

    client.close()


if __name__ == "__main__":
    asyncio.run(seed_service_provider())
