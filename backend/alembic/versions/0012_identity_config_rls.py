"""0012 — RLS policies for identity and config tables.

Revision: 0012
Previous: 0011

Enables Row-Level Security on every tenant-scoped table introduced in
migrations 0010 and 0011.  Global tables are intentionally excluded.

Tenant-scoped (ENABLE + FORCE RLS, policy = tenant_isolation_core_<table>):
  core.users
  core.user_units
  core.user_role_assignments
  core.user_sessions
  core.user_invitations
  core.feature_toggle_overrides
  core.building_settings

Global (no RLS):
  core.feature_toggles  — identical view for every tenant
  core.role_permissions — identical view for every tenant

All policies use the GUC-backed helper:
  core.current_tenant_id() → UUID | NULL
  (defined in migration 0007, reads app.tenant_id session variable)

BYPASSRLS note
--------------
This migration is DDL-only (ALTER TABLE + CREATE POLICY).  DDL statements
are not subject to RLS checks, so strataos_user does not need BYPASSRLS to
run this migration.  After this migration, any application code that reads
or writes a tenant-scoped table MUST first issue:

    SET LOCAL app.tenant_id = '<tenant_uuid>';

See backend/db_postgres/session.py::set_tenant() for the standard helper.
Tests that use raw connections must do the same via:

    await conn.execute("SET LOCAL app.tenant_id = $1", tenant_id_str)
"""
from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Tables that get full tenant isolation (must all have a tenant_id column)
_TENANT_TABLES = [
    "core.users",
    "core.user_units",
    "core.user_role_assignments",
    "core.user_sessions",
    "core.user_invitations",
    "core.feature_toggle_overrides",
    "core.building_settings",
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0012_identity_config_rls.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for full_table in _TENANT_TABLES:
        _, table = full_table.split(".")
        policy = f"tenant_isolation_core_{table}"

        op.execute(f"ALTER TABLE {full_table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {full_table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy} ON {full_table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0012_identity_config_rls.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for full_table in reversed(_TENANT_TABLES):
        _, table = full_table.split(".")
        policy = f"tenant_isolation_core_{table}"

        op.execute(f"DROP POLICY IF EXISTS {policy} ON {full_table}")
        op.execute(f"ALTER TABLE {full_table} DISABLE ROW LEVEL SECURITY")
