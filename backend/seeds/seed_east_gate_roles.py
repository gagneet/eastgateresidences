# @featuretrace:east-gate-roles — East Gate Residences EC and management role seed.
# Layer: seed
# Data flow: this script → db.users + db.user_units (building-scoped, building_id=13195).
# Related: backend/seeds/super_admins.py
#           backend/seeds/seed_harbourview.py (pattern reference)
#           backend/routers/auth.py (user model)
# Collection: users
# Collection: user_units
# Scope: (building-scoped)
"""
Seed script for East Gate Residences (building_id: "13195") EC and management roles.

Creates/upserts:
  - Anthony McDonald — EC Chairman (UA063)
  - Brenda Thompson  — EC Member (TH087)
  - Keith Dears      — EC Secretary (no unit)
  - Jessica Minichiello — Strata Manager (no unit)
  - Gagneet Singh    — Platform Super Admin (ensures correct role)

All upserts are idempotent (keyed on email). Deterministic UUIDv5 IDs are
derived from email so re-runs produce the same document ids.

Run standalone:
    cd backend
    python3 -m seeds.seed_east_gate_roles

Or from project root:
    backend/venv/bin/python3 backend/seeds/seed_east_gate_roles.py
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import bcrypt

# BUILDING_ID constant is intentional: this seed is specifically for East Gate.
BUILDING_ID = "13195"

_NOW = datetime.now(timezone.utc).isoformat()

# UUID namespace for deterministic IDs derived from email addresses
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # RFC 4122 URL namespace

DEFAULT_PASSWORD = os.environ.get("SEED_DEFAULT_PASSWORD", "")
if not DEFAULT_PASSWORD:
    raise ValueError(
        "SEED_DEFAULT_PASSWORD environment variable must be set before running this seed script. "
        "Example: SEED_DEFAULT_PASSWORD=<secure-password> python3 seeds/seed_east_gate_roles.py"
    )


def _det_id(email: str) -> str:
    """Return a deterministic UUIDv5 string keyed on the user's email."""
    return str(uuid.uuid5(_NS, email))


def _hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


# ── User definitions ──────────────────────────────────────────────────────────

_HASHED_DEFAULT = _hash_password(DEFAULT_PASSWORD)


def _make_user(
    email: str,
    full_name: str,
    role: str,
    ec_position: str | None = None,
    unit_number: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: _make_user
    Path: backend/seeds/seed_east_gate_roles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "id": _det_id(email),
        "email": email,
        "full_name": full_name,
        "hashed_password": _HASHED_DEFAULT,
        "role": role,
        "ec_position": ec_position,
        "unit_number": unit_number,
        "building_id": BUILDING_ID,
        "is_active": True,
        "is_verified": True,
        "is_approved": True,
        "status": "active",
        "created_at": _NOW,
        "custom_permissions": {},
        "profile_image": None,
    }


EAST_GATE_USERS: list[dict[str, Any]] = [
    _make_user(
        email="anthony.mcdonald@eastgateresidences.com.au",
        full_name="Anthony McDonald",
        role="ec_member",
        ec_position="CHAIRMAN",
        unit_number="UA063",
    ),
    _make_user(
        email="brenda.thompson@eastgateresidences.com.au",
        full_name="Brenda Thompson",
        role="ec_member",
        ec_position="MEMBER",
        unit_number="TH087",
    ),
    _make_user(
        email="keith.dears@eastgateresidences.com.au",
        full_name="Keith Dears",
        role="ec_member",
        ec_position="SECRETARY",
        unit_number=None,
    ),
    _make_user(
        email="jessica@civium.com.au",
        full_name="Jessica Minichiello",
        role="strata_manager",
        ec_position=None,
        unit_number=None,
    ),
    _make_user(
        email="gagneet@gmail.com",
        full_name="Gagneet Singh",
        role="super_admin",
        ec_position=None,
        unit_number=None,
    ),
]


# ── Seed function ─────────────────────────────────────────────────────────────


async def seed_east_gate_roles() -> None:
    """Upsert East Gate EC and management users into MongoDB."""
    try:
        from database import db  # type: ignore[import]
    except ImportError:
        print(
            "[seed_east_gate_roles] ERROR: Could not import 'database'. "
            "Run from backend/ directory or ensure MONGO_URL/DB_NAME are set.",
        )
        return

    if db is None:
        print("[seed_east_gate_roles] No DB connection — skipping")
        return

    print(f"[seed_east_gate_roles] Starting East Gate roles seed (building_id={BUILDING_ID})...")

    users_upserted = 0
    unit_links_upserted = 0

    for user in EAST_GATE_USERS:
        email = user["email"]

        # Fields to always keep in sync
        set_fields: dict[str, Any] = {
            "full_name": user["full_name"],
            "role": user["role"],
            "building_id": user["building_id"],
            "is_active": user["is_active"],
            "is_verified": user["is_verified"],
            "is_approved": user["is_approved"],
            "status": user["status"],
        }
        if user["ec_position"] is not None:
            set_fields["ec_position"] = user["ec_position"]
        if user["unit_number"] is not None:
            set_fields["unit_number"] = user["unit_number"]

        # On insert only: id, email, hashed_password, created_at, custom_permissions
        set_on_insert: dict[str, Any] = {
            "id": user["id"],
            "email": email,
            "hashed_password": user["hashed_password"],
            "created_at": user["created_at"],
            "custom_permissions": user["custom_permissions"],
            "profile_image": user["profile_image"],
        }

        await db.users.update_one(
            {"email": email},
            {
                "$set": set_fields,
                "$setOnInsert": set_on_insert,
            },
            upsert=True,
        )
        users_upserted += 1
        print(f"[seed_east_gate_roles]   upserted user: {email} (role={user['role']})")

        # Link user to unit in user_units (only if they have a unit)
        unit_number = user.get("unit_number")
        if unit_number:
            user_id = user["id"]
            await db.user_units.update_one(
                {
                    "user_id": user_id,
                    "building_id": BUILDING_ID,
                    "unit_number": unit_number,
                },
                {
                    "$set": {
                        "user_id": user_id,
                        "building_id": BUILDING_ID,
                        "unit_number": unit_number,
                        "relationship": "owner",
                        "is_active": True,
                    }
                },
                upsert=True,
            )
            unit_links_upserted += 1
            print(
                f"[seed_east_gate_roles]     unit_units link: user={user_id} "
                f"unit={unit_number}"
            )

    print(
        f"[seed_east_gate_roles] Done. "
        f"Users upserted: {users_upserted}, "
        f"user_units links upserted: {unit_links_upserted}."
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Allow running directly: backend/venv/bin/python3 backend/seeds/seed_east_gate_roles.py
    _backend_dir = str(Path(__file__).resolve().parent.parent)
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)

    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass  # dotenv optional; rely on env vars already set

    asyncio.run(seed_east_gate_roles())
