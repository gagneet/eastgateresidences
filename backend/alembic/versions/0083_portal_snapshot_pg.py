"""finance.portal_balance_snapshots (+ _lines) — PG home for the portal reconciliation anchor.

Revision ID: 0083_portal_snapshot_pg
Revises: 0082_fund_bank_recon
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial-onboarding — PG portal-balance snapshot staging (B2), Mongo->DR-only.
# Layer: migration
# Data flow: integrations/portal_adapters.py::PortalAdapter.build_snapshot() ->
#            PortalBalanceSnapshot (typed evidence) -> (planned) finance.portal_balance_snapshots
#            + finance.portal_balance_snapshot_lines -> portal_ledger_reconciliation_service.
# Related: backend/integrations/portal_adapters.py (PortalBalanceSnapshot / PortalUnitBalance)
#          backend/services/portal_ledger_reconciliation_service.py
#          backend/scripts/ingest/strata_web_portal_ingest.py
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (B2)
# Table: finance.portal_balance_snapshots, finance.portal_balance_snapshot_lines
#
# Why this table exists
# ---------------------
# The portal/current-balance snapshot is the external RECONCILIATION anchor for the derived
# ledger (the owner's "current per-unit status" input). Today it lands only in the MongoDB
# collection `staging_strata_web_snapshots`. The product owner's target is "MongoDB = DR-only
# for financials", so the anchor that gates promotion MUST live in the PostgreSQL system of
# record. This is the additive PG home; wiring the adapter/ingest + the reconciliation service
# to read/dual-write here is B2b (separate task).
#
# It is reconciliation EVIDENCE, never a journal source (contract §0, rule 6): a snapshot is
# compared against the GL-computed balance; it never posts and never overwrites a derived field.
# Columns mirror integrations/portal_adapters.py::PortalBalanceSnapshot / PortalUnitBalance
# exactly so the adapter output maps 1:1. Integer cents throughout.

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0083_portal_snapshot_pg"
down_revision = "0082_fund_bank_recon"
branch_labels = None
depends_on = None


def _policy_name(schema: str, table: str) -> str:
    return f"tenant_isolation_{schema}_{table}"


def upgrade() -> None:
    # Header — one row per scrape/export (mirrors PortalBalanceSnapshot).
    op.create_table(
        "portal_balance_snapshots",
        sa.Column("snapshot_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        # as_of / financial_year kept as Text to match the Pydantic model's string dates and the
        # existing Mongo staging document; every external snapshot carries an as_of (rule 46).
        sa.Column("snapshot_date", sa.Text(), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        # The producing adapter, e.g. 'strata_web_portal_scrape' (Civium) / 'csv_portal_export'.
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("raw_admin_fund_balance_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw_sinking_fund_balance_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw_arrears_total_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw_credit_total_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw_collection_rate", sa.Numeric(6, 3), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="finance",
    )
    op.create_index(
        "portal_snapshot_scheme_idx", "portal_balance_snapshots",
        ["scheme_id", "financial_year", "snapshot_date"], schema="finance",
    )

    # Lines — one row per lot (mirrors PortalUnitBalance). balance_cents is the per-unit NET
    # (>0 owes, <0 in credit) — the per-unit anchor the reconciliation service diffs against
    # the GL-computed true balance (GAP-FIN-036). Never posted.
    op.create_table(
        "portal_balance_snapshot_lines",
        sa.Column("snapshot_line_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "snapshot_id", sa.UUID(),
            sa.ForeignKey("finance.portal_balance_snapshots.snapshot_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # lot_id is nullable: the portal keys on unit/lot NUMBER; a snapshot may arrive before
        # the lot is matched into core.lots. Store the raw number always; resolve lot_id when known.
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("owner_name", sa.Text(), nullable=True),
        schema="finance",
    )
    op.create_index(
        "portal_snapshot_lines_snapshot_idx", "portal_balance_snapshot_lines",
        ["snapshot_id"], schema="finance",
    )

    # RLS — header carries tenant_id directly; lines isolate by joining through the header
    # (same pattern as expense_evidence_links / receipt_allocations in 0067/0008).
    op.execute("ALTER TABLE finance.portal_balance_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.portal_balance_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_policy_name('finance', 'portal_balance_snapshots')} "
        f"ON finance.portal_balance_snapshots "
        f"USING (tenant_id = core.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_finance_portal_balance_snapshots "
        "ON finance.portal_balance_snapshots"
    )
    op.drop_table("portal_balance_snapshot_lines", schema="finance")
    op.drop_table("portal_balance_snapshots", schema="finance")
