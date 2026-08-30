"""
Scheduler — runs periodic background jobs.

Jobs:
  - recompute_all_building_summaries  (daily 02:00 AEST)
  - run_bi_analytics_etl              (daily 02:15 AEST)
  - nightly_merkle_seal               (daily 01:00 AEST)
  - check_sla_breaches                (every 15 minutes)
  - check_workflow_heartbeats         (every 10 minutes)
  - lease_expiry_alerts               (daily 07:00 AEST)
  - proposal_auto_close               (every 30 minutes)
  - compliance_deadline_check         (weekly Monday 08:00 AEST)
  - delete_expired_guest_tokens       (daily 03:00 AEST)

Run:
    python -m workers.scheduler
    python -m workers.scheduler --run-once recompute_all_building_summaries
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncio
import logging

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import db
from utils.helpers import create_user_notification, create_audit_log
from request_context import set_ctx_building_id

logger = logging.getLogger(__name__)
BATCH_SIZE = 50


async def recompute_all_building_summaries(building_id: str | None = None) -> int:
    """Recompute building summaries for every active building.

    Returns the count of buildings whose recompute failed. A per-building failure is logged and
    does not stop the rest of the batch, but the caller (run()'s --run-once path) needs this count
    to exit non-zero on partial failure -- otherwise a run where every building failed still looks
    like a clean success to systemd/monitoring.
    """
    from workers.analytics_worker import recompute_building_summary
    from utils.workflow_runner import workflow_run

    buildings = await db._db.buildings.find({"is_active": True}, {"id": 1}).to_list(5000)
    ids = [b["id"] for b in buildings if b.get("id")]
    logger.info("Recomputing summaries for %d buildings", len(ids))

    failures = 0

    async def _recompute_one(bid: str) -> bool:
        """Returns True on success, False on failure (logged)."""
        try:
            set_ctx_building_id(bid)
            async with workflow_run("building_summary_recompute", bid, "daily_02:00_aest") as run:
                run.trigger_type = "scheduled"
                await recompute_building_summary(bid)
                run.items_processed = 1
            return True
        except Exception as exc:
            logger.error("Summary recompute failed for building %s: %s", bid, exc)
            return False

    # Process in concurrent batches to avoid sequential bottleneck
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i:i + BATCH_SIZE]
        results = await asyncio.gather(*[_recompute_one(bid) for bid in batch])
        failures += sum(1 for ok in results if not ok)

    return failures


async def run_bi_analytics_etl(building_id: str | None = None) -> int:
    """Run the nightly BI ETL for every building. Returns the count of buildings with any
    failure (a raised exception, or at least one failed fact-table step) -- see
    recompute_all_building_summaries()'s docstring for why the caller needs this count.
    """
    from services.bi_etl_service import run_nightly_etl
    from utils.workflow_runner import workflow_run

    buildings = await db._db.buildings.find(
        {"is_archived": {"$ne": True}}, {"building_id": 1, "plan_id": 1}
    ).to_list(5000)
    ids = [b.get("building_id") or b.get("plan_id") for b in buildings]
    ids = [bid for bid in ids if bid]
    logger.info("Running BI analytics ETL (analytics.fact_*) for %d buildings", len(ids))

    failures = 0

    async def _etl_one(bid: str) -> bool:
        """Returns True if this building's ETL had no failures."""
        try:
            set_ctx_building_id(bid)
            async with workflow_run("bi_analytics_etl", bid, "daily_02:15_aest") as run:
                run.trigger_type = "scheduled"
                result = await run_nightly_etl(bid)
                failed = [name for name, r in result.items() if r.get("status") == "error"]
                run.items_processed = len(result) - len(failed)
                run.items_failed = len(failed)
                if failed:
                    logger.warning("BI analytics ETL partial failure for building %s: %s", bid, failed)
                return not failed
        except Exception as exc:
            logger.error("BI analytics ETL failed for building %s: %s", bid, exc)
            return False

    # Process in concurrent batches to avoid sequential bottleneck
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i:i + BATCH_SIZE]
        results = await asyncio.gather(*[_etl_one(bid) for bid in batch])
        failures += sum(1 for ok in results if not ok)

    return failures


async def check_sla_breaches(building_id: str | None = None) -> None:
    """Generated function header.

    Function: check_sla_breaches
    Path: backend/workers/scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from services.event_emitter import emit_event
    from core.events import EventStream, EventType
    from utils.workflow_runner import workflow_run

    now = datetime.now(timezone.utc).isoformat()
    # Find all active buildings to iterate through
    buildings = await db._db.buildings.find({"is_active": True}, {"id": 1}).to_list(5000)

    for building in buildings:
        bid = building.get("id")
        if not bid:
            continue
        set_ctx_building_id(bid)

        async with workflow_run("sla_breach_check", bid, "scheduled_15min") as run:
            run.trigger_type = "scheduled"

            # Find newly breached requests before bulk-updating
            breaching = await db.workflow_requests.find(
                {
                    "sla_due_at": {"$lt": now},
                    "sla_breached": {"$ne": True},
                    "status": {"$in": ["awaiting_review", "in_progress"]},
                    "is_test_data": {"$ne": True},
                },
                {"_id": 0, "id": 1, "request_number": 1, "request_type": 1,
                 "unit_number": 1, "assigned_to": 1, "sla_due_at": 1, "building_id": 1},
            ).to_list(200)

            if not breaching:
                run.items_processed = 0
                continue

            req_ids = [r["id"] for r in breaching]
            result = await db.workflow_requests.update_many(
                {"id": {"$in": req_ids}},
                {"$set": {"sla_breached": True}},
            )
            run.items_processed = result.modified_count

            # Cache of building strata_manager user IDs — loaded lazily per building
            building_managers: list = []

            # Notify assigned strata managers and write audit entries
            for req in breaching:
                notify_user_ids: list = []

                if req.get("assigned_to"):
                    notify_user_ids = [req["assigned_to"]]
                else:
                    # Fallback: notify all active strata_manager users in this building
                    if not building_managers:
                        mgr_docs = await db._db.users.find(
                            {"building_id": bid, "role": "strata_manager", "is_active": True},
                            {"_id": 0, "id": 1},
                        ).to_list(50)
                        building_managers = [m["id"] for m in mgr_docs if m.get("id")]
                    notify_user_ids = building_managers

                for uid in notify_user_ids:
                    notif_id = await create_user_notification(
                        user_id=uid,
                        title=f"⚠️ SLA breached: {req.get('request_number', req['id'])}",
                        message=(
                            f"{req.get('request_type', 'request').replace('_', ' ').title()} "
                            f"from unit {req.get('unit_number', 'unknown')} is overdue."
                        ),
                        notification_type="sla_breach",
                        link=f"/requests/{req['id']}",
                        building_id=bid,
                    )
                    if notif_id:
                        run.add_artefact("notification", notif_id)

                await create_audit_log(
                    action="sla_breached",
                    resource_type="workflow_request",
                    resource_id=req["id"],
                    user_id="system",
                    user_name="scheduler",
                    details={
                        "sla_due_at": req.get("sla_due_at"),
                        "category": req.get("request_type"),
                        "notified_users": notify_user_ids,
                    },
                    building_id=bid,
                )

            if result.modified_count:
                await emit_event(
                    EventStream.OPERATIONS.value,
                    EventType.SLA_BREACH_DETECTED.value,
                    bid,
                    {"breach_count": result.modified_count},
                )


async def proposal_auto_close(building_id: str | None = None) -> None:
    """Generated function header.

    Function: proposal_auto_close
    Path: backend/workers/scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from utils.workflow_runner import workflow_run

    now = datetime.now(timezone.utc).isoformat()
    buildings = await db._db.buildings.find({"is_active": True}, {"id": 1}).to_list(5000)
    for building in buildings:
        bid = building.get("id")
        if not bid:
            continue
        set_ctx_building_id(bid)

        async with workflow_run("proposal_auto_close", bid, "scheduled_30min") as run:
            run.trigger_type = "scheduled"
            proposals = await db.proposals.find(
                {"status": "open", "voting_closes_at": {"$lt": now}, "is_test_data": {"$ne": True}},
                {"_id": 0},
            ).to_list(100)
            for p in proposals:
                votes = p.get("votes", [])
                eligible = p.get("eligible_voters", [])
                for_votes = sum(1 for v in votes if v.get("vote") == "for")
                total_votes = len(votes)
                quorum_pct = p.get("quorum_required", 0.25)
                quorum_achieved = total_votes >= len(eligible) * quorum_pct if eligible else False
                outcome = "passed" if for_votes > total_votes / 2 and quorum_achieved else "failed"
                await db.proposals.update_one(
                    {"id": p["id"]},
                    {"$set": {
                        "status": outcome,
                        "quorum_achieved": quorum_achieved,
                        "outcome_recorded_at": now,
                        "updated_at": now,
                    }},
                )
                run.items_processed += 1
                run.add_artefact("proposal_outcome", p["id"])
                logger.info("Auto-closed proposal %s: %s", p.get("proposal_number"), outcome)


async def delete_expired_guest_tokens(building_id: str | None = None) -> None:
    """Deactivate guest accounts whose stay end_date has passed. Operates globally."""
    from utils.workflow_runner import workflow_run

    now = datetime.now(timezone.utc).isoformat()

    expired_rels = await db._db.user_units.find(
        {
            "role_at_unit": "guest",
            "is_active": True,
            "end_date": {"$lt": now, "$ne": None},
        },
        {"user_id": 1, "_id": 0},
    ).to_list(10000)

    if not expired_rels:
        return

    expired_user_ids = list({r["user_id"] for r in expired_rels})

    # This job is cross-building; record as a global run (no building context)
    async with workflow_run("delete_expired_guest_tokens", "global", "daily_03:00_aest") as run:
        run.trigger_type = "scheduled"

        result = await db._db.users.update_many(
            {"id": {"$in": expired_user_ids}, "role": "guest", "is_active": True},
            {"$set": {"is_active": False, "deactivated_reason": "guest_stay_expired"}},
        )
        await db._db.user_units.update_many(
            {"user_id": {"$in": expired_user_ids}, "role_at_unit": "guest", "is_active": True},
            {"$set": {"is_active": False, "actual_end_date": now}},
        )
        run.items_processed = result.modified_count
        if result.modified_count:
            logger.info("Deactivated %d expired guest accounts", result.modified_count)


async def lease_expiry_alerts(building_id: str | None = None) -> None:
    """Generated function header.

    Function: lease_expiry_alerts
    Path: backend/workers/scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logger.info("Lease expiry alerts (stub)")


async def compliance_deadline_check(building_id: str | None = None) -> None:
    """Generated function header.

    Function: compliance_deadline_check
    Path: backend/workers/scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from utils.workflow_runner import workflow_run

    now = datetime.now(timezone.utc).isoformat()
    buildings = await db._db.buildings.find({"is_active": True}, {"id": 1}).to_list(5000)
    for building in buildings:
        bid = building.get("id")
        if not bid:
            continue
        set_ctx_building_id(bid)

        async with workflow_run("compliance_deadline_check", bid, "weekly_monday_08:00_aest") as run:
            run.trigger_type = "scheduled"
            result = await db.compliance_items.update_many(
                {"due_date": {"$lt": now}, "status": {"$nin": ["completed", "overdue"]}},
                {"$set": {"status": "overdue"}},
            )
            run.items_processed = result.modified_count
            if result.modified_count:
                logger.info("Marked %d items overdue in building %s", result.modified_count, bid)


async def check_workflow_heartbeats(building_id: str | None = None) -> None:
    """
    Checks that scheduled workflows are running within expected intervals.
    Flags stale workflows by logging — visible on the governance dashboard.
    Runs every 10 minutes. Writes a workflow_run record per building.
    """
    from utils.workflow_runner import _HEARTBEAT_TOLERANCE, workflow_run

    now = datetime.now(timezone.utc)
    buildings = await db._db.buildings.find({"is_active": True}, {"id": 1}).to_list(5000)

    for building in buildings:
        bid = building.get("id")
        if not bid:
            continue
        set_ctx_building_id(bid)

        # Exclude the heartbeat monitor itself to avoid infinite self-check
        workflows_to_check = {
            wf_id: tol for wf_id, tol in _HEARTBEAT_TOLERANCE.items()
            if wf_id != "workflow_heartbeat_monitor"
        }

        async with workflow_run("workflow_heartbeat_monitor", bid, "scheduled_10min") as run:
            run.trigger_type = "scheduled"
            stale = []
            for workflow_id, tolerance_minutes in workflows_to_check.items():
                last_run = await db.workflow_runs.find_one(
                    {
                        "workflow_id": workflow_id,
                        "building_id": bid,
                        "status": {"$in": ["success", "partial"]},
                        "is_test_data": {"$ne": True},
                    },
                    {"_id": 0, "started_at": 1},
                    sort=[("started_at", -1)],
                )
                if last_run and last_run.get("started_at"):
                    try:
                        started = datetime.fromisoformat(last_run["started_at"])
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        age_minutes = (now - started).total_seconds() / 60
                        if age_minutes > tolerance_minutes:
                            stale.append({"workflow_id": workflow_id, "age_minutes": round(age_minutes, 1)})
                    except (ValueError, TypeError):
                        pass

            run.items_processed = len(workflows_to_check)
            run.add_artefact("stale_count", len(stale))
            if stale:
                logger.warning(
                    "Heartbeat check: %d stale workflows in building %s: %s",
                    len(stale),
                    bid,
                    [s["workflow_id"] for s in stale],
                )


async def _arq_toggle_on(building_id: str) -> bool:
    """Return True if financial_integration_layer_v2 is ON for this building.

    Used to skip legacy scheduler jobs when ARQ has taken them over.
    """
    from db_postgres.repos import config_repo

    value = await config_repo.resolve_feature_toggle(
        building_id,
        "financial_integration_layer_v2",
        default=False,
    )
    return bool(value)


async def nightly_merkle_seal(building_id: str | None = None) -> int:
    """Compute and persist a daily Merkle seal for every active building × period.

    Runs at 01:00 AEST (before the 02:00 summary recompute) so audit seals are
    always written before any daily aggregation jobs read the ledger.

    For each building, discovers all distinct period_id values that have at least
    one posted entry in trust_ledger_entries, then calls seal_period() for each.
    Failures for individual (building, period) pairs are logged but do not abort
    the remaining pairs. Returns the count of failures (see
    recompute_all_building_summaries()'s docstring for why the caller needs this).

    Toggle guard: if financial_integration_layer_v2 is ON for a building,
    ARQ's daily_merkle_seal_task owns the seal — skip it here.
    """
    from workers.merkle_seal import seal_period

    today = datetime.now(timezone.utc).date().isoformat()
    buildings = await db._db.buildings.find(
        {"is_active": True, "is_archived": {"$ne": True}},
        {"id": 1},
    ).to_list(5000)

    ids = [b["id"] for b in buildings if b.get("id")]
    if building_id:
        ids = [bid for bid in ids if bid == building_id]

    sealed = 0
    failures = 0
    for bid in ids:
        set_ctx_building_id(bid)
        # Skip buildings managed by ARQ to prevent double-sealing
        if await _arq_toggle_on(bid):
            logger.debug("nightly_merkle_seal: skipping %s (ARQ active)", bid)
            continue
        try:
            periods = await db._db.trust_ledger_entries.distinct(
                "period_id", {"building_id": bid, "status": "posted", "period_id": {"$ne": None}}
            )
        except Exception as exc:
            logger.error("merkle_seal: failed to list periods for building %s: %s", bid, exc)
            failures += 1
            continue

        for pid in periods:
            if not pid:
                continue
            try:
                await seal_period(bid, pid, db._db, seal_date=today)
                sealed += 1
            except Exception as exc:
                logger.error(
                    "merkle_seal: failed for building=%s period=%s: %s", bid, pid, exc
                )
                failures += 1

    logger.info("nightly_merkle_seal: sealed %d period(s) on %s (%d failures)", sealed, today, failures)
    return failures


async def finance_dr_drift_check(building_id: str | None = None) -> int:
    """Measure Mongo<->Postgres finance drift for every active building.

    This is the DR/fallback guarantee, and until 2026-08-29 it did not run at all.
    `services/mongo_pg_finance_sync.py` had been complete and unit-tested since it was
    written, but its only entry point was a manual `scripts/data_repair/` CLI — nothing
    in workers/, cron/, routers/ or server.py ever referenced it. A sync that is never
    invoked protects nothing, and drift that is never measured is drift discovered a
    month later against a portal scrape.

    Read-only by design: `apply=False` measures and reports. Emitting Demo Bank intake
    candidates stays a deliberate human action via the CLI's `--apply`, because those
    candidates are financial evidence and CLAUDE.md rule 15 routes every financial input
    through Demo Bank under review — a scheduler must not manufacture that silently.

    Returns the number of buildings that FAILED to be measured (not the number that
    diverged). Divergence is a reported number, not a job failure: the point is to make
    it visible every day rather than to break the scheduler when the stores disagree.
    """
    from services.mongo_pg_finance_sync import run_finance_dr_sync

    buildings = await db._db.buildings.find(
        {"is_active": True, "is_archived": {"$ne": True}}, {"id": 1}
    ).to_list(5000)
    ids = [b["id"] for b in buildings if b.get("id")]
    if building_id:
        ids = [bid for bid in ids if bid == building_id]
    logger.info("finance_dr_drift_check: measuring %d building(s)", len(ids))

    failures = 0
    diverged_buildings = 0
    for bid in ids:
        try:
            set_ctx_building_id(bid)
            summary = await run_finance_dr_sync(bid, apply=False)
            if summary.get("status") == "drift":
                diverged_buildings += 1
                logger.warning(
                    "finance DR drift building=%s lots_diverged=%s/%s net_gap_cents=%s "
                    "missing_in_pg=%s — run scripts/data_repair/sync_mongo_payments_to_postgres.py "
                    "--building-id %s to review",
                    bid, summary.get("lots_diverged"), summary.get("lots_compared"),
                    summary.get("net_gap_cents"), summary.get("missing_in_pg"), bid,
                )
        except Exception as exc:
            logger.error("finance_dr_drift_check failed for building %s: %s", bid, exc)
            failures += 1

    logger.info(
        "finance_dr_drift_check: %d/%d building(s) showing drift, %d measurement failure(s)",
        diverged_buildings, len(ids), failures,
    )
    return failures


JOBS = {
    "recompute_all_building_summaries": recompute_all_building_summaries,
    "run_bi_analytics_etl": run_bi_analytics_etl,
    "nightly_merkle_seal": nightly_merkle_seal,
    "check_sla_breaches": check_sla_breaches,
    "check_workflow_heartbeats": check_workflow_heartbeats,
    "proposal_auto_close": proposal_auto_close,
    "delete_expired_guest_tokens": delete_expired_guest_tokens,
    "lease_expiry_alerts": lease_expiry_alerts,
    "compliance_deadline_check": compliance_deadline_check,
    "finance_dr_drift_check": finance_dr_drift_check,
}


async def run() -> None:
    """CLI entrypoint. `--run-once <job_name>` exits non-zero (via sys.exit) on an unknown job
    name or if the job itself reports any per-building failure -- previously this always
    returned/exited 0 regardless of outcome, which meant a systemd service invoking a job via
    --run-once could report "success" even when every building failed. Job functions that don't
    track a failure count return None, treated as 0 (success) for backward compatibility.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if "--run-once" in sys.argv:
        idx = sys.argv.index("--run-once")
        if idx + 1 < len(sys.argv):
            job_name = sys.argv[idx + 1]
            fn = JOBS.get(job_name)
            if not fn:
                logger.error("Unknown job: %s. Available: %s", job_name, list(JOBS.keys()))
                sys.exit(1)
            logger.info("Running once: %s", job_name)
            failures = await fn()
            if failures:
                logger.error("Job %s reported %d failure(s)", job_name, failures)
                sys.exit(1)
            return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler not installed: pip install apscheduler")
        return

    scheduler = AsyncIOScheduler(timezone="Australia/Sydney")
    scheduler.add_job(nightly_merkle_seal, CronTrigger(hour=1, minute=0))
    scheduler.add_job(recompute_all_building_summaries, CronTrigger(hour=2, minute=0))
    scheduler.add_job(run_bi_analytics_etl, CronTrigger(hour=2, minute=15))
    scheduler.add_job(check_sla_breaches, CronTrigger(minute="*/15"))
    scheduler.add_job(check_workflow_heartbeats, CronTrigger(minute="*/10"))
    scheduler.add_job(lease_expiry_alerts, CronTrigger(hour=7, minute=0))
    scheduler.add_job(proposal_auto_close, CronTrigger(minute="*/30"))
    scheduler.add_job(compliance_deadline_check, CronTrigger(day_of_week="mon", hour=8, minute=0))
    scheduler.add_job(delete_expired_guest_tokens, CronTrigger(hour=3, minute=0))
    # After the nightly recompute (02:00) and BI ETL (02:15), so the drift number
    # reflects the day's settled state rather than a mid-recompute snapshot.
    scheduler.add_job(finance_dr_drift_check, CronTrigger(hour=3, minute=30))
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
