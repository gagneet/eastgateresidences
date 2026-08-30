"""Powerhouse foundation shell tables for conversations and workflow.

Revision ID: 0046_powerhouse_foundation_shell
Revises: 0044_financial_onboarding_cutover
Create Date: 2026-06-01 13:05:00.000000
"""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — Adds PG shell tables for communications/workflow domains.
# Layer: migration
# Data flow: API shell contracts → communications.* / workflow.* PG shells (building-scoped).
# Related: backend/alembic/versions/0047_conversation_primitives_p1t01.py
#          docs/architecture/conversation-engine.md

from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# 0045 never existed in this branch history, so 0044 is the real parent.
revision = "0046_powerhouse_foundation_shell"
down_revision = "0044"
branch_labels = None
depends_on = None


TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _create_rls_policy(schema_name: str, table_name: str) -> None:
    """Generated function header.

    Function: _create_rls_policy
    Path: backend/alembic/versions/0046_powerhouse_foundation_shell.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(f'ALTER TABLE "{schema_name}"."{table_name}" ENABLE ROW LEVEL SECURITY')
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


def _drop_tables(schema_name: str, table_names: Iterable[str]) -> None:
    """Generated function header.

    Function: _drop_tables
    Path: backend/alembic/versions/0046_powerhouse_foundation_shell.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table_name in table_names:
        op.execute(f'DROP TABLE IF EXISTS "{schema_name}"."{table_name}" CASCADE')


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0046_powerhouse_foundation_shell.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS workflow")
    # Some environments don't provision legacy roles (app_user/app_readonly).
    # Make grants conditional so migration stays portable across dev/staging/prod.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA workflow TO app_user';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_readonly') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA workflow TO app_readonly';
            END IF;
        END
        $$;
        """
    )

    # Communications shell tables
    op.create_table(
        "inboxes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("inbox_name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False, server_default=sa.text("'mock'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("allowed_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="communications",
    )
    op.create_index("ix_inboxes_tenant_building", "inboxes", ["tenant_id", "building_id"], schema="communications")
    op.create_index("ux_inboxes_building_address", "inboxes", ["building_id", "address"], unique=True, schema="communications")

    op.create_table(
        "conversation_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("inbox_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("source_channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("visibility", sa.Text(), nullable=False, server_default=sa.text("'participants_only'")),
        sa.Column("linked_entity_type", sa.Text(), nullable=True),
        sa.Column("linked_entity_id", sa.Text(), nullable=True),
        sa.Column("source_external_id", sa.Text(), nullable=True),
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["inbox_id"], ["communications.inboxes.id"], ondelete="SET NULL"),
        schema="communications",
    )
    op.create_index("ix_threads_tenant_building", "conversation_threads", ["tenant_id", "building_id"], schema="communications")
    op.create_index("ix_threads_status_priority", "conversation_threads", ["status", "priority"], schema="communications")

    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False, server_default=sa.text("'message'")),
        sa.Column("visibility", sa.Text(), nullable=False, server_default=sa.text("'participants_only'")),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal_note", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("source_external_id", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index("ix_messages_tenant_thread", "conversation_messages", ["tenant_id", "thread_id"], schema="communications")
    op.create_index("ix_messages_created_at", "conversation_messages", ["created_at"], schema="communications")

    op.create_table(
        "conversation_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index(
        "ux_conversation_participants_unique",
        "conversation_participants",
        ["tenant_id", "thread_id", "user_id"],
        unique=True,
        schema="communications",
    )

    op.create_table(
        "conversation_watchers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index(
        "ux_conversation_watchers_unique",
        "conversation_watchers",
        ["tenant_id", "thread_id", "user_id"],
        unique=True,
        schema="communications",
    )

    op.create_table(
        "conversation_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_entity_type", sa.Text(), nullable=False),
        sa.Column("linked_entity_id", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index("ix_conversation_links_thread", "conversation_links", ["tenant_id", "thread_id"], schema="communications")

    op.create_table(
        "message_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["message_id"], ["communications.conversation_messages.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index("ix_message_attachments_message", "message_attachments", ["tenant_id", "message_id"], schema="communications")

    op.create_table(
        "message_delivery_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inbox_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="communications",
    )
    op.create_index("ix_delivery_events_tenant_created", "message_delivery_events", ["tenant_id", "created_at"], schema="communications")

    op.create_table(
        "message_ai_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("suggested_next_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("requires_human_approval", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("model_version", sa.Text(), nullable=False, server_default=sa.text("'placeholder-v1'")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index("ix_ai_summaries_thread", "message_ai_summaries", ["tenant_id", "thread_id"], schema="communications")

    op.create_table(
        "message_ai_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_body", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("requires_human_approval", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("safe_to_send", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("model_version", sa.Text(), nullable=False, server_default=sa.text("'placeholder-v1'")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["communications.conversation_threads.id"], ondelete="CASCADE"),
        schema="communications",
    )
    op.create_index("ix_ai_suggestions_thread", "message_ai_suggestions", ["tenant_id", "thread_id"], schema="communications")

    # Workflow shell tables
    op.create_table(
        "workflow_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("definition_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="workflow",
    )
    op.create_index("ux_workflow_templates_key", "workflow_templates", ["tenant_id", "template_key", "version"], unique=True, schema="workflow")

    op.create_table(
        "workflow_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("linked_entity_type", sa.Text(), nullable=True),
        sa.Column("linked_entity_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["template_id"], ["workflow.workflow_templates.id"], ondelete="SET NULL"),
        schema="workflow",
    )
    op.create_index("ix_workflow_instances_tenant_status", "workflow_instances", ["tenant_id", "status"], schema="workflow")

    op.create_table(
        "workflow_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("step_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow.workflow_instances.id"], ondelete="CASCADE"),
        schema="workflow",
    )
    op.create_index("ix_workflow_steps_instance", "workflow_steps", ["tenant_id", "instance_id"], schema="workflow")

    op.create_table(
        "workflow_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow.workflow_instances.id"], ondelete="CASCADE"),
        schema="workflow",
    )
    op.create_index("ix_workflow_events_instance", "workflow_events", ["tenant_id", "instance_id", "created_at"], schema="workflow")

    op.create_table(
        "workflow_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow.workflow_instances.id"], ondelete="CASCADE"),
        schema="workflow",
    )
    op.create_index("ix_workflow_actions_instance", "workflow_actions", ["tenant_id", "instance_id"], schema="workflow")

    op.create_table(
        "workflow_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'assigned'")),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow.workflow_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["workflow.workflow_steps.id"], ondelete="SET NULL"),
        schema="workflow",
    )
    op.create_index("ix_workflow_assignments_assignee", "workflow_assignments", ["tenant_id", "assignee_user_id"], schema="workflow")

    op.create_table(
        "workflow_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=True),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow.workflow_instances.id"], ondelete="CASCADE"),
        schema="workflow",
    )
    op.create_index("ix_workflow_status_history_instance", "workflow_status_history", ["tenant_id", "instance_id"], schema="workflow")

    op.create_table(
        "automation_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("requires_human_approval", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="workflow",
    )
    op.create_index("ux_automation_rules_key", "automation_rules", ["tenant_id", "rule_key"], unique=True, schema="workflow")

    op.create_table(
        "automation_rule_conditions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("condition_type", sa.Text(), nullable=False),
        sa.Column("expression_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["rule_id"], ["workflow.automation_rules.id"], ondelete="CASCADE"),
        schema="workflow",
    )

    op.create_table(
        "automation_rule_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requires_human_approval", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["rule_id"], ["workflow.automation_rules.id"], ondelete="CASCADE"),
        schema="workflow",
    )

    op.create_table(
        "automation_rule_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'simulated'")),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["rule_id"], ["workflow.automation_rules.id"], ondelete="SET NULL"),
        schema="workflow",
    )
    op.create_index("ix_automation_rule_runs_tenant", "automation_rule_runs", ["tenant_id", "created_at"], schema="workflow")

    for table_name in (
        "inboxes",
        "conversation_threads",
        "conversation_messages",
        "conversation_participants",
        "conversation_watchers",
        "conversation_links",
        "message_attachments",
        "message_delivery_events",
        "message_ai_summaries",
        "message_ai_suggestions",
    ):
        _create_rls_policy("communications", table_name)

    for table_name in (
        "workflow_templates",
        "workflow_instances",
        "workflow_steps",
        "workflow_events",
        "workflow_actions",
        "workflow_assignments",
        "workflow_status_history",
        "automation_rules",
        "automation_rule_conditions",
        "automation_rule_actions",
        "automation_rule_runs",
    ):
        _create_rls_policy("workflow", table_name)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0046_powerhouse_foundation_shell.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _drop_tables(
        "workflow",
        [
            "automation_rule_runs",
            "automation_rule_actions",
            "automation_rule_conditions",
            "automation_rules",
            "workflow_status_history",
            "workflow_assignments",
            "workflow_actions",
            "workflow_events",
            "workflow_steps",
            "workflow_instances",
            "workflow_templates",
        ],
    )
    _drop_tables(
        "communications",
        [
            "message_ai_suggestions",
            "message_ai_summaries",
            "message_delivery_events",
            "message_attachments",
            "conversation_links",
            "conversation_watchers",
            "conversation_participants",
            "conversation_messages",
            "conversation_threads",
            "inboxes",
        ],
    )
