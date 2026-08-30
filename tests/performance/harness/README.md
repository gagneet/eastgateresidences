# Cron / Worker Timing Harness

`cron_worker_timing.py` times the background jobs that k6 **cannot** reach —
the scheduler `JOBS` registry (`backend/workers/scheduler.py`) and the cron
`run_*` entrypoints (`backend/cron/*.py`). These are not HTTP routes, so they
have no place in the k6 suite; this harness invokes each one **once** and
records wall-clock latency plus whatever throughput count the callable returns,
then checks each against a per-job budget.

It is a latency/throughput probe, **not** a load test — nothing here drives
concurrency.

## Safety model (read before running)

Many jobs mutate data (mark compliance overdue, close proposals, deactivate
guests, post trust interest) or have side effects (send levy-reminder emails).
**Point this at a staging / seeded database, never production.** The harness
enforces:

| Job class | Behaviour |
|---|---|
| `read_only` | Always allowed (e.g. `check_workflow_heartbeats`, `lease_expiry_alerts`). |
| `dry_run_capable` | Runs only with `--dry-run` (writes nothing) — e.g. `cron_finance_recompute`. |
| `mutating` | **Skipped** unless `--allow-mutation` **and** env `PERF_HARNESS_ALLOW_MUTATION=1`. |
| `mutating` + `all_buildings` | Additionally requires `--allow-all-buildings`. |
| `mutating` + `single_building` | Additionally requires `--building-id` to bound the blast radius. |

The default run (no flags) executes **only** the read-only jobs and prints a
`SKIP` line with the reason for every gated job.

## Usage

```bash
# From the repo root. List the catalogue (no backend deps needed):
python tests/performance/harness/cron_worker_timing.py --list

# Safe default — read-only jobs only, write a JSON summary:
python tests/performance/harness/cron_worker_timing.py \
    --building-id 13195 --json-out /tmp/cron_timing.json

# Add the dry-run-capable recompute (still no writes):
python tests/performance/harness/cron_worker_timing.py \
    --building-id 13195 --dry-run

# Time mutating jobs on a STAGING database, bounded to one building:
PERF_HARNESS_ALLOW_MUTATION=1 python tests/performance/harness/cron_worker_timing.py \
    --building-id STAGING-0001 --allow-mutation --jobs cron_pm_scheduler,check_sla_breaches

# Record timings without failing on budget breaches (reporting only):
python tests/performance/harness/cron_worker_timing.py --building-id 13195 --no-enforce
```

Requires the backend importable (its dependencies installed and DB reachable) —
i.e. run it the same way you run `python -m workers.scheduler`. `--list` is the
only mode that works without backend deps.

## Output & exit codes

- Console: one line per job (`OK` / `SLOW` / `ERR` / `SKIP`) with seconds vs
  budget and throughput where available.
- `--json-out`: a machine-readable summary (`counts` + per-job `results`) for
  trend tracking, mirroring the k6 coverage-report shape under
  `docs/performance/k6_run_<date>/`.
- Exit `0` = all run jobs within budget; `1` = a job exceeded budget or errored
  (suppress with `--no-enforce`); `2` = bad arguments.

## Extending the catalogue

The catalogue lives in `build_catalog()`. Scheduler jobs are pulled from the
live `JOBS` registry (so they can't drift); add per-job metadata to
`_SCHEDULER_META`. For a new cron, add an entry with its **verified** `run_*`
signature and correct `side_effect` / `blast_radius` classification — never
guess a signature, and default an uncertain job to `mutating`.
