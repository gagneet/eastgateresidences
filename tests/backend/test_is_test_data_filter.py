"""Tests for the is_test_data flag introduced in migration 0029.

Two separate regression surfaces are guarded:

1. **Read-side filtering** — ``identity_repo.list_all_active_schemes``,
   ``list_schemes_for_user``, ``get_scheme_by_id`` and
   ``get_scheme_by_number`` must hide ``is_test_data = TRUE`` rows from
   production callers (specifically the SA building switcher).

2. **Bootstrap idempotency** — ``services.bootstrap_demo.ensure_demo_chain``
   must skip the seed when an active demo chain already exists, and the
   demo row must NOT be flagged as test data.

The Postgres-backed tests skip when ``DATABASE_URL`` is unset.
"""
from __future__ import annotations

import os
import re
import uuid

import asyncpg
import pytest

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres-backed tests.",
)

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
    yield conn
    await conn.close()


@pytest.fixture
async def leaked_test_scheme(pg):
    """Insert a tenant + scheme flagged is_test_data=TRUE and yield their ids.

    Mimics the leak we want to prevent: a test that crashed mid-cleanup
    and left a row in core.schemes. The fixture cleans up unconditionally
    after the test.
    """
    tenant_id = uuid.uuid4()
    await pg.execute(
        """INSERT INTO core.tenants (tenant_id, tenant_name, status, is_test_data)
           VALUES ($1, $2, 'active', TRUE)""",
        tenant_id, f"Leaked Test Tenant {tenant_id.hex[:6]}",
    )
    scheme_row = await pg.fetchrow(
        """INSERT INTO core.schemes
           (tenant_id, jurisdiction, scheme_number, scheme_name, status, is_test_data)
           VALUES ($1, 'ACT', $2, $3, 'active', TRUE) RETURNING scheme_id::TEXT, scheme_number""",
        tenant_id,
        f"LEAK-{tenant_id.hex[:6]}",
        f"Leaked Test Scheme {tenant_id.hex[:6]}",
    )
    yield {
        "tenant_id": str(tenant_id),
        "scheme_id": scheme_row["scheme_id"],
        "scheme_number": scheme_row["scheme_number"],
    }
    await pg.execute("DELETE FROM core.schemes WHERE tenant_id = $1", tenant_id)
    await pg.execute("DELETE FROM core.tenants WHERE tenant_id = $1", tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# 1. Read-side filtering
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_all_active_schemes_excludes_test_data(leaked_test_scheme):
    """SA building switcher must never surface a test-flagged scheme."""
    from db_postgres.repos.identity_repo import list_all_active_schemes

    rows = await list_all_active_schemes()
    leaked_id = leaked_test_scheme["scheme_id"]
    assert all(r["scheme_id"] != leaked_id for r in rows), (
        "list_all_active_schemes returned a row with is_test_data=TRUE — "
        "the SA building switcher will surface leaked test data."
    )


@pytest.mark.asyncio
async def test_get_scheme_by_id_excludes_test_data(leaked_test_scheme):
    """switch-building lookup must 404 on a test-flagged scheme."""
    from db_postgres.repos.identity_repo import get_scheme_by_id

    result = await get_scheme_by_id(leaked_test_scheme["scheme_id"])
    assert result is None, (
        "get_scheme_by_id returned a is_test_data=TRUE row — switch-building "
        "would let an SA scope a JWT to a test scheme."
    )


@pytest.mark.asyncio
async def test_get_scheme_by_number_excludes_test_data(leaked_test_scheme):
    """Plan-number lookup must also filter test data."""
    from db_postgres.repos.identity_repo import get_scheme_by_number

    result = await get_scheme_by_number(leaked_test_scheme["scheme_number"])
    assert result is None


@pytest.mark.asyncio
async def test_list_schemes_for_user_excludes_test_data(pg, leaked_test_scheme):
    """Multi-building owner switcher must also filter test data.

    Wires a fake user_role_assignment from a real-looking user to the
    leaked test scheme; even with the assignment in place, the helper
    must not return the test scheme.
    """
    from db_postgres.repos.identity_repo import list_schemes_for_user

    # Create a user inside the leaked tenant so the role assignment FK holds.
    user_id = uuid.uuid4()
    await pg.execute(
        """INSERT INTO core.users
           (user_id, tenant_id, email, full_name, password_hash, role,
            is_active, is_approved, is_test_data)
           VALUES ($1, $2, $3, 'Leak Test', '', 'owner', TRUE, TRUE, TRUE)""",
        user_id,
        uuid.UUID(leaked_test_scheme["tenant_id"]),
        f"leak-{user_id.hex[:8]}@test.invalid",
    )
    try:
        await pg.execute(
            f"SET app.tenant_id = '{leaked_test_scheme['tenant_id']}'"
        )
        await pg.execute(
            """INSERT INTO core.user_role_assignments
                   (tenant_id, user_id, scheme_id, role, is_active)
               VALUES ($1, $2, $3, CAST('owner' AS core.user_role), TRUE)""",
            uuid.UUID(leaked_test_scheme["tenant_id"]),
            user_id,
            uuid.UUID(leaked_test_scheme["scheme_id"]),
        )
        await pg.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")

        rows = await list_schemes_for_user(str(user_id))
        leaked_id = leaked_test_scheme["scheme_id"]
        assert all(r["scheme_id"] != leaked_id for r in rows), (
            "list_schemes_for_user surfaced a is_test_data=TRUE scheme; "
            "the per-user building switcher leaks test data."
        )
    finally:
        await pg.execute(
            "DELETE FROM core.user_role_assignments WHERE user_id = $1", user_id
        )
        await pg.execute("DELETE FROM core.users WHERE user_id = $1", user_id)


# ──────────────────────────────────────────────────────────────────────────
# 2. Bootstrap idempotency
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_skips_when_demo_already_exists(pg):
    """ensure_demo_chain must not re-seed when an active demo already exists.

    Pre-condition: an is_demo=TRUE tenant + scheme already in the DB
    (the canonical seed). The helper's existence check must short-circuit
    and ``seeds.demo_scheme.seed`` must
    not be invoked.
    """
    from integrations.demo_bank import bootstrap as bootstrap_demo

    has_demo = await pg.fetchval("""
                                 SELECT EXISTS (SELECT 1
                                                FROM core.schemes s
                                                         JOIN core.tenants t ON t.tenant_id = s.tenant_id
                                                WHERE s.is_demo = TRUE
                                                  AND t.is_demo = TRUE
                                                  AND s.status = 'active')
                                 """)
    if not has_demo:
        pytest.skip("No demo chain in DB; bootstrap idempotency test needs one.")

    seed_called = {"count": 0}

    async def fake_seed():
        seed_called["count"] += 1

    # Patch the seed *inside* the bootstrap module's import path so the
    # local ``from seeds.demo_scheme
    # import seed`` in ensure_demo_chain picks up the fake. The import is
    # deferred so we patch sys.modules.
    import seeds.demo_scheme as _ds_mod
    real_seed = _ds_mod.seed
    _ds_mod.seed = fake_seed
    try:
        await bootstrap_demo.ensure_demo_chain()
    finally:
        _ds_mod.seed = real_seed

    assert seed_called["count"] == 0, (
        "ensure_demo_chain re-ran the seed even though a demo chain "
        "already existed — bootstrap is not idempotent on the cheap path."
    )


@pytest.mark.asyncio
async def test_demo_scheme_is_not_test_data(pg):
    """Sanity: the demo chain must never carry is_test_data=TRUE.

    is_demo and is_test_data are independent flags. A demo flagged as
    test data would be wiped by the pytest session-end sweeper.
    """
    rows = await pg.fetch("""
                          SELECT scheme_id::TEXT, is_demo, is_test_data
                          FROM core.schemes
                          WHERE is_demo = TRUE
                          """)
    if not rows:
        pytest.skip("No demo scheme in DB.")
    for r in rows:
        assert r["is_test_data"] is False, (
            f"Demo scheme {r['scheme_id']} has is_test_data=TRUE — "
            "the session-end sweeper would delete it. is_demo and "
            "is_test_data must remain independent flags."
        )

    rows = await pg.fetch("""
                          SELECT tenant_id::TEXT, is_demo, is_test_data
                          FROM core.tenants
                          WHERE is_demo = TRUE
                          """)
    for r in rows:
        assert r["is_test_data"] is False, (
            f"Demo tenant {r['tenant_id']} has is_test_data=TRUE — "
            "the session-end sweeper would delete it."
        )
