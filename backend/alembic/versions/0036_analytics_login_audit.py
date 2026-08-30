"""0036 — Analytics login audit table.

The analytics schema was created in an earlier migration but has had
0 tables applied to it. This migration adds the first table:

  analytics.login_audit — login attempt log (success/failure/suspicious)

login_audit_logs currently live in MongoDB GLOBAL_COLLECTIONS. PostgreSQL
is the declared future source of truth for security audit data.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT, INET

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0036_analytics_login_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "login_audit",
        sa.Column("login_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("login_email", CITEXT(), nullable=False),
        sa.Column("ip_address", INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "outcome IN ('success', 'failed_password', 'locked', 'mfa_failed', 'suspicious', 'token_invalid')",
            name="login_audit_outcome_chk",
        ),
        schema="analytics",
    )
    op.create_index("login_audit_user_created_idx", "login_audit", ["user_id", "created_at"], schema="analytics")
    op.create_index("login_audit_created_idx", "login_audit", ["created_at"], schema="analytics")
    op.create_index("login_audit_email_idx", "login_audit", ["login_email", "created_at"], schema="analytics")

    op.execute(
        """
        DO $$ BEGIN
            GRANT USAGE ON SCHEMA analytics TO strataos_user;
            GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA analytics TO strataos_user;
            ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
              GRANT SELECT, INSERT ON TABLES TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )

    op.execute("ALTER TABLE analytics.login_audit ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics.login_audit FORCE ROW LEVEL SECURITY")
    # Rows with NULL tenant_id (platform-level events) are excluded from tenant views;
    # superadmins access them via the bypass mechanism (SET app.tenant_id = bypass_uuid).
    op.execute(
        "CREATE POLICY tenant_isolation_analytics_login_audit ON analytics.login_audit "
        "USING (tenant_id = core.current_tenant_id())"
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0036_analytics_login_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_analytics_login_audit ON analytics.login_audit")
    op.execute("ALTER TABLE analytics.login_audit DISABLE ROW LEVEL SECURITY")
    op.drop_table("login_audit", schema="analytics")
