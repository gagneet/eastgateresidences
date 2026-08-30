#!/usr/bin/env python3
# @featuretrace:financial_core — timing harness for cron/worker background jobs.
# k6 cannot exercise background jobs (they are not HTTP routes), so this harness
# times each scheduler JOB and each cron run_* entrypoint by invoking it once and
# measuring wall-clock + whatever throughput count the callable returns.
#
# Data flow: CLI -> backend.workers.scheduler.JOBS / backend.cron.* run_* ->
#            times asyncio.run(job()) -> JSON/console summary + budget verdict.
#
# SAFETY — READ THIS BEFORE RUNNING AGAINST ANYTHING REAL
# -------------------------------------------------------
# Many of these jobs MUTATE data (mark compliance overdue, close proposals,
# deactivate guests, post trust interest) or have SIDE EFFECTS (send levy
# reminder emails). This harness must be pointed at a STAGING / SEEDED database,
# never production. The safety model:
#   * read_only jobs        -> always allowed.
#   * dry_run_capable jobs  -> run with dry_run=True by default (no writes).
#   * mutating jobs         -> SKIPPED unless --allow-mutation AND env
#                              PERF_HARNESS_ALLOW_MUTATION=1 are both set.
#   * all-buildings jobs    -> a mutating job that iterates every active building
#                              additionally requires --allow-all-buildings, so a
#                              single stray run cannot touch the whole estate.
#
# Nothing here sets a load; each job runs exactly once. This is a latency/
# throughput probe, not a stress test.
#
# Usage:
#   python -m tests.performance.harness.cron_worker_timing --list
#   python tests/performance/harness/cron_worker_timing.py \
#       --building-id <scheme_number> --json-out /tmp/cron_timing.json
#   # include dry-run-capable recompute jobs:
#   python tests/performance/harness/cron_worker_timing.py --building-id 13195 --dry-run
#   # include mutating jobs on a staging DB (bounded to one building):
#   PERF_HARNESS_ALLOW_MUTATION=1 python tests/performance/harness/cron_worker_timing.py \
#       --building-id STAGING-0001 --allow-mutation
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

# Backend must be importable; all heavy imports are lazy (inside factories) so
# this module byte-compiles and --list works without backend deps installed.
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# side_effect classes
READ_ONLY = "read_only"
DRY_RUN = "dry_run_capable"
MUTATING = "mutating"

# blast radius
SINGLE = "single_building"
ALL = "all_buildings"


@dataclass
class Job:
    """One timeable background unit."""
    name: str
    kind: str                       # "scheduler" | "cron"
    side_effect: str                # READ_ONLY | DRY_RUN | MUTATING
    blast_radius: str               # SINGLE | ALL
    budget_seconds: float           # soft SLO for a single run
    note: str
    # factory returns the coroutine to await, given resolved options
    factory: Callable[["RunOptions"], Awaitable[Any]] = field(repr=False)


@dataclass
class RunOptions:
    building_id: Optional[str]
    dry_run: bool
    financial_year: str


# --------------------------------------------------------------------------
# Scheduler JOBS — pulled from the live registry so the catalog can't drift
# from workers/scheduler.py. Each registry job accepts an optional building_id.
# --------------------------------------------------------------------------
def _scheduler_job(job_name: str) -> Callable[[RunOptions], Awaitable[Any]]:
    async def _run(opts: RunOptions) -> Any:
        from workers.scheduler import JOBS  # lazy
        fn = JOBS[job_name]
        # Most scheduler jobs ignore building_id and iterate all active
        # buildings internally; nightly_merkle_seal honours it. Pass it through
        # when supplied — harmless for jobs that ignore it.
        if opts.building_id:
            return await fn(building_id=opts.building_id)
        return await fn()
    return _run


# scheduler job name -> (side_effect, blast_radius, budget_seconds, note)
_SCHEDULER_META: dict[str, tuple[str, str, float, str]] = {
    "check_workflow_heartbeats": (READ_ONLY, ALL, 30.0, "Reads workflow_runs, logs stale; no writes."),
    "lease_expiry_alerts": (READ_ONLY, SINGLE, 5.0, "Stub; times import + call overhead."),
    "check_sla_breaches": (MUTATING, ALL, 45.0, "Sets sla_breached, writes notifications + audit."),
    "recompute_all_building_summaries": (MUTATING, ALL, 120.0, "Recomputes + writes per-building summary."),
    "run_bi_analytics_etl": (MUTATING, ALL, 180.0, "Writes analytics.fact_* rows per building."),
    "nightly_merkle_seal": (MUTATING, SINGLE, 60.0, "Writes audit seals; honours building_id."),
    "proposal_auto_close": (MUTATING, ALL, 30.0, "Closes proposals past voting_closes_at."),
    "compliance_deadline_check": (MUTATING, ALL, 30.0, "Marks overdue compliance items."),
    "delete_expired_guest_tokens": (MUTATING, ALL, 20.0, "Deactivates expired guest accounts (global)."),
}


# --------------------------------------------------------------------------
# Cron entrypoints — accurate signatures verified against backend/cron/*.py.
# --------------------------------------------------------------------------
def _cron_finance_recompute(opts: RunOptions) -> Awaitable[Any]:
    async def _run() -> Any:
        from cron.cron_finance_recompute import run_recompute  # lazy
        # dry_run=True by default -> no writes.
        return await run_recompute(
            year=opts.financial_year,
            dry_run=opts.dry_run,
            building_id=opts.building_id,
        )
    return _run()


def _cron_call(module: str, func: str, *, pass_building: bool = True,
               extra_kwargs: Optional[dict] = None) -> Callable[[RunOptions], Awaitable[Any]]:
    async def _run(opts: RunOptions) -> Any:
        mod = __import__(f"cron.{module}", fromlist=[func])  # lazy
        fn = getattr(mod, func)
        kwargs: dict[str, Any] = dict(extra_kwargs or {})
        if pass_building and opts.building_id:
            kwargs["building_id"] = opts.building_id
        return await fn(**kwargs)
    return _run


def build_catalog() -> dict[str, Job]:
    catalog: dict[str, Job] = {}

    for name, (se, radius, budget, note) in _SCHEDULER_META.items():
        catalog[name] = Job(
            name=name, kind="scheduler", side_effect=se, blast_radius=radius,
            budget_seconds=budget, note=note, factory=_scheduler_job(name),
        )

    # Dry-run-capable cron
    catalog["cron_finance_recompute"] = Job(
        name="cron_finance_recompute", kind="cron", side_effect=DRY_RUN,
        blast_radius=SINGLE if True else ALL, budget_seconds=90.0,
        note="Financial intelligence recompute; dry_run=True writes nothing.",
        factory=_cron_finance_recompute,
    )

    # Mutating / side-effecting cron — building-scoped single-building entrypoints
    mutating_cron = [
        ("cron_arrears_recovery", "run_for_building", 45.0,
         "Computes + writes arrears-recovery state for one building.", {}),
        ("cron_levy_reminders", "run_levy_reminders", 60.0,
         "Sends tier-1 levy reminder emails for one building.", {"tier": 1}),
        ("cron_insurance", "run_insurance_reminders", 30.0,
         "Sends insurance-expiry reminders for one building.", {}),
        ("cron_nsw_compliance", "run_nsw_hub_reminders", 30.0,
         "Sends NSW compliance-hub reminders for one building.", {}),
        ("cron_pm_scheduler", "run_pm_scheduler", 45.0,
         "Generates preventive-maintenance work orders for one building.", {}),
        ("cron_retention_enforcer", "run_retention_check", 45.0,
         "Enforces data-retention policy for one building.", {}),
    ]
    for module, func, budget, note, extra in mutating_cron:
        # cron_arrears_recovery.run_for_building takes a positional building_id;
        # wrap it so the building is passed positionally, not as a kwarg.
        if module == "cron_arrears_recovery":
            def _arrears(opts: RunOptions) -> Awaitable[Any]:
                async def _run() -> Any:
                    from cron.cron_arrears_recovery import run_for_building  # lazy
                    if not opts.building_id:
                        raise ValueError("cron_arrears_recovery requires --building-id")
                    return await run_for_building(opts.building_id)
                return _run()
            factory: Callable[[RunOptions], Awaitable[Any]] = _arrears
        else:
            factory = _cron_call(module, func, pass_building=True, extra_kwargs=extra)
        catalog[module] = Job(
            name=module, kind="cron", side_effect=MUTATING, blast_radius=SINGLE,
            budget_seconds=budget, note=note, factory=factory,
        )

    return catalog


def _allowed(job: Job, args: argparse.Namespace) -> tuple[bool, str]:
    """Return (allowed, reason-if-skipped) under the safety policy."""
    if job.side_effect == READ_ONLY:
        return True, ""
    if job.side_effect == DRY_RUN:
        if args.dry_run:
            return True, ""
        return False, "dry-run-capable; pass --dry-run to time it without writes"
    # MUTATING
    if not (args.allow_mutation and os.environ.get("PERF_HARNESS_ALLOW_MUTATION") == "1"):
        return False, "mutating; needs --allow-mutation AND PERF_HARNESS_ALLOW_MUTATION=1"
    if job.blast_radius == ALL and not args.allow_all_buildings:
        return False, "mutating across ALL buildings; needs --allow-all-buildings"
    if job.blast_radius == SINGLE and not args.building_id:
        return False, "mutating single-building job; needs --building-id to bound it"
    return True, ""


def _throughput(result: Any) -> Optional[int]:
    """Best-effort throughput extraction from a cron return dict."""
    if isinstance(result, dict):
        for key in ("items_processed", "processed", "count", "sent", "updated",
                    "reminders_sent", "work_orders_created", "sealed"):
            if isinstance(result.get(key), int):
                return result[key]
    return None


def run_jobs(jobs: list[Job], args: argparse.Namespace) -> dict[str, Any]:
    opts = RunOptions(
        building_id=args.building_id,
        dry_run=args.dry_run,
        financial_year=args.financial_year,
    )
    results: list[dict[str, Any]] = []
    for job in jobs:
        allowed, reason = _allowed(job, args)
        if not allowed:
            results.append({
                "name": job.name, "kind": job.kind, "side_effect": job.side_effect,
                "blast_radius": job.blast_radius, "status": "skipped", "reason": reason,
                "budget_seconds": job.budget_seconds, "note": job.note,
            })
            print(f"SKIP  {job.name:38s} — {reason}")
            continue

        start = time.perf_counter()
        error = None
        throughput = None
        try:
            result = asyncio.run(job.factory(opts))
            throughput = _throughput(result)
        except Exception as exc:  # noqa: BLE001 — report, don't abort the sweep
            error = f"{type(exc).__name__}: {exc}"
        seconds = round(time.perf_counter() - start, 4)
        within = (error is None) and (
            args.no_enforce or seconds <= job.budget_seconds
        )
        row = {
            "name": job.name, "kind": job.kind, "side_effect": job.side_effect,
            "blast_radius": job.blast_radius,
            "status": "error" if error else ("ok" if within else "budget_exceeded"),
            "seconds": seconds, "budget_seconds": job.budget_seconds,
            "within_budget": within, "throughput": throughput,
            "error": error, "note": job.note,
        }
        results.append(row)
        flag = "ERR " if error else ("OK  " if within else "SLOW")
        tput = f" tput={throughput}" if throughput is not None else ""
        print(f"{flag}  {job.name:38s} {seconds:8.3f}s / budget {job.budget_seconds:6.1f}s{tput}"
              + (f"  {error}" if error else ""))

    summary = {
        "generated_at": _now_iso(),
        "building_id": args.building_id,
        "dry_run": args.dry_run,
        "enforced": not args.no_enforce,
        "counts": {
            "total": len(results),
            "ok": sum(1 for r in results if r.get("status") == "ok"),
            "budget_exceeded": sum(1 for r in results if r.get("status") == "budget_exceeded"),
            "error": sum(1 for r in results if r.get("status") == "error"),
            "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        },
        "results": results,
    }
    return summary


def _now_iso() -> str:
    # Import datetime lazily and only here so the module stays import-light.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Time cron/worker background jobs (latency + throughput).",
    )
    parser.add_argument("--list", action="store_true", help="List the job catalog and exit.")
    parser.add_argument("--jobs", default="", help="Comma-separated job names to run (default: all safe jobs).")
    parser.add_argument("--building-id", default=os.environ.get("BUILDING_ID") or None,
                        help="Scheme number / building_id to scope building-aware jobs.")
    parser.add_argument("--financial-year", default=os.environ.get("FINANCIAL_YEAR", "2026-2027"),
                        help="Financial year for recompute jobs (default 2026-2027).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Enable dry-run-capable jobs (they write nothing).")
    parser.add_argument("--allow-mutation", action="store_true",
                        help="Permit MUTATING jobs (also requires PERF_HARNESS_ALLOW_MUTATION=1).")
    parser.add_argument("--allow-all-buildings", action="store_true",
                        help="Permit MUTATING jobs that iterate every active building.")
    parser.add_argument("--no-enforce", action="store_true",
                        help="Record timings but do not fail on budget breaches.")
    parser.add_argument("--json-out", default="", help="Write the JSON summary to this path.")
    args = parser.parse_args()

    catalog = build_catalog()

    if args.list:
        print(f"{'JOB':40s} {'KIND':9s} {'SIDE-EFFECT':16s} {'RADIUS':16s} {'BUDGET(s)':>9s}  NOTE")
        for job in catalog.values():
            print(f"{job.name:40s} {job.kind:9s} {job.side_effect:16s} "
                  f"{job.blast_radius:16s} {job.budget_seconds:9.1f}  {job.note}")
        return 0

    if args.jobs:
        wanted = [j.strip() for j in args.jobs.split(",") if j.strip()]
        unknown = [j for j in wanted if j not in catalog]
        if unknown:
            print(f"Unknown job(s): {unknown}\nAvailable: {list(catalog)}", file=sys.stderr)
            return 2
        jobs = [catalog[j] for j in wanted]
    else:
        jobs = list(catalog.values())

    summary = run_jobs(jobs, args)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2) + "\n")
        print(f"\nWrote {args.json_out}")

    c = summary["counts"]
    print(f"\n{c['ok']} ok, {c['budget_exceeded']} over budget, "
          f"{c['error']} errored, {c['skipped']} skipped.")
    # Non-zero exit if anything ran and breached budget or errored (unless --no-enforce).
    if not args.no_enforce and (c["budget_exceeded"] or c["error"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
