"""Seed core.role_permissions with canonical permission grants.

This script is idempotent: it uses INSERT ... ON CONFLICT DO UPDATE
so it can be re-run safely after the DEFAULT_PERMISSIONS dict changes.

Source of truth: backend/models/user.py::DEFAULT_PERMISSIONS
Target table:    core.role_permissions (migration 0011, global — no RLS)

Run:
    cd backend && python -m seeds.role_permissions

Or import and call seed() from another seed orchestrator:
    from seeds.role_permissions import seed
    await seed(conn)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow running from repo root or backend/ directory
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from models.user import DEFAULT_PERMISSIONS, UserRole  # noqa: E402

logger = logging.getLogger(__name__)

# Canonical roles we seed permissions for.
# Skip legacy aliases ('reception', 'chairman') — they map to real roles above.
_CANONICAL_ROLES: tuple[str, ...] = (
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
    UserRole.REAL_ESTATE_AGENT,
    UserRole.SERVICE_PROVIDER,
    UserRole.OWNER,
    UserRole.TENANT,
    UserRole.GUEST,
)


def build_rows() -> list[dict]:
    """Build a flat list of (role, permission_key, is_granted) dicts from DEFAULT_PERMISSIONS."""
    rows: list[dict] = []
    for role in _CANONICAL_ROLES:
        perms = DEFAULT_PERMISSIONS.get(role)
        if perms is None:
            logger.warning("No DEFAULT_PERMISSIONS entry for role '%s' — skipping", role)
            continue
        for perm_key, is_granted in perms.model_dump().items():
            rows.append({
                "role": role,
                "permission_key": perm_key,
                "is_granted": bool(is_granted),
            })
    return rows


async def seed(conn) -> int:
    """Insert/upsert role_permissions rows.  Returns the number of rows affected."""
    rows = build_rows()
    if not rows:
        logger.warning("No permission rows to seed — aborting")
        return 0

    # asyncpg executemany with ON CONFLICT upsert
    sql = """
          INSERT INTO core.role_permissions (role, permission_key, is_granted)
          VALUES ($1, $2, $3) ON CONFLICT (role, permission_key) DO
          UPDATE
              SET is_granted = EXCLUDED.is_granted \
          """
    await conn.executemany(sql, [(r["role"], r["permission_key"], r["is_granted"]) for r in rows])
    logger.info("Seeded %d role_permissions rows (%d roles)", len(rows), len(_CANONICAL_ROLES))
    return len(rows)


async def _main() -> None:
    """Generated function header.

    Function: _main
    Path: backend/seeds/role_permissions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import asyncpg  # type: ignore

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        # Try loading from backend/.env
        env_file = _BACKEND / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not database_url:
        raise RuntimeError("DATABASE_URL not set and not found in backend/.env")

    # asyncpg uses postgresql:// not postgresql+asyncpg://
    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(pg_url)
    try:
        n = await seed(conn)
        print(f"Done — {n} rows upserted into core.role_permissions")
    finally:
        await conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(_main())
