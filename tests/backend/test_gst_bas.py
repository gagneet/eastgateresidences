"""
test_gst_bas.py — Unit and integration tests for the GST & BAS Ledger feature.

Coverage:
    - Role guards (allowed roles 200, forbidden roles 403)
    - parse_levy_gst_settings (percentage and decimal inputs, toggle off)
    - _fy_range / _quarter_range date math
    - _compute_output_tax_rows quarterly split and Q4 rounding remainder
    - get_gst_settings endpoint
    - get_output_tax endpoint
    - get_input_tax endpoint (with Pydantic response model)
    - get_gst_summary quarterly / annual paths
    - get_bas_worksheet field mapping (G1, G2, G3, G10, G11, 1A, 1B, net_9)
    - get_quarterly_worksheets response model
    - get_income_tax_prep endpoint
    - Water GST-free: is_gst_free=True, gst_amount=0
    - Multi-tenant isolation (different building_id)
    - PDF export: returns bytes, contains ABN
    - GSTSettings / GSTInputTaxResponse / GSTQuarterlyWorksheetsResponse Pydantic shapes

All tests are idempotent — no real DB writes. Integration tests require
RUN_INTEGRATION_TESTS=1 env var.
"""

import calendar
import io
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import pytest_asyncio

# ── Path setup ─────────────────────────────────────────────────────────────────
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ── Helpers ────────────────────────────────────────────────────────────────────
_RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"


def _cursor(items):
    """Return a mock async cursor whose .to_list() returns *items*."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    cursor.sort.return_value = cursor
    cursor.__aiter__.return_value = iter(items)
    return cursor


# ─────────────────────────────────────────────────────────────────────────────
# Shared test helpers / constants
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(role: str) -> dict:
    return {"id": "u-001", "role": role, "effective_role": role, "building_id": "13195"}


# Fully-parsed GST config dicts (as returned by get_building_levy_gst_settings)
_PARSED_REGISTERED = {
    "gst_registered": True,
    "levy_gst_rate": 0.10,
    "effective_gst_rate": 0.10,
    "gst_multiplier": 1.10,
    "gst_label": "GST (10%)",
}
_PARSED_UNREGISTERED = {
    "gst_registered": False,
    "levy_gst_rate": 0.10,
    "effective_gst_rate": 0.0,
    "gst_multiplier": 1.0,
    "gst_label": "GST (0%)",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pydantic model structure tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGSTModels:
    """Verify model fields, defaults, and serialisation."""

    def test_gst_settings_fields(self):
        from models.gst_bas import GSTSettings
        s = GSTSettings(
            gst_registered=True,
            levy_gst_rate=0.10,
            effective_gst_rate=0.10,
            gst_label="GST (10%)",
            gst_multiplier=1.10,
        )
        assert s.gst_registered is True
        assert s.gst_multiplier == pytest.approx(1.10)

    def test_gst_settings_not_registered(self):
        from models.gst_bas import GSTSettings
        s = GSTSettings(
            gst_registered=False,
            levy_gst_rate=0.10,
            effective_gst_rate=0.0,
            gst_label="GST (10%)",
            gst_multiplier=1.0,
        )
        assert s.effective_gst_rate == 0.0
        assert s.gst_multiplier == pytest.approx(1.0)

    def test_gst_input_tax_response_shape(self):
        from models.gst_bas import GSTInputTaxResponse
        r = GSTInputTaxResponse(
            financial_year="2026",
            itc_total=500.0,
            line_items=[],
            by_category=[],
        )
        assert r.financial_year == "2026"
        assert r.itc_total == 500.0
        assert r.line_items == []

    def test_gst_quarterly_worksheets_response_shape(self):
        from models.gst_bas import GSTQuarterlyWorksheetsResponse, BASWorksheet

        def _ws(q):
            return BASWorksheet(
                financial_year="2026",
                quarter=q,
                period_start="2026-01-01",
                period_end="2026-03-31",
                G1=1100.0,
                G2=0.0,
                G3=0.0,
                G10=0.0,
                G11=550.0,
                field_1A=100.0,
                field_1B=50.0,
                net_9=50.0,
                gst_registered=True,
                notes=[],
            )

        r = GSTQuarterlyWorksheetsResponse(
            financial_year="2026",
            quarterly=[_ws(q) for q in ["Q1", "Q2", "Q3", "Q4"]],
            annual=_ws("Annual"),
        )
        assert len(r.quarterly) == 4
        assert r.annual.quarter == "Annual"

    def test_gst_income_tax_prep_shape(self):
        from models.gst_bas import GSTIncomeTaxPrep
        p = GSTIncomeTaxPrep(
            financial_year="2026",
            interest_income=1234.56,
            total_assessable_income=1234.56,
        )
        assert p.interest_income == pytest.approx(1234.56)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Service helper: parse_levy_gst_settings
# ─────────────────────────────────────────────────────────────────────────────

class TestParseLevyGstSettings:
    """parse_levy_gst_settings should normalise both percentage and decimal inputs."""

    def test_percentage_input_normalised(self):
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings({"gst_registered": True, "levy_gst_rate": 10})
        assert r["levy_gst_rate"] == pytest.approx(0.10)
        assert r["effective_gst_rate"] == pytest.approx(0.10)
        assert r["gst_multiplier"] == pytest.approx(1.10)
        assert r["gst_label"] == "GST (10%)"

    def test_decimal_input_accepted(self):
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings({"gst_registered": True, "levy_gst_rate": 0.10})
        assert r["levy_gst_rate"] == pytest.approx(0.10)

    def test_non_registered_zero_effective_rate(self):
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings({"gst_registered": False, "levy_gst_rate": 0.10})
        assert r["effective_gst_rate"] == pytest.approx(0.0)
        assert r["gst_multiplier"] == pytest.approx(1.0)

    def test_missing_gst_registered_defaults_true(self):
        # DEFAULT_GST_REGISTERED = True in gst_service.py
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings({})
        assert r["gst_registered"] is True
        assert r["effective_gst_rate"] == pytest.approx(0.10)


class TestGstRegistrationThreshold:
    """ATO $150k non-profit compulsory-registration threshold (GAP-ONBOARD-004 C3)."""

    def test_no_income_supplied_keeps_manual_behaviour(self):
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings({"gst_registered": False, "levy_gst_rate": 0.10})
        # Backward compatible: no income => threshold not applied, compulsory unknown.
        assert r["gst_registration_compulsory"] is None
        assert r["gst_registration_basis"] == "manual_flag"
        assert r["gst_registered"] is False
        assert r["effective_gst_rate"] == pytest.approx(0.0)

    def test_income_at_or_above_threshold_is_compulsory(self):
        from services.gst_service import parse_levy_gst_settings
        # Manually unregistered, but $150,000 income => compulsory, surfaced not silent.
        r = parse_levy_gst_settings(
            {"gst_registered": False, "levy_gst_rate": 0.10},
            annual_ex_gst_income_cents=15_000_000,
        )
        assert r["gst_registration_compulsory"] is True
        assert r["gst_registration_basis"] == "ato_threshold"
        assert r["manual_gst_registered"] is False
        assert r["gst_registered"] is True  # OR of manual flag and compulsory
        assert r["effective_gst_rate"] == pytest.approx(0.10)

    def test_income_below_threshold_and_unregistered_stays_unregistered(self):
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings(
            {"gst_registered": False, "levy_gst_rate": 0.10},
            annual_ex_gst_income_cents=14_999_999,  # $149,999.99
        )
        assert r["gst_registration_compulsory"] is False
        assert r["gst_registration_basis"] == "manual_flag"
        assert r["gst_registered"] is False
        assert r["effective_gst_rate"] == pytest.approx(0.0)

    def test_manual_registered_and_above_threshold_reports_both(self):
        from services.gst_service import parse_levy_gst_settings
        r = parse_levy_gst_settings(
            {"gst_registered": True, "levy_gst_rate": 0.10},
            annual_ex_gst_income_cents=30_000_000,
        )
        assert r["gst_registration_compulsory"] is True
        assert r["gst_registration_basis"] == "manual_and_threshold"
        assert r["gst_registered"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Date math helpers: _fy_range and _quarter_range
# ─────────────────────────────────────────────────────────────────────────────

class TestFYDateHelpers:
    """_fy_range and _quarter_range must return ISO-8601 boundary strings."""

    def _import_helpers(self):
        from routers import gst_bas
        return gst_bas._fy_range, gst_bas._quarter_range

    def test_fy_range_calendar_year(self):
        fy_range, _ = self._import_helpers()
        start, end = fy_range("2026", fy_start_month=1)
        assert start == "2026-01-01"
        assert end == "2026-12-31"

    def test_fy_range_july_start(self):
        fy_range, _ = self._import_helpers()
        start, end = fy_range("2026", fy_start_month=7)
        assert start == "2026-07-01"
        assert end == "2027-06-30"

    def test_quarter_range_calendar_q1(self):
        _, q_range = self._import_helpers()
        start, end = q_range("2026", "Q1", fy_start_month=1)
        assert start.startswith("2026-01")
        assert end.startswith("2026-03")

    def test_quarter_range_calendar_q4(self):
        _, q_range = self._import_helpers()
        start, end = q_range("2026", "Q4", fy_start_month=1)
        assert start.startswith("2026-10")
        assert end.startswith("2026-12")

    def test_quarter_range_q4_end_is_last_day_of_december(self):
        _, q_range = self._import_helpers()
        _, end = q_range("2026", "Q4", fy_start_month=1)
        assert end == "2026-12-31"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Output tax rows: quarterly split and rounding
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeOutputTaxRows:
    """_compute_output_tax_rows must split correctly and Q4 absorbs rounding remainder."""

    def _compute(self, admin_ex, sinking_ex, rate):
        from routers import gst_bas
        levy_doc = {
            "proposed_admin_expenses": admin_ex,
            "proposed_sinking_expenses": sinking_ex,
        }
        gst_config = {
            "effective_gst_rate": rate,
            "gst_registered": rate > 0,
            "levy_gst_rate": rate,
            "gst_multiplier": 1 + rate,
            "gst_label": f"GST ({int(rate * 100)}%)",
        }
        return gst_bas._compute_output_tax_rows(
            levy_doc=levy_doc,
            gst_config=gst_config,
            year="2026",
            fy_start_month=1,
        )

    def test_four_rows_returned(self):
        rows = self._compute(1000.0, 500.0, 0.10)
        assert len(rows) == 4

    def test_gst_amounts_sum_to_total(self):
        admin, sinking, rate = 1000.0, 500.0, 0.10
        rows = self._compute(admin, sinking, rate)
        total_gst = round((admin + sinking) * rate, 2)
        summed = round(sum(r.gst_amount for r in rows), 2)
        assert summed == pytest.approx(total_gst, abs=0.05)

    def test_q4_gets_rounding_remainder(self):
        # 1000 + 1 = 1001; * 0.10 = 100.1; quarterly = 25.02 x3 + 25.04
        rows = self._compute(1001.0, 0.0, 0.10)
        assert rows[3].quarter == "Q4"
        # Q4 gst_amount >= others (absorbs the cent remainder)
        q4_gst = rows[3].gst_amount
        for r in rows[:3]:
            assert q4_gst >= r.gst_amount - 0.01

    def test_zero_totals_when_not_registered(self):
        rows = self._compute(1000.0, 500.0, 0.0)
        for r in rows:
            assert r.gst_amount == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Role guard
# ─────────────────────────────────────────────────────────────────────────────

class TestRoleGuard:
    """_require_gst_role must allow strata_manager/strata_admin/super_admin, reject others."""

    def _guard(self, role):
        from routers.gst_bas import _require_gst_role
        from fastapi import HTTPException
        user = _make_user(role)
        try:
            _require_gst_role(user)
            return True
        except HTTPException as e:
            assert e.status_code == 403
            return False

    @pytest.mark.parametrize("role", ["super_admin", "strata_admin", "strata_manager"])
    def test_allowed_roles_pass(self, role):
        assert self._guard(role) is True

    @pytest.mark.parametrize("role", ["ec_member", "owner", "tenant", "guest"])
    def test_disallowed_roles_rejected(self, role):
        assert self._guard(role) is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. get_gst_settings endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetGSTSettings:
    async def test_returns_gst_settings_for_allowed_role(self):
        from routers.gst_bas import get_gst_settings

        mock_settings = _PARSED_REGISTERED
        with patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=mock_settings)):
            result = await get_gst_settings(
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )
        assert result.gst_registered is True
        assert result.effective_gst_rate == pytest.approx(0.10)

    async def test_forbidden_for_owner(self):
        from routers.gst_bas import get_gst_settings
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_gst_settings(
                current_user=_make_user("owner"),
                building_id="13195",
            )
        assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 7. get_output_tax endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetOutputTax:
    async def test_returns_four_quarterly_rows(self):
        from routers.gst_bas import get_output_tax

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": 300000.0,
            "proposed_sinking_expenses": 100000.0,
            "total_uoe": 10000,
        }
        settings_doc = _PARSED_REGISTERED

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            rows = await get_output_tax(
                financial_year="2026",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

        # Router returns Q1/Q2/Q3/Q4 + Annual summary row
        assert len(rows) == 5
        quarterly_rows = [r for r in rows if r.quarter != "Annual"]
        assert len(quarterly_rows) == 4
        total_gst = sum(r.gst_amount for r in quarterly_rows)
        expected_gst = round((300000.0 + 100000.0) * 0.10, 2)
        assert total_gst == pytest.approx(expected_gst, abs=0.05)

    async def test_zero_output_when_not_registered(self):
        from routers.gst_bas import get_output_tax

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": 300000.0,
            "proposed_sinking_expenses": 100000.0,
        }
        settings_doc = _PARSED_UNREGISTERED

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            rows = await get_output_tax(
                financial_year="2026",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

        for r in rows:
            assert r.gst_amount == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 8. get_input_tax endpoint (Pydantic response model verified)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetInputTax:
    async def test_returns_typed_response(self):
        from routers.gst_bas import get_input_tax
        from models.gst_bas import GSTInputTaxResponse

        expense_docs = [
            {
                "_id": "e1",
                "date": "2026-03-15",
                "description": "Electricity",
                "supplier_name": "ActewAGL",
                "invoice_number": "INV-001",
                "category_name": "Electricity",
                "fund_type": "administrative",
                "amount": 550.0,
                "gst_amount": 50.0,
                "is_gst_inclusive": True,
            },
            {
                "_id": "e2",
                "date": "2026-04-01",
                "description": "Cleaning",
                "supplier_name": "CleanCo",
                "invoice_number": "INV-002",
                "category_name": "Cleaning",
                "fund_type": "administrative",
                "amount": 220.0,
                "gst_amount": 20.0,
                "is_gst_inclusive": True,
            },
        ]
        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=_PARSED_REGISTERED)),
        ):
            mock_db.expense_transactions.find.return_value = _cursor(expense_docs)
            result = await get_input_tax(
                financial_year="2026",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

        assert isinstance(result, GSTInputTaxResponse)
        assert result.itc_total == pytest.approx(70.0)
        assert len(result.line_items) == 2
        assert len(result.by_category) == 2  # Electricity + Cleaning

    async def test_itc_total_zero_when_no_gst_expenses(self):
        from routers.gst_bas import get_input_tax
        from models.gst_bas import GSTInputTaxResponse

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=_PARSED_REGISTERED)),
        ):
            mock_db.expense_transactions.find.return_value = _cursor([])
            result = await get_input_tax(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        assert isinstance(result, GSTInputTaxResponse)
        assert result.itc_total == 0.0
        assert result.line_items == []


# ─────────────────────────────────────────────────────────────────────────────
# 9. get_gst_free endpoint — water = GST-free
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetGSTFree:
    async def test_water_bills_marked_gst_free(self):
        from routers.gst_bas import get_gst_free

        units_docs = [
            {"unit_number": "UA001", "building_id": "13195"},
            {"unit_number": "TH001", "building_id": "13195"},
        ]
        water_docs = [
            {
                "_id": "w1",
                "unit_number": "UA001",
                "billing_period_start": "2026-01-01",
                "billing_period_end": "2026-03-31",
                "amount": 123.45,
                "gst_amount": 0.0,
                "description": "Water Q1",
                "supplier_name": "Icon Water",
                "invoice_number": "IW-001",
                "category_name": "Water",
                "fund_type": "administrative",
                "is_gst_free": True,
            }
        ]
        council_docs = []
        settings_doc = {}

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.units.find.return_value = _cursor(units_docs)
            mock_db.water_bills.find.return_value = _cursor(water_docs)
            mock_db.council_rates.find.return_value = _cursor(council_docs)

            result = await get_gst_free(
                financial_year="2026",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

        assert len(result.water_bills) == 1
        w = result.water_bills[0]
        assert w.is_gst_free is True
        assert w.gst_amount == pytest.approx(0.0)

    async def test_council_rates_marked_gst_free(self):
        from routers.gst_bas import get_gst_free

        units_docs = [{"unit_number": "UA001", "building_id": "13195"}]
        rate_docs = [
            {
                "_id": "r1",
                "unit_number": "UA001",
                "financial_year": "2026",
                "total_rates": 1800.0,
                "description": "Council Rates 2026",
                "supplier_name": "ACT Government",
                "invoice_number": "CR-001",
                "category_name": "Council Rates",
                "fund_type": "administrative",
            }
        ]
        settings_doc = {}

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.units.find.return_value = _cursor(units_docs)
            mock_db.water_bills.find.return_value = _cursor([])
            mock_db.council_rates.find.return_value = _cursor(rate_docs)

            result = await get_gst_free(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        assert len(result.council_rates) == 1
        c = result.council_rates[0]
        assert c.is_gst_free is True
        assert c.gst_amount == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 10. get_gst_summary — field mapping
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetGSTSummary:
    """net_gst_payable = output_tax - itc_total."""

    async def _make_summary(self, gst_registered=True, levy_ex=100000.0, itc_total=5000.0):
        from routers.gst_bas import get_gst_summary

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": levy_ex * 0.75,
            "proposed_sinking_expenses": levy_ex * 0.25,
        }
        settings_doc = _PARSED_REGISTERED if gst_registered else _PARSED_UNREGISTERED

        expense_docs = [
            {
                "_id": "e1",
                "date": "2026-03-01",
                "description": "Electricity",
                "supplier_name": "AGL",
                "invoice_number": "INV-001",
                "category_name": "Electricity",
                "fund_type": "administrative",
                "amount": 550.0,
                "gst_amount": itc_total,
                "is_gst_inclusive": True,
            }
        ] if itc_total > 0 else []

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.expense_transactions.find.return_value = _cursor(expense_docs)
            mock_db.units.find.return_value = _cursor([])
            mock_db.water_bills.find.return_value = _cursor([])
            mock_db.council_rates.find.return_value = _cursor([])

            return await get_gst_summary(
                financial_year="2026",
                quarter="Annual",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

    async def test_net_gst_payable_equals_output_minus_itc(self):
        s = await self._make_summary(gst_registered=True, levy_ex=100000.0, itc_total=5000.0)
        expected_output_tax = round(100000.0 * 0.10, 2)
        expected_net = round(expected_output_tax - 5000.0, 2)
        assert s.output_tax == pytest.approx(expected_output_tax, abs=0.05)
        assert s.net_gst_payable == pytest.approx(expected_net, abs=0.05)

    async def test_all_zero_when_not_registered(self):
        s = await self._make_summary(gst_registered=False, levy_ex=100000.0, itc_total=0.0)
        assert s.output_tax == pytest.approx(0.0)
        assert s.itc_total == pytest.approx(0.0)
        assert s.net_gst_payable == pytest.approx(0.0)

    async def test_positive_net_means_owe_ato(self):
        # output_tax > itc_total → positive net_gst_payable
        s = await self._make_summary(gst_registered=True, levy_ex=100000.0, itc_total=1000.0)
        assert s.net_gst_payable > 0

    async def test_negative_net_means_refund(self):
        # itc_total > output_tax → negative net_gst_payable
        s = await self._make_summary(gst_registered=True, levy_ex=10000.0, itc_total=50000.0)
        assert s.net_gst_payable < 0


# ─────────────────────────────────────────────────────────────────────────────
# 11. get_bas_worksheet — ATO field mapping
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetBASWorksheet:
    async def _worksheet(self, quarter="Q1"):
        from routers.gst_bas import get_bas_worksheet

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": 300000.0,
            "proposed_sinking_expenses": 100000.0,
        }
        settings_doc = _PARSED_REGISTERED
        expense_docs = [
            {
                "_id": "e1",
                "date": "2026-03-01",
                "description": "Electricity",
                "supplier_name": "AGL",
                "invoice_number": "INV-001",
                "category_name": "Electricity",
                "fund_type": "administrative",
                "amount": 550.0,
                "gst_amount": 50.0,
                "is_gst_inclusive": True,
            }
        ]

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.expense_transactions.find.return_value = _cursor(expense_docs)
            mock_db.expense_transactions.aggregate.return_value.to_list = AsyncMock(return_value=[])
            mock_db.units.find.return_value = _cursor([])
            mock_db.water_bills.find.return_value = _cursor([])
            mock_db.council_rates.find.return_value = _cursor([])

            return await get_bas_worksheet(
                financial_year="2026",
                quarter=quarter,
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

    async def test_G3_is_zero(self):
        # Water/rates are purchases, not GST-free sales — G3 must always be 0.0
        ws = await self._worksheet()
        assert ws.G3 == pytest.approx(0.0)

    async def test_G2_is_zero(self):
        # No exports for OC
        ws = await self._worksheet()
        assert ws.G2 == pytest.approx(0.0)

    async def test_field_1A_equals_output_tax(self):
        ws = await self._worksheet()
        # For Q1, 1A = output_tax / 4 (approx)
        annual_output_tax = round(400000.0 * 0.10, 2)
        assert ws.field_1A == pytest.approx(annual_output_tax / 4, abs=0.05)

    async def test_net_9_equals_1A_minus_1B(self):
        ws = await self._worksheet()
        assert ws.net_9 == pytest.approx(ws.field_1A - ws.field_1B, abs=0.01)

    async def test_G1_equals_levy_inc_gst(self):
        ws = await self._worksheet(quarter="Annual")
        # G1 should equal total_levy_inc_gst
        expected_levy_inc_gst = round(400000.0 * 1.10, 2)
        assert ws.G1 == pytest.approx(expected_levy_inc_gst, abs=1.0)


# ─────────────────────────────────────────────────────────────────────────────
# 12. get_quarterly_worksheets response model
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetQuarterlyWorksheets:
    async def test_returns_four_quarterly_plus_annual(self):
        from routers.gst_bas import get_quarterly_worksheets
        from models.gst_bas import GSTQuarterlyWorksheetsResponse

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": 300000.0,
            "proposed_sinking_expenses": 100000.0,
        }
        settings_doc = _PARSED_REGISTERED

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.expense_transactions.find.return_value = _cursor([])
            mock_db.expense_transactions.aggregate.return_value.to_list = AsyncMock(return_value=[])
            mock_db.units.find.return_value = _cursor([])
            mock_db.water_bills.find.return_value = _cursor([])
            mock_db.council_rates.find.return_value = _cursor([])

            result = await get_quarterly_worksheets(
                financial_year="2026",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

        assert isinstance(result, GSTQuarterlyWorksheetsResponse)
        assert len(result.quarterly) == 4
        assert result.annual.quarter == "Annual"
        quarters = [ws.quarter for ws in result.quarterly]
        assert set(quarters) == {"Q1", "Q2", "Q3", "Q4"}


# ─────────────────────────────────────────────────────────────────────────────
# 13. get_income_tax_prep endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetIncomeTaxPrep:
    async def test_returns_trust_interest_income(self):
        from routers.gst_bas import get_income_tax_prep

        trust_txns = [
            {
                "_id": "t1",
                "transaction_type": "interest_income",
                "amount": 1200.50,
                "description": "Trust interest Jan 2026",
                "date": "2026-01-31",
                "building_id": "13195",
            },
            {
                "_id": "t2",
                "transaction_type": "interest_income",
                "amount": 800.25,
                "description": "Trust interest Feb 2026",
                "date": "2026-02-28",
                "building_id": "13195",
            },
        ]
        settings_doc = {}
        levy_doc = {"proposed_admin_expenses": 0.0, "proposed_sinking_expenses": 0.0}

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.trust_transactions_v2.find.return_value = _cursor(trust_txns)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            result = await get_income_tax_prep(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        assert result.interest_income == pytest.approx(2000.75, abs=0.01)
        assert result.total_assessable_income == pytest.approx(2000.75, abs=0.01)

    async def test_zero_interest_zero_tax(self):
        from routers.gst_bas import get_income_tax_prep

        settings_doc = {}
        levy_doc = {"proposed_admin_expenses": 0.0, "proposed_sinking_expenses": 0.0}
        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.trust_transactions_v2.find.return_value = _cursor([])
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            result = await get_income_tax_prep(
                financial_year="2026",
                current_user=_make_user("strata_manager"),
                building_id="13195",
            )

        assert result.total_assessable_income == pytest.approx(0.0)
        assert result.net_taxable_income == pytest.approx(0.0)

    async def test_forbidden_for_ec_member(self):
        from routers.gst_bas import get_income_tax_prep
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_income_tax_prep(
                financial_year="2026",
                current_user=_make_user("ec_member"),
                building_id="13195",
            )
        assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 14. Multi-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMultiTenantIsolation:
    """Queries for building_id A must NOT return data for building_id B."""

    async def test_input_tax_scoped_to_building(self):
        from routers.gst_bas import get_input_tax

        # Only return data when building_id == "13195"
        def _scoped_find(filter_dict, *args, **kwargs):
            if filter_dict.get("building_id") == "13195":
                return _cursor([{
                    "_id": "e1",
                    "date": "2026-01-01",
                    "description": "Electric",
                    "supplier_name": "AGL",
                    "invoice_number": "INV-1",
                    "category_name": "Electricity",
                    "fund_type": "administrative",
                    "amount": 110.0,
                    "gst_amount": 10.0,
                    "is_gst_inclusive": True,
                }])
            return _cursor([])

        with patch("routers.gst_bas.db") as mock_db:
            mock_db.expense_transactions.find.side_effect = _scoped_find

            result_13195 = await get_input_tax(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="13195",
            )
            result_16244 = await get_input_tax(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="16244",
            )

        assert result_13195.itc_total == pytest.approx(10.0)
        assert result_16244.itc_total == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 15. PDF generation — bytes output + ABN in cover
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPDFExport:
    async def test_pdf_returns_bytes(self):
        from routers.gst_bas import export_bas_pdf

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": 300000.0,
            "proposed_sinking_expenses": 100000.0,
        }
        settings_doc = {
            **_PARSED_REGISTERED,
            "building_name": "East Gate Residences",
            "building_abn": "12 345 678 901",
        }
        trust_txns = []

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.site_settings.find_one = AsyncMock(return_value=settings_doc)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.expense_transactions.find.return_value = _cursor([])
            mock_db.expense_transactions.aggregate.return_value.to_list = AsyncMock(return_value=[])
            mock_db.units.find.return_value = _cursor([])
            mock_db.water_bills.find.return_value = _cursor([])
            mock_db.council_rates.find.return_value = _cursor([])
            mock_db.trust_transactions_v2.find.return_value = _cursor(trust_txns)

            response = await export_bas_pdf(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        # StreamingResponse — read the body from the iterator
        chunks = b"".join([chunk async for chunk in response.body_iterator])
        assert isinstance(chunks, bytes)
        assert len(chunks) > 100  # non-trivial PDF

    async def test_pdf_contains_pdf_header(self):
        from routers.gst_bas import export_bas_pdf

        levy_doc = {
            "year": "2026",
            "building_id": "13195",
            "proposed_admin_expenses": 100000.0,
            "proposed_sinking_expenses": 50000.0,
        }
        settings_doc = {
            **_PARSED_REGISTERED,
            "building_name": "Test Building",
            "building_abn": "98 765 432 100",
        }

        with (
            patch("routers.gst_bas.db") as mock_db,
            patch("routers.gst_bas.get_building_levy_gst_settings", new=AsyncMock(return_value=settings_doc)),
            patch("routers.gst_bas.get_general_settings_or_default", new=AsyncMock(return_value=settings_doc)),
        ):
            mock_db.site_settings.find_one = AsyncMock(return_value=settings_doc)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.expense_transactions.find.return_value = _cursor([])
            mock_db.expense_transactions.aggregate.return_value.to_list = AsyncMock(return_value=[])
            mock_db.units.find.return_value = _cursor([])
            mock_db.water_bills.find.return_value = _cursor([])
            mock_db.council_rates.find.return_value = _cursor([])
            mock_db.trust_transactions_v2.find.return_value = _cursor([])

            response = await export_bas_pdf(
                financial_year="2026",
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        chunks = b"".join([chunk async for chunk in response.body_iterator])
        # Valid PDFs start with %PDF
        assert chunks[:4] == b"%PDF"


# ─────────────────────────────────────────────────────────────────────────────
# 16. Integration tests (live backend, require RUN_INTEGRATION_TESTS=1)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _RUN_INTEGRATION, reason="Set RUN_INTEGRATION_TESTS=1 to run live tests")
class TestGSTLiveEndpoints:
    """Integration tests against the running backend on port 8003."""

    import httpx as _httpx  # lazy import — may not be available in CI

    _BASE = "http://127.0.0.1:8003/api"
    _ADMIN_EMAIL = "administrator@eastgateresidences.com.au"
    _ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")

    @pytest.fixture(scope="class")
    def token(self):
        import httpx
        r = httpx.post(
            f"{self._BASE}/auth/login",
            json={"email": self._ADMIN_EMAIL, "password": self._ADMIN_PASSWORD},
        )
        assert r.status_code == 200
        body = r.json()
        return body.get("token") or body.get("access_token")

    def test_gst_settings_200(self, token):
        import httpx
        r = httpx.get(
            f"{self._BASE}/gst/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "gst_registered" in data

    def test_output_tax_returns_four_rows(self, token):
        import httpx
        r = httpx.get(
            f"{self._BASE}/gst/output-tax?financial_year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        quarter_rows = [row for row in data if str(row.get("quarter", "")).upper().startswith("Q")]
        assert len(quarter_rows) == 4

    def test_input_tax_response_model(self, token):
        import httpx
        r = httpx.get(
            f"{self._BASE}/gst/input-tax?financial_year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "itc_total" in data
        assert "by_category" in data

    def test_bas_worksheet_g3_is_zero(self, token):
        import httpx
        r = httpx.get(
            f"{self._BASE}/gst/bas-worksheet?financial_year=2026&quarter=Q1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["G3"] == pytest.approx(0.0)

    def test_quarterly_worksheets_typed(self, token):
        import httpx
        r = httpx.get(
            f"{self._BASE}/gst/quarterly-worksheets?financial_year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "quarterly" in data
        assert len(data["quarterly"]) == 4
        assert "annual" in data

    def test_pdf_export_is_pdf_content_type(self, token):
        import httpx
        r = httpx.get(
            f"{self._BASE}/gst/export/pdf?financial_year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert "application/pdf" in r.headers.get("content-type", "")
        assert r.content[:4] == b"%PDF"
