"""0040 — Finance: water_bills, council_rates, trust_interest_postings.

Adds three tables to the existing `finance` schema for financial record types
present in MongoDB but absent from PostgreSQL as at 0039.

  finance.water_bills             — water usage and supply charges per lot/scheme
                                   (Icon Water, Sydney Water etc.)
  finance.council_rates           — annual/quarterly rates notices per lot/scheme
                                   (ACT Government, local councils)
  finance.trust_interest_postings — immutable audit trail of interest earned in
                                   each trust account per period; supports
                                   jurisdictional rules (ACT: interest belongs to
                                   scheme; NSW: statutory account; VIC: scheme)

All money columns are BIGINT cents. Float is forbidden in the finance schema.
All three tables follow the existing finance.* RLS pattern:
  USING (tenant_id = core.current_tenant_id())

Statutory basis: ACT Agents Act 2003 / NSW Property and Stock Agents Act 2002.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # finance.water_bills
    # Per-lot or common-area water charges.
    # OC pays usage for common areas; individual owners pay their own
    # supply charges. Both record types share this table (lot_id nullable).
    # ------------------------------------------------------------------
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0040_finance_water_bills_council_rates_trust_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "water_bills",
        sa.Column(
            "water_bill_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        # NULL = common-area bill paid by OC; otherwise per-lot owner charge
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("billing_period_start", sa.Date(), nullable=False),
        sa.Column("billing_period_end", sa.Date(), nullable=False),
        # Meter readings (kL) — nullable for estimated bills
        sa.Column("meter_reading_start", sa.Numeric(12, 3), nullable=True),
        sa.Column("meter_reading_end", sa.Numeric(12, 3), nullable=True),
        sa.Column("usage_kl", sa.Numeric(12, 3), nullable=True),
        # All amounts in cents (BIGINT — no float in finance schema)
        sa.Column("usage_charge_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("supply_charge_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "payment_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unpaid'"),
        ),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Link to finance.receipts when paid through the portal
        sa.Column(
            "receipt_id",
            sa.UUID(),
            sa.ForeignKey("finance.receipts.receipt_id"),
            nullable=True,
        ),
        # Provider (Icon Water, Sydney Water, Yarra Valley Water, etc.)
        sa.Column("provider_name", sa.Text(), nullable=True),
        # Provider's own bill reference / account number
        sa.Column("provider_reference", sa.Text(), nullable=True),
        # Reference to stored bill document (PDF scan / e-bill)
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "billing_period_end > billing_period_start",
            name="water_bills_period_chk",
        ),
        sa.CheckConstraint(
            "total_amount_cents >= 0",
            name="water_bills_total_positive_chk",
        ),
        sa.CheckConstraint(
            "payment_status IN ('unpaid', 'paid', 'partial', 'waived', 'disputed')",
            name="water_bills_status_chk",
        ),
        schema="finance",
    )

    op.create_index(
        "water_bills_scheme_period_idx",
        "water_bills",
        ["scheme_id", "billing_period_start"],
        schema="finance",
    )
    op.create_index(
        "water_bills_lot_idx",
        "water_bills",
        ["lot_id"],
        schema="finance",
    )
    op.create_index(
        "water_bills_scheme_status_idx",
        "water_bills",
        ["scheme_id", "payment_status"],
        schema="finance",
    )
    op.create_index(
        "water_bills_tenant_idx",
        "water_bills",
        ["tenant_id"],
        schema="finance",
    )

    # ------------------------------------------------------------------
    # finance.council_rates
    # Annual or quarterly rates notices from local government authorities.
    # ACT: ACT Revenue Office (land tax + general rates); NSW: local council.
    # lot_id nullable for scheme-wide rates.
    # ------------------------------------------------------------------
    op.create_table(
        "council_rates",
        sa.Column(
            "rates_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        # Financial year (e.g. 2026 = FY2025-26)
        sa.Column("financial_year", sa.SmallInteger(), nullable=False),
        # 1-4 for quarterly; NULL for annual lump-sum
        sa.Column("quarter", sa.SmallInteger(), nullable=True),
        # AUV or Unimproved Capital Value at assessment date — informational only
        sa.Column("assessed_value_cents", sa.BigInteger(), nullable=True),
        # Component breakdown
        sa.Column("general_rate_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("waste_levy_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("emergency_services_levy_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("land_tax_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "payment_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unpaid'"),
        ),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "receipt_id",
            sa.UUID(),
            sa.ForeignKey("finance.receipts.receipt_id"),
            nullable=True,
        ),
        # Issuing authority (ACT Government, City of Sydney, etc.)
        sa.Column("authority_name", sa.Text(), nullable=True),
        # Authority's rates notice / assessment reference number
        sa.Column("authority_reference", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "quarter IS NULL OR quarter BETWEEN 1 AND 4",
            name="council_rates_quarter_chk",
        ),
        sa.CheckConstraint(
            "total_amount_cents >= 0",
            name="council_rates_total_positive_chk",
        ),
        sa.CheckConstraint(
            "payment_status IN ('unpaid', 'paid', 'partial', 'waived', 'disputed')",
            name="council_rates_status_chk",
        ),
        schema="finance",
    )

    op.create_index(
        "council_rates_scheme_fy_idx",
        "council_rates",
        ["scheme_id", "financial_year", "quarter"],
        schema="finance",
    )
    op.create_index(
        "council_rates_lot_idx",
        "council_rates",
        ["lot_id"],
        schema="finance",
    )
    op.create_index(
        "council_rates_scheme_status_idx",
        "council_rates",
        ["scheme_id", "payment_status"],
        schema="finance",
    )
    op.create_index(
        "council_rates_tenant_idx",
        "council_rates",
        ["tenant_id"],
        schema="finance",
    )

    # ------------------------------------------------------------------
    # finance.trust_interest_postings
    # Immutable audit trail of interest calculated and posted for each
    # trust account period. Append-only — no updates, no deletes.
    #
    # interest_belongs_to drives which ledger account receives the credit:
    #   'scheme'            — posted directly to scheme income (ACT, VIC)
    #   'statutory_account' — posted to a separate statutory interest account (NSW)
    #
    # interest_cents = ROUND(principal_cents * rate_pa * days / 36500).
    # Formula enforcement is application-level (FinancialCoreService); the DB
    # enforces only sign and range via the CHECK constraints below.
    # ------------------------------------------------------------------
    op.create_table(
        "trust_interest_postings",
        sa.Column(
            "posting_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column(
            "trust_account_id",
            sa.UUID(),
            sa.ForeignKey("finance.trust_accounts.trust_account_id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        # Principal balance that earned interest (cents)
        sa.Column("principal_cents", sa.BigInteger(), nullable=False),
        # Annual rate as a decimal fraction (e.g. 0.04500 for 4.5% p.a.)
        sa.Column("rate_pa", sa.Numeric(9, 7), nullable=False),
        # Actual calendar days in the interest period
        sa.Column("days", sa.Integer(), nullable=False),
        # Computed: ROUND(principal_cents * rate_pa * days / 36500)
        sa.Column("interest_cents", sa.BigInteger(), nullable=False),
        # Jurisdiction rules determine which account receives the credit
        sa.Column(
            "jurisdiction",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'ACT'"),
        ),
        # 'scheme' or 'statutory_account' (from JurisdictionalRuleEngine)
        sa.Column(
            "interest_belongs_to",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'scheme'"),
        ),
        # Links to the ledger entry that recorded this interest posting
        sa.Column(
            "journal_entry_id",
            sa.UUID(),
            sa.ForeignKey("finance.journal_entries.journal_entry_id"),
            nullable=True,
        ),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "period_end > period_start",
            name="trust_interest_postings_period_chk",
        ),
        sa.CheckConstraint(
            "days > 0",
            name="trust_interest_postings_days_positive_chk",
        ),
        sa.CheckConstraint(
            "principal_cents >= 0",
            name="trust_interest_postings_principal_positive_chk",
        ),
        sa.CheckConstraint(
            "interest_cents >= 0",
            name="trust_interest_postings_interest_positive_chk",
        ),
        sa.CheckConstraint(
            "interest_belongs_to IN ('scheme', 'statutory_account')",
            name="trust_interest_postings_belongs_to_chk",
        ),
        # Prevent double-posting the same account/period pair
        sa.UniqueConstraint(
            "trust_account_id", "period_start", "period_end",
            name="trust_interest_postings_account_period_ux",
        ),
        schema="finance",
    )

    op.create_index(
        "trust_interest_postings_account_period_idx",
        "trust_interest_postings",
        ["trust_account_id", "period_start"],
        schema="finance",
    )
    op.create_index(
        "trust_interest_postings_scheme_idx",
        "trust_interest_postings",
        ["scheme_id"],
        schema="finance",
    )
    op.create_index(
        "trust_interest_postings_tenant_idx",
        "trust_interest_postings",
        ["tenant_id"],
        schema="finance",
    )

    # ------------------------------------------------------------------
    # RLS — same pattern as all other finance.* tables (0008)
    # ------------------------------------------------------------------
    _NEW_TABLES = ["water_bills", "council_rates", "trust_interest_postings"]
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE finance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE finance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_finance_{table} ON finance.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )

    # Grants to application role (same DO-block pattern as other migrations)
    op.execute(
        """
        DO $$ BEGIN
            GRANT SELECT, INSERT, UPDATE ON finance.water_bills TO strataos_user;
            GRANT SELECT, INSERT, UPDATE ON finance.council_rates TO strataos_user;
            GRANT SELECT, INSERT ON finance.trust_interest_postings TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0040_finance_water_bills_council_rates_trust_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _NEW_TABLES = ["trust_interest_postings", "council_rates", "water_bills"]
    for table in _NEW_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_finance_{table} ON finance.{table}"
        )
        op.execute(f"ALTER TABLE finance.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("trust_interest_postings_tenant_idx", table_name="trust_interest_postings", schema="finance")
    op.drop_index("trust_interest_postings_scheme_idx", table_name="trust_interest_postings", schema="finance")
    op.drop_index("trust_interest_postings_account_period_idx", table_name="trust_interest_postings", schema="finance")
    op.drop_table("trust_interest_postings", schema="finance")

    op.drop_index("council_rates_tenant_idx", table_name="council_rates", schema="finance")
    op.drop_index("council_rates_scheme_status_idx", table_name="council_rates", schema="finance")
    op.drop_index("council_rates_lot_idx", table_name="council_rates", schema="finance")
    op.drop_index("council_rates_scheme_fy_idx", table_name="council_rates", schema="finance")
    op.drop_table("council_rates", schema="finance")

    op.drop_index("water_bills_tenant_idx", table_name="water_bills", schema="finance")
    op.drop_index("water_bills_scheme_status_idx", table_name="water_bills", schema="finance")
    op.drop_index("water_bills_lot_idx", table_name="water_bills", schema="finance")
    op.drop_index("water_bills_scheme_period_idx", table_name="water_bills", schema="finance")
    op.drop_table("water_bills", schema="finance")
