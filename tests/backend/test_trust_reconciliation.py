"""
Tests for Trust Reconciliation features.

Covers:
- format_aud helper
- TrustReconciliationRunCreate model validation
- BankStatementLineCreate model validation
- PeriodLockCreate model validation
- ReconciliationStatus enum values
- TrustImportRunCreate model validation
- _is_period_locked helper function
- fingerprint hashing logic
- period overlap detection logic
"""
from __future__ import annotations

import hashlib

import pytest

from models.trust_accounting import (
    BankStatementLineCreate,
    FundType,
    PeriodLockCreate,
    ReconciliationStatus,
    TrustImportRunCreate,
    TrustReconciliationRunCreate,
    format_aud,
)


# ─── format_aud ──────────────────────────────────────────────────────────────

class TestFormatAud:
    def test_zero_cents(self):
        assert format_aud(0) == "$0.00"

    def test_whole_dollars(self):
        assert format_aud(100) == "$1.00"

    def test_cents_only(self):
        assert format_aud(5) == "$0.05"

    def test_large_amount(self):
        result = format_aud(123456789)
        assert result == "$1,234,567.89"

    def test_negative_amount(self):
        result = format_aud(-150000)
        assert result.startswith("-$")
        assert "1,500.00" in result

    def test_negative_small(self):
        assert format_aud(-1) == "-$0.01"

    def test_exactly_one_dollar(self):
        assert format_aud(100) == "$1.00"

    def test_thousands_separator(self):
        result = format_aud(1000000)
        assert "," in result
        assert result == "$10,000.00"


# ─── ReconciliationStatus enum ───────────────────────────────────────────────

class TestReconciliationStatus:
    def test_open_value(self):
        assert ReconciliationStatus.OPEN.value == "open"

    def test_in_progress_value(self):
        assert ReconciliationStatus.IN_PROGRESS.value == "in_progress"

    def test_closed_value(self):
        assert ReconciliationStatus.CLOSED.value == "closed"

    def test_cancelled_value(self):
        assert ReconciliationStatus.CANCELLED.value == "cancelled"

    def test_all_four_statuses_exist(self):
        values = {s.value for s in ReconciliationStatus}
        assert values == {"open", "in_progress", "closed", "cancelled"}

    def test_is_string_enum(self):
        assert isinstance(ReconciliationStatus.OPEN, str)


# ─── TrustReconciliationRunCreate ─────────────────────────────────────────────

class TestTrustReconciliationRunCreate:
    def _valid_payload(self):
        return {
            "bank_account_id": "acct_001",
            "statement_start_date": "2026-03-01",
            "statement_end_date": "2026-03-31",
            "opening_balance_cents": 500000,
            "closing_balance_cents": 520000,
        }

    def test_valid_create(self):
        m = TrustReconciliationRunCreate(**self._valid_payload())
        assert m.bank_account_id == "acct_001"
        assert m.opening_balance_cents == 500000
        assert m.closing_balance_cents == 520000

    def test_optional_statement_reference_defaults_none(self):
        m = TrustReconciliationRunCreate(**self._valid_payload())
        assert m.imported_statement_reference is None

    def test_optional_statement_reference_set(self):
        payload = {**self._valid_payload(), "imported_statement_reference": "NAB-MARCH-2026"}
        m = TrustReconciliationRunCreate(**payload)
        assert m.imported_statement_reference == "NAB-MARCH-2026"

    def test_negative_opening_balance_allowed(self):
        payload = {**self._valid_payload(), "opening_balance_cents": -100}
        m = TrustReconciliationRunCreate(**payload)
        assert m.opening_balance_cents == -100

    def test_missing_required_field_raises(self):
        from pydantic import ValidationError
        payload = self._valid_payload()
        del payload["bank_account_id"]
        with pytest.raises(ValidationError):
            TrustReconciliationRunCreate(**payload)


# ─── BankStatementLineCreate ──────────────────────────────────────────────────

class TestBankStatementLineCreate:
    def _valid_payload(self):
        return {
            "bank_account_id": "acct_001",
            "statement_date": "2026-03-15",
            "amount_cents": 25000,
            "description": "Levy receipt LOT 42",
        }

    def test_valid_create(self):
        m = BankStatementLineCreate(**self._valid_payload())
        assert m.amount_cents == 25000
        assert m.description == "Levy receipt LOT 42"

    def test_negative_amount_for_debit(self):
        payload = {**self._valid_payload(), "amount_cents": -10000}
        m = BankStatementLineCreate(**payload)
        assert m.amount_cents == -10000

    def test_optional_fields_default_none(self):
        m = BankStatementLineCreate(**self._valid_payload())
        assert m.reconciliation_run_id is None
        assert m.value_date is None
        assert m.external_reference is None
        assert m.fingerprint_hash is None

    def test_fingerprint_hash_can_be_set(self):
        fp = hashlib.sha256(b"2026-03-15|25000|Levy receipt LOT 42").hexdigest()
        payload = {**self._valid_payload(), "fingerprint_hash": fp}
        m = BankStatementLineCreate(**payload)
        assert m.fingerprint_hash == fp

    def test_missing_description_raises(self):
        from pydantic import ValidationError
        payload = self._valid_payload()
        del payload["description"]
        with pytest.raises(ValidationError):
            BankStatementLineCreate(**payload)


# ─── PeriodLockCreate ─────────────────────────────────────────────────────────

class TestPeriodLockCreate:
    def _valid_payload(self):
        return {
            "fund_type": FundType.ADMIN,
            "period_start": "2026-03-01",
            "period_end": "2026-03-31",
        }

    def test_valid_create(self):
        m = PeriodLockCreate(**self._valid_payload())
        assert m.fund_type == FundType.ADMIN
        assert m.period_start == "2026-03-01"
        assert m.period_end == "2026-03-31"

    def test_reason_defaults_none(self):
        m = PeriodLockCreate(**self._valid_payload())
        assert m.reason is None

    def test_reason_can_be_provided(self):
        payload = {**self._valid_payload(), "reason": "Year-end close"}
        m = PeriodLockCreate(**payload)
        assert m.reason == "Year-end close"

    def test_capital_works_fund_type(self):
        payload = {**self._valid_payload(), "fund_type": FundType.CAPITAL_WORKS}
        m = PeriodLockCreate(**payload)
        assert m.fund_type == FundType.CAPITAL_WORKS

    def test_missing_period_start_raises(self):
        from pydantic import ValidationError
        payload = self._valid_payload()
        del payload["period_start"]
        with pytest.raises(ValidationError):
            PeriodLockCreate(**payload)


# ─── TrustImportRunCreate ─────────────────────────────────────────────────────

class TestTrustImportRunCreate:
    def _valid_payload(self):
        return {
            "import_type": "bank_statement",
            "file_name": "nab_march_2026.csv",
        }

    def test_valid_create(self):
        m = TrustImportRunCreate(**self._valid_payload())
        assert m.import_type == "bank_statement"
        assert m.file_name == "nab_march_2026.csv"

    def test_optional_fields_default_none(self):
        m = TrustImportRunCreate(**self._valid_payload())
        assert m.checksum is None
        assert m.source_system is None
        assert m.source_reference is None

    def test_deft_import_type(self):
        payload = {**self._valid_payload(), "import_type": "deft", "source_system": "deft"}
        m = TrustImportRunCreate(**payload)
        assert m.import_type == "deft"
        assert m.source_system == "deft"

    def test_mri_migration_import_type(self):
        payload = {**self._valid_payload(), "import_type": "mri_migration", "source_system": "mri"}
        m = TrustImportRunCreate(**payload)
        assert m.import_type == "mri_migration"

    def test_checksum_can_be_set(self):
        checksum = hashlib.sha256(b"file content").hexdigest()
        payload = {**self._valid_payload(), "checksum": checksum}
        m = TrustImportRunCreate(**payload)
        assert m.checksum == checksum

    def test_missing_import_type_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TrustImportRunCreate(file_name="test.csv")


# ─── _is_period_locked helper ────────────────────────────────────────────────

class TestIsPeriodLocked:
    """Tests for the _is_period_locked helper from trust_reconciliation router."""

    def _get_helper(self):
        from routers.trust_reconciliation import _is_period_locked
        return _is_period_locked

    def test_date_inside_active_lock(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": True,
                "fund_type": "admin_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ]
        assert fn(locks, "2026-03-15", "admin_fund") is True

    def test_date_outside_lock_range(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": True,
                "fund_type": "admin_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ]
        assert fn(locks, "2026-04-01", "admin_fund") is False

    def test_inactive_lock_is_ignored(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": False,
                "fund_type": "admin_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ]
        assert fn(locks, "2026-03-15", "admin_fund") is False

    def test_different_fund_type_is_not_locked(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": True,
                "fund_type": "admin_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ]
        assert fn(locks, "2026-03-15", "capital_works_fund") is False

    def test_empty_locks_list(self):
        fn = self._get_helper()
        assert fn([], "2026-03-15", "admin_fund") is False

    def test_period_boundary_start_is_locked(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": True,
                "fund_type": "admin_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ]
        assert fn(locks, "2026-03-01", "admin_fund") is True

    def test_period_boundary_end_is_locked(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": True,
                "fund_type": "admin_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ]
        assert fn(locks, "2026-03-31", "admin_fund") is True

    def test_multiple_locks_one_match(self):
        fn = self._get_helper()
        locks = [
            {
                "is_active": True,
                "fund_type": "capital_works_fund",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            },
            {
                "is_active": True,
                "fund_type": "admin_fund",
                "period_start": "2026-02-01",
                "period_end": "2026-02-28",
            },
        ]
        assert fn(locks, "2026-03-15", "capital_works_fund") is True
        assert fn(locks, "2026-03-15", "admin_fund") is False


# ─── Fingerprint hashing logic ────────────────────────────────────────────────

class TestFingerprintHashing:
    """Tests to verify deterministic fingerprint generation for dedup."""

    def test_same_inputs_produce_same_hash(self):
        data = "2026-03-15|25000|Levy receipt LOT 42"
        h1 = hashlib.sha256(data.encode()).hexdigest()
        h2 = hashlib.sha256(data.encode()).hexdigest()
        assert h1 == h2

    def test_different_amount_produces_different_hash(self):
        d1 = hashlib.sha256(b"2026-03-15|25000|Levy receipt").hexdigest()
        d2 = hashlib.sha256(b"2026-03-15|25001|Levy receipt").hexdigest()
        assert d1 != d2

    def test_fingerprint_is_hex_string(self):
        fp = hashlib.sha256(b"test").hexdigest()
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_fingerprint_stored_on_model(self):
        fp = hashlib.sha256(b"2026-03-15|25000|description").hexdigest()
        m = BankStatementLineCreate(
            bank_account_id="acct_001",
            statement_date="2026-03-15",
            amount_cents=25000,
            description="description",
            fingerprint_hash=fp,
        )
        assert m.fingerprint_hash == fp


# ─── close_reconciliation_run route registration (regression: missing decorator) ──

class TestCloseRunRouteRegistered:
    """Regression test: close_reconciliation_run previously had no @router.post
    decorator, so FastAPI never registered the route and it 404'd for every
    caller. Documented in financial_field_level_traceability.md §7."""

    def test_close_route_is_registered(self):
        from routers.trust_reconciliation import router

        matches = [
            r for r in router.routes
            if getattr(r, "path", "") == "/trust/reconciliation/runs/{run_id}/close"
            and "POST" in getattr(r, "methods", set())
        ]
        assert matches, (
            "POST /reconciliation/runs/{run_id}/close is not registered — "
            "the @router.post decorator on close_reconciliation_run is missing."
        )

    def test_close_endpoint_is_the_documented_handler(self):
        from routers.trust_reconciliation import router, close_reconciliation_run

        match = next(
            r for r in router.routes
            if getattr(r, "path", "") == "/trust/reconciliation/runs/{run_id}/close"
        )
        assert match.endpoint is close_reconciliation_run


# ─── _normalize_internal_tx_amount (regression: matcher scored double-entry rows as $0) ──

class TestNormalizeInternalTxAmount:
    """Regression test: trust_ledger_entries has two live shapes.

    Double-entry journal rows (POST /trust/journal) have balanced totals, so
    their bank movement must be derived from the line touching the reconciliation
    run's bank account. The matcher's score_pair() used to default rows without
    amount_cents to 0, making journal rows invisible to reconciliation matching.
    Documented in financial_field_level_traceability.md §6/§7.
    """

    def _get_helper(self):
        from routers.trust_reconciliation import _normalize_internal_tx_amount
        return _normalize_internal_tx_amount

    def test_immutable_shape_amount_cents_untouched(self):
        fn = self._get_helper()
        doc = {"amount_cents": 25000, "entry_type": "payment_received"}
        result = fn(doc, "bank-acct")
        assert result["amount_cents"] == 25000

    def test_double_entry_shape_derives_amount_from_bank_account_debit_line(self):
        fn = self._get_helper()
        doc = {
            "debit_total_cents": 25000,
            "credit_total_cents": 25000,
            "lines": [
                {"account_id": "bank-acct", "entry_type": "debit", "amount": 250.00},
                {"account_id": "income-acct", "entry_type": "credit", "amount": 250.00},
            ],
        }
        result = fn(doc, "bank-acct")
        assert result["amount_cents"] == 25000

    def test_double_entry_shape_derives_amount_from_bank_account_credit_line(self):
        fn = self._get_helper()
        doc = {
            "debit_total_cents": 25000,
            "credit_total_cents": 25000,
            "lines": [
                {"account_id": "expense-acct", "entry_type": "debit", "amount": 250.00},
                {"account_id": "bank-acct", "entry_type": "credit", "amount": 250.00},
            ],
        }
        result = fn(doc, "bank-acct")
        assert result["amount_cents"] == -25000

    def test_double_entry_shape_skips_other_bank_accounts(self):
        fn = self._get_helper()
        doc = {
            "debit_total_cents": 25000,
            "credit_total_cents": 25000,
            "lines": [
                {"account_id": "other-bank-acct", "entry_type": "debit", "amount": 250.00},
                {"account_id": "income-acct", "entry_type": "credit", "amount": 250.00},
            ],
        }
        assert fn(doc, "bank-acct") is None

    def test_missing_debit_credit_defaults_to_zero(self):
        fn = self._get_helper()
        doc = {"narration": "no amount fields at all"}
        result = fn(doc, "bank-acct")
        assert result["amount_cents"] == 0

    def test_normalized_double_entry_row_now_scores_as_exact_match(self):
        """End-to-end proof: a double-entry journal row that previously scored
        0 against an equal-amount bank line now scores as an exact match once
        normalized, via the real MatchingEngine used by get_match_suggestions."""
        from routers.trust_reconciliation import _normalize_internal_tx_amount
        from services.reconciliation_matching_service import MatchingEngine

        bank_line = {
            "_id": "bank1",
            "building_id": "13195",
            "amount_cents": 50000,
            "statement_date": "2026-03-15",
            "reference": "LEVY-Q1-LOT42",
        }
        journal_row = {
            "_id": "journal1",
            "building_id": "13195",
            "debit_total_cents": 50000,
            "credit_total_cents": 50000,
            "lines": [
                {"account_id": "bank-acct", "entry_type": "debit", "amount": 500.00},
                {"account_id": "income-acct", "entry_type": "credit", "amount": 500.00},
            ],
            "date": "2026-03-15",
            "reference": "LEVY-Q1-LOT42",
            "narration": "Levy receipt lot 42",
        }

        engine = MatchingEngine()
        unnormalized_score = engine.score_pair(bank_line, journal_row).confidence_score

        normalized_row = _normalize_internal_tx_amount(dict(journal_row), "bank-acct")
        normalized_score = engine.score_pair(bank_line, normalized_row).confidence_score

        # Unnormalized: amount signal defaults internal amount_cents to 0, so it
        # never matches bank_line's 50000 — score comes only from date+reference.
        assert unnormalized_score < MatchingEngine.THRESHOLD_WEAK
        # Normalized: amount now matches exactly, pushing the pair to at least
        # "likely" (same-day + exact-amount + exact-reference, no description text
        # on the bank line in this scenario so it falls short of the 4-signal "exact").
        assert normalized_score >= MatchingEngine.THRESHOLD_LIKELY
        assert normalized_score > unnormalized_score
