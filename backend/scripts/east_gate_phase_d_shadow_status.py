#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — Phase D shadow read parity dashboard for East Gate.
# Layer: script
# Data flow: CLI → core.shadow_diffs + core.domain_cutover_status → stdout (building-scoped).
# Related: backend/scripts/east_gate_phase_d_activate.py
#          backend/services/finance_shadow_read_service.py
"""Phase D Shadow Status Dashboard — East Gate 13195.

Shows per-route shadow diff counts, unresolved divergences, and the
consecutive-days-of-zero-divergence metric that Phase D requires to reach 7
before promoting to postgres_read.

Usage:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    python backend/scripts/east_gate_phase_d_shadow_status.py
    python backend/scripts/east_gate_phase_d_shadow_status.py --json
    python backend/scripts/east_gate_phase_d_shadow_status.py --last-hours 48
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
BYPASS = "00000000-0000-0000-0000-000000000000"

EG_BUILDING_ID = "13195"
_FINANCE_DOMAIN = "finance_ledger"

# Phase D passes when every contract route has 7 consecutive clean days
_TARGET_CLEAN_DAYS = 7
_CONTRACT_ROUTES = [
    "finance.summary",
    "finance.building_overview",
    "finance.unit_dashboard_overview",
    "finance.levy_kpi",
    "finance.arrears_detail",
]


async def _domain_state(conn: asyncpg.Connection) -> dict:
    """Generated function header.

    Function: _domain_state
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await conn.fetchrow(
        """
        SELECT mode, readiness_status, read_source, write_source, rollback_available,
               previous_mode, updated_at
        FROM core.domain_cutover_status
        WHERE building_id=$1 AND domain=$2
        """,
        EG_BUILDING_ID, _FINANCE_DOMAIN,
    )
    if not row:
        return {"error": "no domain_cutover_status row found"}
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(row).items()}


async def _route_stats(conn: asyncpg.Connection, since: datetime) -> list[dict]:
    """Generated function header.

    Function: _route_stats
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

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
        EG_BUILDING_ID, _FINANCE_DOMAIN, since,
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


async def _consecutive_clean_days(conn: asyncpg.Connection) -> dict[str, int]:
    """For each route, count how many consecutive calendar days (ending today) had zero divergences."""
    today = datetime.now(UTC).date()
    result: dict[str, int] = {}
    for route in _CONTRACT_ROUTES:
        count = 0
        for delta in range(0, 30):
            day = today - timedelta(days=delta)
            day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            day_end = day_start + timedelta(days=1)
            divergent_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM core.shadow_diffs
                WHERE building_id=$1 AND route=$2 AND domain=$3
                  AND created_at >= $4 AND created_at < $5
                  AND resolved = FALSE AND divergence_score > 0
                """,
                EG_BUILDING_ID, route, _FINANCE_DOMAIN, day_start, day_end,
            )
            any_seen = await conn.fetchval(
                """
                SELECT COUNT(*) FROM core.shadow_diffs
                WHERE building_id=$1 AND route=$2 AND domain=$3
                  AND created_at >= $4 AND created_at < $5
                """,
                EG_BUILDING_ID, route, _FINANCE_DOMAIN, day_start, day_end,
            )
            if int(any_seen) == 0 and delta == 0:
                # No traffic yet today — slide to yesterday
                continue
            if int(any_seen) == 0:
                # Historical day with no traffic — streak requires verified clean days
                break
            if int(divergent_count) > 0:
                break
            count += 1
        result[route] = count
    return result


async def _sample_unresolved(conn: asyncpg.Connection, limit: int = 5) -> list[dict]:
    """Generated function header.

    Function: _sample_unresolved
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

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
        EG_BUILDING_ID, _FINANCE_DOMAIN, limit,
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


async def _audit_log_tail(conn: asyncpg.Connection, limit: int = 5) -> list[dict]:
    """Generated function header.

    Function: _audit_log_tail
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

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
        EG_BUILDING_ID, _FINANCE_DOMAIN, limit,
    )
    return [
        {
            "action": r["action"],
            "from_mode": r["from_mode"],
            "to_mode": r["to_mode"],
            "reason": r["reason"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def run(last_hours: int = 168) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    since = datetime.now(UTC) - timedelta(hours=last_hours)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        domain = await _domain_state(conn)
        route_stats = await _route_stats(conn, since)
        clean_days = await _consecutive_clean_days(conn)
        unresolved_samples = await _sample_unresolved(conn)
        audit_tail = await _audit_log_tail(conn)

        # Phase D pass criteria: all routes have >= TARGET_CLEAN_DAYS consecutive clean days
        routes_at_target = {r: d for r, d in clean_days.items() if d >= _TARGET_CLEAN_DAYS}
        phase_d_ready = len(routes_at_target) == len(_CONTRACT_ROUTES)

        # Aggregate totals for the window
        total_unresolved = sum(r["unresolved"] for r in route_stats)
        total_divergent = sum(r["divergent_unresolved"] for r in route_stats)
        total_shadow_ok = sum(r["shadow_ok"] for r in route_stats)
        routes_with_no_data = [r for r in _CONTRACT_ROUTES if not any(s["route"] == r for s in route_stats)]

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "window_hours": last_hours,
            "building_id": EG_BUILDING_ID,
            "domain": _FINANCE_DOMAIN,
            "domain_state": domain,
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
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    d = data["domain_state"]
    print(f"\n{'='*60}")
    print(f"  Phase D Shadow Status  |  {EG_BUILDING_ID}  |  {data['as_of'][:19]}")
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
    print("  Consecutive clean days per route:")
    for route, days in data["consecutive_clean_days_per_route"].items():
        status = "✅" if days >= data["phase_d_target_clean_days"] else ("⚠️ " if days > 0 else "❌")
        print(f"    {status} {route:<40} {days} day(s)")
    if data["routes_with_no_shadow_data"]:
        print()
        print("  Routes with NO shadow data yet (no traffic or shadow disabled):")
        for r in data["routes_with_no_shadow_data"]:
            print(f"    • {r}")
    if data["recent_divergences_sample"]:
        print()
        print("  Recent unresolved divergences:")
        for d in data["recent_divergences_sample"]:
            print(f"    [{d['created_at'][:19]}] {d['route']} — score={d['divergence_score']:.2f} diff_type={d['diff_type']}")
    if data["audit_log_tail"]:
        print()
        print("  Recent audit log:")
        for e in data["audit_log_tail"]:
            print(f"    [{e['created_at'][:19]}] {e['action']} {e['from_mode']} → {e['to_mode']}")
    print(f"{'='*60}\n")


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_phase_d_shadow_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Phase D shadow parity dashboard for East Gate finance routes.")
    parser.add_argument("--last-hours", type=int, default=168, help="Stats window in hours (default 168 = 7 days)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="Output JSON instead of table")
    args = parser.parse_args()
    data = asyncio.run(run(last_hours=args.last_hours))
    if args.json_out:
        print(json.dumps(data, indent=2, default=str))
    else:
        _pretty(data)


if __name__ == "__main__":
    main()
