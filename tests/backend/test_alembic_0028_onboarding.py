"""Tests for alembic migration 0028 — onboarding workflow data model.

Verifies the seven invariants documented in
``docs/architecture/onboarding/01_data_model_migration.md`` §1.4:

  1. Migration applied → all 3 new columns + both new indexes + trigger + table + enum exist.
  2. Insert self-managed tenant + scheme → succeeds.
  3. Insert second scheme under same self-managed tenant → fails (unique_violation).
  4. Insert two ``core.tenants`` with same ABN, both active → second fails.
  5. Insert two ``core.tenants`` with same ABN, second after first archived → succeeds.
  6. Trial-request lifecycle: submitted → approved with FK link.
  7. Duplicate ``submitted`` trial for same email → fails (unique_violation backstop).

Requires a live Postgres connection (``DATABASE_URL`` env var) and that
the migration has been applied (``alembic upgrade head``).
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
    reason="DATABASE_URL not set — skipping Postgres migration tests.",
)

# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────

_RLS_BYPASS_SENTINEL = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    # Tests insert across multiple tenants and into schemes/tenants directly,
    # which the production RLS policies (migrations 0021/0026/0027) would
    # block. Use the documented bypass sentinel — same pattern admin-only
    # repo helpers use in production code.
    await conn.execute(f"SET app.tenant_id = '{_RLS_BYPASS_SENTINEL}'")
    yield conn
    await conn.close()


@pytest.fixture
async def cleanup_test_rows(pg):
    """Track and remove any rows inserted by a single test.

    Tests append ``(table, where_sql, params)`` tuples to the yielded list;
    the fixture deletes them after the test runs.  Order is LIFO so child
    rows are removed before their parents.
    """
    inserted: list[tuple[str, str, tuple]] = []
    yield inserted
    for table, where_sql, params in reversed(inserted):
        try:
            await pg.execute(f"DELETE FROM {table} WHERE {where_sql}", *params)
        except Exception:
            # Best-effort cleanup — don't mask the test result.
            pass


# ──────────────────────────────────────────────────────────────────────────
# Test 1 — schema objects exist after migration
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tenants_new_columns_exist(pg):
    rows = await pg.fetch("""
                          SELECT column_name, data_type, is_nullable, column_default
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'tenants'
                            AND column_name IN ('is_self_managed', 'legal_name', 'created_from_trial_id')
                          """)
    cols = {r["column_name"]: r for r in rows}
    assert set(cols) == {"is_self_managed", "legal_name", "created_from_trial_id"}, (
        f"Expected three new columns; found: {set(cols)}"
    )
    assert cols["is_self_managed"]["data_type"] == "boolean"
    assert cols["is_self_managed"]["is_nullable"] == "NO"
    assert cols["is_self_managed"]["column_default"] == "false"
    assert cols["legal_name"]["data_type"] == "text"
    assert cols["legal_name"]["is_nullable"] == "YES"
    assert cols["created_from_trial_id"]["data_type"] == "uuid"


@pytest.mark.asyncio
async def test_tenants_abn_partial_index_exists(pg):
    row = await pg.fetchrow("""
                            SELECT indexdef
                            FROM pg_indexes
                            WHERE schemaname = 'core'
                              AND tablename = 'tenants'
                              AND indexname = 'tenants_abn_unique_active'
                            """)
    assert row is not None, "tenants_abn_unique_active index missing"
    assert "UNIQUE" in row["indexdef"]
    assert "WHERE" in row["indexdef"]
    assert "active" in row["indexdef"]


@pytest.mark.asyncio
async def test_self_managed_trigger_exists(pg):
    row = await pg.fetchrow("""
                            SELECT tgname, tgenabled
                            FROM pg_trigger
                            WHERE tgname = 'schemes_self_managed_one_per_tenant'
                            """)
    assert row is not None, "Self-managed-1:1 trigger missing"
    # pg_trigger.tgenabled is "char" — asyncpg returns bytes. 'O' = origin/replica enabled (default).
    state = row["tgenabled"]
    if isinstance(state, bytes):
        state = state.decode()
    assert state == "O", f"Trigger not enabled (state: {state!r})"


@pytest.mark.asyncio
async def test_trial_requests_table_exists(pg):
    row = await pg.fetchrow("""
                            SELECT table_name
                            FROM information_schema.tables
                            WHERE table_schema = 'core'
                              AND table_name = 'trial_requests'
                            """)
    assert row is not None, "core.trial_requests table missing"


@pytest.mark.asyncio
async def test_trial_request_status_enum_exists(pg):
    rows = await pg.fetch("""
                          SELECT enumlabel
                          FROM pg_enum
                                   JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                          WHERE pg_type.typname = 'trial_request_status'
                          ORDER BY enumsortorder
                          """)
    labels = [r["enumlabel"] for r in rows]
    assert labels == ["submitted", "approved", "rejected", "expired"]


# ──────────────────────────────────────────────────────────────────────────
# Test 2/3 — self-managed 1:1 invariant
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_self_managed_first_scheme_succeeds(pg, cleanup_test_rows):
    tenant_id = uuid.uuid4()
    cleanup_test_rows.append(("core.tenants", "tenant_id = $1", (tenant_id,)))
    cleanup_test_rows.append(("core.schemes", "tenant_id = $1", (tenant_id,)))

    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, is_self_managed, status)
           VALUES ($1, 'Test Self-Managed Owner Corp', TRUE, 'active')""",
        tenant_id,
    )
    await pg.execute(
        """INSERT INTO core.schemes
               (tenant_id, jurisdiction, scheme_number, scheme_name, status)
           VALUES ($1, 'ACT', 'TEST-SM-001', 'Test SM Scheme', 'active')""",
        tenant_id,
    )
    cnt = await pg.fetchval(
        "SELECT COUNT(*) FROM core.schemes WHERE tenant_id = $1", tenant_id,
    )
    assert cnt == 1


@pytest.mark.asyncio
async def test_self_managed_second_scheme_fails(pg, cleanup_test_rows):
    tenant_id = uuid.uuid4()
    cleanup_test_rows.append(("core.tenants", "tenant_id = $1", (tenant_id,)))
    cleanup_test_rows.append(("core.schemes", "tenant_id = $1", (tenant_id,)))

    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, is_self_managed, status)
           VALUES ($1, 'Test Self-Managed B', TRUE, 'active')""",
        tenant_id,
    )
    await pg.execute(
        """INSERT INTO core.schemes
               (tenant_id, jurisdiction, scheme_number, scheme_name, status)
           VALUES ($1, 'ACT', 'TEST-SM-B-1', 'First', 'active')""",
        tenant_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pg.execute(
            """INSERT INTO core.schemes
                   (tenant_id, jurisdiction, scheme_number, scheme_name, status)
               VALUES ($1, 'ACT', 'TEST-SM-B-2', 'Second-should-fail', 'active')""",
            tenant_id,
        )


@pytest.mark.asyncio
async def test_managed_tenant_can_have_multiple_schemes(pg, cleanup_test_rows):
    """SM-managed tenants (is_self_managed=FALSE) may own many schemes."""
    tenant_id = uuid.uuid4()
    cleanup_test_rows.append(("core.tenants", "tenant_id = $1", (tenant_id,)))
    cleanup_test_rows.append(("core.schemes", "tenant_id = $1", (tenant_id,)))

    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, is_self_managed, status)
           VALUES ($1, 'Test SM Co', FALSE, 'active')""",
        tenant_id,
    )
    for i in range(3):
        await pg.execute(
            """INSERT INTO core.schemes
                   (tenant_id, jurisdiction, scheme_number, scheme_name, status)
               VALUES ($1, 'ACT', $2, $3, 'active')""",
            tenant_id, f"TEST-MM-{i}", f"Test MM {i}",
        )
    cnt = await pg.fetchval(
        "SELECT COUNT(*) FROM core.schemes WHERE tenant_id = $1", tenant_id,
    )
    assert cnt == 3


# ──────────────────────────────────────────────────────────────────────────
# Test 4/5 — ABN uniqueness (active-only)
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_abn_active_tenants_fails(pg, cleanup_test_rows):
    abn = f"TEST{uuid.uuid4().hex[:7]}"
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    cleanup_test_rows.append(("core.tenants", "tenant_id IN ($1, $2)", (t1, t2)))

    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, abn, status)
           VALUES ($1, 'Test Abn First', $2, 'active')""",
        t1, abn,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pg.execute(
            """INSERT INTO core.tenants
                   (tenant_id, tenant_name, abn, status)
               VALUES ($1, 'Test Abn Second', $2, 'active')""",
            t2, abn,
        )


@pytest.mark.asyncio
async def test_duplicate_abn_after_archive_succeeds(pg, cleanup_test_rows):
    abn = f"TEST{uuid.uuid4().hex[:7]}"
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    cleanup_test_rows.append(("core.tenants", "tenant_id IN ($1, $2)", (t1, t2)))

    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, abn, status)
           VALUES ($1, 'Test Abn Reuse 1', $2, 'active')""",
        t1, abn,
    )
    await pg.execute(
        "UPDATE core.tenants SET status = 'archived' WHERE tenant_id = $1", t1,
    )
    # Second tenant with same ABN should now succeed because the partial
    # unique index only fires for active rows.
    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, abn, status)
           VALUES ($1, 'Test Abn Reuse 2', $2, 'active')""",
        t2, abn,
    )
    cnt = await pg.fetchval(
        "SELECT COUNT(*) FROM core.tenants WHERE abn = $1 AND status = 'active'",
        abn,
    )
    assert cnt == 1


# ──────────────────────────────────────────────────────────────────────────
# Test 6/7 — trial-request lifecycle
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trial_request_lifecycle_submitted_to_approved(pg, cleanup_test_rows):
    request_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    email = f"trial+{uuid.uuid4().hex[:8]}@test.local"

    cleanup_test_rows.append(("core.trial_requests", "request_id = $1", (request_id,)))
    cleanup_test_rows.append(("core.tenants", "tenant_id  = $1", (tenant_id,)))

    await pg.execute(
        """INSERT INTO core.trial_requests
           (request_id, org_name, jurisdiction,
            contact_first_name, contact_last_name, contact_email)
           VALUES ($1, 'Test Trial Co', 'ACT', 'Jane', 'Doe', $2)""",
        request_id, email,
    )
    await pg.execute(
        """INSERT INTO core.tenants
               (tenant_id, tenant_name, status, created_from_trial_id, is_test_data)
           VALUES ($1, 'Test Trial Co', 'active', $2, TRUE)""",
        tenant_id, request_id,
    )
    await pg.execute(
        """UPDATE core.trial_requests
           SET status            = 'approved',
               reviewed_at       = NOW(),
               created_tenant_id = $1
           WHERE request_id = $2""",
        tenant_id, request_id,
    )
    row = await pg.fetchrow(
        "SELECT status, created_tenant_id FROM core.trial_requests WHERE request_id = $1",
        request_id,
    )
    assert row["status"] == "approved"
    assert row["created_tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_duplicate_open_trial_for_same_email_fails(pg, cleanup_test_rows):
    """Idempotency backstop: only one ``submitted`` row per email."""
    email = f"dup+{uuid.uuid4().hex[:8]}@test.local"
    r1 = uuid.uuid4()
    r2 = uuid.uuid4()
    cleanup_test_rows.append(
        ("core.trial_requests", "request_id IN ($1, $2)", (r1, r2)),
    )

    await pg.execute(
        """INSERT INTO core.trial_requests
           (request_id, org_name, jurisdiction,
            contact_first_name, contact_last_name, contact_email)
           VALUES ($1, 'A', 'ACT', 'X', 'Y', $2)""",
        r1, email,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pg.execute(
            """INSERT INTO core.trial_requests
               (request_id, org_name, jurisdiction,
                contact_first_name, contact_last_name, contact_email)
               VALUES ($1, 'B', 'ACT', 'X', 'Y', $2)""",
            r2, email,
        )

    # Once first one is rejected the email becomes free again.
    await pg.execute(
        "UPDATE core.trial_requests SET status='rejected' WHERE request_id = $1",
        r1,
    )
    await pg.execute(
        """INSERT INTO core.trial_requests
           (request_id, org_name, jurisdiction,
            contact_first_name, contact_last_name, contact_email)
           VALUES ($1, 'C-after-rejection', 'ACT', 'X', 'Y', $2)""",
        r2, email,
    )
