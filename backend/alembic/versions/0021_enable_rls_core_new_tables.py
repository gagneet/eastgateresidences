"""0021 — Enable RLS on core.saga_runs and core.saga_steps.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-02

Enables Row-Level Security on:
- core.saga_runs
- core.saga_steps
- core.shadow_read_divergences

All use the standard tenant_isolation policy pattern keyed by app.tenant_id.
These tables were created in earlier migrations but RLS policies must be added
after the schema is complete and migrations have run through.
"""
from alembic import op
import sqlalchemy as sa

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

# Sentinel UUID for RLS bypass (SECURITY DEFINER functions only)
_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Enable RLS on saga_runs
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0021_enable_rls_core_new_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute('ALTER TABLE core.saga_runs ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE core.saga_runs FORCE ROW LEVEL SECURITY')
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_saga_runs ON core.saga_runs
        USING (tenant_id = core.current_tenant_id())
    """)

    # ──────────────────────────────────────────────────────────────────────
    # Enable RLS on saga_steps
    # ──────────────────────────────────────────────────────────────────────

    op.execute('ALTER TABLE core.saga_steps ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE core.saga_steps FORCE ROW LEVEL SECURITY')
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_saga_steps ON core.saga_steps
        USING (tenant_id = core.current_tenant_id())
    """)

    # ──────────────────────────────────────────────────────────────────────
    # Enable RLS on shadow_read_divergences
    # ──────────────────────────────────────────────────────────────────────

    op.execute('ALTER TABLE core.shadow_read_divergences ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE core.shadow_read_divergences FORCE ROW LEVEL SECURITY')
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_shadow_read_divergences ON core.shadow_read_divergences
        USING (tenant_id = core.current_tenant_id())
    """)


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Disable RLS policies
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0021_enable_rls_core_new_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute('DROP POLICY IF EXISTS tenant_isolation_core_saga_runs ON core.saga_runs')
    op.execute('ALTER TABLE core.saga_runs DISABLE ROW LEVEL SECURITY')

    op.execute('DROP POLICY IF EXISTS tenant_isolation_core_saga_steps ON core.saga_steps')
    op.execute('ALTER TABLE core.saga_steps DISABLE ROW LEVEL SECURITY')

    op.execute('DROP POLICY IF EXISTS tenant_isolation_core_shadow_read_divergences ON core.shadow_read_divergences')
    op.execute('ALTER TABLE core.shadow_read_divergences DISABLE ROW LEVEL SECURITY')
