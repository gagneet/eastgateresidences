import asyncio
from pymongo import AsyncMongoClient


async def seed():
    """Generated function header.

    Function: seed
    Path: backend/seeds/seed_data.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient("mongodb://localhost:27017")
    db = client["eastgate"]

    # 1. Create default building
    await db.buildings.update_one(
        {"id": "13195"},
        {"$set": {
            "id": "13195",
            "name": "East Gate Residences",
            "address": "14 Hoolihan Street, Denman Prospect ACT 2611",
            "plan_number": "13195",
            "is_active": True
        }},
        upsert=True
    )

    # 2. Create another building
    await db.buildings.update_one(
        {"id": "16244"},
        {"$set": {
            "id": "16244",
            "name": "Sierra Gungahlin",
            "address": "70 Efkarpidis Street, Gungahlin ACT 2912",
            "plan_number": "16244",
            "is_active": True
        }},
        upsert=True
    )

    # 3. Create building settings for Sierra
    await db.settings.update_one(
        {"building_id": "16244"},
        {"$set": {
            "building_id": "16244",
            "building_name": "Sierra Gungahlin",
            "building_address": "70 Efkarpidis Street, Gungahlin ACT 2912",
            "building_description": "Luxury living in the heart of Gungahlin.",
            "hero_image": "https://example.com/sierra.jpg"
        }},
        upsert=True
    )

    print("Seed data inserted successfully")


if __name__ == "__main__":
    asyncio.run(seed())
    # Harbourview (18932) was removed on 2026-08-20 — a synthetic seed building with
    # no users, payments or documents. Its creation blocks were deleted here so a
    # re-seed cannot resurrect it. Sierra (16244) above is deliberately retained.

