# @featuretrace:scheduler — ARQ task functions for routine financial jobs (financial_integration_layer_v2).
# Layer: worker
# Data flow: ARQ worker → task functions → integration_inbox / financial_periods / unit_levy_ledger (building-scoped).
# Related: backend/workers/arq_settings.py (WorkerSettings + cron schedule)
#           backend/cron/cron_trust_interest.py (interest accrual delegate)
#           backend/cron/cron_levy_reminders.py (levy reminders delegate)
#           backend/workers/merkle_seal.py (daily Merkle seal)
#           backend/workers/temporal_worker.py (Temporal saga worker)
#           backend/workflows/generate_levies_workflow.py (levy generation saga)
#           backend/workflows/reconciliation_close_workflow.py (reconciliation close saga)
"""
NOT DEPLOYED (verified live 2026-08-11) — see backend/workers/arq_settings.py's module docstring
for the full reasoning. No ARQ worker process runs anywhere in this deployment.

ARQ task functions for routine financial jobs.

All tasks are guarded by the `financial_integration_layer_v2` feature toggle.
If the toggle is OFF for a building, the legacy cron/scheduler path runs instead.
If the toggle is ON, these tasks own the job and the scheduler skips it.

Each task is idempotent — re-running for the same building/date produces no
duplicate journals, reminders, or seals.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict

from database import db
from db_postgres.repos import config_repo
from request_context import set_ctx_building_id
from integrations.registry import get_provider_registry
from cron.cron_trust_interest import run_interest_accrual
from cron.cron_levy_reminders import run_levy_reminders
from workers.merkle_seal import seal_period

logger = logging.getLogger(__name__)


# ── Toggle guard ──────────────────────────────────────────────────────────────

async def _toggle_on(building_id: str) -> bool:
    """Return True if financial_integration_layer_v2 is enabled for this building."""
    set_ctx_building_id(building_id)
    value = await config_repo.resolve_feature_toggle(
        building_id,
        "financial_integration_layer_v2",
        default=False,
    )
    return bool(value)


# ── Bank feed poll ────────────────────────────────────────────────────────────

async def bank_feed_poll_task(ctx: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """Poll the bank feed provider and drop transaction envelopes into integration_inbox.

    Runs every 15 minutes per building when financial_integration_layer_v2 is ON.
    Idempotency: the BankFeedProvider implementation tracks the last-fetched cursor
    per building; re-polling returns only new transactions (no duplicates in inbox).
    """
    if not await _toggle_on(building_id):
        logger.info("bank_feed_poll_task: toggle OFF for %s — skip", building_id)
        return {"skipped": True, "building_id": building_id}

    set_ctx_building_id(building_id)
    registry = get_provider_registry()

    try:
        provider = await registry.get_bank_feed(building_id)
    except Exception as exc:
        logger.warning("bank_feed_poll_task: no bank feed provider for %s: %s", building_id, exc)
        return {"building_id": building_id, "envelopes_added": 0, "error": str(exc)}

    envelopes = await provider.pull_transactions(building_id)

    inserted = 0
    for env in envelopes:
        env.setdefault("building_id", building_id)
        env.setdefault("is_test_data", False)
        env.setdefault("ingested_at", datetime.now(timezone.utc))
        env.setdefault("status", "pending_match")
        result = await db._db.integration_inbox.update_one(
            {"building_id": building_id, "provider_ref": env.get("provider_ref")},
            {"$setOnInsert": env},
            upsert=True,
        )
        if result.upserted_id:
            inserted += 1

    logger.info("bank_feed_poll_task: %s — %d new envelopes", building_id, inserted)
    return {"building_id": building_id, "envelopes_added": inserted}


# ── Matching engine ───────────────────────────────────────────────────────────

async def matching_engine_worker_task(ctx: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """Drain pending inbox entries and run the MatchingEngine for a building.

    Triggered reactively after bank_feed_poll_task adds envelopes.
    Also scheduled nightly as a catch-up pass.
    """
    from services.reconciliation_matching_service import MatchingEngine

    if not await _toggle_on(building_id):
        return {"skipped": True, "building_id": building_id}

    set_ctx_building_id(building_id)
    engine = MatchingEngine(db)

    pending = await db._db.integration_inbox.find(
        {"building_id": building_id, "status": "pending_match"}
    ).to_list(500)

    if not pending:
        return {"building_id": building_id, "matched": 0, "unmatched": 0}

    matched = 0
    unmatched = 0
    for envelope in pending:
        try:
            result = await engine.match_envelope(envelope)
            status = "matched" if result.get("matched") else "unmatched"
            if result.get("matched"):
                matched += 1
            else:
                unmatched += 1
            await db._db.integration_inbox.update_one(
                {"_id": envelope["_id"]},
                {"$set": {"status": status, "match_result": result}},
            )
        except Exception as exc:
            logger.error("matching_engine_worker_task: error on envelope %s: %s", envelope.get("_id"), exc)
            unmatched += 1

    logger.info("matching_engine_worker_task: %s — %d matched, %d unmatched", building_id, matched, unmatched)
    return {"building_id": building_id, "matched": matched, "unmatched": unmatched}


# ── Interest accrual ──────────────────────────────────────────────────────────

async def interest_accrual_task(ctx: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """Post monthly trust interest journals for a building.

    Idempotency: cron_trust_interest.run_interest_accrual() skips if interest
    is already posted for the current period. ARQ additionally uses the job_id
    `interest:{building_id}:{YYYY-MM}` to prevent duplicate enqueues.
    """
    if not await _toggle_on(building_id):
        return {"skipped": True, "building_id": building_id}

    today = date.today()
    logger.info("interest_accrual_task: %s — %s-%02d", building_id, today.year, today.month)
    result = await run_interest_accrual(
        dry_run=False,
        target_building_id=building_id,
    )
    return {"building_id": building_id, **result}


# ── Levy reminder dispatch ────────────────────────────────────────────────────

async def levy_reminder_dispatch_task(ctx: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """Dispatch overdue levy reminders for a building.

    Tier 1 (7–30 days overdue) by default. The cron function reads per-building
    settings from levy_reminder_settings to determine thresholds and cadence.
    """
    if not await _toggle_on(building_id):
        return {"skipped": True, "building_id": building_id}

    logger.info("levy_reminder_dispatch_task: %s", building_id)
    result = await run_levy_reminders(building_id=building_id, tier=1)
    return {"building_id": building_id, **result}


# ── Daily Merkle seal ─────────────────────────────────────────────────────────

async def daily_merkle_seal_task(
        ctx: Dict[str, Any],
        building_id: str,
        target_date: str | None = None,
) -> Dict[str, Any]:
    """Compute and persist the daily Merkle seal for a building's trust ledger.

    Idempotency: seal_period() checks if a seal already exists for this
    (building_id, period_id, date) tuple — returns existing seal without
    re-computing if found.

    target_date: ISO date string (YYYY-MM-DD). Defaults to today (AEST).
    """
    set_ctx_building_id(building_id)

    # Find the open or most recently closed financial period for this building
    period = await db._db.financial_periods.find_one(
        {"building_id": building_id, "state": {"$in": ["open", "closed"]}},
        sort=[("period_start", -1)],
    )
    if not period:
        logger.warning("daily_merkle_seal_task: no financial period for %s", building_id)
        return {"building_id": building_id, "sealed": False, "reason": "no_period"}

    period_id = str(period["_id"])
    seal_date = target_date or date.today().isoformat()

    seal = await seal_period(building_id, period_id, db._db, seal_date=seal_date)
    return {"building_id": building_id, "period_id": period_id, "sealed": True, "seal": seal}


# ── Recurring bill instantiation ──────────────────────────────────────────────

async def instantiate_recurring_bills_task(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Instantiate due recurring bill templates for all active buildings.

    Runs nightly. The domain function is idempotent — it checks next_due_date
    and skips templates that have already been instantiated for the current cycle.
    """
    from domain.recurring_bills import instantiate_recurring_bills

    buildings = await db._db.buildings.find(
        {"is_active": True, "is_archived": {"$ne": True}}, {"id": 1}
    ).to_list(5000)

    total_created = 0
    errors = []
    for bld in buildings:
        bid = bld.get("id") or str(bld.get("_id", ""))
        if not bid:
            continue
        try:
            result = await instantiate_recurring_bills(building_id=bid, db=db)
            total_created += result.get("created", 0)
        except Exception as exc:
            logger.error("instantiate_recurring_bills_task: error for %s: %s", bid, exc)
            errors.append({"building_id": bid, "error": str(exc)})

    logger.info("instantiate_recurring_bills_task: %d bills created across all buildings", total_created)
    return {"total_created": total_created, "errors": errors}


# ── Projection rebuild ────────────────────────────────────────────────────────

async def projection_rebuild_task(
        ctx: Dict[str, Any],
        projection_name: str,
        building_id: str,
        since_event_id: str | None = None,
) -> Dict[str, Any]:
    """Rebuild a named projection from the event log.

    Used for on-demand replay during disaster recovery or after a bug fix.
    since_event_id: if provided, only replay events after this ID (incremental rebuild).
    """
    SUPPORTED = {"lot_balances_projection", "unit_levy_ledger", "levy_payments_projection"}
    if projection_name not in SUPPORTED:
        return {
            "error": f"Unknown projection '{projection_name}'. Supported: {sorted(SUPPORTED)}"
        }

    set_ctx_building_id(building_id)

    query: dict = {"building_id": building_id, "projection_target": projection_name}
    if since_event_id:
        from bson import ObjectId
        try:
            query["_id"] = {"$gt": ObjectId(since_event_id)}
        except Exception:
            return {"error": f"Invalid since_event_id: {since_event_id}"}

    events = await db._db.financial_events.find(query).sort("_id", 1).to_list(50000)
    logger.info(
        "projection_rebuild_task: replaying %d events for %s/%s (since=%s)",
        len(events), building_id, projection_name, since_event_id,
    )

    # Delegate to the projection-specific rebuild function
    from repositories.financial_repository import rebuild_projection
    rebuilt_count = await rebuild_projection(
        db._db, projection_name, building_id, events
    )

    return {
        "building_id": building_id,
        "projection_name": projection_name,
        "events_replayed": len(events),
        "records_rebuilt": rebuilt_count,
    }
