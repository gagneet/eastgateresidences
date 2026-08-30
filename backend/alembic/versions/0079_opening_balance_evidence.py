"""Add 'opening_balance' to finance.evidence_documents.document_type.

Revision ID: 0079_opening_balance_evidence
Revises: 0078_levy_grace_insert_default
Create Date: 2026-08-06 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial-onboarding — first-class opening-balance evidence type (G3).
# Layer: migration
# Data flow: routers/financial_onboarding.py::EvidenceDocumentRequest (regex) →
#            services/onboarding/financial_onboarding.py (_ALLOWED_DOCUMENT_TYPES) →
#            finance.evidence_documents.document_type (this CHECK constraint).
# Related: backend/routers/financial_onboarding.py
#          backend/services/onboarding/financial_onboarding.py
#          docs/architecture/onboarding/04_onboarding_financial_data_flow.md (G3)
# Table: finance.evidence_documents
#
# An onboarding opening balance (fund-level carry-forward, and — via the per-lot opening
# genesis, G3.2 — the per-lot arrears/credit anchor sourced from the portal net snapshot) is
# its own kind of evidence, distinct from a bank_statement/agm_pack. Making it a first-class
# document_type lets the opening genesis reference a typed, approvable, immutable evidence row
# rather than shoehorning it into a generic type. Fully additive; existing rows stay valid.

from alembic import op

revision = "0079_opening_balance_evidence"
down_revision = "0078_levy_grace_insert_default"
branch_labels = None
depends_on = None

# Same compound name db_postgres/base.py's NAMING_CONVENTION produced from migration 0044's
# sa.CheckConstraint(name="evidence_documents_type_chk"); extended by 0068. Verified against
# pg_constraint.conname, not the literal 0044 source name.
_CONSTRAINT_NAME = "ck_evidence_documents_evidence_documents_type_chk"
# Current live set == migration 0068's _NEW_TYPES (0068 is the latest migration to touch it).
_OLD_TYPES = (
    "'bank_statement', 'reconciliation', 'xero_export', "
    "'agm_pack', 'invoice', 'quote', 'historical_expense_import'"
)
_NEW_TYPES = _OLD_TYPES + ", 'opening_balance'"


def upgrade() -> None:
    op.execute(f"ALTER TABLE finance.evidence_documents DROP CONSTRAINT {_CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE finance.evidence_documents "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (document_type IN ({_NEW_TYPES}))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE finance.evidence_documents DROP CONSTRAINT {_CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE finance.evidence_documents "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (document_type IN ({_OLD_TYPES}))"
    )
