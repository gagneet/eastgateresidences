"""P1-T02: Coexistence mapping table for legacy Mongo IDs to Postgres IDs.

Revision ID: 0048_powerhouse_id_mapping
Revises: 0047_powerhouse_conversation
Create Date: 2026-06-02 01:05:00.000000
"""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — Adds coexistence ID mapping support for PG cutover.
# Layer: migration
# Data flow: Legacy Mongo IDs → powerhouse.legacy_id_mappings → PG UUID resolution during router/service reads (building-scoped).
# Related: backend/services/powerhouse_conversation_service.py
#          docs/architecture/conversation-engine.md

import re
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0048_powerhouse_id_mapping"
down_revision = "0047_powerhouse_conversation"
branch_labels = None
depends_on = None

TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _safe_identifier(value: str) -> str:
    """Allow only simple SQL identifiers used in this migration."""
    candidate = str(value).strip()
    if not IDENTIFIER_RE.fullmatch(candidate):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return candidate


def _create_rls_policy(schema_name: str, table_name: str) -> None:
    """Generated function header.

    Function: _create_rls_policy
    Path: backend/alembic/versions/0048_powerhouse_id_mapping.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    safe_schema = _safe_identifier(schema_name)
    safe_table = _safe_identifier(table_name)
    op.execute(f'ALTER TABLE "{safe_schema}"."{safe_table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{safe_schema}"."{safe_table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{safe_table}_tenant_isolation"
        ON "{safe_schema}"."{safe_table}"
        FOR ALL
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{TENANT_BYPASS_ID}'
        )
        WITH CHECK (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{TENANT_BYPASS_ID}'
        )
        """
    )


def _ensure_current_tenant_id_function() -> None:
    """Generated function header.

    Function: _ensure_current_tenant_id_function
    Path: backend/alembic/versions/0048_powerhouse_id_mapping.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regprocedure('core.current_tenant_id()') IS NULL THEN
                RAISE EXCEPTION 'Required function core.current_tenant_id() does not exist';
            END IF;
        END
        $$;
        """
    )


def _grant_schema(schema_name: str) -> None:
    """Generated function header.

    Function: _grant_schema
    Path: backend/alembic/versions/0048_powerhouse_id_mapping.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    safe_schema = _safe_identifier(schema_name)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'strataos_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {safe_schema} TO strataos_user';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {safe_schema} TO strataos_user';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA {safe_schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0048_powerhouse_id_mapping.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _ensure_current_tenant_id_function()
    op.execute("CREATE SCHEMA IF NOT EXISTS powerhouse")
    _grant_schema("powerhouse")

    op.create_table(
        "legacy_id_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("legacy_id", sa.String(255), nullable=False),
        sa.Column("pg_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default=sa.text("'mongo'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="powerhouse",
    )

    _create_rls_policy("powerhouse", "legacy_id_mappings")

    op.create_index(
        "ux_legacy_id_mappings_legacy",
        "legacy_id_mappings",
        ["tenant_id", "entity_type", "legacy_id"],
        unique=True,
        schema="powerhouse",
    )
    op.create_index(
        "ux_legacy_id_mappings_pg",
        "legacy_id_mappings",
        ["tenant_id", "entity_type", "pg_id"],
        unique=True,
        schema="powerhouse",
    )
    op.create_index(
        "idx_legacy_id_mappings_building_entity",
        "legacy_id_mappings",
        ["building_id", "entity_type"],
        schema="powerhouse",
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0048_powerhouse_id_mapping.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute('DROP TABLE IF EXISTS "powerhouse"."legacy_id_mappings" CASCADE')
