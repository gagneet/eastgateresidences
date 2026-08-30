"""0024 — Add ``is_demo`` flag to core.schemes and core.tenants.

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-02

Supports the platform-shell bootstrap UX. The "Try the demo" path seeds a
full Strata Management chain (one demo tenant → one demo Strata Manager
+ one demo Building Admin → one demo scheme → ten demo lots), so reports,
exports, analytics, and migration tooling need a robust way to filter the
demo data out of production aggregates.

Two columns are added:

- ``core.tenants.is_demo``  — flags an entire Strata Management Company
  (demo, training, sandbox). System-wide unique partial index ensures
  exactly one demo tenant.
- ``core.schemes.is_demo``  — flags an individual building. System-wide
  unique partial index ensures exactly one demo scheme.

Both default FALSE; existing rows are unaffected.
"""
from alembic import op
import sqlalchemy as sa

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tenants
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0024_add_is_demo_to_schemes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.add_column(
        'tenants',
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
        schema='core',
    )
    op.execute("""
               CREATE UNIQUE INDEX tenants_one_demo_idx
                   ON core.tenants (is_demo) WHERE is_demo = TRUE
               """)

    # schemes
    op.add_column(
        'schemes',
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
        schema='core',
    )
    op.execute("""
               CREATE UNIQUE INDEX schemes_one_demo_idx
                   ON core.schemes (is_demo) WHERE is_demo = TRUE
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0024_add_is_demo_to_schemes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP INDEX IF EXISTS core.schemes_one_demo_idx")
    op.drop_column('schemes', 'is_demo', schema='core')
    op.execute("DROP INDEX IF EXISTS core.tenants_one_demo_idx")
    op.drop_column('tenants', 'is_demo', schema='core')
