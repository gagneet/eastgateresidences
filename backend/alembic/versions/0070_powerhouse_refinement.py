"""Powerhouse canonical refinement: core.legacy_entity_mappings, communications.thread_entity_links.

Revision ID: 0070_powerhouse_refinement
Revises: 0069_levy_run_type
Create Date: 2026-07-20 12:00:00.000000
"""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — Adds canonical communications relationship linking and robust generic ID mappings.
# Layer: migration
# Data flow: API writes / services → core.legacy_entity_mappings / communications.thread_entity_links (building-scoped).
# Related: backend/services/powerhouse_conversation_service.py
#          backend/alembic/versions/0046_powerhouse_foundation_shell.py
#          backend/alembic/versions/0048_powerhouse_id_mapping.py

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070_powerhouse_refinement"
down_revision = "0069_levy_run_type"
branch_labels = None
depends_on = None

TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _create_rls_policy(schema_name: str, table_name: str) -> None:
    """Create strict tenant-isolation policy for a table."""
    op.execute(f'ALTER TABLE "{schema_name}"."{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{schema_name}"."{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{table_name}_tenant_isolation"
        ON "{schema_name}"."{table_name}"
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


def _grant_schema(schema_name: str) -> None:
    """Grant schema/table privileges to application DB roles when roles exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'strataos_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema_name} TO strataos_user';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema_name} TO strataos_user';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema_name} TO app_user';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_readonly') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema_name} TO app_readonly';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    """Create legacy mapping and enhanced relationship tables with RLS policies."""
    # 1. core.legacy_entity_mappings
    op.create_table(
        "legacy_entity_mappings",
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("source_store", sa.Text(), nullable=False, server_default=sa.text("'mongo'")),
        sa.Column("source_collection", sa.Text(), nullable=False),
        sa.Column("source_entity_type", sa.Text(), nullable=False),
        sa.Column("source_entity_id", sa.Text(), nullable=False),
        sa.Column("target_schema", sa.Text(), nullable=False),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("migration_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )

    _create_rls_policy("core", "legacy_entity_mappings")
    _grant_schema("core")

    op.create_index(
        "ux_legacy_entity_mappings_source",
        "legacy_entity_mappings",
        ["tenant_id", "source_collection", "source_entity_id"],
        unique=True,
        schema="core",
    )
    op.create_index(
        "ux_legacy_entity_mappings_target",
        "legacy_entity_mappings",
        ["tenant_id", "target_schema", "target_table", "target_entity_id"],
        unique=True,
        schema="core",
    )
    op.create_index(
        "idx_legacy_entity_mappings_lookup",
        "legacy_entity_mappings",
        ["building_id", "source_entity_type", "source_entity_id"],
        schema="core",
    )

    # 2. communications.thread_entity_links
    op.create_table(
        "thread_entity_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False, server_default=sa.text("'linked'")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )

    _create_rls_policy("communications", "thread_entity_links")
    _grant_schema("communications")

    op.create_index(
        "idx_thread_entity_links_thread",
        "thread_entity_links",
        ["tenant_id", "thread_id"],
        schema="communications",
    )
    op.create_index(
        "idx_thread_entity_links_entity",
        "thread_entity_links",
        ["tenant_id", "entity_type", "entity_id"],
        schema="communications",
    )


def downgrade() -> None:
    """Drop refined tables."""
    op.execute('DROP TABLE IF EXISTS "communications"."thread_entity_links" CASCADE')
    op.execute('DROP TABLE IF EXISTS "core"."legacy_entity_mappings" CASCADE')
