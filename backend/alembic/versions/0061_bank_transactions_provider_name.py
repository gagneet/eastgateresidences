"""Add provider_name to finance.bank_transactions for bank-feed provenance.

Revision ID: 0061_bank_transactions_provider_name
Revises: 0060_extend_readiness_constraint
Create Date: 2026-07-01

# @featuretrace:demo_bank — Persist canonical bank-feed provider provenance on Postgres bank transactions.
# Layer: migration
# Data flow: POST /bank-feeds/sync → finance.bank_transactions.provider_name.
# Related: backend/routers/bank_feeds.py
#          docs/features/demo_bank_provider_feature.md
"""
from __future__ import annotations

from alembic import op

revision = "0061_bank_tx_provider_name"
down_revision = "0060_extend_readiness_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0061_bank_transactions_provider_name.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        ALTER TABLE finance.bank_transactions
            ADD COLUMN IF NOT EXISTS provider_name TEXT
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0061_bank_transactions_provider_name.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        ALTER TABLE finance.bank_transactions
            DROP COLUMN IF EXISTS provider_name
        """
    )
