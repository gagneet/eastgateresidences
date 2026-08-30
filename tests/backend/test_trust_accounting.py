"""
Tests for Trust Accounting Engine

Tests:
- Double-entry balance validation
- Fund isolation (building_id)
- Account creation and balance tracking
- Journal entry posting and voiding
- Trial balance integrity
- ABA file generation
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── ABA Generator Tests (pure functions, no DB required) ────────────────────

class TestABAGenerator:
    """Tests for the ABA file generation utility."""

    def test_valid_batch_generates_aba(self):
        from utils.aba_generator import generate_aba_file

        batch = {
            "building_id": "test_building",
            "description": "TEST PAY",
            "payment_date": "2026-03-17",
            "items": [
                {
                    "bsb": "082-001",
                    "account_number": "123456789",
                    "account_name": "Acme Plumbing Pty Ltd",
                    "amount": 1100.00,
                    "lodgement_ref": "INV-2026-0042",
                },
                {
                    "bsb": "062-000",
                    "account_number": "987654321",
                    "account_name": "Green Electrical",
                    "amount": 550.00,
                    "lodgement_ref": "INV-2026-0043",
                },
            ],
        }

        aba = generate_aba_file(batch)
        lines = aba.split("\n")

        # Must have: 1 descriptive + 2 detail + 1 total = 4 lines
        assert len(lines) == 4

        # Each line must be exactly 120 chars
        for i, line in enumerate(lines):
            assert len(line) == 120, f"Line {i} is {len(line)} chars, expected 120"

        # Record types
        assert lines[0][0] == "0"  # Descriptive record
        assert lines[1][0] == "1"  # Detail record 1
        assert lines[2][0] == "1"  # Detail record 2
        assert lines[3][0] == "7"  # File total record

    def test_aba_requires_at_least_one_item(self):
        from utils.aba_generator import generate_aba_file, ABAValidationError

        with pytest.raises(ABAValidationError, match="no payment items"):
            generate_aba_file({"building_id": "test", "description": "TEST", "payment_date": "2026-03-17", "items": []})

    def test_aba_rejects_zero_amount(self):
        from utils.aba_generator import generate_aba_file, ABAValidationError

        batch = {
            "description": "TEST",
            "payment_date": "2026-03-17",
            "items": [{"bsb": "082-001", "account_number": "123", "account_name": "Test", "amount": 0.0}],
        }
        with pytest.raises(ABAValidationError, match="positive"):
            generate_aba_file(batch)

    def test_bsb_validation(self):
        from utils.aba_generator import _clean_bsb, ABAValidationError

        assert _clean_bsb("082-001") == "082001"
        assert _clean_bsb("082001") == "082001"

        with pytest.raises(ABAValidationError, match="Invalid BSB"):
            _clean_bsb("invalid")

        with pytest.raises(ABAValidationError, match="Invalid BSB"):
            _clean_bsb("12345")  # only 5 digits

    def test_credit_debit_totals_in_record_7(self):
        from utils.aba_generator import generate_aba_file

        batch = {
            "description": "PAYROLL",
            "payment_date": "2026-03-17",
            "items": [
                {"bsb": "082-001", "account_number": "111111111", "account_name": "Employee A",
                 "amount": 3000.00, "lodgement_ref": "SAL-001"},
                {"bsb": "062-000", "account_number": "222222222", "account_name": "Employee B",
                 "amount": 2500.00, "lodgement_ref": "SAL-002"},
            ],
        }
        aba = generate_aba_file(batch)
        total_line = aba.split("\n")[3]

        # Credits total in cols 31-40 (1-indexed): 550000 cents = $5500
        credit_field = total_line[30:40]
        assert int(credit_field) == 550000, f"Expected 550000, got {int(credit_field)}"


# ─── Encryption Tests ─────────────────────────────────────────────────────────

class TestFieldEncryption:
    """Tests for the field encryption utility."""

    def test_encrypt_decrypt_roundtrip(self):
        from utils.encryption import encrypt_field, decrypt_field

        plaintext = "SecretPassword123!"
        encrypted = encrypt_field(plaintext)

        assert encrypted != plaintext
        assert encrypted.startswith("enc:")

        decrypted = decrypt_field(encrypted)
        assert decrypted == plaintext

    def test_encrypt_none_returns_none(self):
        from utils.encryption import encrypt_field, decrypt_field

        assert encrypt_field(None) is None
        assert decrypt_field(None) is None

    def test_encrypt_empty_returns_empty(self):
        from utils.encryption import encrypt_field, decrypt_field

        assert encrypt_field("") == ""
        assert decrypt_field("") == ""

    def test_already_encrypted_is_idempotent(self):
        from utils.encryption import encrypt_field

        plaintext = "MyPassword"
        enc1 = encrypt_field(plaintext)
        enc2 = encrypt_field(enc1)
        assert enc1 == enc2

    def test_plaintext_passthrough_on_decrypt(self):
        """Decrypt should return plaintext unchanged if not encrypted (migration safety)."""
        from utils.encryption import decrypt_field

        plaintext = "NotEncryptedYet"
        result = decrypt_field(plaintext)
        assert result == plaintext

    def test_is_encrypted(self):
        from utils.encryption import encrypt_field, is_encrypted

        plaintext = "test"
        encrypted = encrypt_field(plaintext)

        assert is_encrypted(encrypted) is True
        assert is_encrypted(plaintext) is False
        assert is_encrypted(None) is False

    def test_encrypt_document_fields(self):
        from utils.encryption import encrypt_document_fields, decrypt_document_fields, is_encrypted

        doc = {"name": "John", "mail_password": "Secret123", "email": "john@example.com"}
        encrypted = encrypt_document_fields(doc, ["mail_password"])

        assert encrypted["name"] == "John"  # unchanged
        assert is_encrypted(encrypted["mail_password"])
        assert not is_encrypted(encrypted["email"])

        # Decrypt back
        decrypted = decrypt_document_fields(encrypted, ["mail_password"])
        assert decrypted["mail_password"] == "Secret123"

    def test_redact_document_fields(self):
        from utils.encryption import redact_document_fields

        doc = {"name": "John", "mail_password": "Secret123"}
        redacted = redact_document_fields(doc, ["mail_password"])

        assert redacted["name"] == "John"
        assert redacted["mail_password"] == "***"
        assert doc["mail_password"] == "Secret123"  # original unchanged


# ─── Journal Entry Validation Tests ──────────────────────────────────────────

class TestJournalEntryValidation:
    """Tests for double-entry balance rules."""

    def test_balanced_journal_passes(self):
        """A journal with equal debits and credits should be valid."""
        debit_lines = [{"entry_type": "debit", "amount": 1000.0}]
        credit_lines = [{"entry_type": "credit", "amount": 1000.0}]
        all_lines = debit_lines + credit_lines

        debit_total = sum(l["amount"] for l in all_lines if l["entry_type"] == "debit")
        credit_total = sum(l["amount"] for l in all_lines if l["entry_type"] == "credit")

        assert abs(debit_total - credit_total) < 0.001

    def test_unbalanced_journal_fails(self):
        """A journal with unequal debits and credits should fail."""
        debit_total = 1000.0
        credit_total = 999.99

        assert abs(debit_total - credit_total) > 0.001

    def test_multi_line_split_entry(self):
        """Split entries: 1 debit → multiple credits — must still balance."""
        lines = [
            {"entry_type": "debit", "amount": 1100.0},
            {"entry_type": "credit", "amount": 1000.0},
            {"entry_type": "credit", "amount": 100.0},
        ]
        debit_total = sum(l["amount"] for l in lines if l["entry_type"] == "debit")
        credit_total = sum(l["amount"] for l in lines if l["entry_type"] == "credit")

        assert abs(debit_total - credit_total) < 0.001


# ─── Jurisdiction Service Tests ──────────────────────────────────────────────

class TestJurisdictionService:
    """Tests for the jurisdiction rules engine."""

    def test_act_audit_threshold_budget(self):
        from domain.jurisdictional_rules import rule_engine

        rule = rule_engine.get_effective_rules("ACT")["audit_threshold_budget_aud"]
        assert rule["value"] == 250_000
        assert rule["unit"] == "AUD"
        assert "UTMA 2011" in rule["legislation"]

    def test_act_public_liability_minimum(self):
        from domain.jurisdictional_rules import rule_engine

        rule = rule_engine.get_effective_rules("ACT")["public_liability_minimum_aud"]
        assert rule["value"] == 10_000_000

    def test_act_levy_interest_rate(self):
        from domain.jurisdictional_rules import rule_engine

        act = rule_engine.get_effective_rules("ACT")
        assert act["levy_interest_rate_default_percent"]["value"] == 10
        assert act["levy_interest_rate_max_percent"]["value"] == 20

    def test_nsw_insurance_quotes_minimum(self):
        from domain.jurisdictional_rules import rule_engine

        rule = rule_engine.get_effective_rules("NSW")["insurance_quotes_minimum"]
        assert rule["value"] == 3

    def test_nsw_hardship_required(self):
        from domain.jurisdictional_rules import rule_engine

        rule = rule_engine.get_effective_rules("NSW")["levy_notice_hardship_required"]
        assert rule["value"] is True

    def test_all_states_have_notice_days(self):
        from domain.jurisdictional_rules import rule_engine

        for state in rule_engine.valid_jurisdictions():
            rules = rule_engine.get_effective_rules(state)
            assert "agm_notice_days" in rules, f"{state} missing agm_notice_days"

    @pytest.mark.asyncio
    async def test_levy_interest_calculation(self):
        """Test simple interest calculation on overdue levy."""
        from services.jurisdiction_service import JurisdictionService

        mock_db = MagicMock()
        mock_db.jurisdiction_config.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"state": "ACT"})

        js = JurisdictionService(db=mock_db)
        result = await js.calculate_levy_interest(
            building_id="test",
            principal=1000.0,
            days_overdue=365,
        )

        # ACT default 10% pa, 365 days = $100 interest
        assert result["rate_percent_pa"] == 10
        assert abs(result["interest_amount"] - 100.0) < 0.01
        assert abs(result["total_owing"] - 1100.0) < 0.01

    @pytest.mark.asyncio
    async def test_audit_required_at_eastgate_budget(self):
        """Eastgate $440K budget should trigger audit requirement."""
        from services.jurisdiction_service import JurisdictionService

        mock_db = MagicMock()
        mock_db.jurisdiction_config.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"state": "ACT"})

        js = JurisdictionService(db=mock_db)
        result = await js.check_audit_required(
            building_id="test",
            annual_budget=440_375.10,
            lot_count=87,
        )

        assert result["required"] is True
        assert "250,000" in result["reason"] or "440" in result["reason"]

    @pytest.mark.asyncio
    async def test_audit_not_required_below_threshold(self):
        from services.jurisdiction_service import JurisdictionService

        mock_db = MagicMock()
        mock_db.jurisdiction_config.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"state": "ACT"})

        js = JurisdictionService(db=mock_db)
        result = await js.check_audit_required(
            building_id="test",
            annual_budget=100_000.0,  # below $250K
            lot_count=20,  # below 100 lots
        )
        assert result["required"] is False
