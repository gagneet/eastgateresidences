# @featuretrace:scheduler — ReconciliationCloseWorkflow: durable Temporal saga for monthly period close with human-in-the-loop.
# Layer: worker
# Data flow: Temporal worker → ReconciliationCloseWorkflow → run_reconciliation_check / notify_reviewer_activity /
#             close_period_activity → financial_periods (building-scoped). State transition: OPEN → CLOSED.
# Related: backend/workers/temporal_worker.py (registers this workflow)
#           backend/domain/period_lifecycle.py (PeriodState + validate_transition)
#           backend/workers/merkle_seal.py (seal computed after close)
#           backend/workers/arq_settings.py (daily_merkle_seal_task runs post-close)
"""
NOT DEPLOYED (verified live 2026-08-11) — see backend/workers/temporal_worker.py's module
docstring. No Temporal server/worker runs anywhere in this deployment; this workflow has never
executed in production.

ReconciliationCloseWorkflow — durable saga for financial period close.

Workflow ID: `recon-close:{building_id}:{period_id}`

Steps:
  1. run_reconciliation_check  — verify journal total == bank statement total
  2a. If balanced: close_period_activity (transition state to CLOSED)
  2b. If discrepancy: notify_reviewer_activity → wait for `reviewer_approved`
      signal → close_period_activity

The workflow blocks indefinitely until a human sends the `reviewer_approved`
signal (via the POST /workflows/reconciliation/{period_id}/approve endpoint).
This is safer than a timeout-based auto-approve.

Production deployment: see docs/architecture/temporal-production-path.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

logger = logging.getLogger(__name__)

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=5,
)


# ── Input dataclass ───────────────────────────────────────────────────────────

@dataclass
class ReconciliationCloseInput:
    building_id: str
    period_id: str
    bank_statement_total_cents: int  # Expected total from bank statement
    reviewer_email: Optional[str] = None  # Who gets the discrepancy alert
    is_test_data: bool = False


# ── Activities ────────────────────────────────────────────────────────────────

@activity.defn
async def run_reconciliation_check(inp: ReconciliationCloseInput) -> Dict[str, Any]:
    """Compute the journal control total and compare with the bank statement total.

    Returns {"balanced": bool, "journal_total_cents": int,
             "bank_total_cents": int, "discrepancy_cents": int}.
    """
    from bson import ObjectId
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(inp.building_id)

    try:
        period_oid = ObjectId(inp.period_id)
    except Exception:
        period_oid = inp.period_id  # type: ignore[assignment]

    pipeline = [
        {
            "$match": {
                "building_id": inp.building_id,
                "period_id": period_oid,
                "status": "posted",
                "entry_type": "debit",
            }
        },
        {"$group": {"_id": None, "total": {"$sum": "$amount_cents"}}},
    ]
    result = await db._db.journal_entries.aggregate(pipeline).to_list(1)
    journal_total = (result[0]["total"] if result else 0)
    discrepancy = abs(journal_total - inp.bank_statement_total_cents)
    balanced = discrepancy == 0

    activity.logger.info(
        "run_reconciliation_check: period=%s journal=%d bank=%d discrepancy=%d balanced=%s",
        inp.period_id, journal_total, inp.bank_statement_total_cents, discrepancy, balanced,
    )

    return {
        "balanced": balanced,
        "journal_total_cents": journal_total,
        "bank_total_cents": inp.bank_statement_total_cents,
        "discrepancy_cents": discrepancy,
    }


@activity.defn
async def notify_reviewer_activity(inp: ReconciliationCloseInput, discrepancy_cents: int) -> None:
    """Send a discrepancy alert to the reviewer and create a bell notification."""
    import os
    from utils.email import send_email_async
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(inp.building_id)

    reviewer_email = inp.reviewer_email
    if not reviewer_email:
        # Fall back to all strata_managers for this building
        managers = await db._db.users.find(
            {"building_id": inp.building_id, "role": "strata_manager", "is_approved": True},
            {"email": 1},
        ).to_list(20)
        reviewer_email = managers[0]["email"] if managers else None

    if not reviewer_email:
        activity.logger.warning(
            "notify_reviewer_activity: no reviewer email for building %s", inp.building_id
        )
        return

    if inp.is_test_data:
        reviewer_email = os.getenv("TEST_EMAIL_INTERCEPT_ADDRESS", reviewer_email)

    await send_email_async(
        to_email=reviewer_email,
        subject=f"Reconciliation Discrepancy — Period {inp.period_id}",
        body=(
            f"A discrepancy of ${discrepancy_cents / 100:.2f} was detected between the journal "
            f"control total and bank statement for period {inp.period_id} "
            f"(building {inp.building_id}).\n\n"
            "Please review and approve the period close via the strata management portal."
        ),
    )
    activity.logger.info(
        "notify_reviewer_activity: alert sent to %s for period %s (discrepancy=%d cents)",
        reviewer_email, inp.period_id, discrepancy_cents,
    )


@activity.defn
async def close_period_activity(inp: ReconciliationCloseInput) -> Dict[str, Any]:
    """Transition the financial period to CLOSED state.

    Writes a closing audit log entry and updates the period document.
    """
    from bson import ObjectId
    from database import db
    from domain.period_lifecycle import PeriodState, validate_transition
    from datetime import datetime, timezone
    from request_context import set_ctx_building_id

    set_ctx_building_id(inp.building_id)

    try:
        period_oid = ObjectId(inp.period_id)
    except Exception:
        period_oid = inp.period_id  # type: ignore[assignment]

    period = await db._db.financial_periods.find_one(
        {"_id": period_oid, "building_id": inp.building_id}
    )
    if not period:
        raise ValueError(f"Period {inp.period_id} not found for building {inp.building_id}")

    current_state = PeriodState(period.get("state", "open"))
    validate_transition(current_state, PeriodState.CLOSED)

    now = datetime.now(timezone.utc)
    await db._db.financial_periods.update_one(
        {"_id": period_oid, "building_id": inp.building_id},
        {
            "$set": {
                "state": PeriodState.CLOSED.value,
                "closed_at": now,
                "closed_by": "reconciliation_workflow",
                "is_test_data": inp.is_test_data,
            }
        },
    )

    activity.logger.info(
        "close_period_activity: period %s for building %s transitioned to CLOSED",
        inp.period_id, inp.building_id,
    )
    return {
        "period_id": inp.period_id,
        "building_id": inp.building_id,
        "closed_at": now.isoformat(),
    }


# ── Workflow ──────────────────────────────────────────────────────────────────

@workflow.defn
class ReconciliationCloseWorkflow:
    """Durable saga for reconciliation and financial period close.

    Blocks on the `reviewer_approved` signal if a discrepancy is detected.
    A human reviewer must call POST /workflows/reconciliation/{period_id}/approve
    to advance the workflow past the discrepancy gate.
    """

    def __init__(self) -> None:
        """Generated function header.

        Function: ReconciliationCloseWorkflow.__init__
        Path: backend/workflows/reconciliation_close_workflow.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._reviewer_approved: bool = False
        self._reviewer_notes: str = ""

    @workflow.signal
    def reviewer_approved(self, notes: str = "") -> None:
        """Signal sent by a human reviewer to approve period close despite discrepancy."""
        self._reviewer_approved = True
        self._reviewer_notes = notes
        workflow.logger.info("reviewer_approved signal received: %s", notes)

    @workflow.run
    async def run(self, inp: ReconciliationCloseInput) -> Dict[str, Any]:
        """Generated function header.

        Function: ReconciliationCloseWorkflow.run
        Path: backend/workflows/reconciliation_close_workflow.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        workflow.logger.info(
            "ReconciliationCloseWorkflow: building=%s period=%s",
            inp.building_id, inp.period_id,
        )

        # Step 1: Run the three-way reconciliation check
        check = await workflow.execute_activity(
            run_reconciliation_check,
            inp,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )

        if not check["balanced"]:
            # Step 2b: Discrepancy — notify reviewer and wait for sign-off
            await workflow.execute_activity(
                notify_reviewer_activity,
                args=[inp, check["discrepancy_cents"]],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            # Block until human sends reviewer_approved signal
            await workflow.wait_condition(lambda: self._reviewer_approved)
            workflow.logger.info(
                "Discrepancy approved by reviewer. Notes: %s", self._reviewer_notes
            )

        # Step 2a / 3: Close the period
        close_result = await workflow.execute_activity(
            close_period_activity,
            inp,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )

        return {
            "building_id": inp.building_id,
            "period_id": inp.period_id,
            "balanced": check["balanced"],
            "discrepancy_cents": check["discrepancy_cents"],
            "reviewer_approved": self._reviewer_approved,
            "reviewer_notes": self._reviewer_notes,
            "closed_at": close_result["closed_at"],
        }
