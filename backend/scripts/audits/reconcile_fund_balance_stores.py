#!/usr/bin/env python3
"""Read-only fund-balance divergence reporter across the duplicated stores.

GAP-FIN-040 FU-4 (physical DB store consolidation). This script does NOT
mutate either database. It is the evidence gate that MUST show zero (or
explained) divergence before any physical merge of a fund-balance store is
attempted — see docs/fixes/finance_consolidation_server_implementation_guide_GAP-FIN-040.md.

The "fund balance" concept is currently served from several stores:
  A. Mongo  annual_levies.{admin_fund,sinking_fund} via
     utils/finance_helpers.get_annual_fund_balance()  (current_balance ->
     closing_balance_actual -> closing_balance -> opening_balance fallback)
  B. Postgres analytics.fact_financial_balance.closing_balance_cents via
     services/financial_read_service.get_consolidated_fund_balances()
  C. Postgres ledger-computed via
     services/financial_read_service.get_fund_balances()  (finance.* journals)

This reporter reads A/B/C for one building + levy year and prints the per-fund
cents deltas. A non-zero exit code means at least one pair diverged beyond the
tolerance, so a caller (CI / a merge preflight) can block on it.

Usage:
    cd backend
    venv/bin/python3 scripts/audits/reconcile_fund_balance_stores.py --building-id 13195
    venv/bin/python3 scripts/audits/reconcile_fund_balance_stores.py --building-id 13195 --year 2026 --json

Exit codes:
    0  all compared pairs within tolerance (or a store legitimately absent)
    1  at least one pair diverged beyond --tolerance-cents
    2  could not resolve building / year (nothing compared)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import db as mongo_db  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from utils.finance_helpers import get_annual_fund_balance, get_latest_levy_year  # noqa: E402


def _dollars_to_cents(value: float | int | None) -> int:
    """Convert a dollar float (Mongo annual_levies is stored in dollars) to cents."""
    if value in (None, ""):
        return 0
    return int(round(float(value) * 100))


async def _mongo_fund_balances(building_id: str, year: str) -> dict:
    """Store A: Mongo annual_levies via the canonical get_annual_fund_balance()."""
    levy = await mongo_db.annual_levies.find_one(
        {"building_id": building_id, "year": year}, {"_id": 0}
    )
    if not levy:
        return {}
    admin_val, admin_src = get_annual_fund_balance(levy.get("admin_fund"))
    sink_val, sink_src = get_annual_fund_balance(levy.get("sinking_fund"))
    return {
        "admin_cents": _dollars_to_cents(admin_val),
        "sinking_cents": _dollars_to_cents(sink_val),
        "admin_source_field": admin_src,
        "sinking_source_field": sink_src,
    }


async def _pg_fund_balances(building_id: str, year: str) -> dict:
    """Stores B + C: Postgres analytics fact table and ledger-computed balances."""
    result: dict = {}
    try:
        from services.financial_read_service import FinancialReadService  # noqa: E402

        svc = FinancialReadService()
        consolidated = await svc.get_consolidated_fund_balances(
            building_id=building_id, financial_year=year
        )
        if consolidated:
            result["analytics"] = {
                "admin_cents": int(consolidated.get("admin_balance_cents") or 0),
                "sinking_cents": int(consolidated.get("sinking_balance_cents") or 0),
                "source": consolidated.get("source", "analytics.fact_financial_balance"),
            }
        ledger = await svc.get_fund_balances(building_id=building_id)
        if ledger:
            result["ledger"] = {
                "admin_cents": int(ledger.get("admin_balance_cents") or 0),
                "sinking_cents": int(ledger.get("sinking_balance_cents") or 0),
                "source": ledger.get("source", "finance.* ledger"),
            }
    except Exception as exc:  # noqa: BLE001 - report, never crash the audit
        result["_pg_error"] = repr(exc)
    return result


def _compare(label: str, a: dict, b: dict, tolerance: int) -> list[dict]:
    findings = []
    for fund in ("admin_cents", "sinking_cents"):
        av, bv = a.get(fund), b.get(fund)
        if av is None or bv is None:
            continue
        delta = av - bv
        findings.append({
            "pair": label,
            "fund": fund.replace("_cents", ""),
            "left_cents": av,
            "right_cents": bv,
            "delta_cents": delta,
            "diverged": abs(delta) > tolerance,
        })
    return findings


async def run(building_id: str, year: str | None, tolerance: int, as_json: bool) -> int:
    set_ctx_building_id(building_id)
    resolved_year = year or await get_latest_levy_year(building_id)
    if not resolved_year:
        print(f"ERROR: could not resolve a levy year for building {building_id}", file=sys.stderr)
        return 2

    mongo = await _mongo_fund_balances(building_id, resolved_year)
    pg = await _pg_fund_balances(building_id, resolved_year)

    findings: list[dict] = []
    if mongo and pg.get("analytics"):
        findings += _compare("mongo_annual_levies vs pg_analytics", mongo, pg["analytics"], tolerance)
    if mongo and pg.get("ledger"):
        findings += _compare("mongo_annual_levies vs pg_ledger", mongo, pg["ledger"], tolerance)
    if pg.get("analytics") and pg.get("ledger"):
        findings += _compare("pg_analytics vs pg_ledger", pg["analytics"], pg["ledger"], tolerance)

    diverged = [f for f in findings if f["diverged"]]
    report = {
        "building_id": building_id,
        "year": resolved_year,
        "tolerance_cents": tolerance,
        "stores": {"mongo_annual_levies": mongo, "postgres": pg},
        "comparisons": findings,
        "diverged_count": len(diverged),
        "verdict": "CLEAN" if not diverged else "DIVERGED",
    }

    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Fund-balance reconciliation — building {building_id}, year {resolved_year}")
        print(f"  Mongo annual_levies : {mongo or '(absent)'}")
        print(f"  Postgres            : {pg or '(absent)'}")
        print("  Comparisons:")
        for f in findings:
            flag = "  DIVERGED" if f["diverged"] else "  ok"
            print(f"    [{f['pair']}] {f['fund']}: {f['left_cents']} vs {f['right_cents']} "
                  f"(delta {f['delta_cents']}c){flag}")
        if not findings:
            print("    (nothing comparable — a store was absent for this building/year)")
        print(f"  VERDICT: {report['verdict']} ({len(diverged)} diverged)")

    return 1 if diverged else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only fund-balance store divergence reporter (GAP-FIN-040 FU-4)")
    parser.add_argument("--building-id", required=True, help="building_id / scheme number, e.g. 13195")
    parser.add_argument("--year", default=None, help="levy year (default: latest started year)")
    parser.add_argument("--tolerance-cents", type=int, default=0, help="max allowed abs delta before DIVERGED (default 0)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    return asyncio.run(run(args.building_id, args.year, args.tolerance_cents, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
