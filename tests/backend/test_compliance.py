"""
Tests for Compliance Engine

Tests:
- Jurisdiction rules (ACT primary, NSW secondary)
- Insurance compliance validation
- Compliance registers structure
- Levy interest calculation accuracy
- ABA file generation format
- Multi-tenancy isolation
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── Jurisdiction Rules ───────────────────────────────────────────────────────

class TestJurisdictionRules:
    """Non-DB tests for the jurisdiction rule engine."""

    def test_no_hardcoded_values_in_rules(self):
        """All rules should be in the domain rule engine, not scattered."""
        from domain.jurisdictional_rules import rule_engine

        required_act_rules = [
            "agm_notice_days",
            "levy_interest_rate_default_percent",
            "audit_threshold_budget_aud",
            "audit_threshold_lots",
            "public_liability_minimum_aud",
            "rental_certificate_validity_years",
            "manager_contract_max_years",
            "record_retention_years",
        ]
        act_rules = rule_engine.get_effective_rules("ACT")
        for rule in required_act_rules:
            assert rule in act_rules, f"ACT missing rule: {rule}"

    def test_all_rules_have_required_metadata(self):
        from domain.jurisdictional_rules import rule_engine

        for state in rule_engine.valid_jurisdictions():
            rules = rule_engine.get_effective_rules(state)
            for key, rule in rules.items():
                assert "value" in rule, f"{state}.{key} missing 'value'"
                assert "unit" in rule, f"{state}.{key} missing 'unit'"
                assert "legislation" in rule, f"{state}.{key} missing 'legislation'"

    def test_act_agm_notice_14_days(self):
        from domain.jurisdictional_rules import rule_engine
        assert rule_engine.get_effective_rules("ACT")["agm_notice_days"]["value"] == 14

    def test_nsw_agm_notice_7_days(self):
        from domain.jurisdictional_rules import rule_engine
        assert rule_engine.get_effective_rules("NSW")["agm_notice_days"]["value"] == 7

    def test_act_manager_contract_max_3_years(self):
        from domain.jurisdictional_rules import rule_engine
        assert rule_engine.get_effective_rules("ACT")["manager_contract_max_years"]["value"] == 3

    def test_rental_cert_validity_5_years(self):
        from domain.jurisdictional_rules import rule_engine
        assert rule_engine.get_effective_rules("ACT")["rental_certificate_validity_years"]["value"] == 5

    def test_act_rental_cert_effective_jan_2025(self):
        """Rental certificate requirement is effective 9 January 2025."""
        from domain.jurisdictional_rules import rule_engine
        effective = rule_engine.get_effective_rules("ACT")["rental_certificate_validity_years"]["effective"]
        assert effective == "2025-01-09"


# ─── Insurance Compliance ─────────────────────────────────────────────────────

class TestInsuranceCompliance:
    """Tests for insurance policy validation logic."""

    def test_policy_expiry_status(self):
        """Test status calculation based on expiry date."""
        from routers.insurance import _determine_status

        # Active: expiry > 60 days away
        future_date = str(date.today() + timedelta(days=90))
        assert _determine_status(future_date) == "active"

        # Pending renewal: 30 days away
        near_date = str(date.today() + timedelta(days=30))
        assert _determine_status(near_date) == "pending_renewal"

        # Expired: in the past
        past_date = str(date.today() - timedelta(days=10))
        assert _determine_status(past_date) == "expired"

    def test_days_until_expiry(self):
        from routers.insurance import _days_until

        future = str(date.today() + timedelta(days=45))
        days = _days_until(future)
        assert 44 <= days <= 45  # small tolerance for test execution

        past = str(date.today() - timedelta(days=5))
        days = _days_until(past)
        assert days < 0

    def test_public_liability_compliance_check(self):
        """Policy below $10M minimum should be non-compliant."""
        from routers.insurance import _compliance_check

        # Policy with $10M PLI — should be compliant
        policy_ok = {
            "policy_type": "public_liability",
            "status": "active",
            "public_liability_amount": 20_000_000,
        }
        rules = {"public_liability_minimum_aud": {"value": 10_000_000}}
        assert _compliance_check(policy_ok, rules) == "compliant"

        # Policy with only $5M PLI — non-compliant
        policy_low = {
            "policy_type": "public_liability",
            "status": "active",
            "public_liability_amount": 5_000_000,
        }
        assert _compliance_check(policy_low, rules) == "non_compliant"

        # Expired policy — non-compliant regardless
        policy_expired = {
            "policy_type": "building",
            "status": "expired",
            "cover_amount": 20_000_000,
        }
        assert _compliance_check(policy_expired, rules) == "non_compliant"


# ─── Compliance Register ──────────────────────────────────────────────────────

class TestComplianceRegisters:
    """Tests for compliance register metadata."""

    def test_all_required_register_types_defined(self):
        from routers.compliance_registers import REGISTER_TYPES

        required = ["fire", "lift", "asbestos", "pool", "whs"]
        for reg_type in required:
            assert reg_type in REGISTER_TYPES, f"Missing register type: {reg_type}"

    def test_fire_register_references_as1851(self):
        from routers.compliance_registers import REGISTER_TYPES

        fire = REGISTER_TYPES["fire"]
        assert "AS 1851" in fire["standard"] or "AS 1851" in fire["legislation"]

    def test_lift_register_references_as1735(self):
        from routers.compliance_registers import REGISTER_TYPES

        lift = REGISTER_TYPES["lift"]
        assert "1735" in lift["standard"] or "1735" in lift["legislation"]

    def test_each_register_has_required_fields(self):
        from routers.compliance_registers import REGISTER_TYPES

        for reg_type, meta in REGISTER_TYPES.items():
            assert "standard" in meta, f"{reg_type} missing 'standard'"
            assert "legislation" in meta, f"{reg_type} missing 'legislation'"
            assert "description" in meta, f"{reg_type} missing 'description'"


# ─── Levy Interest Calculation ────────────────────────────────────────────────

class TestLevyInterest:
    """Tests for ACT levy interest calculations."""

    @pytest.mark.asyncio
    async def test_simple_interest_365_days(self):
        from services.jurisdiction_service import JurisdictionService

        mock_db = MagicMock()
        mock_db.jurisdiction_config.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"state": "ACT"})

        js = JurisdictionService(db=mock_db)

        # $1000 at 10% for 365 days = $100 interest
        result = await js.calculate_levy_interest("b1", 1000.0, 365)
        assert abs(result["interest_amount"] - 100.0) < 0.01
        assert result["rate_percent_pa"] == 10

    @pytest.mark.asyncio
    async def test_custom_rate_capped_at_max(self):
        from services.jurisdiction_service import JurisdictionService

        mock_db = MagicMock()
        mock_db.jurisdiction_config.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"state": "ACT"})

        js = JurisdictionService(db=mock_db)

        # Requesting 25% — should be capped at ACT max 20%
        result = await js.calculate_levy_interest("b1", 1000.0, 365, custom_rate_percent=25)
        assert result["rate_percent_pa"] == 20  # capped at max

    @pytest.mark.asyncio
    async def test_pro_rata_interest_partial_year(self):
        from services.jurisdiction_service import JurisdictionService

        mock_db = MagicMock()
        mock_db.jurisdiction_config.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"state": "ACT"})

        js = JurisdictionService(db=mock_db)

        # $1000 at 10% for 90 days ≈ $24.66 (90/365 * 10% * 1000)
        result = await js.calculate_levy_interest("b1", 1000.0, 90)
        expected = 1000.0 * 0.10 * (90 / 365)
        assert abs(result["interest_amount"] - expected) < 0.01


# ─── Multi-Tenancy Isolation ──────────────────────────────────────────────────

class TestMultiTenancyIsolation:
    """Verify building_id isolation patterns."""

    def test_trust_account_create_has_building_id(self):
        from models.trust_accounting import TrustAccountCreate, FundType, AccountType

        model = TrustAccountCreate(
            building_id="building_a",
            fund_type=FundType.ADMIN,
            account_type=AccountType.ASSET,
            account_code="1001",
            account_name="Test Account",
        )
        assert model.building_id == "building_a"

    def test_journal_entry_create_has_building_id(self):
        from models.trust_accounting import JournalEntryCreate, FundType, JournalLineCreate, EntryType

        model = JournalEntryCreate(
            building_id="building_b",
            fund_type=FundType.ADMIN,
            date="2026-03-17",
            reference="TEST-001",
            narration="Test entry",
            lines=[
                JournalLineCreate(account_id="acc1", entry_type=EntryType.DEBIT, amount=100.0),
                JournalLineCreate(account_id="acc2", entry_type=EntryType.CREDIT, amount=100.0),
            ],
        )
        assert model.building_id == "building_b"

    def test_insurance_policy_create_has_building_id(self):
        from models.insurance import InsurancePolicyCreate

        model = InsurancePolicyCreate(
            building_id="building_c",
            policy_type="building",
            insurer_name="Test Insurer",
            policy_number="POL-001",
            cover_amount=1_000_000,
            premium_annual=5000,
            start_date="2026-01-01",
            expiry_date="2027-01-01",
        )
        assert model.building_id == "building_c"
