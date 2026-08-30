"""
Workflow Governance Router — observability and status API.

Endpoints:
  GET  /workflows/status          — catalogue enriched with last-run data (manager+)
  GET  /workflows/{id}/runs       — recent runs for a specific workflow
  GET  /workflows/catalogue       — raw catalogue (super_admin only)
  GET  /workflows/runs            — list all workflow runs (admin)
  GET  /workflows/runs/{run_id}   — get a specific run (admin)
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from database import db
from models.user import UserRole
from utils.auth import get_current_user, get_current_building, effective_role
from utils.workflow_runner import load_workflow_catalogue, _compute_workflow_health

router = APIRouter(prefix="/workflows", tags=["Workflow Governance"])
logger = logging.getLogger(__name__)

_MANAGER_ROLES = {
    UserRole.EC_MEMBER,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}

_ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER}
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _is_manager(user: dict) -> bool:
    """Returns True if the user holds a management role."""
    return effective_role(user) in _MANAGER_ROLES


def _require_admin(user: dict):
    """Generated function header.

    Function: _require_admin
    Path: backend/routers/workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")


# ---------------------------------------------------------------------------
# GET /workflows/status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Workflow governance status — catalogue enriched with last run data",
)
async def get_workflow_status(
        category: Optional[str] = Query(None, description="Filter by category"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Returns the workflow catalogue enriched with the last run record for each
    workflow in the given building. Computes health status for each.

    Used by the manager observability dashboard and investor reporting.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Manager access required")

    catalogue = load_workflow_catalogue()
    workflows = catalogue.get("workflows", [])

    if category:
        workflows = [w for w in workflows if w.get("category") == category]

    # Batch-fetch last runs: one query per workflow would be N+1
    workflow_ids = [w["id"] for w in workflows]

    # Get latest run per workflow_id using aggregation
    pipeline = [
        {
            "$match": {
                "workflow_id": {"$in": workflow_ids},
                "building_id": building_id,
                "is_test_data": {"$ne": True},
            }
        },
        {"$sort": {"started_at": -1}},
        {
            "$group": {
                "_id": "$workflow_id",
                "last_run": {"$first": "$$ROOT"},
            }
        },
    ]

    last_runs: dict = {}
    async for doc in db.workflow_runs.aggregate(pipeline):
        doc["last_run"].pop("_id", None)
        last_runs[doc["_id"]] = doc["last_run"]

    # Enrich each workflow with last run + health
    results = []
    for wf in workflows:
        last_run = last_runs.get(wf["id"])
        health = _compute_workflow_health(wf, last_run)
        results.append(
            {
                **wf,
                "last_run": last_run,
                "health": health,
            }
        )

    # Summary counts
    total = len(results)
    implemented = sum(1 for r in results if r.get("implemented"))
    summary = {
        "total": total,
        "implemented": implemented,
        "not_implemented": total - implemented,
        "healthy": sum(1 for r in results if r.get("health") == "healthy"),
        "stale": sum(1 for r in results if r.get("health") == "stale"),
        "failing": sum(1 for r in results if r.get("health") == "failing"),
        "never_run": sum(1 for r in results if r.get("health") == "never_run"),
        "coverage_pct": round(implemented / total * 100, 1) if total else 0.0,
    }

    return {"workflows": results, "summary": summary}


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_id}/runs
# ---------------------------------------------------------------------------


@router.get(
    "/{workflow_id}/runs",
    summary="Recent runs for a specific workflow",
)
async def get_workflow_runs(
        workflow_id: str,
        limit: int = Query(20, le=100),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Returns the most recent workflow_runs records for a given workflow ID,
    ordered newest-first.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Manager access required")

    results = []
    cursor = (
        db.workflow_runs.find(
            {
                "workflow_id": workflow_id,
                "building_id": building_id,
                "is_test_data": {"$ne": True},
            },
            {"_id": 0},
        )
        .sort("started_at", -1)
        .limit(limit)
    )
    async for doc in cursor:
        results.append(doc)

    return results


# ---------------------------------------------------------------------------
# GET /workflows/catalogue  (raw — super_admin only)
# ---------------------------------------------------------------------------


@router.get(
    "/catalogue",
    summary="Raw workflow catalogue (super_admin only)",
)
async def get_workflow_catalogue(
        current_user: dict = Depends(get_current_user),
):
    """Returns the raw workflow_catalogue.json content."""
    _require_admin(current_user)

    catalogue_path = _DATA_DIR / "workflow_catalogue.json"
    if not catalogue_path.exists():
        return {"workflows": []}

    with open(catalogue_path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# GET /workflows/runs  (list all runs — admin)
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_workflow_runs(
        workflow_id: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Returns history of workflow executions."""
    _require_admin(current_user)

    query = {"is_test_data": {"$ne": True}}
    if workflow_id:
        query["workflow_id"] = workflow_id
    if status:
        query["status"] = status

    cursor = db.workflow_runs.find(query, {"_id": 0}).sort("started_at", -1).limit(limit)

    results = []
    async for doc in cursor:
        results.append(doc)
    return results


@router.get("/runs/{run_id}")
async def get_workflow_run(
        run_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Returns details of a specific workflow execution."""
    _require_admin(current_user)

    doc = await db.workflow_runs.find_one({"id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    return doc


# ---------------------------------------------------------------------------
# PUT /workflows/{workflow_id}/sla  — per-building SLA override
# ---------------------------------------------------------------------------

class SLAOverrideRequest(BaseModel):
    sla_hours: float


@router.put(
    "/{workflow_id}/sla",
    summary="Set a per-building SLA override for a workflow",
)
async def set_workflow_sla(
        workflow_id: str,
        body: SLAOverrideRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Strata managers and above can override the default SLA for a workflow
    on a per-building basis. Stored in workflow_sla_overrides collection.
    """
    if effective_role(current_user) not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Strata manager or admin access required")

    if body.sla_hours < 0:
        raise HTTPException(status_code=400, detail="sla_hours must be non-negative")

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "building_id": building_id,
        "workflow_id": workflow_id,
        "sla_hours": body.sla_hours,
        "updated_by": current_user.get("email") or current_user.get("id"),
        "updated_at": now,
    }
    await db.workflow_sla_overrides.update_one(
        {"building_id": building_id, "workflow_id": workflow_id},
        {"$set": doc},
        upsert=True,
    )
    return {"status": "ok", "workflow_id": workflow_id, "sla_hours": body.sla_hours}


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_id}/sla  — get effective SLA (override or default)
# ---------------------------------------------------------------------------

@router.get(
    "/{workflow_id}/sla",
    summary="Get effective SLA for a workflow (override or catalogue default)",
)
async def get_workflow_sla(
        workflow_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_workflow_sla
    Path: backend/routers/workflows.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Manager access required")

    override = await db.workflow_sla_overrides.find_one(
        {"building_id": building_id, "workflow_id": workflow_id},
        {"_id": 0},
    )
    if override:
        return {"workflow_id": workflow_id, "sla_hours": override["sla_hours"], "source": "override"}

    catalogue = load_workflow_catalogue()
    wf = next((w for w in catalogue.get("workflows", []) if w["id"] == workflow_id), None)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found in catalogue")

    return {"workflow_id": workflow_id, "sla_hours": wf.get("sla_hours", 24), "source": "catalogue"}


# ---------------------------------------------------------------------------
# POST /workflows/{workflow_id}/run  — manual trigger
# ---------------------------------------------------------------------------

_RUN_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER}


@router.post(
    "/{workflow_id}/run",
    summary="Manually trigger a workflow (super_admin / strata_manager / chairman only)",
)
async def trigger_workflow_run(
        workflow_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Records a manual workflow trigger in workflow_runs. For implemented workflows
    this also attempts to dispatch the workflow via the workflow_runner utility.
    Unimplemented workflows return 400.
    """
    if effective_role(current_user) not in _RUN_ROLES:
        raise HTTPException(status_code=403, detail="Strata manager or admin access required")

    catalogue = load_workflow_catalogue()
    wf = next((w for w in catalogue.get("workflows", []) if w["id"] == workflow_id), None)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found in catalogue")
    if not wf.get("implemented"):
        raise HTTPException(
            status_code=400,
            detail=f"Workflow '{workflow_id}' is not yet implemented and cannot be triggered manually",
        )

    now = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    run_doc = {
        "id": run_id,
        "workflow_id": workflow_id,
        "building_id": building_id,
        "status": "queued",
        "trigger_type": "manual",
        "trigger_detail": f"Manually triggered by {current_user.get('email', 'unknown')}",
        "started_at": now.isoformat(),
        "items_processed": 0,
        "duration_ms": None,
        "error_detail": None,
        "is_test_data": False,
    }
    await db.workflow_runs.insert_one(run_doc)

    # Attempt to dispatch the workflow via its registered cron function (best-effort).
    # Each workflow's cron module must expose a `run(building_id: str)` coroutine.
    dispatched = False
    try:
        module_path = wf.get("module")
        func_name = wf.get("function", "run")
        if module_path:
            import importlib
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name, None)
            if fn:
                await fn(building_id)
                dispatched = True
    except Exception as exc:
        logger.warning("Workflow dispatch for %s raised: %s", workflow_id, exc)
        await db.workflow_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "failure", "error_detail": str(exc)}},
        )
        return {"status": "queued_with_error", "run_id": run_id, "detail": str(exc)}

    if dispatched:
        await db.workflow_runs.update_one(
            {"id": run_id}, {"$set": {"status": "success"}}
        )

    return {"status": "queued" if not dispatched else "success", "run_id": run_id}
