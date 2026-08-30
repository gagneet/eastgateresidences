"""Live database contract tests for GAP-PERF schema/read-model work.

These tests read actual MongoDB/PostgreSQL metadata and counts when local
database env vars are available. They are intentionally read-only.

Run locally:
  backend/venv/bin/python3 -m pytest tests/backend/test_gap_perf_live_database_contract.py -q

To turn known current-state index gaps into hard failures:
  GAP_PERF_LIVE_DB_STRICT=1 backend/venv/bin/python3 -m pytest tests/backend/test_gap_perf_live_database_contract.py -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

load_dotenv(BACKEND / ".env", override=True)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or not os.environ.get("MONGO_URL"),
    reason="GAP-PERF live DB contract requires DATABASE_URL and MONGO_URL",
)

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from pymongo import AsyncMongoClient  # noqa: E402


REQUIRED_SCHEMAS = {
    "access",
    "ai_assist",
    "analytics",
    "communications",
    "compliance",
    "core",
    "documents",
    "finance",
    "governance",
    "modules",
    "ops",
    "powerhouse",
    "sustainability",
    "workflow",
}

POSTGRES_MARKET_TABLES = {
    "ops.cases",
    "ops.case_events",
    "ops.case_links",
    "ops.task_assignments",
    "communications.communication_campaigns",
    "communications.communication_delivery_events",
    "documents.documents",
    "documents.document_versions",
    "documents.document_audit_events",
    "access.access_devices",
    "access.access_device_requests",
    "analytics.fact_building_health_snapshot",
    "analytics.fact_work_order",
    "finance.portal_balance_snapshots",
}

MONGO_BUILDING_INDEXED_COLLECTIONS = {
    "units",
    "unit_levy_ledger",
    "levy_payments",
    "annual_levies",
    "demo_bank_transactions",
    "work_orders",
    "maintenance_requests",
    "documents",
    "activities",
    "strata_financials",
}

MONGO_KNOWN_INDEX_GAPS = {
    "workflow_requests": "dashboard/request queue list and triage reads",
    "user_notifications": "unread/history notification reads",
    "match_review_queue": "finance matching review queue",
    "building_summaries": "dashboard building summary lookup",
}

EAST_GATE_BUILDING_ID = "13195"


async def _mongo_db():
    client = AsyncMongoClient(os.environ["MONGO_URL"])
    try:
        yield client[os.environ.get("DB_NAME", "strataos_production")]
    finally:
        close = client.close()
        if hasattr(close, "__await__"):
            await close


@pytest.mark.asyncio
async def test_live_postgres_market_schema_footprint_matches_current_server():
    async with async_session_context() as session:
        schemas = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT schema_name
                          FROM information_schema.schemata
                         WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
                        """
                    )
                )
            ).fetchall()
        }
        assert REQUIRED_SCHEMAS.issubset(schemas)

        relation_count = (
            await session.execute(
                text(
                    """
                    SELECT count(*)
                      FROM information_schema.tables
                     WHERE table_schema = ANY(:schemas)
                    """
                ),
                {"schemas": sorted(schemas)},
            )
        ).scalar_one()
        assert relation_count >= 230


@pytest.mark.asyncio
async def test_live_postgres_future_market_tables_are_rls_protected():
    async with async_session_context() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT n.nspname AS schema_name,
                           c.relname AS table_name,
                           c.relrowsecurity AS rls_enabled,
                           c.relforcerowsecurity AS rls_forced,
                           EXISTS (
                             SELECT 1
                               FROM information_schema.columns col
                              WHERE col.table_schema = n.nspname
                                AND col.table_name = c.relname
                                AND col.column_name = 'tenant_id'
                           ) AS has_tenant_id,
                           EXISTS (
                             SELECT 1
                               FROM information_schema.columns col
                              WHERE col.table_schema = n.nspname
                                AND col.table_name = c.relname
                                AND col.column_name = 'scheme_id'
                           ) AS has_scheme_id
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE c.relkind = 'r'
                       AND (n.nspname || '.' || c.relname) = ANY(:tables)
                    """
                ),
                {"tables": sorted(POSTGRES_MARKET_TABLES)},
            )
        ).fetchall()
        found = {f"{row.schema_name}.{row.table_name}": row for row in rows}

        missing = sorted(POSTGRES_MARKET_TABLES - found.keys())
        assert not missing, f"missing expected market-foundation tables: {missing}"

        offenders = []
        for name, row in found.items():
            if not row.has_tenant_id or not row.has_scheme_id:
                offenders.append(f"{name}: missing tenant_id/scheme_id")
            if not row.rls_enabled or not row.rls_forced:
                offenders.append(f"{name}: RLS not enabled+forced")
        assert not offenders, "\n".join(offenders)


@pytest.mark.asyncio
async def test_live_east_gate_finance_data_is_present_in_both_stores():
    async with async_session_context() as session:
        scheme = await resolve_scheme_context(EAST_GATE_BUILDING_ID)
        assert scheme, "East Gate scheme context not found"
        await set_tenant(session, str(scheme["tenant_id"]))

        pg_counts = {}
        for qualified in (
            "finance.bank_transactions",
            "finance.receipts",
            "finance.receipt_allocations",
            "finance.levy_items",
            "finance.journal_entries",
            "finance.journal_lines",
        ):
            pg_counts[qualified] = (
                await session.execute(text(f"SELECT count(*) FROM {qualified}"))
            ).scalar_one()
        assert pg_counts["finance.bank_transactions"] >= 3900
        assert pg_counts["finance.receipts"] >= 2200
        assert pg_counts["finance.journal_lines"] >= 12000

    async for db in _mongo_db():
        assert await db.unit_levy_ledger.count_documents({"building_id": EAST_GATE_BUILDING_ID}) >= 500
        assert await db.levy_payments.count_documents({"building_id": EAST_GATE_BUILDING_ID}) >= 2000
        assert await db.demo_bank_transactions.count_documents({"building_id": EAST_GATE_BUILDING_ID}) >= 7000
        assert await db.units.count_documents({"building_id": EAST_GATE_BUILDING_ID}) == 87


async def _building_id_leading_indexes(db, collection: str) -> list[str]:
    indexes = await db[collection].index_information()
    result = []
    for name, spec in indexes.items():
        keys = spec.get("key") or []
        if keys and keys[0][0] == "building_id":
            result.append(name)
    return result


@pytest.mark.asyncio
async def test_live_mongo_current_hot_collections_have_building_id_leading_indexes():
    async for db in _mongo_db():
        names = set(await db.list_collection_names())
        missing = []
        for collection in sorted(MONGO_BUILDING_INDEXED_COLLECTIONS):
            if collection not in names:
                missing.append(f"{collection}: collection missing")
                continue
            if not await _building_id_leading_indexes(db, collection):
                missing.append(f"{collection}: no building_id-leading index")
        assert not missing, "\n".join(missing)


@pytest.mark.asyncio
async def test_live_mongo_known_gap_collections_get_building_id_leading_indexes():
    async for db in _mongo_db():
        names = set(await db.list_collection_names())
        missing = []
        for collection, reason in sorted(MONGO_KNOWN_INDEX_GAPS.items()):
            if collection not in names:
                missing.append(f"{collection}: collection missing for {reason}")
                continue
            if not await _building_id_leading_indexes(db, collection):
                missing.append(f"{collection}: no building_id-leading index for {reason}")

        if missing and os.environ.get("GAP_PERF_LIVE_DB_STRICT") != "1":
            pytest.xfail("Known live index gaps from 2026-08-27:\n" + "\n".join(missing))
        assert not missing, "\n".join(missing)
