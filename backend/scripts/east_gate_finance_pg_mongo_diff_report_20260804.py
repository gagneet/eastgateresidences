#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — READ-ONLY PG-vs-Mongo diff report for East Gate finance routes.
# Layer: script
# Data flow: CLI -> production Mongo builders (_get_*_mongo_fallback) + PG fetch
#            (_get_pg_payload_for_route) + pure comparator (_compare_finance_payloads) -> stdout.
#            NO writes: does NOT call run_shadow_compare/compare_and_record, so core.shadow_diffs
#            is untouched.
# Related: backend/services/finance_shadow_read_service.py
#          backend/routers/finance.py
"""READ-ONLY: recompute the live PG-vs-Mongo divergence for East Gate finance routes.

For finance.building_overview (currently shadow_fail) and finance.unit_dashboard_overview
(currently not_started, per-unit), this rebuilds BOTH payloads exactly as the shadow gate
would — the production Mongo fallback builder and the same _get_pg_payload_for_route() the
gate uses — then runs the SAME pure comparator (_compare_finance_payloads). So "0 diffs
here" == "this route would reach shadow_pass". Nothing is persisted; only SELECTs run.

It ALSO prints the fields the comparators deliberately EXCLUDE — total_outstanding (Mongo)
vs total_arrears_cents (PG). Those are excluded from shadow parity because the two stores
compute "outstanding"/"arrears" by DIFFERENT definitions (finance_shadow_read_service.py
:267-287: "mongo=$122,061.03/85 units vs pg=$16,128.08/16 units … not a data bug, a
comparison-target mismatch … reconciling requires a product decision"). Passing the shadow
gate therefore does NOT prove PG's arrears equals Mongo's — this report shows that gap in
the open so a "clean" verdict is not mistaken for more than it is.

Usage (from repo root):
    source backend/venv/bin/activate
    python backend/scripts/east_gate_finance_pg_mongo_diff_report_20260804.py
    python backend/scripts/east_gate_finance_pg_mongo_diff_report_20260804.py --json
    python backend/scripts/east_gate_finance_pg_mongo_diff_report_20260804.py --year 2026 --limit-units 0
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from database import db  # noqa: E402  (TenantScopedDatabase — auto-scopes by building context)
from request_context import set_ctx_building_id  # noqa: E402
from routers.finance import (  # noqa: E402
    _get_building_overview_mongo_fallback,
    _get_unit_dashboard_overview_mongo_fallback,
)
from services.finance_shadow_read_service import (  # noqa: E402
    _DEFAULT_TOLERANCE_CENTS,
    _ROUTE_TOLERANCES,
    _compare_finance_payloads,
    _get_pg_payload_for_route,
)


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


async def _building_overview(building_id: str, year: str | None) -> dict:
    route = "finance.building_overview"
    out: dict = {"route": route}
    try:
        mongo = await _get_building_overview_mongo_fallback(building_id, year)
        pg = await _get_pg_payload_for_route(building_id=building_id, route_key=route, mongo_payload=mongo)
        if pg is None:
            return {**out, "error": "PG payload unavailable (get_building_finance_pg_dashboard returned None)"}
        diffs = _compare_finance_payloads(route_key=route, mongo_payload=mongo, pg_payload=pg, tolerance_cents=_tol(route))
        rows = _diffs_to_rows(diffs)
        out["compared_field_diffs"] = rows
        out["would_pass_shadow"] = len(rows) == 0
        # Deliberately-excluded definitional fields (informational, not gated):
        out["excluded_arrears_view"] = {
            "mongo_total_outstanding_aud": mongo.get("total_outstanding"),
            "pg_total_arrears_cents": pg.get("total_arrears_cents"),
            "note": "Excluded from shadow parity — different arrears definitions (see script docstring). "
                    "PG's total_arrears_cents sums all unpaid levy_items for the FY regardless of due date; "
                    "Mongo's outstanding uses due-date-aware true arrears. Not comparable as-is.",
        }
    except Exception as exc:  # noqa: BLE001 — report, never crash the whole run
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def _unit_numbers(building_id: str, limit: int) -> list[str]:
    docs = await db.units.find({}, {"_id": 0, "unit_number": 1}).to_list(None)
    nums = [str(d.get("unit_number")) for d in docs if d.get("unit_number") is not None]
    nums = sorted(set(nums), key=lambda u: int(u) if u.isdigit() else float("inf"))
    return nums if limit in (0, None) else nums[:limit]


async def _unit_dashboard(building_id: str, year: str | None, limit_units: int) -> dict:
    route = "finance.unit_dashboard_overview"
    out: dict = {"route": route}
    try:
        units = await _unit_numbers(building_id, limit_units)
    except Exception as exc:  # noqa: BLE001
        return {**out, "error": f"unit list fetch failed: {type(exc).__name__}: {exc}"}

    checked, clean, mismatched, errored = 0, 0, 0, 0
    mismatch_details: list[dict] = []
    for unit_number in units:
        checked += 1
        try:
            mongo = await _get_unit_dashboard_overview_mongo_fallback(building_id, unit_number, year)
            pg = await _get_pg_payload_for_route(building_id=building_id, route_key=route, mongo_payload=mongo)
            if pg is None:
                errored += 1
                mismatch_details.append({"unit": unit_number, "error": "PG payload unavailable"})
                continue
            diffs = _compare_finance_payloads(route_key=route, mongo_payload=mongo, pg_payload=pg, tolerance_cents=_tol(route))
            rows = _diffs_to_rows(diffs)
            if rows:
                mismatched += 1
                mismatch_details.append({"unit": unit_number, "diffs": rows})
            else:
                clean += 1
        except Exception as exc:  # noqa: BLE001
            errored += 1
            mismatch_details.append({"unit": unit_number, "error": f"{type(exc).__name__}: {exc}"})

    out.update({
        "units_checked": checked,
        "clean": clean,
        "mismatched": mismatched,
        "errored": errored,
        "would_pass_shadow": mismatched == 0 and errored == 0 and checked > 0,
        "mismatch_details": mismatch_details,
    })
    return out


async def run(building_id: str, year: str | None, limit_units: int) -> dict:
    set_ctx_building_id(building_id)
    bo = await _building_overview(building_id, year)
    ud = await _unit_dashboard(building_id, year, limit_units)
    clean_across = bool(bo.get("would_pass_shadow")) and bool(ud.get("would_pass_shadow"))
    return {
        "building_id": building_id,
        "year": year or "(latest resolved by services)",
        "verdict": {
            "building_overview_clean": bo.get("would_pass_shadow"),
            "unit_dashboard_clean": ud.get("would_pass_shadow"),
            "clean_across_both": clean_across,
            "headline": (
                "Both routes' GATED fields match — they would reach shadow_pass. NOTE: this does NOT "
                "cover the excluded arrears/outstanding definitional gap; read excluded_arrears_view."
                if clean_across else
                "One or more routes diverge on gated fields — see details. Each diff is PG-vs-Mongo on "
                "the SAME request; investigate which store is correct before promoting."
            ),
        },
        "building_overview": bo,
        "unit_dashboard_overview": ud,
        "read_only": "No shadow_diffs written. Uses production builders + comparators; SELECTs only.",
    }


def _print_human(r: dict) -> None:
    print(f"\nEast Gate PG-vs-Mongo diff report — building {r['building_id']}  year={r['year']}")
    v = r["verdict"]
    print(f"\n  >>> {v['headline']}")
    print(f"      building_overview clean: {v['building_overview_clean']}   unit_dashboard clean: {v['unit_dashboard_clean']}")

    bo = r["building_overview"]
    print("\n  finance.building_overview:")
    if bo.get("error"):
        print(f"    ERROR: {bo['error']}")
    else:
        rows = bo.get("compared_field_diffs") or []
        if not rows:
            print("    gated fields: MATCH (0 diffs)")
        for d in rows:
            print(f"    DIFF {d['field']}: mongo={d['mongo']} pg={d['pg']} diff_cents={d['diff_cents']} [{d['severity']}]")
        ex = bo.get("excluded_arrears_view", {})
        print(f"    [excluded] outstanding: mongo_aud={ex.get('mongo_total_outstanding_aud')} "
              f"vs pg_arrears_cents={ex.get('pg_total_arrears_cents')}  (definitional — not gated)")

    ud = r["unit_dashboard_overview"]
    print("\n  finance.unit_dashboard_overview:")
    if ud.get("error"):
        print(f"    ERROR: {ud['error']}")
    else:
        print(f"    units_checked={ud['units_checked']} clean={ud['clean']} mismatched={ud['mismatched']} errored={ud['errored']}")
        for m in ud.get("mismatch_details", []):
            if m.get("error"):
                print(f"    unit {m['unit']}: ERROR {m['error']}")
            else:
                for d in m["diffs"]:
                    print(f"    unit {m['unit']} DIFF {d['field']}: mongo={d['mongo']} pg={d['pg']} "
                          f"diff_cents={d['diff_cents']} [{d['severity']}]")
    print(f"\n  {r['read_only']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="READ-ONLY PG-vs-Mongo diff report for East Gate finance routes.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--year", default=None, help="Financial year (e.g. 2026). Default: services resolve latest.")
    parser.add_argument("--limit-units", type=int, default=0, help="0 = all units; N = first N (numeric order).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id, args.year, args.limit_units))
    if args.json:
        print(json.dumps(result, indent=2, default=str, sort_keys=True))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
