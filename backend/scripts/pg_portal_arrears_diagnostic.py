#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — READ-ONLY per-unit PG-arrears vs PORTAL-net diagnostic (GAP-FIN-021).
# Layer: script
# Data flow: CLI -> PG unit payload (_get_pg_payload_for_route) + Mongo fallback (reference) +
#            portal snapshot (_latest_portal_by_unit) -> per-unit 3-way table -> stdout. SELECTs only.
# Related: backend/scripts/east_gate_finance_pg_mongo_diff_report_20260804.py (PG-vs-Mongo twin)
#          backend/services/portal_ledger_reconciliation_service.py (portal net source)
#          backend/services/reconstruction_generators/opening_balance_generator.py (G3.2 — same portal-net anchor)
#          docs/architecture/onboarding/04_onboarding_financial_data_flow.md (G6 portal-anchored reconciliation)
"""READ-ONLY: per-unit PG arrears vs the PORTAL's real net — the Mongo-independent GAP-FIN-021 check.

The PG-vs-Mongo diff report compares the reconstruction against Civium's hand-reconciled Mongo
figure. THIS tool compares the reconstruction against the **external portal net** directly — the
independent "today" anchor that neither store derived — so a divergence is not "which store is
stale" but "how far is PG's arrears from the real owed amount."

SCOPE — what this DOES and does NOT do (important, per GAP-FIN-046, 2026-08-05):
  * DOES: quantify the per-unit gap between PG's arrears and the portal's real net — an aggregate
    end-state figure. Useful to VALIDATE that a proposed correction lands on the portal net, and to
    monitor whether the reconstruction/allocation layer has converged.
  * DOES NOT: identify WHICH receipt is phantom. The portal exposes only an aggregate balance per
    unit, never an itemized per-payment history — and the real duplicates carry distinct UUID
    external_references invisible to any attribute signature. So this gap is NOT a safe
    "reverse-until-it-matches" work-list: an attribute-heuristic reversal over-flags real owner
    payments (UA008/UA014 were false positives). Automated per-receipt reversal for this class was
    explicitly RULED OUT. Closing the gap for the held-back units is a reconciliation-adjustment
    (product decision), not a receipt reversal — see the G6 portal-anchored reconciliation.

Sign convention (portal, per portal_ledger_reconciliation_service): balance_cents > 0 = the lot
OWES (arrears); < 0 = CREDIT. PG arrears (total_arrears_cents) is >= 0 owing. So:
    pg_vs_portal_cents = portal_owing_cents - pg_arrears_cents
    > 0  => PG UNDERSTATES arrears (owner owes more than PG shows — reverse phantom receipts)
    < 0  => PG OVERSTATES arrears (rarer; investigate)

It writes NOTHING (SELECTs only) and is building-agnostic (defaults to East Gate 13195).

Usage (from repo root):
    source backend/venv/bin/activate
    python backend/scripts/pg_portal_arrears_diagnostic.py
    python backend/scripts/pg_portal_arrears_diagnostic.py --building-id 13195 --year 2026 --json
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
from routers.finance import _get_unit_dashboard_overview_mongo_fallback  # noqa: E402
from services.finance_shadow_read_service import _get_pg_payload_for_route  # noqa: E402
from services.portal_ledger_reconciliation_service import _latest_portal_by_unit  # noqa: E402
from utils.unit_number import extract_lot_int  # noqa: E402

_ROUTE = "finance.unit_dashboard_overview"


def _pg_arrears_cents(pg: dict) -> int | None:
    """PG per-unit arrears in integer cents. Prefer the unambiguous *_cents field; fall back to
    total_outstanding. Returns None if neither is present (missing != zero)."""
    for key in ("total_arrears_cents", "total_outstanding_cents", "total_outstanding"):
        if key in pg and pg[key] is not None:
            try:
                return int(pg[key])
            except (TypeError, ValueError):
                return None
    return None


async def _unit_numbers(limit: int) -> list[str]:
    docs = await db.units.find({}, {"_id": 0, "unit_number": 1}).to_list(None)
    nums = [str(d.get("unit_number")) for d in docs if d.get("unit_number") is not None]
    nums = sorted(set(nums), key=lambda u: int(u) if u.isdigit() else float("inf"))
    return nums if limit in (0, None) else nums[:limit]


async def _resolve_year(building_id: str, year: str | None) -> str | None:
    """The portal loader does an EXACT string match on financial_year, so an empty/None year finds
    nothing (returns snapshot_date=null / every unit 'not in portal'). When --year is omitted,
    resolve the latest financial_year actually present in the snapshots instead of passing ''."""
    if year:
        return str(year)
    years = await db.staging_strata_web_snapshots.distinct("financial_year", {"building_id": building_id})
    parsed = [str(y) for y in years if str(y).strip()]
    if not parsed:
        return None
    return max(parsed, key=lambda y: int(y) if y.isdigit() else -1)


async def run(building_id: str, year: str | None, limit_units: int) -> dict:
    set_ctx_building_id(building_id)

    resolved_year = await _resolve_year(building_id, year)
    if resolved_year is None:
        snapshot_date, portal_by_token = None, {}
    else:
        snapshot_date, portal_by_token = await _latest_portal_by_unit(db, building_id, resolved_year)
    year = resolved_year  # use the concrete year for the PG/Mongo builders too
    units = await _unit_numbers(limit_units)

    rows: list[dict] = []
    total_pg_arrears = 0
    total_portal_owing = 0
    understated_units = 0
    understated_cents = 0
    no_portal = 0
    errored = 0

    for unit_number in units:
        portal_cents = portal_by_token.get(extract_lot_int(unit_number))
        try:
            mongo = await _get_unit_dashboard_overview_mongo_fallback(building_id, unit_number, year)
            pg = await _get_pg_payload_for_route(building_id=building_id, route_key=_ROUTE, mongo_payload=mongo)
        except Exception as exc:  # noqa: BLE001 — report, never crash the whole run
            errored += 1
            rows.append({"unit": unit_number, "error": f"{type(exc).__name__}: {exc}"})
            continue

        pg_arrears = _pg_arrears_cents(pg) if pg is not None else None
        mongo_outstanding = mongo.get("total_outstanding") if isinstance(mongo, dict) else None

        row: dict = {
            "unit": unit_number,
            "pg_arrears_cents": pg_arrears,
            "portal_net_cents": portal_cents,  # >0 owes, <0 credit, None = not in snapshot
            "mongo_outstanding_raw": mongo_outstanding,  # reference only (Civium reconciliation)
        }

        if pg_arrears is not None:
            total_pg_arrears += max(pg_arrears, 0)

        if portal_cents is None:
            no_portal += 1
            row["pg_vs_portal_cents"] = None
            row["note"] = "unit not in portal snapshot — cannot anchor"
        else:
            portal_owing = max(int(portal_cents), 0)  # only the owing side is arrears
            total_portal_owing += portal_owing
            if pg_arrears is not None:
                delta = portal_owing - pg_arrears
                row["pg_vs_portal_cents"] = delta
                if delta > 0:
                    understated_units += 1
                    understated_cents += delta
                    row["verdict"] = "PG_UNDERSTATES_arrears"  # phantom/duplicate receipts to reverse
                elif delta < 0:
                    row["verdict"] = "PG_OVERSTATES_arrears"
                else:
                    row["verdict"] = "match"
            else:
                row["pg_vs_portal_cents"] = None
                row["note"] = "PG arrears unavailable (missing != zero)"

        rows.append(row)

    return {
        "building_id": building_id,
        "year": year or "(latest resolved by services)",
        "portal_snapshot_date": snapshot_date,
        "read_only": "No writes. Portal snapshot + PG/Mongo builders; SELECTs only.",
        "summary": {
            "units_checked": len(units),
            "units_not_in_portal": no_portal,
            "errored": errored,
            "pg_total_arrears_cents": total_pg_arrears,
            "portal_total_owing_cents": total_portal_owing,
            "units_pg_understates": understated_units,
            "pg_understatement_cents": understated_cents,
            "headline": (
                f"PG understates arrears on {understated_units} unit(s) by "
                f"{understated_cents} cents (${understated_cents/100:,.2f}) vs the portal — "
                "this is the phantom/duplicate-receipt reversal work-list before a PG-primary read flip."
                if understated_units else
                "PG arrears matches the portal within the owing side on every unit anchored."
            ),
        },
        "units": rows,
    }


def _print_human(r: dict) -> None:
    s = r["summary"]
    print(f"\nPG-arrears vs PORTAL-net — building {r['building_id']}  year={r['year']}  "
          f"portal_snapshot={r['portal_snapshot_date']}")
    print(f"\n  >>> {s['headline']}")
    print(f"      units={s['units_checked']}  not_in_portal={s['units_not_in_portal']}  errored={s['errored']}")
    print(f"      PG total arrears = ${s['pg_total_arrears_cents']/100:,.2f}   "
          f"portal total owing = ${s['portal_total_owing_cents']/100:,.2f}")
    print(f"      PG understates on {s['units_pg_understates']} unit(s) by "
          f"${s['pg_understatement_cents']/100:,.2f}\n")
    print(f"  {'unit':<8}{'pg_arrears':>14}{'portal_net':>14}{'pg_vs_portal':>14}   verdict/note")
    for row in r["units"]:
        if row.get("error"):
            print(f"  {row['unit']:<8}  ERROR: {row['error']}")
            continue
        pa = row.get("pg_arrears_cents")
        pn = row.get("portal_net_cents")
        dv = row.get("pg_vs_portal_cents")
        vn = row.get("verdict") or row.get("note") or ""
        fa = f"${pa/100:,.2f}" if isinstance(pa, int) else "—"
        fn = f"${pn/100:,.2f}" if isinstance(pn, int) else "—"
        fd = f"${dv/100:,.2f}" if isinstance(dv, int) else "—"
        # Only print anchored or diverging rows in human mode; JSON has the full set.
        if isinstance(dv, int) and dv == 0:
            continue
        print(f"  {row['unit']:<8}{fa:>14}{fn:>14}{fd:>14}   {vn}")
    print("\n  (rows that match exactly are omitted in text mode; use --json for the full per-unit set)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--year", default=None, help="Financial year for the portal snapshot (e.g. 2026).")
    parser.add_argument("--limit-units", type=int, default=0, help="0 = all units.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(run(args.building_id, args.year, args.limit_units))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)


if __name__ == "__main__":
    main()
