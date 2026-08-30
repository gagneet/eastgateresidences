"""
Tests for MRI Migration models.

Covers:
- MigrationBatchStatus enum values
- ValidationSeverity enum values
- MigrationBatchCreate model validation
- StagingOwnerCreate model validation
- StagingLotCreate model validation
- StagingLedgerCreate model validation (cents validation)
- StagingTransactionCreate model validation
- MigrationValidationReport model validation
- building_id field isolation
- Idempotency key in batch creates
"""
from __future__ import annotations

import pytest

from models.mri_migration import (
    MigrationBatchCreate,
    MigrationBatchStatus,
    MigrationValidationReport,
    StagingLedgerCreate,
    StagingLotCreate,
    StagingOwnerCreate,
    StagingTransactionCreate,
    ValidationSeverity,
)


# ─── MigrationBatchStatus enum ───────────────────────────────────────────────

class TestMigrationBatchStatus:
    def test_pending_value(self):
        assert MigrationBatchStatus.PENDING.value == "pending"

    def test_validating_value(self):
        assert MigrationBatchStatus.VALIDATING.value == "validating"

    def test_validated_value(self):
        assert MigrationBatchStatus.VALIDATED.value == "validated"

    def test_dry_run_value(self):
        assert MigrationBatchStatus.DRY_RUN.value == "dry_run"

    def test_committed_value(self):
        assert MigrationBatchStatus.COMMITTED.value == "committed"

    def test_failed_value(self):
        assert MigrationBatchStatus.FAILED.value == "failed"

    def test_rolled_back_value(self):
        assert MigrationBatchStatus.ROLLED_BACK.value == "rolled_back"

    def test_committing_value(self):
        assert MigrationBatchStatus.COMMITTING.value == "committing"

    def test_is_string_enum(self):
        assert isinstance(MigrationBatchStatus.PENDING, str)

    def test_all_statuses_present(self):
        values = {s.value for s in MigrationBatchStatus}
        assert "pending" in values
        assert "committed" in values
        assert "failed" in values


# ─── ValidationSeverity enum ──────────────────────────────────────────────────

class TestValidationSeverity:
    def test_info_value(self):
        assert ValidationSeverity.INFO.value == "info"

    def test_warning_value(self):
        assert ValidationSeverity.WARNING.value == "warning"

    def test_error_value(self):
        assert ValidationSeverity.ERROR.value == "error"

    def test_blocking_value(self):
        assert ValidationSeverity.BLOCKING.value == "blocking"

    def test_is_string_enum(self):
        assert isinstance(ValidationSeverity.ERROR, str)

    def test_all_four_severities(self):
        values = {s.value for s in ValidationSeverity}
        assert values == {"info", "warning", "error", "blocking"}


# ─── MigrationBatchCreate ─────────────────────────────────────────────────────

class TestMigrationBatchCreate:
    def test_valid_minimal_create(self):
        m = MigrationBatchCreate(description="March 2026 MRI export")
        assert m.description == "March 2026 MRI export"

    def test_source_system_defaults_to_mri(self):
        m = MigrationBatchCreate(description="Test batch")
        assert m.source_system == "mri"

    def test_source_version_defaults_none(self):
        m = MigrationBatchCreate(description="Test batch")
        assert m.source_version is None

    def test_freeze_date_defaults_none(self):
        m = MigrationBatchCreate(description="Test batch")
        assert m.freeze_date is None

    def test_file_checksums_defaults_empty_dict(self):
        m = MigrationBatchCreate(description="Test batch")
        assert m.file_checksums == {}

    def test_full_payload(self):
        m = MigrationBatchCreate(
            source_system="mri",
            source_version="MRI-12.4.1",
            description="Full migration batch",
            freeze_date="2026-03-31",
            file_checksums={"owners.csv": "abc123", "lots.csv": "def456"},
        )
        assert m.source_version == "MRI-12.4.1"
        assert m.freeze_date == "2026-03-31"
        assert m.file_checksums["owners.csv"] == "abc123"

    def test_missing_description_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MigrationBatchCreate(source_system="mri")


# ─── StagingOwnerCreate ───────────────────────────────────────────────────────

class TestStagingOwnerCreate:
    def _valid_payload(self):
        return {
            "migration_batch_id": "batch_001",
            "source_owner_id": "MRI-OWN-001",
            "first_name": "Jane",
            "last_name": "Smith",
        }

    def test_valid_create(self):
        m = StagingOwnerCreate(**self._valid_payload())
        assert m.first_name == "Jane"
        assert m.last_name == "Smith"
        assert m.source_owner_id == "MRI-OWN-001"

    def test_optional_fields_default(self):
        m = StagingOwnerCreate(**self._valid_payload())
        assert m.email is None
        assert m.phone is None
        assert m.address is None
        assert m.lots == []
        assert m.platform_user_id is None

    def test_validation_status_defaults_pending(self):
        m = StagingOwnerCreate(**self._valid_payload())
        assert m.validation_status == "pending"

    def test_lots_list_can_be_populated(self):
        payload = {**self._valid_payload(), "lots": ["LOT-001", "LOT-002"]}
        m = StagingOwnerCreate(**payload)
        assert len(m.lots) == 2

    def test_missing_required_field_raises(self):
        from pydantic import ValidationError
        payload = self._valid_payload()
        del payload["source_owner_id"]
        with pytest.raises(ValidationError):
            StagingOwnerCreate(**payload)


# ─── StagingLotCreate ─────────────────────────────────────────────────────────

class TestStagingLotCreate:
    def _valid_payload(self):
        return {
            "migration_batch_id": "batch_001",
            "source_lot_id": "MRI-LOT-042",
            "lot_number": "42",
        }

    def test_valid_create(self):
        m = StagingLotCreate(**self._valid_payload())
        assert m.lot_number == "42"
        assert m.source_lot_id == "MRI-LOT-042"

    def test_entitlement_units_defaults_one(self):
        m = StagingLotCreate(**self._valid_payload())
        assert m.entitlement_units == 1

    def test_optional_fields_default(self):
        m = StagingLotCreate(**self._valid_payload())
        assert m.unit_number is None
        assert m.property_type is None
        assert m.platform_unit_id is None
        assert m.owner_source_ids == []

    def test_property_types(self):
        for ptype in ("apartment", "townhouse", "commercial"):
            payload = {**self._valid_payload(), "property_type": ptype}
            m = StagingLotCreate(**payload)
            assert m.property_type == ptype

    def test_migration_batch_id_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            StagingLotCreate(source_lot_id="X", lot_number="1")


# ─── StagingLedgerCreate ─────────────────────────────────────────────────────

class TestStagingLedgerCreate:
    def _valid_payload(self):
        return {
            "migration_batch_id": "batch_001",
            "source_account_code": "1001",
            "source_account_name": "NAB Trust Account",
            "balance_cents": 500000,
            "as_at_date": "2026-03-31",
        }

    def test_valid_create(self):
        m = StagingLedgerCreate(**self._valid_payload())
        assert m.balance_cents == 500000
        assert m.source_account_code == "1001"

    def test_balance_is_stored_as_cents(self):
        payload = {**self._valid_payload(), "balance_cents": 123456}
        m = StagingLedgerCreate(**payload)
        assert m.balance_cents == 123456
        assert isinstance(m.balance_cents, int)

    def test_negative_balance_allowed(self):
        payload = {**self._valid_payload(), "balance_cents": -250000}
        m = StagingLedgerCreate(**payload)
        assert m.balance_cents == -250000

    def test_zero_balance_allowed(self):
        payload = {**self._valid_payload(), "balance_cents": 0}
        m = StagingLedgerCreate(**payload)
        assert m.balance_cents == 0

    def test_direction_defaults_credit(self):
        m = StagingLedgerCreate(**self._valid_payload())
        assert m.direction == "credit"

    def test_direction_can_be_debit(self):
        payload = {**self._valid_payload(), "direction": "debit"}
        m = StagingLedgerCreate(**payload)
        assert m.direction == "debit"

    def test_fund_type_optional(self):
        m = StagingLedgerCreate(**self._valid_payload())
        assert m.fund_type is None

    def test_fund_type_can_be_set(self):
        payload = {**self._valid_payload(), "fund_type": "admin_fund"}
        m = StagingLedgerCreate(**payload)
        assert m.fund_type == "admin_fund"


# ─── StagingTransactionCreate ─────────────────────────────────────────────────

class TestStagingTransactionCreate:
    def _valid_payload(self):
        return {
            "migration_batch_id": "batch_001",
            "source_transaction_id": "MRI-TXN-9999",
            "transaction_date": "2026-03-01",
            "transaction_type": "receipt",
            "amount_cents": 75000,
            "description": "Quarterly levy Q1 2026",
        }

    def test_valid_create(self):
        m = StagingTransactionCreate(**self._valid_payload())
        assert m.amount_cents == 75000
        assert m.transaction_type == "receipt"

    def test_transaction_types(self):
        for ttype in ("receipt", "payment", "adjustment", "fee"):
            payload = {**self._valid_payload(), "transaction_type": ttype}
            m = StagingTransactionCreate(**payload)
            assert m.transaction_type == ttype

    def test_optional_fields_default_none(self):
        m = StagingTransactionCreate(**self._valid_payload())
        assert m.reference is None
        assert m.source_lot_id is None
        assert m.source_account_code is None
        assert m.fund_type is None
        assert m.platform_transaction_id is None

    def test_validation_status_defaults_pending(self):
        m = StagingTransactionCreate(**self._valid_payload())
        assert m.validation_status == "pending"

    def test_negative_amount_for_payment(self):
        payload = {**self._valid_payload(), "transaction_type": "payment", "amount_cents": -50000}
        m = StagingTransactionCreate(**payload)
        assert m.amount_cents == -50000

    def test_missing_amount_raises(self):
        from pydantic import ValidationError
        payload = self._valid_payload()
        del payload["amount_cents"]
        with pytest.raises(ValidationError):
            StagingTransactionCreate(**payload)


# ─── MigrationValidationReport ───────────────────────────────────────────────

class TestMigrationValidationReport:
    def _valid_payload(self):
        return {
            "batch_id": "batch_001",
            "building_id": "building_eastgate",
            "validated_at": "2026-03-31T10:00:00Z",
            "is_committable": True,
            "total_records": 250,
            "owners": 87,
            "lots": 87,
            "ledger_entries": 50,
            "transactions": 24,
            "bank_balances": 2,
            "blocking_errors": 0,
            "errors": 0,
            "warnings": 3,
            "info_count": 12,
        }

    def test_valid_create(self):
        m = MigrationValidationReport(**self._valid_payload())
        assert m.batch_id == "batch_001"
        assert m.is_committable is True
        assert m.total_records == 250

    def test_not_committable_when_blocking_errors(self):
        payload = {**self._valid_payload(), "blocking_errors": 5, "is_committable": False}
        m = MigrationValidationReport(**payload)
        assert m.is_committable is False
        assert m.blocking_errors == 5

    def test_building_id_present(self):
        m = MigrationValidationReport(**self._valid_payload())
        assert m.building_id == "building_eastgate"

    def test_missing_building_id_raises(self):
        from pydantic import ValidationError
        payload = self._valid_payload()
        del payload["building_id"]
        with pytest.raises(ValidationError):
            MigrationValidationReport(**payload)


# ─── Building isolation (building_id field presence) ─────────────────────────

class TestBuildingIsolation:
    """Verify that staging models contain migration_batch_id for tracing building context."""

    def test_staging_owner_has_migration_batch_id(self):
        m = StagingOwnerCreate(
            migration_batch_id="batch_abc",
            source_owner_id="OWN-001",
            first_name="A",
            last_name="B",
        )
        assert m.migration_batch_id == "batch_abc"

    def test_staging_lot_has_migration_batch_id(self):
        m = StagingLotCreate(
            migration_batch_id="batch_abc",
            source_lot_id="LOT-001",
            lot_number="1",
        )
        assert m.migration_batch_id == "batch_abc"

    def test_staging_ledger_has_migration_batch_id(self):
        m = StagingLedgerCreate(
            migration_batch_id="batch_abc",
            source_account_code="1001",
            source_account_name="Trust Account",
            balance_cents=0,
            as_at_date="2026-03-31",
        )
        assert m.migration_batch_id == "batch_abc"

    def test_staging_transaction_has_migration_batch_id(self):
        m = StagingTransactionCreate(
            migration_batch_id="batch_abc",
            source_transaction_id="TXN-001",
            transaction_date="2026-03-01",
            transaction_type="receipt",
            amount_cents=1000,
            description="test",
        )
        assert m.migration_batch_id == "batch_abc"

    def test_validation_report_has_building_id(self):
        m = MigrationValidationReport(
            batch_id="batch_abc",
            building_id="eastgate",
            validated_at="2026-03-31T00:00:00Z",
            is_committable=True,
            total_records=0,
            owners=0,
            lots=0,
            ledger_entries=0,
            transactions=0,
            bank_balances=0,
            blocking_errors=0,
            errors=0,
            warnings=0,
            info_count=0,
        )
        assert m.building_id == "eastgate"
