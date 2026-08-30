"""
Tests for Workflow Governance Engine (S2).

Tests:
  - test_workflow_catalogue_loads_correctly
  - test_all_implemented_workflows_have_test_coverage_field
  - test_workflow_run_context_manager_writes_success_record
  - test_workflow_run_context_manager_writes_failure_on_exception
  - test_workflow_run_artefacts_logged_correctly
  - test_workflow_status_api_returns_all_catalogue_entries
  - test_workflow_status_enriched_with_last_run_data
  - test_stale_workflow_flagged_correctly
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Catalogue loading
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_catalogue_loads_correctly():
    """The catalogue loads without error and contains at least 10 workflows."""
    from utils.workflow_runner import reload_workflow_catalogue

    catalogue = reload_workflow_catalogue()
    assert isinstance(catalogue, dict)
    workflows = catalogue.get("workflows", [])
    assert len(workflows) >= 10, f"Expected >= 10 workflows, got {len(workflows)}"
    # Each workflow must have the mandatory fields
    for wf in workflows:
        assert "id" in wf, f"Missing 'id' in workflow: {wf}"
        assert "name" in wf, f"Missing 'name' in workflow: {wf}"
        assert "category" in wf, f"Missing 'category' in workflow: {wf}"
        assert "implemented" in wf, f"Missing 'implemented' in workflow: {wf}"


def test_all_implemented_workflows_have_test_coverage_field():
    """Every implemented workflow has a non-empty test_coverage field."""
    from utils.workflow_runner import reload_workflow_catalogue

    catalogue = reload_workflow_catalogue()
    failures = []
    for wf in catalogue.get("workflows", []):
        if wf.get("implemented"):
            if not wf.get("test_coverage"):
                failures.append(wf["id"])
    assert not failures, (
        f"Implemented workflows missing test_coverage: {failures}"
    )


def test_catalogue_has_financial_and_governance_categories():
    """Catalogue contains at least one financial and one governance workflow."""
    from utils.workflow_runner import reload_workflow_catalogue

    catalogue = reload_workflow_catalogue()
    categories = {wf["category"] for wf in catalogue.get("workflows", [])}
    assert "financial" in categories
    assert "governance" in categories


# ─────────────────────────────────────────────────────────────────────────────
# 2. WorkflowRunContext manager
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_run_context_manager_writes_success_record():
    """workflow_run writes a success record when block completes without error."""
    mock_db = MagicMock()
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("utils.workflow_runner.db", mock_db),
        patch("utils.workflow_runner.set_ctx_building_id"),
    ):
        from utils.workflow_runner import workflow_run

        async with workflow_run("sla_breach_check", BUILDING_ID, "test_run") as run:
            run.items_processed = 3

    mock_db.workflow_runs.insert_one.assert_called_once()
    call_arg = mock_db.workflow_runs.insert_one.call_args[0][0]
    assert call_arg["workflow_id"] == "sla_breach_check"
    assert call_arg["building_id"] == BUILDING_ID
    assert call_arg["status"] == "success"
    assert call_arg["items_processed"] == 3
    assert call_arg["items_failed"] == 0
    assert call_arg["error_detail"] is None
    assert call_arg["is_test_data"] is False
    assert "started_at" in call_arg
    assert "completed_at" in call_arg
    assert call_arg["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_workflow_run_context_manager_writes_failure_on_exception():
    """workflow_run writes a failure record and re-raises when block raises."""
    mock_db = MagicMock()
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("utils.workflow_runner.db", mock_db),
        patch("utils.workflow_runner.set_ctx_building_id"),
    ):
        from utils.workflow_runner import workflow_run

        with pytest.raises(ValueError, match="test error"):
            async with workflow_run("building_summary_recompute", BUILDING_ID, "test_fail") as run:
                run.items_processed = 1
                raise ValueError("test error")

    call_arg = mock_db.workflow_runs.insert_one.call_args[0][0]
    assert call_arg["status"] == "failure"
    assert "test error" in call_arg["error_detail"]
    assert call_arg["items_processed"] == 1


@pytest.mark.asyncio
async def test_workflow_run_partial_status_when_items_failed():
    """workflow_run records 'partial' status when items_failed > 0."""
    mock_db = MagicMock()
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("utils.workflow_runner.db", mock_db),
        patch("utils.workflow_runner.set_ctx_building_id"),
    ):
        from utils.workflow_runner import workflow_run

        async with workflow_run("compliance_deadline_check", BUILDING_ID, "test_partial") as run:
            run.items_processed = 5
            run.items_failed = 2

    call_arg = mock_db.workflow_runs.insert_one.call_args[0][0]
    assert call_arg["status"] == "partial"
    assert call_arg["items_processed"] == 5
    assert call_arg["items_failed"] == 2


@pytest.mark.asyncio
async def test_workflow_run_artefacts_logged_correctly():
    """add_artefact records artefacts in 'type:id' format."""
    mock_db = MagicMock()
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("utils.workflow_runner.db", mock_db),
        patch("utils.workflow_runner.set_ctx_building_id"),
    ):
        from utils.workflow_runner import workflow_run

        async with workflow_run("parcel_arrival_notification", BUILDING_ID, "parcel:abc-123") as run:
            run.add_artefact("notification", "notif-001")
            run.add_artefact("email", "email-042")
            run.items_processed = 1

    call_arg = mock_db.workflow_runs.insert_one.call_args[0][0]
    assert "notification:notif-001" in call_arg["artefacts_created"]
    assert "email:email-042" in call_arg["artefacts_created"]
    assert len(call_arg["artefacts_created"]) == 2


@pytest.mark.asyncio
async def test_workflow_run_test_data_flag_passthrough():
    """is_test_data=True is written to the record when set."""
    mock_db = MagicMock()
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("utils.workflow_runner.db", mock_db),
        patch("utils.workflow_runner.set_ctx_building_id"),
    ):
        from utils.workflow_runner import workflow_run

        async with workflow_run("sla_breach_check", BUILDING_ID, is_test_data=True) as run:
            run.items_processed = 0

    call_arg = mock_db.workflow_runs.insert_one.call_args[0][0]
    assert call_arg["is_test_data"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Health computation
# ─────────────────────────────────────────────────────────────────────────────


def test_not_implemented_workflow_health():
    """Unimplemented workflows are flagged as 'not_implemented'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = {"id": "nsw_strata_hub_reminder", "implemented": False}
    assert _compute_workflow_health(wf, None) == "not_implemented"


def test_healthy_workflow_recent_run():
    """A workflow with a recent successful run is 'healthy'."""
    from utils.workflow_runner import _compute_workflow_health

    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    wf = {
        "id": "sla_breach_check",
        "implemented": True,
        "schedule": "*/15 * * * *",
    }
    last_run = {"status": "success", "started_at": recent}
    assert _compute_workflow_health(wf, last_run) == "healthy"


def test_stale_workflow_flagged_correctly():
    """A workflow that hasn't run within its tolerance window is 'stale'."""
    from utils.workflow_runner import _compute_workflow_health, _HEARTBEAT_TOLERANCE

    # sla_breach_check tolerance = 30 minutes; simulate a run 60 minutes ago
    old_run_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    wf = {
        "id": "sla_breach_check",
        "implemented": True,
        "schedule": "*/15 * * * *",
    }
    last_run = {"status": "success", "started_at": old_run_time}
    # Confirm tolerance is defined
    assert "sla_breach_check" in _HEARTBEAT_TOLERANCE
    assert _compute_workflow_health(wf, last_run) == "stale"


def test_failing_workflow_health():
    """A workflow whose last run failed is 'failing'."""
    from utils.workflow_runner import _compute_workflow_health

    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    wf = {"id": "proposal_auto_close", "implemented": True, "schedule": "*/30 * * * *"}
    last_run = {"status": "failure", "started_at": recent}
    assert _compute_workflow_health(wf, last_run) == "failing"


def test_never_run_implemented_workflow():
    """An implemented workflow with no last_run is 'never_run'."""
    from utils.workflow_runner import _compute_workflow_health

    wf = {"id": "compliance_deadline_check", "implemented": True, "schedule": "0 22 * * 0"}
    assert _compute_workflow_health(wf, None) == "never_run"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Workflow status API (via routers/workflows.py — mocked)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_status_api_returns_all_catalogue_entries():
    """get_workflow_status returns one entry per catalogue workflow."""
    from utils.workflow_runner import reload_workflow_catalogue

    catalogue = reload_workflow_catalogue()
    expected_count = len(catalogue.get("workflows", []))

    mock_db = MagicMock()

    # Simulate empty aggregate (no prior runs)
    async def _empty_agg(*a, **kw):
        return
        yield  # make it an async generator

    mock_db.workflow_runs.aggregate.return_value = _empty_agg()

    with patch("routers.workflows.db", mock_db):
        from routers.workflows import get_workflow_status

        manager_user = {
            "id": "user-mgr-001",
            "role": "strata_manager",
            "effective_role": "strata_manager",
        }
        result = await get_workflow_status(
            category=None,
            current_user=manager_user,
            building_id=BUILDING_ID,
        )

    assert len(result["workflows"]) == expected_count
    assert "summary" in result
    assert result["summary"]["total"] == expected_count


@pytest.mark.asyncio
async def test_workflow_status_enriched_with_last_run_data():
    """get_workflow_status enriches workflows with last_run data from DB."""
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    mock_run = {
        "workflow_id": "sla_breach_check",
        "status": "success",
        "started_at": recent,
        "items_processed": 3,
        "duration_ms": 142,
    }

    mock_db = MagicMock()

    # Simulate aggregate returning one last_run
    async def _agg_result(*a, **kw):
        yield {"_id": "sla_breach_check", "last_run": mock_run}

    mock_db.workflow_runs.aggregate.return_value = _agg_result()

    with patch("routers.workflows.db", mock_db):
        from routers.workflows import get_workflow_status

        manager_user = {"id": "user-mgr-001", "role": "strata_manager"}
        result = await get_workflow_status(
            category=None,
            current_user=manager_user,
            building_id=BUILDING_ID,
        )

    # Find the sla_breach_check entry
    sla_wf = next((w for w in result["workflows"] if w["id"] == "sla_breach_check"), None)
    assert sla_wf is not None
    assert sla_wf["last_run"] is not None
    assert sla_wf["last_run"]["status"] == "success"
    assert sla_wf["health"] == "healthy"


@pytest.mark.asyncio
async def test_workflow_status_requires_manager_role():
    """get_workflow_status returns 403 for non-manager users."""
    from fastapi import HTTPException
    from routers.workflows import get_workflow_status

    owner_user = {"id": "user-owner-001", "role": "owner"}
    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_status(
            category=None,
            current_user=owner_user,
            building_id=BUILDING_ID,
        )
    assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 5. SLA override endpoint (new in this session)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_workflow_sla_stores_override():
    """PUT /workflows/{id}/sla saves an override document."""
    override_doc = None

    async def _mock_update(filter_q, update, upsert=False):
        nonlocal override_doc
        override_doc = update["$set"]

    mock_db = MagicMock()
    mock_db.workflow_sla_overrides.update_one = _mock_update

    with patch("routers.workflows.db", mock_db):
        from routers.workflows import set_workflow_sla, SLAOverrideRequest
        admin_user = {"id": "user-admin", "role": "super_admin"}
        result = await set_workflow_sla(
            workflow_id="sla_breach_check",
            body=SLAOverrideRequest(sla_hours=12.0),
            current_user=admin_user,
            building_id=BUILDING_ID,
        )

    assert result["sla_hours"] == 12.0
    assert override_doc is not None
    assert override_doc["sla_hours"] == 12.0
    assert override_doc["building_id"] == BUILDING_ID


@pytest.mark.asyncio
async def test_set_workflow_sla_rejects_negative():
    """Negative sla_hours should raise 400."""
    from fastapi import HTTPException
    from routers.workflows import set_workflow_sla, SLAOverrideRequest

    admin_user = {"id": "user-admin", "role": "super_admin"}
    with pytest.raises(HTTPException) as exc_info:
        await set_workflow_sla(
            workflow_id="sla_breach_check",
            body=SLAOverrideRequest(sla_hours=-5),
            current_user=admin_user,
            building_id=BUILDING_ID,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_workflow_sla_requires_admin():
    """Owner role should receive 403."""
    from fastapi import HTTPException
    from routers.workflows import set_workflow_sla, SLAOverrideRequest

    owner_user = {"id": "user-owner", "role": "owner"}
    with pytest.raises(HTTPException) as exc_info:
        await set_workflow_sla(
            workflow_id="sla_breach_check",
            body=SLAOverrideRequest(sla_hours=8),
            current_user=owner_user,
            building_id=BUILDING_ID,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_workflow_sla_returns_override_when_present():
    """GET /workflows/{id}/sla returns source=override when an override exists."""
    mock_db = MagicMock()
    mock_db.workflow_sla_overrides.find_one = AsyncMock(return_value={
        "building_id": BUILDING_ID,
        "workflow_id": "sla_breach_check",
        "sla_hours": 6.0,
    })

    with patch("routers.workflows.db", mock_db):
        from routers.workflows import get_workflow_sla
        manager_user = {"id": "user-mgr", "role": "strata_manager"}
        result = await get_workflow_sla(
            workflow_id="sla_breach_check",
            current_user=manager_user,
            building_id=BUILDING_ID,
        )

    assert result["sla_hours"] == 6.0
    assert result["source"] == "override"


@pytest.mark.asyncio
async def test_get_workflow_sla_returns_catalogue_default_when_no_override():
    """GET /workflows/{id}/sla returns source=catalogue when no override exists."""
    mock_db = MagicMock()
    mock_db.workflow_sla_overrides.find_one = AsyncMock(return_value=None)

    with patch("routers.workflows.db", mock_db):
        from routers.workflows import get_workflow_sla
        manager_user = {"id": "user-mgr", "role": "strata_manager"}
        result = await get_workflow_sla(
            workflow_id="sla_breach_check",
            current_user=manager_user,
            building_id=BUILDING_ID,
        )

    assert result["source"] == "catalogue"
    assert isinstance(result["sla_hours"], (int, float))


# ─────────────────────────────────────────────────────────────────────────────
# 6. Manual run endpoint (new in this session)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_workflow_run_creates_run_record():
    """POST /workflows/{id}/run should insert a workflow_runs document."""
    inserted = []

    async def _mock_insert(doc):
        inserted.append(doc)

    mock_db = MagicMock()
    mock_db.workflow_runs.insert_one = _mock_insert
    mock_db.workflow_runs.update_one = AsyncMock()

    with patch("routers.workflows.db", mock_db):
        from routers.workflows import trigger_workflow_run
        admin_user = {
            "id": "user-admin",
            "role": "super_admin",
            "effective_role": "super_admin",
            "email": "test@example.com",
        }
        result = await trigger_workflow_run(
            workflow_id="sla_breach_check",
            current_user=admin_user,
            building_id=BUILDING_ID,
        )

    assert len(inserted) == 1
    doc = inserted[0]
    assert doc["workflow_id"] == "sla_breach_check"
    assert doc["trigger_type"] == "manual"
    assert doc["building_id"] == BUILDING_ID
    assert "run_id" in result


@pytest.mark.asyncio
async def test_trigger_workflow_run_fails_for_unimplemented():
    """POST /workflows/{id}/run returns 400 for unimplemented workflows."""
    from fastapi import HTTPException
    from routers.workflows import trigger_workflow_run

    admin_user = {"id": "user-admin", "role": "super_admin", "effective_role": "super_admin"}
    with pytest.raises(HTTPException) as exc_info:
        await trigger_workflow_run(
            workflow_id="lease_expiry_alerts",  # not yet implemented
            current_user=admin_user,
            building_id=BUILDING_ID,
        )
    assert exc_info.value.status_code == 400
    assert "not yet implemented" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_trigger_workflow_run_returns_404_for_unknown():
    """POST /workflows/unknown/run returns 404."""
    from fastapi import HTTPException
    from routers.workflows import trigger_workflow_run

    admin_user = {"id": "user-admin", "role": "super_admin", "effective_role": "super_admin"}
    with pytest.raises(HTTPException) as exc_info:
        await trigger_workflow_run(
            workflow_id="totally_fake_workflow_xyz",
            current_user=admin_user,
            building_id=BUILDING_ID,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_trigger_workflow_run_denied_for_owner():
    """POST /workflows/{id}/run returns 403 for owner role."""
    from fastapi import HTTPException
    from routers.workflows import trigger_workflow_run

    owner_user = {"id": "user-owner", "role": "owner", "effective_role": "owner"}
    with pytest.raises(HTTPException) as exc_info:
        await trigger_workflow_run(
            workflow_id="sla_breach_check",
            current_user=owner_user,
            building_id=BUILDING_ID,
        )
    assert exc_info.value.status_code == 403
