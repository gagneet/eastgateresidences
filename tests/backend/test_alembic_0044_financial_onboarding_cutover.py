"""Smoke tests for Alembic migration 0044 — financial onboarding cutover."""
from __future__ import annotations

import os
import re

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
async def test_financial_onboarding_tables_exist(pg):
    rows = await pg.fetch(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE (table_schema, table_name) IN (
            ('finance', 'evidence_documents'),
            ('finance', 'financial_cutover_config'),
            ('finance', 'financial_onboarding_audit')
        )
        ORDER BY table_schema, table_name
        """
    )
    assert {(row["table_schema"], row["table_name"]) for row in rows} == {
        ("finance", "evidence_documents"),
        ("finance", "financial_cutover_config"),
        ("finance", "financial_onboarding_audit"),
    }


@pytest.mark.asyncio
async def test_financial_onboarding_column_exists(pg):
    row = await pg.fetchrow(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'finance'
          AND table_name = 'journal_entries'
          AND column_name = 'evidence_document_id'
        """
    )
    assert row is not None, "finance.journal_entries.evidence_document_id column missing"
    assert row["data_type"] == "uuid"
    assert row["is_nullable"] == "YES"


@pytest.mark.asyncio
async def test_financial_onboarding_indexes_exist(pg):
    rows = await pg.fetch(
        """
        SELECT schemaname, indexname
        FROM pg_indexes
        WHERE (schemaname, indexname) IN (
            ('finance', 'evidence_documents_scheme_idx'),
            ('finance', 'evidence_documents_building_idx'),
            ('finance', 'journal_entries_evidence_document_idx'),
            ('finance', 'financial_cutover_config_source_idx')
        )
        ORDER BY schemaname, indexname
        """
    )
    assert {(row["schemaname"], row["indexname"]) for row in rows} == {
        ("finance", "evidence_documents_scheme_idx"),
        ("finance", "evidence_documents_building_idx"),
        ("finance", "journal_entries_evidence_document_idx"),
        ("finance", "financial_cutover_config_source_idx"),
    }
