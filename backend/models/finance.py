"""
Finance-related Pydantic models.

New clean architecture (2026-02-19):
  - AnnualLevy: one per calendar year with fund totals and levy rates
  - LevyCategory: expense categories per year per fund type
  - UnitLevyLedger: per-unit opening/levied/paid/closing balances per year
  - LevyPayment: actual payment records (preserved from old schema)

Old collections dropped: finance, budgets, budget_categories
"""

from pydantic import BaseModel, ConfigDict, Field, AliasChoices, field_validator
from models.timestamps import IsoTimestamp, OptionalIsoTimestamp
from typing import List, Optional, Dict, Any

from utils.auth import DEFAULT_BUILDING_ID


# ─────────────────────────────────────────────────────────────────────────────
# Input length bounds (security audit 2026-08-26, finding 3 — request-body DoS)
#
# Every string/list field on a *request* model below carries one of these. They
# are deliberately far above any value observed in live data (the widest string
# in Mongo `levy_payments`/`annual_levies` is 8 chars; `finance.*` in Postgres
# holds none yet) — the goal is to reject multi-megabyte payloads, not to
# second-guess legitimate content. Anything tighter risks rejecting a real
# OCR-extracted or reconstructed description.
#
# Response models must NOT inherit these: FastAPI validates outbound payloads
# too, so a bound on a field a Response subclass inherits turns one over-long
# stored document into a 500 for the whole endpoint. Where a Response subclasses
# a Create model, the bounded fields are re-declared unconstrained — see the
# `# read-side: unconstrained` blocks below. (This is the same failure mode that
# LevyCategoryResponse.created_at already carries a comment about.)
# ─────────────────────────────────────────────────────────────────────────────

MAX_CODE_LEN = 100      # ids, enum-ish values, ISO dates, years, quarters, references
MAX_NAME_LEN = 300      # names, titles, labels, supplier/insurer/policy identifiers
MAX_TEXT_LEN = 5_000    # free text: descriptions, notes, rejection reasons
MAX_LIST_ITEMS = 500    # bounded list inputs (schedules, instalments, line items)


# ─────────────────────────────────────────────────────────────────────────────
# Annual Levy (one document per calendar year)
# ─────────────────────────────────────────────────────────────────────────────

class AnnualFundSummary(BaseModel):
    levy_income: float = 0
    other_income: float = 0
    total_income: float = 0
    total_expenses: float = 0
    opening_balance: float = 0
    closing_balance: float = 0
    surplus_deficit: float = 0
    current_balance: Optional[float] = None


class PaymentScheduleEntry(BaseModel):
    quarter: str = Field(..., max_length=MAX_CODE_LEN)  # Q1, Q2, Q3, Q4
    due_date: str = Field(..., max_length=MAX_CODE_LEN)  # ISO date string


class AnnualLevyCreate(BaseModel):
    year: str = Field(..., max_length=MAX_CODE_LEN)  # "2025", "2026"
    status: str = Field(..., max_length=MAX_CODE_LEN)  # "proposed" or "actual"
    building_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN)  # for compatibility
    total_uoe: int = 10000
    admin_fund: AnnualFundSummary
    sinking_fund: AnnualFundSummary
    payment_schedule: List[PaymentScheduleEntry] = Field(default=[], max_length=MAX_LIST_ITEMS)
    admin_levy_per_uoe_annual: float = 0
    admin_levy_per_uoe_quarterly: float = 0
    sinking_levy_per_uoe_annual: float = 0
    sinking_levy_per_uoe_quarterly: float = 0
    # Data provenance — used to distinguish imported vs system-generated records.
    # "agm_import": ratified at AGM and imported from official documents
    # "user_provided": manually entered by strata manager
    # "system_generated": auto-created by the platform (projections, placeholders)
    data_source: Optional[str] = Field(None, max_length=MAX_CODE_LEN)  # "agm_import" | "user_provided" | "system_generated"
    is_synthetic: bool = False  # True only for placeholder/projection records not approved by AGM


class AnnualLevyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str
    year: str
    status: str
    building_id: str = Field(validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(validation_alias=AliasChoices("building_id", "plan_id"))  # for compatibility
    total_uoe: int
    admin_fund: Dict[str, Any]
    sinking_fund: Dict[str, Any]
    payment_schedule: List[Dict[str, Any]]
    admin_levy_per_uoe_annual: float
    admin_levy_per_uoe_quarterly: float
    sinking_levy_per_uoe_annual: float
    sinking_levy_per_uoe_quarterly: float
    data_source: Optional[str] = None
    is_synthetic: bool = False
    created_at: IsoTimestamp
    updated_at: IsoTimestamp


# ─────────────────────────────────────────────────────────────────────────────
# Levy Categories (expense line items per year per fund)
# ─────────────────────────────────────────────────────────────────────────────

class LevyCategoryCreate(BaseModel):
    year: str = Field(..., max_length=MAX_CODE_LEN)  # "2025", "2026"
    fund_type: str = Field(..., max_length=MAX_CODE_LEN)  # "administrative" or "sinking"
    name: str = Field(..., max_length=MAX_NAME_LEN)
    budgeted_amount: float = 0
    actual_amount: float = 0
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    building_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN)  # for compatibility
    status: str = Field(default="proposed", max_length=MAX_CODE_LEN)  # "proposed" or "actual"


class LevyCategoryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str
    year: str
    fund_type: str
    name: str
    budgeted_amount: float
    actual_amount: float
    description: Optional[str]
    building_id: str = Field(validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(validation_alias=AliasChoices("building_id", "plan_id"))  # for compatibility
    status: str
    # Optional + datetime-coercing: levy_categories rows written by the onboarding import store
    # created_at/updated_at as datetime OBJECTS (Motor reads them back as datetime), but this
    # response contracts them as ISO strings. Without coercion a single such row raised a
    # ValidationError that 500'd the whole /levy-categories endpoint — which the spending-categories
    # page silently turned into a blank "no categories" state via Promise.allSettled.
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_datetime_to_iso(cls, v: Any) -> Optional[str]:
        from datetime import date, datetime
        if v is None:
            return None
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        return str(v)


class LevyCategoryUpdate(BaseModel):
    actual_amount: Optional[float] = None
    budgeted_amount: Optional[float] = None
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


# ─────────────────────────────────────────────────────────────────────────────
# Unit Levy Ledger (per unit per year opening/levied/paid/closing)
# ─────────────────────────────────────────────────────────────────────────────

class UnitLevyLedgerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    # id/lot_number/uoe/property_type are optional because historical records (2021-2025)
    # were seeded before these fields were added to the schema. The API must not 500 on these.
    id: Optional[str] = None
    building_id: str = Field(validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(validation_alias=AliasChoices("building_id", "plan_id"))  # for compatibility
    year: str
    unit_number: str
    lot_number: Optional[str] = None
    uoe: Optional[int] = None
    property_type: Optional[str] = None
    # Admin fund (exact per-fund values from Strata Web statement)
    admin_opening: float = 0.0
    admin_levied: float = 0.0
    admin_special_levy: float = 0.0
    admin_paid: float = 0.0
    admin_closing: float = 0.0
    admin_interest: float = 0.0
    # Sinking fund (exact per-fund values from Strata Web statement)
    sinking_opening: float = 0.0
    sinking_levied: float = 0.0
    sinking_paid: float = 0.0
    sinking_closing: float = 0.0
    sinking_interest: float = 0.0
    # Combined totals
    total_opening: float = 0.0
    total_levied: float = 0.0
    special_levy: float = 0.0  # admin special levy (sinking special levy is always 0)
    total_paid: float = 0.0
    total_closing: float = 0.0
    interest_paid: float = 0.0  # total interest (admin + sinking)
    # Convenience
    opening_arrears: float = 0.0  # max(0, total_opening) — arrears carried from prior year
    net_balance: float = 0.0  # total_closing: positive = owes money, negative = credit
    # total_paid is NOT reliably scoped to this year -- confirmed live 2026-08-01 that it can be
    # back-solved from a portal balance snapshot (opening + levied - target_balance), i.e.
    # cumulative payment history through the scrape date, not payments received this year
    # specifically. paid_this_year is the year-scoped algebraic figure
    # (annual_total_levied - net_balance), kept for existing collection-rate/detail consumers.
    # *_due_to_date fields are the Levy Status table's as-of-today basis and must only include
    # periods whose configured due date has arrived.
    paid_this_year: float = 0.0
    levied_due_to_date: float = 0.0
    paid_due_to_date: float = 0.0
    outstanding_due_to_date: float = 0.0
    annual_total_levied: float = 0.0
    annual_paid_this_year: float = 0.0
    periods_due_to_date: int = 0
    total_periods: int = 0
    special_levy_ids: List[str] = []
    # Owner info — injected by the API from units/user_units (not stored in collection)
    owner_name: Optional[str] = None
    owner_name_b: Optional[str] = None
    # Portal cross-check — written by strata scraper, NOT used in accounting calculations.
    # net_balance remains the authoritative computed value (levied − paid + opening).
    portal_net_balance: Optional[float] = None  # portal's current owner balance snapshot
    portal_synced_at: Optional[str] = None  # ISO timestamp of last portal sync
    # Data provenance
    is_synthetic: bool = False
    data_source: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Levy Payments (recording actual payment events)
# ─────────────────────────────────────────────────────────────────────────────

class LevyPaymentCreate(BaseModel):
    unit_number: str = Field(..., max_length=MAX_CODE_LEN)
    amount: float
    payment_method: str = Field(..., max_length=MAX_CODE_LEN)  # deft, bpay, credit_card, bank_transfer, cash, cheque
    payment_reference: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    quarter: str = Field(..., max_length=MAX_CODE_LEN)  # Q1, Q2, Q3, Q4
    year: str = Field(..., max_length=MAX_CODE_LEN)  # "2025", "2026" (calendar year)
    fund_type: Optional[str] = Field(None, max_length=MAX_CODE_LEN)  # "administrative", "sinking", or None for combined
    payment_type: Optional[str] = Field("standard", max_length=MAX_CODE_LEN)  # "standard" | "partial" | "advance"
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class LevyPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    unit_number: str
    amount: float
    payment_method: str
    payment_reference: Optional[str]
    quarter: str
    year: str
    fund_type: Optional[str]
    payment_type: Optional[str]  # "standard" | "partial" | "advance"
    credit_amount: Optional[float]  # excess above period levy (advance payments only)
    status: str  # pending_verification, confirmed, rejected, failed
    notes: Optional[str]
    receipt_number: str
    paid_by: Optional[str]
    confirmed_by: Optional[str]
    confirmed_at: Optional[str]
    rejected_by: Optional[str] = None
    rejected_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: str


class LevyPaymentVerify(BaseModel):
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class LevyPaymentReject(BaseModel):
    rejection_reason: str = Field(..., max_length=MAX_TEXT_LEN)
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


# ─────────────────────────────────────────────────────────────────────────────
# Financial Projections (kept from old schema)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectionAssumptions(BaseModel):
    inflation_rate: float = 3.0
    insurance_increase: float = 5.0
    utilities_increase: float = 4.0
    wages_increase: float = 3.5
    sinking_fund_contribution: float = 10000
    major_works: List[dict] = Field(default=[], max_length=MAX_LIST_ITEMS)


class FinancialProjectionCreate(BaseModel):
    projection_name: str = Field(..., max_length=MAX_NAME_LEN)
    base_year: str = Field(..., max_length=MAX_CODE_LEN)  # e.g., "2026"
    projection_years: int = Field(default=5, ge=1, le=100)
    assumptions: ProjectionAssumptions


class FinancialProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    projection_name: str
    base_year: str
    projection_years: int
    assumptions: dict
    projections: List[dict]
    created_by: str
    created_at: str
    updated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Budget Proposals (new-year budget planning from prior-year actuals + CPI)
# ─────────────────────────────────────────────────────────────────────────────

class BudgetProposalItem(BaseModel):
    fund_type: str = Field(..., max_length=MAX_CODE_LEN)  # "administrative" or "sinking"
    name: str = Field(..., max_length=MAX_NAME_LEN)
    prior_year_actual: float = 0.0
    prior_year_budgeted: float = 0.0
    proposed_amount: float  # CPI-adjusted base amount
    amended_amount: Optional[float] = None
    approved: bool = False


class BudgetProposalCreate(BaseModel):
    target_year: str = Field(..., max_length=MAX_CODE_LEN)  # year to create proposals for, e.g. "2027"
    base_year: str = Field(..., max_length=MAX_CODE_LEN)  # year to derive from, e.g. "2026"
    inflation_rate: float = 3.0
    items: List[BudgetProposalItem] = Field(..., max_length=MAX_LIST_ITEMS)


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Transactions
# ─────────────────────────────────────────────────────────────────────────────

class TransactionBase(BaseModel):
    building_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    financial_year: str = Field(..., max_length=MAX_CODE_LEN)
    amount: float = 0.0
    date: Optional[str] = Field(None, max_length=MAX_CODE_LEN)  # ISO date of the transaction e.g. "2026-04-01"
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)  # human-readable description


class ExpenseTransactionCreate(TransactionBase):
    category_id: str = Field(default="101", max_length=MAX_CODE_LEN)
    category_name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)  # human-readable category name
    fund_type: Optional[str] = Field(None, max_length=MAX_CODE_LEN)  # "administrative" or "sinking"
    supplier_name: str = Field(..., max_length=MAX_NAME_LEN)
    invoice_number: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    is_gst_inclusive: bool = True
    gst_amount: float = 0.0

    # Digital Twin Linkage
    asset_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    facility_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    zone_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    benefit_group_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)


class ExpenseTransactionResponse(ExpenseTransactionCreate):
    model_config = ConfigDict(extra="allow")
    # read-side: unconstrained. GET /expense-transactions validates rows written by
    # importers and scrapers that never passed through ExpenseTransactionCreate, so the
    # input bounds above must not apply here — one over-long legacy row would otherwise
    # 500 the whole list endpoint. Defaults and validation aliases are reproduced
    # verbatim from the parent; only max_length is dropped.
    building_id: str = Field(default=DEFAULT_BUILDING_ID,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    financial_year: str
    date: Optional[str] = None
    description: Optional[str] = None
    category_id: str = "101"
    category_name: Optional[str] = None
    fund_type: Optional[str] = None
    supplier_name: str
    invoice_number: Optional[str] = None
    asset_id: Optional[str] = None
    facility_id: Optional[str] = None
    zone_id: Optional[str] = None
    benefit_group_id: Optional[str] = None

    id: str
    created_at: str
    updated_at: str
    created_by: str


class IncomeTransactionCreate(TransactionBase):
    source: str = Field(..., max_length=MAX_CODE_LEN)  # interest, rebate, grant, other
    category_name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    fund_type: Optional[str] = Field(None, max_length=MAX_CODE_LEN)


class IncomeTransactionResponse(IncomeTransactionCreate):
    model_config = ConfigDict(extra="allow")
    # read-side: unconstrained — see ExpenseTransactionResponse.
    building_id: str = Field(default=DEFAULT_BUILDING_ID,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    financial_year: str
    date: Optional[str] = None
    description: Optional[str] = None
    source: str
    category_name: Optional[str] = None
    fund_type: Optional[str] = None

    id: str
    created_at: str
    updated_at: str
    created_by: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Special Levies
# ─────────────────────────────────────────────────────────────────────────────

class SpecialLevyCreate(BaseModel):
    building_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    year: str = Field(..., max_length=MAX_CODE_LEN)
    title: str = Field(..., max_length=MAX_NAME_LEN)
    description: str = Field(..., max_length=MAX_TEXT_LEN)
    total_amount: float
    due_date: str = Field(..., max_length=MAX_CODE_LEN)
    fund_type: str = Field(default="sinking", max_length=MAX_CODE_LEN)


class SpecialLevyResponse(SpecialLevyCreate):
    model_config = ConfigDict(extra="ignore")
    # read-side: unconstrained — see ExpenseTransactionResponse.
    building_id: str = Field(default=DEFAULT_BUILDING_ID,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    year: str
    title: str
    description: str
    due_date: str
    fund_type: str = "sinking"

    id: str
    created_at: str
    updated_at: str


class SpecialLevyPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    special_levy_id: str
    unit_number: str
    amount_levied: float
    amount_paid: float = 0.0
    status: str = "unpaid"  # unpaid, partial, paid
    paid_at: Optional[str] = None
    created_at: str
    updated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Payment Plans
# ─────────────────────────────────────────────────────────────────────────────

class PaymentPlanInstalment(BaseModel):
    due_date: str = Field(..., max_length=MAX_CODE_LEN)
    amount: float
    is_paid: bool = False
    paid_at: Optional[str] = Field(None, max_length=MAX_CODE_LEN)


# DEAD — do not use for new code. These modelled the /payment-plans handlers that
# used to live in routers/finance.py; those were removed because they registered
# the same (method, path) as routers/payment_plans.py and shadowed the NSW Form 1
# s.83A owner flow entirely. Nothing imports these two any more (PaymentPlanInstalment
# below is still referenced by a test).
#
# The LIVE request/response models are routers.payment_plans.PaymentPlanCreate /
# PaymentPlanResponse. Import those. They are NOT interchangeable with these: the
# live pair denominates money in integer cents (outstanding_amount_cents,
# requested_instalment_cents) per the ledger-precision rule, whereas the pair below
# uses float dollars (total_amount) — so reaching for this one by name would quietly
# reintroduce float money into a payment path.
class PaymentPlanCreate(BaseModel):
    unit_number: str = Field(..., max_length=MAX_CODE_LEN)
    total_amount: float
    start_date: str = Field(..., max_length=MAX_CODE_LEN)
    instalments: List[PaymentPlanInstalment] = Field(..., max_length=MAX_LIST_ITEMS)
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class PaymentPlanResponse(PaymentPlanCreate):
    model_config = ConfigDict(extra="ignore")
    # read-side: unconstrained — see ExpenseTransactionResponse.
    unit_number: str
    start_date: str
    instalments: List[PaymentPlanInstalment]
    notes: Optional[str] = None

    id: str
    status: str = "active"  # active, completed, cancelled
    is_active: bool = True
    created_at: str
    updated_at: str
    created_by: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Bank Reconciliation
# ─────────────────────────────────────────────────────────────────────────────

class BankReconciliationCreate(BaseModel):
    building_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    financial_year: str = Field(..., max_length=MAX_CODE_LEN)
    fund_type: str = Field(..., max_length=MAX_CODE_LEN)  # administrative | sinking
    statement_date: str = Field(..., max_length=MAX_CODE_LEN)
    opening_balance: float
    closing_balance: float
    total_receipts: float
    total_payments: float
    bank_statement_reference: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    fund_account_name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)


class BankReconciliationResponse(BankReconciliationCreate):
    model_config = ConfigDict(extra="ignore")
    # read-side: unconstrained — see ExpenseTransactionResponse.
    building_id: str = Field(default=DEFAULT_BUILDING_ID,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    financial_year: str
    fund_type: str
    statement_date: str
    bank_statement_reference: Optional[str] = None
    fund_account_name: Optional[str] = None

    id: str
    variance_amount: float
    is_reconciled: bool
    reconciled_at: Optional[str] = None
    reconciled_by: Optional[str] = None
    created_at: str
    updated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Insurance Policies
# ─────────────────────────────────────────────────────────────────────────────

class InsurancePolicyCreate(BaseModel):
    building_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID, max_length=MAX_CODE_LEN,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    policy_number: str = Field(..., max_length=MAX_NAME_LEN)
    insurer: str = Field(..., max_length=MAX_NAME_LEN)
    broker: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    policy_type: str = Field(..., max_length=MAX_NAME_LEN)  # Building, Public Liability, etc.
    start_date: str = Field(..., max_length=MAX_CODE_LEN)
    end_date: str = Field(..., max_length=MAX_CODE_LEN)
    premium_amount: float
    sum_insured: float
    is_active: bool = True


class InsurancePolicyResponse(InsurancePolicyCreate):
    model_config = ConfigDict(extra="ignore")
    # read-side: unconstrained — see ExpenseTransactionResponse.
    building_id: str = Field(default=DEFAULT_BUILDING_ID,
                             validation_alias=AliasChoices("building_id", "plan_id"))
    plan_id: str = Field(default=DEFAULT_BUILDING_ID,
                         validation_alias=AliasChoices("building_id", "plan_id"))
    policy_number: str
    insurer: str
    broker: Optional[str] = None
    policy_type: str
    start_date: str
    end_date: str

    id: str
    created_at: str
    updated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Arrears Metadata
# ─────────────────────────────────────────────────────────────────────────────

class ContactLogEntry(BaseModel):
    date: str = Field(..., max_length=MAX_CODE_LEN)
    method: str = Field(..., max_length=MAX_CODE_LEN)  # email, phone, letter, dca_referral
    description: str = Field(..., max_length=MAX_TEXT_LEN)
    performed_by: str = Field(..., max_length=MAX_CODE_LEN)
    performed_by_name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)


class ArrearsMetadata(BaseModel):
    dca_status: str = Field(default="none", max_length=MAX_CODE_LEN)  # none | eligible | referred | recovering | resolved | recalled
    dca_reference: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    first_notice_sent_at: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    legal_referral_status: str = Field(default="none", max_length=MAX_CODE_LEN)  # none | flagged | referred | resolved
    contact_log: List[ContactLogEntry] = Field(default=[], max_length=MAX_LIST_ITEMS)


# ─── ACT Statutory Interest — UTMA 2011 s.96 ─────────────────────────────────

class SpecialResolutionRateCreate(BaseModel):
    """Record a special resolution raising arrears interest above the 10% p.a. default."""
    interest_rate_pct: float = Field(..., ge=10.0, le=20.0,
                                     description="Annual rate (%). Must be ≥ 10 (statutory default) and ≤ 20 (UTMA cap).")
    passed_date: str = Field(..., max_length=MAX_CODE_LEN,
                             description="ISO-8601 date the resolution was passed at a general meeting.")
    expires_at: Optional[str] = Field(None, max_length=MAX_CODE_LEN,
                                      description="ISO-8601 date the override lapses. None = indefinite.")
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class SpecialResolutionRateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    resolution_type: str
    title: str
    interest_rate_pct: float
    passed_date: str
    passed_by: str
    expires_at: Optional[str] = None
    status: str
    notes: Optional[str] = None
    created_at: str


class InterestRateResponse(BaseModel):
    """Current effective arrears interest rate and its provenance."""
    building_id: str
    rate_pct: float
    source: str  # "special_resolution" | "building_config" | "act_default" (ACT only) | "jurisdiction_default" (NSW/VIC/QLD)
    resolution_id: Optional[str] = None
    resolution_date: Optional[str] = None
    expires_at: Optional[str] = None
    max_rate_pct: float
    jurisdiction: str = "ACT"
    statute: str = "UTMA 2011 s.94"


# ─── Ack models for mutating endpoints (AUDIT-11 6f) ─────────────────────────

class MessageAck(BaseModel):
    """Generic single-message acknowledgement returned by DELETE/PUT endpoints."""
    message: str


class DeleteWithIdAck(BaseModel):
    """Acknowledgement that includes the deleted record's id."""
    message: str
    id: str


# Alias kept for import compatibility — identical schema to MessageAck.
LevyReminderSettingsAck = MessageAck


__all__ = [
    "AnnualFundSummary",
    "PaymentScheduleEntry",
    "AnnualLevyCreate",
    "AnnualLevyResponse",
    "LevyCategoryCreate",
    "LevyCategoryResponse",
    "LevyCategoryUpdate",
    "UnitLevyLedgerResponse",
    "LevyPaymentCreate",
    "LevyPaymentResponse",
    "LevyPaymentVerify",
    "LevyPaymentReject",
    "ProjectionAssumptions",
    "FinancialProjectionCreate",
    "FinancialProjectionResponse",
    "BudgetProposalItem",
    "BudgetProposalCreate",
    "ExpenseTransactionCreate",
    "ExpenseTransactionResponse",
    "IncomeTransactionCreate",
    "IncomeTransactionResponse",
    "SpecialLevyCreate",
    "SpecialLevyResponse",
    "SpecialLevyPaymentResponse",
    "PaymentPlanCreate",
    "PaymentPlanResponse",
    "PaymentPlanInstalment",
    "BankReconciliationCreate",
    "BankReconciliationResponse",
    "InsurancePolicyCreate",
    "InsurancePolicyResponse",
    "ContactLogEntry",
    "ArrearsMetadata",
    "SpecialResolutionRateCreate",
    "SpecialResolutionRateResponse",
    "InterestRateResponse",
    "MessageAck",
    "DeleteWithIdAck",
    "LevyReminderSettingsAck",
]
