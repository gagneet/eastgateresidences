"""0044 — Clean-slate financial onboarding cutover.

Revision ID: 0044
Revises: 0043
Create Date: 2026-06-01

Adds the evidence and cutover control tables needed for the clean-slate
financial core onboarding flow. Historical Mongo finance data remains archive
only; these tables define the PostgreSQL ledger starting point.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0044_financial_onboarding_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "evidence_documents",
        sa.Column("document_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("sha256_hash", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("declared_total_cents", sa.BigInteger(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("document_type IN ('bank_statement', 'reconciliation', 'xero_export')", name="evidence_documents_type_chk"),
        sa.CheckConstraint("sha256_hash ~ '^[0-9a-f]{64}$'", name="evidence_documents_sha256_chk"),
        schema="finance",
    )
    op.create_index("evidence_documents_scheme_idx", "evidence_documents", ["scheme_id", "uploaded_at"], schema="finance")
    op.create_index("evidence_documents_building_idx", "evidence_documents", ["building_id"], schema="finance")

    # Use IF NOT EXISTS — approved_by was already present from a prior migration run.
    op.execute(
        "ALTER TABLE finance.journal_entries "
        "ADD COLUMN IF NOT EXISTS evidence_document_id UUID "
        "REFERENCES finance.evidence_documents(document_id)"
    )
    op.execute(
        "ALTER TABLE finance.journal_entries "
        "ADD COLUMN IF NOT EXISTS approved_by UUID "
        "REFERENCES core.users(user_id)"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS journal_entries_evidence_document_idx "
        "ON finance.journal_entries (evidence_document_id)"
    )

    op.create_table(
        "financial_cutover_config",
        sa.Column("config_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("cutover_date", sa.Date(), nullable=False),
        sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("onboarded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approved_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("evidence_document_id", sa.UUID(), sa.ForeignKey("finance.evidence_documents.document_id"), nullable=True),
        sa.Column("journal_entry_ids", sa.ARRAY(sa.UUID()), nullable=False, server_default=sa.text("ARRAY[]::uuid[]")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("building_id", name="financial_cutover_config_building_ux"),
        sa.UniqueConstraint("scheme_id", name="financial_cutover_config_scheme_ux"),
        schema="finance",
    )
    op.create_index("financial_cutover_config_source_idx", "financial_cutover_config", ["building_id", "cutover_date", "onboarded"], schema="finance")

    op.create_table(
        "financial_onboarding_audit",
        sa.Column("audit_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("building_id", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("cutover_date", sa.Date(), nullable=False),
        sa.Column("evidence_document_id", sa.UUID(), sa.ForeignKey("finance.evidence_documents.document_id"), nullable=False),
        sa.Column("evidence_sha256_hash", sa.Text(), nullable=False),
        sa.Column("opening_balances", JSONB(), nullable=False),
        sa.Column("journal_entry_ids", sa.ARRAY(sa.UUID()), nullable=False),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="finance",
    )

    for table in ["evidence_documents", "financial_cutover_config", "financial_onboarding_audit"]:
        op.execute(f"ALTER TABLE finance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE finance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_finance_{table} ON finance.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0044_financial_onboarding_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in ["financial_onboarding_audit", "financial_cutover_config", "evidence_documents"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_finance_{table} ON finance.{table}")
        op.execute(f"ALTER TABLE finance.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("financial_onboarding_audit", schema="finance")
    op.drop_index("financial_cutover_config_source_idx", table_name="financial_cutover_config", schema="finance")
    op.drop_table("financial_cutover_config", schema="finance")
    op.drop_index("journal_entries_evidence_document_idx", table_name="journal_entries", schema="finance")
    op.drop_column("journal_entries", "approved_by", schema="finance")
    op.drop_column("journal_entries", "evidence_document_id", schema="finance")
    op.drop_index("evidence_documents_building_idx", table_name="evidence_documents", schema="finance")
    op.drop_index("evidence_documents_scheme_idx", table_name="evidence_documents", schema="finance")
    op.drop_table("evidence_documents", schema="finance")
