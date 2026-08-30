"""Extend finance.evidence_documents.document_type to cover expense-side evidence.

Revision ID: 0068_evidence_doc_types_exp
Revises: 0067_expense_transactions
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:demo_bank — Evidence-document type expansion for historical expense import.
# Layer: migration
# Data flow: routers/financial_onboarding.py::EvidenceDocumentRequest (regex) →
#            services/onboarding/financial_onboarding.py (_ALLOWED_DOCUMENT_TYPES) →
#            finance.evidence_documents.document_type (this CHECK constraint).
# Related: backend/routers/financial_onboarding.py
#          backend/services/onboarding/financial_onboarding.py
# Table: finance.evidence_documents

from alembic import op

revision = "0068_evidence_doc_types_exp"
down_revision = "0067_expense_transactions"
branch_labels = None
depends_on = None

# db_postgres/base.py's NAMING_CONVENTION ("ck": "ck_%(table_name)s_%(constraint_name)s")
# transformed migration 0044's sa.CheckConstraint(name="evidence_documents_type_chk")
# into this compound name at creation time — confirmed against the live database
# (pg_constraint.conname), not the literal name the 0044 migration source passes.
_CONSTRAINT_NAME = "ck_evidence_documents_evidence_documents_type_chk"
_OLD_TYPES = "'bank_statement', 'reconciliation', 'xero_export'"
_NEW_TYPES = (
    "'bank_statement', 'reconciliation', 'xero_export', "
    "'agm_pack', 'invoice', 'quote', 'historical_expense_import'"
)


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
