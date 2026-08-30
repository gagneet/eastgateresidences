"""Reconstruction provenance columns on finance.bank_transactions and finance.receipts.

Revision ID: 0066_bank_tx_recon_metadata
Revises: 0065_shadow_read_coverage_daily
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:demo_bank — Phase 0B provenance-into-Postgres columns.
# Layer: migration
# Data flow: BankTxObserved.metadata (backend/integrations/envelopes.py) →
#            routers/bank_feeds.py::_tx_from_envelope/_insert_bank_transaction →
#            finance.bank_transactions (these columns) → finance.receipts (these columns).
# Related: backend/integrations/envelopes.py
#          backend/routers/bank_feeds.py
#          docs/migration/historical_ledger_reconciliation_plan01.md
#          docs/migration/historical_ledger_reconciliation_plan02.md
# Table: finance.bank_transactions, finance.receipts

import sqlalchemy as sa
from alembic import op

revision = "0066_bank_tx_recon_metadata"
down_revision = "0065_shadow_read_coverage_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Explicit columns for the fields every reconstructed transaction carries,
    # plus one JSONB catch-all — mirrors the existing source_snapshot_ids JSONB
    # precedent from 0064 rather than inventing a new pattern.
    op.execute(
        """
        ALTER TABLE finance.bank_transactions
            ADD COLUMN IF NOT EXISTS transaction_origin TEXT,
            ADD COLUMN IF NOT EXISTS reconstruction_batch_id UUID,
            ADD COLUMN IF NOT EXISTS reconstruction_version INT,
            ADD COLUMN IF NOT EXISTS assumption_code TEXT,
            ADD COLUMN IF NOT EXISTS reconstruction_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS bank_transactions_reconstruction_batch_idx "
        "ON finance.bank_transactions (reconstruction_batch_id) "
        "WHERE reconstruction_batch_id IS NOT NULL"
    )

    # finance.receipts has no metadata column today (unlike journal_entries,
    # which already has one) — add the same two-column pattern so a receipt's
    # provenance survives independently of whatever the source bank_transaction
    # row later becomes (archived, purged, etc.).
    op.execute(
        """
        ALTER TABLE finance.receipts
            ADD COLUMN IF NOT EXISTS reconstruction_batch_id UUID,
            ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS receipts_reconstruction_batch_idx "
        "ON finance.receipts (reconstruction_batch_id) "
        "WHERE reconstruction_batch_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS finance.receipts_reconstruction_batch_idx")
    op.execute(
        """
        ALTER TABLE finance.receipts
            DROP COLUMN IF EXISTS reconstruction_batch_id,
            DROP COLUMN IF EXISTS metadata
        """
    )
    op.execute("DROP INDEX IF EXISTS finance.bank_transactions_reconstruction_batch_idx")
    op.execute(
        """
        ALTER TABLE finance.bank_transactions
            DROP COLUMN IF EXISTS transaction_origin,
            DROP COLUMN IF EXISTS reconstruction_batch_id,
            DROP COLUMN IF EXISTS reconstruction_version,
            DROP COLUMN IF EXISTS assumption_code,
            DROP COLUMN IF EXISTS reconstruction_metadata
        """
    )
