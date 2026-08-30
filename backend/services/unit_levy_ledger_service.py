# @featuretrace:financial-onboarding — unit_levy_ledger (Mongo) projection writer/rebuilder for
#   the historical levy-charge posting pipeline; also the shared closing-balance formula used by
#   the live payment path.
# Layer: service
# Data flow: finance.levy_items (Postgres, sum by lot/year/fund) -> unit_levy_ledger (Mongo
#            projection). Live payment path: routers/finance.py::_upsert_ledger_for_payment()
#            calls compute_ledger_levied_and_closing() for its own admin_levied/sinking_levied/
#            closing/net_balance arithmetic (unchanged behaviour, regression-tested).
# Related: backend/routers/finance.py (_upsert_ledger_for_payment — the payment write path this
#          module's pure helper is extracted from; never modified beyond calling the helper)
#          backend/services/financial_core/service.py (create_historical_levy — the poster whose
#          output this module's projection reads)
#          backend/scripts/historical_levy_charge_posting.py (--rebuild-ledger-projection)
#          docs/finances/reconcilation-2021-2022-finances-plan01.md
#          docs/finances/reconcilation-2021-2022-finances-plan02.md
# Toggle: historical_reconstruction_posting
# Collection: unit_levy_ledger, units
# Table: finance.levy_items, finance.levy_runs, finance.funds
"""unit_levy_ledger (Mongo) projection: shared closing-balance formula + Postgres-sourced rebuild.

Two separate concerns share this module deliberately, per
`docs/finances/reconcilation-2021-2022-finances-plan01.md` item 4:

1. `compute_ledger_levied_and_closing()` — a PURE function (no I/O), extracted byte-identically
   from `routers/finance.py::_upsert_ledger_for_payment()`'s existing `if existing:` branch. The
   live payment path calls this now instead of inlining the formula; its own behaviour is
   otherwise completely unchanged (regression-tested against the pre-extraction output).
2. `rebuild_ledger_projection()` — a NEW capability, used only by
   `backend/scripts/historical_levy_charge_posting.py`. Sums real POSTED `finance.levy_items`
   cents from Postgres (the system of record, per CLAUDE.md's Postgres-first policy) rather than
   re-deriving `admin_levied`/`sinking_levied` from `annual_levies`' float rate x UOE — the same
   figure should not be independently computed two different ways in two different stores. Never
   touches `admin_paid`/`sinking_paid`/`applied_payment_ids` — those remain exclusively owned by
   the payment write path (`routers/finance.py`'s own documented invariant: "Never write
   net_balance/total_paid directly to unit_levy_ledger from external imports").

Requires neither a reconstruction batch nor a `HistoricalPostingAuthorisation` — it posts no new
accounting entry, only re-projects already-posted Postgres figures into Mongo, so it is safe to
run standalone/independently of the posting flow (and safe to re-run any time to repair the
projection without reposting anything).
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def compute_ledger_levied_and_closing(
    *,
    admin_opening: float,
    sinking_opening: float,
    admin_interest: float,
    sinking_interest: float,
    admin_paid: float,
    sinking_paid: float,
    admin_levied: float,
    sinking_levied: float,
) -> dict:
    """Pure closing-balance formula — byte-identical to
    routers/finance.py::_upsert_ledger_for_payment()'s pre-extraction `if existing:` branch:
    closing = opening + levied + interest - paid; net_balance = admin_closing + sinking_closing.

    Callers resolve `admin_levied`/`sinking_levied` themselves BEFORE calling this — the live
    payment path derives them from `annual_levies` rate x UOE (unchanged), the historical
    projection rebuild derives them from summed posted `finance.levy_items` cents (new). This
    function only ever does the shared downstream arithmetic, never the levied-amount source
    selection itself.
    """
    admin_closing = round(admin_opening + admin_levied + admin_interest - admin_paid, 2)
    sinking_closing = round(sinking_opening + sinking_levied + sinking_interest - sinking_paid, 2)
    net_balance = round(admin_closing + sinking_closing, 2)
    return {
        "admin_levied": round(admin_levied, 2),
        "sinking_levied": round(sinking_levied, 2),
        # YTD CHARGED-TO-DATE, *not* the annual budget. This is admin+sinking levied
        # for periods that have come due so far this year (e.g. Q1+Q2 only in a mid-year
        # snapshot), so it is legitimately ~half the full-year approved budget for most
        # of the year. This is BY DESIGN (GAP-FIN-033 B1; see owner_finance_service.py:52).
        # DO NOT "fix" a `sum(total_levied) / annual_budget ≈ 0.5` (ratio ~2.0) reading by
        # doubling this — that makes net_balance count not-yet-due quarters as owing and
        # breaks arrears building-wide (the MANDATORY per-unit arrears rule; the UA042
        # $963→$2,768 and "31 units/$1,469.49" incidents). The half-year figure is exactly
        # what makes net_balance reconcile to the portal. Verified 2026-08-05, East Gate.
        "total_levied": round(admin_levied + sinking_levied, 2),
        "admin_closing": admin_closing,
        "sinking_closing": sinking_closing,
        "net_balance": net_balance,
    }


async def _sum_posted_levy_items_by_fund(
    pg_session, scheme_id: UUID, lot_id: UUID, year: str,
) -> dict[str, int]:
    """{"admin": total_cents, "sinking": total_cents} summed from real posted
    finance.levy_items for this lot/year — the system-of-record figure this
    module projects into Mongo. finance.levy_items.status has no established
    "reversed"/"voided" convention anywhere in this codebase yet (checked
    2026-07-31 — no caller sets it to anything but the 'issued' default), so
    no status filter is applied here; revisit this query if/when a reversal
    status convention is introduced.
    """
    rows = await pg_session.execute(
        text(
            """
            SELECT f.fund_type::text AS fund_type, SUM(li.principal_cents + li.gst_cents) AS total_cents
            FROM finance.levy_items li
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            JOIN finance.funds f ON f.fund_id = li.fund_id
            WHERE li.scheme_id = :sid AND li.lot_id = :lot_id AND lr.financial_year = :fy
            GROUP BY f.fund_type
            """
        ),
        {"sid": str(scheme_id), "lot_id": str(lot_id), "fy": year},
    )
    totals = {"admin": 0, "sinking": 0}
    for fund_type, total_cents in rows.all():
        fund = "sinking" if fund_type in ("capital_works", "sinking") else "admin"
        totals[fund] = totals.get(fund, 0) + int(total_cents or 0)
    return totals


async def rebuild_ledger_projection(
    *,
    mongo_db,
    pg_session,
    scheme_id: UUID,
    building_id: str,
    unit_number: str,
    lot_id: UUID,
    year: str,
) -> dict:
    """Idempotently create-or-refresh one unit_levy_ledger row's levied/closing/net_balance
    fields from real posted finance.levy_items — never touches admin_paid/sinking_paid/
    applied_payment_ids. Returns the fields that were written (for the caller's own reporting).
    """
    fund_totals_cents = await _sum_posted_levy_items_by_fund(pg_session, scheme_id, lot_id, year)
    admin_levied = fund_totals_cents["admin"] / 100.0
    sinking_levied = fund_totals_cents["sinking"] / 100.0

    existing = await mongo_db.unit_levy_ledger.find_one(
        {"year": year, "building_id": building_id, "unit_number": unit_number}, {"_id": 0}
    )

    if existing:
        computed = compute_ledger_levied_and_closing(
            admin_opening=existing.get("admin_opening", 0.0),
            sinking_opening=existing.get("sinking_opening", 0.0),
            admin_interest=existing.get("admin_interest", 0.0),
            sinking_interest=existing.get("sinking_interest", 0.0),
            admin_paid=existing.get("admin_paid", 0.0),
            sinking_paid=existing.get("sinking_paid", 0.0),
            admin_levied=admin_levied,
            sinking_levied=sinking_levied,
        )
        await mongo_db.unit_levy_ledger.update_one(
            {"year": year, "building_id": building_id, "unit_number": unit_number},
            {"$set": {**computed, "updated_at": _now()}},
        )
        logger.info(
            "unit_levy_ledger projection refreshed: building=%s unit=%s year=%s "
            "admin_levied=%.2f sinking_levied=%.2f net_balance=%.2f",
            building_id, unit_number, year, computed["admin_levied"], computed["sinking_levied"],
            computed["net_balance"],
        )
        return computed

    unit_doc = await mongo_db.units.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0, "entitlement": 1, "lot_number": 1, "property_type": 1},
    )
    uoe = (unit_doc or {}).get("entitlement", 0)

    prev_year = str(int(year) - 1) if year.isdigit() else None
    admin_opening = 0.0
    sinking_opening = 0.0
    if prev_year:
        prev_ledger = await mongo_db.unit_levy_ledger.find_one(
            {"year": prev_year, "building_id": building_id, "unit_number": unit_number}, {"_id": 0}
        )
        if prev_ledger:
            admin_opening = round(prev_ledger.get("admin_closing", 0.0), 2)
            sinking_opening = round(prev_ledger.get("sinking_closing", 0.0), 2)

    computed = compute_ledger_levied_and_closing(
        admin_opening=admin_opening, sinking_opening=sinking_opening,
        admin_interest=0.0, sinking_interest=0.0,
        admin_paid=0.0, sinking_paid=0.0,
        admin_levied=admin_levied, sinking_levied=sinking_levied,
    )
    doc = {
        "id": str(_uuid.uuid4()),
        "building_id": building_id,
        "year": year,
        "unit_number": unit_number,
        "lot_number": (unit_doc or {}).get("lot_number", ""),
        "uoe": uoe,
        "property_type": (unit_doc or {}).get("property_type", ""),
        "admin_opening": admin_opening,
        "sinking_opening": sinking_opening,
        "admin_paid": 0.0,
        "sinking_paid": 0.0,
        "total_paid": 0.0,
        "applied_payment_ids": [],
        **computed,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await mongo_db.unit_levy_ledger.replace_one(
        {"year": year, "building_id": building_id, "unit_number": unit_number}, doc, upsert=True,
    )
    logger.info(
        "unit_levy_ledger projection created: building=%s unit=%s year=%s (unpaid, "
        "admin_levied=%.2f sinking_levied=%.2f)",
        building_id, unit_number, year, computed["admin_levied"], computed["sinking_levied"],
    )
    return computed
