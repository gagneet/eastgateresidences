# @featuretrace:finance-shadow-reads — Synthetic traffic generator for the finance_ledger shadow-parity soak.
# Layer: cron
# Data flow: cron → routers/finance.py route handlers (called in-process, real DB reads) →
#            maybe_run_finance_shadow() background tasks → core.shadow_diffs (building-scoped).
# Related: backend/scripts/east_gate_phase_d_shadow_status.py
#          backend/services/finance_shadow_read_service.py
#          docs/fixes/duplicate_receipt_bank_transaction_guard_20260804.md
"""Finance shadow-parity traffic generator.

Real user traffic to the finance dashboard drives finance_ledger's shadow-diff
comparisons (`maybe_run_finance_shadow()`, fired as a background task inside each
route handler). A domain sitting in postgres_shadow/postgres_read/postgres_write
with no real traffic accumulates zero fresh comparisons, so `phase_d_ready`
(7 consecutive clean days per route) never advances no matter how long it waits.

This calls the same route handlers real requests call — same permission checks,
same DB reads, same shadow-compare side effects — just invoked directly instead
of over HTTP, for every building whose finance_ledger domain is at or past
postgres_shadow. Building-agnostic: discovers target buildings and a valid
manager/admin actor per building from the DB, never hardcodes a building_id.

Usage (standalone):
    python cron_finance_shadow_traffic.py                # all eligible buildings
    python cron_finance_shadow_traffic.py --building-id 13195
    python cron_finance_shadow_traffic.py --units-per-building 15

Cron schedule (via system crontab or setup_cron_jobs.sh) — run a few times a day,
spread out, so shadow-diff coverage isn't a single burst:
    0 8,13,18 * * * /usr/bin/python /path/to/cron_finance_shadow_traffic.py >> /var/log/finance_shadow_traffic.log 2>&1
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(_BACKEND_DIR / ".env")

log = logging.getLogger("finance_shadow_traffic")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _eligible_building_ids(explicit: str | None) -> list[str]:
    """Buildings whose finance_ledger domain is at or past postgres_shadow."""
    if explicit:
        return [explicit]
    import asyncpg
    import os

    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    conn = await asyncpg.connect(database_url)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", "00000000-0000-0000-0000-000000000000")
    rows = await conn.fetch(
        """
        SELECT DISTINCT building_id FROM core.domain_cutover_status
        WHERE domain = 'finance_ledger'
          AND mode IN ('postgres_shadow', 'postgres_read', 'postgres_write')
        """
    )
    await conn.close()
    return [r["building_id"] for r in rows]


async def _pick_actor(building_id: str) -> dict | None:
    """A real, active manager/admin user for this building — never a synthetic id."""
    from database import db

    user = await db.users.find_one(
        {
            "building_id": building_id,
            "role": {"$in": ["strata_manager", "strata_admin", "super_admin"]},
            "is_active": {"$ne": False},
        }
    )
    if not user:
        return None
    return {
        "id": user.get("id") or str(user.get("_id")),
        "email": user.get("email"),
        "role": user.get("role"),
        "is_approved": True,
        "building_id": building_id,
    }


async def _sample_unit_numbers(building_id: str, limit: int) -> list[str]:
    from database import db

    units = await db.units.find({"building_id": building_id}, {"unit_number": 1}).to_list(500)
    numbers = [u["unit_number"] for u in units if u.get("unit_number")]
    return numbers[:limit] if limit else numbers


async def _run_and_drain(coro) -> str | None:
    """Await a route handler, then drain any shadow-compare background task it
    scheduled (asyncio.create_task inside the handler) so it actually completes
    before this process exits, instead of being silently cancelled."""
    before = asyncio.all_tasks()
    try:
        await coro
        err = None
    except Exception as exc:
        err = repr(exc)
    after = asyncio.all_tasks() - before - {asyncio.current_task()}
    if after:
        await asyncio.gather(*after, return_exceptions=True)
    return err


async def generate_traffic_for_building(building_id: str, units_per_building: int) -> dict:
    from request_context import set_ctx_building_id
    from routers.finance import (
        get_finance_summary,
        get_unit_dashboard_overview,
        get_levy_kpi,
        get_building_fund_overview,
        get_arrears_board,
    )

    actor = await _pick_actor(building_id)
    if not actor:
        log.warning("building_id=%s: no active manager/admin user found, skipping", building_id)
        return {"building_id": building_id, "skipped": "no_actor"}

    set_ctx_building_id(building_id)
    results = {"building_id": building_id, "actor_email": actor["email"], "routes": {}}

    for label, coro in (
        ("finance.summary", get_finance_summary(year=None, current_user=actor, building_id=building_id)),
        ("finance.building_overview", get_building_fund_overview(year=None, current_user=actor, building_id=building_id)),
        ("finance.levy_kpi", get_levy_kpi(year=None, current_user=actor, building_id=building_id)),
        ("finance.arrears_detail", get_arrears_board(year=None, current_user=actor, building_id=building_id)),
    ):
        err = await _run_and_drain(coro)
        results["routes"][label] = err or "ok"

    unit_numbers = await _sample_unit_numbers(building_id, units_per_building)
    unit_errors = {}
    for unit_number in unit_numbers:
        err = await _run_and_drain(
            get_unit_dashboard_overview(unit_number=unit_number, year=None, current_user=actor, building_id=building_id)
        )
        if err:
            unit_errors[unit_number] = err
    results["routes"]["finance.unit_dashboard_overview"] = (
        "ok" if not unit_errors else f"{len(unit_errors)}/{len(unit_numbers)} failed: {unit_errors}"
    )
    results["units_covered"] = len(unit_numbers)
    return results


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", default=None, help="Limit to one building. Default: all eligible buildings.")
    parser.add_argument("--units-per-building", type=int, default=15, help="Max units to sample per building (0 = all).")
    args = parser.parse_args()

    building_ids = await _eligible_building_ids(args.building_id)
    if not building_ids:
        log.info("No buildings with finance_ledger at postgres_shadow or later. Nothing to do.")
        return 0

    log.info("Generating finance shadow traffic for %d building(s): %s", len(building_ids), building_ids)
    for building_id in building_ids:
        result = await generate_traffic_for_building(building_id, args.units_per_building)
        log.info("  %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
