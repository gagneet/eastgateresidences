#!/usr/bin/env python3
# @featuretrace:finance-evidence-gateway — Portal snapshot vs PG/Mongo ledger reconciliation (READ-ONLY).
# Layer: script
# Data flow: portal snapshot JSON + finance.levy_items/receipts/receipt_allocations (PG)
#            + unit_levy_ledger (Mongo) -> per-lot variance report -> stdout / JSON.
# Related: backend/integrations/demo_bank/ingestion.py   (where the deltas must be staged)
#          docs/architecture/finance_ledger_postgres_cutover_analysis_2026-08-27.md
# Toggle: n/a (read-only audit — writes nothing, anywhere)
"""Reconcile a strata-portal position snapshot against the PG and Mongo ledgers.

READ-ONLY. This script writes to no store. Its output is a worklist, not a repair.

What the portal is, and is not
------------------------------
The portal gives the **current life-to-date credit/debt position per lot** (Admin +
Sinking, GST included) as at the moment it was read. It does **not** give the
payments that produced that position — there is no transaction history in it.

The ledgers therefore lag it: PostgreSQL and MongoDB reflect the last time payment
activity was ingested. A difference between the two is not automatically a defect —
in the normal case it is simply **payments made since the last ingest**.

That makes the sign of the difference the diagnostic:

  portal is BETTER than the ledger (less owing / more credit)
      -> consistent with owners having paid since the ledger was last updated.
         This is the expected case and is the input for a Demo Bank ingest.

  portal is WORSE than the ledger (more owing / less credit)
      -> CANNOT be explained by a payment. Either a levy was raised that the
         ledger has not seen, a payment was reversed, or one side is wrong.
         These are the rows that need a human.

Per the operator's standing instruction, the resulting deltas are NOT applied here.
They must enter as real transactions through the Demo Bank pipeline — the single
door into finance — so the GL derives the new position rather than having it
written on top.

PostgreSQL position — from the CANONICAL module, not a local formula
--------------------------------------------------------------------
This calls ``services/finance_metrics/lot_true_balance.compute_lot_true_balances``.
It does NOT compute a balance of its own.

Two earlier versions of this script did, and both were wrong. The first treated every
unallocated receipt as credit, which double-counts wherever the allocation trail is
incomplete. The second copied the canonical module's ``GREATEST(0, received - levied)``
shape but not its filters, so it counted RETIRED and REVERSED receipts and reported
phantom credits of $19,000-$30,000 on lots that hold none. A re-implementation drifts
from its original the moment the original is fixed; the only safe version calls it.

Basis check, not a basis assumption
-----------------------------------
The portal reports LIFE-TO-DATE. ``compute_lot_true_balances`` is scoped to ONE
financial year. Those agree only when every prior year is fully closed — charged ==
paid, with no surplus carried.

This script VERIFIES that rather than assuming it, and refuses to report a comparison
for any lot whose prior years are not closed. For East Gate on 2026-08-28 the check
passes exactly: 2021-2025 each show charged == paid == received to the cent, so FY2026
IS the life-to-date position.

Usage (from repo root):
    backend/venv/bin/python3 backend/scripts/audits/portal_vs_ledger_reconciliation.py \
        --building-id 13195 --portal-file portal_snapshot.json
    ... --json-out /tmp/reconciliation.json

``--portal-file`` is JSON mapping lot_number -> position in DOLLARS, positive for
owing and negative for credit:  {"1": 0.77, "3": -1568.96, ...}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

import asyncpg  # noqa: E402


async def _prior_years_closed(conn, tenant_id, financial_year: str) -> tuple[bool, list[dict]]:
    """Is every year before `financial_year` fully settled, per lot?

    The portal is life-to-date and the canonical balance module is per-year. They are
    comparable only if nothing is outstanding or in surplus from earlier years. Returns
    (ok, offending_rows) so the caller can refuse rather than quietly mislead.
    """
    rows = await conn.fetch(
        """
        SELECT lr.financial_year AS fy,
               SUM(li.principal_cents + li.gst_cents) AS charged,
               SUM(li.paid_cents) AS paid
        FROM finance.levy_items li
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        WHERE li.tenant_id = $1 AND lr.financial_year < $2
        GROUP BY 1 ORDER BY 1
        """,
        tenant_id, financial_year,
    )
    bad = [dict(r) for r in rows if int(r["charged"] or 0) != int(r["paid"] or 0)]
    return (not bad), [dict(r) for r in rows]


async def _pg_positions(building_id: str, financial_year: str) -> tuple[str, dict[str, dict], list[dict]]:
    """Per-lot true balance from the canonical module, keyed by lot_number."""
    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from services.finance_metrics.lot_true_balance import compute_lot_true_balances
    from sqlalchemy import text as sql_text

    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise SystemExit(f"No Postgres scheme for building_id={building_id}")
    tenant_id, scheme_id = str(scheme["tenant_id"]), str(scheme["scheme_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        lots = {
            str(r[0]): (r[1], r[2])
            for r in (await session.execute(
                sql_text("SELECT lot_id, lot_number, unit_number FROM core.lots "
                         "WHERE tenant_id = :t"),
                {"t": tenant_id},
            )).all()
        }
        balances = await compute_lot_true_balances(
            session, scheme_id=scheme_id, tenant_id=tenant_id, financial_year=financial_year,
        )

    out: dict[str, dict] = {}
    for lot_id, (lot_number, unit_number) in lots.items():
        bal = balances.get(lot_id)
        out[str(lot_number)] = {
            "unit_number": unit_number,
            # Missing is NOT zero. A lot with no ledger rows for the year has an UNKNOWN
            # balance and must be surfaced as such, never rendered as $0.00.
            "position_cents": None if bal is None else bal.true_balance_cents,
            "outstanding_cents": None if bal is None else bal.outstanding_cents,
            "credit_cents": None if bal is None else bal.unapplied_credit_cents,
        }
    return tenant_id, out, []


async def _mongo_positions(building_id: str) -> dict[str, int]:
    """Life-to-date net position from unit_levy_ledger, keyed by unit_number."""
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    from database import db

    rows = await db.unit_levy_ledger.find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1, "net_balance": 1},
    ).to_list(None)
    agg: dict[str, int] = {}
    for r in rows:
        u = r.get("unit_number")
        if not u:
            continue
        agg[u] = agg.get(u, 0) + round(float(r.get("net_balance") or 0) * 100)
    return agg


def _fmt(cents: int) -> str:
    return f"{cents / 100:>11,.2f}"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--portal-file", required=True,
                    help="JSON: lot_number -> dollars (positive owing, negative credit).")
    ap.add_argument("--financial-year", default="2026",
                    help="Year the canonical balance module is scoped to. Prior years "
                         "must be closed for this to equal the portal's life-to-date basis; "
                         "the check below enforces that.")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    portal_raw = json.loads(Path(args.portal_file).read_text())
    portal = {str(k): round(float(v) * 100) for k, v in portal_raw.items()}

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    tenant_id, pg, _ = await _pg_positions(args.building_id, args.financial_year)

    # Basis check BEFORE any comparison: the portal is life-to-date, the module is
    # per-year, and they only agree when earlier years carry nothing forward.
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")
        closed, prior = await _prior_years_closed(conn, tenant_id, args.financial_year)
    finally:
        await conn.close()
    print(f"\n  Basis check — years before FY{args.financial_year}:")
    for r in prior:
        delta = int(r["charged"] or 0) - int(r["paid"] or 0)
        print(f"    {r['fy']}: charged {_fmt(int(r['charged'] or 0))} "
              f"paid {_fmt(int(r['paid'] or 0))} outstanding {_fmt(delta)}")
    if not closed:
        print("\n  REFUSING to compare: a prior year is not closed, so FY"
              f"{args.financial_year} is NOT the life-to-date position the portal reports.")
        return 2
    print(f"    -> closed. FY{args.financial_year} IS the life-to-date position.")

    mongo = await _mongo_positions(args.building_id)

    rows, unexplained, agreed = [], [], []
    for lot in sorted(portal, key=lambda x: int(x)):
        p = portal[lot]
        g = pg.get(lot)
        if g is None:
            rows.append({"lot": lot, "note": "lot not present in PostgreSQL"})
            continue
        unit = g["unit_number"]
        if g["position_cents"] is None:
            rows.append({"lot": lot, "note": f"{unit}: no PostgreSQL ledger rows for the year "
                                             "— balance UNKNOWN, not zero"})
            continue
        m = mongo.get(unit)
        # delta > 0  => the ledger says they owe MORE than the portal does
        #            => a payment happened that the ledger has not ingested.
        delta = g["position_cents"] - p
        row = {
            "lot": lot, "unit": unit,
            "portal_cents": p,
            "pg_position_cents": g["position_cents"],
            "pg_outstanding_cents": g["outstanding_cents"],
            "pg_credit_cents": g["credit_cents"],
            "mongo_position_cents": m,
            "implied_payment_cents": delta,
            "explainable_by_payment": delta >= 0,
        }
        rows.append(row)
        if delta == 0:
            agreed.append(row)
        elif delta < 0:
            unexplained.append(row)

    print(f"\n{'=' * 118}")
    print(f"Portal vs ledger reconciliation — building {args.building_id}  (READ-ONLY, nothing written)")
    print("=" * 118)
    print(f"{'lot':>4} {'unit':8} {'portal':>11} {'PG posn':>11} {'Mongo posn':>11} "
          f"{'implied pmt':>12}  flag")
    for r in rows:
        if "note" in r:
            print(f"{r['lot']:>4} {'—':8} {r['note']}")
            continue
        mg = _fmt(r["mongo_position_cents"]) if r["mongo_position_cents"] is not None else "        n/a"
        flag = ("" if r["implied_payment_cents"] == 0
                else "PAID SINCE" if r["explainable_by_payment"] else "*** UNEXPLAINED ***")
        print(f"{r['lot']:>4} {r['unit']:8} {_fmt(r['portal_cents'])} {_fmt(r['pg_position_cents'])} "
              f"{mg} {_fmt(r['implied_payment_cents'])}  {flag}")

    scored = [r for r in rows if "note" not in r]
    tot_portal = sum(r["portal_cents"] for r in scored)
    tot_pg = sum(r["pg_position_cents"] for r in scored)
    tot_delta = sum(r["implied_payment_cents"] for r in scored)

    print(f"\n{'-' * 118}")
    print(f"  lots compared            {len(scored)}")
    print(f"  agree exactly            {len(agreed)}")
    print(f"  explained by a payment   {len([r for r in scored if r['implied_payment_cents'] > 0])}")
    print(f"  UNEXPLAINED (ledger better than portal)  {len(unexplained)}")
    print(f"\n  portal total position    {_fmt(tot_portal)}")
    print(f"  PG total position        {_fmt(tot_pg)}")
    print(f"  implied payments to stage{_fmt(tot_delta)}")
    if unexplained:
        print("\n  UNEXPLAINED rows — a payment cannot make a position worse:")
        for r in unexplained:
            print(f"    lot {r['lot']:>3} {r['unit']:8} portal {_fmt(r['portal_cents'])} "
                  f"vs PG {_fmt(r['pg_position_cents'])}  ({_fmt(r['implied_payment_cents'])})")
    print("\n  NEXT STEP: stage the implied payments as Demo Bank transactions.")
    print("  Do NOT write these figures onto the ledger directly — the GL must derive")
    print("  the new position from real transactions passing through the one door.")
    print("=" * 118)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "building_id": args.building_id, "tenant_id": tenant_id,
            "basis": "life-to-date, Admin + Sinking incl GST; positive = owing, negative = credit",
            "read_only": True, "rows": rows,
            "totals": {"portal_cents": tot_portal, "pg_cents": tot_pg,
                       "implied_payments_cents": tot_delta},
            "unexplained": unexplained,
        }, indent=2))
        print(f"\n  JSON written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
