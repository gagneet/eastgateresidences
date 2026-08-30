"""GAP-FIN-052 item 2 (cont'd) — analytics.py governed route-cutover migration.

`/analytics/sinking-fund-forecast`, `/analytics/levy-benchmarks`,
`/analytics/expense-breakdown`, and `/analytics/budget-variance` previously
carried their own inline ``is_cutover_feature_enabled(FINANCIAL_PG_READS_ENABLED)``
gate that bypassed the shadow-soak/critical-diff gates every other finance route
respects. All four now resolve their read source via
``get_finance_route_runtime_state()`` (through the ``_governed_read_source``
helper) and serve their existing ``analytics_pg_service`` PG read model only when
the engine resolves the route to ``postgres``.

These tests assert the governed dispatch, not the SQL: when the engine says
``postgres`` the PG service is served (and a PG error falls back to Mongo); when
it says ``mongo`` (today's state — none of these are promoted) the PG service is
never called.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import routers.analytics as analytics


_SENTINEL_PG = {"source": "postgres", "sentinel": True}


def _user(unit_number=None):
    u = {"role": "strata_manager", "is_active": True, "is_approved": True, "building_id": "13195"}
    if unit_number:
        u["unit_number"] = unit_number
    return u


def _engine(source):
    return AsyncMock(return_value={"source": source})


# ─── postgres source serves the PG read model (one per route) ────────────────

@pytest.mark.asyncio
async def test_sinking_fund_forecast_postgres_source_serves_pg():
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("postgres")), \
         patch("services.analytics_pg_service.get_sinking_fund_forecast_pg",
               AsyncMock(return_value=_SENTINEL_PG)):
        result = await analytics.get_sinking_fund_forecast(
            years=10, current_user=_user(), building_id="13195")
    assert result is _SENTINEL_PG


@pytest.mark.asyncio
async def test_levy_benchmarks_postgres_source_serves_pg():
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("postgres")), \
         patch("services.analytics_pg_service.get_levy_benchmarks_pg",
               AsyncMock(return_value=_SENTINEL_PG)):
        result = await analytics.get_levy_benchmarks(
            financial_year=None, current_user=_user(), building_id="13195")
    assert result is _SENTINEL_PG


@pytest.mark.asyncio
async def test_expense_breakdown_postgres_source_serves_pg():
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("postgres")), \
         patch("services.analytics_pg_service.get_expense_breakdown_pg",
               AsyncMock(return_value=_SENTINEL_PG)):
        result = await analytics.get_expense_breakdown(
            financial_year="2026", year=None, current_user=_user(), building_id="13195")
    assert result is _SENTINEL_PG


@pytest.mark.asyncio
async def test_budget_variance_postgres_source_serves_pg():
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("postgres")), \
         patch("services.analytics_pg_service.get_budget_variance_pg",
               AsyncMock(return_value=_SENTINEL_PG)):
        result = await analytics.get_budget_variance(
            year="2026", current_user=_user(), building_id="13195")
    assert result is _SENTINEL_PG


# ─── mongo source never invokes the PG read model ────────────────────────────

@pytest.mark.asyncio
async def test_levy_benchmarks_mongo_source_skips_pg():
    pg = AsyncMock(return_value=_SENTINEL_PG)
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])  # no annual_levies => empty result
    mock_db.annual_levies.find.return_value = cursor
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("mongo")), \
         patch("services.analytics_pg_service.get_levy_benchmarks_pg", new=pg), \
         patch.object(analytics, "db", mock_db):
        result = await analytics.get_levy_benchmarks(
            financial_year=None, current_user=_user(), building_id="13195")
    assert result == []
    pg.assert_not_awaited()


@pytest.mark.asyncio
async def test_expense_breakdown_mongo_source_skips_pg():
    pg = AsyncMock(return_value=_SENTINEL_PG)
    mongo_payload = [{"category": "Cleaning", "amount": 1234.0}]
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("mongo")), \
         patch("services.analytics_pg_service.get_expense_breakdown_pg", new=pg), \
         patch("utils.finance_helpers.get_expense_breakdown_by_category",
               AsyncMock(return_value=mongo_payload)):
        result = await analytics.get_expense_breakdown(
            financial_year="2026", year=None, current_user=_user(), building_id="13195")
    assert result == mongo_payload
    pg.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_variance_mongo_source_skips_pg():
    pg = AsyncMock(return_value=_SENTINEL_PG)
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])  # no levy_categories => empty result
    mock_db.levy_categories.find.return_value = cursor
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("mongo")), \
         patch("services.analytics_pg_service.get_budget_variance_pg", new=pg), \
         patch.object(analytics, "db", mock_db):
        result = await analytics.get_budget_variance(
            year="2026", current_user=_user(), building_id="13195")
    assert result == []
    pg.assert_not_awaited()


# ─── levy_allocation_breakdown: the last ungoverned inline-PG hole (GAP-FIN-052 follow-up) ───

class _ACM:
    """Minimal async context manager yielding a mock session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_levy_allocation_breakdown_postgres_source_serves_pg():
    """Engine → postgres: the inline finance.levy_items PG read model is served."""
    from types import SimpleNamespace

    fund_rows = SimpleNamespace(
        fetchall=MagicMock(return_value=[
            SimpleNamespace(fund_type="admin", amount_cents=30000000),
            SimpleNamespace(fund_type="sinking", amount_cents=14000000),
        ])
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=fund_rows)

    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("postgres")), \
         patch("db_postgres.repos.config_repo.resolve_scheme_context",
               AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"})), \
         patch("db_postgres.session.async_session_context",
               MagicMock(return_value=_ACM(session))), \
         patch("db_postgres.session.set_tenant", AsyncMock()):
        result = await analytics.get_levy_allocation_breakdown(
            year="2026", current_user=_user(), building_id="13195")

    assert result["source"] == "postgres"
    assert result["admin_annual"] == 300000.0
    assert result["sinking_annual"] == 140000.0


@pytest.mark.asyncio
async def test_levy_allocation_breakdown_mongo_source_skips_pg():
    """Engine → mongo (today's state): the inline PG path is never entered."""
    resolve = AsyncMock()
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    with patch.object(analytics, "get_finance_route_runtime_state", new=_engine("mongo")), \
         patch("db_postgres.repos.config_repo.resolve_scheme_context", new=resolve), \
         patch.object(analytics, "db", mock_db), \
         patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value="2026")):
        result = await analytics.get_levy_allocation_breakdown(
            year=None, current_user=_user(), building_id="13195")
    # PG scheme resolution must not be attempted when the engine says mongo.
    resolve.assert_not_awaited()
    assert result["source"] != "postgres"


@pytest.mark.asyncio
async def test_levy_allocation_breakdown_fires_shadow_when_run_shadow():
    """Mongo path + run_shadow True → the route feeds core.shadow_diffs with its
    admin/sinking/total AUD figures (the PG side is fetched by the shadow service)."""
    engine = AsyncMock(return_value={"source": "mongo", "run_shadow": True})
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    shadow = AsyncMock()
    with patch.object(analytics, "get_finance_route_runtime_state", new=engine), \
         patch.object(analytics, "db", mock_db), \
         patch("services.finance_shadow_read_service.maybe_run_finance_shadow", new=shadow):
        await analytics.get_levy_allocation_breakdown(
            year="2026", current_user=_user(), building_id="13195")
    shadow.assert_awaited_once()
    kwargs = shadow.await_args.kwargs
    assert kwargs["route_key"] == "analytics.levy_allocation_breakdown"
    assert {"year", "admin_annual", "sinking_annual", "total_annual"} <= set(kwargs["mongo_payload"])


@pytest.mark.asyncio
async def test_levy_allocation_breakdown_no_shadow_when_run_shadow_false():
    engine = AsyncMock(return_value={"source": "mongo", "run_shadow": False})
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    shadow = AsyncMock()
    with patch.object(analytics, "get_finance_route_runtime_state", new=engine), \
         patch.object(analytics, "db", mock_db), \
         patch("services.finance_shadow_read_service.maybe_run_finance_shadow", new=shadow):
        await analytics.get_levy_allocation_breakdown(
            year="2026", current_user=_user(), building_id="13195")
    shadow.assert_not_awaited()


@pytest.mark.asyncio
async def test_levy_allocation_totals_pg_maps_funds_to_cents():
    """The shadow PG payload sums levy_items per fund into admin/sinking/total CENTS
    (capital_works rolls into sinking, matching the route's own fund grouping)."""
    from types import SimpleNamespace
    import services.analytics_pg_service as aps

    fund_rows = SimpleNamespace(fetchall=MagicMock(return_value=[
        SimpleNamespace(fund_type="admin", amount_cents=30000000),
        SimpleNamespace(fund_type="capital_works", amount_cents=14000000),
    ]))
    session = MagicMock()
    session.execute = AsyncMock(return_value=fund_rows)
    with patch.object(aps, "_resolve_scheme",
                      AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"})), \
         patch("db_postgres.session.async_session_context", MagicMock(return_value=_ACM(session))), \
         patch("db_postgres.session.set_tenant", AsyncMock()):
        result = await aps.get_levy_allocation_totals_pg("13195", "2026")
    assert result == {
        "admin_annual": 30000000, "sinking_annual": 14000000, "total_annual": 44000000,
    }


# ── the three list-shaped analytics routes: derived-scalar shadow (GAP-FIN-055) ──

@pytest.mark.asyncio
async def test_expense_breakdown_fires_shadow_with_total():
    """Mongo path + run_shadow → fires shadow with total_expense = Σ category amount."""
    engine = AsyncMock(return_value={"source": "mongo", "run_shadow": True})
    shadow = AsyncMock()
    with patch.object(analytics, "get_finance_route_runtime_state", new=engine), \
         patch("utils.finance_helpers.get_expense_breakdown_by_category",
               AsyncMock(return_value=[{"category": "Cleaning", "amount": 100.0},
                                       {"category": "Insurance", "amount": 50.0}])), \
         patch("services.finance_shadow_read_service.maybe_run_finance_shadow", new=shadow):
        await analytics.get_expense_breakdown(
            financial_year="2026", year=None, current_user=_user(), building_id="13195")
    shadow.assert_awaited_once()
    assert shadow.await_args.kwargs["mongo_payload"]["total_expense"] == 150.0


@pytest.mark.asyncio
async def test_analytics_shadow_pg_payload_reductions_to_cents():
    """_get_pg_payload_for_route reduces each list-shaped PG read model to the
    matching scalar in CENTS (so _compare_generic_payloads can compare vs Mongo AUD)."""
    import services.finance_shadow_read_service as fsr

    with patch("services.analytics_pg_service.get_sinking_fund_forecast_pg",
               AsyncMock(return_value={"current_balance": 1234.56})), \
         patch("services.analytics_pg_service.get_expense_breakdown_pg",
               AsyncMock(return_value=[{"amount": 100.0}, {"amount": 50.0}])), \
         patch("services.analytics_pg_service.get_levy_benchmarks_pg",
               AsyncMock(return_value=[{"Building": 200.0}, {"Building": 300.0}])):
        sinking = await fsr._get_pg_payload_for_route(
            building_id="13195", route_key="analytics.sinking_fund_forecast", mongo_payload={})
        expense = await fsr._get_pg_payload_for_route(
            building_id="13195", route_key="analytics.expense_breakdown", mongo_payload={"year": "2026"})
        bench = await fsr._get_pg_payload_for_route(
            building_id="13195", route_key="analytics.levy_benchmarks", mongo_payload={})

    assert sinking == {"current_balance": 123456}
    assert expense == {"total_expense": 15000}
    assert bench == {"total_levied": 50000}


@pytest.mark.asyncio
async def test_levy_allocation_breakdown_engine_unavailable_defaults_to_mongo():
    """Engine raising must not 500 the route or leak into the PG path."""
    resolve = AsyncMock()
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    with patch.object(analytics, "get_finance_route_runtime_state",
                      AsyncMock(side_effect=RuntimeError("control plane down"))), \
         patch("db_postgres.repos.config_repo.resolve_scheme_context", new=resolve), \
         patch.object(analytics, "db", mock_db), \
         patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value="2026")):
        result = await analytics.get_levy_allocation_breakdown(
            year=None, current_user=_user(), building_id="13195")
    resolve.assert_not_awaited()
    assert result["source"] != "postgres"


# ─── engine-unavailable defaults to the safe Mongo primary (never 500) ───────

@pytest.mark.asyncio
async def test_engine_unavailable_defaults_to_mongo():
    pg = AsyncMock(return_value=_SENTINEL_PG)
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    mock_db.levy_categories.find.return_value = cursor
    with patch.object(analytics, "get_finance_route_runtime_state",
                      AsyncMock(side_effect=RuntimeError("control plane down"))), \
         patch("services.analytics_pg_service.get_budget_variance_pg", new=pg), \
         patch.object(analytics, "db", mock_db):
        result = await analytics.get_budget_variance(
            year="2026", current_user=_user(), building_id="13195")
    assert result == []
    pg.assert_not_awaited()
