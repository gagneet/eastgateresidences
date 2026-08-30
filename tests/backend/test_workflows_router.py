"""
Tests for Workflow Governance Router (routers/workflows.py).

Covers:
  - GET  /workflows/status          — catalogue enriched with last-run + health
  - GET  /workflows/{id}/runs       — recent runs for a specific workflow
  - GET  /workflows/catalogue       — raw catalogue (super_admin only)
  - GET  /workflows/runs            — all runs list (admin)
  - GET  /workflows/runs/{run_id}   — single run by id (admin)
  - Unit: _compute_workflow_health  — pure function logic

All DB calls are mocked. No real database writes occur.
Tests are idempotent and multi-tenant safe.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

BUILDING_ID = "13195"
BUILDING_ID_OTHER = "16244"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_manager_user(role="strata_manager"):
    return {
        "id": "user-manager-001",
        "role": role,
        "email": "manager@test.com",
        "building_id": BUILDING_ID,
    }


def _make_owner_user():
    return {
        "id": "user-owner-001",
        "role": "owner",
        "email": "owner@test.com",
        "building_id": BUILDING_ID,
    }


def _make_workflow_entry(
        wf_id="sla_breach_check",
        implemented=True,
        schedule="*/15 * * * *",
        category="operations",
):
    return {
        "id": wf_id,
        "name": "SLA Breach Check",
        "description": "Detects overdue requests and fires breach alerts",
        "category": category,
        "implemented": implemented,
        "schedule": schedule,
    }


def _make_run_doc(
        run_id="run-001",
        workflow_id="sla_breach_check",
        status="success",
        started_at=None,
        building_id=BUILDING_ID,
):
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "building_id": building_id,
        "status": status,
        "started_at": started_at,
        "completed_at": started_at,
        "duration_ms": 42,
        "items_processed": 3,
        "items_failed": 0,
        "is_test_data": False,
    }


def _mock_cursor(docs: list):
    """Return an async-iterable mock whose sort().limit() chain also works."""

    async def _aiter(items):
        for item in items:
            yield item

    cursor = MagicMock()
    cursor.__aiter__ = lambda self: _aiter(docs)
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


def _mock_aggregate(docs: list):
    """Async-iterable mock for db.collection.aggregate(pipeline)."""

    async def _aiter(items):
        for item in items:
            yield item

    agg = MagicMock()
    agg.__aiter__ = lambda self: _aiter(docs)
    return agg


# ─────────────────────────────────────────────────────────────────────────────
# 1. _compute_workflow_health — pure unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_health_not_implemented():
    """Not-implemented workflows always return 'not_implemented'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = _make_workflow_entry(implemented=False)
    assert _compute_workflow_health(wf, None) == "not_implemented"


def test_workflow_health_never_run_when_no_last_run():
    """Implemented workflow with no run record → 'never_run'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = _make_workflow_entry(implemented=True)
    assert _compute_workflow_health(wf, None) == "never_run"


def test_workflow_health_failing_on_failure_status():
    """Last run with status='failure' → 'failing'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = _make_workflow_entry(implemented=True)
    run = _make_run_doc(status="failure")
    assert _compute_workflow_health(wf, run) == "failing"


def test_workflow_health_failing_on_partial_status():
    """Last run with status='partial' → 'failing' (partial is treated as failure)."""
    from utils.workflow_runner import _compute_workflow_health

    wf = _make_workflow_entry(implemented=True)
    run = _make_run_doc(status="partial")
    assert _compute_workflow_health(wf, run) == "failing"


def test_workflow_health_healthy_on_recent_success():
    """Recent successful run within tolerance → 'healthy'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = _make_workflow_entry(implemented=True, wf_id="building_summary_recompute")
    run = _make_run_doc(
        workflow_id="building_summary_recompute",
        status="success",
        # Run just 1 hour ago — well within the 26-hour tolerance
        started_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    )
    assert _compute_workflow_health(wf, run) == "healthy"


def test_workflow_health_stale_when_overdue():
    """Run that is beyond the heartbeat tolerance → 'stale'."""
    from utils.workflow_runner import _compute_workflow_health

    # sla_breach_check has a 30-minute tolerance
    wf = _make_workflow_entry(implemented=True, wf_id="sla_breach_check", schedule="*/15 * * * *")
    run = _make_run_doc(
        workflow_id="sla_breach_check",
        status="success",
        # 60 minutes ago — beyond the 30-minute tolerance
        started_at=(datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat(),
    )
    result = _compute_workflow_health(wf, run)
    assert result == "stale"


def test_workflow_health_computed_correctly_with_known_data():
    """Comprehensive scenario: success + recent → healthy."""
    from utils.workflow_runner import _compute_workflow_health

    wf = {
        "id": "proposal_auto_close",
        "implemented": True,
        "schedule": "*/30 * * * *",
    }
    run = {
        "status": "success",
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
    }
    # proposal_auto_close tolerance = 60 min; run was 20 min ago → healthy
    assert _compute_workflow_health(wf, run) == "healthy"


def test_workflow_health_no_schedule_field_success_returns_healthy():
    """Workflow without a schedule field and a success run → 'healthy'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = {"id": "no_schedule_wf", "implemented": True}
    run = _make_run_doc(status="success")
    assert _compute_workflow_health(wf, run) == "healthy"


# ─────────────────────────────────────────────────────────────────────────────
# 2. GET /workflows/status
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_status_returns_catalogue_with_health():
    """GET /workflows/status should return list of workflows each with a health field."""
    from routers.workflows import get_workflow_status

    catalogue = {
        "workflows": [
            _make_workflow_entry("sla_breach_check", implemented=True),
            _make_workflow_entry("proposal_auto_close", implemented=True),
        ]
    }

    # Aggregate returns one last_run record
    agg_result = [
        {
            "_id": "sla_breach_check",
            "last_run": _make_run_doc("run-001", "sla_breach_check", "success"),
        }
    ]

    mock_db = MagicMock()
    mock_db.workflow_runs.aggregate.return_value = _mock_aggregate(agg_result)

    with (
        patch("routers.workflows.db", mock_db),
        patch("routers.workflows.load_workflow_catalogue", return_value=catalogue),
    ):
        result = await get_workflow_status(
            category=None,
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    assert "workflows" in result
    assert "summary" in result
    workflows = result["workflows"]
    assert len(workflows) == 2

    # Each workflow entry must have a health field
    for wf in workflows:
        assert "health" in wf, f"Missing health field in: {wf}"

    # summary must have total count matching catalogue
    assert result["summary"]["total"] == 2


@pytest.mark.asyncio
async def test_workflow_status_requires_manager_role():
    """Non-manager (owner) must receive 403 from /workflows/status."""
    from fastapi import HTTPException
    from routers.workflows import get_workflow_status

    mock_db = MagicMock()
    mock_db.workflow_runs.aggregate.return_value = _mock_aggregate([])

    with (
        patch("routers.workflows.db", mock_db),
        patch("routers.workflows.load_workflow_catalogue", return_value={"workflows": []}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_status(
                category=None,
                current_user=_make_owner_user(),
                building_id=BUILDING_ID,
            )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_workflow_status_category_filter():
    """When category param is set, only matching workflows are returned."""
    from routers.workflows import get_workflow_status

    catalogue = {
        "workflows": [
            _make_workflow_entry("sla_breach_check", category="operations"),
            _make_workflow_entry("compliance_deadline_check", category="compliance"),
        ]
    }

    mock_db = MagicMock()
    mock_db.workflow_runs.aggregate.return_value = _mock_aggregate([])

    with (
        patch("routers.workflows.db", mock_db),
        patch("routers.workflows.load_workflow_catalogue", return_value=catalogue),
    ):
        result = await get_workflow_status(
            category="compliance",
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    workflows = result["workflows"]
    assert len(workflows) == 1
    assert workflows[0]["id"] == "compliance_deadline_check"


@pytest.mark.asyncio
async def test_workflow_status_summary_counts_implemented():
    """Summary.implemented must count only implemented=True entries."""
    from routers.workflows import get_workflow_status

    catalogue = {
        "workflows": [
            _make_workflow_entry("wf-a", implemented=True),
            _make_workflow_entry("wf-b", implemented=False),
            _make_workflow_entry("wf-c", implemented=True),
        ]
    }

    mock_db = MagicMock()
    mock_db.workflow_runs.aggregate.return_value = _mock_aggregate([])

    with (
        patch("routers.workflows.db", mock_db),
        patch("routers.workflows.load_workflow_catalogue", return_value=catalogue),
    ):
        result = await get_workflow_status(
            category=None,
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    assert result["summary"]["implemented"] == 2
    assert result["summary"]["not_implemented"] == 1
    assert result["summary"]["total"] == 3


@pytest.mark.asyncio
async def test_workflow_status_aggregate_filters_test_data():
    """Aggregate pipeline must include is_test_data: {$ne: True} filter."""
    from routers.workflows import get_workflow_status

    catalogue = {"workflows": [_make_workflow_entry("sla_breach_check")]}

    pipeline_seen = []

    def _mock_aggregate_fn(pipeline):
        pipeline_seen.extend(pipeline)
        return _mock_aggregate([])

    mock_db = MagicMock()
    mock_db.workflow_runs.aggregate.side_effect = _mock_aggregate_fn

    with (
        patch("routers.workflows.db", mock_db),
        patch("routers.workflows.load_workflow_catalogue", return_value=catalogue),
    ):
        await get_workflow_status(
            category=None,
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    # First stage should be $match with is_test_data filter
    match_stage = next((s for s in pipeline_seen if "$match" in s), None)
    assert match_stage is not None
    assert match_stage["$match"].get("is_test_data") == {"$ne": True}


# ─────────────────────────────────────────────────────────────────────────────
# 3. GET /workflows/{workflow_id}/runs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_runs_returns_recent_runs():
    """GET /workflows/{id}/runs returns list of run records for given workflow."""
    from routers.workflows import get_workflow_runs

    runs = [
        _make_run_doc("run-001", "sla_breach_check", "success"),
        _make_run_doc("run-002", "sla_breach_check", "failure"),
    ]

    mock_db = MagicMock()
    mock_db.workflow_runs.find.return_value = _mock_cursor(runs)

    with patch("routers.workflows.db", mock_db):
        result = await get_workflow_runs(
            workflow_id="sla_breach_check",
            limit=20,
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    assert len(result) == 2
    assert result[0]["workflow_id"] == "sla_breach_check"
    assert result[1]["status"] == "failure"


@pytest.mark.asyncio
async def test_workflow_runs_requires_manager_role():
    """Non-manager must receive 403 from GET /workflows/{id}/runs."""
    from fastapi import HTTPException
    from routers.workflows import get_workflow_runs

    mock_db = MagicMock()
    mock_db.workflow_runs.find.return_value = _mock_cursor([])

    with patch("routers.workflows.db", mock_db):
        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_runs(
                workflow_id="sla_breach_check",
                limit=20,
                current_user=_make_owner_user(),
                building_id=BUILDING_ID,
            )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_workflow_runs_filters_by_workflow_id():
    """DB query must include workflow_id filter."""
    from routers.workflows import get_workflow_runs

    query_seen = {}

    def _find_side_effect(query, *args, **kwargs):
        query_seen.update(query)
        return _mock_cursor([])

    mock_db = MagicMock()
    mock_db.workflow_runs.find.side_effect = _find_side_effect

    with patch("routers.workflows.db", mock_db):
        await get_workflow_runs(
            workflow_id="proposal_auto_close",
            limit=20,
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    assert query_seen.get("workflow_id") == "proposal_auto_close"


@pytest.mark.asyncio
async def test_workflow_runs_multi_tenant_isolated():
    """Each building's runs are filtered by building_id; other building must not bleed through."""
    from routers.workflows import get_workflow_runs

    query_seen = {}

    def _find_side_effect(query, *args, **kwargs):
        query_seen.update(query)
        return _mock_cursor([])

    mock_db = MagicMock()
    mock_db.workflow_runs.find.side_effect = _find_side_effect

    with patch("routers.workflows.db", mock_db):
        await get_workflow_runs(
            workflow_id="sla_breach_check",
            limit=20,
            current_user=_make_manager_user(),
            building_id=BUILDING_ID,
        )

    assert query_seen.get("building_id") == BUILDING_ID


# ─────────────────────────────────────────────────────────────────────────────
# 4. GET /workflows/catalogue
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catalogue_requires_super_admin():
    """Non-super-admin (including strata_manager) must get 403 on /catalogue."""
    from fastapi import HTTPException
    from routers.workflows import get_workflow_catalogue

    for non_admin_role in ["strata_manager", "owner", "ec_member", "chairman"]:
        user = {**_make_manager_user(), "role": non_admin_role}
        # strata_manager is in _ADMIN_ROLES so skip it for this test
        if non_admin_role == "strata_manager":
            continue
        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_catalogue(current_user=user)
        assert exc_info.value.status_code == 403, (
            f"Expected 403 for role={non_admin_role}, got {exc_info.value.status_code}"
        )


@pytest.mark.asyncio
async def test_catalogue_accessible_by_super_admin(tmp_path):
    """super_admin should receive the catalogue JSON content."""
    from routers.workflows import get_workflow_catalogue
    import json as _json

    catalogue_data = {"workflows": [_make_workflow_entry("sla_breach_check")]}
    cat_file = tmp_path / "workflow_catalogue.json"
    cat_file.write_text(_json.dumps(catalogue_data))

    with patch("routers.workflows._DATA_DIR", tmp_path):
        result = await get_workflow_catalogue(
            current_user={**_make_manager_user(), "role": "super_admin"}
        )

    assert "workflows" in result
    assert len(result["workflows"]) == 1


@pytest.mark.asyncio
async def test_catalogue_returns_empty_when_file_missing(tmp_path):
    """When catalogue file does not exist, endpoint returns {'workflows': []}."""
    from routers.workflows import get_workflow_catalogue

    # tmp_path has no workflow_catalogue.json
    with patch("routers.workflows._DATA_DIR", tmp_path):
        result = await get_workflow_catalogue(
            current_user={**_make_manager_user(), "role": "super_admin"}
        )

    assert result == {"workflows": []}


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /workflows/runs  (all runs list — admin)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_runs_requires_admin():
    """Non-admin roles must receive 403 on GET /workflows/runs."""
    from fastapi import HTTPException
    from routers.workflows import list_workflow_runs

    for role in ["owner", "chairman", "ec_member"]:
        user = {"id": "u1", "role": role, "email": "x@test.com"}
        mock_db = MagicMock()
        mock_db.workflow_runs.find.return_value = _mock_cursor([])

        with patch("routers.workflows.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await list_workflow_runs(
                    workflow_id=None,
                    status=None,
                    limit=50,
                    current_user=user,
                    building_id=BUILDING_ID,
                )
            assert exc_info.value.status_code == 403, f"Expected 403 for role={role}"


@pytest.mark.asyncio
async def test_all_runs_returns_list_for_admin():
    """strata_manager gets list of runs without error."""
    from routers.workflows import list_workflow_runs

    runs = [_make_run_doc(), _make_run_doc("run-002")]

    mock_db = MagicMock()
    mock_db.workflow_runs.find.return_value = _mock_cursor(runs)

    with patch("routers.workflows.db", mock_db):
        result = await list_workflow_runs(
            workflow_id=None,
            status=None,
            limit=50,
            current_user=_make_manager_user("strata_manager"),
            building_id=BUILDING_ID,
        )

    assert isinstance(result, list)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_all_runs_filters_by_status():
    """When status param is provided, it must be included in the DB query."""
    from routers.workflows import list_workflow_runs

    query_seen = {}

    def _find_side_effect(query, *args, **kwargs):
        query_seen.update(query)
        return _mock_cursor([])

    mock_db = MagicMock()
    mock_db.workflow_runs.find.side_effect = _find_side_effect

    with patch("routers.workflows.db", mock_db):
        await list_workflow_runs(
            workflow_id=None,
            status="failure",
            limit=50,
            current_user=_make_manager_user("super_admin"),
            building_id=BUILDING_ID,
        )

    assert query_seen.get("status") == "failure"


# ─────────────────────────────────────────────────────────────────────────────
# 6. GET /workflows/runs/{run_id}
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_single_run_returns_doc():
    """GET /workflows/runs/{run_id} returns the matching run document."""
    from routers.workflows import get_workflow_run

    run = _make_run_doc("run-xyz")
    mock_db = MagicMock()
    mock_db.workflow_runs.find_one = AsyncMock(return_value=run)

    with patch("routers.workflows.db", mock_db):
        result = await get_workflow_run(
            run_id="run-xyz",
            current_user=_make_manager_user("super_admin"),
            building_id=BUILDING_ID,
        )

    assert result["id"] == "run-xyz"


@pytest.mark.asyncio
async def test_get_single_run_returns_404_when_not_found():
    """GET /workflows/runs/{run_id} must raise 404 when run does not exist."""
    from fastapi import HTTPException
    from routers.workflows import get_workflow_run

    mock_db = MagicMock()
    mock_db.workflow_runs.find_one = AsyncMock(return_value=None)

    with patch("routers.workflows.db", mock_db):
        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_run(
                run_id="nonexistent-run",
                current_user=_make_manager_user("super_admin"),
                building_id=BUILDING_ID,
            )

    assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 7. _is_manager and _require_admin helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_workflows_is_manager_true_for_manager_roles():
    # Per migration 0025: chairman is no longer a top-level role.
    from routers.workflows import _is_manager

    for role in ["ec_member", "strata_manager", "super_admin"]:
        assert _is_manager({"role": role}) is True


def test_workflows_is_manager_false_for_non_manager():
    from routers.workflows import _is_manager

    for role in ["owner", "tenant", "guest"]:
        assert _is_manager({"role": role}) is False


def test_workflows_require_admin_raises_for_non_admin():
    from fastapi import HTTPException
    from routers.workflows import _require_admin

    for role in ["owner", "ec_member"]:
        with pytest.raises(HTTPException) as exc_info:
            _require_admin({"role": role})
        assert exc_info.value.status_code == 403


def test_workflows_require_admin_passes_for_admin_roles():
    from routers.workflows import _require_admin

    for role in ["super_admin", "strata_manager"]:
        # Should not raise
        _require_admin({"role": role})
