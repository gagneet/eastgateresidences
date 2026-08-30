"""
tests/backend/test_rls_coverage.py

Sweeps EVERY tenant-owned table in the database and fails if any of them is
missing row-level security.

## Why this file was rewritten (2026-08-23)

The previous version hardcoded three Phase F tables (core.saga_runs,
core.saga_steps, core.shadow_read_divergences). It was an allowlist, not a
coverage test, so it passed while 22 tenant-owned tables — including all eight
`governance.*` tables holding the EC voting and decision record — sat with RLS
disabled and zero policies. Migrations 0033/0034/0035 had enabled RLS on 18 of
them; it was turned off out-of-band some time later and nothing noticed.

The point of this test is that it is impossible to add a tenant-owned table, or
to disable RLS on an existing one, without the suite going red.

"Tenant-owned" is defined structurally: the table has a `tenant_id` column.
Exemptions are explicit, individually justified, and asserted to still exist —
so an exemption cannot silently outlive the table it was written for.

Requires a live Postgres. Skips cleanly when DATABASE_URL is unset.
"""

import os

import pytest
from sqlalchemy import text

from db_postgres.engine import get_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="RLS coverage requires a live Postgres (DATABASE_URL unset)",
)


# Tables that legitimately carry a tenant_id but must NOT have a strict
# tenant_isolation policy. Every entry needs a reason; nothing goes here to
# make the test pass.
EXEMPT: dict[str, str] = {
    # The tenant registry itself. Scoping it to a tenant would make it
    # impossible to resolve a tenant before context is established. Documented
    # in CLAUDE.md as intentionally un-scoped.
    "core.tenants": "tenant registry; must be readable before tenant context exists",
}

# Tenant-owned tables whose tenant_id is NULLABLE, where NULL means "global".
# A strict `tenant_id = current_tenant_id()` policy would hide those global
# rows, so each needs a policy that also admits `tenant_id IS NULL` plus an
# audit of its live callers. Tracked for a follow-up change; listed here so the
# sweep reports them as known-pending rather than silently ignoring them.
PENDING_NULLABLE_TENANT: dict[str, str] = {
    "analytics.bi_alert_rules": "nullable tenant_id (global alert rules)",
    "analytics.login_audit": "nullable tenant_id",
    "core.management_entities": "nullable tenant_id; live: routers/management_hierarchy.py",
    "core.onboarding_sessions": "nullable tenant_id; live: routers/onboarding.py",
}


async def _tenant_owned_tables() -> list[dict]:
    """Every table with a tenant_id column, plus its RLS state."""
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text("""
                SELECT n.nspname                AS schema_name,
                       c.relname                AS table_name,
                       c.relrowsecurity         AS rls_enabled,
                       c.relforcerowsecurity    AS rls_forced,
                       col.is_nullable          AS tenant_nullable,
                       (SELECT count(*)
                          FROM pg_policies p
                         WHERE p.schemaname = n.nspname
                           AND p.tablename  = c.relname) AS policy_count
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  JOIN information_schema.columns col
                    ON col.table_schema = n.nspname
                   AND col.table_name   = c.relname
                   AND col.column_name  = 'tenant_id'
                 WHERE c.relkind = 'r'
                   AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
                 ORDER BY 1, 2
            """)
        )
        return [dict(row._mapping) for row in result]


def _qualified(row: dict) -> str:
    return f"{row['schema_name']}.{row['table_name']}"


@pytest.mark.asyncio
async def test_every_tenant_owned_table_has_rls_enabled():
    """No tenant-owned table may have RLS disabled."""
    rows = await _tenant_owned_tables()
    assert rows, "no tenant-owned tables found — is this pointed at the right database?"

    offenders = [
        _qualified(r) for r in rows
        if not r["rls_enabled"]
        and _qualified(r) not in EXEMPT
        and _qualified(r) not in PENDING_NULLABLE_TENANT
    ]
    assert not offenders, (
        "RLS is DISABLED on tenant-owned table(s):\n  "
        + "\n  ".join(offenders)
        + "\n\nEnable it in a migration (see 0091_restore_tenant_rls). Do NOT add "
          "an exemption to silence this — a query returning 0 rows means the "
          "caller never set app.tenant_id (footgun #8), not that RLS is wrong."
    )


@pytest.mark.asyncio
async def test_every_tenant_owned_table_forces_rls():
    """ENABLE without FORCE is decorative: the app owns these tables.

    The application connects as the table owner (strataos_user), and a table
    owner bypasses RLS unless FORCE ROW LEVEL SECURITY is set. A table that is
    ENABLEd but not FORCEd gives false assurance.
    """
    rows = await _tenant_owned_tables()
    offenders = [
        _qualified(r) for r in rows
        if r["rls_enabled"] and not r["rls_forced"]
        and _qualified(r) not in EXEMPT
        and _qualified(r) not in PENDING_NULLABLE_TENANT
    ]
    assert not offenders, (
        "RLS is ENABLED but not FORCED on:\n  " + "\n  ".join(offenders)
        + "\n\nThe app owns these tables, so RLS does not apply to it without FORCE."
    )


@pytest.mark.asyncio
async def test_every_tenant_owned_table_has_a_policy():
    """RLS with no policy denies everything; RLS with a policy isolates tenants."""
    rows = await _tenant_owned_tables()
    offenders = [
        _qualified(r) for r in rows
        if r["policy_count"] == 0
        and _qualified(r) not in EXEMPT
        and _qualified(r) not in PENDING_NULLABLE_TENANT
    ]
    assert not offenders, (
        "No RLS policy on tenant-owned table(s):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.asyncio
async def test_governance_tables_are_isolated():
    """Explicit regression guard for the 2026-08-23 finding.

    The entire governance schema — EC membership, decisions, motions, votes,
    attendance, by-laws — was found with RLS disabled. These tables are the
    legal record that the ACT authorisation model checks authority against, so
    they get a named test rather than relying on the generic sweep alone.
    """
    rows = {_qualified(r): r for r in await _tenant_owned_tables()}
    governance = {k: v for k, v in rows.items() if k.startswith("governance.")}
    assert governance, "no governance.* tenant-owned tables found"

    for name, row in sorted(governance.items()):
        assert row["rls_enabled"], f"{name}: RLS disabled"
        assert row["rls_forced"], f"{name}: RLS not forced"
        assert row["policy_count"] > 0, f"{name}: no RLS policy"


@pytest.mark.asyncio
async def test_exemptions_still_refer_to_real_tables():
    """An exemption must not outlive the table it was written for."""
    live = {_qualified(r) for r in await _tenant_owned_tables()}
    stale = sorted((EXEMPT.keys() | PENDING_NULLABLE_TENANT.keys()) - live)
    assert not stale, (
        "Exemption(s) name a table that no longer has a tenant_id column "
        f"(or no longer exists): {stale}. Remove the exemption."
    )
