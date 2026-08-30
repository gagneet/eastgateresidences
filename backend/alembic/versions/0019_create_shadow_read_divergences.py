"""0019 — Create shadow_read_divergences table for parity validation.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-02

Adds core.shadow_read_divergences to track differences between Postgres and
MongoDB reads during shadow-read validation (Component 6 of Phase F).

When financial_core.shadow_read_postgres toggle is enabled, every read from
the financial ledger is done twice: once from Postgres (primary), once from
MongoDB (shadow). If results differ, a divergence row is inserted here.

Schema:
- divergence_id (UUID PK)
- tenant_id, scheme_id (for scoping)
- query_type (TEXT) — e.g. 'get_trial_balance', 'get_fund_balance'
- query_params (JSONB) — the inputs to the query
- postgres_result (JSONB) — what Postgres returned
- mongodb_result (JSONB) — what MongoDB returned
- difference_summary (TEXT) — human-readable summary of divergence
- detected_at (TIMESTAMPTZ)
- resolved (BOOLEAN, nullable) — whether this divergence was root-caused

This table is write-only during shadow mode. After 7+ days with zero
divergences, the team manually approves flipping
financial_core.read_from_postgres to true.
"""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Create shadow_read_divergences table
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0019_create_shadow_read_divergences.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        'shadow_read_divergences',
        sa.Column('divergence_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('scheme_id', sa.UUID(), nullable=True),
        sa.Column('query_type', sa.String(), nullable=False),
        sa.Column('query_params', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('postgres_result', sa.JSON(), nullable=False),
        sa.Column('mongodb_result', sa.JSON(), nullable=False),
        sa.Column('difference_summary', sa.Text(), nullable=False),
        sa.Column('resolved', sa.Boolean(), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['core.tenants.tenant_id']),
        sa.ForeignKeyConstraint(['scheme_id'], ['core.schemes.scheme_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('divergence_id'),
        schema='core'
    )

    # ──────────────────────────────────────────────────────────────────────
    # Indexes for shadow read queries
    # ──────────────────────────────────────────────────────────────────────

    op.create_index(
        'shadow_divergences_unresolved_idx',
        'shadow_read_divergences',
        ['detected_at'],
        schema='core',
        postgresql_where=sa.text('resolved IS NULL')
    )

    op.create_index(
        'shadow_divergences_scheme_idx',
        'shadow_read_divergences',
        ['scheme_id', 'query_type'],
        schema='core',
        postgresql_where=sa.text('scheme_id IS NOT NULL')
    )


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Drop table
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0019_create_shadow_read_divergences.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_index('shadow_divergences_scheme_idx', schema='core')
    op.drop_index('shadow_divergences_unresolved_idx', schema='core')
    op.drop_table('shadow_read_divergences', schema='core')
