"""
Tests for domain/jurisdictional_rules.py — JurisdictionalRuleEngine.

Covers: all Australian states and territories, error handling,
determinism, and singleton behaviour. No DB access — pure domain tests.
"""
import pytest
from decimal import Decimal

from domain.jurisdictional_rules import JurisdictionalRuleEngine, rule_engine


# ── ACT ──────────────────────────────────────────────────────────────────────

class TestACTRules:
    def test_max_interest_rate_pa(self):
        assert rule_engine.max_interest_rate_pa("ACT") == Decimal("10")

    def test_interest_grace_period_days_zero(self):
        assert rule_engine.interest_grace_period_days("ACT") == 0

    def test_can_transfer_between_funds_is_true(self):
        assert rule_engine.can_transfer_between_funds("ACT") is True

    def test_inter_fund_transfer_ordinary(self):
        assert rule_engine.inter_fund_transfer_resolution_required("ACT") == "ordinary"

    def test_overbudget_threshold_pct_none(self):
        assert rule_engine.overbudget_threshold_pct("ACT") is None

    def test_pooled_trust_permitted(self):
        assert rule_engine.pooled_trust_permitted("ACT") is True

    def test_interest_belongs_to_scheme(self):
        assert rule_engine.interest_belongs_to("ACT") == "scheme"

    def test_trial_balance_deadline_15_days(self):
        assert rule_engine.trial_balance_deadline_days("ACT") == 15

    def test_audit_deadline_30_september(self):
        assert rule_engine.audit_deadline("ACT") == "30 September"

    def test_committee_spending_cap_formula_none(self):
        assert rule_engine.committee_spending_cap_formula("ACT") is None

    def test_min_withholding_pct_47(self):
        assert rule_engine.min_withholding_pct_no_abn("ACT") == Decimal("47")


# ── NSW ──────────────────────────────────────────────────────────────────────

class TestNSWRules:
    def test_max_interest_rate_pa(self):
        assert rule_engine.max_interest_rate_pa("NSW") == Decimal("10")

    def test_interest_grace_period_days_30(self):
        assert rule_engine.interest_grace_period_days("NSW") == 30

    def test_can_transfer_between_funds_is_true(self):
        assert rule_engine.can_transfer_between_funds("NSW") is True

    def test_overbudget_threshold_pct_10(self):
        assert rule_engine.overbudget_threshold_pct("NSW") == Decimal("10")

    def test_interest_belongs_to_statutory_account(self):
        assert rule_engine.interest_belongs_to("NSW") == "statutory_account"

    def test_trial_balance_deadline_21_days(self):
        assert rule_engine.trial_balance_deadline_days("NSW") == 21

    def test_pooled_trust_permitted(self):
        assert rule_engine.pooled_trust_permitted("NSW") is True

    def test_levy_notice_hardship_required_key_present(self):
        rules = rule_engine.get_effective_rules("NSW")
        assert "levy_notice_hardship_required" in rules
        assert rules["levy_notice_hardship_required"]["value"] is True

    def test_insurance_quotes_minimum_3(self):
        rules = rule_engine.get_effective_rules("NSW")
        assert rules["insurance_quotes_minimum"]["value"] == 3

    def test_capital_funding_standard_and_emergency_notice_periods(self):
        assert rule_engine.levy_notice_period_days("NSW") == 30
        assert rule_engine.levy_notice_period_days("NSW", emergency=True) == 14

    def test_capital_funding_requires_hardship_and_legal_review(self):
        summary = rule_engine.capital_funding_rules_summary("NSW")
        assert summary["capital_funding_hardship_notice_required"]["value"] is True
        assert summary["capital_funding_hybrid_legal_review_required"]["value"] is True


# ── VIC ──────────────────────────────────────────────────────────────────────

class TestVICRules:
    def test_pooled_trust_not_permitted(self):
        assert rule_engine.pooled_trust_permitted("VIC") is False

    def test_interest_belongs_to_scheme(self):
        assert rule_engine.interest_belongs_to("VIC") == "scheme"

    def test_inter_fund_transfer_special(self):
        assert rule_engine.inter_fund_transfer_resolution_required("VIC") == "special"

    def test_audit_deadline_pre_agm(self):
        assert rule_engine.audit_deadline("VIC") == "pre-AGM"

    def test_can_transfer_between_funds_true(self):
        assert rule_engine.can_transfer_between_funds("VIC") is True

    def test_capital_funding_vic_special_resolution_trigger(self):
        assert rule_engine.levy_notice_period_days("VIC") == 28
        assert rule_engine.capital_funding_resolution_required("VIC") == "ordinary_or_special_when_triggered"
        assert rule_engine.capital_funding_allocation_basis("VIC") == "lot_liability"
        assert rule_engine.capital_funding_benefit_principle_available("VIC") is True
        assert rule_engine.capital_funding_tenant_consultation_required("VIC") is True


# ── QLD ──────────────────────────────────────────────────────────────────────

class TestQLDRules:
    def test_can_transfer_between_funds_is_false(self):
        assert rule_engine.can_transfer_between_funds("QLD") is False

    def test_inter_fund_transfer_is_prohibited(self):
        assert rule_engine.inter_fund_transfer_resolution_required("QLD") == "prohibited"

    def test_committee_spending_cap_formula(self):
        assert rule_engine.committee_spending_cap_formula("QLD") == "$200 * lot_count"

    def test_committee_spending_cap_per_lot_cents(self):
        assert rule_engine.committee_spending_cap_per_lot_cents("QLD") == 20000

    def test_max_interest_rate_pa(self):
        assert rule_engine.max_interest_rate_pa("QLD") == Decimal("30")

    def test_pooled_trust_not_permitted(self):
        assert rule_engine.pooled_trust_permitted("QLD") is False

    def test_interest_belongs_to_body_corporate(self):
        assert rule_engine.interest_belongs_to("QLD") == "body_corporate"

    def test_overbudget_threshold_pct_is_none(self):
        assert rule_engine.overbudget_threshold_pct("QLD") is None

    def test_capital_funding_qld_contribution_notice(self):
        assert rule_engine.levy_notice_period_days("QLD") == 30
        assert rule_engine.capital_funding_resolution_required("QLD") == "ordinary"
        assert rule_engine.capital_funding_allocation_basis("QLD") == "contribution_schedule_lot_entitlement"


# ── National capital-funding surface ─────────────────────────────────────────

class TestCapitalFundingRules:
    def test_act_special_purpose_fund_gate(self):
        assert rule_engine.levy_notice_period_days("ACT") is None
        assert rule_engine.capital_funding_resolution_required("ACT") == "special_for_special_purpose_fund"
        assert rule_engine.capital_funding_allocation_basis("ACT") == "unit_entitlement"

    @pytest.mark.parametrize(
        ("jurisdiction", "allocation_basis"),
        [
            ("SA", "unit_entitlement_or_unanimous_alternative"),
            ("WA", "unit_entitlement_subject_to_by_laws"),
            ("TAS", "unit_entitlement"),
            ("NT", "unit_entitlement"),
        ],
    )
    def test_new_jurisdictions_have_capital_funding_rules(self, jurisdiction, allocation_basis):
        assert rule_engine.levy_notice_period_days(jurisdiction) is None
        assert rule_engine.capital_funding_allocation_basis(jurisdiction) == allocation_basis
        summary = rule_engine.capital_funding_rules_summary(jurisdiction)
        assert summary["capital_funding_notice_period_verified"]["value"] is False
        assert summary["capital_funding_hybrid_legal_review_required"]["value"] is True


# ── Error handling ────────────────────────────────────────────────────────────

class TestEngineErrorHandling:
    def test_unknown_jurisdiction_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown jurisdiction"):
            rule_engine.max_interest_rate_pa("INVALID")

    def test_invalid_jurisdiction_in_get_effective_rules(self):
        with pytest.raises(ValueError):
            rule_engine.get_effective_rules("INVALID")

    def test_case_insensitive_input(self):
        assert rule_engine.max_interest_rate_pa("act") == rule_engine.max_interest_rate_pa("ACT")


# ── Determinism ───────────────────────────────────────────────────────────────

class TestEngineDeterminism:
    def test_same_input_same_output(self):
        r1 = rule_engine.max_interest_rate_pa("ACT")
        r2 = rule_engine.max_interest_rate_pa("ACT")
        assert r1 == r2

    def test_get_effective_rules_excludes_meta(self):
        rules = rule_engine.get_effective_rules("ACT")
        assert "_meta" not in rules

    def test_valid_jurisdictions_returns_all_eight(self):
        assert set(rule_engine.valid_jurisdictions()) == {"ACT", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT"}

    def test_module_singleton_matches_fresh_instance(self):
        fresh = JurisdictionalRuleEngine()
        assert rule_engine.max_interest_rate_pa("ACT") == fresh.max_interest_rate_pa("ACT")

    def test_get_rule_dict_equals_get_effective_rules(self):
        assert rule_engine.get_rule_dict("NSW") == rule_engine.get_effective_rules("NSW")

    def test_min_withholding_identical_across_jurisdictions(self):
        rates = {j: rule_engine.min_withholding_pct_no_abn(j) for j in rule_engine.valid_jurisdictions()}
        assert len(set(rates.values())) == 1

    def test_all_jurisdictions_have_trial_balance_deadline(self):
        for j in rule_engine.valid_jurisdictions():
            days = rule_engine.trial_balance_deadline_days(j)
            assert isinstance(days, int) and days > 0
