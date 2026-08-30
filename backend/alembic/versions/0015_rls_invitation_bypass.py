"""rls_invitation_bypass

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-07

Adds the bypass sentinel to the ``core.user_invitations`` RLS policy so
that pre-claim token lookups (where the tenant_id is unknown) can work
without RLS filtering.

Same pattern as migration 0014 which fixed ``core.users``.
"""
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0015_rls_invitation_bypass.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_user_invitations ON core.user_invitations")
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_user_invitations ON core.user_invitations
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{_BYPASS_UUID}'
        )
    """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0015_rls_invitation_bypass.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_user_invitations ON core.user_invitations")
    op.execute("""
        CREATE POLICY tenant_isolation_core_user_invitations ON core.user_invitations
        USING (tenant_id = core.current_tenant_id())
    """)
