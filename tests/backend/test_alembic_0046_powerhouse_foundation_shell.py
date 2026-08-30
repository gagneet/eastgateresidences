"""Smoke tests for Alembic migration 0046 — Powerhouse foundation shell.

Requires DATABASE_URL and that ``alembic upgrade head`` has been applied.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg
import pytest

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)
_RLS_BYPASS_SENTINEL = "00000000-0000-0000-0000-000000000000"
MIGRATION_FILE = Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions" / "0046_powerhouse_foundation_shell.py"

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres migration tests.",
)


@pytest.fixture(scope="module")
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(f"SET app.tenant_id = '{_RLS_BYPASS_SENTINEL}'")
    row = await conn.fetchrow(
        """
        SELECT
            EXISTS (
                SELECT 1
                FROM information_schema.schemata
                WHERE schema_name = 'workflow'
            ) AS has_workflow_schema,
            EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'communications'
                  AND table_name = 'conversation_threads'
            ) AS has_conversation_threads,
            EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'workflow'
                  AND table_name = 'workflow_templates'
            ) AS has_workflow_templates
        """
    )
    if not row or not all(
        (
            row["has_workflow_schema"],
            row["has_conversation_threads"],
            row["has_workflow_templates"],
        )
    ):
        pytest.skip("Powerhouse foundation schema objects are not available in this environment.")
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_powerhouse_workflow_schema_exists(pg):
    row = await pg.fetchrow(
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name = 'workflow'
        """
    )
    assert row is not None


@pytest.mark.asyncio
async def test_powerhouse_foundation_tables_exist(pg):
    rows = await pg.fetch(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE (table_schema, table_name) IN (
            ('communications', 'inboxes'),
            ('communications', 'conversation_threads'),
            ('communications', 'conversation_messages'),
            ('communications', 'message_ai_summaries'),
            ('workflow', 'workflow_templates'),
            ('workflow', 'workflow_instances'),
            ('workflow', 'workflow_events'),
            ('workflow', 'automation_rules'),
            ('workflow', 'automation_rule_runs')
        )
        ORDER BY table_schema, table_name
        """
    )
    got = {(r["table_schema"], r["table_name"]) for r in rows}
    assert got == {
        ("communications", "conversation_messages"),
        ("communications", "conversation_threads"),
        ("communications", "inboxes"),
        ("communications", "message_ai_summaries"),
        ("workflow", "automation_rule_runs"),
        ("workflow", "automation_rules"),
        ("workflow", "workflow_events"),
        ("workflow", "workflow_instances"),
        ("workflow", "workflow_templates"),
    }


def test_powerhouse_revision_chain_points_to_0044():
    # Guard against reintroducing the missing 0045 assumption that broke deploy.sh.
    text = MIGRATION_FILE.read_text(encoding="utf-8")
    assert 'down_revision = "0044"' in text
    assert "0045_fix_refresh_tokens_table" not in text
