#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — Batch recorder: persist shadow_ok samples so the
#                                       finance write-cutover gate can open for East Gate.
# Layer: script
# Data flow: CLI → routers.finance._get_unit_dashboard_overview_mongo_fallback (Mongo payload)
#            + finance_shadow_read_service._get_pg_payload_for_route (PG payload)
#            + finance_shadow_read_service.run_shadow_compare (persists core.shadow_diffs +
#            core.shadow_read_coverage_daily) → get_route_shadow_readiness → stdout (building-scoped).
# Related: backend/services/finance_shadow_read_service.py       (run_shadow_compare, readiness)
#          backend/services/finance_write_cutover_service.py     (write gate, lines 316-343)
#          backend/scripts/east_gate_finance_pg_mongo_diff_report_20260804.py  (read-only verify)
#          backend/scripts/set_pg_cutover_toggles.py             (toggle + write-readiness view)
#          docs/architecture/go_live_readiness_2026-09-01.md     (§0a correction, §5 housekeeping)
"""Persist shadow_ok samples for finance.unit_dashboard_overview — the one gate blocking
East Gate's finance PG-WRITE cutover.

WHY THIS EXISTS
---------------
For East Gate (13195), `financial_pg_reads_enabled` and `financial_pg_writes_enabled` are
both already TRUE (per-building overrides, live since 2026-08-04). Reads already serve
PostgreSQL. WRITES do not: the runtime write gate
(finance_write_cutover_service.py:316-343) additionally requires
`finance.unit_dashboard_overview` shadow readiness == "shadow_pass" with critical_count == 0.
That readiness is stuck at "not_started" for a structural reason: once a route serves PG,
the live read path sets run_shadow=False (finance_route_cutover_service.py:724), so no fresh
`shadow_ok` rows are ever recorded for it — the very toggle that turned reads on suppresses
the shadowing the write gate depends on (chicken-and-egg).

`get_route_shadow_readiness` returns "shadow_pass" only when core.shadow_diffs holds at least
one `shadow_ok` sample AND zero divergent rows for the route within the last 24h. This script
records those `shadow_ok` samples by running the SAME comparison the live path would, via the
SAME production function (run_shadow_compare) — it does not reimplement any comparison logic.

SAFETY
------
- Dry-run by default. --apply is required to persist anything.
- Pre-check first: every unit's PG-vs-Mongo payload is compared with the pure comparator
  (no persistence). --apply is REFUSED if ANY unit diverges or errors, because run_shadow_compare
  records a `field_mismatch` row on divergence, which would flip readiness to shadow_fail — the
  opposite of the goal. A divergence here means STOP and investigate which store is correct
  (per the diff-report's ethos), not "record anyway".
- Rows are written with is_test_data=FALSE. This is deliberate and required: the readiness
  query filters is_test_data=FALSE, so test rows would not count. These are real cutover
  control-plane rows.

CONSEQUENCE — READ THIS BEFORE --apply
--------------------------------------
Applying this is the FINAL act of the finance write cutover for East Gate. Once
finance.unit_dashboard_overview reaches shadow_pass, the write gate opens on its own (both
toggles are already enabled) and finance WRITES for building 13195 begin routing to
PostgreSQL. PG and Mongo have been verified in perfect parity
(east_gate_finance_pg_mongo_diff_report_20260804.py, 87/87 units clean, 2026-08-11), and PG
is the system of record, so this is the intended coherent end state — but it is a real,
production write-path change. Confirm you intend it.

REVERSIBLE
----------
shadow_pass is a 24h rolling window: if nothing else changes, the recorded shadow_ok samples
age out after 24h and readiness returns to not_started (writes re-gate to Mongo). To revert
sooner, mark the recorded rows resolved, or flip the toggles off with
set_pg_cutover_toggles.py --disable --apply.

Usage (from repo root; or from backend/ drop the leading `backend/`):
    source backend/venv/bin/activate
    python backend/scripts/east_gate_finance_shadow_record_20260811.py                 # dry-run
    python backend/scripts/east_gate_finance_shadow_record_20260811.py --json
    python backend/scripts/east_gate_finance_shadow_record_20260811.py --apply          # persist
    python backend/scripts/east_gate_finance_shadow_record_20260811.py --building-id 13195 --year 2026
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from database import db  # noqa: E402  (TenantScopedDatabase — auto-scopes by building context)
from request_context import set_ctx_building_id  # noqa: E402
from routers.finance import _get_unit_dashboard_overview_mongo_fallback  # noqa: E402
from services.finance_shadow_read_service import (  # noqa: E402
    _DEFAULT_TOLERANCE_CENTS,
    _ROUTE_TOLERANCES,
    _compare_finance_payloads,
    _get_pg_payload_for_route,
    get_route_shadow_readiness,
    run_shadow_compare,
)

DEFAULT_BUILDING_ID = "13195"
DEFAULT_YEAR = "2026"
_ROUTE = "finance.unit_dashboard_overview"  # the finance write gate's read-compat dependency


def _tol(route_key: str) -> int:
    return _ROUTE_TOLERANCES.get(route_key, _DEFAULT_TOLERANCE_CENTS)


def _diffs_to_rows(diffs) -> list[dict]:
    rows = []
    for d in diffs or []:
        rows.append({
            "field": getattr(d, "field_path", "?"),
            "mongo": getattr(d, "mongo_value", None),
            "pg": getattr(d, "pg_value", None),
            "diff_cents": getattr(d, "diff_cents", None),
            "severity": getattr(d, "severity", None),
        })
    return rows


async def _unit_numbers(building_id: str, limit: int) -> list[str]:
    docs = await db.units.find({}, {"_id": 0, "unit_number": 1}).to_list(None)
    nums = [str(d.get("unit_number")) for d in docs if d.get("unit_number") is not None]
    nums = sorted(set(nums), key=lambda u: int(u) if u.isdigit() else float("inf"))
    return nums if limit in (0, None) else nums[:limit]


async def _precheck(building_id: str, year: str | None, limit_units: int) -> dict:
    """Read-only: build both payloads per unit and compare with the pure comparator.

    Persists NOTHING. Mirrors east_gate_finance_pg_mongo_diff_report_20260804.py so its
    "clean" verdict and this pre-check are the same computation.
    """
    units = await _unit_numbers(building_id, limit_units)
    checked = clean = mismatched = errored = 0
    problems: list[dict] = []
    for unit_number in units:
        checked += 1
        try:
            mongo = await _get_unit_dashboard_overview_mongo_fallback(building_id, unit_number, year)
            pg = await _get_pg_payload_for_route(building_id=building_id, route_key=_ROUTE, mongo_payload=mongo)
            if pg is None:
                errored += 1
                problems.append({"unit": unit_number, "error": "PG payload unavailable"})
                continue
            diffs = _compare_finance_payloads(
                route_key=_ROUTE, mongo_payload=mongo, pg_payload=pg, tolerance_cents=_tol(_ROUTE),
            )
            rows = _diffs_to_rows(diffs)
            if rows:
                mismatched += 1
                problems.append({"unit": unit_number, "diffs": rows})
            else:
                clean += 1
        except Exception as exc:  # noqa: BLE001 — report, never crash the whole run
            errored += 1
            problems.append({"unit": unit_number, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "units_checked": checked,
        "clean": clean,
        "mismatched": mismatched,
        "errored": errored,
        "all_clean": mismatched == 0 and errored == 0 and checked > 0,
        "problems": problems,
    }


async def _record(building_id: str, year: str | None, limit_units: int) -> dict:
    """Persist shadow samples via the production run_shadow_compare for every unit.

    Only reached after _precheck confirmed all_clean, so run_shadow_compare records
    shadow_ok / coverage rows (never a field_mismatch). The 30-minute throttle in
    _should_record_shadow_ok means at most one shadow_ok row is stored — which is all
    readiness needs (pass_samples > 0).
    """
    units = await _unit_numbers(building_id, limit_units)
    compared = matched = not_matched = errored = 0
    for unit_number in units:
        try:
            mongo = await _get_unit_dashboard_overview_mongo_fallback(building_id, unit_number, year)
            pg = await _get_pg_payload_for_route(building_id=building_id, route_key=_ROUTE, mongo_payload=mongo)
            if pg is None:
                # _precheck already refuses --apply on any errored unit, so this is only
                # reachable via a TOCTOU blip (PG went unavailable between the two passes).
                # run_shadow_compare would not crash on None — it records a `pg_unavailable`
                # diff row — but that row counts as a non-`shadow_ok` diff and would drop
                # readiness to `shadow_warn`, blocking the `shadow_pass` this run exists to
                # produce. Skip the unit instead of polluting core.shadow_diffs.
                errored += 1
                print(f"  ! unit {unit_number}: PG payload unavailable — skipped (not recorded)")
                continue
            res = await run_shadow_compare(
                building_id=building_id,
                route_key=_ROUTE,
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=False,  # real cutover control-plane rows; readiness filters is_test_data=FALSE
            )
            compared += 1
            if getattr(res, "matched", False):
                matched += 1
            else:
                not_matched += 1
        except Exception as exc:  # noqa: BLE001
            errored += 1
            print(f"  ! unit {unit_number}: {type(exc).__name__}: {exc}")
    return {"compared": compared, "matched": matched, "not_matched": not_matched, "errored": errored}


async def run(building_id: str, year: str | None, limit_units: int, apply: bool) -> dict:
    set_ctx_building_id(building_id)

    precheck = await _precheck(building_id, year, limit_units)
    readiness_before = await get_route_shadow_readiness(building_id=building_id, route_key=_ROUTE)

    result: dict = {
        "building_id": building_id,
        "year": year or "(latest resolved by services)",
        "route": _ROUTE,
        "mode": "APPLY" if apply else "DRY-RUN",
        "precheck": precheck,
        "readiness_before": readiness_before,
    }

    if not precheck["all_clean"]:
        result["action"] = "REFUSED"
        result["reason"] = (
            "Pre-check found divergent/errored units. Recording now would write a field_mismatch "
            "row and flip readiness to shadow_fail. Investigate which store is correct first "
            "(east_gate_finance_pg_mongo_diff_report_20260804.py) — do NOT record over a divergence."
        )
        return result

    if not apply:
        result["action"] = "DRY-RUN — would record shadow_ok for a clean route"
        result["note"] = (
            "Re-run with --apply to persist. Applying opens the finance write gate: writes for "
            f"building {building_id} begin routing to PostgreSQL (both toggles already enabled)."
        )
        return result

    recorded = await _record(building_id, year, limit_units)
    readiness_after = await get_route_shadow_readiness(building_id=building_id, route_key=_ROUTE)
    result["action"] = "APPLIED"
    result["recorded"] = recorded
    result["readiness_after"] = readiness_after
    result["write_gate_should_open"] = readiness_after.get("status") == "shadow_pass" and int(
        readiness_after.get("critical_count") or 0
    ) == 0
    result["next"] = (
        "Confirm the write gate opened: "
        "python backend/scripts/set_pg_cutover_toggles.py  (the write-readiness table should now "
        "show source=postgres / no 'readiness is not_started' blocker for the levy_payment routes)."
    )
    return result


def _print_human(r: dict) -> None:
    print(f"\nEast Gate finance shadow recorder — building {r['building_id']}  year={r['year']}  [{r['mode']}]\n")
    pc = r["precheck"]
    print(f"  pre-check (read-only): checked={pc['units_checked']} clean={pc['clean']} "
          f"mismatched={pc['mismatched']} errored={pc['errored']}  all_clean={pc['all_clean']}")
    rb = r["readiness_before"]
    print(f"  readiness before: status={rb.get('status')} diffs={rb.get('diff_count')} "
          f"critical={rb.get('critical_count')}")
    if pc.get("problems"):
        print("  problems:")
        for p in pc["problems"][:20]:
            print(f"    - {p}")
    print(f"\n  action: {r['action']}")
    if r.get("reason"):
        print(f"  reason: {r['reason']}")
    if r.get("note"):
        print(f"  note: {r['note']}")
    if r.get("recorded"):
        rec = r["recorded"]
        print(f"  recorded: compared={rec['compared']} matched={rec['matched']} "
              f"not_matched={rec['not_matched']} errored={rec['errored']}")
    if r.get("readiness_after"):
        ra = r["readiness_after"]
        print(f"  readiness after: status={ra.get('status')} diffs={ra.get('diff_count')} "
              f"critical={ra.get('critical_count')}  → write_gate_should_open={r.get('write_gate_should_open')}")
    if r.get("next"):
        print(f"\n  next: {r['next']}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", default=DEFAULT_BUILDING_ID)
    ap.add_argument("--year", default=DEFAULT_YEAR)
    ap.add_argument("--limit-units", type=int, default=0, help="0 = all units")
    ap.add_argument("--apply", action="store_true", help="persist shadow samples (default: dry-run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = asyncio.run(run(args.building_id, args.year, args.limit_units, args.apply))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)

    # Non-zero exit if we refused or applied but the gate did not open, so callers/CI can branch.
    if result.get("action") == "REFUSED":
        sys.exit(3)
    if result.get("action") == "APPLIED" and not result.get("write_gate_should_open"):
        sys.exit(4)


if __name__ == "__main__":
    main()
