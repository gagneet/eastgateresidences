"""Super-admin seed — Postgres clean-break bootstrap.

Upserts the two platform super-admin users into core.users with a
pre-hashed password.  Safe to run multiple times (idempotent).

Usage::

    cd backend
    python3 seeds/super_admins.py

The initial password is taken from ``SEED_SUPER_ADMIN_PASSWORD``; the seed
refuses to run without it.  Both accounts are created
with ``is_approved = TRUE`` and ``is_active = TRUE``.

Database URL is read from the ``DATABASE_URL`` environment variable
(same as alembic; loaded automatically from backend/.env if present).

NEVER commit plain-text passwords.  The hash below was generated with
passlib bcrypt (rounds=12) and is safe to store in source.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running directly from the backend/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import uuid

import bcrypt

from sqlalchemy import text
from db_postgres.session import async_session_context

# ──────────────────────────────────────────────────────────────────────────────
# Constants — no secret values; the hash is public knowledge once committed
# ──────────────────────────────────────────────────────────────────────────────

# Platform tenant — deterministic UUIDv5 so re-runs never create duplicates
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # UUID namespace (URL)
PLATFORM_TENANT_ID = str(uuid.uuid5(_NS, "strataos-platform-tenant"))

PLATFORM_TENANT_NAME = "StrataOS Platform"

def _initial_password_hash() -> str:
    """bcrypt hash for a freshly seeded super admin.

    The password comes from SEED_SUPER_ADMIN_PASSWORD and there is NO DEFAULT — the
    seed refuses to run without it, rather than falling back to something a reader
    of this file would know.

    This used to be a pre-computed hash literal with the plaintext written in the
    comment above it, both committed. The account it created
    (administrator@strataos.live) was still active as a super_admin on 2026-08-26
    with that exact hash still stored, i.e. the "change these passwords immediately
    after first login" note below had never been acted on in the whole life of the
    system. Rotated that day. A literal here recreates the problem on the next
    environment, so it cannot come back.
    """
    password = os.environ.get("SEED_SUPER_ADMIN_PASSWORD")
    if not password:
        raise SystemExit(
            "SEED_SUPER_ADMIN_PASSWORD is not set.\n"
            "Generate one and pass it in for this run only, e.g.\n"
            "  SEED_SUPER_ADMIN_PASSWORD=\"$(python3 -c \"import secrets,string;\\\n"
            "    a=''.join(c for c in string.ascii_letters+string.digits+'-_.~@+=' if c not in 'lI1O0');\\\n"
            "    print(''.join(secrets.choice(a) for _ in range(24)))\")\" \\\n"
            "    python3 seeds/super_admins.py\n"
            "Do not put it in .env or a shell history you keep."
        )
    if len(password) < 16:
        raise SystemExit("SEED_SUPER_ADMIN_PASSWORD must be at least 16 characters.")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()

SUPER_ADMINS = [
    {
        "email": "gagneet@silverfoxtechnologies.com.au",
        "full_name": "Silverfox Admin",
        "first_name": "Gagneet",
        "last_name": "Singh",
        # Was a committed bcrypt hash. Verified 2026-08-26 that it is no longer the
        # stored hash for this account, so it was not a live credential — but a
        # committed hash is crackable offline and is a credential in every sense
        # that matters, so it goes the same way as the other one.
        "password_hash": None,
    },
    {
        "email": "administrator@strataos.live",
        "full_name": "StrataOS Administrator",
        "first_name": "StrataOS",
        "last_name": "Administrator",
        # Resolved at run time from SEED_SUPER_ADMIN_PASSWORD — see
        # _initial_password_hash(). Left as None here so importing this module
        # never requires the env var; only seeding does.
        "password_hash": None,
    },
]


async def seed() -> None:
    # Fail before touching the database. Resolving the password here rather than
    # at the first user upsert means a missing env var cannot leave a half-seeded
    # platform tenant behind.
    _initial_password_hash()
    """Generated function header.

    Function: seed
    Path: backend/seeds/super_admins.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        # ── 1. Upsert platform tenant (no RLS on core.tenants) ──────────────
        await session.execute(
            text("""
                 INSERT INTO core.tenants (tenant_id, tenant_name)
                 VALUES (:tid, :name) ON CONFLICT (tenant_id) DO
                 UPDATE SET tenant_name = EXCLUDED.tenant_name
                 """),
            {"tid": PLATFORM_TENANT_ID, "name": PLATFORM_TENANT_NAME},
        )
        print(f"[seed] Platform tenant upserted: {PLATFORM_TENANT_ID}")

        # ── 2. SET LOCAL tenant context for RLS ────────────────────────────
        await session.execute(text(f"SET LOCAL app.tenant_id = '{PLATFORM_TENANT_ID}'"))

        # ── 3. Upsert each super admin ─────────────────────────────────────
        for sa in SUPER_ADMINS:
            result = await session.execute(
                text("""
                     INSERT INTO core.users
                     (tenant_id, email, full_name, first_name, last_name,
                      password_hash, role, is_active, is_approved)
                     VALUES (:tid, :email, :full_name, :first_name, :last_name,
                             :pw_hash, 'super_admin'::core.user_role, TRUE, TRUE) ON CONFLICT (tenant_id, email) DO
                     UPDATE
                         SET full_name = EXCLUDED.full_name,
                         first_name = EXCLUDED.first_name,
                         last_name = EXCLUDED.last_name,
                         is_active = TRUE,
                         is_approved = TRUE,
                         updated_at = NOW()
                         RETURNING user_id::TEXT
                     """),
                {
                    "tid": PLATFORM_TENANT_ID,
                    "email": sa["email"],
                    "full_name": sa["full_name"],
                    "first_name": sa["first_name"],
                    "last_name": sa["last_name"],
                    "pw_hash": sa.get("password_hash") or _initial_password_hash(),
                },
            )
            user_id = result.scalar()
            print(f"[seed] Super admin upserted: {sa['email']} → {user_id}")

            # ── 4. Global role assignment (scheme_id = NULL) ─────────────
            await session.execute(
                text("""
                     INSERT INTO core.user_role_assignments
                         (tenant_id, user_id, scheme_id, role, is_active)
                     VALUES (:tid, :uid, NULL, 'super_admin'::core.user_role, TRUE) ON CONFLICT (user_id, role)
                     WHERE scheme_id IS NULL
                         DO
                     UPDATE SET is_active = TRUE, granted_at = NOW()
                     """),
                {"tid": PLATFORM_TENANT_ID, "uid": user_id},
            )
            print(f"[seed] Global role assignment created for {sa['email']}")

    print("\n[seed] Done — super admin seed complete.")
    print(f"       Platform tenant ID: {PLATFORM_TENANT_ID}")
    print("       Initial password: the value of SEED_SUPER_ADMIN_PASSWORD")
    print("       ⚠️  Change these passwords immediately after first login.")


if __name__ == "__main__":
    # Ensure DATABASE_URL is available
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set. Source backend/.env or set it manually.")
        sys.exit(1)
    asyncio.run(seed())
