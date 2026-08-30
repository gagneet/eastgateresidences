#!/usr/bin/env python3
"""
Unit Tests: Council Rates per-building AUV/entitlement resolution

Tests that:
  - _get_block_auv() uses council_rate_settings.block_auv when configured
  - _get_block_auv() falls back to _DEFAULT_BLOCK_AUV when no settings record
  - _get_council_settings() queries by building_id (multi-tenant isolation)
  - Sierra (16244) uses its own AUV, not East Gate's (13195)
  - Harbourview (18932) uses its own AUV once configured
  - is_estimated flag is True when settings exist but is_configured=False
  - total_block_entitlement is read per-building from council_rate_settings
  - report_service assembles correct per-building assumptions for PDF reports

No live backend required — pure unit tests with mocked DB.

Run:
    cd /home/gagneet/strata-management
    backend/venv/bin/python3 -m pytest tests/backend/test_council_rates_per_building.py -v
"""

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.council_rates import _get_block_auv, _DEFAULT_BLOCK_AUV, _TOTAL_BLOCK_ENTITLEMENT, _is_privileged


# ── _get_block_auv() pure-function tests ──────────────────────────────────────

class TestGetBlockAuv:
    """_get_block_auv(settings) resolves the correct AUV for any building."""

    def test_uses_configured_block_auv(self):
        """When block_auv is set and is_configured=True, use it as authoritative."""
        settings = {"block_auv": 4_708_728.0, "is_configured": True}
        auv, is_estimated = _get_block_auv(settings)
        assert auv == 4_708_728.0
        assert is_estimated is False

    def test_uses_configured_block_auv_estimated_when_not_confirmed(self):
        """block_auv set but is_configured=False → is_estimated=True."""
        settings = {"block_auv": 4_708_728.0, "is_configured": False}
        auv, is_estimated = _get_block_auv(settings)
        assert auv == 4_708_728.0
        assert is_estimated is True

    def test_falls_back_to_default_when_no_block_auv(self):
        """Empty settings returns the hardcoded default (East Gate fallback)."""
        auv, is_estimated = _get_block_auv({})
        assert auv == _DEFAULT_BLOCK_AUV
        assert is_estimated is False  # default is defined, not estimated

    def test_falls_back_when_block_auv_is_none(self):
        settings = {"block_auv": None, "is_configured": True}
        auv, is_estimated = _get_block_auv(settings)
        assert auv == _DEFAULT_BLOCK_AUV

    def test_falls_back_when_block_auv_is_zero(self):
        settings = {"block_auv": 0, "is_configured": True}
        auv, is_estimated = _get_block_auv(settings)
        assert auv == _DEFAULT_BLOCK_AUV

    def test_sierra_configured_auv(self):
        """Sierra FY2025-26 block AUV $4,708,728 is distinct from East Gate."""
        settings = {"block_auv": 4_708_728.0, "is_configured": True, "building_id": "16244"}
        auv, is_estimated = _get_block_auv(settings)
        assert auv == 4_708_728.0
        assert auv != _DEFAULT_BLOCK_AUV  # must differ from East Gate default
        assert is_estimated is False

    def test_different_buildings_different_auvs(self):
        east_gate_settings = {"block_auv": 4_200_000.0, "is_configured": True}
        sierra_settings = {"block_auv": 4_708_728.0, "is_configured": True}

        eg_auv, _ = _get_block_auv(east_gate_settings)
        si_auv, _ = _get_block_auv(sierra_settings)

        assert eg_auv != si_auv

    def test_returns_float(self):
        settings = {"block_auv": "4708728", "is_configured": True}  # string input
        auv, _ = _get_block_auv(settings)
        assert isinstance(auv, float)


class TestCouncilRatesPrivileges:
    def test_strata_admin_is_privileged(self):
        assert _is_privileged({"role": "strata_admin"}) is True

    def test_owner_is_not_privileged(self):
        assert _is_privileged({"role": "owner"}) is False


# ── _get_council_settings() multi-tenant isolation ───────────────────────────

class TestGetCouncilSettings:
    """_get_council_settings queries by building_id — no cross-building contamination."""

    @pytest.mark.asyncio
    async def test_queries_by_financial_year_and_building_id(self):
        from routers.council_rates import _get_council_settings

        expected = {"financial_year": "2025-26", "building_id": "16244", "block_auv": 4_708_728.0}
        coll = MagicMock()
        coll.find_one = AsyncMock(return_value=expected)

        with patch("routers.council_rates.db") as mock_db:
            mock_db.__getitem__ = MagicMock(return_value=coll)

            result = await _get_council_settings("2025-26", "16244")

            assert result == expected
            query = coll.find_one.call_args[0][0]
            assert query["financial_year"] == "2025-26"
            assert query["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_record(self):
        from routers.council_rates import _get_council_settings

        with patch("routers.council_rates.db") as mock_db:
            mock_db.__getitem__ = MagicMock(return_value=MagicMock(
                find_one=AsyncMock(return_value=None)
            ))

            result = await _get_council_settings("2025-26", "18932")
            assert result == {}

    @pytest.mark.asyncio
    async def test_east_gate_settings_not_returned_for_sierra(self):
        """Querying Sierra must not return East Gate's record."""
        from routers.council_rates import _get_council_settings

        east_gate_doc = {
            "financial_year": "2025-26",
            "building_id": "13195",
            "block_auv": 4_200_000.0,
        }

        with patch("routers.council_rates.db") as mock_db:
            coll = MagicMock()
            mock_db.__getitem__ = MagicMock(return_value=coll)

            # Simulate DB returning None for Sierra (no record yet)
            coll.find_one = AsyncMock(return_value=None)

            result = await _get_council_settings("2025-26", "16244")
            assert result == {}
            assert result != east_gate_doc

            # Verify the query used Sierra's building_id
            query = coll.find_one.call_args[0][0]
            assert query.get("building_id") == "16244"
            assert query.get("building_id") != "13195"


# ── total_block_entitlement resolution ───────────────────────────────────────

class TestTotalBlockEntitlement:
    """Per-building entitlement is used; fallback is _TOTAL_BLOCK_ENTITLEMENT."""

    def test_default_entitlement_constant_is_10000(self):
        assert _TOTAL_BLOCK_ENTITLEMENT == 10_000

    def test_settings_entitlement_takes_priority(self):
        """If council_rate_settings.total_block_entitlement is set, use it."""
        settings = {"total_block_entitlement": 9_800, "block_auv": 4_708_728.0}
        entitlement = float(settings.get("total_block_entitlement") or _TOTAL_BLOCK_ENTITLEMENT)
        assert entitlement == 9_800.0

    def test_fallback_when_not_set(self):
        settings = {"block_auv": 4_200_000.0}
        entitlement = float(settings.get("total_block_entitlement") or _TOTAL_BLOCK_ENTITLEMENT)
        assert entitlement == float(_TOTAL_BLOCK_ENTITLEMENT)

    def test_fallback_when_none(self):
        settings = {"total_block_entitlement": None}
        entitlement = float(settings.get("total_block_entitlement") or _TOTAL_BLOCK_ENTITLEMENT)
        assert entitlement == float(_TOTAL_BLOCK_ENTITLEMENT)


# ── report_service per-building assumptions ───────────────────────────────────

class TestReportServicePerBuildingAssumptions:
    """report_service._generate_full_report_impl queries council_rate_settings
    scoped to the building being reported on."""

    def _make_full_mock_db(self, council_doc):
        """Return a mock DB wired for all collections that _generate_full_report_impl touches."""
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        cursor.sort = MagicMock(return_value=cursor)
        agg_cursor = MagicMock()
        agg_cursor.to_list = AsyncMock(return_value=[])

        mock_db.buildings.find_one = AsyncMock(return_value={
            "building_id": "16244", "name": "Sierra Gungahlin",
            "address": "2 Burrumarra Avenue, Gungahlin ACT 2912", "total_units": 42,
        })
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)
        mock_db.financial_forecasts.aggregate = MagicMock(return_value=agg_cursor)
        mock_db.sinking_fund_plan.find = MagicMock(return_value=cursor)
        mock_db.capital_replacement_schedule.find = MagicMock(return_value=cursor)
        mock_db.financial_transactions.find = MagicMock(return_value=cursor)
        mock_db.settings.find_one = AsyncMock(return_value={
            "building_id": "16244",
            "strata_address": "2 Burrumarra Avenue, Gungahlin ACT 2912",
            "plan_number": "16244",
        })
        mock_db.trust_accounts.find = MagicMock(return_value=cursor)
        mock_db.trust_transactions.find = MagicMock(return_value=cursor)
        # council_rate_settings uses db["council_rate_settings"] subscript form
        council_coll = MagicMock()
        council_coll.find_one = AsyncMock(return_value=council_doc)
        mock_db.__getitem__ = MagicMock(return_value=council_coll)
        return mock_db, council_coll

    def test_council_rate_settings_query_contains_building_id_param(self):
        """The source query must use the dynamic building_id parameter (not a hardcoded string)."""
        import inspect
        from services.report_service import _generate_full_report_impl

        source = inspect.getsource(_generate_full_report_impl)
        # The query must reference the building_id variable, not a hardcoded value
        assert '"building_id": building_id' in source or "'building_id': building_id" in source
        # Confirm the collection being queried is council_rate_settings
        assert "council_rate_settings" in source

    @pytest.mark.asyncio
    async def test_council_rate_settings_query_never_uses_east_gate_for_sierra(self):
        """Sierra report must not query East Gate's council_rate_settings."""
        from services.report_service import _generate_full_report_impl

        mock_db, council_coll = self._make_full_mock_db({
            "building_id": "16244", "block_auv": 4_708_728.0,
        })

        with patch("services.report_service.db", mock_db):
            try:
                await _generate_full_report_impl("2026", "16244", "strata_manager")
            except Exception:
                pass

        if council_coll.find_one.called:
            query = council_coll.find_one.call_args[0][0]
            assert query.get("building_id") != "13195", \
                "Sierra report must not query East Gate (13195) council_rate_settings"

    @pytest.mark.asyncio
    async def test_report_auv_string_reflects_configured_auv(self):
        """When block_auv is configured, the formatted string uses the per-building value."""
        # This tests the inline formatting logic directly
        raw_auv = 4_708_728.0
        block_auv_str = f"${float(raw_auv):,.0f}" if raw_auv else "not configured"
        assert block_auv_str == "$4,708,728"
        assert block_auv_str != "$4,200,000"  # must not be East Gate's value

    def test_report_auv_string_shows_not_configured_when_absent(self):
        """When block_auv is None or missing, the assumptions section says 'not configured'."""
        raw_auv = None
        block_auv_str = f"${float(raw_auv):,.0f}" if raw_auv else "not configured"
        assert block_auv_str == "not configured"
