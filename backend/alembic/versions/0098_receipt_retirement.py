"""Allow a receipt to be retired without deleting it.

Revision ID: 0098_receipt_retirement
Revises: 0097_party_user_salutation
Create Date: 2026-08-27

# @featuretrace:financial_core — mark a superseded receipt so consumers can exclude it.
# Layer: migration
# Data flow: alembic upgrade head → finance.receipts.retired_at → any SUM over receipts
#            must filter it out (building-scoped).
# Related: backend/scripts/data_repair/eastgate_retire_duplicate_receipts.py
#          tasks/GAP-FIN-073-post-restore-finance-audit.md

East Gate carries 88 `manual_adjustment` receipts totalling $1,771,185.66, each an exact
per-lot duplicate of that lot's ordinary receipts, all posted from a portal scrape between
2026-08-01 and 2026-08-05 and none of them allocated to a levy item.

They are already offset in the general ledger — proof 4 in GAP-FIN-073 shows the Bank
Account moved $0.02 and Accounts Receivable nets exactly the Mongo net_balance sum, so
cash, income and arrears are all correct. What remains wrong is the receipts TABLE:
summing it returns $3,564,955.45 against $1,771,930.86 of levy income, roughly double.

Deleting them is not available. ACT/NSW seven-year retention forbids destroying a record
of a receipt that was posted, and the journal entries behind them are immutable anyway.
So the row stays and gains a marker.

A first-class column rather than a key in the existing `metadata` jsonb: every consumer
that sums this table has to be able to exclude retired rows cheaply and visibly, and a
`WHERE retired_at IS NULL` that a reviewer can see beats a JSON path they will not think
to look for. The partial index covers the common case — live receipts are the majority
and the ones every aggregate scans.

NULL means live, so every existing row and every future insert is unaffected.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0098_receipt_retirement"
down_revision = "0097_party_user_salutation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE finance.receipts
            ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS retired_reason TEXT NULL
    """))
    # Partial index on the LIVE rows: aggregates read those, and indexing the retired
    # minority would cost writes for a set nothing sums.
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS receipts_live_idx
            ON finance.receipts (tenant_id, received_on)
            WHERE retired_at IS NULL
    """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS finance.receipts_live_idx"))
    op.execute(text("""
        ALTER TABLE finance.receipts
            DROP COLUMN IF EXISTS retired_at,
            DROP COLUMN IF EXISTS retired_reason
    """))
