"""GAP-FIN-052 item 2 — intelligence.py governed route-cutover migration.

`get_levy_fairness_v2` (/intelligence/levy-fairness) and `get_capital_shock`
(/intelligence/capital-shock) previously carried their own inline
try-Postgres/fall-back-to-Mongo logic that gated only on
``financial_pg_reads_enabled`` and bypassed the shadow-soak/critical-diff gates
every other finance route respects. Both now consult
``get_finance_route_runtime_state()`` for their route_key, exactly like
finance.py's canonical routes.

These tests assert the governed dispatch, not the SQL itself (the PG helpers are
patched): when the engine resolves the route to ``mongo`` (the current state —
neither route is promoted) the Postgres helper is never called; when it resolves
to ``postgres`` the PG result is served, and any None/exception from the PG
helper falls back to MongoDB rather than surfacing an error.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import routers.intelligence as intel


def _mock_db_with_levy_fairness(doc):
    mock_db = MagicMock()
    mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=doc)
    mock_db.capital_shock_risks.find_one = AsyncMock(return_value=doc)
    return mock_db


# ─── levy-fairness ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_levy_fairness_mongo_source_never_touches_postgres():
    """When the cutover engine resolves the route to mongo (current, unpromoted
    state), the Postgres helper is never invoked and MongoDB serves the result."""
    mongo_doc = {"building_id": "13195", "source": "mongo", "lbfi": {"current_score": 88.0}}
    pg_helper = AsyncMock(return_value={"source": "postgres"})
    with patch.object(intel, "db", _mock_db_with_levy_fairness(mongo_doc)), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "mongo"}),
    ), patch.object(intel, "_levy_fairness_postgres", new=pg_helper):
        result = await intel.get_levy_fairness_v2(current_user={"role": "owner"}, bid="13195")

    assert result is mongo_doc
    pg_helper.assert_not_awaited()


@pytest.mark.asyncio
async def test_levy_fairness_postgres_source_serves_pg_result():
    pg_payload = {"source": "postgres", "lbfi": {"current_score": 91.2, "D": 0.1}}
    with patch.object(intel, "db", _mock_db_with_levy_fairness({"source": "mongo"})), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "postgres"}),
    ), patch.object(intel, "_levy_fairness_postgres", new=AsyncMock(return_value=pg_payload)):
        result = await intel.get_levy_fairness_v2(current_user={"role": "owner"}, bid="13195")

    assert result is pg_payload


@pytest.mark.asyncio
async def test_levy_fairness_postgres_none_falls_back_to_mongo():
    """PG source but no scheme/lots in Postgres yet (helper returns None) →
    MongoDB fallback, not an empty PG response."""
    mongo_doc = {"building_id": "13195", "source": "mongo"}
    with patch.object(intel, "db", _mock_db_with_levy_fairness(mongo_doc)), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "postgres"}),
    ), patch.object(intel, "_levy_fairness_postgres", new=AsyncMock(return_value=None)):
        result = await intel.get_levy_fairness_v2(current_user={"role": "owner"}, bid="13195")

    assert result is mongo_doc


@pytest.mark.asyncio
async def test_levy_fairness_postgres_error_falls_back_to_mongo():
    mongo_doc = {"building_id": "13195", "source": "mongo"}
    with patch.object(intel, "db", _mock_db_with_levy_fairness(mongo_doc)), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "postgres"}),
    ), patch.object(
        intel, "_levy_fairness_postgres",
        new=AsyncMock(side_effect=RuntimeError("PG down")),
    ):
        result = await intel.get_levy_fairness_v2(current_user={"role": "owner"}, bid="13195")

    assert result is mongo_doc


@pytest.mark.asyncio
async def test_levy_fairness_mongo_empty_recomputes():
    """Mongo path with no cached doc recomputes via simulate_levy_fairness_v2 —
    behaviour unchanged from before the migration."""
    computed = {"source": "computed", "lbfi": {"current_score": 70.0}}
    with patch.object(intel, "db", _mock_db_with_levy_fairness(None)), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "mongo"}),
    ), patch.object(intel, "simulate_levy_fairness_v2", new=AsyncMock(return_value=computed)):
        result = await intel.get_levy_fairness_v2(current_user={"role": "owner"}, bid="13195")

    assert result is computed


# ─── capital-shock ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capital_shock_mongo_source_never_touches_postgres():
    mongo_doc = {"building_id": "13195", "capital_shock_index": {"rows": []}}
    pg_helper = AsyncMock(return_value={"source": "postgres"})
    with patch.object(intel, "db", _mock_db_with_levy_fairness(mongo_doc)), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "mongo"}),
    ), patch.object(intel, "_capital_shock_postgres", new=pg_helper):
        result = await intel.get_capital_shock(current_user={"role": "owner"}, bid="13195")

    assert result is mongo_doc
    pg_helper.assert_not_awaited()


@pytest.mark.asyncio
async def test_capital_shock_postgres_source_serves_pg_result():
    pg_payload = {"source": "postgres", "capital_shock_index": {"rows": [], "next_shock": None}}
    with patch.object(intel, "db", _mock_db_with_levy_fairness({"source": "mongo"})), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "postgres"}),
    ), patch.object(intel, "_capital_shock_postgres", new=AsyncMock(return_value=pg_payload)):
        result = await intel.get_capital_shock(current_user={"role": "owner"}, bid="13195")

    assert result is pg_payload


@pytest.mark.asyncio
async def test_capital_shock_postgres_error_falls_back_to_mongo():
    mongo_doc = {"building_id": "13195", "capital_shock_index": {"rows": []}}
    with patch.object(intel, "db", _mock_db_with_levy_fairness(mongo_doc)), patch.object(
        intel, "get_finance_route_runtime_state",
        new=AsyncMock(return_value={"source": "postgres"}),
    ), patch.object(
        intel, "_capital_shock_postgres",
        new=AsyncMock(side_effect=RuntimeError("PG down")),
    ):
        result = await intel.get_capital_shock(current_user={"role": "owner"}, bid="13195")

    assert result is mongo_doc
