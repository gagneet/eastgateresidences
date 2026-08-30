"""
Tests for backend/services/finance_metrics/ (facade + mongo_adapter) and the
Phase 1 fixture-consistency invariant: the frozen East Gate snapshot's
building_kpis.collection_rate must equal what the canonical facade computes
from that same snapshot's underlying dollar values.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from services.finance_metrics.facade import (
    get_current_year_collection_rate,
    get_historical_year_collection_rate,
)
from services.finance_metrics.mongo_adapter import dollars_to_cents

_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "east_gate_2026_07_12_snapshot.json"
)


class TestDollarsToCents:
    def test_none_returns_zero(self):
        assert dollars_to_cents(None) == 0

    def test_whole_dollars(self):
        assert dollars_to_cents(100) == 10000

    def test_rounds_to_nearest_cent(self):
        # Real ledger values from the live fixture have exactly 2 decimal places;
        # this covers a float-representation edge case (e.g. 1928.03 * 100 in
        # binary float arithmetic must still round to exactly 192803, not 192802).
        assert dollars_to_cents(1928.03) == 192803
        assert dollars_to_cents(110093.74) == 11009374

    def test_int_input(self):
        assert dollars_to_cents(0) == 0


class TestFacadeMetricResult:
    def test_current_year_result_has_correct_metric_id_and_scope(self):
        result = get_current_year_collection_rate(
            building_id="13195",
            financial_year="2026",
            opening_arrears_dollars=1928.03,
            levied_dollars=110093.74,
            outstanding_dollars=15284.25,
        )
        assert result.metric_id == "levy.collection.current_year.v1"
        assert result.scope == "13195"
        assert result.financial_year == "2026"
        assert result.value == Decimal("86.36")
        assert result.source == "mongo.unit_levy_ledger"

    def test_historical_year_result(self):
        result = get_historical_year_collection_rate(
            building_id="13195",
            financial_year="2025",
            levied_dollars=100000.0,
            closing_arrears_dollars=25000.0,
        )
        assert result.metric_id == "levy.collection.historical_year.v1"
        assert result.value == Decimal("75.00")


class TestFixtureConsistency:
    """The frozen East Gate fixture's building_kpis.collection_rate must equal
    what the canonical formula computes from the same fixture's dollar inputs.
    This is the permanent regression guard replacing the ephemeral live
    before/after diff done during the Phase 1 migration itself (see
    docs/architecture/financial-calculations-deep-dive.md "Golden fixture" —
    a hardcoded live number would break on legitimate data changes; this
    compares two values WITHIN the same frozen, dated snapshot instead)."""

    @pytest.fixture(scope="class")
    def fixture_data(self):
        assert _FIXTURE_PATH.exists(), f"Fixture missing: {_FIXTURE_PATH}"
        return json.loads(_FIXTURE_PATH.read_text())

    def test_current_year_collection_rate_matches_frozen_kpi(self, fixture_data):
        kpis = fixture_data["building_kpis"]
        result = get_current_year_collection_rate(
            building_id=fixture_data["building_id"],
            financial_year=fixture_data["year"],
            opening_arrears_dollars=kpis["total_opening_arrears"],
            levied_dollars=kpis["total_levied"],
            outstanding_dollars=kpis["total_arrears"],
        )
        assert float(result.value) == kpis["collection_rate"]

    def test_current_year_collection_rate_is_between_0_and_100(self, fixture_data):
        kpis = fixture_data["building_kpis"]
        assert 0 <= kpis["collection_rate"] <= 100
