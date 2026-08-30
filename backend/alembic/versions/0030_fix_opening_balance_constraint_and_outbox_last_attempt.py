"""0030 — Drop non-negative opening balance constraint; add outbox.last_attempt_at.

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-05

Two related bug fixes:

1. finance.funds.chk_opening_balance_non_negative
   Migration 0017 added a CHECK constraint requiring opening_balance_cents >= 0.
   This is incorrect: admin funds can legitimately carry a negative opening balance
   (fund in deficit at genesis). The application-layer guard (opening_balance_cents != 0)
   is sufficient; the non-negative DB constraint is removed.

2. core.outbox.last_attempt_at
   The outbox relay backoff was anchored to created_at, causing burst retries after
   worker downtime. Per-attempt backoff now anchors to last_attempt_at so each retry
   waits the full delay from the moment of the previous attempt.
   Column: TIMESTAMPTZ NULL (NULL until first dispatch attempt).
"""
from alembic import op
import sqlalchemy as sa

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the non-negative constraint on finance.funds
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0030_fix_opening_balance_constraint_and_outbox_last_attempt.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        sa.text(
            'ALTER TABLE finance.funds DROP CONSTRAINT IF EXISTS chk_opening_balance_non_negative'
        )
    )

    # 2. Add last_attempt_at to core.outbox
    op.add_column(
        'outbox',
        sa.Column(
            'last_attempt_at',
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment='Timestamp of the most recent dispatch attempt. NULL until first attempt. '
                    'Backoff delay is computed relative to this value.',
        ),
        schema='core',
    )


def downgrade() -> None:
    # Re-add the non-negative constraint (values already in DB may violate it)
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0030_fix_opening_balance_constraint_and_outbox_last_attempt.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        sa.text(
            'ALTER TABLE finance.funds ADD CONSTRAINT chk_opening_balance_non_negative '
            'CHECK (opening_balance_cents IS NULL OR opening_balance_cents >= 0)'
        )
    )

    op.drop_column('outbox', 'last_attempt_at', schema='core')
