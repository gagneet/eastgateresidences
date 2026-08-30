"""
Trust Accounting Models — Double-Entry Ledger

Implements Australian trust accounting requirements for Strata Management:
- Separate ledgers per fund type (admin_fund, capital_works_fund, special_levy)
- Immutable double-entry journal entries
- Per-building isolation (multi-tenancy)
- Audit trail on every transaction

Collections used:
  trust_ledger_entries   : journal lines (debit side or credit side)
  trust_ledger_accounts  : account master (one per building × fund type × account type)
  trust_ledger_batches   : payment batches (ABA, BPAY, manual)
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Optional


# ─── Constants ────────────────────────────────────────────────────────────────

class FundType(str, Enum):
    ADMIN = "admin_fund"
    CAPITAL_WORKS = "capital_works_fund"
    SPECIAL_LEVY = "special_levy"
    OPERATING = "operating"  # catch-all for non-strata funds


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    INCOME = "income"
    EXPENSE = "expense"
    EQUITY = "equity"


class EntryType(str, Enum):
    """Semantic transaction types for trust_ledger_entries (immutable audit log).

    Positive amount_cents = money in (funds increase).
    Negative amount_cents = money out (funds decrease).
    Corrections MUST be made via REVERSAL entries, never by updating existing rows.
    """
    # Income / receipts
    LEVY_RAISED = "levy_raised"  # Lot levy raised in the ledger (receivable created)
    PAYMENT_RECEIVED = "payment_received"  # Cash received from lot owner
    INTEREST_INCOME = "interest_income"  # Bank interest credited to fund
    GRANT = "grant"  # Government grant, insurance proceeds
    # Expenditure / disbursements
    EXPENSE = "expense"  # Payment to supplier / creditor
    MANAGEMENT_FEE = "management_fee"  # Strata manager fee
    INSURANCE = "insurance"  # Insurance premium
    # Transfers & adjustments
    FUND_TRANSFER = "fund_transfer"  # Transfer between admin_fund ↔ capital_works_fund
    ADJUSTMENT = "adjustment"  # Manual correction (requires approval)
    OPENING_BALANCE = "opening_balance"  # Seed / carry-forward from prior period
    REVERSAL = "reversal"  # Corrects a prior entry (links via reversal_of_entry_id)
    # Legacy (kept for backward compat — new code should not use)
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerStatus(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    VOIDED = "voided"


class BatchType(str, Enum):
    ABA = "aba"
    BPAY = "bpay"
    DIRECT = "direct"
    CHEQUE = "cheque"
    TRANSFER = "transfer"


class BatchStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    CLEARED = "cleared"
    REJECTED = "rejected"


# ─── Account Master ───────────────────────────────────────────────────────────

class TrustAccountCreate(BaseModel):
    """Create a trust ledger account for a building/fund."""
    building_id: str
    fund_type: FundType
    account_type: AccountType
    account_code: str  # e.g. "1001", "2001"
    account_name: str  # e.g. "NAB Trust Account - Admin Fund"
    bsb: Optional[str] = None
    account_number: Optional[str] = None  # stored encrypted
    opening_balance: float = 0.0
    currency: str = "AUD"
    is_active: bool = True
    notes: Optional[str] = None


class TrustAccountResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    fund_type: str
    account_type: str
    account_code: str
    account_name: str
    bsb: Optional[str] = None
    account_number: Optional[str] = None  # redacted in responses
    current_balance: float = 0.0
    opening_balance: float = 0.0
    currency: str = "AUD"
    is_active: bool
    created_at: str
    updated_at: Optional[str] = None


# ─── Journal Entry (single line) ─────────────────────────────────────────────

class JournalLineCreate(BaseModel):
    """One side of a double-entry journal."""
    account_id: str  # trust ledger account _id
    entry_type: EntryType
    amount: float = Field(..., gt=0)
    description: Optional[str] = None


class JournalEntryCreate(BaseModel):
    """A balanced double-entry journal transaction.

    The sum of all debit lines MUST equal the sum of all credit lines.
    """
    building_id: str
    fund_type: FundType
    date: str  # ISO-8601 date, e.g. "2026-03-17"
    reference: str  # cheque#, invoice#, BPAY ref, etc.
    narration: str
    lines: List[JournalLineCreate]  # ≥ 2 lines, balanced
    source_type: Optional[str] = None  # "levy" | "invoice" | "bank_feed" | "manual"
    source_id: Optional[str] = None  # foreign key to levy, invoice, etc.
    batch_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}


class JournalEntryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    fund_type: str
    date: str
    reference: str
    narration: str
    lines: List[Dict[str, Any]]
    status: str
    is_balanced: bool
    debit_total: float
    credit_total: float
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    batch_id: Optional[str] = None
    created_by: str
    created_at: str
    voided_at: Optional[str] = None
    voided_by: Optional[str] = None
    void_reason: Optional[str] = None


# ─── Payment Batch ────────────────────────────────────────────────────────────

class PaymentItem(BaseModel):
    """A single payment within a batch."""
    payee_name: str
    bsb: str
    account_number: str
    amount: float = Field(..., gt=0)
    reference: str
    lodgement_ref: Optional[str] = None
    remittance_email: Optional[str] = None
    invoice_id: Optional[str] = None


class PaymentBatchCreate(BaseModel):
    """Create a payment batch (ABA / BPAY / Direct)."""
    building_id: str
    fund_type: FundType
    batch_type: BatchType
    description: str
    payment_date: str  # ISO-8601
    items: List[PaymentItem]  # ≥ 1
    is_test_data: bool = False


class PaymentBatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    fund_type: str
    batch_type: str
    description: str
    payment_date: str
    status: str
    total_amount: float
    item_count: int
    items: List[Dict[str, Any]]
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    submitted_at: Optional[str] = None
    aba_file_content: Optional[str] = None  # base64-encoded ABA file
    is_test_data: bool = False
    created_by: str
    created_at: str


# ─── Fund Balance ─────────────────────────────────────────────────────────────

class FundBalanceResponse(BaseModel):
    """Current balance summary per fund for a building."""
    building_id: str
    fund_type: str
    account_code: str
    account_name: str
    debit_total: float
    credit_total: float
    net_balance: float
    as_at: str


# ─── Trial Balance ────────────────────────────────────────────────────────────

class TrialBalanceResponse(BaseModel):
    """Trial balance across all accounts for a building."""
    building_id: str
    as_at: str
    accounts: List[Dict[str, Any]]
    total_debits: float
    total_credits: float
    is_balanced: bool


# ─── BuildingTrustConfig ───────────────────────────────────────────────────────

class BuildingTrustConfig(BaseModel):
    """Trust accounting configuration for a single building.

    This is a subdocument embedded in the buildings collection.
    All financial parameters are per-building and independently configurable.
    RULE 5: No financial constant may be hardcoded in application code.
    """
    model_config = ConfigDict(extra='ignore')

    current_financial_year: Optional[str] = None  # e.g. "2026-27"

    admin_fund_annual_budget_cents: int = 0
    sinking_fund_annual_budget_cents: int = 0

    quarterly_due_dates: List[str] = []  # 4 ISO date strings "YYYY-MM-DD"
    grace_period_days: int = 14

    arrears_interest_rate: float = 0.10

    total_uoe: int = 0

    deft_biller_code_admin: Optional[str] = None
    deft_biller_code_sinking: Optional[str] = None

    bank_name: Optional[str] = None

    arrears_reminder_days: int = 14
    arrears_formal_notice_days: int = 21
    arrears_interest_charge_days: int = 30
    arrears_legal_flag_days: int = 60
    manager_alert_email: Optional[str] = None

    is_trust_configured: bool = False
    trust_configured_at: Optional[str] = None
    trust_configured_by: Optional[str] = None


class BuildingTrustConfigUpdate(BaseModel):
    """Partial update for building trust config."""
    model_config = ConfigDict(extra='ignore')

    current_financial_year: Optional[str] = None
    admin_fund_annual_budget_cents: Optional[int] = None
    sinking_fund_annual_budget_cents: Optional[int] = None
    quarterly_due_dates: Optional[List[str]] = None
    grace_period_days: Optional[int] = None
    arrears_interest_rate: Optional[float] = None
    deft_biller_code_admin: Optional[str] = None
    deft_biller_code_sinking: Optional[str] = None
    bank_name: Optional[str] = None
    arrears_reminder_days: Optional[int] = None
    arrears_formal_notice_days: Optional[int] = None
    arrears_interest_charge_days: Optional[int] = None
    arrears_legal_flag_days: Optional[int] = None
    manager_alert_email: Optional[str] = None


# ─── TrustAccount (Phase 1 extension) ────────────────────────────────────────

class AccountCategory(str, Enum):
    """Physical bank account category — determines interest behaviour."""
    TRANSACTION = "transaction"  # Standard transaction/cheque account (low/no interest)
    SAVINGS = "savings"  # High-interest savings account
    TERM_DEPOSIT = "term_deposit"  # Fixed-term deposit (higher interest, locked for period)
    OFFSET = "offset"  # Offset account linked to a loan


class InterestCalcMethod(str, Enum):
    DAILY_BALANCE = "daily_balance"  # Interest on daily closing balance
    MONTHLY_BALANCE = "monthly_balance"  # Interest on month-end balance


class TrustAccountPhase1Create(BaseModel):
    """Create a trust account with Phase 1 fields.
    
    building_id is not accepted here — it is always sourced from the JWT (RULE 4).
    """
    account_type: str  # 'admin_fund' | 'sinking_fund' | 'special_purpose'
    bsb: str
    account_number_masked: str  # display only: "****4567"
    bank_name: str
    account_name: str
    deft_biller_code: Optional[str] = None
    current_balance_cents: int = 0
    balance_snapshot_at: Optional[str] = None  # ISO timestamp of last current_balance_cents update
    last_reconciled_at: Optional[str] = None
    last_reconciled_balance_cents: int = 0
    is_active: bool = True
    created_by: Optional[str] = None
    # Bank interest fields
    account_category: AccountCategory = AccountCategory.TRANSACTION
    interest_rate_pa: float = 0.0  # Annual interest rate, e.g. 0.045 = 4.5%
    interest_calc_method: InterestCalcMethod = InterestCalcMethod.DAILY_BALANCE
    term_deposit_maturity_date: Optional[str] = None  # ISO date; only for term_deposit
    notes: Optional[str] = None


class TrustAccountPhase1Response(BaseModel):
    """Trust account response with money fields."""
    model_config = ConfigDict(extra='ignore')
    id: str
    building_id: str
    account_type: str
    bsb: str
    account_number_masked: str
    bank_name: str
    account_name: str
    deft_biller_code: Optional[str] = None
    current_balance_cents: int
    current_balance_display: str
    last_reconciled_at: Optional[str] = None
    last_reconciled_balance_cents: int
    last_reconciled_balance_display: str
    is_active: bool
    created_at: str
    updated_at: str
    # Bank interest fields
    account_category: AccountCategory = AccountCategory.TRANSACTION
    interest_rate_pa: float = 0.0
    interest_calc_method: InterestCalcMethod = InterestCalcMethod.DAILY_BALANCE
    term_deposit_maturity_date: Optional[str] = None
    last_interest_posted_at: Optional[str] = None
    last_interest_period_end: Optional[str] = None
    interest_ytd_cents: int = 0
    interest_ytd_display: str = "$0.00"
    notes: Optional[str] = None


class BankAccountUpdate(BaseModel):
    """Partial update for trust account bank/interest settings."""
    bank_name: Optional[str] = None
    account_name: Optional[str] = None
    bsb: Optional[str] = None
    account_number_masked: Optional[str] = None
    account_category: Optional[AccountCategory] = None
    interest_rate_pa: Optional[float] = None
    interest_calc_method: Optional[InterestCalcMethod] = None
    term_deposit_maturity_date: Optional[str] = None
    deft_biller_code: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class InterestPostingRequest(BaseModel):
    """Request to post bank interest income for a period."""
    period_start: str  # ISO date "2026-03-01"
    period_end: str  # ISO date "2026-03-31"
    # If amount_cents is provided it overrides the auto-calculation
    amount_cents: Optional[int] = None
    # Distribution override — by default split proportionally by fund balance
    admin_amount_cents: Optional[int] = None
    sinking_amount_cents: Optional[int] = None
    reference: Optional[str] = None
    notes: Optional[str] = None


# ─── TrustLevySchedule ───────────────────────────────────────────────────────

class TrustLevyScheduleCreate(BaseModel):
    """Create a levy schedule entry for one unit in one quarter."""
    building_id: str
    unit_id: str
    unit_number: str
    lot_number: int
    uoe_snapshot: int
    total_uoe_snapshot: int
    admin_budget_cents_snapshot: int
    sinking_budget_cents_snapshot: int
    quarter: str  # "2026-Q1"
    financial_year: str  # "2026-27"
    due_date: str  # ISO date string
    grace_period_days: int
    admin_fund_cents: int
    sinking_fund_cents: int
    special_levy_cents: int = 0
    total_cents: int
    deft_crn: str
    status: str = 'pending'
    paid_cents: int = 0
    outstanding_cents: int = 0
    generated_by: Optional[str] = None


class TrustLevyScheduleResponse(BaseModel):
    """Levy schedule response with money display fields."""
    model_config = ConfigDict(extra='ignore')
    id: str
    building_id: str
    unit_id: str
    unit_number: str
    lot_number: int
    uoe_snapshot: int
    total_uoe_snapshot: int
    quarter: str
    financial_year: str
    due_date: str
    grace_period_days: int
    admin_fund_cents: int
    admin_fund_display: str
    sinking_fund_cents: int
    sinking_fund_display: str
    special_levy_cents: int
    total_cents: int
    total_display: str
    deft_crn: str
    status: str
    paid_cents: int
    paid_display: str
    outstanding_cents: int
    outstanding_display: str
    paid_at: Optional[str] = None
    overdue_since: Optional[str] = None
    interest_accrued_cents: int = 0
    drb_stage: str = 'none'
    notes: Optional[str] = None
    generated_at: str
    updated_at: str
    # Advance payment fields (payment received before due_date)
    is_advance_payment: bool = False
    advance_payment_date: Optional[str] = None  # date payment was received (if before due_date)
    advance_days: int = 0  # days early the payment was received
    advance_interest_earned_cents: int = 0  # bank interest earned by OC on this advance amount
    advance_interest_posted: bool = False  # whether interest has been formally posted to the fund
    advance_interest_posted_at: Optional[str] = None


# ─── TrustTransaction (Phase 1 extension) ────────────────────────────────────

class TrustTransactionPhase1Create(BaseModel):
    """Create an immutable trust transaction.
    
    building_id is not accepted here — it is always sourced from the JWT (RULE 4).
    """
    trust_account_id: str
    trust_levy_schedule_id: Optional[str] = None
    unit_id: Optional[str] = None
    unit_number: Optional[str] = None
    type: str  # 'receipt' | 'disbursement' | 'bank_charge' | 'interest' | 'reversal' | 'adjustment'
    amount_cents: int
    gst_cents: int = 0
    description: str
    reference: Optional[str] = None
    payee_payer: Optional[str] = None
    payment_method: Optional[str] = None
    deft_transaction_id: Optional[str] = None
    deft_crn: Optional[str] = None
    is_reconciled: bool = False
    bank_date: Optional[str] = None
    reversal_of_id: Optional[str] = None
    requires_approval: bool = False
    created_by: Optional[str] = None


class TrustTransactionPhase1Response(BaseModel):
    """Trust transaction response."""
    model_config = ConfigDict(extra='ignore')
    id: str
    building_id: str
    trust_account_id: str
    trust_levy_schedule_id: Optional[str] = None
    unit_id: Optional[str] = None
    unit_number: Optional[str] = None
    type: str
    amount_cents: int
    amount_display: str
    gst_cents: int
    description: str
    reference: Optional[str] = None
    payee_payer: Optional[str] = None
    payment_method: Optional[str] = None
    deft_transaction_id: Optional[str] = None
    deft_crn: Optional[str] = None
    is_reconciled: bool
    is_reversed: bool = False
    reversal_of_id: Optional[str] = None
    running_balance_cents: Optional[int] = None
    running_balance_display: Optional[str] = None
    requires_approval: bool
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    created_at: str
    created_by: Optional[str] = None


# ─── TrustAuditLog ────────────────────────────────────────────────────────────

class TrustAuditLogCreate(BaseModel):
    """Create a trust audit log entry."""
    building_id: str
    action: str
    entity_type: str
    entity_id: str
    performed_by: str
    performer_role: str
    performer_email: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    before_snapshot: Optional[dict] = None
    after_snapshot: Optional[dict] = None
    metadata: Optional[dict] = None


# ─── DeftNotification ────────────────────────────────────────────────────────

class DeftNotificationCreate(BaseModel):
    """Create a DEFT notification record (raw webhook storage)."""
    raw_payload: dict
    deft_transaction_id: str
    deft_crn: Optional[str] = None
    amount_cents: Optional[int] = None
    payment_date: Optional[str] = None
    building_id: Optional[str] = None
    status: str = 'received'
    matched_levy_schedule_id: Optional[str] = None
    matched_transaction_id: Optional[str] = None
    error_message: Optional[str] = None
    processed_at: Optional[str] = None


# ─── Levy Generation ─────────────────────────────────────────────────────────

class LevyGenerateRequest(BaseModel):
    """Request to generate levy schedules for a building/quarter."""
    quarter: str  # "2026-Q1"
    financial_year: str  # "2026-27"
    due_date: str  # ISO date string


class LevyGenerateResponse(BaseModel):
    """Response from levy generation."""
    created: int
    skipped: int
    total_units: int
    admin_quarterly_cents: int
    admin_quarterly_display: str
    sinking_quarterly_cents: int
    sinking_quarterly_display: str
    rounding_difference_cents: int
    quarter: str
    financial_year: str


# ─── Money helper (Python equivalent of frontend money.ts) ───────────────────

def format_aud(cents: int) -> str:
    """Format integer cents as AUD display string."""
    negative = cents < 0
    abs_cents = abs(cents)
    dollars = abs_cents // 100
    remainder = abs_cents % 100
    formatted = f"${dollars:,}.{remainder:02d}"
    return f"-{formatted}" if negative else formatted


# ─── TrustReconciliationRun ───────────────────────────────────────────────────

class ReconciliationStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TrustReconciliationRunCreate(BaseModel):
    """Create a bank reconciliation run for a trust account."""
    bank_account_id: str
    statement_start_date: str  # ISO-8601 date
    statement_end_date: str
    opening_balance_cents: int
    closing_balance_cents: int
    imported_statement_reference: Optional[str] = None


class TrustReconciliationRunResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    bank_account_id: str
    statement_start_date: str
    statement_end_date: str
    opening_balance_cents: int
    closing_balance_cents: int
    imported_statement_reference: Optional[str] = None
    status: str
    matched_count: int = 0
    unmatched_count: int = 0
    discrepancy_cents: int = 0
    prepared_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    closed_at: Optional[str] = None
    created_at: str
    updated_at: str


# ─── BankStatementLine ───────────────────────────────────────────────────────

class BankStatementLineCreate(BaseModel):
    """A single line from an imported bank statement."""
    bank_account_id: str
    reconciliation_run_id: Optional[str] = None
    statement_date: str  # ISO-8601 date
    value_date: Optional[str] = None
    amount_cents: int  # positive = credit to account, negative = debit
    description: str
    external_reference: Optional[str] = None
    fingerprint_hash: Optional[str] = None  # for dedup detection


class BankStatementLineResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    bank_account_id: str
    reconciliation_run_id: Optional[str] = None
    statement_date: str
    value_date: Optional[str] = None
    amount_cents: int
    amount_display: str
    description: str
    external_reference: Optional[str] = None
    fingerprint_hash: Optional[str] = None
    matched_status: str = "unmatched"  # unmatched | matched | excluded
    matched_ledger_entry_ids: List[str] = []
    import_run_id: Optional[str] = None
    created_at: str


# ─── TrustImportRun ───────────────────────────────────────────────────────────

class TrustImportRunCreate(BaseModel):
    """Track an import batch (bank statement CSV, DEFT, MRI)."""
    import_type: str  # "bank_statement" | "deft" | "mri_migration"
    file_name: str
    checksum: Optional[str] = None
    source_system: Optional[str] = None  # "nab" | "deft" | "mri" etc.
    source_reference: Optional[str] = None


class TrustImportRunResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    import_type: str
    file_name: str
    checksum: Optional[str] = None
    source_system: Optional[str] = None
    source_reference: Optional[str] = None
    row_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    started_at: str
    completed_at: Optional[str] = None
    status: str = "pending"  # pending | processing | completed | failed
    initiated_by: str
    error_summary: Optional[List[str]] = None


# ─── PeriodLock ──────────────────────────────────────────────────────────────

class PeriodLockCreate(BaseModel):
    """Lock a financial period to prevent new postings."""
    fund_type: FundType
    period_start: str  # ISO-8601 date "YYYY-MM-01"
    period_end: str  # ISO-8601 date "YYYY-MM-31"
    reason: Optional[str] = None


class PeriodLockResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    fund_type: str
    period_start: str
    period_end: str
    reason: Optional[str] = None
    locked_by: str
    locked_at: str
    unlocked_by: Optional[str] = None
    unlocked_at: Optional[str] = None
    is_active: bool = True


# ─── Interest Income ──────────────────────────────────────────────────────────

class InterestForecastResponse(BaseModel):
    """Preview of expected interest income for an account over a period."""
    account_id: str
    account_name: str
    account_type: str
    account_category: str
    interest_rate_pa: float
    period_start: str
    period_end: str
    days_in_period: int
    average_balance_cents: int
    average_balance_display: str
    forecast_interest_cents: int
    forecast_interest_display: str
    # How the interest would be split between funds (for shared accounts)
    admin_split_cents: int = 0
    admin_split_display: str = "$0.00"
    sinking_split_cents: int = 0
    sinking_split_display: str = "$0.00"
    already_posted_this_period: bool = False


class InterestPostingResponse(BaseModel):
    """Result of a posted interest income entry."""
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    account_id: str
    account_name: str
    period_start: str
    period_end: str
    total_interest_cents: int
    total_interest_display: str
    admin_interest_cents: int
    admin_interest_display: str
    sinking_interest_cents: int
    sinking_interest_display: str
    admin_transaction_id: Optional[str] = None
    sinking_transaction_id: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    posted_by: str
    posted_at: str
    is_auto_posted: bool = False


# ─── Advance Payments ─────────────────────────────────────────────────────────

class AdvancePaymentSummary(BaseModel):
    """Summary of a single advance levy payment and interest earned."""
    levy_schedule_id: str
    unit_number: str
    lot_number: int
    quarter: str
    due_date: str
    paid_date: str
    advance_days: int
    total_paid_cents: int
    total_paid_display: str
    interest_rate_pa: float
    interest_earned_cents: int
    interest_earned_display: str
    interest_posted: bool
    interest_posted_at: Optional[str] = None


class AdvancePaymentsListResponse(BaseModel):
    """Paginated list of advance payments for a building."""
    items: List[AdvancePaymentSummary]
    total: int
    total_interest_earned_cents: int
    total_interest_earned_display: str
    unposted_interest_cents: int
    unposted_interest_display: str


# ─── Immutable Trust Ledger Entry (Phase C blueprint) ─────────────────────────

class TrustLedgerEntry(BaseModel):
    """A single immutable entry in the trust_ledger_entries collection.

    Design rules (per strata_app_enhancements.md §3):
    - Never UPDATE or DELETE a posted entry. All corrections via REVERSAL entries.
    - amount_cents: signed integer. Positive = increase to fund, Negative = decrease.
    - running_balance_cents: fund balance AFTER this entry. Computed at write time.
    - lot_id: optional link to a specific lot (unit). Not set for building-wide entries.
    - effective_date: the accounting date; may differ from created_at.
    """
    building_id: str
    fund_type: FundType
    entry_type: EntryType
    amount_cents: int = Field(..., description="Signed cents. Positive=in, Negative=out")
    running_balance_cents: Optional[int] = Field(None, description="Fund balance after this entry")
    effective_date: str  # ISO-8601 date "YYYY-MM-DD" — accounting date
    description: str
    reference: Optional[str] = None  # Invoice#, BPAY ref, DEFT ref, etc.
    lot_id: Optional[str] = None  # units._id or units.unit_number if lot-specific
    batch_id: Optional[str] = None  # trust_ledger_batches._id for batch payments
    source_type: Optional[str] = None  # "levy_payment" | "trust_transaction" | "manual"
    source_id: Optional[str] = None  # FK to levy_payments._id or trust_transactions_v2._id
    reversal_of_entry_id: Optional[str] = None  # If this is a REVERSAL, the original entry id
    is_reversed: bool = False  # True once a REVERSAL entry exists for this entry
    posted_by: Optional[str] = None  # users._id
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TrustLedgerEntryResponse(TrustLedgerEntry):
    """API response for a single trust ledger entry."""
    model_config = ConfigDict(extra="ignore")
    id: str
    amount_display: str = ""  # e.g. "$1,234.56" formatted server-side
    created_at: str = ""


class TrustLedgerCreateRequest(BaseModel):
    """Request body to post a single trust ledger entry (admin/manager only)."""
    fund_type: FundType
    entry_type: EntryType
    amount_cents: int
    effective_date: str
    description: str
    reference: Optional[str] = None
    lot_id: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TrustLedgerReversalRequest(BaseModel):
    """Request to reverse (correct) an existing posted entry."""
    original_entry_id: str
    reason: str  # Required — must document why the reversal is needed
    effective_date: str  # May differ from original entry


# ─── AUDIT-11 response envelopes (Phase 1 mutating endpoints) ────────────────
# Document the exact response contract for each Phase 1 mutating endpoint so
# clients (and the OpenAPI/Postman schema) can rely on the shape. JSONResponse
# bodies still own runtime serialisation — these models are the source of
# truth for the contract.

class TrustConfigUpdateResponse(BaseModel):
    """PUT /trust/v2/config/{building_id} — config update ack."""
    success: bool
    message: str


class TrustAccountCreateResponse(BaseModel):
    """POST /trust/v2/accounts — newly-created account ack."""
    success: bool
    id: str  # MongoDB ObjectId of the new trust_accounts_v2 document


class BankAccountUpdateData(BaseModel):
    """Inner data for PATCH /trust/v2/accounts/{id}."""
    id: str
    account_category: str
    interest_rate_pa: float
    interest_calc_method: str
    bank_name: Optional[str] = None
    account_name: Optional[str] = None
    bsb: Optional[str] = None


class BankAccountUpdateResponse(BaseModel):
    """PATCH /trust/v2/accounts/{id} — partial update result."""
    success: bool
    data: BankAccountUpdateData


class InterestPostingEnvelope(BaseModel):
    """POST /trust/v2/accounts/{id}/post-interest — interest income posted."""
    success: bool
    data: InterestPostingResponse


class TrustTransactionCreateResponse(BaseModel):
    """POST /trust/v2/transactions — newly-created transaction ack."""
    success: bool
    id: str  # MongoDB ObjectId of the new trust_transactions_v2 document


class TrustTransactionReversalResponse(BaseModel):
    """POST /trust/v2/transactions/{id}/reverse — reversal entry ack."""
    success: bool
    reversal_id: str  # MongoDB ObjectId of the reversal trust_transactions_v2 document


class LevyGenerateEnvelope(BaseModel):
    """POST /trust/v2/levies/generate — quarterly levy schedule generation result."""
    success: bool
    data: LevyGenerateResponse


class LevyPaymentResponse(BaseModel):
    """POST /trust/v2/levies/{schedule_id}/pay — flat payment ack."""
    success: bool
    new_status: str  # paid | partial | pending
    outstanding_cents: int
    is_advance_payment: bool
    advance_days: int
    advance_interest_earned_cents: int
    advance_interest_earned_display: Optional[str] = None  # null when not an advance


class DeftWebhookResponse(BaseModel):
    """POST /trust/v2/deft/webhook — minimal idempotent ack (always 200)."""
    received: bool


class DeftSimulatedPayment(BaseModel):
    """POST /trust/v2/deft/simulate — a DEFT notification a user is standing in for.

    Mirrors the fields the real webhook reads out of the bank's payload, so a
    simulated payment travels the identical ingestion path. Bounded because this is
    a request body reachable by an authenticated user (security audit finding 3).
    """
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(..., min_length=1, max_length=100,
                                description="Bank-side transaction id; also the dedup key.")
    crn: str = Field(..., min_length=1, max_length=100,
                     description="Customer Reference Number matching a trust_levy_schedules_v2 row.")
    amount_cents: int = Field(..., gt=0, description="Payment amount in integer cents.")
    payment_date: Optional[str] = Field(None, max_length=64, description="ISO-8601 payment date.")


class ArrearsEscalationData(BaseModel):
    """Inner data for POST /trust/v2/arrears/escalate."""
    buildings_processed: int
    levies_scanned: int
    reminders_sent: int
    formal_notices_sent: int
    interest_transactions_created: int
    legal_flagged: int
    dry_run: bool
    ran_at: str  # ISO-8601


class ArrearsEscalationResponse(BaseModel):
    """POST /trust/v2/arrears/escalate — arrears escalation summary."""
    success: bool
    data: ArrearsEscalationData


# ─── AUDIT-11 6d: legacy trust_accounting.py mutating endpoint envelopes ─────
# These document the exact response contract for the 5 legacy /trust/* endpoints
# that were missing response_model. They are used by FastAPI for runtime response
# validation/serialization and for OpenAPI/Postman contract accuracy.

class VoidJournalEntryResponse(BaseModel):
    """POST /trust/journal/{entry_id}/void."""
    message: str
    entry_id: str


class BatchApprovalResponse(BaseModel):
    """POST /trust/batches/{batch_id}/approve — first or sole approval."""
    message: str
    batch_id: str
    status: str
    dual_approval_required: bool
    aba_generated: bool


class BatchSecondApprovalResponse(BaseModel):
    """POST /trust/batches/{batch_id}/second-approve — second approval."""
    message: str
    batch_id: str
    status: str
    aba_generated: bool


class LedgerEntryCreateResponse(BaseModel):
    """POST /trust/ledger — post a new immutable ledger entry."""
    message: str
    entry_id: str


class LedgerReversalCreateResponse(BaseModel):
    """POST /trust/ledger/reversal — post a reversal entry."""
    message: str
    reversal_entry_id: str
    original_entry_id: str
