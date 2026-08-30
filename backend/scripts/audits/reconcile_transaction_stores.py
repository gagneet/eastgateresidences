#!/usr/bin/env python3
"""Read-only transaction-store divergence reporter across the duplicated stores.

GAP-FIN-040 FU-2 / FU-4. Does NOT mutate either database. This is the evidence
gate for the "one authoritative store + one reader per concept" work on the
Financial Transactions tab, and for any physical merge of a transaction store.

The "transactions for a building+year" concept is currently spread across:
  Mongo:
    - expense_transactions + income_transactions  (legacy mirrors the tab reads)
    - financial_transactions                      (GL mirror)
    - demo_bank_transactions                      (evidence: imports/scrapes land here)
  Postgres (GL truth, post-cutover authoritative):
    - finance.journal_lines / finance.journal_entries
    - finance.receipts (income) + finance.expense_transactions (expense)

This reporter prints per-store row COUNT and summed AMOUNT (in cents where
resolvable) for one building + year so the disconnects the #564 stopgap papered
over are visible and quantified. A non-zero exit means the tab-read stores
(expense/income_transactions) are empty while an upstream store
(demo_bank_transactions / PG) holds rows — i.e. the promotion step is still owed.

Usage:
    cd backend
    venv/bin/python3 scripts/audits/reconcile_transaction_stores.py --building-id 13195
    venv/bin/python3 scripts/audits/reconcile_transaction_stores.py --building-id 13195 --year 2026 --json

Exit codes:
    0  tab-read stores are populated (or every store is legitimately empty)
    1  upstream store has rows the tab-read stores do not (promotion owed / diverged)
    2  could not resolve building / year
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
from utils.finance_helpers import get_latest_levy_year  # noqa: E402

BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"

# Amount fields differ per collection; sum the first present per row.
_AMOUNT_FIELDS = ("amount_cents", "amount", "value", "total", "debit", "credit")


def _row_amount_cents(row: dict) -> int:
    for field in _AMOUNT_FIELDS:
        val = row.get(field)
        if val in (None, ""):
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        # Heuristic: *_cents fields are already cents; dollar fields need *100.
        return int(round(num)) if field.endswith("_cents") else int(round(num * 100))
    return 0


def _year_filter(year: str) -> dict:
    # Matches YYYY string years and ISO date strings that start with the year.
    return {"$or": [
        {"year": year},
        {"year": int(year)} if year.isdigit() else {"year": year},
        {"date": {"$regex": f"^{year}"}},
        {"transaction_date": {"$regex": f"^{year}"}},
    ]}


async def _mongo_store(collection: str, building_id: str, year: str, extra: dict | None = None) -> dict:
    query = {"$and": [_year_filter(year), extra]} if extra else _year_filter(year)
    try:
        rows = await getattr(mongo_db, collection).find(query, {"_id": 0}).to_list(5000)
    except Exception as exc:  # noqa: BLE001
        return {"count": None, "sum_cents": None, "error": repr(exc)}
    return {"count": len(rows), "sum_cents": sum(_row_amount_cents(r) for r in rows)}


async def _pg_transaction_stores(building_id: str, year: str) -> dict:
    out: dict = {}
    try:
        from sqlalchemy import text  # noqa: E402

        from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
        from db_postgres.session import async_session_context, set_tenant  # noqa: E402

        scheme = await resolve_scheme_context(building_id)
        if not scheme:
            return {"_pg_note": "no scheme row for building"}
        tenant_id = str(scheme["tenant_id"])
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            for label, sql in (
                ("journal_lines", "SELECT count(*) c, COALESCE(SUM(ABS(amount_cents)),0) s FROM finance.journal_lines"),
                ("receipts", "SELECT count(*) c, COALESCE(SUM(amount_cents),0) s FROM finance.receipts"),
                ("expense_transactions", "SELECT count(*) c, COALESCE(SUM(amount_cents),0) s FROM finance.expense_transactions"),
            ):
                try:
                    row = (await session.execute(text(sql))).one()
                    out[label] = {"count": int(row.c), "sum_cents": int(row.s)}
                except Exception as exc:  # noqa: BLE001 - table may not exist in all envs
                    out[label] = {"count": None, "sum_cents": None, "error": repr(exc)}
    except Exception as exc:  # noqa: BLE001
        out["_pg_error"] = repr(exc)
    return out


async def run(building_id: str, year: str | None, as_json: bool) -> int:
    set_ctx_building_id(building_id)
    resolved_year = year or await get_latest_levy_year(building_id)
    if not resolved_year:
        print(f"ERROR: could not resolve a levy year for building {building_id}", file=sys.stderr)
        return 2

    mongo = {
        "expense_transactions": await _mongo_store("expense_transactions", building_id, resolved_year),
        "income_transactions": await _mongo_store("income_transactions", building_id, resolved_year),
        "financial_transactions": await _mongo_store("financial_transactions", building_id, resolved_year),
        "demo_bank_transactions": await _mongo_store("demo_bank_transactions", building_id, resolved_year),
    }
    pg = await _pg_transaction_stores(building_id, resolved_year)

    tab_read_rows = (mongo["expense_transactions"].get("count") or 0) + (mongo["income_transactions"].get("count") or 0)
    upstream_rows = (
        (mongo["demo_bank_transactions"].get("count") or 0)
        + (mongo["financial_transactions"].get("count") or 0)
        + sum((pg.get(k, {}).get("count") or 0) for k in ("journal_lines", "receipts", "expense_transactions"))
    )
    promotion_owed = tab_read_rows == 0 and upstream_rows > 0

    report = {
        "building_id": building_id,
        "year": resolved_year,
        "mongo": mongo,
        "postgres": pg,
        "tab_read_rows": tab_read_rows,
        "upstream_rows": upstream_rows,
        "promotion_owed": promotion_owed,
        "verdict": "PROMOTION_OWED" if promotion_owed else "OK",
    }

    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Transaction-store reconciliation — building {building_id}, year {resolved_year}")
        for store, data in mongo.items():
            print(f"  mongo.{store:<24} count={data.get('count')}  sum_cents={data.get('sum_cents')}"
                  + (f"  ERROR={data['error']}" if data.get("error") else ""))
        for store, data in pg.items():
            if isinstance(data, dict) and "count" in data:
                print(f"  pg.finance.{store:<21} count={data.get('count')}  sum_cents={data.get('sum_cents')}")
            else:
                print(f"  pg.{store}: {data}")
        print(f"  tab-read rows (expense+income) = {tab_read_rows}")
        print(f"  upstream rows (demo_bank/GL/PG) = {upstream_rows}")
        print(f"  VERDICT: {report['verdict']}"
              + ("  <- Financial Transactions tab reads empty stores; promotion step owed (FU-2)"
                 if promotion_owed else ""))

    return 1 if promotion_owed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only transaction-store divergence reporter (GAP-FIN-040 FU-2/FU-4)")
    parser.add_argument("--building-id", required=True, help="building_id / scheme number, e.g. 13195")
    parser.add_argument("--year", default=None, help="levy year (default: latest started year)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    return asyncio.run(run(args.building_id, args.year, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
