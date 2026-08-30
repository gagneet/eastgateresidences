"""0035 — Communications schema.

Creates the communications schema with tables for owner-facing communications
that require 7-year retention (ACT s.115 / NSW s.93):

  communications.announcements  — owner announcements (live in MongoDB: announcements)
  communications.notices        — legal notices served (live in MongoDB: notices)
  communications.letters_log    — outbound letter history (registered in MongoDB but not live)
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0035_communications_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS communications")

    op.create_table(
        "announcements",
        sa.Column("announcement_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("author_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="communications",
    )
    op.create_index("announcements_scheme_published_idx", "announcements", ["scheme_id", "published_at"], schema="communications")
    op.create_index("announcements_tenant_idx", "announcements", ["tenant_id"], schema="communications")

    op.create_table(
        "notices",
        sa.Column("notice_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("notice_type", sa.Text(), nullable=False),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("recipient_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sent_via", sa.Text(), nullable=False, server_default="email"),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="communications",
    )
    op.create_index("notices_scheme_type_sent_idx", "notices", ["scheme_id", "notice_type", "sent_at"], schema="communications")
    op.create_index("notices_lot_idx", "notices", ["lot_id"], schema="communications")

    op.create_table(
        "letters_log",
        sa.Column("letter_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("letter_type", sa.Text(), nullable=False),
        sa.Column("recipient_party_ids", sa.ARRAY(sa.UUID()), nullable=False, server_default=sa.text("ARRAY[]::uuid[]")),
        sa.Column("template_name", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="communications",
    )
    op.create_index("letters_log_scheme_generated_idx", "letters_log", ["scheme_id", "generated_at"], schema="communications")

    op.execute(
        """
        DO $$ BEGIN
            GRANT USAGE ON SCHEMA communications TO strataos_user;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA communications TO strataos_user;
            ALTER DEFAULT PRIVILEGES IN SCHEMA communications
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )

    _COMMS_TABLES = ["announcements", "notices", "letters_log"]
    for table in _COMMS_TABLES:
        op.execute(f"ALTER TABLE communications.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE communications.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_communications_{table} ON communications.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0035_communications_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in ["letters_log", "notices", "announcements"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_communications_{table} ON communications.{table}")
        op.execute(f"ALTER TABLE communications.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("letters_log", schema="communications")
    op.drop_table("notices", schema="communications")
    op.drop_table("announcements", schema="communications")
    op.execute("DROP SCHEMA IF EXISTS communications")
