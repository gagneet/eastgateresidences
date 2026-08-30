"""0091 — Restore tenant RLS on governance, compliance, communications and core.tenancy_periods.

## Why this migration exists

Migrations 0033 (governance), 0034 (compliance extensions) and 0035
(communications) each ended their ``upgrade()`` with the standard block:

    ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
    ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_<schema>_<t> ON <t>
        USING (tenant_id = core.current_tenant_id());

A 2026-08-23 authorisation audit found those 18 tables live with
``relrowsecurity = false`` and zero policies, while the database reports
alembic head ``0090_align_gl_fund_ids`` — far past all three migrations.

The migration bodies demonstrably DID run: every index they create
(``agm_records_scheme_date_idx``, ``by_laws_acks_by_law_lot_idx``,
``privacy_consents_party_type_idx``, ``insurance_policies_scheme_expiry_idx``,
…) is present, and ``strataos_user`` owns the tables, so the ``ALTER TABLE``
statements could not have failed on privileges. Nothing in this repository
disables RLS outside a ``downgrade()``. The only consistent explanation is an
out-of-band ``DISABLE ROW LEVEL SECURITY`` — most likely someone clearing the
documented "RLS returns 0 rows with no tenant context" footgun
(rules/post-compact-critical.md footgun #8) and never restoring it.

This migration restores the intended state and is written to be idempotent, so
it is safe to re-run if the same thing happens again.
``tests/backend/test_rls_coverage.py`` is rewritten alongside it into a real
sweep over every tenant-owned table, so a future out-of-band disable fails CI
instead of going unnoticed for months.

## Why FORCE matters

The application connects as ``strataos_user``, which OWNS these tables. A table
owner bypasses RLS unless ``FORCE ROW LEVEL SECURITY`` is set. ``ENABLE``
without ``FORCE`` would be decorative here.

## Scope and blast radius

All 18 tables were verified EMPTY (0 rows) at the time of writing, and no
application read path serves them from Postgres. ``governance.*`` is touched
only by ``services/governance_bootstrap_service.py``, a one-way Mongo→PG
bootstrap that already calls ``db_postgres.session.set_tenant()`` before every
governance write, so it keeps working under FORCE RLS.

Four further tenant-owned tables are DELIBERATELY NOT included, because each
has a nullable ``tenant_id`` (rows with ``tenant_id IS NULL`` are global by
design and a strict policy would hide them) and/or a live code path that must
be audited for tenant context first:

    analytics.bi_alert_rules      nullable, 10 rows
    analytics.login_audit         nullable
    core.management_entities      nullable, live: routers/management_hierarchy.py
    core.onboarding_sessions      nullable, 5 rows, live: routers/onboarding.py

``core.tenants`` is excluded permanently: it is the tenant registry itself and
is intentionally un-scoped (see CLAUDE.md).
"""
from __future__ import annotations

from alembic import op

revision = "0091_restore_tenant_rls"
down_revision = "0090_align_gl_fund_ids"
branch_labels = None
depends_on = None


# (schema, table) — every entry has a NOT NULL tenant_id.
_TABLES: list[tuple[str, str]] = [
    ("governance", "agm_records"),
    ("governance", "agm_motions"),
    ("governance", "agm_votes"),
    ("governance", "agm_attendance"),
    ("governance", "ec_members"),
    ("governance", "decisions"),
    ("governance", "by_laws"),
    ("governance", "by_laws_acks"),
    ("compliance", "insurance_policies"),
    ("compliance", "data_breach_log"),
    ("compliance", "whs_incidents"),
    ("compliance", "whs_inductions"),
    ("compliance", "whs_swms"),
    ("compliance", "privacy_consents"),
    ("communications", "announcements"),
    ("communications", "letters_log"),
    ("communications", "notices"),
    ("core", "tenancy_periods"),
]


def upgrade() -> None:
    """Enable, force and police tenant isolation on 18 tenant-owned tables.

    Idempotent: the policy is dropped-if-exists before creation, and ENABLE /
    FORCE are no-ops when already set.
    """
    for schema, table in _TABLES:
        policy = f"tenant_isolation_{schema}_{table}"
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        # Drop first so a partially-restored table converges on the canonical
        # policy rather than erroring on a duplicate name.
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {schema}.{table}")
        # Matches the convention used by every other tenant_isolation_* policy
        # in this database: FOR ALL, USING only. Postgres falls back to the
        # USING expression as the WITH CHECK for INSERT/UPDATE, so writes are
        # constrained to the caller's tenant as well as reads.
        op.execute(
            f"CREATE POLICY {policy} ON {schema}.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Return the 18 tables to their (unsafe) pre-0091 state.

    Provided for alembic symmetry only. Running this re-opens cross-tenant
    visibility on the EC voting and decision record — do not run it to work
    around a "query returns 0 rows" problem. That symptom means the caller has
    not set ``app.tenant_id``; fix the caller.
    """
    for schema, table in reversed(_TABLES):
        policy = f"tenant_isolation_{schema}_{table}"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {schema}.{table}")
        op.execute(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY")
