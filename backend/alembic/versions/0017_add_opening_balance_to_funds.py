"""0017 — Add opening_balance_cents to finance.funds.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-02

Adds opening_balance_cents (BIGINT) to finance.funds to support the genesis
journal pattern (Component 2 of Phase F).

When post_genesis_journal() is called, it will store the opening balance in
this column and post a genesis entry with debit assets:bank and credit
equity:opening_balances_clearing.

The column is nullable to support non-genesis funds created through other paths.
Defaults to 0 for genesis funds where no specific opening balance is recorded.
"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Add opening balance tracking to funds
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0017_add_opening_balance_to_funds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.add_column(
        'funds',
        sa.Column(
            'opening_balance_cents',
            sa.BigInteger(),
            nullable=True,
            comment='Opening balance at genesis in cents. NULL if fund has no genesis entry.'
        ),
        schema='finance'
    )

    # ──────────────────────────────────────────────────────────────────────
    # Add constraint: opening balance must be non-negative if set
    # ──────────────────────────────────────────────────────────────────────

    op.execute(
        sa.text(
            'ALTER TABLE finance.funds ADD CONSTRAINT chk_opening_balance_non_negative '
            'CHECK (opening_balance_cents IS NULL OR opening_balance_cents >= 0)'
        )
    )


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Remove constraint and column
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0017_add_opening_balance_to_funds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        sa.text(
            'ALTER TABLE finance.funds DROP CONSTRAINT chk_opening_balance_non_negative'
        )
    )

    op.drop_column('funds', 'opening_balance_cents', schema='finance')
