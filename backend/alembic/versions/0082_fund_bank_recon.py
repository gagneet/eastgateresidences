"""finance.fund_bank_reconciliations — per-fund opening→closing→external-balance bridge.

Revision ID: 0082_fund_bank_recon
Revises: 0081_outbox_rls_bypass
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial-onboarding — fund-level Admin+Sinking bank-reconciliation control (A3).
# Layer: migration
# Data flow: (planned) reconciliation engine → finance.fund_bank_reconciliations one row per
#            (fund, financial_year, period) → GL-computed closing vs external (bank/trust/portal)
#            balance → variance + status → /finance/views/reconciliation.
# Related: backend/services/portal_ledger_reconciliation_service.py
#          backend/services/reconstruction_generators/annual_fund_reconciliation.py
#          docs/architecture/financial-summary-analysis-of-issues.md (rule 44: every fund/FY needs
#              an explainable opening-to-closing bridge)
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (A3)
# Table: finance.fund_bank_reconciliations
#
# Why this table exists
# ---------------------
# The controlling contract requires "every lot, fund and financial year to have an explainable
# opening-to-closing bridge" (rule 44) and treats bank-to-GL reconciliation as a first-class,
# separate control from portal-to-GL (rule 39). Today the GL-computed fund position exists
# (finance.funds + journal_lines) but there is NO stored control that ties a fund's cash to an
# EXTERNAL statement balance (bank/trust feed, or the portal's per-fund figure) with a persisted
# variance + status. This is the "Admin+Sinking TODAY() balance" reconciliation the product owner
# flagged as the recurring pain point. This is the additive schema for it; the reconciliation
# ENGINE that populates it is a separate task (A3 engine, server-side).
#
# It is a reconciliation CONTROL, never a journal source: it records the comparison
# (computed vs external) and the variance — it never posts, and a variance is surfaced, never
# force-matched (contract §0 / rule 43). Integer cents throughout (CLAUDE.md ledger-money rule).

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0082_fund_bank_recon"
down_revision = "0081_outbox_rls_bypass"
branch_labels = None
depends_on = None


def _policy_name(schema: str, table: str) -> str:
    return f"tenant_isolation_{schema}_{table}"


def upgrade() -> None:
    op.create_table(
        "fund_bank_reconciliations",
        sa.Column("reconciliation_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("fund_id", sa.UUID(), sa.ForeignKey("finance.funds.fund_id"), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        # Period within the year the bridge covers: a quarter label ("Q1"..) or "FY" for the
        # whole-year roll-forward. Left free-text (like levy_runs) rather than an enum so a
        # half-yearly / custom schedule is not blocked.
        sa.Column("period_label", sa.Text(), nullable=False, server_default=sa.text("'FY'")),
        sa.Column("as_of", sa.Date(), nullable=False),
        # The GL-computed opening→movements→closing bridge, all integer cents.
        sa.Column("opening_balance_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("levied_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("received_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("expense_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("interest_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("adjustment_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("computed_closing_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        # The EXTERNAL anchor: a real bank/trust statement balance, or (interim) the portal's
        # per-fund figure. NULL == no external evidence yet (missing != zero).
        sa.Column("external_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "external_source", sa.Text(), nullable=False, server_default=sa.text("'unreconciled'"),
        ),
        # variance = computed_closing - external. Stored (not just derived) so a historical
        # reconciliation snapshot is auditable even after the ledger moves on.
        sa.Column("variance_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'unreconciled'"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "external_source IN ('unreconciled', 'bank_feed', 'trust_statement', "
            "'portal_snapshot', 'manual_csv')",
            name="fund_bank_recon_external_source_chk",
        ),
        sa.CheckConstraint(
            "status IN ('unreconciled', 'reconciled', 'variance_flagged', 'superseded')",
            name="fund_bank_recon_status_chk",
        ),
        # One live bridge per (scheme, fund, year, period) — a re-run UPSERTs, never duplicates.
        sa.UniqueConstraint(
            "scheme_id", "fund_id", "financial_year", "period_label",
            name="fund_bank_recon_period_ux",
        ),
        schema="finance",
    )
    op.create_index(
        "fund_bank_recon_scheme_idx", "fund_bank_reconciliations",
        ["scheme_id", "financial_year"], schema="finance",
    )
    op.create_index(
        "fund_bank_recon_status_idx", "fund_bank_reconciliations",
        ["scheme_id", "status"], schema="finance",
        postgresql_where=sa.text("status = 'variance_flagged'"),
    )

    # RLS — same pattern as every other tenant-scoped finance table (0008/0067). No bypass
    # clause (matches finance.receipts): a raw connection must SET app.tenant_id first.
    op.execute("ALTER TABLE finance.fund_bank_reconciliations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.fund_bank_reconciliations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_policy_name('finance', 'fund_bank_reconciliations')} "
        f"ON finance.fund_bank_reconciliations "
        f"USING (tenant_id = core.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_finance_fund_bank_reconciliations "
        "ON finance.fund_bank_reconciliations"
    )
    op.drop_table("fund_bank_reconciliations", schema="finance")
