# @featuretrace:financial_core — SQLAlchemy ORM models mapping finance.* tables.
# Layer: model
# Data flow: alembic/versions/000X_*.py (schema) -> these ORM classes ->
#            services/financial_core/adapters/db_postgres/ledger_repo.py (building-scoped).
# Related: backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          backend/alembic/versions/0003_finance_core_tables.py
#          backend/alembic/versions/0004_finance_extension_tables.py
#          backend/alembic/versions/0067_finance_expense_transactions.py
# Table: finance.funds, finance.trust_accounts, finance.gl_accounts,
#        finance.accounting_periods, finance.journal_entries, finance.journal_lines,
#        finance.levy_runs, finance.levy_items, finance.receipts,
#        finance.receipt_allocations, finance.expense_transactions, core.outbox
"""SQLAlchemy 2.x ORM models for the financial core.

These map to the PostgreSQL tables defined in the Alembic migrations (0002–0006).
They are ONLY used within the financial_core adapters — no other module imports these.

Naming: each class prefixed with "Pg" to distinguish from domain entities.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db_postgres.base import Base

RECORD_STATUS_ENUM = Enum(
    "draft", "active", "inactive", "archived",
    name="record_status",
    schema="core",
    native_enum=True,
    create_constraint=False,
)
FUND_TYPE_ENUM = Enum(
    "admin", "capital_works", "sinking", "special_purpose", "other",
    name="fund_type",
    schema="finance",
    native_enum=True,
    create_constraint=False,
)
ACCOUNT_TYPE_ENUM = Enum(
    "asset", "liability", "equity", "income", "expense",
    name="account_type",
    schema="finance",
    native_enum=True,
    create_constraint=False,
)
ENTRY_STATUS_ENUM = Enum(
    "draft", "pending_approval", "posted", "reversed", "voided",
    name="entry_status",
    schema="finance",
    native_enum=True,
    create_constraint=False,
)
ENTRY_DIRECTION_ENUM = Enum(
    "debit", "credit",
    name="entry_direction",
    schema="finance",
    native_enum=True,
    create_constraint=False,
)
PAYMENT_CHANNEL_ENUM = Enum(
    "bpay", "deft", "eft", "card", "cash", "cheque", "bank_transfer", "aba", "manual_adjustment",
    name="payment_channel",
    schema="finance",
    native_enum=True,
    create_constraint=False,
)


class PgFund(Base):
    __tablename__ = "funds"
    __table_args__ = {"schema": "finance"}

    fund_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    fund_code: Mapped[str] = mapped_column(Text, nullable=False)
    fund_name: Mapped[str] = mapped_column(Text, nullable=False)
    fund_type: Mapped[str] = mapped_column(FUND_TYPE_ENUM, nullable=False)
    opening_balance_cents: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(RECORD_STATUS_ENUM, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgTrustAccount(Base):
    __tablename__ = "trust_accounts"
    __table_args__ = {"schema": "finance"}

    trust_account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    fund_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance.funds.fund_id"), nullable=True
    )
    bank_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    masked_bsb: Mapped[Optional[str]] = mapped_column(Text)
    masked_account_number: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(RECORD_STATUS_ENUM, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgGlAccount(Base):
    __tablename__ = "gl_accounts"
    __table_args__ = {"schema": "finance"}

    gl_account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    account_code: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(ACCOUNT_TYPE_ENUM, nullable=False)
    status: Mapped[str] = mapped_column(RECORD_STATUS_ENUM, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgAccountingPeriod(Base):
    __tablename__ = "accounting_periods"
    __table_args__ = {"schema": "finance"}

    period_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    period_label: Mapped[str] = mapped_column(Text, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NOTE: there is no `locked_by` column on the live table — confirmed via
    # information_schema.columns during the 2026-07-24 re-audit. An earlier
    # session pass added this mapping based on a misread DuplicateColumnError
    # message and was never re-verified against the live schema directly;
    # it silently broke get_accounting_period_for_date() (UndefinedColumnError)
    # for every caller, invisible until this audit because all prior tests
    # used a mock repository. Do not re-add without confirming the column
    # exists via information_schema first.


class PgReconstructionExecutionBatch(Base):
    __tablename__ = "reconstruction_execution_batches"
    __table_args__ = {"schema": "finance"}

    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_period_label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="manifest_pending")
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expected_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[Optional[str]] = mapped_column(Text)
    applied_by: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_row_count: Mapped[Optional[int]] = mapped_column(Integer)
    actual_amount_cents: Mapped[Optional[int]] = mapped_column(BigInteger)
    failure_count: Mapped[Optional[int]] = mapped_column(Integer)


class PgReconstructionExecutionBatchItem(Base):
    __tablename__ = "reconstruction_execution_batch_items"
    __table_args__ = {"schema": "finance"}

    batch_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance.reconstruction_execution_batches.batch_id"),
        nullable=False,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bank_transaction_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_name: Mapped[Optional[str]] = mapped_column(Text)
    lot_number: Mapped[Optional[str]] = mapped_column(Text)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    expense_journal_entry_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    recharge_journal_entry_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PgEvidenceDocument(Base):
    """finance.evidence_documents — created by migration 0044, extended by 0051
    (approval metadata, supersession) and 0068 (expense-side document_type values).

    Added here 2026-07-24: PgJournalEntry.evidence_document_id declares
    ForeignKey("finance.evidence_documents.document_id"), but no SQLAlchemy model
    for this table existed anywhere in the codebase — every prior reference was a
    raw-SQL script, never an ORM class. SQLAlchemy's mapper configuration resolves
    ALL registered FKs the first time any ORM object is used in a process, so this
    silently broke every call to FinancialCoreService.record_payment() (the sole
    authorised ledger-write path, per financial_matching.py's own comment) with
    "could not find table 'finance.evidence_documents' to generate a foreign key"
    — found while individually reviewing East Gate 13195's reconstruction queue
    items; the failure is process-wide, not building- or row-specific.
    """
    __tablename__ = "evidence_documents"
    __table_args__ = {"schema": "finance"}

    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    building_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    declared_total_cents: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default={})
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_system: Mapped[Optional[str]] = mapped_column(Text)
    import_batch_id: Mapped[Optional[str]] = mapped_column(Text)
    supersedes_document_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance.evidence_documents.document_id"), nullable=True,
    )


class PgJournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = {"schema": "finance"}

    journal_entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    fund_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    period_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance.accounting_periods.period_id"),
        nullable=False,
    )
    entry_number: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[Optional[str]] = mapped_column(Text)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(ENTRY_STATUS_ENUM, nullable=False, default="draft")
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    approved_by: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    evidence_document_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("finance.evidence_documents.document_id"))
    reversal_of_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance.journal_entries.journal_entry_id"),
        nullable=True,
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(Text)
    prev_entry_hash: Mapped[Optional[str]] = mapped_column(Text)
    entry_hash: Mapped[Optional[str]] = mapped_column(Text)
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["PgJournalLine"]] = relationship(
        "PgJournalLine", back_populates="entry", lazy="selectin"
    )


class PgJournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = {"schema": "finance"}

    journal_line_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    journal_entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance.journal_entries.journal_entry_id"),
        nullable=False,
    )
    gl_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance.gl_accounts.gl_account_id"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(ENTRY_DIRECTION_ENUM, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gst_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lot_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    party_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    narration: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    entry: Mapped["PgJournalEntry"] = relationship("PgJournalEntry", back_populates="lines")


class PgLevyRun(Base):
    __tablename__ = "levy_runs"
    __table_args__ = {"schema": "finance"}

    levy_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    financial_year: Mapped[str] = mapped_column(Text, nullable=False)
    quarter_no: Mapped[Optional[int]] = mapped_column(Integer)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    # Column added by migration 0069 but never mapped here until now — every
    # levy_run ever created via create_levy() silently fell back to the
    # column's DB default ('ordinary') regardless of intent, since an
    # unmapped column is invisible to the ORM.
    levy_run_type: Mapped[str] = mapped_column(Text, nullable=False, default="ordinary")
    # Nullable, added by migration 0076 — populated only by create_historical_levy();
    # the live create_levy() path never sets it (NULL, excluded from the partial unique
    # index levy_runs_natural_key_idx). See that migration's docstring for why a run-level
    # fund column was needed despite the live path enforcing "one run = one fund" only at
    # the application layer.
    fund_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance.funds.fund_id"), nullable=True,
    )
    # Added by migration 0077 — minimum grace_deadline_date across this run's
    # levy_items; earliest_past_grace_item_id is an optimization hint, not a
    # required join. See that migration's docstring / arrears_calculation_spec.
    grace_deadline_date: Mapped[date] = mapped_column(Date, nullable=False)
    earliest_past_grace_item_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance.levy_items.levy_item_id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgLevyItem(Base):
    __tablename__ = "levy_items"
    __table_args__ = {"schema": "finance"}

    levy_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    levy_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance.levy_runs.levy_run_id"),
        nullable=False,
    )
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    owner_party_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    fund_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    principal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gst_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    interest_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    recovery_costs_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    paid_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="issued")
    # Present in the base schema (0004) but previously unmapped here — needed to
    # join a levy_charge journal_entries row back to the levy_item(s) it charges,
    # for classify_levy_journals()'s downstream-reference check.
    journal_entry_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Added by migration 0076 — 'resolved' (default) or 'unresolved' (owner_party_id
    # resolved to the core.parties party_type='unresolved_placeholder' sentinel because
    # no primary owner was found for this lot at the charge's issue_date). Durable
    # provenance, not solely journal metadata, so it survives a unit_levy_ledger
    # projection rebuild — see historical_levy_charge_generator.py's module docstring.
    owner_resolution_status: Mapped[str] = mapped_column(Text, nullable=False, default="resolved")
    # Added by migration 0077 — due_date + grace_period_days from levy_rules;
    # is_past_grace/days_overdue are derived and kept in sync by that migration's
    # levy_items_grace_deadline_trigger on INSERT/UPDATE, not written by the ORM.
    grace_deadline_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_past_grace: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    days_overdue: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgReceipt(Base):
    __tablename__ = "receipts"
    __table_args__ = {"schema": "finance"}

    receipt_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    trust_account_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    bank_transaction_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    payer_party_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    lot_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(PAYMENT_CHANNEL_ENUM, nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_reference: Mapped[Optional[str]] = mapped_column(Text)
    journal_entry_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    reconstruction_batch_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgReceiptAllocation(Base):
    __tablename__ = "receipt_allocations"
    __table_args__ = {"schema": "finance"}

    allocation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    # tenant_id added by migration 0009; must match parent receipt's tenant_id
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    receipt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance.receipts.receipt_id"), nullable=False
    )
    levy_item_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    allocation_type: Mapped[str] = mapped_column(Text, nullable=False)
    allocated_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgExpenseTransaction(Base):
    __tablename__ = "expense_transactions"
    __table_args__ = {"schema": "finance"}

    expense_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    fund_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    gl_account_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    vendor_name: Mapped[Optional[str]] = mapped_column(Text)
    invoice_number: Mapped[Optional[str]] = mapped_column(Text)
    category_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    financial_year: Mapped[Optional[str]] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gst_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    journal_entry_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance.journal_entries.journal_entry_id"), nullable=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, default="historical_import")
    derivation_level: Mapped[str] = mapped_column(Text, nullable=False, default="aggregate_balancing")
    reconstruction_batch_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default={})
    idempotency_key: Mapped[Optional[str]] = mapped_column(Text)
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PgOutbox(Base):
    __tablename__ = "outbox"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    aggregate_type: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    causation_id: Mapped[Optional[str]] = mapped_column(Text)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Added by migration 0086 (GAP-FIN-061) for independent per-destination Redis/Mongo delivery
    # tracking, back when the relay published to both. Redis was removed 2026-08-11 (verified live
    # that its only consumers had no deployment mechanism -- see outbox_relay.py's module
    # docstring), so the relay is Mongo-only again and none of these four columns are currently
    # written -- `published_at` alone is the operative field. Left in place rather than dropped
    # (cheaper, reversible; no cost to idle nullable columns) in case a real second destination
    # becomes a genuine need later.
    redis_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    mongo_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    redis_error: Mapped[Optional[str]] = mapped_column(Text)
    mongo_error: Mapped[Optional[str]] = mapped_column(Text)
