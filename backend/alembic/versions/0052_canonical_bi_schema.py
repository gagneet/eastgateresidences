"""0052 — Canonical BI/Analytics schema: dimensions, facts, and lineage tables.

Revision ID: 0052_canonical_bi_schema
Revises: 0051_financial_opening_evidence_genesis
Create Date: 2026-06-08

# @featuretrace:bi-analytics — Foundational BI data warehouse tables in analytics schema.
# Layer: migration
# Data flow: ETL services → analytics.dim_* + analytics.fact_* → /api/bi/* endpoints → BIAnalyticsPage.tsx
# Related: backend/services/bi_service.py
#          backend/routers/bi.py
#          frontend/src/pages/dashboard/BIAnalyticsPage.tsx
#          docs/architecture/canonical_bi_model.md
# Toggle: bi_analytics_enabled
# Table: analytics.fact_levy_charge, analytics.fact_levy_payment, analytics.fact_arrears_snapshot,
#        analytics.fact_financial_balance, analytics.fact_work_order, analytics.fact_compliance_event,
#        analytics.fact_utility_bill, analytics.fact_building_health_snapshot, analytics.fact_smart_request,
#        analytics.fact_ownership_transfer, analytics.fact_occupancy_snapshot, analytics.fact_capex_plan,
#        analytics.fact_capex_actual, analytics.fact_asset_condition_snapshot, analytics.fact_true_cost_ownership,
#        analytics.fact_investor_yield_snapshot, analytics.dim_building, analytics.dim_lot, analytics.dim_owner,
#        analytics.bridge_lot_owner, analytics.dim_date, analytics.dim_supplier

Adds the canonical BI data warehouse layer to the existing `analytics` schema.

Architecture:
  Dimensions  — snapshot copies of core entities, versioned with valid_from/valid_to.
                Populated by bi_etl_service.py on entity change.
  Facts       — immutable measurement rows sourced from MongoDB or operational Postgres.
                Populated by bi_etl_service.py on schedule or event.
  Lineage     — every fact/dimension row carries source_system, source_id, ingested_at,
                confidence, and is_current so BI consumers can see data provenance.

RLS:  All tenant-scoped tables use the existing core.current_tenant_id() function.
      analytics.dim_date has no RLS (global, pre-loaded).
      analytics.dim_supplier is scheme-scoped.

Money: BIGINT cents (same convention as finance schema).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0052_canonical_bi_schema"
down_revision = "0051_financial_genesis"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (mirror the pattern established in 0042)
# ─────────────────────────────────────────────────────────────────────────────

def _lineage_columns() -> list[sa.Column]:
    """Source provenance columns attached to every dimension/fact row."""
    return [
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'mongo'")),
        sa.Column("source_collection", sa.Text(), nullable=True),
        sa.Column("source_table", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default=sa.text("1.00")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
    ]


def _snapshot_columns() -> list[sa.Column]:
    """Columns for slowly-changing dimension rows (SCD Type 2)."""
    return [
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("valid_to", sa.TIMESTAMP(timezone=True), nullable=True),
    ]


def _fact_common() -> list[sa.Column]:
    """Standard columns for fact tables."""
    return [
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def _grant_analytics() -> None:
    """Generated function header.

    Function: _grant_analytics
    Path: backend/alembic/versions/0052_canonical_bi_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        DO $$ BEGIN
            GRANT USAGE ON SCHEMA analytics TO strataos_user;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics TO strataos_user;
            ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )


def _enable_rls_analytics(tables: list[str]) -> None:
    """Generated function header.

    Function: _enable_rls_analytics
    Path: backend/alembic/versions/0052_canonical_bi_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in tables:
        op.execute(f"ALTER TABLE analytics.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE analytics.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_analytics_{table} ON analytics.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def _disable_rls_analytics(tables: list[str]) -> None:
    """Generated function header.

    Function: _disable_rls_analytics
    Path: backend/alembic/versions/0052_canonical_bi_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_analytics_{table} ON analytics.{table}")
        op.execute(f"ALTER TABLE analytics.{table} DISABLE ROW LEVEL SECURITY")


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade
# ─────────────────────────────────────────────────────────────────────────────

def upgrade() -> None:

    # ── Dimension: dim_date ──────────────────────────────────────────────────
    # Pre-loaded date spine 2000-01-01 → 2050-12-31.  No RLS — global.
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0052_canonical_bi_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "dim_date",
        sa.Column("date_id", sa.Integer(), primary_key=True),  # YYYYMMDD integer key
        sa.Column("full_date", sa.Date(), nullable=False, unique=True),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("quarter", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("month_name", sa.Text(), nullable=False),
        sa.Column("week_of_year", sa.SmallInteger(), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=False),
        sa.Column("is_weekend", sa.Boolean(), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),  # e.g. "FY2026"
        sa.Column("financial_quarter", sa.Text(), nullable=False),  # e.g. "FY2026-Q1"
        sa.Column("financial_month", sa.Integer(), nullable=False),  # month offset within FY (1-12)
        schema="analytics",
    )
    op.create_index("dim_date_full_date_idx", "dim_date", ["full_date"], schema="analytics")

    # ── Dimension: dim_building ──────────────────────────────────────────────
    # Grain: one row per scheme/building snapshot (SCD2).
    op.create_table(
        "dim_building",
        sa.Column("dim_building_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("scheme_number", sa.Text(), nullable=False),
        sa.Column("building_name", sa.Text(), nullable=True),
        sa.Column("suburb", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("postcode", sa.Text(), nullable=True),
        sa.Column("jurisdiction", sa.Text(), nullable=True),
        sa.Column("total_lots", sa.Integer(), nullable=True),
        sa.Column("total_entitlement", sa.Numeric(12, 4), nullable=True),
        sa.Column("strata_manager_party_id", sa.UUID(), nullable=True),
        *_snapshot_columns(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("dim_building_scheme_current_idx", "dim_building", ["scheme_id", "is_current"], schema="analytics")
    op.create_index("dim_building_tenant_idx", "dim_building", ["tenant_id"], schema="analytics")

    # ── Dimension: dim_lot ───────────────────────────────────────────────────
    # Grain: one row per lot snapshot (SCD2).
    op.create_table(
        "dim_lot",
        sa.Column("dim_lot_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),  # FK core.lots if PG-sourced
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("unit_number", sa.Text(), nullable=True),
        sa.Column("lot_type", sa.Text(), nullable=True),
        sa.Column("entitlement", sa.Numeric(10, 4), nullable=True),
        sa.Column("floor_area_sqm", sa.Numeric(8, 2), nullable=True),
        sa.Column("bedrooms", sa.SmallInteger(), nullable=True),
        sa.Column("parking_spaces", sa.SmallInteger(), nullable=True),
        *_snapshot_columns(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("dim_lot_scheme_number_idx", "dim_lot", ["scheme_id", "lot_number"], schema="analytics")
    op.create_index("dim_lot_lot_id_idx", "dim_lot", ["lot_id"], schema="analytics")

    # ── Dimension: dim_owner ─────────────────────────────────────────────────
    # Grain: one row per party snapshot (SCD2). Sourced from core.parties + users collection.
    op.create_table(
        "dim_owner",
        sa.Column("dim_owner_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("party_id", sa.UUID(), nullable=True),  # FK core.parties if PG-sourced
        sa.Column("mongo_user_id", sa.Text(), nullable=True),  # MongoDB users._id
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("is_company", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("owner_type", sa.Text(), nullable=True),  # owner, tenant, investor, strata_manager
        *_snapshot_columns(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("dim_owner_tenant_email_idx", "dim_owner", ["tenant_id", "email"], schema="analytics")
    op.create_index("dim_owner_party_id_idx", "dim_owner", ["party_id"], schema="analytics")

    # ── Bridge: bridge_lot_owner ─────────────────────────────────────────────
    # Grain: one row per lot-owner period.  Many-to-many lot↔owner with date range.
    op.create_table(
        "bridge_lot_owner",
        sa.Column("bridge_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=False),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=False),
        sa.Column("ownership_period_id", sa.UUID(), nullable=True),  # FK core.ownership_periods if PG-sourced
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ownership_share", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_primary_owner", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("bridge_lot_owner_lot_idx", "bridge_lot_owner", ["dim_lot_id", "valid_from"], schema="analytics")
    op.create_index("bridge_lot_owner_owner_idx", "bridge_lot_owner", ["dim_owner_id"], schema="analytics")

    # ── Dimension: dim_supplier ──────────────────────────────────────────────
    # Grain: one row per vendor/supplier snapshot.
    op.create_table(
        "dim_supplier",
        sa.Column("dim_supplier_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=True),
        sa.Column("vendor_id", sa.UUID(), nullable=True),
        sa.Column("mongo_vendor_id", sa.Text(), nullable=True),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        sa.Column("trade_category", sa.Text(), nullable=True),
        sa.Column("abn", sa.Text(), nullable=True),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_snapshot_columns(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("dim_supplier_tenant_name_idx", "dim_supplier", ["tenant_id", "supplier_name"], schema="analytics")

    # ── Fact: fact_levy_charge ───────────────────────────────────────────────
    # Grain: one row per lot per levy run quarter.
    # Source: finance.levy_items (PG) or unit_levy_ledger (Mongo).
    op.create_table(
        "fact_levy_charge",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("levy_run_id", sa.UUID(), nullable=True),
        sa.Column("levy_item_id", sa.UUID(), nullable=True),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        sa.Column("quarter_no", sa.SmallInteger(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("fund_type", sa.Text(), nullable=False),  # admin, sinking, capital_works, special
        sa.Column("principal_cents", sa.BigInteger(), nullable=False),
        sa.Column("gst_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("interest_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_charged_cents", sa.BigInteger(), nullable=False),
        sa.Column("paid_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("outstanding_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("entitlement_units", sa.Numeric(10, 4), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_levy_charge_scheme_fy_idx", "fact_levy_charge", ["scheme_id", "financial_year"], schema="analytics")
    op.create_index("fact_levy_charge_lot_fy_idx", "fact_levy_charge", ["dim_lot_id", "financial_year"], schema="analytics")
    op.create_index("fact_levy_charge_due_date_idx", "fact_levy_charge", ["due_date"], schema="analytics")

    # ── Fact: fact_levy_payment ──────────────────────────────────────────────
    # Grain: one row per receipt allocation (a payment may allocate across multiple levy items).
    # Source: finance.receipts + finance.receipt_allocations (PG) or levy_payments (Mongo).
    op.create_table(
        "fact_levy_payment",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("fact_levy_charge_id", sa.UUID(), sa.ForeignKey("analytics.fact_levy_charge.fact_id"), nullable=True),
        sa.Column("receipt_id", sa.UUID(), nullable=True),
        sa.Column("allocation_id", sa.UUID(), nullable=True),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=True),  # bpay, direct_debit, eft, cheque
        sa.Column("fund_type", sa.Text(), nullable=False),
        sa.Column("allocated_cents", sa.BigInteger(), nullable=False),
        sa.Column("days_after_due", sa.Integer(), nullable=True),  # negative = early payment
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_levy_payment_scheme_date_idx", "fact_levy_payment", ["scheme_id", "received_on"], schema="analytics")
    op.create_index("fact_levy_payment_lot_date_idx", "fact_levy_payment", ["dim_lot_id", "received_on"], schema="analytics")

    # ── Fact: fact_arrears_snapshot ──────────────────────────────────────────
    # Grain: one row per lot per nightly snapshot.  Captures point-in-time arrears position.
    op.create_table(
        "fact_arrears_snapshot",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("total_outstanding_cents", sa.BigInteger(), nullable=False),
        sa.Column("admin_outstanding_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("sinking_outstanding_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("interest_outstanding_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("days_overdue", sa.Integer(), nullable=True),
        sa.Column("escalation_status", sa.Text(), nullable=True),  # none, reminder_sent, legal_demand, payment_plan
        sa.Column("last_payment_date", sa.Date(), nullable=True),
        sa.Column("last_payment_cents", sa.BigInteger(), nullable=True),
        sa.Column("arrears_band", sa.Text(), nullable=True),  # current, 0-30, 31-60, 61-90, 90+
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_arrears_snapshot_scheme_date_idx", "fact_arrears_snapshot", ["scheme_id", "snapshot_date"], schema="analytics")
    op.create_index("fact_arrears_snapshot_lot_date_idx", "fact_arrears_snapshot", ["dim_lot_id", "snapshot_date"], schema="analytics")
    op.create_index("fact_arrears_snapshot_overdue_idx", "fact_arrears_snapshot", ["scheme_id", "days_overdue"], schema="analytics")

    # ── Fact: fact_financial_balance ─────────────────────────────────────────
    # Grain: one row per fund per period end.  Captures fund balance at period boundaries.
    op.create_table(
        "fact_financial_balance",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        sa.Column("quarter_no", sa.SmallInteger(), nullable=True),
        sa.Column("fund_type", sa.Text(), nullable=False),
        sa.Column("fund_id", sa.UUID(), nullable=True),
        sa.Column("opening_balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("levy_income_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("other_income_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("expenses_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("closing_balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("bank_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column("reconciliation_gap_cents", sa.BigInteger(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_financial_balance_scheme_period_idx", "fact_financial_balance", ["scheme_id", "period_date", "fund_type"], schema="analytics")

    # ── Fact: fact_work_order ────────────────────────────────────────────────
    # Grain: one row per work order lifecycle event (creation, assignment, completion, invoiced).
    op.create_table(
        "fact_work_order",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_supplier_id", sa.UUID(), sa.ForeignKey("analytics.dim_supplier.dim_supplier_id"), nullable=True),
        sa.Column("work_order_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("subcategory", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reported_date", sa.Date(), nullable=True),
        sa.Column("assigned_date", sa.Date(), nullable=True),
        sa.Column("completed_date", sa.Date(), nullable=True),
        sa.Column("days_to_complete", sa.Integer(), nullable=True),
        sa.Column("sla_breached", sa.Boolean(), nullable=True),
        sa.Column("approved_budget_cents", sa.BigInteger(), nullable=True),
        sa.Column("actual_cost_cents", sa.BigInteger(), nullable=True),
        sa.Column("budget_variance_cents", sa.BigInteger(), nullable=True),
        sa.Column("fund_type", sa.Text(), nullable=True),
        sa.Column("lot_number", sa.Text(), nullable=True),
        sa.Column("is_ppm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_emergency", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_work_order_scheme_date_idx", "fact_work_order", ["scheme_id", "reported_date"], schema="analytics")
    op.create_index("fact_work_order_category_idx", "fact_work_order", ["scheme_id", "category", "reported_date"], schema="analytics")
    op.create_index("fact_work_order_supplier_idx", "fact_work_order", ["dim_supplier_id"], schema="analytics")

    # ── Fact: fact_compliance_event ──────────────────────────────────────────
    # Grain: one row per compliance item status change.
    op.create_table(
        "fact_compliance_event",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("compliance_register_id", sa.Text(), nullable=True),
        sa.Column("compliance_item_id", sa.Text(), nullable=True),
        sa.Column("register_type", sa.Text(), nullable=True),
        sa.Column("item_title", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),  # due, completed, overdue, inspection_booked
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("days_overdue", sa.Integer(), nullable=True),
        sa.Column("rag_status", sa.Text(), nullable=True),  # green, amber, red
        sa.Column("assigned_party_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_compliance_event_scheme_date_idx", "fact_compliance_event", ["scheme_id", "event_date"], schema="analytics")
    op.create_index("fact_compliance_event_due_idx", "fact_compliance_event", ["scheme_id", "due_date"], schema="analytics")

    # ── Fact: fact_utility_bill ──────────────────────────────────────────────
    # Grain: one row per utility bill (water, electricity, gas, council rates).
    op.create_table(
        "fact_utility_bill",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("utility_type", sa.Text(), nullable=False),  # water, electricity, gas, council_rates
        sa.Column("provider_name", sa.Text(), nullable=True),
        sa.Column("billing_period_start", sa.Date(), nullable=False),
        sa.Column("billing_period_end", sa.Date(), nullable=False),
        sa.Column("total_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("usage_quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("usage_unit", sa.Text(), nullable=True),  # kL, kWh, MJ
        sa.Column("is_spike", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("spike_pct_above_avg", sa.Numeric(7, 2), nullable=True),
        sa.Column("payment_status", sa.Text(), nullable=True),
        sa.Column("lot_number", sa.Text(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_utility_bill_scheme_period_idx", "fact_utility_bill", ["scheme_id", "billing_period_start", "utility_type"], schema="analytics")
    op.create_index("fact_utility_bill_spike_idx", "fact_utility_bill", ["scheme_id", "is_spike"], schema="analytics")

    # ── Fact: fact_capex_plan ────────────────────────────────────────────────
    # Grain: one row per capital works plan item per year.
    op.create_table(
        "fact_capex_plan",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("plan_item_id", sa.Text(), nullable=True),
        sa.Column("asset_name", sa.Text(), nullable=False),
        sa.Column("asset_category", sa.Text(), nullable=True),
        sa.Column("planned_year", sa.SmallInteger(), nullable=False),
        sa.Column("planned_cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("fund_type", sa.Text(), nullable=False),  # sinking, capital_works
        sa.Column("useful_life_years", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_capex_plan_scheme_year_idx", "fact_capex_plan", ["scheme_id", "planned_year"], schema="analytics")

    # ── Fact: fact_capex_actual ──────────────────────────────────────────────
    # Grain: one row per capex actual spend item (links to work order invoice).
    op.create_table(
        "fact_capex_actual",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("fact_capex_plan_id", sa.UUID(), sa.ForeignKey("analytics.fact_capex_plan.fact_id"), nullable=True),
        sa.Column("fact_work_order_id", sa.UUID(), sa.ForeignKey("analytics.fact_work_order.fact_id"), nullable=True),
        sa.Column("dim_supplier_id", sa.UUID(), sa.ForeignKey("analytics.dim_supplier.dim_supplier_id"), nullable=True),
        sa.Column("asset_name", sa.Text(), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column("actual_cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("gst_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("fund_type", sa.Text(), nullable=False),
        sa.Column("variance_cents", sa.BigInteger(), nullable=True),  # actual - plan (positive = over budget)
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_capex_actual_scheme_fy_idx", "fact_capex_actual", ["scheme_id", "financial_year"], schema="analytics")

    # ── Fact: fact_asset_condition_snapshot ──────────────────────────────────
    # Grain: one row per asset per monthly condition snapshot.
    op.create_table(
        "fact_asset_condition_snapshot",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("asset_id", sa.Text(), nullable=True),
        sa.Column("asset_name", sa.Text(), nullable=False),
        sa.Column("asset_category", sa.Text(), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("condition_score", sa.Numeric(4, 2), nullable=True),  # 0-10
        sa.Column("condition_label", sa.Text(), nullable=True),  # excellent, good, fair, poor, critical
        sa.Column("remaining_life_years", sa.Numeric(5, 1), nullable=True),
        sa.Column("replacement_cost_cents", sa.BigInteger(), nullable=True),
        sa.Column("annualised_depreciation_cents", sa.BigInteger(), nullable=True),
        sa.Column("risk_band", sa.Text(), nullable=True),  # low, medium, high, critical
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_asset_condition_scheme_date_idx", "fact_asset_condition_snapshot", ["scheme_id", "snapshot_date"], schema="analytics")
    op.create_index("fact_asset_condition_risk_idx", "fact_asset_condition_snapshot", ["scheme_id", "risk_band"], schema="analytics")

    # ── Fact: fact_building_health_snapshot ──────────────────────────────────
    # Grain: one row per building per nightly snapshot.
    op.create_table(
        "fact_building_health_snapshot",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("financial_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("maintenance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("compliance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("governance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("levy_collection_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("arrears_count", sa.Integer(), nullable=True),
        sa.Column("arrears_total_cents", sa.BigInteger(), nullable=True),
        sa.Column("overdue_compliance_count", sa.Integer(), nullable=True),
        sa.Column("open_work_orders", sa.Integer(), nullable=True),
        sa.Column("sla_breach_count", sa.Integer(), nullable=True),
        sa.Column("sinking_fund_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column("health_label", sa.Text(), nullable=True),  # excellent, good, fair, at_risk, critical
        sa.Column("drivers", JSONB(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_building_health_scheme_date_idx", "fact_building_health_snapshot", ["scheme_id", "snapshot_date"], schema="analytics")

    # ── Fact: fact_smart_request ─────────────────────────────────────────────
    # Grain: one row per smart request / workflow submission.
    op.create_table(
        "fact_smart_request",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("subcategory", sa.Text(), nullable=True),
        sa.Column("submitted_date", sa.Date(), nullable=False),
        sa.Column("resolved_date", sa.Date(), nullable=True),
        sa.Column("days_to_resolve", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=True),
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recurrence_count", sa.Integer(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_smart_request_scheme_date_idx", "fact_smart_request", ["scheme_id", "submitted_date"], schema="analytics")
    op.create_index("fact_smart_request_category_idx", "fact_smart_request", ["scheme_id", "category", "submitted_date"], schema="analytics")

    # ── Fact: fact_ownership_transfer ────────────────────────────────────────
    # Grain: one row per ownership period (lot ownership handover event).
    op.create_table(
        "fact_ownership_transfer",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("outgoing_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("incoming_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("settlement_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("levy_at_transfer_cents", sa.BigInteger(), nullable=True),
        sa.Column("arrears_at_transfer_cents", sa.BigInteger(), nullable=True),
        sa.Column("strata_roll_updated", sa.Boolean(), nullable=True),
        sa.Column("levy_ledger_updated", sa.Boolean(), nullable=True),
        sa.Column("user_access_updated", sa.Boolean(), nullable=True),
        sa.Column("propagation_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_ownership_transfer_scheme_date_idx", "fact_ownership_transfer", ["scheme_id", "transfer_date"], schema="analytics")
    op.create_index("fact_ownership_transfer_lot_idx", "fact_ownership_transfer", ["dim_lot_id", "transfer_date"], schema="analytics")

    # ── Fact: fact_occupancy_snapshot ────────────────────────────────────────
    # Grain: one row per lot per monthly occupancy snapshot.
    op.create_table(
        "fact_occupancy_snapshot",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("occupancy_type", sa.Text(), nullable=False),  # owner_occupier, tenant, investor_vacant, unknown
        sa.Column("has_active_tenancy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("weekly_rent_cents", sa.BigInteger(), nullable=True),
        sa.Column("lease_expiry_date", sa.Date(), nullable=True),
        sa.Column("managing_agent_party_id", sa.UUID(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_occupancy_snapshot_scheme_date_idx", "fact_occupancy_snapshot", ["scheme_id", "snapshot_date"], schema="analytics")

    # ── Fact: fact_true_cost_ownership ──────────────────────────────────────
    # Grain: one row per lot per financial year.  Total cost of ownership calculation.
    op.create_table(
        "fact_true_cost_ownership",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("financial_year", sa.Text(), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("admin_levy_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("sinking_levy_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("special_levy_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("water_bill_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("council_rates_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("insurance_share_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("cost_per_sqm_cents", sa.BigInteger(), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_tco_scheme_fy_idx", "fact_true_cost_ownership", ["scheme_id", "financial_year"], schema="analytics")
    op.create_index("fact_tco_lot_fy_idx", "fact_true_cost_ownership", ["dim_lot_id", "financial_year"], schema="analytics")

    # ── Fact: fact_investor_yield_snapshot ───────────────────────────────────
    # Grain: one row per lot per quarter.
    op.create_table(
        "fact_investor_yield_snapshot",
        sa.Column("fact_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("dim_lot_id", sa.UUID(), sa.ForeignKey("analytics.dim_lot.dim_lot_id"), nullable=True),
        sa.Column("dim_owner_id", sa.UUID(), sa.ForeignKey("analytics.dim_owner.dim_owner_id"), nullable=True),
        sa.Column("snapshot_quarter", sa.Text(), nullable=False),  # e.g. "2026-Q1"
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("gross_rental_income_cents", sa.BigInteger(), nullable=True),
        sa.Column("total_strata_costs_cents", sa.BigInteger(), nullable=True),
        sa.Column("net_income_cents", sa.BigInteger(), nullable=True),
        sa.Column("gross_yield_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("net_yield_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("estimated_value_cents", sa.BigInteger(), nullable=True),
        sa.Column("vacancy_weeks", sa.Numeric(4, 1), nullable=True),
        *_fact_common(),
        *_lineage_columns(),
        schema="analytics",
    )
    op.create_index("fact_investor_yield_scheme_quarter_idx", "fact_investor_yield_snapshot", ["scheme_id", "snapshot_quarter"], schema="analytics")
    op.create_index("fact_investor_yield_lot_idx", "fact_investor_yield_snapshot", ["dim_lot_id", "snapshot_quarter"], schema="analytics")

    # ── ETL Control: bi_etl_runs ─────────────────────────────────────────────
    # Tracks ETL run history for each fact/dimension table.  No RLS — admin-level.
    op.create_table(
        "bi_etl_runs",
        sa.Column("run_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("scheme_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'running'")),
        sa.Column("rows_inserted", sa.Integer(), nullable=True),
        sa.Column("rows_updated", sa.Integer(), nullable=True),
        sa.Column("rows_skipped", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_watermark", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="analytics",
    )
    op.create_index("bi_etl_runs_table_started_idx", "bi_etl_runs", ["target_table", "started_at"], schema="analytics")

    # ── Alert rules: bi_alert_rules ──────────────────────────────────────────
    op.create_table(
        "bi_alert_rules",
        sa.Column("rule_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=True),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=True),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("rule_name", sa.Text(), nullable=False),
        sa.Column("rule_config", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="analytics",
    )

    # ── Alert events: bi_alert_events ────────────────────────────────────────
    op.create_table(
        "bi_alert_events",
        sa.Column("event_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("rule_id", sa.UUID(), sa.ForeignKey("analytics.bi_alert_rules.rule_id"), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),  # lot, building, asset, compliance_item
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("alert_payload", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("fired_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.UUID(), nullable=True),
        sa.Column("suppressed_until", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="analytics",
    )
    op.create_index("bi_alert_events_scheme_rule_idx", "bi_alert_events", ["scheme_id", "rule_code", "fired_at"], schema="analytics")

    # ── Grants & RLS ─────────────────────────────────────────────────────────
    _grant_analytics()

    _TENANT_SCOPED_TABLES = [
        "dim_building",
        "dim_lot",
        "dim_owner",
        "bridge_lot_owner",
        "dim_supplier",
        "fact_levy_charge",
        "fact_levy_payment",
        "fact_arrears_snapshot",
        "fact_financial_balance",
        "fact_work_order",
        "fact_compliance_event",
        "fact_utility_bill",
        "fact_capex_plan",
        "fact_capex_actual",
        "fact_asset_condition_snapshot",
        "fact_building_health_snapshot",
        "fact_smart_request",
        "fact_ownership_transfer",
        "fact_occupancy_snapshot",
        "fact_true_cost_ownership",
        "fact_investor_yield_snapshot",
        "bi_alert_events",
    ]
    _enable_rls_analytics(_TENANT_SCOPED_TABLES)

    # Seed the 10 canonical alert rules.
    # Use json_build_object() to avoid ":NUMBER" patterns that SQLAlchemy text()
    # misparses as named bind parameters (e.g. :60 → $1).
    op.execute(sa.text("""
        INSERT INTO analytics.bi_alert_rules (rule_code, rule_name, rule_config, is_active)
        VALUES
          ('arrears_escalation',    'Arrears Escalation',             json_build_object('days_overdue',60,'no_payment_days',30)::jsonb,     true),
          ('sinking_fund_shortfall','Sinking Fund Shortfall',         json_build_object('threshold_pct',0.15,'horizon_years',10)::jsonb,    true),
          ('compliance_due',        'Compliance Due Soon',            json_build_object('days_ahead',30)::jsonb,                            true),
          ('ppm_overdue',           'PPM Task Overdue',               '{}'::jsonb,                                                          true),
          ('health_score_drop',     'Building Health Score Drop',     json_build_object('drop_points',10,'window_days',30)::jsonb,           true),
          ('utility_spike',         'Utility Spend Spike',            json_build_object('pct_above_avg',20,'rolling_months',3)::jsonb,       true),
          ('ownership_propagation', 'Ownership Transfer Incomplete',  json_build_object('check_all',true)::jsonb,                           true),
          ('request_pattern',       'Smart Request Category Pattern', json_build_object('min_lots',3,'window_days',30)::jsonb,               true),
          ('asset_slippage',        'Asset Replacement Slippage',     '{}'::jsonb,                                                          true),
          ('owner_lot_mismatch',    'Owner/Lot Record Mismatch',      json_build_object('check_all',true)::jsonb,                           true)
        ON CONFLICT DO NOTHING
    """))

    # Seed dim_date spine: 2000-01-01 → 2055-12-31 (sa.text() avoids :: cast parse)
    op.execute(sa.text("""
        INSERT INTO analytics.dim_date (
            date_id, full_date, year, quarter, month, month_name,
            week_of_year, day_of_week, day_of_month, is_weekend,
            financial_year, financial_quarter, financial_month
        )
        SELECT
            TO_CHAR(d, 'YYYYMMDD')::INTEGER,
            d::DATE,
            EXTRACT(YEAR FROM d)::SMALLINT,
            EXTRACT(QUARTER FROM d)::SMALLINT,
            EXTRACT(MONTH FROM d)::SMALLINT,
            TO_CHAR(d, 'Month'),
            EXTRACT(WEEK FROM d)::SMALLINT,
            EXTRACT(DOW FROM d)::SMALLINT,
            EXTRACT(DAY FROM d)::SMALLINT,
            EXTRACT(DOW FROM d) IN (0, 6),
            CASE
                WHEN EXTRACT(MONTH FROM d) >= 7
                THEN 'FY' || (EXTRACT(YEAR FROM d) + 1)::TEXT
                ELSE 'FY' || EXTRACT(YEAR FROM d)::TEXT
            END,
            CASE
                WHEN EXTRACT(MONTH FROM d) BETWEEN 7 AND 9   THEN 'FY' || (EXTRACT(YEAR FROM d)+1)::TEXT || '-Q1'
                WHEN EXTRACT(MONTH FROM d) BETWEEN 10 AND 12 THEN 'FY' || (EXTRACT(YEAR FROM d)+1)::TEXT || '-Q2'
                WHEN EXTRACT(MONTH FROM d) BETWEEN 1 AND 3   THEN 'FY' || EXTRACT(YEAR FROM d)::TEXT || '-Q3'
                ELSE                                               'FY' || EXTRACT(YEAR FROM d)::TEXT || '-Q4'
            END,
            CASE
                WHEN EXTRACT(MONTH FROM d) >= 7 THEN (EXTRACT(MONTH FROM d) - 6)::INTEGER
                ELSE (EXTRACT(MONTH FROM d) + 6)::INTEGER
            END
        FROM generate_series('2000-01-01'::date, '2055-12-31'::date, '1 day'::interval) d
        ON CONFLICT (date_id) DO NOTHING
    """))


# ─────────────────────────────────────────────────────────────────────────────
# Downgrade
# ─────────────────────────────────────────────────────────────────────────────

def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0052_canonical_bi_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _TENANT_SCOPED_TABLES = [
        "dim_building", "dim_lot", "dim_owner", "bridge_lot_owner", "dim_supplier",
        "fact_levy_charge", "fact_levy_payment", "fact_arrears_snapshot",
        "fact_financial_balance", "fact_work_order", "fact_compliance_event",
        "fact_utility_bill", "fact_capex_plan", "fact_capex_actual",
        "fact_asset_condition_snapshot", "fact_building_health_snapshot",
        "fact_smart_request", "fact_ownership_transfer", "fact_occupancy_snapshot",
        "fact_true_cost_ownership", "fact_investor_yield_snapshot", "bi_alert_events",
    ]
    _disable_rls_analytics(_TENANT_SCOPED_TABLES)

    # Drop in reverse FK dependency order
    op.drop_table("bi_alert_events", schema="analytics")
    op.drop_table("bi_alert_rules", schema="analytics")
    op.drop_table("bi_etl_runs", schema="analytics")
    op.drop_table("fact_investor_yield_snapshot", schema="analytics")
    op.drop_table("fact_true_cost_ownership", schema="analytics")
    op.drop_table("fact_occupancy_snapshot", schema="analytics")
    op.drop_table("fact_ownership_transfer", schema="analytics")
    op.drop_table("fact_smart_request", schema="analytics")
    op.drop_table("fact_building_health_snapshot", schema="analytics")
    op.drop_table("fact_asset_condition_snapshot", schema="analytics")
    op.drop_table("fact_capex_actual", schema="analytics")
    op.drop_table("fact_capex_plan", schema="analytics")
    op.drop_table("fact_utility_bill", schema="analytics")
    op.drop_table("fact_compliance_event", schema="analytics")
    op.drop_table("fact_work_order", schema="analytics")
    op.drop_table("fact_financial_balance", schema="analytics")
    op.drop_table("fact_arrears_snapshot", schema="analytics")
    op.drop_table("fact_levy_payment", schema="analytics")
    op.drop_table("fact_levy_charge", schema="analytics")
    op.drop_table("bridge_lot_owner", schema="analytics")
    op.drop_table("dim_supplier", schema="analytics")
    op.drop_table("dim_owner", schema="analytics")
    op.drop_table("dim_lot", schema="analytics")
    op.drop_table("dim_building", schema="analytics")
    op.drop_table("dim_date", schema="analytics")
