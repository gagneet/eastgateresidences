#!/usr/bin/env ../../venv/bin/python3
"""
Seed RBAC (Role-Based Access Control)

Seeds the permissions, roles, and role_permissions collections and creates
the required MongoDB indexes for the RBAC and relationship-tuple subsystems.

Safe to run multiple times (idempotent via upsert).
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncio
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncMongoClient(mongo_url)
db = client[os.environ['DB_NAME']]

# ---------------------------------------------------------------------------
# Permission definitions
# ---------------------------------------------------------------------------

PERMISSIONS = [
    # building
    {"slug": "building.view", "name": "View Building", "category": "building"},
    {"slug": "building.update", "name": "Update Building", "category": "building"},
    {"slug": "building.config", "name": "Configure Building", "category": "building"},
    # financial
    {"slug": "financial.view", "name": "View Financials", "category": "financial"},
    {"slug": "financial.view_own", "name": "View Own Financials", "category": "financial"},
    {"slug": "financial.manage", "name": "Manage Financials", "category": "financial"},
    {"slug": "financial.approve_invoice", "name": "Approve Invoices", "category": "financial"},
    {"slug": "financial.export", "name": "Export Financials", "category": "financial"},
    # workorder
    {"slug": "workorder.view", "name": "View Work Orders", "category": "workorder"},
    {"slug": "workorder.create", "name": "Create Work Orders", "category": "workorder"},
    {"slug": "workorder.update", "name": "Update Work Orders", "category": "workorder"},
    {"slug": "workorder.approve", "name": "Approve Work Orders", "category": "workorder"},
    # documents
    {"slug": "documents.view", "name": "View Documents", "category": "documents"},
    {"slug": "documents.upload", "name": "Upload Documents", "category": "documents"},
    {"slug": "documents.delete", "name": "Delete Documents", "category": "documents"},
    {"slug": "documents.manage", "name": "Manage Documents", "category": "documents"},
    # committee
    {"slug": "committee.vote", "name": "Cast Committee Vote", "category": "committee"},
    {"slug": "committee.finalize_minutes", "name": "Finalize Minutes", "category": "committee"},
    {"slug": "committee.manage", "name": "Manage Committee", "category": "committee"},
    # users
    {"slug": "users.view", "name": "View Users", "category": "users"},
    {"slug": "users.manage", "name": "Manage Users", "category": "users"},
    {"slug": "users.invite", "name": "Invite Users", "category": "users"},
    # announcements
    {"slug": "announcements.view", "name": "View Announcements", "category": "announcements"},
    {"slug": "announcements.create", "name": "Create Announcements", "category": "announcements"},
    {"slug": "announcements.manage", "name": "Manage Announcements", "category": "announcements"},
    # meetings
    {"slug": "meetings.view", "name": "View Meetings", "category": "meetings"},
    {"slug": "meetings.manage", "name": "Manage Meetings", "category": "meetings"},
    # listings
    {"slug": "listings.view", "name": "View Listings", "category": "listings"},
    {"slug": "listings.create", "name": "Create Listings", "category": "listings"},
    {"slug": "listings.manage", "name": "Manage Listings", "category": "listings"},
    # maintenance
    {"slug": "maintenance.request", "name": "Request Maintenance", "category": "maintenance"},
    {"slug": "maintenance.manage", "name": "Manage Maintenance", "category": "maintenance"},
    # tenancy
    {"slug": "tenancy.view", "name": "View Tenancy", "category": "tenancy"},
    {"slug": "tenancy.manage", "name": "Manage Tenancy", "category": "tenancy"},
    # chat
    {"slug": "chat.access", "name": "Access Chat", "category": "chat"},
    # notifications
    {"slug": "notifications.send", "name": "Send Notifications", "category": "notifications"},
    # schedule
    {"slug": "schedule.view", "name": "View Schedule", "category": "schedule"},
    {"slug": "schedule.manage", "name": "Manage Schedule", "category": "schedule"},
    # email
    {"slug": "email.access", "name": "Access Email", "category": "email"},
]

ALL_SLUGS = {p["slug"] for p in PERMISSIONS}

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

_OWNER_PERMS = {
    "financial.view_own", "workorder.view", "workorder.create",
    "documents.view", "committee.vote", "announcements.view",
    "meetings.view", "listings.view", "listings.create",
    "maintenance.request", "tenancy.view", "tenancy.manage",
    "chat.access", "email.access",
}

_EC_MEMBER_PERMS = {
    "financial.view", "financial.manage",
    "workorder.view", "workorder.create", "workorder.update", "workorder.approve",
    "documents.view", "documents.upload",
    "committee.vote", "committee.finalize_minutes", "committee.manage",
    "users.view", "users.manage",
    "announcements.view", "announcements.create", "announcements.manage",
    "meetings.view", "meetings.manage",
    "listings.view", "listings.create",
    "maintenance.request", "maintenance.manage",
    "tenancy.view",
    "chat.access", "notifications.send",
    "schedule.view", "email.access",
}

ROLES = [
    {
        "name": "SUPER_ADMIN",
        "display_name": "Super Administrator",
        "description": "Full system access including building configuration",
        "base_role": True,
        "inherits": [],
        "permissions": list(ALL_SLUGS),
    },
    {
        "name": "CHAIRMAN",
        "display_name": "Chairman",
        "description": "EC Chairman — all permissions except building configuration",
        "base_role": False,
        "inherits": ["EC_MEMBER"],
        "permissions": list(ALL_SLUGS - {"building.config"}),
    },
    {
        "name": "TREASURER",
        "display_name": "Treasurer",
        "description": "EC Treasurer — financial management and approval",
        "base_role": False,
        "inherits": ["EC_MEMBER"],
        "permissions": [
            "financial.view", "financial.manage", "financial.approve_invoice", "financial.export",
            "committee.vote",
            "meetings.view",
            "workorder.view", "workorder.approve",
            "documents.view",
            "chat.access",
            "announcements.view",
        ],
    },
    {
        "name": "SECRETARY",
        "display_name": "Secretary",
        "description": "EC Secretary — minutes, meetings, and document management",
        "base_role": False,
        "inherits": ["EC_MEMBER"],
        "permissions": [
            "committee.vote", "committee.finalize_minutes",
            "meetings.view", "meetings.manage",
            "documents.view", "documents.upload",
            "announcements.view", "announcements.create",
            "chat.access",
        ],
    },
    {
        "name": "EC_MEMBER",
        "display_name": "EC Member",
        "description": "Executive Committee member",
        "base_role": False,
        "inherits": ["OWNER"],
        "permissions": list(_EC_MEMBER_PERMS),
    },
    {
        "name": "STRATA_MANAGER",
        "display_name": "Strata Manager",
        "description": "Professional strata manager — all permissions except building configuration",
        "base_role": True,
        "inherits": [],
        "permissions": list(ALL_SLUGS - {"building.config"}),
    },
    {
        "name": "BUILDING_MANAGER",
        "display_name": "Building Manager",
        "description": "On-site building manager",
        "base_role": True,
        "inherits": [],
        "permissions": [
            "building.view", "building.update",
            "workorder.view", "workorder.create", "workorder.update", "workorder.approve",
            "maintenance.request", "maintenance.manage",
            "schedule.view", "schedule.manage",
            "documents.view", "documents.upload",
            "users.view",
            "chat.access",
            "tenancy.view", "tenancy.manage",
        ],
    },
    {
        "name": "ADMIN_STAFF",
        "display_name": "Admin Staff",
        "description": "Building administration staff",
        "base_role": True,
        "inherits": [],
        "permissions": [
            "documents.view",
            "meetings.view",
            "maintenance.request", "maintenance.manage",
            "tenancy.view", "tenancy.manage",
            "chat.access",
            "schedule.view",
            "users.view",
        ],
    },
    {
        "name": "OWNER",
        "display_name": "Owner",
        "description": "Property owner",
        "base_role": True,
        "inherits": [],
        "permissions": list(_OWNER_PERMS),
    },
    {
        "name": "TENANT",
        "display_name": "Tenant",
        "description": "Residential tenant",
        "base_role": True,
        "inherits": [],
        "permissions": [
            "workorder.view",
            "documents.view",
            "announcements.view",
            "meetings.view",
            "listings.view", "listings.create",
            "maintenance.request",
            "chat.access",
        ],
    },
    {
        "name": "REAL_ESTATE_AGENT",
        "display_name": "Real Estate Agent",
        "description": "Licensed real estate agent managing owner properties",
        "base_role": True,
        "inherits": [],
        "permissions": [
            "workorder.view",
            "documents.view", "documents.upload",
            "listings.view", "listings.create", "listings.manage",
            "maintenance.request", "maintenance.manage",
            "tenancy.view", "tenancy.manage",
            "chat.access",
            "schedule.view",
        ],
    },
    {
        "name": "SERVICE_PROVIDER",
        "display_name": "Service Provider",
        "description": "External contractor or service provider",
        "base_role": True,
        "inherits": [],
        "permissions": [
            "workorder.view", "workorder.update",
            "chat.access",
            "schedule.view",
        ],
    },
    {
        "name": "GUEST",
        "display_name": "Guest",
        "description": "Unauthenticated or read-only visitor",
        "base_role": True,
        "inherits": [],
        "permissions": [
            "announcements.view",
        ],
    },
]


# ---------------------------------------------------------------------------
# Deterministic ID helpers
# ---------------------------------------------------------------------------

def permission_id(slug: str) -> str:
    """Generated function header.

    Function: permission_id
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, slug))


def role_id(role_name: str) -> str:
    """Generated function header.

    Function: role_id
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "role:" + role_name))


def role_permission_id(r_id: str, slug: str) -> str:
    """Generated function header.

    Function: role_permission_id
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, r_id + ":" + slug))


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

async def seed_permissions() -> int:
    """Generated function header.

    Function: seed_permissions
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for perm in PERMISSIONS:
        doc = {
            "id": permission_id(perm["slug"]),
            "slug": perm["slug"],
            "name": perm["name"],
            "category": perm["category"],
            "updated_at": now,
        }
        result = await db.permissions.update_one(
            {"slug": perm["slug"]},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        if result.upserted_id:
            count += 1
    return count


async def seed_roles() -> int:
    """Generated function header.

    Function: seed_roles
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for role in ROLES:
        r_id = role_id(role["name"])
        doc = {
            "id": r_id,
            "name": role["name"],
            "display_name": role["display_name"],
            "description": role["description"],
            "base_role": role["base_role"],
            "inherits": role["inherits"],
            "updated_at": now,
        }
        result = await db.roles.update_one(
            {"name": role["name"]},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        if result.upserted_id:
            count += 1
    return count


async def seed_role_permissions() -> int:
    """Generated function header.

    Function: seed_role_permissions
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for role in ROLES:
        r_id = role_id(role["name"])
        for slug in role["permissions"]:
            rp_id = role_permission_id(r_id, slug)
            doc = {
                "id": rp_id,
                "role_id": r_id,
                "role_name": role["name"],
                "permission_id": permission_id(slug),
                "slug": slug,
                "updated_at": now,
            }
            result = await db.role_permissions.update_one(
                {"role_id": r_id, "slug": slug},
                {"$set": doc, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
            if result.upserted_id:
                count += 1
    return count


async def create_indexes() -> None:
    # user_roles
    """Generated function header.

    Function: create_indexes
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await db.user_roles.create_index(
        [("user_id", 1), ("building_id", 1), ("is_active", 1)],
        name="user_roles_user_building_active",
    )
    await db.user_roles.create_index(
        [("role_id", 1), ("building_id", 1)],
        name="user_roles_role_building",
    )
    # relationship_tuples
    await db.relationship_tuples.create_index(
        [("subject", 1), ("relation", 1), ("object", 1)],
        name="rel_tuples_subject_relation_object",
    )
    await db.relationship_tuples.create_index(
        [("building_id", 1), ("is_active", 1)],
        name="rel_tuples_building_active",
    )
    # permissions
    await db.permissions.create_index(
        [("slug", 1)],
        unique=True,
        name="permissions_slug_unique",
    )
    # roles
    await db.roles.create_index(
        [("name", 1)],
        unique=True,
        name="roles_name_unique",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/seeds/rbac.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        print("Seeding RBAC data...")

        print("  Creating indexes...")
        await create_indexes()
        print("  ✓ Indexes created/verified")

        new_permissions = await seed_permissions()
        print(f"  ✓ Permissions: {len(PERMISSIONS)} total ({new_permissions} new)")

        new_roles = await seed_roles()
        print(f"  ✓ Roles: {len(ROLES)} total ({new_roles} new)")

        total_rp = sum(len(r["permissions"]) for r in ROLES)
        new_rp = await seed_role_permissions()
        print(f"  ✓ Role→Permission mappings: {total_rp} total ({new_rp} new)")

        print("RBAC seeding complete.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
