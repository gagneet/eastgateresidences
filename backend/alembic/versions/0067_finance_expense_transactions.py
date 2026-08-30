"""finance.expense_transactions header table + finance.expense_evidence_links.

Revision ID: 0067_expense_transactions
Revises: 0066_bank_tx_recon_metadata
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:demo_bank — Expense-side ledger header table (settled/confirmed expenses).
# Layer: migration
# Data flow: routers/onboarding.py::import_historical_expenses (CSV) OR
#            routers/invoices.py (confirmed AP invoice) → FinancialCoreService.record_expense() →
#            finance.expense_transactions + finance.journal_entries (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/domain/entities.py
#          backend/services/financial_core/adapters/db_postgres/models.py
#          docs/migration/historical_ledger_reconciliation_plan02.md (amendment 10)
# Table: finance.expense_transactions, finance.expense_evidence_links

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0067_expense_transactions"
down_revision = "0066_bank_tx_recon_metadata"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _policy_name(schema: str, table: str) -> str:
    return f"tenant_isolation_{schema}_{table}"


def upgrade() -> None:
    op.create_table(
        "expense_transactions",
        sa.Column("expense_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("fund_id", sa.UUID(), sa.ForeignKey("finance.funds.fund_id"), nullable=True),
        sa.Column("gl_account_id", sa.UUID(), sa.ForeignKey("finance.gl_accounts.gl_account_id"), nullable=True),
        sa.Column("vendor_name", sa.Text(), nullable=True),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("financial_year", sa.Text(), nullable=True),
        # ex-GST principal; total owed/paid is amount_cents + gst_cents.
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("gst_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("journal_entry_id", sa.UUID(), sa.ForeignKey("finance.journal_entries.journal_entry_id"), nullable=True),
        sa.Column(
            "source", sa.Text(), nullable=False, server_default=sa.text("'historical_import'"),
        ),
        # Renamed from an earlier draft's "derived" to avoid ambiguity with OCR
        # extraction terminology — see plan02 amendment 10.
        sa.Column(
            "derivation_level", sa.Text(), nullable=False, server_default=sa.text("'aggregate_balancing'"),
        ),
        sa.Column("reconstruction_batch_id", sa.UUID(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("amount_cents > 0", name="expense_transactions_amount_positive_chk"),
        sa.CheckConstraint("gst_cents >= 0", name="expense_transactions_gst_non_negative_chk"),
        sa.CheckConstraint(
            "derivation_level IN ('exact', 'source_derived', 'aggregate_balancing')",
            name="expense_transactions_derivation_level_chk",
        ),
        sa.CheckConstraint(
            "source IN ('historical_import', 'invoice_confirmed', 'manual_adjustment')",
            name="expense_transactions_source_chk",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="expense_transactions_idempotency_ux"),
        schema="finance",
    )
    op.create_index(
        "expense_transactions_scheme_idx", "expense_transactions",
        ["scheme_id", "transaction_date"], schema="finance",
    )
    op.create_index(
        "expense_transactions_reconstruction_batch_idx", "expense_transactions",
        ["reconstruction_batch_id"], schema="finance",
        postgresql_where=sa.text("reconstruction_batch_id IS NOT NULL"),
    )

    # An expense may be supported by more than one evidence document (an AGM
    # pack AND an audited report AND an invoice AND a quote) — join table
    # instead of a single evidence_document_id FK. See plan02 amendment 10.
    op.create_table(
        "expense_evidence_links",
        sa.Column("expense_id", sa.UUID(), sa.ForeignKey("finance.expense_transactions.expense_id"), primary_key=True),
        sa.Column("evidence_document_id", sa.UUID(), sa.ForeignKey("finance.evidence_documents.document_id"), primary_key=True),
        sa.Column("relationship_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="finance",
    )

    # RLS — same pattern as every other tenant-scoped finance table (0008).
    op.execute("ALTER TABLE finance.expense_transactions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.expense_transactions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_policy_name('finance', 'expense_transactions')} "
        f"ON finance.expense_transactions "
        f"USING (tenant_id = core.current_tenant_id())"
    )
    # expense_evidence_links has no direct tenant_id column (matches the
    # documented pattern for receipt_allocations/payment_plan_installments in
    # 0008/0009) — isolation is enforced by joining through expense_transactions.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_finance_expense_transactions ON finance.expense_transactions")
    op.drop_table("expense_evidence_links", schema="finance")
    op.drop_table("expense_transactions", schema="finance")
