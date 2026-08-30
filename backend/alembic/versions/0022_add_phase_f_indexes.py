"""0022 — Add performance indexes for Phase F tables.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-02

Adds production-grade indexes for:
- core.saga_runs and core.saga_steps (workflow queries)
- core.shadow_read_divergences (parity monitoring)

These are heavy on-write but essential for fast reads during operational
and analytical queries.
"""
from alembic import op
import sqlalchemy as sa

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Saga query indexes (already created in 0018, just verifying)
    # ──────────────────────────────────────────────────────────────────────

    # saga_runs: lookups by saga_type, status filtering
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0022_add_phase_f_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_index(
        'saga_runs_tenant_initiated_idx',
        'saga_runs',
        ['tenant_id', 'initiated_at'],
        schema='core',
        if_not_exists=True
    )

    op.create_index(
        'saga_runs_failed_idx',
        'saga_runs',
        ['status'],
        schema='core',
        postgresql_where=sa.text("status IN ('failed', 'compensated')"),
        if_not_exists=True
    )

    # saga_steps: lookups by step status within a saga
    op.create_index(
        'saga_steps_tenant_started_idx',
        'saga_steps',
        ['tenant_id', 'started_at'],
        schema='core',
        if_not_exists=True
    )

    # ──────────────────────────────────────────────────────────────────────
    # Shadow read divergence indexes
    # ──────────────────────────────────────────────────────────────────────

    op.create_index(
        'shadow_divergences_detected_idx',
        'shadow_read_divergences',
        ['detected_at', 'resolved'],
        schema='core',
        if_not_exists=True
    )

    op.create_index(
        'shadow_divergences_query_type_idx',
        'shadow_read_divergences',
        ['query_type'],
        schema='core',
        if_not_exists=True
    )


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Drop indexes
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0022_add_phase_f_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_index('shadow_divergences_query_type_idx', schema='core', if_exists=True)
    op.drop_index('shadow_divergences_detected_idx', schema='core', if_exists=True)
    op.drop_index('saga_steps_tenant_started_idx', schema='core', if_exists=True)
    op.drop_index('saga_runs_failed_idx', schema='core', if_exists=True)
    op.drop_index('saga_runs_tenant_initiated_idx', schema='core', if_exists=True)
