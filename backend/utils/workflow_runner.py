"""
Workflow Runner — async context manager for observable, logged automations.

Every automated workflow execution wrapped in `workflow_run()` writes a
`workflow_runs` document recording the outcome, duration, items processed,
and artefacts created.

Usage:
    async with workflow_run("sla_breach_check", building_id, "scheduled_15min") as run:
        run.trigger_type = "scheduled"
        # ... do work ...
        run.items_processed = len(breached)
        run.add_artefact("notification", notif_id)

The workflow_runs collection powers the observability dashboard at
/admin/workflows.
"""

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import logging
from typing import Optional

from database import db

try:
    from request_context import set_ctx_building_id
except ImportError:
    def set_ctx_building_id(_: str) -> None:  # noqa: D401
        """No-op stub used in test environments without request_context."""

logger = logging.getLogger(__name__)

# Path to the workflow catalogue (source of truth for workflow metadata)
_CATALOGUE_PATH = Path(__file__).parent.parent / "data" / "workflow_catalogue.json"
_catalogue_cache: Optional[dict] = None


def load_workflow_catalogue() -> dict:
    """
    Load the workflow catalogue from disk. Cached after first load.
    Returns the full catalogue dict with a `workflows` list.
    """
    global _catalogue_cache
    if _catalogue_cache is None:
        try:
            with open(_CATALOGUE_PATH, "r", encoding="utf-8") as f:
                _catalogue_cache = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load workflow catalogue: %s", exc)
            _catalogue_cache = {"workflows": [], "version": "0.0"}
    return _catalogue_cache


def reload_workflow_catalogue() -> dict:
    """Force reload the catalogue from disk (useful in tests)."""
    global _catalogue_cache
    _catalogue_cache = None
    return load_workflow_catalogue()


class WorkflowRunContext:
    """
    Mutable context object passed to the `async with workflow_run(...)` block.
    Callers update `items_processed`, `items_failed`, and call `add_artefact()`.
    """

    def __init__(self, run_id: str, workflow_id: str, building_id: str) -> None:
        """Generated function header.

        Function: WorkflowRunContext.__init__
        Path: backend/utils/workflow_runner.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.building_id = building_id
        self.trigger_type: str = "scheduled"
        self.items_processed: int = 0
        self.items_failed: int = 0
        self.artefacts: list = []

    def add_artefact(self, artefact_type: str, artefact_id: str) -> None:
        """Record a created artefact. Example: run.add_artefact('notification', notif_id)"""
        self.artefacts.append(f"{artefact_type}:{artefact_id}")


@asynccontextmanager
async def workflow_run(
        workflow_id: str,
        building_id: str,
        trigger_detail: str = "",
        is_test_data: bool = False,
):
    """
    Async context manager that wraps any automation function and writes a
    workflow_runs record regardless of success or failure.

    Args:
        workflow_id:    ID from workflow_catalogue.json (e.g. "sla_breach_check")
        building_id:    Building this run applies to (e.g. "13195")
        trigger_detail: Free-text trigger context (e.g. "scheduled_15min" or "parcel:abc-123")
        is_test_data:   Set True in tests so records are filtered from production queries

    Yields:
        WorkflowRunContext — update items_processed/items_failed/artefacts inside the block

    Raises:
        Re-raises any exception from within the block after writing the failure record.
    """
    set_ctx_building_id(building_id)

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    start_ms = time.monotonic_ns() // 1_000_000

    run = WorkflowRunContext(run_id, workflow_id, building_id)
    status = "success"
    error_detail = None

    try:
        yield run
        status = "success" if run.items_failed == 0 else "partial"
    except Exception as exc:
        status = "failure"
        error_detail = str(exc)
        logger.error("Workflow %s failed in building %s: %s", workflow_id, building_id, exc)
        raise
    finally:
        completed_at = datetime.now(timezone.utc)
        duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

        try:
            await db.workflow_runs.insert_one(
                {
                    "id": run_id,
                    "building_id": building_id,
                    "workflow_id": workflow_id,
                    "trigger_type": run.trigger_type,
                    "trigger_detail": trigger_detail,
                    "status": status,
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "duration_ms": duration_ms,
                    "items_processed": run.items_processed,
                    "items_failed": run.items_failed,
                    "error_detail": error_detail,
                    "artefacts_created": run.artefacts,
                    "is_test_data": is_test_data,
                }
            )
        except Exception as write_exc:
            # Logging only — never let the observability layer mask the real error
            logger.error(
                "Failed to write workflow_run record for %s/%s: %s",
                workflow_id,
                building_id,
                write_exc,
            )


def _compute_workflow_health(
        workflow: dict,
        last_run: Optional[dict],
) -> str:
    """
    Compute health status for a workflow based on last run and schedule.

    Returns one of:
      "healthy"         — last run succeeded and is within expected interval
      "stale"           — last run succeeded but is overdue for next run
      "failing"         — last run failed or partial
      "never_run"       — no run record exists (but workflow is implemented)
      "not_implemented" — workflow.implemented is False
    """
    if not workflow.get("implemented", False):
        return "not_implemented"

    if not last_run:
        return "never_run"

    if last_run.get("status") == "failure":
        return "failing"

    if last_run.get("status") == "partial":
        return "failing"

    # Check staleness for scheduled workflows
    schedule = workflow.get("schedule")
    if schedule and last_run.get("started_at"):
        try:
            started = datetime.fromisoformat(last_run["started_at"])
            now = datetime.now(timezone.utc)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age_minutes = (now - started).total_seconds() / 60

            # Use HEARTBEAT_TOLERANCE if available, otherwise derive from schedule
            tolerance = _HEARTBEAT_TOLERANCE.get(workflow["id"])
            if tolerance and age_minutes > tolerance:
                return "stale"
        except (ValueError, TypeError):
            pass

    if last_run.get("status") == "success":
        return "healthy"

    return "never_run"


# Heartbeat tolerance (minutes) per workflow — how long before a scheduled
# workflow is considered "stale" (i.e. missed its expected run window).
_HEARTBEAT_TOLERANCE = {
    "levy_run_quarterly": 90 * 24 * 60,  # quarterly — very wide tolerance
    "building_summary_recompute": 26 * 60,  # daily + 2h
    "bi_analytics_etl": 26 * 60,  # daily + 2h
    "sla_breach_check": 30,  # 15-min schedule, 30-min tolerance
    "compliance_deadline_check": 10080 + 60,  # weekly + 1h
    "ppm_due_notification": 26 * 60,
    "lease_expiry_alerts": 26 * 60,
    "proposal_auto_close": 60,  # 30-min schedule, 1h tolerance
    "delete_expired_guest_tokens": 26 * 60,
    "workflow_heartbeat_monitor": 20,  # 10-min schedule, 20-min tolerance
    "insurance_renewal_reminder": 10080 + 60,
    "arrears_notice_tier1": 10080 + 60,
}
