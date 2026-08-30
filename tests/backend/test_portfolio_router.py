"""
Tests for Portfolio Operations Router (routers/portfolio.py).

Covers:
  - GET  /portfolio/dashboard              — portfolio summary across buildings
  - GET  /portfolio/summary                — cross-building metrics
  - GET  /portfolio/buildings              — list buildings with health
  - GET  /portfolio/arrears-summary        — cross-building arrears
  - GET  /portfolio/onboarding/template    — onboarding template with steps
  - POST /portfolio/buildings/{id}/onboarding/validate  — go-live readiness checks

All DB calls are mocked. No real database writes occur.
Tests are idempotent and multi-tenant safe.
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

BUILDING_ID = "13195"
BUILDING_ID_OTHER = "16244"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_super_admin():
    return {
        "id": "user-superadmin-001",
        "role": "super_admin",
        "email": "superadmin@test.com",
        "full_name": "Super Admin",
        "building_id": BUILDING_ID,
    }


def _onboarding_request(building_id: str):
    """A Request whose path params name the building, as FastAPI would supply."""
    from starlette.requests import Request as StarletteRequest

    request = StarletteRequest({
        "type": "http",
        "method": "GET",
        "path": f"/api/portfolio/buildings/{building_id}/onboarding",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })
    request.scope["path_params"] = {"building_id": building_id}
    return request


def _onboarding_dependency(capability: str):
    from services.capability_registry import require_capability

    return require_capability(capability, scope_params={"building_id": "building_id"})


def _make_strata_manager():
    return {
        "id": "user-manager-001",
        "role": "strata_manager",
        "email": "manager@test.com",
        "full_name": "Strata Manager",
        "building_id": BUILDING_ID,
    }


def _make_owner_user():
    return {
        "id": "user-owner-001",
        "role": "owner",
        "email": "owner@test.com",
        "building_id": BUILDING_ID,
    }


def _make_building_doc(
        building_id=BUILDING_ID,
        name="East Gate Residences",
        total_lots=87,
        lot_count=None,
        is_active=True,
):
    # lot_count defaults to total_lots so callers only need to pass one param
    _lot_count = lot_count if lot_count is not None else total_lots
    return {
        "id": building_id,
        "name": name,
        "total_lots": total_lots,
        "lot_count": _lot_count,
        "is_active": is_active,
    }


def _make_summary_doc(building_id=BUILDING_ID, health_score=92, arrears_rate=3.5):
    return {
        "building_id": building_id,
        "health_score": health_score,
        "arrears_rate": arrears_rate,
        "open_work_orders": 5,
        "next_compliance_item": "AGM Minutes",
    }


def _make_onboarding_step(step_id="s001", required=True, completed=False):
    return {
        "id": step_id,
        "name": f"Step {step_id}",
        "category": "data",
        "required": required,
        "description": "A required onboarding step",
        "completed": completed,
        "completed_at": None,
        "completed_by": None,
        "notes": "",
    }


def _make_onboarding_template():
    return {
        "version": "1.0",
        "steps": [
            _make_onboarding_step("s001", required=True),
            _make_onboarding_step("s002", required=True),
            _make_onboarding_step("s003", required=False),
        ],
    }


def _mock_cursor_to_list(docs: list):
    """Mock for db.collection.find(...).to_list(length=N)."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    cursor.find.return_value = cursor
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    return cursor


# ─────────────────────────────────────────────────────────────────────────────
# 1. GET /portfolio/dashboard — requires strata_manager
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_dashboard_requires_manager():
    """Non-manager (owner, ec_member, chairman) must receive 403."""
    from fastapi import HTTPException
    from routers.portfolio import get_portfolio_dashboard

    for role in ["owner", "tenant", "guest"]:
        user = {"id": "u1", "role": role}
        with pytest.raises(HTTPException) as exc_info:
            await get_portfolio_dashboard(current_user=user)
        assert exc_info.value.status_code == 403, (
            f"Expected 403 for role={role}, got {exc_info.value.status_code}"
        )


@pytest.mark.asyncio
async def test_portfolio_dashboard_returns_buildings_and_summary():
    """strata_manager receives buildings list and summary_totals dict."""
    from routers.portfolio import get_portfolio_dashboard

    bld = _make_building_doc()
    summary = _make_summary_doc()

    mock_db = MagicMock()
    mock_db._db = MagicMock()

    mock_db._db.organisation_buildings.find.return_value = _mock_cursor_to_list(
        [{"building_id": BUILDING_ID, "is_active": True}]
    )
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([bld])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([summary])

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await get_portfolio_dashboard(current_user=_make_strata_manager())

    assert "buildings" in result
    assert "summary" in result
    assert isinstance(result["buildings"], list)
    assert result["summary"]["total_buildings"] >= 0


@pytest.mark.asyncio
async def test_portfolio_dashboard_uses_ledger_arrears_rate():
    """arrears_rate in the response comes from the ledger helper, not building_summaries."""
    from routers.portfolio import get_portfolio_dashboard

    bld = _make_building_doc()
    # building_summaries carries a stale/different arrears_rate — must be ignored.
    summary = _make_summary_doc(arrears_rate=99.9)

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.organisation_buildings.find.return_value = _mock_cursor_to_list([])
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([bld])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([summary])

    ledger_arrears = {BUILDING_ID: {
        "total_outstanding": 4200.0, "total_levied": 20000.0,
        "units_in_arrears": 3, "arrears_rate": 21.0,
    }}
    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value=ledger_arrears)):
        result = await get_portfolio_dashboard(current_user=_make_strata_manager())

    entry = result["buildings"][0]
    assert entry["arrears_rate"] == 21.0
    assert entry["total_outstanding"] == 4200.0
    high_arrears_alerts = [a for a in result["alerts"] if a["type"] == "high_arrears"]
    assert len(high_arrears_alerts) == 1


@pytest.mark.asyncio
async def test_portfolio_dashboard_ec_member_has_access():
    """ec_member (EC Chairman) is now in _MANAGER_WITH_CHAIRMAN for portfolio access.
    EC members see only their own building's data and need this view for building health."""
    from routers.portfolio import _require_manager

    ec_user = {"id": "u-ec", "role": "ec_member"}
    # Should not raise — ec_member is now an allowed role
    _require_manager(ec_user)


@pytest.mark.asyncio
async def test_portfolio_dashboard_alerts_generated_for_low_health():
    """Buildings with health_score < 50 should generate a low_health alert."""
    from routers.portfolio import get_portfolio_dashboard

    bld = _make_building_doc()
    low_health_summary = _make_summary_doc(health_score=35)

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.organisation_buildings.find.return_value = _mock_cursor_to_list([])
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([bld])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([low_health_summary])

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await get_portfolio_dashboard(current_user=_make_strata_manager())

    alert_types = [a["type"] for a in result.get("alerts", [])]
    assert "low_health" in alert_types


# ─────────────────────────────────────────────────────────────────────────────
# 2. GET /portfolio/summary
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_summary_returns_building_count():
    """GET /portfolio/summary should return active_buildings count."""
    from routers.portfolio import get_portfolio_summary

    buildings = [
        _make_building_doc(BUILDING_ID, total_lots=87),
        _make_building_doc(BUILDING_ID_OTHER, name="Sierra Gungahlin", total_lots=50),
    ]

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list(buildings)
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([])

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await get_portfolio_summary(current_user=_make_super_admin())

    assert result["active_buildings"] == 2
    assert result["total_lots"] == 137


@pytest.mark.asyncio
async def test_portfolio_summary_arrears_from_ledger_not_building_summaries():
    """total_arrears_cents must come from the ledger helper, not building_summaries.arrears_cents."""
    from routers.portfolio import get_portfolio_summary

    buildings = [_make_building_doc(BUILDING_ID, total_lots=87)]
    # building_summaries carries a stale/wrong arrears_cents — must be ignored entirely.
    stale_summary = {"building_id": BUILDING_ID, "arrears_cents": 999999, "health_score": 90}

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list(buildings)
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([stale_summary])

    ledger_arrears = {BUILDING_ID: {
        "total_outstanding": 150.25, "total_levied": 1000.0,
        "units_in_arrears": 1, "arrears_rate": 15.0,
    }}
    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value=ledger_arrears)):
        result = await get_portfolio_summary(current_user=_make_super_admin())

    assert result["total_arrears_cents"] == 15025
    assert result["is_health_score_authoritative_finance_metric"] is False


@pytest.mark.asyncio
async def test_portfolio_summary_requires_admin():
    """Non-admin roles must get 403 on /portfolio/summary."""
    from fastapi import HTTPException
    from routers.portfolio import get_portfolio_summary

    for role in ["owner", "chairman", "ec_member"]:
        user = {"id": "u1", "role": role}
        with pytest.raises(HTTPException) as exc_info:
            await get_portfolio_summary(current_user=user)
        assert exc_info.value.status_code == 403, f"Expected 403 for role={role}"


@pytest.mark.asyncio
async def test_portfolio_summary_empty_buildings():
    """When no active buildings, active_buildings=0 and total_lots=0."""
    from routers.portfolio import get_portfolio_summary

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([])

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await get_portfolio_summary(current_user=_make_super_admin())

    assert result["active_buildings"] == 0
    assert result["total_lots"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. GET /portfolio/buildings
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_buildings_list():
    """GET /portfolio/buildings returns list of buildings with health metrics."""
    from routers.portfolio import list_portfolio_buildings

    buildings = [
        _make_building_doc(BUILDING_ID),
        _make_building_doc(BUILDING_ID_OTHER, name="Sierra", total_lots=50),
    ]
    summaries = [
        _make_summary_doc(BUILDING_ID, health_score=90),
        _make_summary_doc(BUILDING_ID_OTHER, health_score=78),
    ]

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list(buildings)
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list(summaries)

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await list_portfolio_buildings(current_user=_make_super_admin())

    assert "buildings" in result
    assert "total" in result
    assert result["total"] == 2
    # Each building entry should include health_score from summary
    for bld in result["buildings"]:
        assert "health_score" in bld


@pytest.mark.asyncio
async def test_portfolio_buildings_arrears_rate_from_ledger():
    """arrears_rate must come from the ledger helper, not building_summaries.arrears_rate."""
    from routers.portfolio import list_portfolio_buildings

    bld = _make_building_doc(BUILDING_ID)
    stale_summary = _make_summary_doc(BUILDING_ID, arrears_rate=99.9)

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([bld])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([stale_summary])

    ledger_arrears = {BUILDING_ID: {
        "total_outstanding": 500.0, "total_levied": 2000.0,
        "units_in_arrears": 2, "arrears_rate": 25.0,
    }}
    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value=ledger_arrears)):
        result = await list_portfolio_buildings(current_user=_make_super_admin())

    assert result["buildings"][0]["arrears_rate"] == 25.0
    assert result["buildings"][0]["total_outstanding"] == 500.0


@pytest.mark.asyncio
async def test_portfolio_buildings_requires_manager():
    """Non-manager must get 403 from /portfolio/buildings."""
    from fastapi import HTTPException
    from routers.portfolio import list_portfolio_buildings

    with pytest.raises(HTTPException) as exc_info:
        await list_portfolio_buildings(current_user=_make_owner_user())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_portfolio_buildings_uses_summary_health_when_available():
    """health_score in response comes from building_summaries, not the buildings doc."""
    from routers.portfolio import list_portfolio_buildings

    bld = _make_building_doc(BUILDING_ID)
    summary = _make_summary_doc(BUILDING_ID, health_score=55)

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([bld])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([summary])

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await list_portfolio_buildings(current_user=_make_super_admin())

    assert result["buildings"][0]["health_score"] == 55


@pytest.mark.asyncio
async def test_portfolio_buildings_defaults_health_to_100_when_no_summary():
    """When no summary exists for a building, health_score defaults to 100."""
    from routers.portfolio import list_portfolio_buildings

    bld = _make_building_doc(BUILDING_ID)

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list([bld])
    mock_db._db.building_summaries.find.return_value = _mock_cursor_to_list([])

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await list_portfolio_buildings(current_user=_make_super_admin())

    assert result["buildings"][0]["health_score"] == 100


# ─────────────────────────────────────────────────────────────────────────────
# 4. GET /portfolio/onboarding/template
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_onboarding_template_returns_steps(tmp_path):
    """GET /portfolio/onboarding/template returns dict with steps list."""
    from routers.portfolio import get_onboarding_template

    template = _make_onboarding_template()
    template_file = tmp_path / "onboarding_template.json"
    template_file.write_text(json.dumps(template))

    with patch("routers.portfolio._DATA_DIR", tmp_path):
        result = await get_onboarding_template(current_user=_make_super_admin())

    assert "steps" in result
    assert len(result["steps"]) == 3


@pytest.mark.asyncio
async def test_onboarding_template_requires_admin():
    """Non-admin roles (ec_member, chairman, owner) must get 403."""
    from fastapi import HTTPException
    from routers.portfolio import get_onboarding_template

    for role in ["owner", "chairman", "ec_member"]:
        user = {"id": "u1", "role": role}
        with pytest.raises(HTTPException) as exc_info:
            await get_onboarding_template(current_user=user)
        assert exc_info.value.status_code == 403, (
            f"Expected 403 for role={role}"
        )


@pytest.mark.asyncio
async def test_onboarding_template_returns_empty_steps_when_missing(tmp_path):
    """When template file does not exist, endpoint returns {'steps': []}."""
    from routers.portfolio import get_onboarding_template

    # tmp_path has no onboarding_template.json
    with patch("routers.portfolio._DATA_DIR", tmp_path):
        result = await get_onboarding_template(current_user=_make_super_admin())

    assert result == {"steps": []}


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /portfolio/arrears-summary — requires manager
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_arrears_requires_manager():
    """Non-manager must receive 403 from /portfolio/arrears-summary."""
    from fastapi import HTTPException
    from routers.portfolio import get_arrears_summary

    for role in ["owner", "tenant", "guest"]:
        user = {"id": "u1", "role": role}
        with pytest.raises(HTTPException) as exc_info:
            await get_arrears_summary(current_user=user)
        assert exc_info.value.status_code == 403, (
            f"Expected 403 for role={role}"
        )


@pytest.mark.asyncio
async def test_portfolio_arrears_returns_building_breakdown():
    """strata_manager receives list of per-building arrears entries, sourced from
    the ledger helper — not levy_payments.balance (GAP-FIN-014: levy_payments is
    the receipts/detail layer, not the accounting ledger)."""
    from routers.portfolio import get_arrears_summary

    buildings = [
        _make_building_doc(BUILDING_ID),
        _make_building_doc(BUILDING_ID_OTHER, name="Sierra", total_lots=50),
    ]
    ledger_arrears = {
        BUILDING_ID: {"total_outstanding": 12500.75, "total_levied": 50000.0,
                       "units_in_arrears": 5, "arrears_rate": 25.0},
        BUILDING_ID_OTHER: {"total_outstanding": 3200.00, "total_levied": 40000.0,
                             "units_in_arrears": 2, "arrears_rate": 8.0},
    }

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list(buildings)

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value=ledger_arrears)):
        result = await get_arrears_summary(current_user=_make_strata_manager())

    assert "buildings" in result
    assert result["total"] == 2
    building_ids = [b["building_id"] for b in result["buildings"]]
    assert BUILDING_ID in building_ids
    assert BUILDING_ID_OTHER in building_ids


@pytest.mark.asyncio
async def test_portfolio_arrears_excludes_buildings_with_no_arrears():
    """A building with units_in_arrears == 0 is omitted from the breakdown."""
    from routers.portfolio import get_arrears_summary

    buildings = [_make_building_doc(BUILDING_ID)]
    ledger_arrears = {
        BUILDING_ID: {"total_outstanding": 0.0, "total_levied": 50000.0,
                       "units_in_arrears": 0, "arrears_rate": 0.0},
    }

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.buildings.find.return_value = _mock_cursor_to_list(buildings)

    with patch("routers.portfolio.db", mock_db), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value=ledger_arrears)):
        result = await get_arrears_summary(current_user=_make_strata_manager())

    assert result["total"] == 0
    assert result["buildings"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 5b. _get_portfolio_ledger_arrears — the shared GAP-FIN-014 helper
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_arrears_helper_rounds_to_2dp_and_computes_rate():
    """total_outstanding/total_levied round to 2dp; arrears_rate is a percentage."""
    from routers.portfolio import _get_portfolio_ledger_arrears

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    year_cursor = MagicMock()
    year_cursor.to_list = AsyncMock(return_value=[{"_id": BUILDING_ID, "max_year": "2026"}])
    arrears_cursor = MagicMock()
    arrears_cursor.to_list = AsyncMock(return_value=[{
        "_id": BUILDING_ID, "total_outstanding": 1234.5678, "total_levied": 10000.0,
        "units_in_arrears": 3,
    }])
    mock_db._db.unit_levy_ledger.aggregate = AsyncMock(side_effect=[year_cursor, arrears_cursor])

    with patch("routers.portfolio.db", mock_db):
        result = await _get_portfolio_ledger_arrears([BUILDING_ID])

    assert result[BUILDING_ID]["total_outstanding"] == round(1234.5678, 2)
    assert result[BUILDING_ID]["arrears_rate"] == round(1234.5678 / 10000.0 * 100, 1)


@pytest.mark.asyncio
async def test_ledger_arrears_helper_returns_empty_for_no_building_ids():
    """No DB calls are made when building_ids is empty."""
    from routers.portfolio import _get_portfolio_ledger_arrears

    mock_db = MagicMock()
    with patch("routers.portfolio.db", mock_db):
        result = await _get_portfolio_ledger_arrears([])

    assert result == {}
    mock_db._db.unit_levy_ledger.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_ledger_arrears_helper_returns_empty_when_no_ledger_rows():
    """A building with zero unit_levy_ledger rows is simply absent from the result
    (not a KeyError or a fabricated zero) — callers must default missing entries."""
    from routers.portfolio import _get_portfolio_ledger_arrears

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    empty_cursor = MagicMock()
    empty_cursor.to_list = AsyncMock(return_value=[])
    mock_db._db.unit_levy_ledger.aggregate = AsyncMock(return_value=empty_cursor)

    with patch("routers.portfolio.db", mock_db):
        result = await _get_portfolio_ledger_arrears([BUILDING_ID])

    assert result == {}
    # Only the year-lookup aggregate should run; the second is skipped entirely.
    mock_db._db.unit_levy_ledger.aggregate.assert_called_once()


@pytest.mark.asyncio
async def test_ledger_arrears_helper_uses_only_latest_year_per_building():
    """Only the latest year's rows are matched in the second aggregate call —
    prior years' net_balance is already carried into that year's opening balance,
    so summing across years would double-count arrears."""
    from routers.portfolio import _get_portfolio_ledger_arrears

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    year_cursor = MagicMock()
    year_cursor.to_list = AsyncMock(return_value=[
        {"_id": BUILDING_ID, "max_year": "2026"},
        {"_id": BUILDING_ID_OTHER, "max_year": "2025"},
    ])
    arrears_cursor = MagicMock()
    arrears_cursor.to_list = AsyncMock(return_value=[])
    mock_db._db.unit_levy_ledger.aggregate = AsyncMock(side_effect=[year_cursor, arrears_cursor])

    with patch("routers.portfolio.db", mock_db):
        await _get_portfolio_ledger_arrears([BUILDING_ID, BUILDING_ID_OTHER])

    second_call_pipeline = mock_db._db.unit_levy_ledger.aggregate.call_args_list[1][0][0]
    match_stage = second_call_pipeline[0]["$match"]
    assert {"building_id": BUILDING_ID, "year": "2026"} in match_stage["$or"]
    assert {"building_id": BUILDING_ID_OTHER, "year": "2025"} in match_stage["$or"]


@pytest.mark.asyncio
async def test_portfolio_arrears_ec_member_has_access():
    """ec_member is now in _MANAGER_WITH_CHAIRMAN — arrears summary must not 403."""
    from routers.portfolio import _require_manager

    ec_user = {"id": "u-ec", "role": "ec_member"}
    # Should not raise
    _require_manager(ec_user)


# ─────────────────────────────────────────────────────────────────────────────
# 6. POST /portfolio/buildings/{id}/onboarding/validate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_go_live_validation_all_passed():
    """validate_go_live returns all_passed=True when all checks succeed."""
    from routers.portfolio import validate_go_live

    checklist = {
        "building_id": BUILDING_ID,
        "steps": [
            _make_onboarding_step("s001", required=True, completed=True),
            _make_onboarding_step("s002", required=True, completed=True),
        ],
    }

    # Every check is now filtered on the PATH building_id via db._db (raw Motor),
    # not on the caller's session building via the TenantScopedDatabase wrapper —
    # see routers/portfolio.validate_go_live. Mocks follow that.
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.ec_members.count_documents = AsyncMock(return_value=3)
    mock_db._db.units.count_documents = AsyncMock(side_effect=[87, 87])  # with_uoe, total
    mock_db._db.document_folders.count_documents = AsyncMock(return_value=6)
    mock_db._db.memberships.distinct = AsyncMock(return_value=["u-1", "u-2"])
    mock_db._db.users.count_documents = AsyncMock(return_value=10)
    mock_db._db.building_onboarding_checklists.find_one = AsyncMock(return_value=checklist)

    with patch("routers.portfolio.db", mock_db):
        result = await validate_go_live(
            building_id=BUILDING_ID,
            current_user=_make_strata_manager(),
        )

    assert result["all_passed"] is True
    assert result["ready_for_go_live"] is True
    assert len(result["checks"]) == 5


@pytest.mark.asyncio
async def test_go_live_validation_fails_with_no_ec_members():
    """validate_go_live returns all_passed=False when no EC members exist."""
    from routers.portfolio import validate_go_live

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.ec_members.count_documents = AsyncMock(return_value=0)  # no EC members
    mock_db._db.units.count_documents = AsyncMock(side_effect=[87, 87])
    mock_db._db.document_folders.count_documents = AsyncMock(return_value=6)
    mock_db._db.memberships.distinct = AsyncMock(return_value=["u-1"])
    mock_db._db.users.count_documents = AsyncMock(return_value=5)
    mock_db._db.building_onboarding_checklists.find_one = AsyncMock(return_value=None)

    with patch("routers.portfolio.db", mock_db):
        result = await validate_go_live(
            building_id=BUILDING_ID,
            current_user=_make_strata_manager(),
        )

    assert result["all_passed"] is False
    ec_check = next(c for c in result["checks"] if c["check"] == "ec_members_assigned")
    assert ec_check["passed"] is False


@pytest.mark.asyncio
async def test_go_live_validation_requires_manager():
    """owner must get 403 on /portfolio/buildings/{id}/onboarding/validate.

    The guard moved from a `_require_manager(current_user)` call in the body to the
    `require_capability("building.onboarding.view", ...)` dependency, so it can no
    longer be exercised by calling the handler directly — a direct call passes
    `current_user` straight past the Depends default. The dependency is therefore
    what this test drives.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _onboarding_dependency("building.onboarding.view")(
            request=_onboarding_request(BUILDING_ID), current_user=_make_owner_user()
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_go_live_endpoints_deny_a_building_the_caller_is_not_assigned_to():
    """The BOLA these four routes carried: a manager of one building reaching another.

    `building_id` is caller-supplied and the handlers query
    `db._db.building_onboarding_checklists` — which bypasses TenantScopedDatabase —
    with it. Before the capability was scoped to the path parameter, a role-only
    `_require_manager()` let any ec_member / strata_admin / strata_manager of ANY
    building read and mutate another building's go-live checklist.
    """
    from fastapi import HTTPException

    async def _verified(subject, scope, **_hydration_hints):
        return {**subject, "assigned_building_ids": [BUILDING_ID], "governance_offices": []}

    for capability in ("building.onboarding.view", "building.onboarding.manage"):
        with patch(
            "services.authorisation_context.hydrate_authorisation_claims",
            new=AsyncMock(side_effect=_verified),
        ):
            # The building they ARE assigned to is allowed...
            assert await _onboarding_dependency(capability)(
                request=_onboarding_request(BUILDING_ID),
                current_user=_make_strata_manager(),
            ) is not None
            # ...and a foreign one is not.
            with pytest.raises(HTTPException) as exc_info:
                await _onboarding_dependency(capability)(
                    request=_onboarding_request("16244"),
                    current_user=_make_strata_manager(),
                )
            assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 7. Portfolio _require_manager and _require_admin helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_portfolio_require_manager_raises_for_non_manager():
    from fastapi import HTTPException
    from routers.portfolio import _require_manager

    # owner, tenant, guest have no management access
    # ec_member was added to _MANAGER_WITH_CHAIRMAN to allow EC chairmen portfolio access
    for role in ["owner", "tenant", "guest"]:
        with pytest.raises(HTTPException) as exc_info:
            _require_manager({"role": role})
        assert exc_info.value.status_code == 403, f"Expected 403 for role={role}"


def test_portfolio_require_manager_passes_for_manager_roles():
    from routers.portfolio import _require_manager

    # strata_manager and super_admin are in _MANAGER_ROLES
    for role in ["strata_manager", "super_admin"]:
        _require_manager({"role": role})  # Should not raise


def test_portfolio_require_admin_raises_for_non_admin():
    from fastapi import HTTPException
    from routers.portfolio import _require_admin

    for role in ["owner", "chairman", "ec_member", "tenant"]:
        with pytest.raises(HTTPException) as exc_info:
            _require_admin({"role": role})
        assert exc_info.value.status_code == 403


def test_portfolio_require_admin_passes_for_admin_roles():
    from routers.portfolio import _require_admin

    for role in ["super_admin", "strata_manager"]:
        _require_admin({"role": role})  # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
# 10. Multi-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_summary_building_13195_data_not_leaked_to_16244():
    """
    Multi-tenant: the summary aggregates from db._db.buildings which the
    TenantScopedDatabase or application-level scoping must isolate per building.
    We assert the function uses the db reference (not a hardcoded building_id),
    confirming that swapping `db` for a different-scoped instance
    would return different data.
    """
    from routers.portfolio import get_portfolio_summary

    # Simulate a db scoped to building 16244 returning only its buildings
    buildings_b = [_make_building_doc(BUILDING_ID_OTHER, name="Sierra", total_lots=50)]

    mock_db_b = MagicMock()
    mock_db_b._db = MagicMock()
    mock_db_b._db.buildings.find.return_value = _mock_cursor_to_list(buildings_b)
    mock_db_b._db.building_summaries.find.return_value = _mock_cursor_to_list([])

    with patch("routers.portfolio.db", mock_db_b), \
         patch("routers.portfolio._get_portfolio_ledger_arrears", AsyncMock(return_value={})):
        result = await get_portfolio_summary(current_user=_make_super_admin())

    # Only Sierra's lots should appear (no cross-tenant bleed)
    assert result["total_lots"] == 50
    assert result["active_buildings"] == 1
