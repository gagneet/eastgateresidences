"""0051 — Financial opening evidence and genesis immutability hardening.

Revision ID: 0051_financial_opening_evidence_genesis
Revises: 0050_identity_import_tracking
Create Date: 2026-06-04

# @featuretrace:financial-onboarding — Additive finance migration for approved opening evidence and immutable approved evidence rows.
# Layer: migration
# Data flow: financial_onboarding service/CLI → finance.evidence_documents / finance.financial_cutover_config / finance.financial_onboarding_audit / finance.journal_entries (building-scoped).
# Related: backend/services/onboarding/financial_onboarding.py
#          backend/scripts/bootstrap_postgres_financial_genesis.py
#          backend/scripts/postgres_cutover_p0_readiness.py
#          docs/migration/east-gate-postgres-financial-genesis.md

Adds approval metadata and superseding evidence support to finance.evidence_documents
and prevents mutating or deleting approved evidence rows. The migration is
fully additive: existing rows remain valid and no finance ledger history is
rewritten.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0051_financial_genesis"
down_revision = "0050_identity_import_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0051_financial_opening_evidence_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        ALTER TABLE finance.evidence_documents
            ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES core.users(user_id),
            ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS notes TEXT,
            ADD COLUMN IF NOT EXISTS source_system TEXT,
            ADD COLUMN IF NOT EXISTS import_batch_id TEXT,
            ADD COLUMN IF NOT EXISTS supersedes_document_id UUID REFERENCES finance.evidence_documents(document_id)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'finance'
                  AND table_name = 'evidence_documents'
                  AND column_name = 'declared_totals_by_fund'
            ) THEN
                ALTER TABLE finance.evidence_documents
                    ADD COLUMN declared_totals_by_fund JSONB NOT NULL DEFAULT '{}'::jsonb;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'finance'
                  AND tablename = 'evidence_documents'
                  AND indexname = 'evidence_documents_scheme_sha256_ux'
            ) THEN
                CREATE UNIQUE INDEX evidence_documents_scheme_sha256_ux
                ON finance.evidence_documents (scheme_id, sha256_hash)
                WHERE sha256_hash IS NOT NULL;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'finance'
                  AND tablename = 'evidence_documents'
                  AND indexname = 'evidence_documents_import_batch_idx'
            ) THEN
                CREATE INDEX evidence_documents_import_batch_idx
                ON finance.evidence_documents (building_id, import_batch_id)
                WHERE import_batch_id IS NOT NULL;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.prevent_approved_evidence_mutation()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.approved_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'evidence document % is approved and immutable; create superseding evidence instead',
                    OLD.document_id;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_prevent_approved_evidence_update
        ON finance.evidence_documents
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_approved_evidence_update
            BEFORE UPDATE OR DELETE
            ON finance.evidence_documents
            FOR EACH ROW EXECUTE FUNCTION finance.prevent_approved_evidence_mutation()
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0051_financial_opening_evidence_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_approved_evidence_update ON finance.evidence_documents")
    op.execute("DROP FUNCTION IF EXISTS finance.prevent_approved_evidence_mutation() CASCADE")
    op.execute("DROP INDEX IF EXISTS finance.evidence_documents_import_batch_idx")
    op.execute("DROP INDEX IF EXISTS finance.evidence_documents_scheme_sha256_ux")
    op.execute(
        """
        ALTER TABLE finance.evidence_documents
            DROP COLUMN IF EXISTS supersedes_document_id,
            DROP COLUMN IF EXISTS declared_totals_by_fund,
            DROP COLUMN IF EXISTS import_batch_id,
            DROP COLUMN IF EXISTS source_system,
            DROP COLUMN IF EXISTS notes,
            DROP COLUMN IF EXISTS approved_at,
            DROP COLUMN IF EXISTS approved_by
        """
    )
