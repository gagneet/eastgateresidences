"""Tests for Subsidy Map — Phase 2 (East Gate ~$42K cross-subsidy)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.levy_fairness_service import (
    SubsidyMapEntry,
    SubsidyMapResult,
    compute_subsidy_map,
)

BUILDING_ID = "13195"
FINANCIAL_YEAR = "2026-2027"


# ── Model validation tests ─────────────────────────────────────────────────

class TestSubsidyMapEntryModel:
    def test_entry_requires_all_fields(self):
        entry = SubsidyMapEntry(
            unit_type="Apartment",
            facility_category="lift",
            facility_id="LIFT-01",
            facility_name="East Wing Lift",
            benefit_group="APARTMENTS_ONLY",
            annual_cost_cents=1_200_000,
            uoe_share_cents=600_000,
            benefit_share_cents=1_200_000,
            subsidy_amount_cents=600_000,
            subsidy_direction="paying_excess",
            affected_units=["UA001", "UA002", "UA003"],
        )
        assert entry.unit_type == "Apartment"
        assert entry.annual_cost_cents == 1_200_000
        assert isinstance(entry.annual_cost_cents, int)

    def test_monetary_values_are_integers(self):
        entry = SubsidyMapEntry(
            unit_type="Townhouse",
            facility_category="lift",
            facility_id="LIFT-01",
            facility_name="East Wing Lift",
            benefit_group="APARTMENTS_ONLY",
            annual_cost_cents=1_200_000,
            uoe_share_cents=600_000,
            benefit_share_cents=0,
            subsidy_amount_cents=-600_000,
            subsidy_direction="being_subsidised",
            affected_units=["TH087"],
        )
        # Monetary fields must be int (not float)
        assert type(entry.annual_cost_cents) is int
        assert type(entry.uoe_share_cents) is int
        assert type(entry.benefit_share_cents) is int
        assert type(entry.subsidy_amount_cents) is int

    def test_subsidy_direction_values(self):
        for direction in ("paying_excess", "being_subsidised", "fair"):
            entry = SubsidyMapEntry(
                unit_type="Apartment",
                facility_category="pool",
                facility_id="POOL-01",
                facility_name="Pool",
                benefit_group="ALL_LOTS",
                annual_cost_cents=500_000,
                uoe_share_cents=250_000,
                benefit_share_cents=250_000,
                subsidy_amount_cents=0,
                subsidy_direction=direction,
                affected_units=[],
            )
            assert entry.subsidy_direction == direction


class TestSubsidyMapResultModel:
    def _make_entry(self, unit_type="Apartment", direction="paying_excess", amount=100_000):
        return SubsidyMapEntry(
            unit_type=unit_type,
            facility_category="lift",
            facility_id="F-01",
            facility_name="Lift",
            benefit_group="APARTMENTS_ONLY",
            annual_cost_cents=amount,
            uoe_share_cents=amount // 2,
            benefit_share_cents=amount,
            subsidy_amount_cents=amount // 2,
            subsidy_direction=direction,
            affected_units=[],
        )

    def test_result_fields_present(self):
        result = SubsidyMapResult(
            building_id=BUILDING_ID,
            financial_year=FINANCIAL_YEAR,
            total_cross_subsidy_cents=4_200_000,
            subsidy_map=[self._make_entry()],
            summary_by_unit_type={"Apartment": {"net_subsidy_cents": 4_200_000}},
            key_findings=["Apartments pay $42,000 more than fair share"],
            computed_at=datetime.now(timezone.utc),
        )
        assert result.building_id == BUILDING_ID
        assert result.total_cross_subsidy_cents == 4_200_000
        assert len(result.subsidy_map) == 1

    def test_total_cross_subsidy_is_integer(self):
        result = SubsidyMapResult(
            building_id=BUILDING_ID,
            financial_year=FINANCIAL_YEAR,
            total_cross_subsidy_cents=4_200_000,
            subsidy_map=[],
            summary_by_unit_type={},
            key_findings=[],
            computed_at=datetime.now(timezone.utc),
        )
        assert type(result.total_cross_subsidy_cents) is int


# ── compute_subsidy_map integration tests (mocked DB) ─────────────────────

def _make_cursor_mock(return_value=None):
    return MagicMock(to_list=AsyncMock(return_value=return_value or []))


def _make_mock_db():
    db = MagicMock()

    # building_assets (used by _derive_virtual_cost_centres)
    db.building_assets.find = MagicMock(return_value=_make_cursor_mock([
        {
            "asset_name": "East Wing Lift",
            "asset_category": "lift",
            "facility_id": "LIFT-01",
            "replacement_cost_estimate": 1_200_000,
            "expected_lifespan_years": 25,
            "benefit_group": "APARTMENTS_ONLY",
            "building_id": BUILDING_ID,
        },
        {
            "asset_name": "Swimming Pool",
            "asset_category": "pool",
            "facility_id": "POOL-01",
            "replacement_cost_estimate": 500_000,
            "expected_lifespan_years": 20,
            "benefit_group": "ALL_LOTS",
            "building_id": BUILDING_ID,
        },
    ]))

    # facilities (used by _derive_virtual_cost_centres)
    db.facilities.find = MagicMock(return_value=_make_cursor_mock([
        {
            "id": "LIFT-01",
            "name": "East Wing Lift",
            "benefit_group_id": "APARTMENTS_ONLY",
            "building_id": BUILDING_ID,
        },
        {
            "id": "POOL-01",
            "name": "Swimming Pool",
            "benefit_group_id": "ALL_LOTS",
            "building_id": BUILDING_ID,
        },
    ]))

    # benefit_groups — 'id' field is required by compute_subsidy_map line 916
    db.benefit_groups.find = MagicMock(return_value=_make_cursor_mock([
        {
            "id": "APARTMENTS_ONLY",
            "name": "APARTMENTS_ONLY",
            "unit_types": ["Apartment"],
            "building_id": BUILDING_ID,
        },
        {
            "id": "ALL_LOTS",
            "name": "ALL_LOTS",
            "unit_types": ["Apartment", "Townhouse"],
            "building_id": BUILDING_ID,
        },
    ]))

    # units
    db.units.find = MagicMock(return_value=_make_cursor_mock([
        {"unit_number": "UA001", "unit_type": "Apartment", "building_id": BUILDING_ID, "entitlement": 100},
        {"unit_number": "UA002", "unit_type": "Apartment", "building_id": BUILDING_ID, "entitlement": 100},
        {"unit_number": "TH087", "unit_type": "Townhouse", "building_id": BUILDING_ID, "entitlement": 100},
    ]))

    # unit_levy_ledger: used via .find() for actual levy totals
    db.unit_levy_ledger.find = MagicMock(return_value=_make_cursor_mock([
        {"unit_number": "UA001", "total_levied": 2_000.0, "year": "2026-2027"},
        {"unit_number": "UA002", "total_levied": 2_000.0, "year": "2026-2027"},
        {"unit_number": "TH087", "total_levied": 1_000.0, "year": "2026-2027"},
    ]))
    # Also mock aggregate (used by _get_levy_history)
    db.unit_levy_ledger.aggregate = MagicMock(return_value=_make_cursor_mock([
        {"_id": "Apartment", "total_levy_cents": 4_000_00},
        {"_id": "Townhouse", "total_levy_cents": 1_000_00},
    ]))

    # annual_levies: fallback when ledger total is 0
    db.annual_levies.find_one = AsyncMock(return_value=None)

    # unit_attributes (used by get_unit_attributes in facility_allocation_engine)
    db.unit_attributes.find = MagicMock(return_value=_make_cursor_mock([]))

    return db


class TestComputeSubsidyMap:
    async def test_returns_subsidy_map_result(self):
        db = _make_mock_db()
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        assert isinstance(result, SubsidyMapResult)
        assert result.building_id == BUILDING_ID
        assert result.financial_year == FINANCIAL_YEAR

    async def test_computed_at_is_set(self):
        db = _make_mock_db()
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        assert result.computed_at is not None

    async def test_total_cross_subsidy_is_non_negative_integer(self):
        db = _make_mock_db()
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        assert isinstance(result.total_cross_subsidy_cents, int)
        assert result.total_cross_subsidy_cents >= 0

    async def test_summary_by_unit_type_populated(self):
        db = _make_mock_db()
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        assert isinstance(result.summary_by_unit_type, dict)

    async def test_key_findings_list(self):
        db = _make_mock_db()
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        assert isinstance(result.key_findings, list)

    async def test_per_unit_type_subsidy_amounts_are_integers(self):
        """All monetary amounts in subsidy_map entries must be integers."""
        db = _make_mock_db()
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        for entry in result.subsidy_map:
            assert type(entry.annual_cost_cents) is int
            assert type(entry.uoe_share_cents) is int
            assert type(entry.benefit_share_cents) is int
            assert type(entry.subsidy_amount_cents) is int

    async def test_empty_assets_returns_zero_subsidy(self):
        """No assets in DB → zero subsidy map, no error."""
        db = MagicMock()
        db.building_assets.find = MagicMock(return_value=_make_cursor_mock([]))
        db.facilities.find = MagicMock(return_value=_make_cursor_mock([]))
        db.benefit_groups.find = MagicMock(return_value=_make_cursor_mock([]))
        db.units.find = MagicMock(return_value=_make_cursor_mock([]))
        db.unit_levy_ledger.find = MagicMock(return_value=_make_cursor_mock([]))
        db.unit_levy_ledger.aggregate = MagicMock(return_value=_make_cursor_mock([]))
        db.annual_levies.find_one = AsyncMock(return_value=None)
        db.unit_attributes.find = MagicMock(return_value=_make_cursor_mock([]))
        with patch("services.levy_fairness_service.db", db):
            result = await compute_subsidy_map(BUILDING_ID, FINANCIAL_YEAR)
        assert result.total_cross_subsidy_cents == 0
        assert result.subsidy_map == []
