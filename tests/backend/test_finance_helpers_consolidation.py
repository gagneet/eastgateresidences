"""
Tests for GAP-FIN-016 Phase 2 consolidation — finance_helpers.py shared helpers.

Covers three real defects/duplications fixed in this pass:
- get_unit_ledger_stats: was missing the mandatory is_test_data filter (CLAUDE.md rule),
  letting test-created ledger rows pollute real building-kpis/health-score aggregates.
- get_levy_fund_data: single source replacing 4 byte-identical private `_get_levy_data`
  copies previously duplicated across health_service.py, report_service.py,
  anomaly_service.py, and forecast_service.py.
- compute_combined_fund_totals: single source replacing identical inline
  admin_fund/sinking_fund summation duplicated in health_service.py and report_service.py.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.finance_helpers import compute_combined_fund_totals, get_levy_fund_data, get_unit_ledger_stats


# ─────────────────────────────────────────────────────────────────────────────
# get_unit_ledger_stats — is_test_data filter regression
# ─────────────────────────────────────────────────────────────────────────────

class TestLedgerStatsExcludesTestData:
    @pytest.mark.asyncio
    async def test_aggregate_match_stage_excludes_test_data(self):
        """The $match stage sent to Mongo must exclude is_test_data=True rows.

        Regression guard for the gap found in GAP-FIN-016 Phase 2: this
        aggregation was the only ledger-stats pipeline in the codebase without
        the filter finance.py's parallel inline aggregation already had.
        """
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])

        with patch("utils.finance_helpers.db", mock_db):
            mock_db.annual_levies.find_one = AsyncMock(return_value=None)
            await get_unit_ledger_stats("2026", "13195")

        pipeline = mock_db.unit_levy_ledger.aggregate.call_args[0][0]
        match_stage = pipeline[0]["$match"]
        assert match_stage.get("is_test_data") == {"$ne": True}, (
            f"get_unit_ledger_stats aggregation must exclude is_test_data rows, got match={match_stage}"
        )
        assert match_stage["year"] == "2026"
        assert match_stage["building_id"] == "13195"


# ─────────────────────────────────────────────────────────────────────────────
# get_levy_fund_data — single source for the 4 previously-duplicated _get_levy_data copies
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLevyFundData:
    @pytest.mark.asyncio
    async def test_prefers_new_schema_shape(self):
        new_shape = {"year": "2026", "admin_fund": {"total_income": 100.0}}
        with patch(
            "services.financial_service.get_legacy_annual_levy_shape",
            AsyncMock(return_value=new_shape),
        ):
            result = await get_levy_fund_data("2026", "13195")
        assert result == new_shape

    @pytest.mark.asyncio
    async def test_falls_back_to_raw_annual_levies_when_new_shape_empty(self):
        mock_db = MagicMock()
        legacy_doc = {"year": "2026", "admin_fund": {"total_income": 50.0}}
        mock_db.annual_levies.find_one = AsyncMock(return_value=legacy_doc)

        with patch(
            "services.financial_service.get_legacy_annual_levy_shape",
            AsyncMock(return_value=None),
        ), patch("utils.finance_helpers.db", mock_db):
            result = await get_levy_fund_data("2026", "13195")

        assert result == legacy_doc
        mock_db.annual_levies.find_one.assert_awaited_once_with(
            {"year": "2026", "building_id": "13195"}, {"_id": 0}
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_found(self):
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch(
            "services.financial_service.get_legacy_annual_levy_shape",
            AsyncMock(return_value=None),
        ), patch("utils.finance_helpers.db", mock_db):
            result = await get_levy_fund_data("2099", "13195")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# compute_combined_fund_totals — single source for the health_service/report_service duplication
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeCombinedFundTotals:
    def test_sums_admin_and_sinking(self):
        levy = {
            "admin_fund": {"total_income": 100.0, "total_expenses": 40.0, "closing_balance": 60.0},
            "sinking_fund": {"total_income": 200.0, "total_expenses": 30.0, "closing_balance": 170.0},
        }
        result = compute_combined_fund_totals(levy)
        assert result == {
            "total_income": 300.0,
            "total_expenses": 70.0,
            "closing_balance": 230.0,
            "surplus": 230.0,
        }

    def test_none_levy_returns_zeros(self):
        result = compute_combined_fund_totals(None)
        assert result == {
            "total_income": 0,
            "total_expenses": 0,
            "closing_balance": 0,
            "surplus": 0,
        }

    def test_missing_fund_keys_default_to_zero(self):
        """A levy doc with only one fund populated must not KeyError."""
        levy = {"admin_fund": {"total_income": 100.0}}
        result = compute_combined_fund_totals(levy)
        assert result["total_income"] == 100.0
        assert result["total_expenses"] == 0
        assert result["closing_balance"] == 0

    def test_none_field_values_treated_as_zero(self):
        """Mongo docs can store explicit nulls, not just missing keys."""
        levy = {
            "admin_fund": {"total_income": None, "total_expenses": 10.0, "closing_balance": None},
            "sinking_fund": {"total_income": 50.0, "total_expenses": None, "closing_balance": 20.0},
        }
        result = compute_combined_fund_totals(levy)
        assert result["total_income"] == 50.0
        assert result["total_expenses"] == 10.0
        assert result["closing_balance"] == 20.0


# ---------------------------------------------------------------------------
# normalise_financial_year (2026-08-28)
# ---------------------------------------------------------------------------

class TestNormaliseFinancialYear:
    """Canonical closing-year form for any financial-year label.

    This exists because the same `split("-")[1]` logic was inlined in three places
    while `staging_strata_web_snapshots` stored whatever label its caller passed.
    East Gate holds "2025", "2026" AND "2026-2027" side by side, and the balance-delta
    inference paired snapshots on an exact string match — so two snapshots of the same
    year under different labels never paired, and a whole scraper run produced zero
    payment candidates with only a warning string to show for it.
    """

    @pytest.mark.parametrize("given,expected", [
        ("2026", "2026"),
        ("2025-2026", "2026"),          # hyphenated range names its CLOSING year
        ("2026-2027", "2027"),
        ("FY2026", "2026"),
        ("2026-27", "2027"),            # two-digit tail borrows the head's century
        ("FY2025-26", "2026"),
        (" 2026 ", "2026"),
        (2026, "2026"),                 # non-string input
    ])
    def test_normalises_to_closing_year(self, given, expected):
        from utils.finance_helpers import normalise_financial_year
        assert normalise_financial_year(given) == expected

    @pytest.mark.parametrize("given", ["", None])
    def test_empty_is_empty(self, given):
        from utils.finance_helpers import normalise_financial_year
        assert normalise_financial_year(given) == ""

    def test_unparseable_returns_itself_rather_than_raising(self):
        """Callers use this to MATCH, not to validate. A label this cannot parse must
        still equal itself so two identically-labelled rows continue to pair."""
        from utils.finance_helpers import normalise_financial_year
        assert normalise_financial_year("garbage") == "garbage"
        assert normalise_financial_year("garbage") == normalise_financial_year("garbage")

    def test_distinct_years_do_not_collide(self):
        """Over-matching would be worse than under-matching: pairing FY2026 against
        FY2027 would compute a balance delta across a levy year boundary."""
        from utils.finance_helpers import normalise_financial_year as n
        assert n("2025-2026") != n("2026-2027")
        assert n("2026") != n("2027")
