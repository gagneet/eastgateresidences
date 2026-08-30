#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — Posts GAP-FIN-046's portal-evidenced correction via
#   FinancialCoreService.record_adjustment() (new command, 2026-08-06).
# Layer: script
# Data flow: staging_strata_web_snapshots (financial_year=2026, latest snapshot_date) ->
#            per-unit required adjustment -> RecordAdjustmentCommand -> finance.journal_entries
#            + finance.levy_items.paid_cents (2026 only). SELECTs + one write path (--apply).
# Related: services/financial_core/service.py::record_adjustment (built 2026-08-06)
#          scripts/data_repair/gap_fin_046_allocation_integrity_repair_20260805.py (the
#          allocation-layer repair this is NOT trying to redo — see module docstring)
#          services/portal_ledger_reconciliation_service.py::_latest_portal_by_unit
"""GAP-FIN-046 — post the portal-evidenced correction for units whose reconstructed
receipts overstated how much was actually paid (2026-08-06).

WHY THIS SCRIPT EXISTS, AND WHY IT'S NOT ANOTHER ALLOCATION REPAIR
--------------------------------------------------------------------
The 2026-08-05 allocation-integrity repair (gap_fin_046_allocation_integrity_repair_
20260805.py) fixes two bugs in the *allocation layer* between finance.receipts and
finance.levy_items.paid_cents. It correctly committed 73/87 units. The remaining
~13-14 units all show a different, deeper problem, confirmed 2026-08-06: for every one
of them, real (non-reversed) receipts sum to EXACTLY the levied amount, every year
2021-2026 — meaning PG's own receipts contain no recorded shortfall at all. The owner's
real-world underpayment was never recorded as a receipt anywhere in this system; it was
lost when the historical reconstruction generator assumed full payment. There is
therefore no allocation bug left to fix and no specific receipt to reverse — reversing
individual receipts to manufacture the gap was investigated and explicitly ruled out
(GAP-FIN-046 duplicate-receipt-finder, 2026-08-05: real duplicates carry distinct UUID
external_references invisible to any signature, and the heuristic false-positives on
legitimate receipts like UA008/UA014).

The correct fix is a single, evidenced, auditable correction against the verified
portal balance — FinancialCoreService.record_adjustment() (RecordAdjustmentCommand),
built for exactly this. It does NOT try to identify which receipt was wrong.

SCOPE DECISION (booked against 2026 only, not spread across 2021-2026)
------------------------------------------------------------------------
The portal gives a life-to-date NET balance, not a per-year breakdown — there is no
evidence for which specific year(s) the shortfall belongs to. Per the user's explicit
2026-08-06 decision, the whole correction is booked as a single FY2026 adjustment
(simpler, fully auditable, doesn't fabricate a year-by-year split the evidence doesn't
support) rather than spread proportionally across years.

REQUIRED TASK HEADER (Financial Evidence Gateway)
----------------------------------------------------
  Serving source before/after : Mongo / Mongo (this does not promote PG reads —
                                 that is a separate, parallel workstream)
  Authoritative source        : staging_strata_web_snapshots, building_id=13195,
                                 financial_year="2026", latest snapshot_date
                                 (verified 2026-08-06 to match building_summaries and
                                 the live portal export to the cent)
  Evidence type                : adjustment, evidenced by portal_balance_snapshot
  Posting command               : FinancialCoreService.record_adjustment()
                                 (RecordAdjustmentCommand) — never a raw UPDATE
  Affected journals             : new finance.journal_entries rows (source_type=
                                 "reconstruction_adjustment"); finance.levy_items.
                                 paid_cents decremented for FY2026 only
  Reconciliation invariants    : per unit, PG arrears == portal net (±2 cents) after
                                 the adjustment; building total adjustment == the sum
                                 of understated cents reported by
                                 pg_portal_arrears_diagnostic.py; the units already
                                 correct (not in scope) must show zero change
  Production mutation authorised: NO by default — this script defaults to --dry-run;
                                 --apply requires --authorised-by/--executed-by (two
                                 distinct real user UUIDs) and is a separate,
                                 explicitly-authorised action

SAFETY (same discipline as gap_fin_046_allocation_integrity_repair_20260805.py)
-----------------------------------------------------------------------------------
  - Defaults to --dry-run; --apply required for any DB write.
  - Per-unit SAVEPOINT (session.begin_nested()) + hard-assert before keeping any
    change; a unit that doesn't land on the portal figure within 2 cents is rolled
    back and reported, never partially written. Nothing commits until every kept
    unit has independently passed its own hard-assert.
  - decrement_paid_for_year() itself validates full sufficiency before writing
    anything (two-pass plan-then-apply) — a shortfall raises with zero rows touched,
    not a dependency on this script's savepoint as the only safety net.
  - Units already in credit (portal net <= 0) are automatically skipped — there is
    nothing to claw back, and no attempt is made to invent one.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py
    venv/bin/python3 scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py \\
        --only-units UA001,UA009 --apply \\
        --authorised-by <uuid-A> --executed-by <uuid-B>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import asyncpg  # noqa: E402

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
FINANCIAL_YEAR = "2026"
TOLERANCE_CENTS = 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", default=False, help="Write. Without this flag, dry-run only.")
    p.add_argument("--only-units", default="", help="Comma-separated allowlist: adjust ONLY these unit numbers.")
    p.add_argument("--exclude-units", default="", help="Comma-separated unit numbers to skip.")
    p.add_argument("--authorised-by", default=None, help="Approver identity (required for --apply).")
    p.add_argument("--executed-by", default=None, help="Executor identity (required for --apply; "
                                                        "must differ from --authorised-by).")
    p.add_argument("--reason", default="GAP-FIN-046 portal-evidenced correction (2026-08-06)")
    return p.parse_args()


async def _fetch_plan() -> list[dict]:
    """Read-only: build the adjustment plan from PG arrears + today's portal snapshot,
    via the already-fixed pg_portal_arrears_diagnostic path (raw lot-int matching)."""
    from database import db
    from request_context import set_ctx_building_id
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback
    from services.finance_shadow_read_service import _get_pg_payload_for_route
    from services.portal_ledger_reconciliation_service import _latest_portal_by_unit
    from utils.unit_number import extract_lot_int

    set_ctx_building_id(BUILDING_ID)
    snapshot_date, portal_by_lot_int = await _latest_portal_by_unit(db, BUILDING_ID, FINANCIAL_YEAR)
    if snapshot_date is None:
        raise RuntimeError(f"No staged portal snapshot for building {BUILDING_ID} financial_year {FINANCIAL_YEAR}")

    docs = await db.units.find({}, {"_id": 0, "unit_number": 1}).to_list(None)
    unit_numbers = sorted({str(d["unit_number"]) for d in docs if d.get("unit_number")})

    plan = []
    for unit_number in unit_numbers:
        lot_int = extract_lot_int(unit_number)
        portal_cents = portal_by_lot_int.get(lot_int)
        if portal_cents is None or portal_cents <= 0:
            continue  # not in snapshot, or in credit — nothing to claw back

        mongo = await _get_unit_dashboard_overview_mongo_fallback(BUILDING_ID, unit_number, FINANCIAL_YEAR)
        pg = await _get_pg_payload_for_route(building_id=BUILDING_ID, route_key="finance.unit_dashboard_overview",
                                              mongo_payload=mongo)
        pg_arrears = None
        for key in ("total_arrears_cents", "total_outstanding_cents", "total_outstanding"):
            if pg is not None and key in pg and pg[key] is not None:
                pg_arrears = int(pg[key])
                break
        if pg_arrears is None:
            pg_arrears = 0

        gap = portal_cents - pg_arrears
        if gap <= TOLERANCE_CENTS:
            continue  # already reconciled or PG overstates (out of scope for this script)

        plan.append({
            "unit_number": unit_number,
            "pg_arrears_cents": pg_arrears,
            "portal_net_cents": portal_cents,
            "adjustment_cents": gap,
        })
    return plan


async def _run(args: argparse.Namespace) -> int:
    """Single coroutine for the whole program — plan fetch AND apply share one event
    loop. Splitting these across two separate asyncio.run() calls (the original,
    buggy shape of this script) crashed on --apply: the DB connection pool created
    while fetching the plan gets bound to that first event loop, and reusing it from
    a second, independently-created loop raises 'Future attached to a different
    loop' — discovered live 2026-08-06 on a real --apply attempt. Confirmed via
    direct query afterward that the crash happened before any write (zero
    reconstruction_adjustment journal entries, every unit's paid_cents unchanged) —
    it failed at the very first DB connection attempt inside _apply(), not partway
    through posting."""
    plan = await _fetch_plan()

    only = {u.strip() for u in args.only_units.split(",") if u.strip()}
    excl = {u.strip() for u in args.exclude_units.split(",") if u.strip()}
    kept_plan = [p for p in plan if (not only or p["unit_number"] in only) and p["unit_number"] not in excl]

    total_cents = sum(p["adjustment_cents"] for p in kept_plan)
    print(f"GAP-FIN-046 portal-evidenced adjustment — building {BUILDING_ID}  FY{FINANCIAL_YEAR}")
    print(f"\n{len(kept_plan)} unit(s) in scope. Total adjustment: ${total_cents/100:,.2f}\n")
    for p in kept_plan:
        print(f"  {p['unit_number']:6}  pg=${p['pg_arrears_cents']/100:>9,.2f}  "
              f"portal=${p['portal_net_cents']/100:>9,.2f}  adjust=${p['adjustment_cents']/100:>9,.2f}")

    if not args.apply:
        print("\nDry-run complete. Re-run with --apply (+ --authorised-by/--executed-by) to write these changes.")
        return 0

    if not args.authorised_by or not args.executed_by:
        print("\n--apply requires --authorised-by and --executed-by (two distinct real user UUIDs).")
        return 2
    if args.authorised_by == args.executed_by:
        print("\n--authorised-by and --executed-by must differ (dual control).")
        return 2

    return await _apply(kept_plan, args)


def main() -> int:
    args = parse_args()
    return asyncio.run(_run(args))


async def _apply(plan: list[dict], args: argparse.Namespace) -> int:
    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import (
        HistoricalPostingAuthorisation, RecordAdjustmentCommand, SchemeRef,
    )
    from sqlalchemy import text as sa_text
    import uuid as _uuid

    scheme = await config_repo.resolve_scheme_context(BUILDING_ID)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))
    batch_id = _uuid.uuid4()
    snapshot_ref = f"staging_strata_web_snapshots:{BUILDING_ID}:{FINANCIAL_YEAR}"

    # AUDIT FIX (2026-08-06), superseded same day: this script briefly had its own
    # identity_repo.get_user_by_id() pre-check here after a prior --apply run posted 13
    # entries with an --executed-by that resolved to no real user in core.users or Mongo.
    # That bespoke check is now removed — the real fix is
    # FinancialCoreService._verify_authorisation_identities(), called at the top of
    # record_adjustment() (and every other Historical* handler) itself, so it protects
    # every caller uniformly rather than living as one script's private copy that could
    # drift from the authoritative check (e.g. this script's old version never required
    # is_active=TRUE; the service-layer check does). A bad identity now fails inside the
    # per-unit savepoint on the first unit attempted — still zero rows written, just
    # reported per-unit rather than as one upfront message.

    kept, rolled_back = [], []
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        svc = get_financial_core_service(session)

        for entry in plan:
            unit_number = entry["unit_number"]
            try:
                async with session.begin_nested():
                    lot_row = await session.execute(
                        sa_text("SELECT lot_id FROM core.lots WHERE tenant_id = :tid AND unit_number = :u"),
                        {"tid": str(scheme["tenant_id"]), "u": unit_number},
                    )
                    lot_id = lot_row.scalar_one_or_none()
                    if lot_id is None:
                        raise ValueError(f"{unit_number}: no core.lots row found")

                    cmd = RecordAdjustmentCommand(
                        scheme_ref=scheme_ref, lot_id=lot_id, amount_cents=entry["adjustment_cents"],
                        financial_year=FINANCIAL_YEAR,
                        authorisation=HistoricalPostingAuthorisation(
                            reconstruction_batch_id=batch_id, reason=args.reason,
                            approved_by=UUID(args.authorised_by), executed_by=UUID(args.executed_by),
                            approval_reference=f"gap-fin-046-portal-adjustment-{date.today().isoformat()}",
                            evidence_reference=snapshot_ref,
                        ),
                        effective_on=date.today(),
                        idempotency_key=f"gap-fin-046-adj-{unit_number}-{FINANCIAL_YEAR}",
                    )
                    await svc.record_adjustment(cmd)

                    # Hard-assert: PG arrears (levied - paid) for FY2026 now lands on the
                    # portal figure within tolerance.
                    check = await session.execute(
                        sa_text("""
                            SELECT COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents
                                                 + li.recovery_costs_cents - li.paid_cents), 0) AS outstanding
                            FROM finance.levy_items li
                            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                            WHERE li.tenant_id = :tid AND li.lot_id = :lid AND lr.financial_year = :fy
                        """),
                        {"tid": str(scheme["tenant_id"]), "lid": lot_id, "fy": FINANCIAL_YEAR},
                    )
                    actual_outstanding = check.scalar_one()
                    target = entry["pg_arrears_cents"] + entry["adjustment_cents"]
                    if abs(actual_outstanding - target) > TOLERANCE_CENTS:
                        raise ValueError(
                            f"{unit_number}: post-adjustment FY2026 outstanding=${actual_outstanding/100:,.2f} "
                            f"!= target=${target/100:,.2f}"
                        )
                kept.append(unit_number)
            except Exception as exc:
                rolled_back.append({"unit_number": unit_number, "error": str(exc)})

        if kept:
            await session.commit()
            print(f"Committed adjustments for {len(kept)} unit(s): {kept}")
        else:
            print("No units passed their hard-assert — nothing committed.")

    if rolled_back:
        print(f"\nROLLED BACK (hard-assert failed, nothing written) for {len(rolled_back)} unit(s):")
        for r in rolled_back:
            print(f"  {r}")

    return 1 if rolled_back and not kept else 0


if __name__ == "__main__":
    raise SystemExit(main())
