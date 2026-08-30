#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — READ-ONLY verifier: actual finance_ledger request-time routing (PG vs Mongo).
# Layer: script
# Data flow: CLI -> get_or_default_cutover_status + is_cutover_feature_enabled + get_route_shadow_readiness
#            + static route policies -> reconstructed per-route routing verdict -> stdout. NO writes.
# Related: backend/services/finance_route_cutover_service.py   (read gate, lines 231-257)
#          backend/services/finance_write_cutover_service.py   (write gate, lines 316-343)
#          docs/deployment/east_gate_finance_ledger_read_first_cutover_runbook.md
"""READ-ONLY: what is finance_ledger ACTUALLY serving right now — PostgreSQL or MongoDB?

The `domain_cutover_status` row (mode / read_source / write_source) is the *declared*
state. It is NOT what routes at request time: the runtime read gate
(finance_route_cutover_service.py:231-257) and write gate
(finance_write_cutover_service.py:316-343) independently re-check shadow parity,
per-building toggles, and identity/financial readiness snapshots, and fall back to
Mongo when any fails. A mode of `postgres_write` with those gates unmet still serves
Mongo.

This script reconstructs that per-route decision from READ-ONLY inputs and prints,
for every finance contract route, the effective source and the FIRST failing gate.

WHY reconstruct instead of calling the real runtime functions: those call
require_domain_source(), which PERSISTS a guard-audit row every invocation
(domain_source_guard.py:285,361). Probing them would write audit spam. This script
only issues SELECTs (cutover status, feature-toggle resolution, shadow-diff counts)
and evaluates the same gate conditions in-process. It cites the authoritative gate
lines above; if in doubt, the runtime functions are the source of truth.

Usage (from repo root):
    source backend/venv/bin/activate
    python backend/scripts/east_gate_finance_ledger_routing_verify_20260804.py
    python backend/scripts/east_gate_finance_ledger_routing_verify_20260804.py --json
    python backend/scripts/east_gate_finance_ledger_routing_verify_20260804.py --building-id 13195
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from models.cutover_status import CutoverMode  # noqa: E402
from services.cutover_config_service import (  # noqa: E402
    FINANCIAL_PG_READS_ENABLED,
    FINANCIAL_PG_WRITES_ENABLED,
    UMBRELLA_FEATURE_KEY,
    is_cutover_feature_enabled,
)
from services.cutover_status_service import get_or_default_cutover_status  # noqa: E402
from services.finance_route_cutover_service import list_finance_route_policies  # noqa: E402
from services.finance_shadow_read_service import get_route_shadow_readiness  # noqa: E402
from services.finance_write_cutover_service import list_finance_write_route_policies  # noqa: E402

_READ_PG_MODES = {
    CutoverMode.postgres_read.value,
    CutoverMode.postgres_write.value,
    "mongo_archive",
}
_UNIT_ROUTE = "finance.unit_dashboard_overview"  # the write gate's read-compat dependency


def _shadow_ok(readiness: dict) -> bool:
    return readiness.get("status") == "shadow_pass" and int(readiness.get("critical_count") or 0) == 0


def _read_verdict(policy, *, domain_mode: str, pg_reads: bool, readiness: dict) -> tuple[str, str]:
    """Mirror finance_route_cutover_service.py:231-257 (first failing gate wins)."""
    if not getattr(policy, "read_only", False):
        return "mongo", "route is not read-only"
    if not getattr(policy, "postgres_read_supported", False):
        return "mongo", getattr(policy, "notes", "") or "postgres read not supported for this route"
    if domain_mode not in _READ_PG_MODES:
        return "mongo", f"domain mode is {domain_mode!r} (needs postgres_read+)"
    if not pg_reads:
        return "mongo", f"{FINANCIAL_PG_READS_ENABLED} (or umbrella) resolves False"
    if readiness.get("status") != "shadow_pass":
        return "mongo", f"shadow readiness is {readiness.get('status')!r} (needs shadow_pass)"
    if int(readiness.get("critical_count") or 0) > 0:
        return "mongo", f"{readiness.get('critical_count')} critical shadow diff(s)"
    return "postgres", "all read gates pass"


def _write_verdict(policy, *, domain_mode: str, pg_writes: bool, pg_reads: bool,
                   fin_onboarding_pass: bool, identity_pass: bool, unit_readiness: dict) -> tuple[str, str]:
    """Mirror finance_write_cutover_service.py:316-343 (first failing gate wins)."""
    if not getattr(policy, "postgres_write_supported", False):
        return "mongo", getattr(policy, "notes", "") or "postgres write not supported for this route"
    if domain_mode != CutoverMode.postgres_write.value:
        return "mongo", f"domain mode is {domain_mode!r} (needs postgres_write)"
    if not pg_writes:
        return "mongo", f"{FINANCIAL_PG_WRITES_ENABLED} (or umbrella) resolves False"
    if not pg_reads:
        return "mongo", f"{FINANCIAL_PG_READS_ENABLED} (or umbrella) resolves False"
    if not fin_onboarding_pass:
        return "mongo", "financial_onboarding readiness snapshot is not 'pass'"
    if not identity_pass:
        return "mongo", "identity_core.identity_foundation snapshot is not 'pass'"
    if unit_readiness.get("status") != "shadow_pass":
        return "mongo", f"{_UNIT_ROUTE} readiness is {unit_readiness.get('status')!r} (needs shadow_pass)"
    if int(unit_readiness.get("critical_count") or 0) > 0:
        return "mongo", f"{_UNIT_ROUTE} has {unit_readiness.get('critical_count')} critical shadow diff(s)"
    if getattr(policy, "idempotency_required", False):
        return "postgres", "all write gates pass (route also requires an Idempotency-Key header at call time)"
    return "postgres", "all write gates pass"


async def run(building_id: str) -> dict:
    fin = await get_or_default_cutover_status(building_id, "finance_ledger")
    ident = await get_or_default_cutover_status(building_id, "identity_core")

    fin_mode = fin.mode.value if fin else "unknown"
    fin_snap = (fin.p0_snapshot or {}).get("financial_onboarding", {}) if fin else {}
    ident_snap = (ident.p0_snapshot or {}).get("identity_foundation", {}) if ident else {}
    fin_onboarding_pass = fin_snap.get("status") == "pass"
    identity_pass = ident_snap.get("status") == "pass"

    umbrella_on = await is_cutover_feature_enabled(building_id, UMBRELLA_FEATURE_KEY)
    pg_reads = await is_cutover_feature_enabled(building_id, FINANCIAL_PG_READS_ENABLED)
    pg_writes = await is_cutover_feature_enabled(building_id, FINANCIAL_PG_WRITES_ENABLED)

    # Read routes
    read_rows = []
    for policy in list_finance_route_policies():
        readiness = await get_route_shadow_readiness(building_id=building_id, route_key=policy.route_key)
        source, why = _read_verdict(policy, domain_mode=fin_mode, pg_reads=pg_reads, readiness=readiness)
        read_rows.append({
            "route_key": policy.route_key,
            "effective_source": source,
            "reason": why,
            "shadow_status": readiness.get("status"),
            "critical_count": readiness.get("critical_count"),
        })

    # Write routes — all share the unit_dashboard_overview read-compat dependency
    unit_readiness = await get_route_shadow_readiness(building_id=building_id, route_key=_UNIT_ROUTE)
    write_rows = []
    for policy in list_finance_write_route_policies():
        source, why = _write_verdict(
            policy, domain_mode=fin_mode, pg_writes=pg_writes, pg_reads=pg_reads,
            fin_onboarding_pass=fin_onboarding_pass, identity_pass=identity_pass,
            unit_readiness=unit_readiness,
        )
        write_rows.append({
            "route_key": policy.route_key,
            "effective_source": source,
            "reason": why,
        })

    reads_pg = sum(1 for r in read_rows if r["effective_source"] == "postgres")
    writes_pg = sum(1 for r in write_rows if r["effective_source"] == "postgres")

    return {
        "building_id": building_id,
        "declared": {
            "finance_ledger_mode": fin_mode,
            "finance_ledger_readiness": fin.readiness_status.value if fin else None,
            "read_source": fin.read_source.value if fin else None,
            "write_source": fin.write_source.value if fin else None,
            "identity_core_mode": ident.mode.value if ident else None,
        },
        "gate_inputs": {
            "umbrella_financial_integration_layer_v2": umbrella_on,
            "financial_pg_reads_enabled": pg_reads,
            "financial_pg_writes_enabled": pg_writes,
            "financial_onboarding_snapshot_pass": fin_onboarding_pass,
            "identity_foundation_snapshot_pass": identity_pass,
            f"{_UNIT_ROUTE}_shadow": {
                "status": unit_readiness.get("status"),
                "critical_count": unit_readiness.get("critical_count"),
            },
        },
        "verdict_summary": {
            "reads_served_by_postgres": f"{reads_pg}/{len(read_rows)}",
            "writes_served_by_postgres": f"{writes_pg}/{len(write_rows)}",
            "headline": (
                "PG is authoritative for reads AND writes"
                if reads_pg == len(read_rows) and writes_pg == len(write_rows)
                else "Declared mode may be postgres_write, but one or more runtime gates "
                     "still route to Mongo — see reasons below."
            ),
        },
        "read_routes": read_rows,
        "write_routes": write_rows,
        "note": "READ-ONLY reconstruction of finance_route_cutover_service.py:231-257 and "
                "finance_write_cutover_service.py:316-343. The authoritative runtime functions "
                "additionally persist a guard-audit row per call; this script does not.",
    }


def _print_human(result: dict) -> None:
    d = result["declared"]
    print(f"\nEast Gate finance_ledger routing — building {result['building_id']}")
    print(f"  DECLARED: mode={d['finance_ledger_mode']} readiness={d['finance_ledger_readiness']} "
          f"read_source={d['read_source']} write_source={d['write_source']} | identity_core={d['identity_core_mode']}")
    gi = result["gate_inputs"]
    print("  GATE INPUTS:")
    for k, v in gi.items():
        print(f"    - {k}: {v}")
    vs = result["verdict_summary"]
    print(f"\n  >>> {vs['headline']}")
    print(f"      reads on PG: {vs['reads_served_by_postgres']}   writes on PG: {vs['writes_served_by_postgres']}")
    print("\n  READ ROUTES:")
    for r in result["read_routes"]:
        print(f"    [{r['effective_source']:8}] {r['route_key']:34} — {r['reason']}")
    print("  WRITE ROUTES:")
    for r in result["write_routes"]:
        print(f"    [{r['effective_source']:8}] {r['route_key']:34} — {r['reason']}")
    print(f"\n  {result['note']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="READ-ONLY: reconstruct actual finance_ledger request-time routing (PG vs Mongo)."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of the human summary.")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id))
    if args.json:
        print(json.dumps(result, indent=2, default=str, sort_keys=True))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
