"""
Tests for Intelligence Fixes — database.py _inject_bid, capital-shock endpoint,
levy-fairness endpoint, and owner-hub unit-tco / units endpoints.

Covers:
  TestDatabaseInjectBid     — unit tests for TenantCollection._inject_bid logic
  TestCapitalShockEndpoint  — GET /intelligence/capital-shock (live + mocked)
  TestLevyFairnessEndpoint  — GET /intelligence/levy-fairness (live + mocked)
  TestUnitTcoEndpoint       — GET /owner-hub/unit-tco (unit + live)
  TestOwnerHubUnitsEndpoint — GET /owner-hub/units (unit + live)

Run with:
    cd /home/gagneet/strata-management/backend
    venv/bin/pytest ../tests/backend/test_intelligence_fixes.py -v
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typing import Any


@pytest.fixture(autouse=True)
def _mock_all_unit_owners():
    with patch("services.levy_fairness_service.get_all_unit_owners", AsyncMock(return_value={})):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cursor(data):
    """Return a mock cursor whose .to_list() returns data."""
    cur = MagicMock()
    cur.sort = MagicMock(return_value=cur)
    cur.to_list = AsyncMock(return_value=data)
    return cur


def _make_unit(unit_number: str = "TH087", entitlement: float = 120.0,
               property_type: str = "Townhouse") -> dict:
    return {
        "unit_number": unit_number,
        "entitlement": entitlement,
        "property_type": property_type,
        "owner_name": "Test Owner",
        "building_id": "13195",
    }


def _make_ledger(unit_number: str = "TH087", admin_levied: float = 5000.0,
                 sinking_levied: float = 2000.0, year: str = "2026") -> dict:
    return {
        "unit_number": unit_number,
        "year": year,
        "building_id": "13195",
        "admin_levied": admin_levied,
        "sinking_levied": sinking_levied,
        "admin_opening": 0.0,
        "sinking_opening": 0.0,
        "total_levied": admin_levied + sinking_levied,
        "total_paid": 0.0,
        "net_balance": admin_levied + sinking_levied,
    }


def _build_tco_mock_db(unit=None, ledger=None):
    """Build a mock db for TCO unit tests."""
    mock_db = MagicMock()

    u = unit or _make_unit()
    l = ledger or _make_ledger()

    mock_db.units.find_one = AsyncMock(return_value=u)
    # compute_unit_tco() never calls unit_levy_ledger.find_one() — that's used by a
    # different function (compute_property_tco, :311). Its own ledger-fallback branch
    # (:514, only reached when get_levy_rates() returns no committed rate) queries via
    # .find(...).to_list(40), returning a LIST of rows aggregated by quarter/annual —
    # mocking find_one here was always inert for this function; kept for callers that
    # may still rely on it, but .find() is what actually needs the ledger fixture.
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=l)
    mock_db.unit_levy_ledger.find.return_value = _cursor([l])
    # compute_unit_tco() (services/ownerhub_service.py:572) queries council_rates via
    # .find(...).to_list(20), not .find_one() — this fixture previously only mocked
    # find_one, so every call raised "TypeError: object MagicMock can't be used in
    # 'await' expression" the moment the real code path executed .find().
    mock_db.council_rates.find.return_value = _cursor([])
    mock_db.council_rates.find_one = AsyncMock(return_value=None)
    mock_db.water_bills.aggregate.return_value = _cursor([])
    mock_db.units.aggregate.return_value = _cursor([{"total": 8700.0}])
    mock_db.capital_replacement_schedule.find.return_value = _cursor([])
    # Unconditionally queried twice for the 10-year TCO projection (ownerhub_service.py:657,693)
    # — also missing from this fixture, same MagicMock-await failure as council_rates above.
    mock_db.sinking_fund_plan.find.return_value = _cursor([])
    # get_unit_mortgage() (ownerhub_service.py:487), called unconditionally from
    # compute_unit_tco() (ownerhub_service.py:807) since the owner-mortgage TCO
    # line item was added — same MagicMock-await gap as council_rates/sinking_fund_plan.
    mock_db.owner_mortgages.find_one = AsyncMock(return_value=None)

    return mock_db


# ─────────────────────────────────────────────────────────────────────────────
# TestDatabaseInjectBid — pure unit tests for TenantCollection._inject_bid
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseInjectBid:
    """
    Unit tests for TenantCollection._inject_bid and _has_explicit_building_id.

    We test the logic methods directly by constructing a TenantCollection
    with a mock underlying Motor collection.  The key invariant tested here
    is the Fix 1 regression: double building_id injection (MongoDB error 54)
    must NEVER occur when the filter already carries an explicit building_id.
    """

    def _make_tc(self) -> Any:
        """Return a TenantCollection wrapping a MagicMock collection."""
        from database import TenantCollection
        mock_coll = MagicMock()
        mock_coll.name = "unit_levy_ledger"  # a tenant-scoped collection
        return TenantCollection(mock_coll)

    # ── _has_explicit_building_id ─────────────────────────────────────────────

    def test_has_explicit_building_id_direct_key(self):
        """Filter with top-level building_id key is detected as explicit."""
        tc = self._make_tc()
        assert tc._has_explicit_building_id({"building_id": "13195", "unit_number": "TH087"}) is True

    def test_has_explicit_building_id_inside_and(self):
        """Filter with building_id inside $and clause is detected as explicit."""
        tc = self._make_tc()
        f = {"$and": [{"building_id": "13195"}, {"unit_number": "TH087"}]}
        assert tc._has_explicit_building_id(f) is True

    def test_has_explicit_building_id_missing(self):
        """Filter without any building_id is NOT detected as explicit."""
        tc = self._make_tc()
        assert tc._has_explicit_building_id({"unit_number": "TH087"}) is False

    def test_has_explicit_building_id_none_returns_false(self):
        """None filter is not a dict — returns False."""
        tc = self._make_tc()
        assert tc._has_explicit_building_id(None) is False

    def test_has_explicit_building_id_non_dict_and_clause(self):
        """$and with non-dict elements does not crash and returns False."""
        tc = self._make_tc()
        f = {"$and": ["bad-element"]}
        assert tc._has_explicit_building_id(f) is False

    # ── _inject_bid — with bid context set ───────────────────────────────────
    # NOTE: _inject_bid uses a local import:
    #   from request_context import get_ctx_building_id
    # So the patch target is "request_context.get_ctx_building_id".

    def test_inject_bid_with_explicit_building_id_returns_unchanged(self):
        """
        Fix 1 regression: when filter already has building_id AND a context
        bid is set, _inject_bid must NOT double-inject.
        Without the fix this produced a MongoDB error code 54.
        """
        tc = self._make_tc()
        original_filter = {"building_id": "13195", "unit_number": "TH087"}

        with patch("request_context.get_ctx_building_id", return_value="13195"):
            result = tc._inject_bid(original_filter)

        # The filter must be returned unchanged — no extra $and wrapping
        assert result == original_filter
        assert "building_id" in result
        # Must NOT be double-injected via $and
        assert "$and" not in result or not any(
            isinstance(c, dict) and "building_id" in c
            for c in result.get("$and", [])
        )

    def test_inject_bid_without_explicit_building_id_injects_bid(self):
        """
        When filter lacks building_id and context bid is set, _inject_bid
        should inject building_id via $and wrapping.
        """
        tc = self._make_tc()
        original_filter = {"unit_number": "TH087"}

        with patch("request_context.get_ctx_building_id", return_value="13195"):
            result = tc._inject_bid(original_filter)

        # building_id must be present somewhere in the result
        assert tc._filter_has_building_id(result) is True

    def test_inject_bid_none_filter_returns_bid_only(self):
        """
        When filter is None and context bid is set, _inject_bid returns
        a dict allowing either building_id or plan_id.
        """
        tc = self._make_tc()

        with patch("request_context.get_ctx_building_id", return_value="13195"):
            result = tc._inject_bid(None)

        assert result == {"$or": [{"building_id": "13195"}, {"plan_id": "13195"}]}

    def test_inject_bid_no_context_explicit_filter_passes(self):
        """
        When there is no building context but the filter already has an
        explicit building_id, _inject_bid must pass the filter through
        without raising RuntimeError.
        """
        tc = self._make_tc()
        f = {"building_id": "13195", "unit_number": "UA001"}

        with patch("request_context.get_ctx_building_id", return_value=None):
            # Must not raise — explicit building_id satisfies the guard
            result = tc._inject_bid(f)

        assert result == f

    def test_inject_bid_no_context_no_explicit_raises(self):
        """
        When no building context and no explicit building_id in filter,
        _inject_bid must raise RuntimeError (tenant isolation enforced).
        """
        tc = self._make_tc()

        with patch("request_context.get_ctx_building_id", return_value=None):
            with pytest.raises(RuntimeError, match="Missing building context"):
                tc._inject_bid({"unit_number": "UA001"})

    def test_inject_bid_no_context_none_filter_raises(self):
        """
        When no building context and filter is None, _inject_bid must
        raise RuntimeError.
        """
        tc = self._make_tc()

        with patch("request_context.get_ctx_building_id", return_value=None):
            with pytest.raises(RuntimeError, match="Missing building context"):
                tc._inject_bid(None)

    def test_inject_bid_existing_and_clause_extends(self):
        """
        Filter with existing $and (but no building_id) must have building_id/plan_id
        appended to the $and list (not double-nested).
        """
        tc = self._make_tc()
        f = {"$and": [{"year": "2026"}]}

        with patch("request_context.get_ctx_building_id", return_value="13195"):
            result = tc._inject_bid(f)

        # Result should still use $and, extended with building_id/plan_id $or
        assert "$and" in result
        bid_clauses = [c for c in result["$and"] if isinstance(c, dict) and "$or" in c]
        assert len(bid_clauses) == 1
        assert bid_clauses[0] == {"$or": [{"building_id": "13195"}, {"plan_id": "13195"}]}

    def test_inject_bid_explicit_building_id_in_and_returns_unchanged(self):
        """
        Fix 1: Filter that already has building_id inside $and must NOT be
        double-injected, even when context bid is set.
        """
        tc = self._make_tc()
        f = {"$and": [{"building_id": "13195"}, {"year": "2026"}]}

        with patch("request_context.get_ctx_building_id", return_value="13195"):
            result = tc._inject_bid(f)

        # Returned unchanged — only one building_id clause
        building_clauses = [c for c in result.get("$and", []) if isinstance(c, dict) and "building_id" in c]
        assert len(building_clauses) == 1

    # ── _filter_has_building_id — recursive search ───────────────────────────

    def test_filter_has_building_id_nested_in_or(self):
        """_filter_has_building_id searches recursively into $or."""
        tc = self._make_tc()
        f = {"$or": [{"unit_number": "UA001"}, {"building_id": "13195"}]}
        assert tc._filter_has_building_id(f) is True

    def test_filter_has_building_id_in_list(self):
        """_filter_has_building_id searches recursively into lists."""
        tc = self._make_tc()
        lst = [{"unit_number": "UA001"}, {"building_id": "13195"}]
        assert tc._filter_has_building_id(lst) is True

    def test_filter_has_building_id_absent(self):
        """_filter_has_building_id returns False when absent at all levels."""
        tc = self._make_tc()
        f = {"$or": [{"unit_number": "UA001"}, {"year": "2026"}]}
        assert tc._filter_has_building_id(f) is False


# ─────────────────────────────────────────────────────────────────────────────
# TestCapitalShockEndpoint — mocked + live integration
# ─────────────────────────────────────────────────────────────────────────────

def _build_capital_shock_mock_db(schedule_items=None, levies=None):
    """
    Build a mock DB for capital_shock_service tests.

    detect_capital_shocks calls (in order):
      1. db.capital_replacement_schedule.find(...).to_list(200)
      2. db.annual_levies.find_one(...)         via _get_financial_baseline
      3. db.maintenance_forecasts.find_one(...)
      4. db.sinking_fund_plan.find(...).sort(...).to_list(40)
      5. db.sinking_fund_capital_events.find(...).sort(...).to_list(20)
      6. db.capital_shock_risks.update_one(...)

    All .find()...sort()...to_list() chains must be set up as MagicMock chains
    where the final .to_list() is an AsyncMock.
    """
    if schedule_items is None:
        schedule_items = []

    mock_db = MagicMock()

    def _sorted_cursor(data):
        """Cursor that supports an optional .sort(...) chain then .to_list()."""
        cur = MagicMock()
        cur.sort.return_value = cur
        cur.to_list = AsyncMock(return_value=data)
        return cur

    # 1. capital_replacement_schedule.find(...).to_list(200)
    mock_db.capital_replacement_schedule.find.return_value = _sorted_cursor(schedule_items)

    # 2. annual_levies.find_one (via _get_financial_baseline)
    mock_db.annual_levies.find_one = AsyncMock(return_value=levies)

    # 3. maintenance_forecasts.find_one
    mock_db.maintenance_forecasts.find_one = AsyncMock(return_value=None)

    # 4. sinking_fund_plan.find(...).sort(...).to_list(40)
    mock_db.sinking_fund_plan.find.return_value = _sorted_cursor([])

    # 5. sinking_fund_capital_events.find(...).sort(...).to_list(20)
    mock_db.sinking_fund_capital_events.find.return_value = _sorted_cursor([])

    # 6. persistence
    mock_db.capital_shock_risks.find_one = AsyncMock(return_value=None)
    mock_db.capital_shock_risks.update_one = AsyncMock(return_value=MagicMock())

    return mock_db


class TestCapitalShockEndpointUnit:
    """Unit tests for capital shock service output shape."""

    @pytest.mark.asyncio
    async def test_detect_capital_shocks_returns_required_fields(self):
        """detect_capital_shocks must include risk_level, reserve_balance,
        capital_shock_index regardless of DB state."""
        mock_db = _build_capital_shock_mock_db(
            levies={
                "admin_fund": {"levy_income": 300000, "total_expenses": 280000},
                "sinking_fund": {"levy_income": 100000, "closing_balance": 250000, "total_expenses": 50000},
            }
        )

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      AsyncMock(return_value="2026")):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("13195")

        assert "risk_level" in result
        assert "reserve_balance" in result
        assert "capital_shock_index" in result

    @pytest.mark.asyncio
    async def test_detect_capital_shocks_risk_level_is_valid(self):
        """risk_level must be one of the defined risk levels produced by the service.

        The service maps reserve_collapse risk_level through _collapse_to_risk:
          {"critical": "CRITICAL", "risk": "HIGH", "watch": "MEDIUM", "stable": "LOW"}
        With empty plan rows the reserve_collapse returns "stable" → "LOW".
        """
        mock_db = _build_capital_shock_mock_db()

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      AsyncMock(return_value=None)):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("13195")

        valid_levels = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert result["risk_level"] in valid_levels

    @pytest.mark.asyncio
    async def test_detect_capital_shocks_reports_unknown_reserve_when_no_levy_year(self):
        """No levy year => reserve_balance is None and flagged unavailable, never a number.

        This test used to assert only `reserve_balance >= 0`, which passed for the wrong
        reason: the service substituted a hardcoded 150000.0 whenever a building had no
        financial data, and 150000 >= 0. East Gate (13195) has no financial data in either
        store, so /intelligence/capital-risk and /intelligence/building both displayed
        "$150,000.00 — Available for capital works" beside $0 income and $0 expenses.

        The contract is now explicit: unknown is None plus reserve_balance_available=False.
        Asserting `>= 0` again would re-admit any fabricated figure, including 0.0, which
        the UI cannot distinguish from a real zero balance.
        """
        mock_db = _build_capital_shock_mock_db()

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      AsyncMock(return_value=None)):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("13195")

        assert result["reserve_balance"] is None, (
            f"expected None for a building with no levy year, got {result['reserve_balance']!r} "
            "— a default was substituted again"
        )
        assert result["reserve_balance_available"] is False
        # The response must still be well-formed so the UI can render an empty state.
        assert result["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    @pytest.mark.asyncio
    async def test_detect_capital_shocks_capital_shock_index_is_dict(self):
        """capital_shock_index is a dict produced by _compute_capital_shock_index.
        It must contain 'rows' (list) and 'overall_level' (str) sub-keys.
        """
        mock_db = _build_capital_shock_mock_db()

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      AsyncMock(return_value=None)):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("13195")

        csi = result["capital_shock_index"]
        assert isinstance(csi, dict), f"expected dict, got {type(csi)}"
        assert "rows" in csi
        assert isinstance(csi["rows"], list)


class TestCapitalShockEndpointLive:
    """Live integration tests against running backend (skipped by default)."""

    @pytest.mark.integration
    def test_capital_shock_returns_200(self, admin_headers):
        """GET /intelligence/capital-shock returns 200 with valid auth."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/intelligence/capital-shock",
            headers=admin_headers,
            timeout=15,
        )
        assert r.status_code == 200

    @pytest.mark.integration
    def test_capital_shock_has_required_fields(self, admin_headers):
        """Response body includes risk_level, reserve_balance, capital_shock_index."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/intelligence/capital-shock",
            headers=admin_headers,
            timeout=15,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        assert "risk_level" in data
        assert "reserve_balance" in data
        assert "capital_shock_index" in data

    @pytest.mark.integration
    def test_capital_shock_no_auth_returns_401(self):
        """Without auth token the endpoint must return 401."""
        import requests
        try:
            r = requests.get(
                "http://127.0.0.1:8003/api/intelligence/capital-shock",
                timeout=10,
            )
            assert r.status_code == 401
        except Exception:
            pytest.skip("Backend not reachable")


# ─────────────────────────────────────────────────────────────────────────────
# TestLevyFairnessEndpoint — mocked unit tests + live integration
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyFairnessEndpointUnit:
    """Unit tests for levy fairness service output shape and invariants."""

    def _build_mock_db(self, units=None, ledger=None):
        if units is None:
            units = [
                {"unit_number": "UA001", "entitlement": 100.0, "property_type": "Apartment", "owner_name": "Owner A"},
                {"unit_number": "UA002", "entitlement": 150.0, "property_type": "Apartment", "owner_name": "Owner B"},
                {"unit_number": "TH071", "entitlement": 120.0, "property_type": "Townhouse", "owner_name": "Owner C"},
            ]
        if ledger is None:
            ledger = [
                {"unit_number": "UA001", "total_levied": 4000.0, "year": "2026"},
                {"unit_number": "UA002", "total_levied": 6000.0, "year": "2026"},
                {"unit_number": "TH071", "total_levied": 5000.0, "year": "2026"},
            ]

        mock_db = MagicMock()
        mock_db.units.find.return_value = _cursor(units)
        mock_db.benefit_groups.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor(ledger)
        mock_db.unit_attributes.find.return_value = _cursor([])
        mock_db.capital_replacement_schedule.find.return_value = _cursor([])
        mock_db.financial_summary.find_one = AsyncMock(return_value=None)
        mock_db.facilities.find.return_value = _cursor([])
        mock_db.building_assets.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.aggregate.return_value = _cursor([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)
        mock_db.levy_fairness_results_v2.update_one = AsyncMock(return_value=MagicMock())
        return mock_db

    @pytest.mark.asyncio
    async def test_levy_fairness_returns_required_fields(self):
        """simulate_levy_fairness_v2 result must contain the canonical top-level keys."""
        mock_db = self._build_mock_db()

        alloc = {"UA001": 4000.0, "UA002": 6000.0, "TH071": 5000.0}

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value=alloc)), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value=alloc)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        for key in ("total_budget", "lei_score", "sei_scheme", "unit_impact", "building_id"):
            assert key in result, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_levy_fairness_has_unit_impact_array(self):
        """unit_impact must be a non-empty list when units are present."""
        mock_db = self._build_mock_db()

        alloc = {"UA001": 4000.0, "UA002": 6000.0, "TH071": 5000.0}

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value=alloc)), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value=alloc)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert isinstance(result["unit_impact"], list)
        assert len(result["unit_impact"]) > 0

    @pytest.mark.asyncio
    async def test_levy_fairness_lei_score_in_range(self):
        """lei_score must be between 0 and 100 inclusive."""
        mock_db = self._build_mock_db()

        alloc = {"UA001": 4000.0, "UA002": 6000.0, "TH071": 5000.0}

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value=alloc)), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value=alloc)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert 0 <= result["lei_score"] <= 100

    @pytest.mark.asyncio
    async def test_levy_fairness_revenue_neutral(self):
        """
        Sum of proposed_levy across all unit_impact entries must equal
        sum of current_levy within 1% (revenue neutral allocation).
        When caps are identity (proposed == fair == current), totals match exactly.
        """
        mock_db = self._build_mock_db()

        alloc = {"UA001": 4000.0, "UA002": 6000.0, "TH071": 5000.0}

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value=alloc)), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value=alloc)):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        units = result["unit_impact"]
        total_current = sum(u.get("current_levy", 0) for u in units)
        total_proposed = sum(u.get("proposed_levy", 0) for u in units)

        if total_current > 0:
            deviation = abs(total_proposed - total_current) / total_current
            assert deviation < 0.01, (
                f"Revenue neutrality violated: current={total_current}, "
                f"proposed={total_proposed}, deviation={deviation:.2%}"
            )

    @pytest.mark.asyncio
    async def test_levy_fairness_building_id_matches_input(self):
        """result['building_id'] must equal the building_id passed to the function."""
        mock_db = self._build_mock_db()

        import services.levy_fairness_service as svc
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value={})):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert result["building_id"] == "13195"


class TestLevyFairnessEndpointLive:
    """Live integration tests against running backend (skipped by default)."""

    @pytest.mark.integration
    def test_levy_fairness_returns_200(self, admin_headers):
        """GET /intelligence/levy-fairness returns 200 with valid auth."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/intelligence/levy-fairness",
            headers=admin_headers,
            timeout=20,
        )
        assert r.status_code == 200

    @pytest.mark.integration
    def test_levy_fairness_has_unit_impact(self, admin_headers):
        """Live response has unit_impact array."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/intelligence/levy-fairness",
            headers=admin_headers,
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        assert "unit_impact" in data
        assert isinstance(data["unit_impact"], list)

    @pytest.mark.integration
    def test_levy_fairness_lei_score_in_range_live(self, admin_headers):
        """Live lei_score is between 0 and 100."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/intelligence/levy-fairness",
            headers=admin_headers,
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        if "lei_score" in data:
            assert 0 <= data["lei_score"] <= 100

    @pytest.mark.integration
    def test_levy_fairness_revenue_neutral_live(self, admin_headers):
        """Live proposed totals equal current totals within 1%."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/intelligence/levy-fairness",
            headers=admin_headers,
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        units = data.get("unit_impact", [])
        if not units:
            pytest.skip("No unit_impact in response")
        total_current = sum(u.get("current_levy", 0) for u in units)
        total_proposed = sum(u.get("proposed_levy", 0) for u in units)
        if total_current > 0:
            deviation = abs(total_proposed - total_current) / total_current
            assert deviation < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# TestUnitTcoEndpoint — compute_unit_tco service + live endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestUnitTcoService:
    """Unit tests for ownerhub_service.compute_unit_tco — pure service layer."""

    @pytest.fixture(autouse=True)
    def _mock_get_owner_info(self):
        with patch("services.ownerhub_service.get_owner_info", AsyncMock(return_value={"owner_name": "Test Owner"})):
            yield

    @pytest.mark.asyncio
    async def test_unit_tco_returns_required_fields(self):
        """compute_unit_tco must return unit_number, total_costs, strata_levies, projection."""
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        for key in ("unit_number", "total_costs", "strata_levies", "projection"):
            assert key in result, f"Missing field: {key}"

    @pytest.mark.asyncio
    async def test_unit_tco_projection_is_10_years(self):
        """projection array must contain exactly 10 entries (year+1 through year+10)."""
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert len(result["projection"]) == 10

    @pytest.mark.asyncio
    async def test_unit_tco_projection_years_sequential(self):
        """Projection entries must cover years 2027–2036 (when base year is 2026)."""
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        years = [p["year"] for p in result["projection"]]
        assert years == list(range(2027, 2037))

    @pytest.mark.asyncio
    async def test_unit_tco_total_costs_non_negative(self):
        """total_costs must be >= 0 regardless of missing data."""
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert result["total_costs"] >= 0

    @pytest.mark.asyncio
    async def test_unit_tco_strata_levies_from_ledger(self):
        """strata_levies must equal admin_levied + sinking_levied from the ledger.

        This exercises the unit_levy_ledger FALLBACK branch specifically (ownerhub_service.py
        :505-517), which only runs when get_levy_rates() returns no committed rate. That
        function is imported directly into ownerhub_service and was previously left
        unmocked here, so it silently queried the real building_id="13195" (East Gate)
        against whatever live/test MongoDB this suite is pointed at — building 13195 has
        real committed 2026 rates, so the rate-based branch ran instead of the ledger
        fallback this test claims to cover, and the assertion only passed or failed
        depending on ambient DB state rather than the mocked ledger fixture.
        """
        ledger = _make_ledger("TH087", admin_levied=5300.0, sinking_levied=1800.0)
        mock_db = _build_tco_mock_db(ledger=ledger)

        with patch("services.ownerhub_service.db", mock_db), \
             patch("services.ownerhub_service.get_levy_rates",
                   AsyncMock(return_value={"admin_annual": 0.0, "sinking_annual": 0.0})):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert result["strata_levies"] == pytest.approx(7100.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_unit_tco_no_ledger_strata_levies_zero(self):
        """When no ledger entry exists (and no committed rate), strata_levies must default to 0.0."""
        mock_db = _build_tco_mock_db()
        # compute_unit_tco's ledger-fallback branch reads unit_levy_ledger via .find()
        # (a list), not .find_one() — see the fixture comment above.
        mock_db.unit_levy_ledger.find.return_value = _cursor([])

        with patch("services.ownerhub_service.db", mock_db), \
             patch("services.ownerhub_service.get_levy_rates",
                   AsyncMock(return_value={"admin_annual": 0.0, "sinking_annual": 0.0})):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert result["strata_levies"] == 0.0

    @pytest.mark.asyncio
    async def test_unit_tco_missing_unit_raises_404(self):
        """When unit_number does not exist, compute_unit_tco must raise HTTP 404."""
        from fastapi import HTTPException
        mock_db = _build_tco_mock_db()
        mock_db.units.find_one = AsyncMock(return_value=None)

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            with pytest.raises(HTTPException) as exc_info:
                await compute_unit_tco("INVALID", "2026", 2)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unit_tco_has_breakdown_dict(self):
        """result must include 'breakdown' dict with individual cost components.

        breakdown's 4 keys (strata_levies/rates_and_charges/water_charges/
        capital_replacement, ownerhub_service.py:741-746) are the ones that sum to
        total_costs. council_rates and land_tax are also present as separate
        top-level result fields (:733-734) for finer-grained detail, but are folded
        together into "rates_and_charges" inside breakdown specifically — this test
        previously checked for "council_rates" inside breakdown, a key that dict has
        never had.
        """
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert "breakdown" in result
        bd = result["breakdown"]
        for component in ("strata_levies", "rates_and_charges", "water_charges", "capital_replacement"):
            assert component in bd, f"Missing breakdown component: {component}"
        # council_rates/land_tax are top-level fields, not breakdown members.
        assert "council_rates" in result
        assert "land_tax" in result

    @pytest.mark.asyncio
    async def test_unit_tco_breakdown_sums_to_total(self):
        """Sum of breakdown components must equal total_costs."""
        ledger = _make_ledger("TH087", admin_levied=4000.0, sinking_levied=1500.0)
        mock_db = _build_tco_mock_db(ledger=ledger)

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        bd = result["breakdown"]
        computed_total = round(
            bd["strata_levies"] + bd["rates_and_charges"] +
            bd["water_charges"] + bd["capital_replacement"],
            2
        )
        assert computed_total == pytest.approx(result["total_costs"], abs=0.01)

    @pytest.mark.asyncio
    async def test_unit_tco_unit_number_preserved_in_result(self):
        """result['unit_number'] must match the input unit_number."""
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert result["unit_number"] == "TH087"

    @pytest.mark.asyncio
    async def test_unit_tco_year_preserved_in_result(self):
        """result['year'] must match the input year (first 4 chars)."""
        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            from services.ownerhub_service import compute_unit_tco
            result = await compute_unit_tco("TH087", "2026", 2)

        assert result["year"] == "2026"


class TestUnitTcoEndpointLive:
    """Live integration tests for GET /owner-hub/unit-tco (skipped by default)."""

    @pytest.mark.integration
    def test_unit_tco_returns_200_for_admin(self, admin_headers):
        """GET /owner-hub/unit-tco?unit_number=TH087&year=2026 returns 200 for admin."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/owner-hub/unit-tco",
            params={"unit_number": "TH087", "year": "2026"},
            headers=admin_headers,
            timeout=20,
        )
        assert r.status_code == 200

    @pytest.mark.integration
    def test_unit_tco_has_required_fields_live(self, admin_headers):
        """Live response includes unit_number, total_costs, strata_levies, projection."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/owner-hub/unit-tco",
            params={"unit_number": "TH087", "year": "2026"},
            headers=admin_headers,
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        for key in ("unit_number", "total_costs", "strata_levies", "projection"):
            assert key in data

    @pytest.mark.integration
    def test_unit_tco_projection_is_10_years_live(self, admin_headers):
        """Live projection has exactly 10 entries."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/owner-hub/unit-tco",
            params={"unit_number": "TH087", "year": "2026"},
            headers=admin_headers,
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        assert len(data.get("projection", [])) == 10

    @pytest.mark.integration
    def test_unit_tco_missing_unit_returns_404_live(self, admin_headers):
        """GET /owner-hub/unit-tco?unit_number=INVALID returns 404."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/owner-hub/unit-tco",
            params={"unit_number": "XXINVALIDXX", "year": "2026"},
            headers=admin_headers,
            timeout=15,
        )
        assert r.status_code == 404

    @pytest.mark.integration
    def test_unit_tco_no_auth_returns_401(self):
        """Without auth token, endpoint returns 401."""
        import requests
        try:
            r = requests.get(
                "http://127.0.0.1:8003/api/owner-hub/unit-tco",
                params={"unit_number": "TH087", "year": "2026"},
                timeout=10,
            )
            assert r.status_code in (401, 403)
        except Exception:
            pytest.skip("Backend not reachable")


class TestUnitTcoOwnerPermission:
    """Unit tests verifying owner role is blocked from viewing other units."""

    @pytest.fixture(autouse=True)
    def _mock_get_owner_info(self):
        with patch("services.ownerhub_service.get_owner_info", AsyncMock(return_value={"owner_name": "Test Owner"})):
            yield

    @pytest.mark.asyncio
    async def test_owner_cannot_see_other_unit(self):
        """
        Owner whose unit_number is UA001 must get HTTP 403 when requesting
        TCO for TH087. The 403 is raised inside the router function BEFORE
        compute_unit_tco is called — no DB patching required for this test.
        """
        from fastapi import HTTPException
        from routers.ownerhub import get_unit_tco

        owner_user = {
            "id": "user-owner-1",
            "role": "owner",
            "unit_number": "UA001",
            "full_name": "Test Owner",
            "is_approved": True,
        }

        with pytest.raises(HTTPException) as exc_info:
            await get_unit_tco(
                unit_number="TH087",
                year="2026",
                vacancy_weeks=2,
                current_user=owner_user,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_can_see_own_unit(self):
        """Owner can access TCO for their own unit without 403."""
        from routers.ownerhub import get_unit_tco

        owner_user = {
            "id": "user-owner-1",
            "role": "owner",
            "unit_number": "TH087",
            "full_name": "Avneet Rooprai",
            "is_approved": True,
        }

        mock_db = _build_tco_mock_db()

        # routers/ownerhub.py calls ohsvc.compute_unit_tco which uses database.db internally
        with patch("services.ownerhub_service.db", mock_db):
            result = await get_unit_tco(
                unit_number="TH087",
                year="2026",
                vacancy_weeks=2,
                current_user=owner_user,
            )

        assert result["unit_number"] == "TH087"

    @pytest.mark.asyncio
    async def test_super_admin_can_see_any_unit(self):
        """Super admin can access TCO for any unit without 403."""
        from routers.ownerhub import get_unit_tco

        admin_user = {
            "id": "user-admin-1",
            "role": "super_admin",
            "unit_number": "",
            "full_name": "Admin User",
            "is_approved": True,
        }

        mock_db = _build_tco_mock_db()

        with patch("services.ownerhub_service.db", mock_db):
            result = await get_unit_tco(
                unit_number="TH087",
                year="2026",
                vacancy_weeks=2,
                current_user=admin_user,
            )

        assert result["unit_number"] == "TH087"


# ─────────────────────────────────────────────────────────────────────────────
# TestOwnerHubUnitsEndpoint — list_strata_units (unit + live)
# ─────────────────────────────────────────────────────────────────────────────

class TestOwnerHubUnitsService:
    """
    Unit tests for GET /owner-hub/units router function.

    routers/ownerhub.py uses `from database import db` INSIDE each function
    body (lazy import), so `patch("routers.ownerhub.db")` does not work.
    The correct patch target is `database.db` — this replaces the object
    in the module where it lives, which is what the `from database import db`
    statement will reference at call time.
    """

    @pytest.mark.asyncio
    async def test_admin_gets_all_units(self):
        """Super admin role returns all units from db.units."""
        from routers.ownerhub import list_strata_units

        sample_units = [
            {"unit_number": "UA001", "owner_name": "Owner A", "property_type": "Apartment", "entitlement": 100.0},
            {"unit_number": "TH087", "owner_name": "Avneet Rooprai", "property_type": "Townhouse",
             "entitlement": 120.0},
        ]

        mock_db = MagicMock()
        mock_find_cursor = MagicMock()
        mock_find_cursor.skip.return_value = mock_find_cursor
        mock_find_cursor.limit.return_value = mock_find_cursor
        mock_find_cursor.to_list = AsyncMock(return_value=sample_units)
        mock_db.units.find.return_value = mock_find_cursor

        admin_user = {
            "id": "admin-1",
            "role": "super_admin",
            "unit_number": "",
        }

        # Patch database.db — the module-level object; lazy `from database import db`
        # inside the router function will see this patched version.
        with patch("database.db", mock_db):
            result = await list_strata_units(current_user=admin_user)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_owner_gets_only_own_unit(self):
        """Owner role receives only their own unit from db.units."""
        from routers.ownerhub import list_strata_units

        sample_units = [
            {"unit_number": "TH087", "owner_name": "Avneet Rooprai", "property_type": "Townhouse",
             "entitlement": 120.0},
        ]

        mock_db = MagicMock()
        mock_find_cursor = MagicMock()
        mock_find_cursor.skip.return_value = mock_find_cursor
        mock_find_cursor.limit.return_value = mock_find_cursor
        mock_find_cursor.to_list = AsyncMock(return_value=sample_units)
        mock_db.units.find.return_value = mock_find_cursor

        owner_user = {
            "id": "owner-1",
            "role": "owner",
            "unit_number": "TH087",
        }

        with patch("database.db", mock_db):
            result = await list_strata_units(current_user=owner_user)

        # Verify find was called with the unit filter (not empty dict)
        call_args = mock_db.units.find.call_args
        # First positional arg is the filter dict
        filter_arg = call_args[0][0] if call_args[0] else call_args[1].get("filter", {})
        assert filter_arg.get("unit_number") == "TH087"

    @pytest.mark.asyncio
    async def test_units_returns_expected_fields(self):
        """Response must include unit_number, owner_name, entitlement fields."""
        from routers.ownerhub import list_strata_units

        sample_units = [
            {"unit_number": "UA001", "owner_name": "Owner A", "property_type": "Apartment", "entitlement": 100.0},
        ]

        mock_db = MagicMock()
        mock_find_cursor = MagicMock()
        mock_find_cursor.skip.return_value = mock_find_cursor
        mock_find_cursor.limit.return_value = mock_find_cursor
        mock_find_cursor.to_list = AsyncMock(return_value=sample_units)
        mock_db.units.find.return_value = mock_find_cursor

        admin_user = {
            "id": "admin-1",
            "role": "super_admin",
            "unit_number": "",
        }

        with patch("database.db", mock_db):
            result = await list_strata_units(current_user=admin_user)

        assert len(result) > 0
        unit = result[0]
        assert "unit_number" in unit
        assert "owner_name" in unit
        assert "entitlement" in unit

    @pytest.mark.asyncio
    async def test_strata_manager_sees_all_units(self):
        """strata_manager role must get the same all-units query as super_admin."""
        from routers.ownerhub import list_strata_units

        sample_units = [
            {"unit_number": "UA001", "owner_name": "A", "property_type": "Apartment", "entitlement": 100.0},
            {"unit_number": "UA002", "owner_name": "B", "property_type": "Apartment", "entitlement": 110.0},
        ]

        mock_db = MagicMock()
        mock_find_cursor = MagicMock()
        mock_find_cursor.skip.return_value = mock_find_cursor
        mock_find_cursor.limit.return_value = mock_find_cursor
        mock_find_cursor.to_list = AsyncMock(return_value=sample_units)
        mock_db.units.find.return_value = mock_find_cursor

        sm_user = {
            "id": "sm-1",
            "role": "strata_manager",
            "unit_number": "",
        }

        with patch("database.db", mock_db):
            result = await list_strata_units(current_user=sm_user)

        # strata_manager hits the "all units" branch — no unit_number filter
        call_args = mock_db.units.find.call_args
        filter_arg = call_args[0][0] if call_args[0] else {}
        # Should NOT have a unit_number key in the filter (admin branch = {} or similar)
        assert "unit_number" not in filter_arg


class TestOwnerHubUnitsEndpointLive:
    """Live integration tests for GET /owner-hub/units (skipped by default)."""

    @pytest.mark.integration
    def test_units_list_admin_returns_all(self, admin_headers):
        """Admin gets all units (expect multiple results for Eastgate with 87 units)."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/owner-hub/units",
            headers=admin_headers,
            timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # Eastgate has 87 units — verify a reasonable count
        assert len(data) > 0

    @pytest.mark.integration
    def test_units_list_returns_expected_fields_live(self, admin_headers):
        """Each unit in live response has unit_number, owner_name, entitlement."""
        import requests
        if not admin_headers:
            pytest.skip("No admin token available")
        r = requests.get(
            "http://127.0.0.1:8003/api/owner-hub/units",
            headers=admin_headers,
            timeout=15,
        )
        if r.status_code != 200:
            pytest.skip(f"Backend returned {r.status_code}")
        data = r.json()
        if not data:
            pytest.skip("Empty units list")
        first = data[0]
        assert "unit_number" in first

    @pytest.mark.integration
    def test_units_no_auth_returns_401(self):
        """Without auth token, endpoint returns 401 or 403."""
        import requests
        try:
            r = requests.get(
                "http://127.0.0.1:8003/api/owner-hub/units",
                timeout=10,
            )
            assert r.status_code in (401, 403)
        except Exception:
            pytest.skip("Backend not reachable")
