"""0016 — Extend core.outbox for relay worker retry logic.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-02

Adds four columns to core.outbox to support the transactional outbox relay worker
(Component 5 of Phase F):

1. aggregate_id: UUID — groups related events (e.g., all events from a single
   workflow instance). Nullable initially; filled on new writes.

2. aggregate_type: TEXT — semantic type of the aggregate (e.g. 'levy_run',
   'payment_batch'). Nullable initially.

3. attempts: BIGINT — number of delivery attempts. Defaults to 0. Relay worker
   increments on each retry.

4. last_error: TEXT — most recent error message if delivery failed. Nullable.
   Relay stores the error before sleeping for backoff.

This is pure additive work. Existing published rows need not be backfilled.
The relay worker will skip rows where attempts >= 5 and log them to a dead-letter view.
"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Extend core.outbox with relay worker tracking columns
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0016_extend_outbox_for_relay.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.add_column(
        'outbox',
        sa.Column('aggregate_id', sa.UUID(), nullable=True),
        schema='core'
    )

    op.add_column(
        'outbox',
        sa.Column('aggregate_type', sa.String(), nullable=True),
        schema='core'
    )

    op.add_column(
        'outbox',
        sa.Column('attempts', sa.BigInteger(), nullable=False, server_default='0'),
        schema='core'
    )

    op.add_column(
        'outbox',
        sa.Column('last_error', sa.String(), nullable=True),
        schema='core'
    )

    # ──────────────────────────────────────────────────────────────────────
    # Indexes for relay worker queries
    # ──────────────────────────────────────────────────────────────────────

    op.create_index(
        'outbox_unpublished_attempts_idx',
        'outbox',
        ['tenant_id', 'published_at', 'attempts'],
        schema='core',
        postgresql_where=sa.text('published_at IS NULL AND attempts < 5')
    )

    op.create_index(
        'outbox_aggregate_id_idx',
        'outbox',
        ['tenant_id', 'aggregate_id'],
        schema='core',
        postgresql_where=sa.text('aggregate_id IS NOT NULL')
    )


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Drop indexes
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0016_extend_outbox_for_relay.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_index('outbox_aggregate_id_idx', schema='core')
    op.drop_index('outbox_unpublished_attempts_idx', schema='core')

    # ──────────────────────────────────────────────────────────────────────
    # Drop columns
    # ──────────────────────────────────────────────────────────────────────

    op.drop_column('outbox', 'last_error', schema='core')
    op.drop_column('outbox', 'attempts', schema='core')
    op.drop_column('outbox', 'aggregate_type', schema='core')
    op.drop_column('outbox', 'aggregate_id', schema='core')
