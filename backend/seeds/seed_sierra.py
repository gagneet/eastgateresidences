"""
Seed script for Sierra (building_id: "16244") — multi-tenant demo data.

Creates:
  - Building record (upsert)
  - Building settings
  - 10 users: 1 Chairman, 2 EC members, 1 Strata Manager (manager@eastgate.com), 6 owners
  - Memberships for all users in building 16244
  - Units (S001–S035)
  - EC member records
  - Emergency service contacts
  - Sample blog posts (scope: building)
  - Sample marketplace listings
  - Feature toggles

Run standalone:
  python3 -m seeds.seed_sierra
Or via seed_all.py
"""

import os
import uuid
from datetime import datetime, timezone

import asyncio
import bcrypt

def _seed_password() -> str:
    """Password for seeded fixture accounts, from SEED_TEST_USER_PASSWORD.

    No default: a default is what turns "seeded once" into "the production password",
    which is what happened to the super-admin account (GAP-SEC-013).
    """
    pw = os.environ.get("SEED_TEST_USER_PASSWORD")
    if not pw:
        raise SystemExit("SEED_TEST_USER_PASSWORD is not set. This seed will not invent a password.")
    return pw



try:
    from database import db
except ImportError:
    db = None

BUILDING_ID = "16244"
NOW = datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generated function header.

    Function: _new_id
    Path: backend/seeds/seed_sierra.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid4())


def _hash(password: str) -> str:
    """Generated function header.

    Function: _hash
    Path: backend/seeds/seed_sierra.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ── Users ─────────────────────────────────────────────────────────────────────

SIERRA_USERS = [
    {
        "id": _new_id(),
        "email": "james.chen@sierra.com.au",
        "password_hash": _hash("SierraChair123!"),
        "full_name": "James Chen",
        "role": "strata_admin",
        "ec_position": "CHAIRMAN",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S001",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 001",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "lisa.wang@sierra.com.au",
        # Filled at seed time, like the other seeded account in this list. The
        # literal here was mangled by a regex sweep before it was env-ified.
        "password_hash": None,
        "full_name": "Lisa Wang",
        "role": "ec_member",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S002",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 002",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "david.park@sierra.com.au",
        "password_hash": _hash("SierraEC2123!"),
        "full_name": "David Park",
        "role": "ec_member",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S003",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 003",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "manager@eastgate.com",
        # Filled at seed time from SEED_TEST_USER_PASSWORD. Left None here so that
        # IMPORTING this module never needs a credential — only seeding does. An
        # earlier revision called _seed_password() inside this literal, which made
        # `import seeds.seed_sierra` raise SystemExit.
        "password_hash": None,
        "full_name": "Building Manager",
        "role": "strata_manager",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": None,
        # Cross-building user: primary building is East Gate. Sierra access is granted
        # via the membership record created below, NOT via this building_id field.
        # Using "13195" here ensures a fresh install does not misplace this user.
        "building_id": "13195",
        "phone": "+61 2 6285 0325",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "sarah.jones@sierra.com.au",
        "password_hash": _hash("SierraOwner1!"),
        "full_name": "Sarah Jones",
        "role": "owner",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S010",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 010",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "mike.taylor@sierra.com.au",
        "password_hash": _hash("SierraOwner2!"),
        "full_name": "Mike Taylor",
        "role": "owner",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S015",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 015",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "emily.white@sierra.com.au",
        "password_hash": _hash("SierraOwner3!"),
        "full_name": "Emily White",
        "role": "owner",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S020",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 020",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "raj.kumar@sierra.com.au",
        "password_hash": _hash("SierraOwner4!"),
        "full_name": "Raj Kumar",
        "role": "owner",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S025",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 025",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "claire.murphy@sierra.com.au",
        "password_hash": _hash("SierraOwner5!"),
        "full_name": "Claire Murphy",
        "role": "owner",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S030",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 030",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
    {
        "id": _new_id(),
        "email": "tony.brown@sierra.com.au",
        "password_hash": _hash("SierraOwner6!"),
        "full_name": "Tony Brown",
        "role": "owner",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "unit_number": "S035",
        "building_id": BUILDING_ID,
        "phone": "+61 412 001 035",
        "profile_image": None,
        "created_at": NOW,
        "custom_permissions": {},
    },
]

# ── Units ─────────────────────────────────────────────────────────────────────

SIERRA_UNITS = [
    {"unit_number": "S001", "lot_number": 1, "unit_type": "apartment", "floor": 1, "bedrooms": 2, "bathrooms": 1,
     "car_spaces": 1, "building_id": BUILDING_ID, "owner_name": "James Chen", "created_at": NOW},
    {"unit_number": "S002", "lot_number": 2, "unit_type": "apartment", "floor": 1, "bedrooms": 2, "bathrooms": 1,
     "car_spaces": 1, "building_id": BUILDING_ID, "owner_name": "Lisa Wang", "created_at": NOW},
    {"unit_number": "S003", "lot_number": 3, "unit_type": "apartment", "floor": 1, "bedrooms": 2, "bathrooms": 2,
     "car_spaces": 1, "building_id": BUILDING_ID, "owner_name": "David Park", "created_at": NOW},
    {"unit_number": "S010", "lot_number": 10, "unit_type": "apartment", "floor": 3, "bedrooms": 3, "bathrooms": 2,
     "car_spaces": 2, "building_id": BUILDING_ID, "owner_name": "Sarah Jones", "created_at": NOW},
    {"unit_number": "S015", "lot_number": 15, "unit_type": "apartment", "floor": 4, "bedrooms": 2, "bathrooms": 1,
     "car_spaces": 1, "building_id": BUILDING_ID, "owner_name": "Mike Taylor", "created_at": NOW},
    {"unit_number": "S020", "lot_number": 20, "unit_type": "apartment", "floor": 5, "bedrooms": 2, "bathrooms": 2,
     "car_spaces": 1, "building_id": BUILDING_ID, "owner_name": "Emily White", "created_at": NOW},
    {"unit_number": "S025", "lot_number": 25, "unit_type": "apartment", "floor": 6, "bedrooms": 3, "bathrooms": 2,
     "car_spaces": 2, "building_id": BUILDING_ID, "owner_name": "Raj Kumar", "created_at": NOW},
    {"unit_number": "S030", "lot_number": 30, "unit_type": "apartment", "floor": 7, "bedrooms": 2, "bathrooms": 1,
     "car_spaces": 1, "building_id": BUILDING_ID, "owner_name": "Claire Murphy", "created_at": NOW},
    {"unit_number": "S035", "lot_number": 35, "unit_type": "apartment", "floor": 8, "bedrooms": 3, "bathrooms": 2,
     "car_spaces": 2, "building_id": BUILDING_ID, "owner_name": "Tony Brown", "created_at": NOW},
]

# ── EC Members ────────────────────────────────────────────────────────────────

SIERRA_EC_MEMBERS = [
    {
        "id": _new_id(),
        "name": "James Chen",
        "position": "Chairperson",
        "email": "james.chen@sierra.com.au",
        "phone": "+61 412 001 001",
        "unit_number": "S001",
        "term_start": "2024-07-01",
        "term_end": "2026-06-30",
        "bio": "Chairperson of Sierra Owners Corporation. Passionate about sustainable building management.",
        "building_id": BUILDING_ID,
        "created_at": NOW,
    },
    {
        "id": _new_id(),
        "name": "Lisa Wang",
        "position": "Secretary",
        "email": "lisa.wang@sierra.com.au",
        "phone": "+61 412 001 002",
        "unit_number": "S002",
        "term_start": "2024-07-01",
        "term_end": "2026-06-30",
        "bio": "Secretary of Sierra EC. Handles all official correspondence and minutes.",
        "building_id": BUILDING_ID,
        "created_at": NOW,
    },
    {
        "id": _new_id(),
        "name": "David Park",
        "position": "Treasurer",
        "email": "david.park@sierra.com.au",
        "phone": "+61 412 001 003",
        "unit_number": "S003",
        "term_start": "2024-07-01",
        "term_end": "2026-06-30",
        "bio": "Treasurer responsible for Sierra financial management and levy collections.",
        "building_id": BUILDING_ID,
        "created_at": NOW,
    },
]

# ── Emergency Services ────────────────────────────────────────────────────────

SIERRA_EMERGENCY_SERVICES = [
    {
        "id": _new_id(),
        "name": "Gungahlin Emergency Electrician",
        "category": "Electrical",
        "phone": "+61 2 6123 4000",
        "email": "electric@gungahlin.com.au",
        "address": "5 Hibberson Street, Gungahlin ACT 2912",
        "description": "24/7 emergency electrical services for Sierra residents.",
        "is_emergency": True,
        "is_private": False,
        "order": 1,
        "building_id": BUILDING_ID,
        "created_at": NOW,
    },
    {
        "id": _new_id(),
        "name": "Gungahlin Plumbing Services",
        "category": "Plumbing",
        "phone": "+61 2 6123 4001",
        "email": "plumbing@gungahlin.com.au",
        "address": "12 Anthony Rolfe Avenue, Gungahlin ACT 2912",
        "description": "Licensed plumber covering Sierra complex and surrounds.",
        "is_emergency": True,
        "is_private": False,
        "order": 2,
        "building_id": BUILDING_ID,
        "created_at": NOW,
    },
    {
        "id": _new_id(),
        "name": "Sierra Security & Locksmith",
        "category": "Locksmith",
        "phone": "+61 412 400 001",
        "email": "security@sierra-gungahlin.com.au",
        "address": "70 Efkarpidis Street, Gungahlin ACT 2912",
        "description": "On-site security and locksmith for Sierra residents.",
        "is_emergency": True,
        "is_private": False,
        "order": 3,
        "building_id": BUILDING_ID,
        "created_at": NOW,
    },
]

# ── Blog Posts ────────────────────────────────────────────────────────────────

SIERRA_BLOG_POSTS = [
    {
        "id": _new_id(),
        "title": "Welcome to Sierra — Community Update",
        "content": "<p>Welcome to the Sierra residents portal. Use this platform to stay connected with your community, access important documents, and manage levy payments.</p>",
        "excerpt": "Welcome to the Sierra residents portal.",
        "author_id": "system",
        "author_name": "James Chen",
        "category": "Announcement",
        "is_published": True,
        "is_public": True,
        "scope": "building",
        "building_id": BUILDING_ID,
        "views": 0,
        "created_at": NOW,
        "updated_at": NOW,
    },
    {
        "id": _new_id(),
        "title": "Sierra Q2 2026 Levy Notice",
        "content": "<p>Quarterly levy notices for Q2 2026 have been issued. Please ensure payment by 1 June 2026. Contact the strata manager for payment arrangements.</p>",
        "excerpt": "Q2 2026 quarterly levies are now due.",
        "author_id": "system",
        "author_name": "Building Manager",
        "category": "Finance",
        "is_published": True,
        "is_public": True,
        "scope": "building",
        "building_id": BUILDING_ID,
        "views": 0,
        "created_at": NOW,
        "updated_at": NOW,
    },
    {
        "id": _new_id(),
        "title": "Parking Policy Reminder",
        "content": "<p>All residents are reminded that visitor parking spaces must not be used for permanent parking. Vehicles found in violation may be towed.</p>",
        "excerpt": "Reminder about Sierra parking policy.",
        "author_id": "system",
        "author_name": "Lisa Wang",
        "category": "Notice",
        "is_published": True,
        "is_public": True,
        "scope": "building",
        "building_id": BUILDING_ID,
        "views": 0,
        "created_at": NOW,
        "updated_at": NOW,
    },
]

# ── Listings ─────────────────────────────────────────────────────────────────

SIERRA_LISTINGS = [
    {
        "id": _new_id(),
        "title": "Apartment S025 — For Rent",
        "description": "Spacious 3-bedroom apartment with city views. Available from 1 July 2026. $650/week.",
        "listing_type": "for_rent",
        "price": 650.0,
        "unit_number": "S025",
        "contact_name": "Raj Kumar",
        "contact_email": "raj.kumar@sierra.com.au",
        "contact_phone": "+61 412 001 025",
        "is_public": True,
        "scope": "building",
        "status": "active",
        "building_id": BUILDING_ID,
        "created_by": "system",
        "created_by_name": "Raj Kumar",
        "created_at": NOW,
        "updated_at": NOW,
    },
    {
        "id": _new_id(),
        "title": "Parking Space S-P12 — For Rent",
        "description": "Covered basement parking space available. $120/month.",
        "listing_type": "parking",
        "price": 120.0,
        "unit_number": "S015",
        "contact_name": "Mike Taylor",
        "contact_email": "mike.taylor@sierra.com.au",
        "contact_phone": "+61 412 001 015",
        "is_public": True,
        "scope": "building",
        "status": "active",
        "building_id": BUILDING_ID,
        "created_by": "system",
        "created_by_name": "Mike Taylor",
        "created_at": NOW,
        "updated_at": NOW,
    },
]


# ── Seed Function ─────────────────────────────────────────────────────────────

async def seed_sierra():
    """Generated function header.

    Function: seed_sierra
    Path: backend/seeds/seed_sierra.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if db is None:
        print("[seed_sierra] No DB connection — skipping")
        return

    print("[seed_sierra] Starting Sierra seed...")

    # 1. Building record
    existing_building = await db.buildings.find_one({"id": BUILDING_ID})
    if not existing_building:
        await db.buildings.insert_one({
            "id": BUILDING_ID,
            "name": "Sierra Gungahlin",
            "address": "70 Efkarpidis Street, Gungahlin ACT 2912",
            "plan_number": BUILDING_ID,
            "lots": 120,
            "year_built": 2022,
            "is_active": True,
            "is_demo": False,
            "slug": "sierra",
            "description": "Sierra strata complex, Gungahlin ACT.",
            "created_at": NOW,
        })
        print("[seed_sierra] Building record created.")
    else:
        print("[seed_sierra] Building exists — skipping.")

    # 2. Building settings
    await db.settings.update_one(
        {"building_id": BUILDING_ID},
        {"$setOnInsert": {
            "building_id": BUILDING_ID,
            "building_name": "Sierra Gungahlin",
            "building_address": "70 Efkarpidis Street, Gungahlin ACT 2912",
            "building_description": "Luxury living in the heart of Gungahlin.",
            "manager_email": "manager@eastgate.com",
            "contact_phone": "+61 2 6123 4567",
            "created_at": NOW,
        }},
        upsert=True,
    )
    print("[seed_sierra] Settings upserted.")

    # 3. Users
    users_added = 0
    for user in SIERRA_USERS:
        if user.get("password_hash") is None:
            user["password_hash"] = _hash(_seed_password())
        existing = await db.users.find_one({"email": user["email"]})
        if not existing:
            await db.users.insert_one(dict(user))
            users_added += 1
        else:
            # User already exists (e.g. manager@eastgate.com is cross-building).
            # Do NOT overwrite their building_id — that field records their PRIMARY
            # building for login-token scoping.  Adding a Sierra membership record
            # (step 4 below) is sufficient to grant access to this building.
            pass
    print(f"[seed_sierra] Users: {users_added} added (others existed).")

    # 4. Memberships
    memberships_added = 0
    for user in SIERRA_USERS:
        user_doc = await db.users.find_one({"email": user["email"]}, {"_id": 0})
        if not user_doc:
            continue
        existing_m = await db.memberships.find_one(
            {"user_id": user_doc["id"], "building_id": BUILDING_ID}
        )
        if not existing_m:
            await db.memberships.insert_one({
                "id": _new_id(),
                "user_id": user_doc["id"],
                "building_id": BUILDING_ID,
                "role": user["role"],
                "is_active": True,
                "created_at": NOW,
            })
            memberships_added += 1
    print(f"[seed_sierra] Memberships: {memberships_added} added.")

    # 5. Units
    units_added = 0
    for unit in SIERRA_UNITS:
        existing = await db.units.find_one({"unit_number": unit["unit_number"], "building_id": BUILDING_ID})
        if not existing:
            await db.units.insert_one(dict(unit))
            units_added += 1
    print(f"[seed_sierra] Units: {units_added} added.")

    # 6. EC Members
    ec_added = 0
    for ec in SIERRA_EC_MEMBERS:
        existing = await db.ec_members.find_one({"email": ec["email"], "building_id": BUILDING_ID})
        if not existing:
            await db.ec_members.insert_one(dict(ec))
            ec_added += 1
    print(f"[seed_sierra] EC members: {ec_added} added.")

    # 7. Emergency services
    es_added = 0
    for es in SIERRA_EMERGENCY_SERVICES:
        existing = await db.emergency_services.find_one({"name": es["name"], "building_id": BUILDING_ID})
        if not existing:
            await db.emergency_services.insert_one(dict(es))
            es_added += 1
    print(f"[seed_sierra] Emergency services: {es_added} added.")

    # 8. Blog posts
    blog_added = 0
    for post in SIERRA_BLOG_POSTS:
        existing = await db.blog_posts.find_one({"title": post["title"], "building_id": BUILDING_ID})
        if not existing:
            await db.blog_posts.insert_one(dict(post))
            blog_added += 1
    print(f"[seed_sierra] Blog posts: {blog_added} added.")

    # 9. Listings
    listings_added = 0
    for listing in SIERRA_LISTINGS:
        existing = await db.listings.find_one({"title": listing["title"], "building_id": BUILDING_ID})
        if not existing:
            await db.listings.insert_one(dict(listing))
            listings_added += 1
    print(f"[seed_sierra] Listings: {listings_added} added.")

    print("[seed_sierra] Sierra seed complete.")


if __name__ == "__main__":
    asyncio.run(seed_sierra())

# ── Credential Reference ──────────────────────────────────────────────────────
SIERRA_CREDENTIALS = """
=== Sierra (16244) Seed User Credentials ===

Chairman:
  Email: james.chen@sierra.com.au
  Password: SierraChair123!
  Unit: S001

EC Member 1:
  Email: lisa.wang@sierra.com.au
  Password: the value of SEED_TEST_USER_PASSWORD
  Unit: S002

EC Member 2:
  Email: david.park@sierra.com.au
  Password: SierraEC2123!
  Unit: S003

Strata Manager (shared):
  Email: manager@eastgate.com
  Password: $SEED_TEST_USER_PASSWORD

Owners:
  sarah.jones@sierra.com.au / SierraOwner1! (S010)
  mike.taylor@sierra.com.au / SierraOwner2! (S015)
  emily.white@sierra.com.au / SierraOwner3! (S020)
  raj.kumar@sierra.com.au   / SierraOwner4! (S025)
  claire.murphy@sierra.com.au / SierraOwner5! (S030)
  tony.brown@sierra.com.au  / SierraOwner6! (S035)
"""
