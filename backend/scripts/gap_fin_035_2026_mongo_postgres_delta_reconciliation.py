#!/usr/bin/env python3
"""GAP-FIN-035 Item 2 follow-up — reconcile ALL 2026 Mongo-vs-Postgres balance deltas.

Extends GAP-FIN-031's phase-2 reconciliation-delta mechanism (which only ran for lots that
had a phase-1-eligible transaction) to every unit with a real Mongo-vs-Postgres mismatch,
regardless of phase-1 participation. Same underlying finding as TH087's $254.98 gap
(GAP-FIN-033): Postgres's itemized ledger was only ever built from the derived Demo Bank
batch, which structurally cannot contain a transaction for a unit's real-world portal-
verified credit/payment that falls outside that batch's own coverage.

Mongo's net_balance is the authoritative accounting balance (see finance.py's own header
comment) -- it is treated as ground truth here, portal-verified via reconciliation_source.
Postgres is brought up to match it, never the other way around, and NEVER by reducing a
lot's Postgres balance below what's already posted there (a negative delta -- Postgres
ahead of Mongo -- is flagged for human review, not auto-adjusted).

Idempotency key format and external_reference convention match GAP-FIN-031's phase 2 exactly
(gap-fin-031-reconciliation-delta:<scheme_id>:<unit_number>:<year> /
portal_scrape_reconciliation_delta:<year>) so a unit already reconciled by that script's
phase 2 is correctly recognised as already_posted here, not double-posted.

Usage:
    cd backend
    venv/bin/python3 scripts/gap_fin_035_2026_mongo_postgres_delta_reconciliation.py \\
        --building-id 13195 --year 2026                 # dry-run (default)
    venv/bin/python3 scripts/gap_fin_035_2026_mongo_postgres_delta_reconciliation.py \\
        --building-id 13195 --year 2026 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from database import db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.repos.ownership_repo import get_lot_id_by_number  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from scripts.gap_fin_031_post_fy2026_derived_receipts import _post_pg_only_payment  # noqa: E402
from services.financial_core.domain.entities import SchemeRef  # noqa: E402

_SYSTEM_USER_ID = uuid.UUID("5dc9c793-df7d-5239-b87e-967c5442e114")  # System Financial Cutover


async def main(building_id: str, year: str, apply: bool) -> None:
    set_ctx_building_id(building_id)

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        print(f"No Postgres scheme found for building_id={building_id!r} -- aborting.")
        return
    scheme_ref = SchemeRef(tenant_id=scheme["tenant_id"], scheme_id=scheme["scheme_id"])
    today = date.today()

    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year, "is_test_data": {"$ne": True}},
        {"_id": 0, "unit_number": 1, "total_levied": 1, "net_balance": 1},
    ).to_list(200)

    results = {"posted": [], "already_posted": [], "no_delta": [], "negative_delta_review": [],
               "failed": [], "unit_not_in_pg": [], "dry_run": []}

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))

        for d in ledger_docs:
            unit_number = d["unit_number"]
            mongo_levied_cents = round((d.get("total_levied") or 0) * 100)
            mongo_net_balance_cents = round((d.get("net_balance") or 0) * 100)
            mongo_paid_cents = mongo_levied_cents - mongo_net_balance_cents

            pg_lot_id_str = await get_lot_id_by_number(session, str(scheme["scheme_id"]), unit_number)
            if not pg_lot_id_str:
                results["unit_not_in_pg"].append(unit_number)
                continue

            # Scoped to THIS financial_year via levy_runs -- Mongo's total_levied/net_balance
            # for a "2026" doc are year-scoped (YTD-to-date charged, per GAP-FIN-033 B1), while
            # finance.levy_items spans every historical year 2021-2026 for a lot. Summing
            # paid_cents across all years here (the bug this comment replaces) compares a
            # year-scoped Mongo figure against a life-to-date Postgres one -- caught live via a
            # dry-run that returned an implausible ~$1.55M "delta" across every single unit
            # before this fix.
            pg_result = await session.execute(
                text("""
                    SELECT COALESCE(SUM(li.paid_cents), 0) AS paid_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    WHERE li.scheme_id = :sid AND li.lot_id = :lot_id AND lr.financial_year = :year
                """),
                {"sid": str(scheme["scheme_id"]), "lot_id": pg_lot_id_str, "year": year},
            )
            pg_paid_cents = int(pg_result.scalar() or 0)
            delta_cents = mongo_paid_cents - pg_paid_cents

            entry = {
                "unit_number": unit_number,
                "mongo_paid_cents": mongo_paid_cents,
                "pg_paid_cents": pg_paid_cents,
                "delta_cents": delta_cents,
            }
            if abs(delta_cents) <= 1:
                results["no_delta"].append(entry)
                continue
            if delta_cents < 0:
                # Postgres shows MORE paid than Mongo -- never auto-reduce a lot's posted
                # balance. Flag for human review; this is a real data-quality question,
                # not something this script decides unilaterally.
                results["negative_delta_review"].append(entry)
                continue

            idempotency_key = f"gap-fin-031-reconciliation-delta:{scheme['scheme_id']}:{unit_number}:{year}"
            external_reference = f"portal_scrape_reconciliation_delta:{year}"
            metadata = {
                "provenance": "portal_scrape_reconciliation_delta",
                "source": "civium_portal_scrape",
                "target_levy_year": year,
                "gap_fin_035_item_2_followup": True,
            }
            if not apply:
                results["dry_run"].append(entry)
                continue

            receipt_id, error = await _post_pg_only_payment(
                session=session, scheme_ref=scheme_ref, unit_number=unit_number,
                amount_cents=delta_cents, received_on=today,
                idempotency_key=idempotency_key, external_reference=external_reference,
                metadata=metadata, reconstruction_batch_id=None,
                system_user_id=_SYSTEM_USER_ID,
            )
            entry["receipt_id"] = receipt_id
            entry["error"] = error
            if receipt_id == "already_posted":
                results["already_posted"].append(entry)
            elif receipt_id:
                results["posted"].append(entry)
            else:
                results["failed"].append(entry)

        if apply:
            await session.commit()

    print(f"building_id={building_id} year={year} mode={'APPLY' if apply else 'DRY-RUN'}")
    print(f"Total units checked: {len(ledger_docs)}")
    for key, items in results.items():
        if not items:
            continue
        print(f"\n{key} ({len(items)}):")
        for e in items[:30]:
            if isinstance(e, str):
                print(f"  {e}")
            else:
                cents = e.get("delta_cents", 0)
                print(f"  {e['unit_number']}: delta=${cents / 100:,.2f}"
                      + (f" receipt={e.get('receipt_id')}" if "receipt_id" in e else "")
                      + (f" error={e.get('error')}" if e.get("error") else ""))

    total_positive_delta = sum(
        e["delta_cents"] for e in (results["dry_run"] + results["posted"]) if isinstance(e, dict)
    )
    print(f"\nTotal positive delta {'to be posted' if not apply else 'posted'}: "
          f"${total_positive_delta / 100:,.2f}")
    if results["negative_delta_review"]:
        total_negative = sum(e["delta_cents"] for e in results["negative_delta_review"])
        print(f"Flagged for review (Postgres ahead of Mongo, NOT auto-adjusted): "
              f"{len(results['negative_delta_review'])} units, ${total_negative / 100:,.2f}")

    if not apply:
        print("\nDry run -- not posting anything. Re-run with --apply to post.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", type=str, required=True)
    parser.add_argument("--year", type=str, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.building_id, args.year, args.apply))
