"""0026 — Add RLS bypass-sentinel clause to core.schemes.tenant_isolation.

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-03

The ``core.users.tenant_isolation`` policy was extended in migration 0014
to honour the bypass-sentinel UUID ``00000000-0000-0000-0000-000000000000``
so SECURITY DEFINER functions and admin-tooling repos could read users
cross-tenant. The same need applies to ``core.schemes`` for these flows:

  - ``GET /api/buildings/me`` for super admins (lists every active scheme
    in the platform regardless of tenant)
  - ``POST /api/auth/switch-building`` validating a target scheme exists
    when the SA picks one from the building switcher
  - ``identity_repo.get_scheme_by_id`` / ``get_scheme_by_number`` lookups
    used by ``utils.auth.get_current_building`` when an SA passes
    ``X-Building-ID``

Without this clause those reads return zero rows for SA queries
(``current_tenant_id()`` returns NULL outside a tenant context) and the
SA cannot resolve any scheme outside their own platform tenant.

Application code must continue to set the bypass sentinel only inside
SECURITY DEFINER functions or in admin-only repo helpers (mirroring the
pattern in ``identity_repo.find_user_by_email_for_admin``).
"""
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None

_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0026_schemes_rls_bypass_for_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_schemes ON core.schemes")
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_schemes ON core.schemes
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{_BYPASS_UUID}'
        )
    """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0026_schemes_rls_bypass_for_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_schemes ON core.schemes")
    op.execute("""
        CREATE POLICY tenant_isolation_core_schemes ON core.schemes
        USING (tenant_id = core.current_tenant_id())
    """)
