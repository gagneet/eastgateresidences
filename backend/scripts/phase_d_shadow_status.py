#!/usr/bin/env python3
# @featuretrace:pg-migration-shadow — Generalized Phase D shadow parity dashboard, any domain.
# Layer: script
# Data flow: CLI → core.shadow_diffs + core.domain_cutover_status + core.shadow_read_coverage_daily
#            (via services.shadow_read_service.classify_route_day) → stdout (building-scoped).
# Related: backend/scripts/phase_d_activate.py
#          backend/scripts/east_gate_phase_d_shadow_status.py
#          backend/services/shadow_read_service.py
#          docs/migration/shadow-correction-updates-plan-p2.md
"""Generalized Phase D shadow parity dashboard — any domain, any building.

Unlike east_gate_phase_d_shadow_status.py (finance-only, infers "clean day" purely from the
absence of divergent core.shadow_diffs rows — which cannot distinguish a genuinely clean day
from an idle one with zero traffic), this script's clean-day streak uses
services.shadow_read_service.classify_route_day(), backed by core.shadow_read_coverage_daily's
atomic attempted-comparison counters. Only a day classified "clean" (adequate coverage, zero
mismatches/infrastructure failures) advances the streak — "no_traffic" and
"insufficient_coverage" days do not, and do not silently count as clean either.

Usage:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    python backend/scripts/phase_d_shadow_status.py --domain finance_ledger
    python backend/scripts/phase_d_shadow_status.py --domain identity_core --json
    python backend/scripts/phase_d_shadow_status.py --domain trust_ledger --last-hours 48
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from services.cutover_status_service import canonical_domain  # noqa: E402
from services.shadow_read_service import classify_route_day  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
BYPASS = "00000000-0000-0000-0000-000000000000"

_TARGET_CLEAN_DAYS = 7

# Contract routes per domain. Update this when a domain's shadow comparators are actually
# built (Phase D added identity_core's 4 routes; Phase G will add trust_ledger/
# trust_reconciliation's) — keep in lockstep with each domain's *_shadow_read_service.py
# comparator registration dict, not guessed independently.
DOMAIN_CONTRACT_ROUTES: dict[str, list[str]] = {
    "finance_ledger": [
        "finance.summary",
        "finance.building_overview",
        "finance.unit_dashboard_overview",
        "finance.levy_kpi",
        "finance.arrears_detail",
    ],
    "identity_core": [
        "identity.users.list",
        "ownership.owner.current",
        "ownership.owner.all_units",
        "ownership.owner.agm_voters",
        "ownership.owner.membership_check",
    ],
    "trust_ledger": [
        "trust_v2.accounts.summary",
    ],
    "trust_reconciliation": [
        "trust_reconciliation.summary",
    ],
}


async def _domain_state(conn: asyncpg.Connection, building_id: str, domain: str) -> dict:
    """Generated function header.

    Function: _domain_state
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await conn.fetchrow(
        """
        SELECT mode, readiness_status, read_source, write_source, rollback_available,
               previous_mode, updated_at
        FROM core.domain_cutover_status
        WHERE building_id=$1 AND domain=$2
        """,
        building_id, domain,
    )
    if not row:
        return {"error": "no domain_cutover_status row found"}
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(row).items()}


async def _route_stats(conn: asyncpg.Connection, building_id: str, domain: str, since: datetime, routes: list[str]) -> list[dict]:
    """Generated function header.

    Function: _route_stats
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await conn.fetch(
        """
        SELECT
            route,
            COUNT(*) FILTER (WHERE resolved = FALSE) AS unresolved,
            COUNT(*) FILTER (WHERE resolved = TRUE)  AS resolved_count,
            COUNT(*) FILTER (WHERE resolved = FALSE AND divergence_score > 0) AS divergent,
            COUNT(*) FILTER (WHERE diff_type = 'pg_unavailable') AS pg_unavailable,
            COUNT(*) FILTER (WHERE diff_type = 'shadow_ok' OR divergence_score = 0) AS shadow_ok,
            MAX(created_at) AS last_seen
        FROM core.shadow_diffs
        WHERE building_id=$1 AND domain=$2 AND created_at >= $3
        GROUP BY route
        ORDER BY route
        """,
        building_id, domain, since,
    )
    return [
        {
            "route": r["route"],
            "unresolved": int(r["unresolved"]),
            "resolved": int(r["resolved_count"]),
            "divergent_unresolved": int(r["divergent"]),
            "pg_unavailable": int(r["pg_unavailable"]),
            "shadow_ok": int(r["shadow_ok"]),
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        }
        for r in rows
    ]


async def _consecutive_clean_days(building_id: str, domain: str, routes: list[str]) -> dict[str, dict]:
    """For each route, walk backward from today counting a real coverage-backed streak.

    A day only advances the streak if classify_route_day() returns "clean" — "no_traffic"
    and "insufficient_coverage" days stop the streak (they don't count as clean, but also
    aren't punished as "dirty"; they're reported separately so an operator can tell the
    difference between "broken" and "idle").
    """
    today = datetime.now(UTC).date()
    result: dict[str, dict] = {}
    for route in routes:
        streak = 0
        last_classification = None
        for delta in range(0, 30):
            day = today - timedelta(days=delta)
            classification = await classify_route_day(
                building_id=building_id, domain=domain, route=route, coverage_date=day,
            )
            if delta == 0:
                last_classification = classification
            if classification == "no_traffic" and delta == 0:
                continue  # today may just not have run yet — slide to yesterday
            if classification != "clean":
                break
            streak += 1
        result[route] = {"streak": streak, "latest_day_status": last_classification}
    return result


async def _sample_unresolved(conn: asyncpg.Connection, building_id: str, domain: str, limit: int = 5) -> list[dict]:
    """Generated function header.

    Function: _sample_unresolved
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await conn.fetch(
        """
        SELECT route, diff_type, divergence_score, mongo_value, created_at
        FROM core.shadow_diffs
        WHERE building_id=$1 AND domain=$2 AND resolved=FALSE AND divergence_score > 0
        ORDER BY created_at DESC
        LIMIT $3
        """,
        building_id, domain, limit,
    )
    out = []
    for r in rows:
        mongo_val = r["mongo_value"]
        if isinstance(mongo_val, str):
            try:
                mongo_val = json.loads(mongo_val)
            except Exception:
                pass
        out.append({
            "route": r["route"],
            "diff_type": r["diff_type"],
            "divergence_score": float(r["divergence_score"] or 0),
            "field_diffs": mongo_val.get("fields") if isinstance(mongo_val, dict) else None,
            "created_at": r["created_at"].isoformat(),
        })
    return out


async def _audit_log_tail(conn: asyncpg.Connection, building_id: str, domain: str, limit: int = 5) -> list[dict]:
    """Generated function header.

    Function: _audit_log_tail
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await conn.fetch(
        """
        SELECT action, from_mode, to_mode, reason, created_at
        FROM core.cutover_audit_log
        WHERE building_id=$1 AND domain=$2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        building_id, domain, limit,
    )
    return [
        {
            "action": r["action"], "from_mode": r["from_mode"], "to_mode": r["to_mode"],
            "reason": r["reason"], "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def run(*, building_id: str, domain: str, last_hours: int = 168) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    domain = canonical_domain(domain)
    routes = DOMAIN_CONTRACT_ROUTES.get(domain)
    if routes is None:
        return {"error": f"No contract routes registered for domain {domain!r}. "
                          f"Known domains: {sorted(DOMAIN_CONTRACT_ROUTES)}"}

    since = datetime.now(UTC) - timedelta(hours=last_hours)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        domain_state = await _domain_state(conn, building_id, domain)
        route_stats = await _route_stats(conn, building_id, domain, since, routes)
        clean_days = await _consecutive_clean_days(building_id, domain, routes)
        unresolved_samples = await _sample_unresolved(conn, building_id, domain)
        audit_tail = await _audit_log_tail(conn, building_id, domain)

        routes_at_target = {r: c["streak"] for r, c in clean_days.items() if c["streak"] >= _TARGET_CLEAN_DAYS}
        phase_d_ready = len(routes_at_target) == len(routes)

        total_unresolved = sum(r["unresolved"] for r in route_stats)
        total_divergent = sum(r["divergent_unresolved"] for r in route_stats)
        total_shadow_ok = sum(r["shadow_ok"] for r in route_stats)
        routes_with_no_data = [r for r in routes if not any(s["route"] == r for s in route_stats)]

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "window_hours": last_hours,
            "building_id": building_id,
            "domain": domain,
            "domain_state": domain_state,
            "phase_d_target_clean_days": _TARGET_CLEAN_DAYS,
            "phase_d_ready": phase_d_ready,
            "consecutive_clean_days_per_route": clean_days,
            "routes_at_target": list(routes_at_target.keys()),
            "routes_with_no_shadow_data": routes_with_no_data,
            "summary": {
                "total_unresolved": total_unresolved,
                "total_divergent_unresolved": total_divergent,
                "total_shadow_ok": total_shadow_ok,
            },
            "per_route": route_stats,
            "recent_divergences_sample": unresolved_samples,
            "audit_log_tail": audit_tail,
        }
    finally:
        await conn.close()


def _pretty(data: dict) -> None:
    """Generated function header.

    Function: _pretty
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if "error" in data:
        print(f"ERROR: {data['error']}")
        return
    d = data["domain_state"]
    print(f"\n{'='*60}")
    print(f"  Phase D Shadow Status  |  {data['domain']}  |  {data['building_id']}  |  {data['as_of'][:19]}")
    print(f"{'='*60}")
    print(f"  Domain mode       : {d.get('mode', '?')}")
    print(f"  Readiness         : {d.get('readiness_status', '?')}")
    print(f"  Phase D target    : {data['phase_d_target_clean_days']} consecutive clean days per route")
    print(f"  Phase D ready     : {'✅ YES — ready to promote to postgres_read' if data['phase_d_ready'] else '❌ NO'}")
    print()
    print(f"  Window            : last {data['window_hours']}h")
    print(f"  Shadow OK         : {data['summary']['total_shadow_ok']}")
    print(f"  Unresolved diffs  : {data['summary']['total_unresolved']}")
    print(f"  Divergent (unresd): {data['summary']['total_divergent_unresolved']}")
    print()
    print("  Consecutive clean days per route (coverage-backed):")
    for route, info in data["consecutive_clean_days_per_route"].items():
        streak = info["streak"]
        status = "✅" if streak >= data["phase_d_target_clean_days"] else ("⚠️ " if streak > 0 else "❌")
        print(f"    {status} {route:<40} {streak} day(s)  (latest day: {info['latest_day_status']})")
    if data["routes_with_no_shadow_data"]:
        print()
        print("  Routes with NO shadow data yet (no traffic or shadow disabled):")
        for r in data["routes_with_no_shadow_data"]:
            print(f"    • {r}")
    if data["recent_divergences_sample"]:
        print()
        print("  Recent unresolved divergences:")
        for dv in data["recent_divergences_sample"]:
            print(f"    [{dv['created_at'][:19]}] {dv['route']} — score={dv['divergence_score']:.2f} diff_type={dv['diff_type']}")
    if data["audit_log_tail"]:
        print()
        print("  Recent audit log:")
        for e in data["audit_log_tail"]:
            print(f"    [{e['created_at'][:19]}] {e['action']} {e['from_mode']} → {e['to_mode']}")
    print(f"{'='*60}\n")


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Generalized Phase D shadow parity dashboard for any domain.")
    parser.add_argument("--domain", required=True, help="e.g. finance_ledger, identity_core, trust_ledger")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--last-hours", type=int, default=168, help="Stats window in hours (default 168 = 7 days)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="Output JSON instead of table")
    args = parser.parse_args()
    data = asyncio.run(run(building_id=args.building_id, domain=args.domain, last_hours=args.last_hours))
    if args.json_out:
        print(json.dumps(data, indent=2, default=str))
    else:
        _pretty(data)


if __name__ == "__main__":
    main()
