"""Smoke tests for Alembic migration 0042 — Phase 1 operations foundations.

Requires a live Postgres connection via DATABASE_URL and that
``alembic upgrade head`` has already been applied.
"""
from __future__ import annotations

import os
import re
import uuid

import asyncpg
import pytest

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)
_RLS_BYPASS_SENTINEL = "00000000-0000-0000-0000-000000000000"

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres migration tests.",
)


@pytest.fixture(scope="module")
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(f"SET app.tenant_id = '{_RLS_BYPASS_SENTINEL}'")
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_phase1_schemas_exist(pg):
    rows = await pg.fetch(
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name IN ('access', 'ai_assist', 'sustainability')
        ORDER BY schema_name
        """
    )
    assert [r["schema_name"] for r in rows] == ["access", "ai_assist", "sustainability"]


@pytest.mark.asyncio
async def test_phase1_foundation_tables_exist(pg):
    rows = await pg.fetch(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE (table_schema, table_name) IN (
            ('ops', 'cases'),
            ('ops', 'service_requests'),
            ('documents', 'document_versions'),
            ('compliance', 'obligation_templates'),
            ('finance', 'utility_bills'),
            ('access', 'access_device_requests'),
            ('communications', 'communication_campaigns'),
            ('ai_assist', 'ai_assessments'),
            ('sustainability', 'building_sustainability_profiles')
        )
        ORDER BY table_schema, table_name
        """
    )
    got = {(r["table_schema"], r["table_name"]) for r in rows}
    expected = {
        ("ops", "cases"),
        ("ops", "service_requests"),
        ("documents", "document_versions"),
        ("compliance", "obligation_templates"),
        ("finance", "utility_bills"),
        ("access", "access_device_requests"),
        ("communications", "communication_campaigns"),
        ("ai_assist", "ai_assessments"),
        ("sustainability", "building_sustainability_profiles"),
    }
    assert got == expected


@pytest.mark.asyncio
async def test_phase1_indexes_exist(pg):
    rows = await pg.fetch(
        """
        SELECT schemaname, indexname
        FROM pg_indexes
        WHERE (schemaname, indexname) IN (
            ('ops', 'ops_cases_scheme_status_idx'),
            ('access', 'access_devices_scheme_inventory_ux'),
            ('communications', 'communication_campaigns_scheme_status_idx'),
            ('ai_assist', 'ai_assessments_scheme_status_idx'),
            ('finance', 'utility_anomalies_scheme_assigned_idx')
        )
        ORDER BY schemaname, indexname
        """
    )
    got = {(r["schemaname"], r["indexname"]) for r in rows}
    assert got == {
        ("ops", "ops_cases_scheme_status_idx"),
        ("access", "access_devices_scheme_inventory_ux"),
        ("communications", "communication_campaigns_scheme_status_idx"),
        ("ai_assist", "ai_assessments_scheme_status_idx"),
        ("finance", "utility_anomalies_scheme_assigned_idx"),
    }


@pytest.mark.asyncio
async def test_phase1_rls_policies_exist(pg):
    rows = await pg.fetch(
        """
        SELECT schemaname, tablename, policyname
        FROM pg_policies
        WHERE (schemaname, tablename, policyname) IN (
            ('ops', 'cases', 'tenant_isolation_ops_cases'),
            ('documents', 'document_versions', 'tenant_isolation_documents_document_versions'),
            ('compliance', 'compliance_events', 'tenant_isolation_compliance_compliance_events'),
            ('finance', 'utility_bills', 'tenant_isolation_finance_utility_bills'),
            ('access', 'access_device_requests', 'tenant_isolation_access_access_device_requests'),
            ('communications', 'communication_campaigns', 'tenant_isolation_communications_communication_campaigns'),
            ('ai_assist', 'ai_recommendations', 'tenant_isolation_ai_assist_ai_recommendations'),
            ('sustainability', 'energy_efficiency_projects', 'tenant_isolation_sustainability_energy_efficiency_projects')
        )
        ORDER BY schemaname, tablename
        """
    )
    got = {(r["schemaname"], r["tablename"], r["policyname"]) for r in rows}
    assert got == {
        ("ops", "cases", "tenant_isolation_ops_cases"),
        ("documents", "document_versions", "tenant_isolation_documents_document_versions"),
        ("compliance", "compliance_events", "tenant_isolation_compliance_compliance_events"),
        ("finance", "utility_bills", "tenant_isolation_finance_utility_bills"),
        ("access", "access_device_requests", "tenant_isolation_access_access_device_requests"),
        ("communications", "communication_campaigns", "tenant_isolation_communications_communication_campaigns"),
        ("ai_assist", "ai_recommendations", "tenant_isolation_ai_assist_ai_recommendations"),
        ("sustainability", "energy_efficiency_projects", "tenant_isolation_sustainability_energy_efficiency_projects"),
    }


@pytest.mark.asyncio
async def test_phase1_feature_toggles_exist(pg):
    """Verify Phase 1 ops feature toggles exist in core.feature_toggles.

    These toggles were seeded as is_enabled=False by migration 0042 but are
    now enabled globally — CLAUDE.md confirms all 164 feature_toggles rows
    are enabled as of 2026-05-30. The assertion below checks that all five
    toggles exist (i.e. migration 0042 ran) without asserting their current
    enabled state, which the platform seed legitimately updates over time.
    """
    rows = await pg.fetch(
        """
        SELECT feature_key, is_enabled
        FROM core.feature_toggles
        WHERE feature_key = ANY($1::text[])
        ORDER BY feature_key
        """,
        [
            "ops_case_management_pg_enabled",
            "communications_campaigns_pg_enabled",
            "access_device_lifecycle_pg_enabled",
            "ai_review_panel_enabled",
            "sustainability_assessments_pg_enabled",
        ],
    )
    # All five toggles must exist — migration 0042 is the invariant being tested.
    assert [r["feature_key"] for r in rows] == [
        "access_device_lifecycle_pg_enabled",
        "ai_review_panel_enabled",
        "communications_campaigns_pg_enabled",
        "ops_case_management_pg_enabled",
        "sustainability_assessments_pg_enabled",
    ]
    # is_enabled state is managed by the platform seed (all enabled globally per
    # CLAUDE.md — seeds/feature_toggles.py runs after migrations in production).
    # Verify is_enabled is a boolean rather than asserting a specific value.
    assert all(isinstance(r["is_enabled"], bool) for r in rows)


@pytest.mark.asyncio
async def test_case_status_transition_trigger_blocks_invalid_update(pg):
    tenant_id = uuid.uuid4()
    case_id = uuid.uuid4()

    await pg.execute(f"SET app.tenant_id = '{_RLS_BYPASS_SENTINEL}'")
    await pg.execute(
        """
        INSERT INTO core.tenants (tenant_id, tenant_name, status)
        VALUES ($1, 'Phase1 Ops Tenant', 'active')
        """,
        tenant_id,
    )
    await pg.execute(
        """
        INSERT INTO core.schemes
            (tenant_id, jurisdiction, scheme_number, scheme_name, status)
        VALUES ($1, 'ACT', $2, 'Phase1 Ops Scheme', 'active')
        """,
        tenant_id,
        f"PHASE1-{tenant_id.hex[:8]}",
    )

    try:
        await pg.execute(f"SET app.tenant_id = '{tenant_id}'")
        await pg.execute(
            """
            INSERT INTO ops.cases
                (id, tenant_id, scheme_id, title, category, status)
            SELECT $1, tenant_id, scheme_id, 'Phase1 test case', 'repair', 'new'
            FROM core.schemes
            WHERE tenant_id = $2
            """,
            case_id,
            tenant_id,
        )

        await pg.execute("UPDATE ops.cases SET status = 'triaged' WHERE id = $1", case_id)

        with pytest.raises(asyncpg.CheckViolationError):
            await pg.execute("UPDATE ops.cases SET status = 'closed' WHERE id = $1", case_id)
    finally:
        await pg.execute(f"SET app.tenant_id = '{tenant_id}'")
        await pg.execute("DELETE FROM ops.cases WHERE id = $1", case_id)
        await pg.execute(f"SET app.tenant_id = '{_RLS_BYPASS_SENTINEL}'")
        await pg.execute("DELETE FROM core.schemes WHERE tenant_id = $1", tenant_id)
        await pg.execute("DELETE FROM core.tenants WHERE tenant_id = $1", tenant_id)
