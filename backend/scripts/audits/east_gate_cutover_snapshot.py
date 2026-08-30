#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — Read-only operator snapshot of live cutover state.
# Layer: script
# Data flow: reads core.domain_cutover_status (via list_all_cutover_status) +
#            finance route policy engine (via get_finance_route_readiness_table).
# Related: backend/services/cutover_status_service.py
#          backend/services/finance_route_cutover_service.py
#          backend/routers/cutover_admin.py
"""Dump the LIVE cutover control-plane rows and per-route blocked_reason for a building.

Read-only. Makes no writes and promotes nothing. Answers two questions at once:

  1. Which domains are registered in core.domain_cutover_status for this building,
     and what mode / read_source / write_source / readiness each one is in.
  2. For every finance route, what source it currently resolves to, whether it is
     eligible for postgres_read, and — the important column — the exact
     `blocked_reason` string explaining why it is NOT serving PostgreSQL yet.

Run on the SERVER (needs DB access + backend venv):

    cd backend && venv/bin/python3 scripts/audits/east_gate_cutover_snapshot.py
    cd backend && venv/bin/python3 scripts/audits/east_gate_cutover_snapshot.py 13195 --json

The first positional arg is the building_id (defaults to 13195 / East Gate).
Pass --json for machine-readable output instead of the aligned table.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Make `backend/` importable regardless of the current working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


async def _gather(building_id: str) -> dict:
    from services.cutover_status_service import list_all_cutover_status
    from services.finance_route_cutover_service import get_finance_route_readiness_table

    domains = await list_all_cutover_status(building_id_filter=building_id)
    routes = await get_finance_route_readiness_table(building_id=building_id)
    return {
        "building_id": building_id,
        "domains": [d.model_dump(mode="json") for d in domains],
        "routes": routes,
    }


def _print_table(snap: dict) -> None:
    bid = snap["building_id"]
    print(f"\n=== CONTROL PLANE — core.domain_cutover_status (building {bid}) ===\n")
    domains = snap["domains"]
    if not domains:
        print("  (no rows — every domain defaults to mongo_primary / mongo reads)\n")
    else:
        hdr = f"{'domain':<26} {'mode':<16} {'read':<9} {'write':<9} {'readiness':<16} {'rollback':<8}"
        print(hdr)
        print("-" * len(hdr))
        for d in domains:
            print(
                f"{d['domain']:<26} {d['mode']:<16} {d['read_source']:<9} "
                f"{d['write_source']:<9} {d['readiness_status']:<16} "
                f"{str(d['rollback_available']):<8}"
            )

    print(f"\n=== FINANCE ROUTES — resolved source + why (building {bid}) ===\n")
    for r in snap["routes"]:
        serving = "PostgreSQL" if r["current_source"] == "postgres" else "MongoDB"
        frozen = "" if r["shadow_monitoring_active"] else "  (frozen/monitoring-stopped)"
        elig = "YES" if r["eligible_for_postgres_read"] else "no"
        print(f"• {r['route_key']:<34} {r['path']}")
        print(
            f"    serving={serving:<11} domain_mode={r['domain_mode']:<15} "
            f"eligible_pg_read={elig:<4} "
            f"diffs={r['diff_count']}/crit={r['critical_count']}{frozen}"
        )
        print(
            f"    shadow_supported={r['shadow_supported']}  "
            f"postgres_read_supported={r['postgres_read_supported']}"
        )
        reason = r.get("blocked_reason")
        if reason:
            print(f"    BLOCKED: {reason}")
        else:
            print(f"    BLOCKED: <none — this route is serving/eligible for PostgreSQL>")
        print()


def main() -> None:
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    building_id = args[0] if args else "13195"

    snap = asyncio.run(_gather(building_id))

    if as_json:
        print(json.dumps(snap, indent=2, default=str))
    else:
        _print_table(snap)


if __name__ == "__main__":
    main()
