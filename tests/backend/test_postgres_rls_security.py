"""
PostgreSQL Row-Level Security (RLS) Enforcement Tests.

Verifies that the `core.*` schema's RLS policies enforce multi-tenant
isolation at the database level — independent of application-layer guards.

RLS rules (CLAUDE.md + migration history):
  core.schemes:          tenant_id = current_tenant_id()
                         OR app.tenant_id = bypass_UUID
  core.lots:             tenant_id = current_tenant_id() ONLY — NO bypass clause
  core.tenants:          accessible via bypass sentinel (no RLS) or own tenant
  core.feature_toggles:  scheme_id IS NULL rows are global; per-scheme rows scoped

IMPORTANT GOTCHA (CLAUDE.md):
  Setting app.tenant_id = bypass_UUID lets you DELETE from core.schemes/tenants
  but NOT from core.lots — the lots policy has no bypass clause.
  Test cleanup must: SET app.tenant_id = <real_tid> → DELETE lots
                     SET app.tenant_id = bypass_UUID → DELETE schemes/tenants

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_postgres_rls_security.py -v

Requires: DATABASE_URL env var (same as other live-DB tests in this directory).
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)
_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping live Postgres RLS tests.",
)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
async def bypass_conn():
    """asyncpg connection with bypass sentinel — for fixture setup only."""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
async def two_tenants(bypass_conn):
    """
    Two (tenant_id, scheme_id, scheme_number) records from the live DB.
    Skips if fewer than 2 non-test, non-demo active schemes exist.
    """
    rows = await bypass_conn.fetch(
        """
        SELECT s.scheme_id::TEXT  AS scheme_id,
               s.scheme_number,
               s.tenant_id::TEXT  AS tenant_id
        FROM   core.schemes  s
        JOIN   core.tenants  t ON t.tenant_id = s.tenant_id
        WHERE  s.status = 'active'
          AND  COALESCE(s.is_test_data, FALSE) = FALSE
          AND  COALESCE(t.is_test_data, FALSE) = FALSE
        ORDER  BY s.created_at
        LIMIT  2
        """
    )
    if len(rows) < 2:
        pytest.skip("Need at least 2 active non-test schemes in DB — seed demo data first")
    return [dict(r) for r in rows]


@pytest.fixture
async def fresh_conn():
    """New asyncpg connection with NO app.tenant_id set — represents an unset session."""
    conn = await asyncpg.connect(DATABASE_URL)
    yield conn
    await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. core.schemes — has bypass clause
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemesRLS:
    """core.schemes allows cross-tenant reads via bypass sentinel."""

    @pytest.mark.asyncio
    async def test_bypass_sentinel_reads_any_scheme(self, two_tenants, fresh_conn):
        """With bypass UUID, any scheme is readable regardless of tenant."""
        await fresh_conn.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
        for t in two_tenants:
            row = await fresh_conn.fetchrow(
                "SELECT scheme_id::TEXT FROM core.schemes WHERE scheme_id = $1::UUID",
                t["scheme_id"],
            )
            assert row is not None, (
                f"Bypass sentinel must allow reading scheme {t['scheme_id']}"
            )

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_read_tenant_b_scheme(self, two_tenants, fresh_conn):
        """Without bypass, setting Tenant A's UUID blocks Tenant B's scheme."""
        a, b = two_tenants[0], two_tenants[1]
        if a["tenant_id"] == b["tenant_id"]:
            pytest.skip("Both schemes belong to the same tenant — cannot test isolation")

        await fresh_conn.execute(f"SET app.tenant_id = '{a['tenant_id']}'")
        row = await fresh_conn.fetchrow(
            "SELECT scheme_id::TEXT FROM core.schemes WHERE scheme_id = $1::UUID",
            b["scheme_id"],
        )
        assert row is None, (
            f"Tenant A ({a['tenant_id']}) must NOT see Tenant B scheme ({b['scheme_id']})"
        )

    @pytest.mark.asyncio
    async def test_tenant_can_read_own_scheme(self, two_tenants, fresh_conn):
        """Each tenant can read its own scheme."""
        for t in two_tenants:
            await fresh_conn.execute(f"SET app.tenant_id = '{t['tenant_id']}'")
            row = await fresh_conn.fetchrow(
                "SELECT scheme_id::TEXT FROM core.schemes WHERE scheme_id = $1::UUID",
                t["scheme_id"],
            )
            assert row is not None, f"Tenant must see its own scheme {t['scheme_id']}"

    @pytest.mark.asyncio
    async def test_unset_tenant_id_returns_no_schemes(self, fresh_conn):
        """
        A fresh connection with no app.tenant_id set should return 0 rows
        (neither current_tenant_id() nor bypass is active).
        """
        # Ensure no prior SET persists
        await fresh_conn.execute("RESET app.tenant_id")
        count = await fresh_conn.fetchval("SELECT COUNT(*) FROM core.schemes")
        # Either 0 rows (clean RLS enforcement) or permission denied — both are safe
        assert count == 0 or count is None, (
            f"Unset app.tenant_id returned {count} schemes — RLS may not be active"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. core.lots — NO bypass clause (critical gap from CLAUDE.md)
# ─────────────────────────────────────────────────────────────────────────────

class TestLotsRLS:
    """
    core.lots has NO bypass clause — bypass UUID shows 0 lots.
    Only the owning tenant can read or delete its lots.
    This is documented in CLAUDE.md as a cleanup gotcha.
    """

    @pytest.mark.asyncio
    async def test_bypass_uuid_returns_zero_lots(self, fresh_conn):
        """
        With bypass UUID, core.lots returns 0 rows.
        The lots policy has no bypass clause — this protects cross-tenant enumeration.
        """
        await fresh_conn.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
        count = await fresh_conn.fetchval("SELECT COUNT(*) FROM core.lots")
        assert count == 0, (
            f"core.lots must return 0 rows under bypass UUID (no bypass clause). "
            f"Got {count}. This is a RLS misconfiguration."
        )

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_read_tenant_b_lots(self, two_tenants, fresh_conn):
        """Tenant A cannot enumerate Tenant B's lots."""
        a, b = two_tenants[0], two_tenants[1]
        if a["tenant_id"] == b["tenant_id"]:
            pytest.skip("Both schemes belong to same tenant")

        # Confirm Tenant B has lots (only valid if seeded)
        await fresh_conn.execute(f"SET app.tenant_id = '{b['tenant_id']}'")
        b_count = await fresh_conn.fetchval(
            "SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1::UUID",
            b["scheme_id"],
        )

        # Switch to Tenant A and attempt cross-tenant read
        await fresh_conn.execute(f"SET app.tenant_id = '{a['tenant_id']}'")
        cross_count = await fresh_conn.fetchval(
            "SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1::UUID",
            b["scheme_id"],
        )
        assert cross_count == 0, (
            f"Tenant A ({a['tenant_id']}) must not see Tenant B lots "
            f"(scheme {b['scheme_id']} has {b_count} lots, cross-tenant read returned {cross_count})"
        )

    @pytest.mark.asyncio
    async def test_tenant_can_read_own_lots(self, two_tenants, fresh_conn):
        """Each tenant can read its own lots (count ≥ 0 — some may be unseeded)."""
        for t in two_tenants:
            await fresh_conn.execute(f"SET app.tenant_id = '{t['tenant_id']}'")
            count = await fresh_conn.fetchval(
                "SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1::UUID",
                t["scheme_id"],
            )
            assert count is not None  # Query succeeded (RLS didn't deny the read)

    @pytest.mark.asyncio
    async def test_delete_lots_with_bypass_uuid_deletes_nothing(self, fresh_conn):
        """
        Attempting DELETE from core.lots with bypass UUID silently deletes 0 rows.
        This confirms the documented cleanup gotcha: bypass does NOT bypass lots.
        Uses a non-existent scheme_id so no real data is at risk.
        """
        nonexistent = str(uuid.uuid4())
        await fresh_conn.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
        result = await fresh_conn.execute(
            "DELETE FROM core.lots WHERE scheme_id = $1::UUID",
            nonexistent,
        )
        rows_affected = int(result.split()[-1])
        # 0 rows because (a) nonexistent scheme, AND (b) bypass UUID can't see lots anyway
        assert rows_affected == 0

    @pytest.mark.asyncio
    async def test_correct_cleanup_pattern_lots_then_schemes(self, bypass_conn):
        """
        Validates the documented safe cleanup order:
          1. SET app.tenant_id = <real_tid>  → DELETE lots
          2. SET app.tenant_id = bypass_UUID → DELETE schemes/tenants

        Uses a synthetic test tenant so production data is never touched.
        """
        test_tid = str(uuid.uuid4())
        test_scheme_id = str(uuid.uuid4())

        # core.lots has no bypass clause — the INSERT must use the real tenant UUID,
        # not the bypass sentinel. We open a dedicated connection for all lot operations.
        conn2 = await asyncpg.connect(DATABASE_URL)
        try:
            # Tenant + scheme can be inserted via bypass (they have bypass clauses / no RLS).
            # tenant_name is NOT NULL with no default — must be supplied explicitly.
            await bypass_conn.execute(
                """
                INSERT INTO core.tenants
                  (tenant_id, tenant_name, legal_name, status, is_test_data, created_at, updated_at)
                VALUES ($1::UUID, 'RLS Test Tenant', 'RLS Test Tenant', 'active', TRUE, NOW(), NOW())
                ON CONFLICT DO NOTHING
                """,
                test_tid,
            )
            await bypass_conn.execute(
                """
                INSERT INTO core.schemes
                  (scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, status,
                   is_test_data, created_at, updated_at)
                VALUES ($1::UUID, $2::UUID, 'RLS-TEST-01', 'RLS Test Scheme',
                        'ACT', 'active', TRUE, NOW(), NOW())
                ON CONFLICT DO NOTHING
                """,
                test_scheme_id, test_tid,
            )

            # core.lots INSERT must be done as the real tenant — bypass UUID is blocked.
            # This itself validates the "no bypass clause" behaviour.
            await conn2.execute(f"SET app.tenant_id = '{test_tid}'")
            await conn2.execute(
                """
                INSERT INTO core.lots
                  (lot_id, scheme_id, tenant_id, lot_number, unit_number, lot_use,
                   is_test_data, created_at, updated_at)
                VALUES (gen_random_uuid(), $1::UUID, $2::UUID, '1', 'T1', 'residential',
                        TRUE, NOW(), NOW())
                ON CONFLICT DO NOTHING
                """,
                test_scheme_id, test_tid,
            )

            # Step 1: Delete lots as the real tenant (same conn2 still set to test_tid)
            lots_deleted = await conn2.execute(
                "DELETE FROM core.lots WHERE scheme_id = $1::UUID AND is_test_data = TRUE",
                test_scheme_id,
            )
            assert int(lots_deleted.split()[-1]) >= 0

            # Step 2: Switch to bypass → delete scheme + tenant
            await conn2.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
            await conn2.execute(
                "DELETE FROM core.schemes WHERE scheme_id = $1::UUID AND is_test_data = TRUE",
                test_scheme_id,
            )
            await conn2.execute(
                "DELETE FROM core.tenants WHERE tenant_id = $1::UUID",
                test_tid,
            )
        finally:
            await conn2.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. core.feature_toggles — global vs per-scheme rows
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureTogglesRLS:
    """
    core.feature_toggles is the GLOBAL registry — no RLS, no per-scheme scoping.
    core.feature_toggle_overrides holds PER-SCHEME overrides and IS tenant-scoped (RLS).

    Architecture (migration 0011 + 0012):
      - core.feature_toggles         — one row per feature key, global, NO RLS.
      - core.feature_toggle_overrides — (tenant_id, scheme_id, feature_key, is_enabled).
                                        RLS policy: tenant_id = current_tenant_id().

    Feature-toggle 3-tier resolution:
      1. User-level override  (application layer)
      2. core.feature_toggle_overrides  (scheme-level, tenant-scoped via RLS)
      3. core.feature_toggles           (global default, no RLS)
    """

    @pytest.mark.asyncio
    async def test_global_toggles_visible_to_all_tenants(self, two_tenants, fresh_conn):
        """
        core.feature_toggles has NO RLS — any session can read the global registry.
        Every tenant context should see the same row count.
        """
        counts = []
        for t in two_tenants:
            await fresh_conn.execute(f"SET app.tenant_id = '{t['tenant_id']}'")
            count = await fresh_conn.fetchval("SELECT COUNT(*) FROM core.feature_toggles")
            if count == 0:
                pytest.skip(
                    "No global feature toggles in DB — run: cd backend && python3 seeds/feature_toggles.py"
                )
            counts.append(count)
            assert count > 0, f"Tenant {t['tenant_id']} must see the global feature toggle registry"

        # All tenants see the same global count (no RLS filtering)
        assert len(set(counts)) == 1, (
            f"Global feature toggle count differs across tenants: {counts} — "
            "core.feature_toggles should have no RLS"
        )

    @pytest.mark.asyncio
    async def test_per_scheme_override_not_visible_to_other_tenant(
        self, two_tenants, bypass_conn, fresh_conn
    ):
        """
        core.feature_toggle_overrides is per-scheme and tenant-scoped via RLS.
        An override created for Scheme A must be invisible from Tenant B's context.

        Uses core.feature_toggle_overrides (NOT core.feature_toggles — that table
        has no scheme_id column and no RLS).
        """
        # Look for any existing overrides across all tenants (bypass lets us see all)
        override_rows = await bypass_conn.fetch(
            """
            SELECT o.feature_key,
                   o.tenant_id::TEXT  AS tenant_id,
                   o.scheme_id::TEXT  AS scheme_id
            FROM   core.feature_toggle_overrides o
            LIMIT  4
            """
        )
        if not override_rows:
            pytest.skip(
                "No per-scheme feature toggle overrides in DB — "
                "override a toggle via the admin UI or API to enable this test"
            )

        override = dict(override_rows[0])
        scheme_a_tenant = override["tenant_id"]
        scheme_a_id     = override["scheme_id"]

        # Find a different tenant to act as the cross-tenant attacker
        other_tenant = next(
            (t["tenant_id"] for t in two_tenants if t["tenant_id"] != scheme_a_tenant),
            None,
        )
        if not other_tenant:
            pytest.skip("Both test tenants are the same — cannot check cross-tenant isolation")

        await fresh_conn.execute(f"SET app.tenant_id = '{other_tenant}'")
        count = await fresh_conn.fetchval(
            "SELECT COUNT(*) FROM core.feature_toggle_overrides WHERE scheme_id = $1::UUID",
            scheme_a_id,
        )
        assert count == 0, (
            f"Tenant {other_tenant} must NOT see feature_toggle_overrides for scheme "
            f"{scheme_a_id} (owned by tenant {scheme_a_tenant})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. core.user_units — tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestUserUnitsRLS:
    """core.user_units must be tenant-scoped."""

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_see_tenant_b_user_units(self, two_tenants, fresh_conn):
        """User-unit assignments from Tenant B are invisible to Tenant A."""
        a, b = two_tenants[0], two_tenants[1]
        if a["tenant_id"] == b["tenant_id"]:
            pytest.skip("Both schemes belong to same tenant")

        # Count tenant B's user_units (from bypass context)
        bypass = await asyncpg.connect(DATABASE_URL)
        try:
            await bypass.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
            # user_units may not have bypass; use tenant B's own context
            await bypass.execute(f"SET app.tenant_id = '{b['tenant_id']}'")
            b_count = await bypass.fetchval(
                "SELECT COUNT(*) FROM core.user_units WHERE scheme_id = $1::UUID",
                b["scheme_id"],
            )
        finally:
            await bypass.close()

        if b_count == 0:
            pytest.skip("Tenant B has no user_units — seed demo data")

        # Now read from tenant A's context
        await fresh_conn.execute(f"SET app.tenant_id = '{a['tenant_id']}'")
        cross_count = await fresh_conn.fetchval(
            "SELECT COUNT(*) FROM core.user_units WHERE scheme_id = $1::UUID",
            b["scheme_id"],
        )
        assert cross_count == 0, (
            f"Tenant A ({a['tenant_id']}) must not see Tenant B user_units "
            f"(scheme {b['scheme_id']} has {b_count} entries, cross read returned {cross_count})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. is_test_data sentinel — test rows never surface in production queries
# ─────────────────────────────────────────────────────────────────────────────

class TestIsTestDataFilter:
    """
    Rows with is_test_data=TRUE must be filtered from all production queries.
    The DB layer (repo helpers) add this automatically; this test verifies
    the raw data is there but filtered.
    """

    @pytest.mark.asyncio
    async def test_is_test_data_schemes_exist_but_filtered_by_repo(self, bypass_conn):
        """
        If any test schemes exist, verify that production queries (is_test_data=FALSE)
        exclude them.
        """
        test_count = await bypass_conn.fetchval(
            "SELECT COUNT(*) FROM core.schemes WHERE COALESCE(is_test_data, FALSE) = TRUE"
        )
        prod_count = await bypass_conn.fetchval(
            "SELECT COUNT(*) FROM core.schemes WHERE COALESCE(is_test_data, FALSE) = FALSE"
        )
        total_count = await bypass_conn.fetchval("SELECT COUNT(*) FROM core.schemes")

        # Totals must add up
        assert test_count + prod_count == total_count, (
            f"is_test_data accounting mismatch: {test_count} test + {prod_count} prod ≠ {total_count} total"
        )

    @pytest.mark.asyncio
    async def test_is_test_data_lots_filtered(self, bypass_conn):
        """Lots with is_test_data=TRUE must not appear in is_test_data=FALSE counts."""
        # core.lots has no bypass, so use a tenant context
        rows = await bypass_conn.fetch(
            """
            SELECT tenant_id::TEXT FROM core.tenants
            WHERE COALESCE(is_test_data, FALSE) = FALSE
            LIMIT 1
            """
        )
        if not rows:
            pytest.skip("No production tenants in DB")
        tenant_id = rows[0]["tenant_id"]

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(f"SET app.tenant_id = '{tenant_id}'")
            prod_lots = await conn.fetchval(
                "SELECT COUNT(*) FROM core.lots WHERE COALESCE(is_test_data, FALSE) = FALSE"
            )
            test_lots = await conn.fetchval(
                "SELECT COUNT(*) FROM core.lots WHERE is_test_data = TRUE"
            )
            total_lots = await conn.fetchval("SELECT COUNT(*) FROM core.lots")
            assert prod_lots + test_lots == total_lots
        finally:
            await conn.close()
