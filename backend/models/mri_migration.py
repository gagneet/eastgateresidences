"""
MRI Migration Models — Staging and validation models for MRI → platform migration.

Stage collections:
  migration_batches          : top-level batch tracker
  staging_owners             : imported owner records (pre-commit)
  staging_lots               : imported lot/unit records (pre-commit)
  staging_ledgers            : imported opening ledger balances (pre-commit)
  staging_levies             : imported levy schedules (pre-commit)
  staging_transactions       : imported historical transactions (pre-commit)
  staging_bank_balances      : trust bank opening balances (pre-commit)
  staging_suppliers          : supplier / creditor directory (pre-commit)
  staging_validation_issues  : per-row validation errors / warnings
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from typing import Any, Dict, List, Optional


class MigrationBatchStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    DRY_RUN = "dry_run"
    DRY_RUN_COMPLETE = "dry_run_complete"
    READY_TO_COMMIT = "ready_to_commit"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ROLLBACK_AVAILABLE = "rollback_available"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


class MigrationBatchCreate(BaseModel):
    """Create a new MRI migration batch."""
    source_system: str = "mri"
    source_version: Optional[str] = None
    description: str
    freeze_date: Optional[str] = None  # ISO date: data as-at date
    file_checksums: Optional[Dict[str, str]] = {}  # filename → sha256


class MigrationBatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    source_system: str
    source_version: Optional[str] = None
    description: str
    freeze_date: Optional[str] = None
    status: str
    total_records: int = 0
    validated_records: int = 0
    error_count: int = 0
    warning_count: int = 0
    committed_at: Optional[str] = None
    rolled_back_at: Optional[str] = None
    created_by: str
    created_at: str
    updated_at: str


class StagingOwnerCreate(BaseModel):
    """Staged owner record from MRI import."""
    migration_batch_id: str
    source_owner_id: str
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    lots: List[str] = []  # source lot references
    raw_source: Optional[Dict[str, Any]] = {}
    validation_status: str = "pending"
    platform_user_id: Optional[str] = None  # set after commit


class StagingLotCreate(BaseModel):
    """Staged lot/unit record from MRI import."""
    migration_batch_id: str
    source_lot_id: str
    lot_number: str
    unit_number: Optional[str] = None
    entitlement_units: int = 1
    owner_source_ids: List[str] = []
    property_type: Optional[str] = None  # apartment | townhouse | commercial
    raw_source: Optional[Dict[str, Any]] = {}
    validation_status: str = "pending"
    platform_unit_id: Optional[str] = None  # set after commit


class StagingLedgerCreate(BaseModel):
    """Opening ledger balance from MRI."""
    migration_batch_id: str
    source_account_code: str
    source_account_name: str
    fund_type: Optional[str] = None  # admin_fund | capital_works_fund | special_levy
    balance_cents: int
    as_at_date: str
    direction: str = "credit"  # credit = liability (owner owes) | debit = asset
    platform_account_code: Optional[str] = None
    raw_source: Optional[Dict[str, Any]] = {}
    validation_status: str = "pending"


class StagingTransactionCreate(BaseModel):
    """Historical transaction from MRI."""
    migration_batch_id: str
    source_transaction_id: str
    transaction_date: str
    transaction_type: str  # receipt | payment | adjustment | fee
    amount_cents: int
    description: str
    reference: Optional[str] = None
    source_lot_id: Optional[str] = None
    source_account_code: Optional[str] = None
    fund_type: Optional[str] = None
    raw_source: Optional[Dict[str, Any]] = {}
    validation_status: str = "pending"
    platform_transaction_id: Optional[str] = None


class StagingBankBalanceCreate(BaseModel):
    """Trust bank opening balance from MRI."""
    migration_batch_id: str
    source_account_ref: str
    account_name: str
    bank_name: Optional[str] = None
    bsb: Optional[str] = None
    fund_type: Optional[str] = None
    balance_cents: int
    as_at_date: str
    raw_source: Optional[Dict[str, Any]] = {}
    validation_status: str = "pending"


class StagingValidationIssue(BaseModel):
    """Per-record validation issue."""
    migration_batch_id: str
    entity_type: str  # owner | lot | ledger | transaction | bank_balance
    source_record_id: str
    field: Optional[str] = None
    severity: ValidationSeverity
    code: str  # e.g. "MISSING_EMAIL", "BALANCE_MISMATCH", "UNKNOWN_ACCOUNT_CODE"
    message: str
    raw_value: Optional[Any] = None
    suggested_fix: Optional[str] = None


class ValidationFinding(BaseModel):
    """Structured finding from migration validation (Phase 2 hardened model)."""
    severity: str  # "blocking" | "warning" | "info"
    field: Optional[str] = None
    row_index: Optional[int] = None
    code: str  # MISSING_REQUIRED_FIELD, AMOUNT_OUT_OF_RANGE, DUPLICATE_CRN, etc.
    message: str
    suggested_fix: Optional[str] = None


class MigrationValidationReport(BaseModel):
    """Summary of validation run for a migration batch."""
    batch_id: str
    building_id: str
    validated_at: str
    # Phase 2 fields
    total_rows: int = 0
    blocking_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    can_proceed: bool = True  # False if blocking_count > 0
    findings: List[ValidationFinding] = []
    # Legacy / compatibility fields kept alongside new ones
    is_committable: bool = True  # alias for can_proceed
    total_records: int = 0
    owners: int = 0
    lots: int = 0
    ledger_entries: int = 0
    transactions: int = 0
    bank_balances: int = 0
    blocking_errors: int = 0  # alias for blocking_count
    errors: int = 0
    warnings: int = 0
    issues_sample: List[Dict[str, Any]] = []  # first 20 raw issues (legacy)
    balance_check: Optional[Dict[str, Any]] = None
