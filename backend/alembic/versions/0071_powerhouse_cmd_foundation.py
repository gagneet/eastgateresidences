"""Powerhouse P2A command foundation.

Revision ID: 0071_powerhouse_cmd_foundation
Revises: 0070_powerhouse_refinement
Create Date: 2026-07-20 18:00:00.000000
"""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — Adds PG command idempotency and continuity policy metadata.
# Layer: migration
# Data flow: Powerhouse command services → core.command_idempotency_records + core.audit_events + core.outbox.
# Note: revision id must stay <= 32 characters because the deployed
# core.alembic_version.version_num column is varchar(32).
# Related: backend/services/powerhouse_command_foundation.py
#          docs/features/powerhouse_postgres_native_implementation_part2.md

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0071_powerhouse_cmd_foundation"
down_revision = "0070_powerhouse_refinement"
branch_labels = None
depends_on = None

TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _rls(schema_name: str, table_name: str) -> None:
    """Enable tenant RLS using the shared app.tenant_id GUC."""
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


def _grant(schema_name: str) -> None:
    """Grant access for app roles only when those roles exist."""
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
    """Add command idempotency and continuity metadata used by P2A services."""
    op.create_table(
        "command_idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("command_scope", sa.Text(), nullable=False),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=True),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_status", sa.Text(), nullable=False, server_default=sa.text("'started'")),
        sa.Column("result_reference", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "tenant_id",
            "scheme_id",
            "command_scope",
            "command_type",
            "idempotency_key",
            name="uq_command_idempotency_scope_key",
        ),
        sa.CheckConstraint(
            "result_status IN ('started', 'created', 'updated', 'idempotent_replay', 'conflict', "
            "'invalid_transition', 'forbidden', 'not_found', 'dependency_unavailable', 'failed')",
            name="ck_command_idempotency_result_status",
        ),
        schema="core",
    )
    _rls("core", "command_idempotency_records")
    _grant("core")

    op.create_index(
        "idx_command_idempotency_aggregate",
        "command_idempotency_records",
        ["tenant_id", "aggregate_type", "aggregate_id"],
        schema="core",
        postgresql_where=sa.text("aggregate_id IS NOT NULL"),
    )
    op.create_index(
        "idx_command_idempotency_building",
        "command_idempotency_records",
        ["building_id", "command_type", "last_seen_at"],
        schema="core",
    )

    op.add_column(
        "domain_cutover_status",
        sa.Column("continuity_source", sa.Text(), nullable=True),
        schema="core",
    )
    op.add_column(
        "domain_cutover_status",
        sa.Column("continuity_policy", sa.Text(), nullable=True),
        schema="core",
    )
    op.create_check_constraint(
        "ck_cutover_status_continuity_source",
        "domain_cutover_status",
        "continuity_source IS NULL OR continuity_source IN ('mongo', 'none')",
        schema="core",
    )
    op.create_check_constraint(
        "ck_cutover_status_continuity_policy",
        "domain_cutover_status",
        "continuity_policy IS NULL OR continuity_policy IN ('classified_read_only', 'disabled')",
        schema="core",
    )

    op.add_column("outbox", sa.Column("correlation_id", sa.Text(), nullable=True), schema="core")
    op.add_column("outbox", sa.Column("causation_id", sa.Text(), nullable=True), schema="core")
    op.add_column("outbox", sa.Column("event_version", sa.Integer(), nullable=False, server_default=sa.text("1")), schema="core")
    op.add_column("outbox", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True), schema="core")
    op.add_column("outbox", sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True), schema="core")
    op.create_index(
        "idx_outbox_powerhouse_pending",
        "outbox",
        ["tenant_id", "created_at"],
        schema="core",
        postgresql_where=sa.text("published_at IS NULL AND dead_lettered_at IS NULL"),
    )


def downgrade() -> None:
    """Remove P2A command-foundation schema additions."""
    op.drop_index("idx_outbox_powerhouse_pending", table_name="outbox", schema="core")
    op.drop_column("outbox", "dead_lettered_at", schema="core")
    op.drop_column("outbox", "next_attempt_at", schema="core")
    op.drop_column("outbox", "event_version", schema="core")
    op.drop_column("outbox", "causation_id", schema="core")
    op.drop_column("outbox", "correlation_id", schema="core")

    op.drop_constraint("ck_cutover_status_continuity_policy", "domain_cutover_status", schema="core")
    op.drop_constraint("ck_cutover_status_continuity_source", "domain_cutover_status", schema="core")
    op.drop_column("domain_cutover_status", "continuity_policy", schema="core")
    op.drop_column("domain_cutover_status", "continuity_source", schema="core")

    op.drop_index("idx_command_idempotency_building", table_name="command_idempotency_records", schema="core")
    op.drop_index("idx_command_idempotency_aggregate", table_name="command_idempotency_records", schema="core")
    op.drop_table("command_idempotency_records", schema="core")
