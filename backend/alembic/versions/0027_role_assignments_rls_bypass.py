"""0027 — Add RLS bypass-sentinel clause to core.user_role_assignments.tenant_isolation.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-03

Multi-org / multi-building owner case: a single login (one ``core.users``
row in one tenant) needs to surface every scheme that user holds a role
assignment for, even when those assignments live in OTHER tenants
(e.g. an owner with units in two buildings under different Strata
Management Organisations). Reading those assignments cross-tenant
requires the same bypass-sentinel pattern that ``core.users``
(migration 0014) and ``core.schemes`` (migration 0026) already use.

Application code must continue to set the bypass sentinel only inside
admin-only repo helpers (the existing ``identity_repo.list_schemes_for_user``
and ``find_user_by_*_for_admin`` helpers) — not in arbitrary route
handlers.
"""
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None

_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0027_role_assignments_rls_bypass.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_user_role_assignments ON core.user_role_assignments")
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_user_role_assignments ON core.user_role_assignments
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{_BYPASS_UUID}'
        )
    """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0027_role_assignments_rls_bypass.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_user_role_assignments ON core.user_role_assignments")
    op.execute("""
        CREATE POLICY tenant_isolation_core_user_role_assignments ON core.user_role_assignments
        USING (tenant_id = core.current_tenant_id())
    """)
