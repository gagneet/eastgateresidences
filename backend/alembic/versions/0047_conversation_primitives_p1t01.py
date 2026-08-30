"""P1-T01: Postgres conversation schema (conversations, messages, participants, assignments, events).

Revision ID: 0047_powerhouse_conversation
Revises: 0046_powerhouse_foundation_shell
Create Date: 2026-06-01 14:00:00.000000
"""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — Adds PG-native conversation primitives with tenant isolation.
# Layer: migration
# Data flow: API/service writes → powerhouse.* tables (building-scoped).
# Related: backend/alembic/versions/0046_powerhouse_foundation_shell.py
#          docs/architecture/postgres-canonical-domain-model.md

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0047_powerhouse_conversation"
down_revision = "0046_powerhouse_foundation_shell"
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


def _ensure_current_tenant_id_function() -> None:
    """Fail fast if shared tenant helper function is missing."""
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
    """Grant schema/table privileges to application DB role when role exists."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'strataos_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema_name} TO strataos_user';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema_name} TO strataos_user';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    """Create conversation schema tables with RLS policies."""
    _ensure_current_tenant_id_function()
    op.execute("CREATE SCHEMA IF NOT EXISTS powerhouse")
    _grant_schema("powerhouse")

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'open'")),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="powerhouse",
    )
    _create_rls_policy("powerhouse", "conversations")
    op.create_index("idx_conversations_tenant_scheme", "conversations", ["tenant_id", "scheme_id"], schema="powerhouse")
    op.create_index("idx_conversations_status_type", "conversations", ["status", "type"], schema="powerhouse")
    op.create_index("idx_conversations_created_by", "conversations", ["created_by"], schema="powerhouse")

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(100), nullable=False),
        sa.Column("author_role", sa.String(50), nullable=True),
        sa.Column("message_type", sa.String(50), nullable=False, server_default=sa.text("'text'")),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="powerhouse",
    )
    _create_rls_policy("powerhouse", "messages")
    op.create_index("idx_messages_tenant_conversation", "messages", ["tenant_id", "conversation_id"], schema="powerhouse")
    op.create_index("idx_messages_author_id", "messages", ["author_id"], schema="powerhouse")
    op.create_index("idx_messages_created_at", "messages", ["created_at"], schema="powerhouse")
    op.create_foreign_key(
        "fk_messages_conversation",
        "messages",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="powerhouse",
        referent_schema="powerhouse",
        ondelete="CASCADE",
    )

    op.create_table(
        "participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="powerhouse",
    )
    _create_rls_policy("powerhouse", "participants")
    op.create_index("idx_participants_tenant_conversation", "participants", ["tenant_id", "conversation_id"], schema="powerhouse")
    op.create_index("idx_participants_user_id", "participants", ["user_id"], schema="powerhouse")
    op.create_index("idx_participants_status", "participants", ["status"], schema="powerhouse")
    op.create_unique_constraint(
        "uq_participants_conversation_user",
        "participants",
        ["conversation_id", "user_id"],
        schema="powerhouse",
    )
    op.create_foreign_key(
        "fk_participants_conversation",
        "participants",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="powerhouse",
        referent_schema="powerhouse",
        ondelete="CASCADE",
    )

    op.create_table(
        "assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_to_user_id", sa.String(100), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(100), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="powerhouse",
    )
    _create_rls_policy("powerhouse", "assignments")
    op.create_index("idx_assignments_tenant_conversation", "assignments", ["tenant_id", "conversation_id"], schema="powerhouse")
    op.create_index("idx_assignments_assigned_to", "assignments", ["assigned_to_user_id"], schema="powerhouse")
    op.create_index("idx_assignments_status_deadline", "assignments", ["status", "deadline"], schema="powerhouse")
    op.create_foreign_key(
        "fk_assignments_conversation",
        "assignments",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="powerhouse",
        referent_schema="powerhouse",
        ondelete="CASCADE",
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_user_id", sa.String(100), nullable=False),
        sa.Column("actor_role", sa.String(50), nullable=True),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="powerhouse",
    )
    _create_rls_policy("powerhouse", "events")
    op.create_index("idx_events_tenant_conversation", "events", ["tenant_id", "conversation_id"], schema="powerhouse")
    op.create_index("idx_events_event_type", "events", ["event_type"], schema="powerhouse")
    op.create_index("idx_events_actor_user_id", "events", ["actor_user_id"], schema="powerhouse")
    op.create_index("idx_events_created_at", "events", ["created_at"], schema="powerhouse")
    op.create_foreign_key(
        "fk_events_conversation",
        "events",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="powerhouse",
        referent_schema="powerhouse",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Drop conversation schema and all dependent objects."""
    op.execute("DROP SCHEMA IF EXISTS powerhouse CASCADE")
