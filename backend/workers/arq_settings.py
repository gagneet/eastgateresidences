# @featuretrace:scheduler — ARQ WorkerSettings: Redis connection, task registry, and cron schedule.
# Layer: worker
# Data flow: external cron/Redis → WorkerSettings → arq_tasks.py task functions → (building-scoped collections).
# Related: backend/workers/arq_tasks.py (task function definitions)
#           backend/workers/temporal_worker.py (Temporal workflow worker — runs alongside)
# Scope: (building-scoped)
"""
NOT DEPLOYED (verified live 2026-08-11): no systemd unit, no PM2 entry, not started anywhere in
this codebase's deployment. `financial_integration_layer_v2` (the toggle these tasks are gated
behind) has no live worker process to ever claim a job even when the toggle is on -- 100% of
buildings run on the "legacy" cron/systemd path (see backend/cron/, deploy/systemd/) regardless of
toggle state. Right-sized architecture review concluded this app's scale (1 real building) doesn't
need ARQ; the cron/systemd pattern already covers this task set (arq_tasks.py wraps the same
cron.cron_trust_interest/cron.cron_levy_reminders functions those cron scripts already run).
Left in place, not deleted, pending a stable release -- see
docs/architecture/financial-postgres-cutover-status-2026-08-11.md for the full reasoning. Do not
deploy this without first re-confirming it's still not redundant with the cron/systemd path.

ARQ WorkerSettings for the financial integration layer v2 scheduler.

Run the worker:
    cd backend && venv/bin/python3 -m arq workers.arq_settings.WorkerSettings

Environment variables:
    REDIS_URL  — Redis connection string (default: redis://localhost:6379)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

_AEST = timezone(timedelta(hours=10))

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


# ── Startup / shutdown ────────────────────────────────────────────────────────

async def startup(ctx: Dict[str, Any]) -> None:
    """Connect to Redis and warm up any shared state needed by tasks."""
    logger.info("ARQ worker starting up (REDIS_URL=%s)", REDIS_URL)


async def shutdown(ctx: Dict[str, Any]) -> None:
    """Generated function header.

    Function: shutdown
    Path: backend/workers/arq_settings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logger.info("ARQ worker shutting down")


# ── Cron schedule ─────────────────────────────────────────────────────────────
# Cron entries enqueue tasks for every active building.
# The tasks themselves guard against disabled toggles — no per-building cron
# entries are needed; one global cron enqueues all buildings.

from arq.connections import RedisSettings
from arq.cron import cron

from workers.arq_tasks import (
    bank_feed_poll_task,
    daily_merkle_seal_task,
    instantiate_recurring_bills_task,
    interest_accrual_task,
    levy_reminder_dispatch_task,
    matching_engine_worker_task,
    projection_rebuild_task,
)


async def _enqueue_per_building(ctx: Dict[str, Any], task_func, **kwargs) -> None:
    """Fetch all active buildings and enqueue a task for each."""
    from database import db
    buildings = await db._db.buildings.find(
        {"is_active": True, "is_archived": {"$ne": True}}, {"id": 1}
    ).to_list(5000)
    for bld in buildings:
        bid = bld.get("id") or str(bld.get("_id", ""))
        if not bid:
            continue
        await ctx["redis"].enqueue_job(
            task_func.__name__,
            bid,
            _job_id=f"{task_func.__name__}:{bid}",
            **kwargs,
        )


async def cron_bank_feed_poll(ctx: Dict[str, Any]) -> None:
    """Generated function header.

    Function: cron_bank_feed_poll
    Path: backend/workers/arq_settings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _enqueue_per_building(ctx, bank_feed_poll_task)


async def cron_levy_reminders(ctx: Dict[str, Any]) -> None:
    """Generated function header.

    Function: cron_levy_reminders
    Path: backend/workers/arq_settings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _enqueue_per_building(ctx, levy_reminder_dispatch_task)


async def cron_merkle_seal(ctx: Dict[str, Any]) -> None:
    """Generated function header.

    Function: cron_merkle_seal
    Path: backend/workers/arq_settings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _enqueue_per_building(ctx, daily_merkle_seal_task)


async def cron_interest_accrual(ctx: Dict[str, Any]) -> None:
    """Monthly interest accrual — runs on the 1st of each month at 00:30 AEST (14:30 UTC prev day).

    ARQ cron lacks day-of-month support so this fires daily; the AEST date
    guard ensures enqueuing only happens on the 1st.
    """
    if datetime.now(_AEST).day != 1:
        logger.debug("cron_interest_accrual: skipping — AEST date is not the 1st of month")
        return
    await _enqueue_per_building(ctx, interest_accrual_task)


class WorkerSettings:
    """ARQ WorkerSettings — imported by `python -m arq workers.arq_settings.WorkerSettings`."""

    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    on_startup = startup
    on_shutdown = shutdown

    # All task functions that ARQ workers can execute
    functions = [
        bank_feed_poll_task,
        matching_engine_worker_task,
        interest_accrual_task,
        levy_reminder_dispatch_task,
        daily_merkle_seal_task,
        instantiate_recurring_bills_task,
        projection_rebuild_task,
    ]

    # Cron schedule — times are UTC
    cron_jobs = [
        # Bank feed poll every 15 minutes
        cron(cron_bank_feed_poll, minute={0, 15, 30, 45}),
        # Levy reminders daily 08:00 AEST = 22:00 UTC prev day
        cron(cron_levy_reminders, hour=22, minute=0),
        # Merkle seal daily 01:00 AEST = 15:00 UTC prev day
        cron(cron_merkle_seal, hour=15, minute=0),
        # Recurring bill instantiation nightly 00:30 AEST = 14:30 UTC prev day
        cron(instantiate_recurring_bills_task, hour=14, minute=30),
        # Interest accrual 1st of each month 00:30 AEST = 14:30 UTC prev day
        # ARQ cron doesn't support day-of-month — use the month_cron wrapper
        cron(cron_interest_accrual, hour=14, minute=32),
    ]

    # Job settings
    max_jobs = 50
    job_timeout = 600  # 10 minutes max per task
    keep_result = 3600  # Keep results for 1 hour
    retry_jobs = True
    max_tries = 3
