"""Add provenance/confidence fields to finance.bank_transactions.

Revision ID: 0064_bank_tx_provenance_fields
Revises: 0063_parties_secondary_email
Create Date: 2026-07-05

# @featuretrace:demo_bank — Provenance/confidence schema for bank transaction staging (GAP-FIN-015 blocker 2).
# Layer: migration
# Data flow: Demo Bank / historical reconstruction / Strata Web inference
#            → demo_bank_transactions (Mongo) → POST /bank-feeds/sync
#            → finance.bank_transactions (Postgres, this migration's new columns).
# Related: backend/integrations/demo_bank/ingestion.py
#          backend/routers/bank_feeds.py
#          backend/routers/financial_matching.py
#          docs/migration/strataos_finance_ledger_segregation_next_phase_2026-07-05.md
# Table: finance.bank_transactions

All columns are additive and nullable (or defaulted) so existing rows and the
existing INSERT column list in bank_feeds._insert_bank_transaction() remain valid
until that function is updated to populate them.
"""
from __future__ import annotations

from alembic import op

revision = "0064_bank_tx_provenance_fields"
down_revision = "0063_parties_secondary_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0064_bank_transactions_provenance_fields.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        ALTER TABLE finance.bank_transactions
            ADD COLUMN IF NOT EXISTS source_type TEXT,
            ADD COLUMN IF NOT EXISTS confidence TEXT,
            ADD COLUMN IF NOT EXISTS provenance_class TEXT,
            ADD COLUMN IF NOT EXISTS evidence_type TEXT,
            ADD COLUMN IF NOT EXISTS formula_version TEXT,
            ADD COLUMN IF NOT EXISTS source_snapshot_ids JSONB,
            ADD COLUMN IF NOT EXISTS supersedes_event_id TEXT,
            ADD COLUMN IF NOT EXISTS requires_review BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS date_basis TEXT,
            ADD COLUMN IF NOT EXISTS unit_number TEXT
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0064_bank_transactions_provenance_fields.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        ALTER TABLE finance.bank_transactions
            DROP COLUMN IF EXISTS source_type,
            DROP COLUMN IF EXISTS confidence,
            DROP COLUMN IF EXISTS provenance_class,
            DROP COLUMN IF EXISTS evidence_type,
            DROP COLUMN IF EXISTS formula_version,
            DROP COLUMN IF EXISTS source_snapshot_ids,
            DROP COLUMN IF EXISTS supersedes_event_id,
            DROP COLUMN IF EXISTS requires_review,
            DROP COLUMN IF EXISTS date_basis,
            DROP COLUMN IF EXISTS unit_number
        """
    )
