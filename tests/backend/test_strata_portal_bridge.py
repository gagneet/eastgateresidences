"""
test_strata_portal_bridge.py — Tests for the strata-portal → finance bridge.

Verifies:
  1. parse_money() handles all portal money formats (positive, credit, zero, empty)
  2. portal_summary is included in /finance/summary response (additive, nil if absent)
  3. GET /finance/portal-bank-balances returns correct structure and respects building_id
  4. GET /finance/portal-actuals maps financial_year correctly and scopes by building_id
  5. GET /unit-levy-ledger/{unit} injects portal_net_balance/portal_synced_at
  6. GET /levy-status/{unit} includes portal_balance / portal_status / portal_synced_at
  7. Single source of truth: strata_balance/strata_status/strata_synced_at are NOT
     written to the units collection by run_scraper or strata_sync
  8. Multi-tenant isolation: building 13195 data not visible from building 16244

Run (unit tests only — no live backend needed):
    backend/venv/bin/python3 -m pytest tests/backend/test_strata_portal_bridge.py -v

Run (with integration tests, requires running backend):
    RUN_INTEGRATION_TESTS=1 backend/venv/bin/python3 -m pytest tests/backend/test_strata_portal_bridge.py -v
"""
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — allow importing from scripts/strata
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "strata"))

# ---------------------------------------------------------------------------
# 1. parse_money() — pure function, no DB
# ---------------------------------------------------------------------------

from scripts.run_scraper import PORTAL_REPORT_URL, open_committee_report, parse_money  # noqa: E402


# ---------------------------------------------------------------------------
# Async cursor helper
# ---------------------------------------------------------------------------

async def _async_cursor(items: list):
    """Yield items from a list as an async generator — simulates a Motor cursor.

    Motor cursors are async-iterable (``async for doc in collection.find(...)``).
    MagicMock() does NOT support async iteration by default, so tests that
    exercise code paths using async comprehensions over ``find()`` results
    must wire this helper instead.

    Usage::

        collection.find = MagicMock(return_value=_async_cursor([{"unit_number": "UA001"}]))
    """
    for item in items:
        yield item


class TestParseMoney:
    """Portal money string parsing — covers all formats seen in production."""

    @pytest.mark.parametrize("raw,expected", [
        ("$1,056.77", 1056.77),
        ("$0.00", 0.0),
        ("$1,020.26 (CR)", -1020.26),
        ("$289.55 (CR)", -289.55),
        ("$0.04 (CR)", -0.04),
        ("$3,060.60 (CR)", -3060.60),
        ("$1,558.44 (CR)", -1558.44),
        ("$27.49 (CR)", -27.49),
        ("$1.21 (CR)", -1.21),
        ("$0.66 (CR)", -0.66),
        ("", 0.0),
        ("N/A", 0.0),
        ("$16,412.64", 16412.64),
    ])
    def test_parse_money_formats(self, raw: str, expected: float):
        assert abs(parse_money(raw) - expected) < 0.001

    def test_credit_is_negative(self):
        assert parse_money("$100.00 (CR)") < 0

    def test_positive_is_positive(self):
        assert parse_money("$100.00") > 0

    def test_zero_is_zero(self):
        assert parse_money("$0.00") == 0.0


class _PortalLocator:
    def __init__(self, visible: bool):
        self.visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self.visible else 0

    async def is_visible(self):
        return self.visible


class _ReportPage:
    def __init__(self, *, url=PORTAL_REPORT_URL, visible_selectors=None):
        self.goto_calls = []
        self.load_states = []
        self.url = url
        self.visible_selectors = set(visible_selectors or [])

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})

    async def wait_for_load_state(self, state, timeout=None):
        self.load_states.append({"state": state, "timeout": timeout})
        raise TimeoutError("simulated open background request")

    def locator(self, selector):
        return _PortalLocator(selector in self.visible_selectors)


@pytest.mark.asyncio
async def test_open_committee_report_does_not_require_networkidle():
    page = _ReportPage(visible_selectors={"a:has-text('Building Financials')"})

    await open_committee_report(page, report_nav_timeout=1)

    assert page.goto_calls == [
        {"url": PORTAL_REPORT_URL, "wait_until": "domcontentloaded", "timeout": 60000}
    ]
    assert page.load_states == [{"state": "networkidle", "timeout": 6000}]


@pytest.mark.asyncio
async def test_open_committee_report_accepts_table_only_on_report_url():
    page = _ReportPage(visible_selectors={"table"})

    await open_committee_report(page, report_nav_timeout=1)

    assert page.goto_calls[0]["wait_until"] == "domcontentloaded"


@pytest.mark.asyncio
async def test_open_committee_report_rejects_login_redirect_with_table():
    page = _ReportPage(url="https://my.civiumstrata.com.au/login.aspx", visible_selectors={"table"})

    with pytest.raises(TimeoutError, match="Committee report did not open"):
        await open_committee_report(page, report_nav_timeout=1)


# ---------------------------------------------------------------------------
# 2. portal_summary in /finance/summary — unit test with mock DB
# ---------------------------------------------------------------------------

class TestFinanceSummaryPortalSummary:
    """portal_summary key is appended to /finance/summary response."""

    def _make_mock_db(self, portal_doc: Optional[dict] = None) -> MagicMock:
        """Build a minimal mock db that satisfies get_finance_summary queries."""
        mock_db = MagicMock()

        # annual_levies — returns a minimal levy document
        levy_doc = {
            "year": "2026",
            "status": "partial_actual",
            "admin_fund": {"total_income": 50000.0, "levy_income": 50000.0},
            "sinking_fund": {"total_income": 20000.0, "levy_income": 20000.0},
            "payment_schedule": [],
            "admin_levy_per_uoe_annual": 100.0,
            "sinking_levy_per_uoe_annual": 40.0,
            "total_uoe": 1000,
        }
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)

        # settings
        mock_db.settings = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value={
            "grace_period_days": 14,
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "first",
        })

        # levy_categories — empty
        cats_cursor = MagicMock()
        cats_cursor.to_list = AsyncMock(return_value=[])
        mock_db.levy_categories.find = MagicMock(return_value=cats_cursor)

        # unit_levy_ledger aggregate — empty
        ledger_agg = MagicMock()
        ledger_agg.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=ledger_agg)
        # Diagnostic re-check in get_finance_summary's empty-ledger-aggregate
        # branch (Task #17) — fires here since the aggregate above is always
        # empty in this fixture.
        mock_db.unit_levy_ledger.count_documents = AsyncMock(return_value=0)

        # levy_payments aggregate — empty
        payments_agg = MagicMock()
        payments_agg.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate = MagicMock(return_value=payments_agg)

        # building_summaries
        mock_db.building_summaries.find_one = AsyncMock(return_value=portal_doc)

        return mock_db

    @pytest.mark.asyncio
    async def test_portal_summary_present_when_scraper_has_run(self):
        portal_doc = {
            "building_id": "13195",
            "arrears_total": 22234.80,
            "credit_total": 7124.59,
            "arrears_count": 20,
            "credit_count": 10,
            "clear_count": 57,
            "total_lots": 87,
            "collection_rate": 77.0,
            "risk_level": "MEDIUM",
            "updated_at": "2026-04-16T10:00:00Z",
        }
        mock_db = self._make_mock_db(portal_doc)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_finance_summary
            result = await get_finance_summary(
                year="2026",
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )

        ps = result.get("portal_summary")
        assert ps is not None, "portal_summary must be present when building_summaries has data"
        assert abs(ps["arrears_total"] - 22234.80) < 0.01
        assert abs(ps["credit_total"] - 7124.59) < 0.01
        assert ps["arrears_count"] == 20
        assert ps["credit_count"] == 10
        assert ps["total_lots"] == 87
        assert ps["risk_level"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_portal_summary_none_before_first_scrape(self):
        """portal_summary is null when building_summaries has no document yet."""
        mock_db = self._make_mock_db(portal_doc=None)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_finance_summary
            result = await get_finance_summary(
                year="2026",
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )

        assert result.get("portal_summary") is None

    @pytest.mark.asyncio
    async def test_existing_unit_ledger_summary_unchanged(self):
        """Existing unit_ledger_summary keys must not be affected by portal_summary addition."""
        mock_db = self._make_mock_db(portal_doc={"arrears_total": 100.0, "credit_total": 0.0})

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_finance_summary
            result = await get_finance_summary(
                year="2026",
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )

        uls = result.get("unit_ledger_summary")
        assert uls is not None
        assert "total_levied" in uls
        assert "total_paid" in uls
        assert "net_balance" in uls


# ---------------------------------------------------------------------------
# 3. GET /finance/portal-bank-balances — unit test
# ---------------------------------------------------------------------------

class TestPortalBankBalances:
    """portal-bank-balances endpoint returns correct structure and scopes by building_id."""

    def _make_mock_db(self, bank_docs: list, summary_doc: Optional[dict]) -> MagicMock:
        mock_db = MagicMock()
        bank_cursor = MagicMock()
        bank_cursor.to_list = AsyncMock(return_value=bank_docs)
        mock_db.bank_accounts.find = MagicMock(return_value=bank_cursor)
        mock_db.building_summaries.find_one = AsyncMock(return_value=summary_doc)
        return mock_db

    @pytest.mark.asyncio
    async def test_returns_accounts_and_totals(self):
        bank_docs = [
            {
                "building_id": "13195",
                "bsb": "032-062",
                "account_number": "260611108",
                "account_name": "East Gate Admin Fund",
                "admin_balance": 16412.64,
                "sinking_balance": 0.0,
                "total_balance": 16412.64,
                "updated_at": "2026-04-16T10:00:00Z",
            }
        ]
        summary_doc = {
            "building_id": "13195",
            "arrears_total": 22234.80,
            "credit_total": 7124.59,
            "arrears_count": 20,
            "credit_count": 10,
            "clear_count": 57,
            "total_lots": 87,
            "collection_rate": 77.0,
            "risk_level": "MEDIUM",
            "updated_at": "2026-04-16T10:00:00Z",
        }
        mock_db = self._make_mock_db(bank_docs, summary_doc)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_bank_balances
            result = await get_portal_bank_balances(
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )

        assert "accounts" in result
        assert "totals" in result
        assert "owner_summary" in result
        assert abs(result["totals"]["admin_balance"] - 16412.64) < 0.01
        assert abs(result["totals"]["total_balance"] - 16412.64) < 0.01
        assert result["owner_summary"]["total_lots"] == 87

    @pytest.mark.asyncio
    async def test_building_id_filter_applied(self):
        """Verify building_id is passed to both DB queries."""
        bank_cursor = MagicMock()
        bank_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.bank_accounts.find = MagicMock(return_value=bank_cursor)
        mock_db.building_summaries.find_one = AsyncMock(return_value=None)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_bank_balances
            await get_portal_bank_balances(
                current_user={"id": "u1", "building_id": "16244", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="16244",
            )

        # Confirm building_id="16244" was used in both queries
        bank_call_filter = mock_db.bank_accounts.find.call_args[0][0]
        assert bank_call_filter["building_id"] == "16244"
        summary_call_filter = mock_db.building_summaries.find_one.call_args[0][0]
        assert summary_call_filter["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_no_bank_data_returns_empty_accounts(self):
        mock_db = self._make_mock_db([], None)
        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_bank_balances
            result = await get_portal_bank_balances(
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )
        assert result["accounts"] == []
        assert result["totals"]["total_balance"] == 0.0
        assert result["owner_summary"] is None


# ---------------------------------------------------------------------------
# 4. GET /finance/portal-actuals — financial year mapping
# ---------------------------------------------------------------------------

class TestPortalActuals:
    """portal-actuals maps levy year → financial_year correctly and scopes by building_id."""

    def _make_mock_db(self, docs: list, levy_year: str = "2026") -> MagicMock:
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value={"year": levy_year})
        fin_cursor = MagicMock()
        fin_cursor.sort = MagicMock(return_value=fin_cursor)
        fin_cursor.to_list = AsyncMock(return_value=docs)
        mock_db.strata_financials.find = MagicMock(return_value=fin_cursor)
        return mock_db

    @pytest.mark.asyncio
    async def test_levy_year_2026_maps_to_2025_2026(self):
        mock_db = self._make_mock_db([])
        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_actuals
            result = await get_portal_actuals(
                year="2026",
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )
        assert result["financial_year"] == "2025-2026"
        assert result["year"] == "2026"

    @pytest.mark.asyncio
    async def test_levy_year_2025_maps_to_2024_2025(self):
        mock_db = self._make_mock_db([])
        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_actuals
            result = await get_portal_actuals(
                year="2025",
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )
        assert result["financial_year"] == "2024-2025"

    @pytest.mark.asyncio
    async def test_categories_returned_with_totals(self):
        docs = [
            {"category": "Accounting Service Provision", "fund": "admin", "planned": 616.0, "actual": 229.02,
             "variance": 386.98},
            {"category": "Capital Works Reserve", "fund": "capital_works", "planned": 5000.0, "actual": 1200.0,
             "variance": 3800.0},
        ]
        mock_db = self._make_mock_db(docs)
        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_actuals
            result = await get_portal_actuals(
                year="2026",
                current_user={"id": "u1", "building_id": "13195", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="13195",
            )
        assert len(result["categories"]) == 2
        assert abs(result["totals"]["admin"]["planned"] - 616.0) < 0.01
        assert abs(result["totals"]["capital_works"]["planned"] - 5000.0) < 0.01

    @pytest.mark.asyncio
    async def test_building_id_scoped(self):
        mock_db = self._make_mock_db([])
        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_actuals
            await get_portal_actuals(
                year="2026",
                current_user={"id": "u1", "building_id": "16244", "permissions": {"can_view_finances": True},
                              "role": "super_admin"},
                building_id="16244",
            )
        call_filter = mock_db.strata_financials.find.call_args[0][0]
        assert call_filter["building_id"] == "16244"


# ---------------------------------------------------------------------------
# 5. GET /unit-levy-ledger/{unit} injects portal fields
# ---------------------------------------------------------------------------

class TestUnitLevyLedgerPortalInjection:
    """portal_net_balance and portal_synced_at are injected from strata_owners."""

    def _make_mock_db(
            self,
            ledger_entries: list,
            portal_doc: Optional[dict],
    ) -> MagicMock:
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=ledger_entries)
        mock_db.unit_levy_ledger.find = MagicMock(return_value=cursor)
        mock_db.strata_owners.find_one = AsyncMock(return_value=portal_doc)
        # Per-unit interest aggregation (levy_payments + financial_transactions),
        # added 2026-08-04 (commit a396a405) to the same asyncio.gather() this route
        # awaits -- an unmocked MagicMock().aggregate(...).to_list(...) is not
        # awaitable and broke every test in this class with a generic asyncio.gather
        # TypeError, unrelated to portal injection (what this class actually tests).
        interest_cursor = MagicMock()
        interest_cursor.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate = MagicMock(return_value=interest_cursor)
        mock_db.financial_transactions.aggregate = MagicMock(return_value=interest_cursor)
        # Canonical unit resolution runs before the ledger read; echo back the
        # request value so these tests stay focused on portal injection.
        #
        # Two query shapes must be handled: the resolver probes the exact
        # normalised token first (a plain string) and only falls back to the
        # candidate `$in` when that misses. The exact probe exists so a real unit
        # can never resolve to a *different* real unit whose number happens to be
        # one of its candidate variants (UA087 -> TH087); see
        # resolve_canonical_unit_number's docstring.
        def _echo_unit(query, *_a, **_k):
            wanted = query.get("unit_number")
            if isinstance(wanted, str):
                return {"unit_number": wanted}
            if isinstance(wanted, dict) and wanted.get("$in"):
                return {"unit_number": wanted["$in"][0]}
            return None

        mock_db.units.find_one = AsyncMock(side_effect=_echo_unit)
        return mock_db

    @pytest.mark.asyncio
    async def test_portal_balance_injected_when_present(self):
        entries = [{"year": "2026", "unit_number": "TH087", "net_balance": 150.0}]
        portal_doc = {
            "balance": 27.49,
            "status": "ARREARS",
            "updated_at": "2026-04-16T10:00:00Z",
        }
        mock_db = self._make_mock_db(entries, portal_doc)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_unit_levy_ledger_by_unit
            result = await get_unit_levy_ledger_by_unit(
                unit_number="TH087",
                year=None,
                current_user={"id": "u1", "unit_number": "TH087", "building_id": "13195",
                              "permissions": {"can_view_finances": True}, "role": "owner"},
                building_id="13195",
            )

        assert len(result) == 1
        assert abs(result[0]["portal_net_balance"] - 27.49) < 0.001
        assert result[0]["portal_synced_at"] == "2026-04-16T10:00:00Z"
        # Original ledger field must be unchanged
        assert abs(result[0]["net_balance"] - 150.0) < 0.001

    @pytest.mark.asyncio
    async def test_portal_balance_none_when_no_strata_owners(self):
        entries = [{"year": "2026", "unit_number": "UA001", "net_balance": 0.0}]
        mock_db = self._make_mock_db(entries, portal_doc=None)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_unit_levy_ledger_by_unit
            result = await get_unit_levy_ledger_by_unit(
                unit_number="UA001",
                year=None,
                current_user={"id": "u1", "unit_number": "UA001", "building_id": "13195",
                              "permissions": {"can_view_finances": True}, "role": "owner"},
                building_id="13195",
            )

        assert result[0]["portal_net_balance"] is None
        assert result[0]["portal_synced_at"] is None

    @pytest.mark.asyncio
    async def test_credit_portal_balance_is_negative(self):
        entries = [{"year": "2026", "unit_number": "UA003", "net_balance": -1020.26}]
        portal_doc = {"balance": -1020.26, "status": "CREDIT", "updated_at": "2026-04-16T10:00:00Z"}
        mock_db = self._make_mock_db(entries, portal_doc)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_unit_levy_ledger_by_unit
            result = await get_unit_levy_ledger_by_unit(
                unit_number="UA003",
                year=None,
                current_user={"id": "u1", "unit_number": "UA003", "building_id": "13195",
                              "permissions": {"can_view_finances": True}, "role": "owner"},
                building_id="13195",
            )

        assert result[0]["portal_net_balance"] < 0

    @pytest.mark.asyncio
    async def test_strata_owners_query_scoped_to_building(self):
        mock_db = self._make_mock_db([], None)
        with patch("routers.finance.db", mock_db):
            from routers.finance import get_unit_levy_ledger_by_unit
            await get_unit_levy_ledger_by_unit(
                unit_number="UA001",
                year=None,
                current_user={"id": "u1", "unit_number": "UA001", "building_id": "16244",
                              "permissions": {"can_view_finances": True}, "role": "super_admin"},
                building_id="16244",
            )
        call_args = mock_db.strata_owners.find_one.call_args[0][0]
        assert call_args["building_id"] == "16244"


# ---------------------------------------------------------------------------
# 6. Single source of truth — run_scraper does NOT write strata_balance to units
# ---------------------------------------------------------------------------

class TestSingleSourceOfTruth:
    """run_scraper.upsert_to_mongo does NOT write strata_balance/strata_status
    or strata_synced_at to the units collection."""

    @pytest.mark.asyncio
    async def test_upsert_to_mongo_does_not_write_strata_balance_to_units(self):
        """The $set document for units must only contain owner_name / owner_name_b."""
        mock_mdb = MagicMock()
        mock_mdb.__getitem__ = MagicMock()

        units_collection = MagicMock()
        units_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        # Motor's find() returns an async-iterable cursor; wire _async_cursor so the
        # async comprehension ``{u["unit_number"] async for u in mdb["units"].find(...)}``
        # correctly populates valid_units (otherwise it stays empty and update_one is skipped).
        units_collection.find = MagicMock(return_value=_async_cursor([{"unit_number": "TH087"}]))

        strata_owners_collection = MagicMock()
        strata_owners_collection.find_one = AsyncMock(return_value=None)
        strata_owners_collection.update_one = AsyncMock()

        strata_financials_collection = MagicMock()
        strata_financials_collection.update_one = AsyncMock()

        bank_accounts_collection = MagicMock()
        bank_accounts_collection.update_one = AsyncMock()

        building_summaries_collection = MagicMock()
        building_summaries_collection.update_one = AsyncMock()

        levy_categories_collection = MagicMock()
        levy_categories_collection.update_one = AsyncMock()

        annual_levies_collection = MagicMock()
        annual_levies_collection.find_one = AsyncMock(return_value=None)
        annual_levies_collection.insert_one = AsyncMock(return_value=MagicMock())
        annual_levies_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        unit_levy_ledger_collection = MagicMock()
        unit_levy_ledger_collection.find_one = AsyncMock(return_value=None)
        unit_levy_ledger_collection.insert_one = AsyncMock(return_value=MagicMock())
        unit_levy_ledger_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        def getitem(name):
            return {
                "units": units_collection,
                "strata_owners": strata_owners_collection,
                "strata_financials": strata_financials_collection,
                "bank_accounts": bank_accounts_collection,
                "building_summaries": building_summaries_collection,
                "levy_categories": levy_categories_collection,
                "annual_levies": annual_levies_collection,
                "unit_levy_ledger": unit_levy_ledger_collection,
            }[name]

        mock_mdb.__getitem__ = MagicMock(side_effect=getitem)

        from scripts.run_scraper import upsert_to_mongo

        owners = [{
            "lot": 87,
            "unit": 87,
            "unit_number": "TH087",
            "owner": "Avneet Rooprai & Gagneet Singh",
            "owner_name": "Avneet Rooprai",
            "owner_name_b": "Gagneet Singh",
            "balance": -27.49,
            "status": "CREDIT",
            "uoe": 161,
        }]
        await upsert_to_mongo(mock_mdb, "13195", [], owners, {
            "building_id": "13195",
            "generated_at": "2026-04-16T10:00:00Z",
            "total_lots": 1,
            "arrears_count": 0,
            "credit_count": 1,
            "clear_count": 0,
            "arrears_total": 0.0,
            "credit_total": 27.49,
            "collection_rate": 100.0,
            "risk_level": "LOW",
            "top_arrears": [],
            "budget_overruns": [],
            "admin_fund": {"total_planned": 0.0, "total_actual": 0.0},
            "capital_works_fund": {"total_planned": 0.0, "total_actual": 0.0},
        }, [], "2025-2026")

        # Verify units.update_one was called
        assert units_collection.update_one.called

        # Inspect every call to units.update_one — none should set strata_balance
        for call in units_collection.update_one.call_args_list:
            set_doc = call[0][1].get("$set", {})
            assert "strata_balance" not in set_doc, \
                "strata_balance must NOT be written to units — it lives in strata_owners"
            assert "strata_status" not in set_doc, \
                "strata_status must NOT be written to units"
            assert "strata_synced_at" not in set_doc, \
                "strata_synced_at must NOT be written to units"

    @pytest.mark.asyncio
    async def test_upsert_to_mongo_writes_owner_name_to_units(self):
        """run_scraper should still write owner_name/owner_name_b to units."""
        mock_mdb = MagicMock()
        units_collection = MagicMock()
        units_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        # Same async cursor fix as above — UA001 must appear in valid_units.
        units_collection.find = MagicMock(return_value=_async_cursor([{"unit_number": "UA001"}]))

        strata_owners_collection = MagicMock()
        strata_owners_collection.find_one = AsyncMock(return_value=None)
        strata_owners_collection.update_one = AsyncMock()
        bank_accounts_collection = MagicMock()
        bank_accounts_collection.update_one = AsyncMock()
        strata_financials_collection = MagicMock()
        strata_financials_collection.update_one = AsyncMock()
        building_summaries_collection = MagicMock()
        building_summaries_collection.update_one = AsyncMock()
        levy_categories_collection2 = MagicMock()
        levy_categories_collection2.update_one = AsyncMock()
        annual_levies_collection2 = MagicMock()
        annual_levies_collection2.find_one = AsyncMock(return_value=None)
        annual_levies_collection2.insert_one = AsyncMock(return_value=MagicMock())
        annual_levies_collection2.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        unit_levy_ledger_collection2 = MagicMock()
        unit_levy_ledger_collection2.find_one = AsyncMock(return_value=None)
        unit_levy_ledger_collection2.insert_one = AsyncMock(return_value=MagicMock())
        unit_levy_ledger_collection2.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        def getitem(name):
            return {
                "units": units_collection,
                "strata_owners": strata_owners_collection,
                "strata_financials": strata_financials_collection,
                "bank_accounts": bank_accounts_collection,
                "building_summaries": building_summaries_collection,
                "levy_categories": levy_categories_collection2,
                "annual_levies": annual_levies_collection2,
                "unit_levy_ledger": unit_levy_ledger_collection2,
            }[name]

        mock_mdb.__getitem__ = MagicMock(side_effect=getitem)

        from scripts.run_scraper import upsert_to_mongo

        owners = [{
            "lot": 1,
            "unit": 1,
            "unit_number": "UA001",
            "owner": "Ms Jane Pearce & Mr Yushan Han",
            "owner_name": "Ms Jane Pearce",
            "owner_name_b": "Mr Yushan Han",
            "balance": 1056.77,
            "status": "ARREARS",
            "uoe": 82,
        }]
        await upsert_to_mongo(mock_mdb, "13195", [], owners, {
            "building_id": "13195", "generated_at": "2026-04-16T10:00:00Z",
            "total_lots": 1, "arrears_count": 1, "credit_count": 0, "clear_count": 0,
            "arrears_total": 1056.77, "credit_total": 0.0, "collection_rate": 0.0,
            "risk_level": "HIGH", "top_arrears": [], "budget_overruns": [],
            "admin_fund": {"total_planned": 0.0, "total_actual": 0.0},
            "capital_works_fund": {"total_planned": 0.0, "total_actual": 0.0},
        }, [], "2025-2026")

        # At least one call to units.update_one with owner_name set
        any_owner_name_write = any(
            "owner_name" in call[0][1].get("$set", {})
            for call in units_collection.update_one.call_args_list
        )
        assert any_owner_name_write, "owner_name must be written to units"


class TestOwnerSyncTriggersTransferDetection:
    """2026-08-19 fix: the browser-scrape owner sync (upsert_to_mongo) previously never
    called detect_and_create_portal_owner_transfer() at all, unlike routers/strata_sync.py's
    _upsert_owners() (the Postman-push path) — a real East Gate sync silently overwrote two
    units' owner names with zero EC/strata-manager review record created. Locks in the fix:
    the detection function must be called, with the scraped combined owner name, before the
    raw display fields are written."""

    def _mock_mdb(self, *, unit_number: str, owner_combined: str):
        mock_mdb = MagicMock()
        units_collection = MagicMock()
        units_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        units_collection.find = MagicMock(return_value=_async_cursor([{"unit_number": unit_number}]))
        strata_owners_collection = MagicMock()
        strata_owners_collection.find_one = AsyncMock(return_value=None)
        strata_owners_collection.update_one = AsyncMock()
        bank_accounts_collection = MagicMock()
        bank_accounts_collection.update_one = AsyncMock()
        strata_financials_collection = MagicMock()
        strata_financials_collection.update_one = AsyncMock()
        building_summaries_collection = MagicMock()
        building_summaries_collection.update_one = AsyncMock()
        levy_categories_collection = MagicMock()
        levy_categories_collection.update_one = AsyncMock()
        annual_levies_collection = MagicMock()
        annual_levies_collection.find_one = AsyncMock(return_value=None)
        annual_levies_collection.insert_one = AsyncMock(return_value=MagicMock())
        annual_levies_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        unit_levy_ledger_collection = MagicMock()
        unit_levy_ledger_collection.find_one = AsyncMock(return_value=None)
        unit_levy_ledger_collection.insert_one = AsyncMock(return_value=MagicMock())
        unit_levy_ledger_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        def getitem(name):
            return {
                "units": units_collection,
                "strata_owners": strata_owners_collection,
                "strata_financials": strata_financials_collection,
                "bank_accounts": bank_accounts_collection,
                "building_summaries": building_summaries_collection,
                "levy_categories": levy_categories_collection,
                "annual_levies": annual_levies_collection,
                "unit_levy_ledger": unit_levy_ledger_collection,
            }[name]

        mock_mdb.__getitem__ = MagicMock(side_effect=getitem)
        return mock_mdb, units_collection, strata_owners_collection

    @pytest.mark.asyncio
    async def test_detection_is_called_before_owner_name_is_overwritten(self):
        mock_mdb, units_collection, strata_owners_collection = self._mock_mdb(
            unit_number="UA029", owner_combined="Sonja Zink",
        )

        from scripts.run_scraper import upsert_to_mongo

        owners = [{
            "lot": 29, "unit": 29, "unit_number": "UA029", "owner": "Sonja Zink",
            "owner_name": "Sonja Zink", "owner_name_b": None, "balance": 0.0,
            "status": "CLEAR", "uoe": 100,
        }]
        summary = {
            "building_id": "13195", "generated_at": "2026-08-19T00:00:00Z",
            "total_lots": 1, "arrears_count": 0, "credit_count": 0, "clear_count": 1,
            "arrears_total": 0.0, "credit_total": 0.0, "collection_rate": 100.0,
            "risk_level": "LOW", "top_arrears": [], "budget_overruns": [],
            "admin_fund": {"total_planned": 0.0, "total_actual": 0.0},
            "capital_works_fund": {"total_planned": 0.0, "total_actual": 0.0},
        }

        with patch(
            "scripts.run_scraper.detect_and_create_portal_owner_transfer",
            new_callable=AsyncMock,
        ) as mock_detect:
            mock_detect.return_value = {"created": True, "id": "transfer-123"}
            await upsert_to_mongo(mock_mdb, "13195", [], owners, summary, [], "2025-2026")

        mock_detect.assert_awaited_once()
        call_args = mock_detect.call_args
        assert call_args[0][0] is mock_mdb
        assert call_args[0][1] == "13195"
        assert call_args[0][2] == "UA029"
        assert call_args[0][3] == "Sonja Zink"
        # strata_owners write must still happen regardless (display always mirrors the
        # portal; only the FORMAL ownership change waits on EC/manager approval).
        assert strata_owners_collection.update_one.called

    @pytest.mark.asyncio
    async def test_detection_failure_does_not_block_the_raw_sync(self):
        """A transfer-detection error (e.g. a downstream DB issue) must not prevent the
        display-field sync from completing — mirrors the existing owner-sync resilience
        pattern (bad lot mapping logs a warning and continues, never raises)."""
        mock_mdb, units_collection, strata_owners_collection = self._mock_mdb(
            unit_number="UA042", owner_combined="Ms Sarah Marrapodi",
        )

        from scripts.run_scraper import upsert_to_mongo

        owners = [{
            "lot": 42, "unit": 42, "unit_number": "UA042", "owner": "Ms Sarah Marrapodi",
            "owner_name": "Ms Sarah Marrapodi", "owner_name_b": None, "balance": 0.0,
            "status": "CLEAR", "uoe": 100,
        }]
        summary = {
            "building_id": "13195", "generated_at": "2026-08-19T00:00:00Z",
            "total_lots": 1, "arrears_count": 0, "credit_count": 0, "clear_count": 1,
            "arrears_total": 0.0, "credit_total": 0.0, "collection_rate": 100.0,
            "risk_level": "LOW", "top_arrears": [], "budget_overruns": [],
            "admin_fund": {"total_planned": 0.0, "total_actual": 0.0},
            "capital_works_fund": {"total_planned": 0.0, "total_actual": 0.0},
        }

        with patch(
            "scripts.run_scraper.detect_and_create_portal_owner_transfer",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated DB error"),
        ):
            await upsert_to_mongo(mock_mdb, "13195", [], owners, summary, [], "2025-2026")

        assert strata_owners_collection.update_one.called
        assert units_collection.update_one.called


# ---------------------------------------------------------------------------
# 7. Multi-tenant isolation
# ---------------------------------------------------------------------------

class TestMultiTenantIsolation:
    """Building 13195 portal data is not visible when queried as building 16244."""

    @pytest.mark.asyncio
    async def test_bank_balances_isolated_by_building_id(self):
        """Querying as 16244 uses building_id=16244 in DB filter, not 13195."""
        bank_cursor = MagicMock()
        bank_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.bank_accounts.find = MagicMock(return_value=bank_cursor)
        mock_db.building_summaries.find_one = AsyncMock(return_value=None)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_bank_balances
            await get_portal_bank_balances(
                current_user={"id": "u2", "building_id": "16244",
                              "permissions": {"can_view_finances": True}, "role": "super_admin"},
                building_id="16244",
            )

        filter_used = mock_db.bank_accounts.find.call_args[0][0]
        assert filter_used["building_id"] == "16244"
        assert filter_used["building_id"] != "13195"

    @pytest.mark.asyncio
    async def test_portal_actuals_isolated_by_building_id(self):
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026"})
        fin_cursor = MagicMock()
        fin_cursor.sort = MagicMock(return_value=fin_cursor)
        fin_cursor.to_list = AsyncMock(return_value=[])
        mock_db.strata_financials.find = MagicMock(return_value=fin_cursor)

        with patch("routers.finance.db", mock_db):
            from routers.finance import get_portal_actuals
            await get_portal_actuals(
                year="2026",
                current_user={"id": "u2", "building_id": "16244",
                              "permissions": {"can_view_finances": True}, "role": "super_admin"},
                building_id="16244",
            )

        filter_used = mock_db.strata_financials.find.call_args[0][0]
        assert filter_used["building_id"] == "16244"


# ---------------------------------------------------------------------------
# 8. Integration smoke tests (opt-in: RUN_INTEGRATION_TESTS=1)
# ---------------------------------------------------------------------------

RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8003/api")
ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "anthony@eastgateresidences.com.au")
ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", os.environ.get("E2E_CHAIRMAN_PASSWORD", ""))


@pytest.mark.skipif(not RUN_INTEGRATION, reason="Set RUN_INTEGRATION_TESTS=1 to run live tests")
class TestIntegrationPortalBridge:
    """Live integration tests — require a running backend."""

    @pytest.fixture(scope="class")
    def token(self):
        import requests
        res = requests.post(f"{BACKEND_URL}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert res.status_code == 200
        body = res.json()
        return body.get("token") or body.get("access_token")

    def test_portal_bank_balances_endpoint_200(self, token):
        import requests
        res = requests.get(
            f"{BACKEND_URL}/finance/portal-bank-balances",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "accounts" in data
        assert "totals" in data

    def test_portal_actuals_endpoint_200(self, token):
        import requests
        res = requests.get(
            f"{BACKEND_URL}/finance/portal-actuals?year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "financial_year" in data
        assert data["financial_year"] == "2025-2026"

    def test_finance_summary_includes_portal_summary(self, token):
        import requests
        res = requests.get(
            f"{BACKEND_URL}/finance/summary?year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        # portal_summary key must always be present (may be None if scraper hasn't run)
        assert "portal_summary" in data

    def test_levy_status_includes_portal_balance(self, token):
        import requests
        res = requests.get(
            f"{BACKEND_URL}/levy-status/TH087?year=2026",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        # Key must be present; value may be None if strata_owners not populated
        assert "portal_balance" in data
        assert "portal_status" in data
        assert "portal_synced_at" in data
