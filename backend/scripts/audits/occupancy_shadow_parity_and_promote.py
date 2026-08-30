#!/usr/bin/env python3
"""
Occupancy domain: Mongo↔Postgres shadow-parity check + gated promotion.

Turns the occupancy dashboard 🟡→🟢. Occupancy is already a governed dual-path —
every read in routers/occupancy.py branches on
resolve_read_source(building_id, "occupancy"). The PG code path
(analytics.fact_occupancy_snapshot) is complete and the table is populated; it is
dark only because the "occupancy" cutover domain has not been promoted to
postgres_read. This script:

  1. (default, READ-ONLY) Verifies parity between the Postgres occupancy summary
     (analytics.fact_occupancy_snapshot, via the router's own _pg_occupancy_summary)
     and the MongoDB occupancy summary (db.occupancy_status) for a building. Prints
     the current cutover mode. This is the safety gate the control plane's
     "shadow-read" step is a proxy for — we check the data actually matches.

  2. (--promote, MUTATES the control plane) Only if parity holds, advances the
     "occupancy" domain ONE governed step via cutover_status_service.promote_domain
     (mongo_primary → postgres_shadow → postgres_read). Re-run to take the next
     step. Promotion goes no further than postgres_read — occupancy writes stay on
     Mongo by design (the recompute endpoint 409s in PG mode; the PG snapshot is
     refreshed offline by bootstrap_postgres_occupancy_snapshot.py).

SAFETY
------
* Default run writes NOTHING (parity report only).
* --promote is the only mutating path, requires --actor-user-id + --reason, and
  REFUSES to promote if parity fails. It also stops at postgres_read (never
  promotes occupancy to postgres_write).
* Building-agnostic: --building-id required, no "13195" default.

USAGE
-----
    cd backend && source venv/bin/activate

    # 1. Read-only parity check + current mode (do this first)
    python3 scripts/audits/occupancy_shadow_parity_and_promote.py --building-id 13195

    # 2. If parity is OK, promote one step (repeat until read_source=postgres)
    python3 scripts/audits/occupancy_shadow_parity_and_promote.py --building-id 13195 \
        --promote --actor-user-id <super_admin_uuid> --reason "occupancy PG read cutover"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


async def _pg_summary(building_id: str) -> dict[str, Any]:
    """Postgres occupancy summary via the router's own PG helper (ungated)."""
    from routers.occupancy import _pg_occupancy_summary
    r = await _pg_occupancy_summary(building_id)
    return {
        "total_lots": r.total_lots,
        "tenant_count": r.tenant_count,
        "owner_occupied_count": r.owner_occupied_count,
        "investor_unknown_count": r.investor_unknown_count,
    }


async def _mongo_summary(building_id: str) -> dict[str, Any]:
    """MongoDB occupancy summary — mirrors get_occupancy_summary's Mongo branch."""
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    rows = await db.occupancy_status.aggregate(pipeline).to_list(length=10)
    counts = {"tenant": 0, "owner_occupied": 0, "investor_unknown": 0}
    total = 0
    for row in rows:
        status = row.get("_id")
        cnt = int(row.get("count") or 0)
        counts[status] = counts.get(status, 0) + cnt
        total += cnt
    return {
        "total_lots": total,
        "tenant_count": counts["tenant"],
        "owner_occupied_count": counts["owner_occupied"],
        "investor_unknown_count": counts["investor_unknown"],
    }


def _parity(pg: dict[str, Any], mongo: dict[str, Any]) -> tuple[bool, list[str]]:
    fields = ["total_lots", "tenant_count", "owner_occupied_count", "investor_unknown_count"]
    mismatches = [f"{f}: pg={pg.get(f)} mongo={mongo.get(f)}" for f in fields if pg.get(f) != mongo.get(f)]
    return (len(mismatches) == 0 and pg.get("total_lots", 0) > 0), mismatches


async def _current_mode(building_id: str) -> dict[str, Any]:
    from services.cutover_status_service import get_or_default_cutover_status
    status = await get_or_default_cutover_status(building_id, "occupancy")
    return {
        "mode": getattr(status.mode, "value", str(status.mode)),
        "read_source": getattr(status.read_source, "value", str(status.read_source)),
        "write_source": getattr(status.write_source, "value", str(status.write_source)),
        "readiness_status": getattr(status.readiness_status, "value", str(status.readiness_status)),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    building_id = args.building_id
    pg, mongo, mode = await asyncio.gather(
        _pg_summary(building_id), _mongo_summary(building_id), _current_mode(building_id)
    )
    parity_ok, mismatches = _parity(pg, mongo)
    out: dict[str, Any] = {
        "building_id": building_id,
        "cutover_mode_before": mode,
        "pg_summary": pg,
        "mongo_summary": mongo,
        "parity_ok": parity_ok,
        "mismatches": mismatches,
        "promoted": False,
    }

    if not args.promote:
        return out

    if not parity_ok:
        out["promote_error"] = "Parity failed — refusing to promote. Fix the PG snapshot first."
        return out
    if mode["read_source"] == "postgres":
        out["promote_note"] = "occupancy already resolves reads to Postgres — nothing to promote."
        return out
    if not args.actor_user_id or not args.reason:
        out["promote_error"] = "--promote requires --actor-user-id and --reason."
        return out

    from services.cutover_status_service import promote_domain
    try:
        status, audit_id = await promote_domain(
            building_id=building_id,
            domain="occupancy",
            actor_user_id=args.actor_user_id,
            actor_role=args.actor_role,
            reason=args.reason,
        )
        new_mode = getattr(status.mode, "value", str(status.mode))
        new_read = getattr(status.read_source, "value", str(status.read_source))
        out["promoted"] = True
        out["cutover_mode_after"] = {"mode": new_mode, "read_source": new_read}
        out["promote_audit_id"] = str(audit_id)
        if new_read != "postgres":
            out["promote_note"] = (
                f"Advanced one step to '{new_mode}'. Reads still resolve to '{new_read}'. "
                "Re-run with --promote to take the next step toward postgres_read."
            )
    except Exception as exc:  # promote_domain raises HTTPException(409) on a blocked gate
        out["promote_error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
    return out


def _print_human(res: dict[str, Any]) -> None:
    print(f"\n=== Occupancy shadow-parity  building={res['building_id']} ===\n")
    m = res["cutover_mode_before"]
    print(f"Current cutover: mode={m['mode']}  read_source={m['read_source']}  "
          f"write_source={m['write_source']}  readiness={m['readiness_status']}")
    print(f"\n  Postgres (fact_occupancy_snapshot): {res['pg_summary']}")
    print(f"  MongoDB  (occupancy_status):        {res['mongo_summary']}")
    print(f"\n  PARITY: {'✅ MATCH — safe to promote' if res['parity_ok'] else '❌ MISMATCH'}")
    for mm in res["mismatches"]:
        print(f"    - {mm}")
    if res.get("promoted"):
        after = res.get("cutover_mode_after", {})
        print(f"\n  PROMOTED ✅  new mode={after.get('mode')} read_source={after.get('read_source')} "
              f"(audit={res.get('promote_audit_id')})")
    if res.get("promote_note"):
        print(f"  NOTE: {res['promote_note']}")
    if res.get("promote_error"):
        print(f"  PROMOTE BLOCKED: {res['promote_error']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Occupancy Mongo↔PG shadow-parity + gated promotion")
    ap.add_argument("--building-id", required=True, help="e.g. 13195 (no default — multi-tenant rule)")
    ap.add_argument("--promote", action="store_true", help="MUTATES: advance the occupancy domain one step (parity-gated)")
    ap.add_argument("--actor-user-id", help="super_admin user UUID performing the promotion (required with --promote)")
    ap.add_argument("--actor-role", default="super_admin", help="actor role (default super_admin)")
    ap.add_argument("--reason", help="audit reason for the promotion (required with --promote)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    res = asyncio.run(run(args))
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        _print_human(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
