"""
Tests for GAP-INF-003: Jurisdiction Rule Engine extensions.

Covers new typed methods added in this sprint:
  agm_notice_period_days, evoting_permitted, proxy caps,
  building_manager_duties_register_required, initial_maintenance_schedule_*,
  manager_report_period_months, manager_report_retention_years,
  privacy_dsar_response_days.
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "backend"))

import pytest
from decimal import Decimal
from domain.jurisdictional_rules import JurisdictionalRuleEngine


@pytest.fixture
def engine():
    return JurisdictionalRuleEngine()


class TestAGMRules:
    def test_nsw_agm_notice_days(self, engine):
        assert engine.agm_notice_period_days("NSW") == 7

    def test_act_agm_notice_days_falls_back(self, engine):
        # ACT JSON may not have agm_notice_period_days; fallback is 7
        days = engine.agm_notice_period_days("ACT")
        assert isinstance(days, int)
        assert days >= 7

    def test_nsw_evoting_permitted(self, engine):
        assert engine.evoting_permitted("NSW") is True

    def test_act_evoting_permitted(self, engine):
        # ACT: verify it returns a bool regardless
        result = engine.evoting_permitted("ACT")
        assert isinstance(result, bool)


class TestProxyCaps:
    def test_nsw_proxy_cap_pct_is_5(self, engine):
        cap = engine.proxy_cap_scheme_pct("NSW")
        assert cap == Decimal("5")

    def test_nsw_small_scheme_proxy_cap_is_1(self, engine):
        assert engine.proxy_cap_small_scheme_lots("NSW") == 1

    def test_nsw_proxy_threshold_is_20(self, engine):
        assert engine.proxy_cap_scheme_size_threshold("NSW") == 20

    def test_act_proxy_cap_pct_is_none_or_int(self, engine):
        cap = engine.proxy_cap_scheme_pct("ACT")
        assert cap is None or isinstance(cap, Decimal)

    def test_unknown_jurisdiction_raises(self, engine):
        with pytest.raises(ValueError, match="Unknown jurisdiction"):
            engine.proxy_cap_scheme_pct("ZZ")


class TestComplianceRegisters:
    def test_nsw_building_manager_duties_required(self, engine):
        assert engine.building_manager_duties_register_required("NSW") is True

    def test_act_building_manager_duties_required_is_bool(self, engine):
        result = engine.building_manager_duties_register_required("ACT")
        assert isinstance(result, bool)

    def test_nsw_initial_maintenance_schedule_required(self, engine):
        assert engine.initial_maintenance_schedule_required("NSW") is True

    def test_nsw_ims_deadline_is_6_months(self, engine):
        assert engine.initial_maintenance_schedule_deadline_months("NSW") == 6

    def test_act_ims_deadline_is_none_or_int(self, engine):
        result = engine.initial_maintenance_schedule_deadline_months("ACT")
        assert result is None or isinstance(result, int)


class TestManagerReporting:
    def test_nsw_report_period_is_6_months(self, engine):
        assert engine.manager_report_period_months("NSW") == 6

    def test_nsw_report_retention_is_10_years(self, engine):
        assert engine.manager_report_retention_years("NSW") == 10

    def test_act_report_period_is_int(self, engine):
        result = engine.manager_report_period_months("ACT")
        assert isinstance(result, int)
        assert result > 0


class TestPrivacyRules:
    def test_nsw_dsar_response_30_days(self, engine):
        assert engine.privacy_dsar_response_days("NSW") == 30

    def test_act_dsar_response_is_int(self, engine):
        result = engine.privacy_dsar_response_days("ACT")
        assert isinstance(result, int)
        assert result > 0

    def test_all_jurisdictions_have_dsar_response(self, engine):
        for jur in engine.valid_jurisdictions():
            days = engine.privacy_dsar_response_days(jur)
            assert days >= 28, f"{jur}: DSAR response must be >= 28 days"


class TestAllJurisdictions:
    """Smoke tests: all methods must return without error for all supported jurisdictions."""

    JURISDICTIONS = ["ACT", "NSW", "VIC", "QLD"]

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_agm_notice_period_days(self, engine, jur):
        assert isinstance(engine.agm_notice_period_days(jur), int)

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_evoting_permitted(self, engine, jur):
        assert isinstance(engine.evoting_permitted(jur), bool)

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_proxy_cap_scheme_size_threshold(self, engine, jur):
        assert isinstance(engine.proxy_cap_scheme_size_threshold(jur), int)

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_building_manager_duties_required(self, engine, jur):
        assert isinstance(engine.building_manager_duties_register_required(jur), bool)

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_initial_maintenance_schedule_required(self, engine, jur):
        assert isinstance(engine.initial_maintenance_schedule_required(jur), bool)

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_manager_report_period_months(self, engine, jur):
        result = engine.manager_report_period_months(jur)
        assert isinstance(result, int) and result > 0

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_manager_report_retention_years(self, engine, jur):
        result = engine.manager_report_retention_years(jur)
        assert isinstance(result, int) and result >= 7

    @pytest.mark.parametrize("jur", JURISDICTIONS)
    def test_privacy_dsar_response_days(self, engine, jur):
        result = engine.privacy_dsar_response_days(jur)
        assert isinstance(result, int) and result > 0
