#!/usr/bin/env python3
"""
GAP-FIN-031 — post eligible derived FY2026 (or any levy year) bank_transactions
into finance.receipts, then reconcile the residual per-lot delta against the
portal-scrape-verified Mongo unit_levy_ledger figure.

DELIBERATELY DOES NOT USE `routers.financial_matching._post_payment_to_ledger()`
— that function unconditionally mirrors every posted receipt into Mongo's
`unit_levy_ledger` (via `_upsert_ledger_for_payment`). For this building/year,
Mongo's unit_levy_ledger is ALREADY the authoritative figure, independently
established via the Civium portal scrape on 2026-08-01 — mirroring these
Postgres catch-up postings back into Mongo would double-count against that
already-correct figure, repeating the exact double-counting failure mode
already found and reversed once today (the $1.77M `portal_reconciliation:`
incident), just via a different code path. This script posts to Postgres ONLY,
using the same lot/payer-resolution logic `_post_payment_to_ledger` uses (for
consistency), via the sanctioned `FinancialCoreService.record_payment()` /
`allocate_payment()` — never raw SQL.

Two independently --apply-gated phases:

Phase 1 (derived-transaction posting): posts every bank_transactions row this
building/year's dry-run report (gap_fin_031_fy2026_posting_dry_run.py)
classifies "eligible" (lot resolves, positive amount, in-year period, due date
<= today). Idempotency key is tied to the source bank_transaction_id — safe to
re-run; a duplicate insert is caught and skipped, not retried as new.

Phase 2 (portal-reconciliation delta): for each lot with an eligible-set
payment, computes
  delta_cents = mongo_paid_this_year_due_to_date_cents - postgres_paid_cents_after_phase_1
where mongo_paid_this_year_due_to_date_cents mirrors the ALREADY-FIXED,
due-to-date-aware formula in routers/finance.py::get_levy_status()
(GAP-FIN-033 Part A2: levied_due_to_date - net_balance) — reusing that
computation's shape, not the raw multi-year-cumulative `total_paid` field
whose misuse caused the earlier incident. Posts at most ONE receipt per lot
for the delta, dated TODAY (this reconciliation is happening today — not
backdated to pretend otherwise), with a STABLE idempotency key (no date
component, scoped to scheme+lot+year) — safe to re-run. If delta is negative
(Postgres already exceeds the Mongo-verified figure for this lot), no receipt
is posted and the lot is flagged for manual review instead — this script never
reduces or reverses a posted receipt.

Usage:
    backend/venv/bin/python3 backend/scripts/gap_fin_031_post_fy2026_derived_receipts.py \
        --building-id 13195 --year 2026                       # dry-run (default)
    backend/venv/bin/python3 backend/scripts/gap_fin_031_post_fy2026_derived_receipts.py \
        --building-id 13195 --year 2026 --apply                # actually post
    ... --apply --skip-reconciliation-delta                    # phase 1 only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

_PERIOD_RE = re.compile(r"\bQ(\d+)\s+(\d{4})\b")


def _num_periods(levy_collection_frequency: str) -> int:
    return {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}.get(
        levy_collection_frequency, 4
    )


async def _resolve_system_user_id(session, tenant_id) -> UUID:
    """Resolve the per-tenant system-cutover service account used elsewhere in
    this codebase for automated ledger postings (a UUID(int=0) placeholder
    fails finance.journal_entries' posted_by/approved_by foreign key — core.users
    has no all-zeros row). Building-generic: looked up by tenant, not hardcoded."""
    result = await session.execute(
        sa.text("SELECT user_id FROM core.users WHERE tenant_id = :tid AND email LIKE 'system-cutover%' LIMIT 1"),
        {"tid": str(tenant_id)},
    )
    row = result.first()
    if not row:
        raise RuntimeError(
            f"No system-cutover service account found for tenant={tenant_id} — "
            "cannot resolve a valid posted_by/approved_by user."
        )
    return UUID(str(row[0]))


async def _post_pg_only_payment(
    *, session, scheme_ref, unit_number: str, amount_cents: int, received_on: date,
    idempotency_key: str, external_reference: str, metadata: dict,
    reconstruction_batch_id, system_user_id: UUID,
):
    """PG-only payment posting — see module docstring for why this does NOT
    reuse financial_matching._post_payment_to_ledger() (which mirrors to Mongo).
    Mirrors that function's lot/payer-resolution logic for consistency."""
    from db_postgres.repos.ownership_repo import get_lot_id_by_number
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import (
        RecordPaymentCommand, PaymentChannel, AllocatePaymentCommand,
    )

    pg_lot_id_str = await get_lot_id_by_number(session, str(scheme_ref.scheme_id), unit_number)
    if not pg_lot_id_str:
        return None, f"unit_number {unit_number} not found in core.lots"
    pg_lot_id = UUID(pg_lot_id_str)

    owner_result = await session.execute(
        sa.text(
            "SELECT owner_party_id::text FROM core.ownership_periods "
            "WHERE lot_id = :lot_id AND is_primary_owner = TRUE "
            "AND recorded_to IS NULL "
            "AND valid_from <= :received_on "
            "AND (valid_to IS NULL OR valid_to >= :received_on) "
            "LIMIT 1"
        ),
        {"lot_id": str(pg_lot_id), "received_on": received_on},
    )
    owner_row = owner_result.first()
    if not owner_row:
        return None, f"no primary owner for lot {unit_number} as of {received_on}"
    payer_party_id = UUID(owner_row.owner_party_id)

    svc = get_financial_core_service(session)
    cmd = RecordPaymentCommand(
        scheme_ref=scheme_ref,
        payer_party_id=payer_party_id,
        lot_id=pg_lot_id,
        channel=PaymentChannel.BANK_TRANSFER,
        received_on=received_on,
        amount_cents=amount_cents,
        external_reference=external_reference,
        idempotency_key=idempotency_key,
        is_test_data=False,
        posted_by=system_user_id,
        approved_by=system_user_id,
        metadata=metadata,
        reconstruction_batch_id=reconstruction_batch_id,
    )
    # Each row runs inside its own SAVEPOINT (nested transaction) — a duplicate-
    # key or other per-row failure must roll back only that row's work, never
    # poison the shared session for the rest of the batch (a single bad row
    # previously cascaded into every subsequent row failing with
    # PendingRollbackError, even though nothing was wrong with them).
    try:
        async with session.begin_nested():
            receipt = await svc.record_payment(cmd)
    except IntegrityError:
        return "already_posted", None

    try:
        async with session.begin_nested():
            alloc_cmd = AllocatePaymentCommand(scheme_ref=scheme_ref, receipt_id=receipt.receipt_id)
            await svc.allocate_payment(alloc_cmd)
    except Exception as alloc_exc:
        return str(receipt.receipt_id), f"posted but allocate_payment failed: {alloc_exc}"
    return str(receipt.receipt_id), None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually write. Default is dry-run.")
    parser.add_argument("--skip-reconciliation-delta", action="store_true")
    args = parser.parse_args()

    from database import db
    from request_context import set_ctx_building_id
    from utils.finance_helpers import compute_period_due_dates
    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core.domain.entities import SchemeRef

    set_ctx_building_id(args.building_id)
    settings_doc = await db.settings.find_one(
        {
            "building_id": args.building_id,
            "$or": [{"type": {"$exists": False}}, {"type": None}, {"type": "general"}],
        },
        {"_id": 0},
        sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
    ) or {}
    fy_start_month = int(settings_doc.get("financial_year_start_month", 1))
    num_periods = _num_periods(settings_doc.get("levy_collection_frequency", "quarterly"))
    levy_due_months = settings_doc.get("levy_due_months") or []
    levy_due_day_type = settings_doc.get("levy_due_day_type") or "first"
    levy_due_day = settings_doc.get("levy_due_day")
    levy_due_custom_dates = settings_doc.get("levy_due_custom_dates")
    today = date.today()

    scheme = await config_repo.resolve_scheme_context(args.building_id)
    if scheme is None:
        print(json.dumps({"error": f"no Postgres scheme for building_id={args.building_id}"}))
        return
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    phase1_results = {"posted": [], "already_posted": [], "failed": [], "dry_run": []}
    per_lot_pg_paid_cents: dict[str, int] = {}

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        system_user_id = await _resolve_system_user_id(session, scheme["tenant_id"])

        rows_result = await session.execute(
            sa.text(
                """
                SELECT bt.bank_transaction_id, bt.transaction_date, bt.amount_cents,
                       bt.unit_number, bt.description, bt.reconstruction_batch_id
                FROM finance.bank_transactions bt
                WHERE bt.scheme_id = :scheme_id
                  AND bt.transaction_date >= CAST(:year || '-01-01' AS date)
                  AND bt.transaction_date < CAST((CAST(:year AS int) + 1) || '-01-01' AS date)
                  AND bt.amount_cents > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM finance.receipts r
                      WHERE r.scheme_id = bt.scheme_id
                        AND (r.bank_transaction_id = bt.bank_transaction_id
                             OR r.external_reference = CAST(bt.bank_transaction_id AS text))
                  )
                ORDER BY bt.unit_number, bt.transaction_date
                """
            ),
            {"scheme_id": scheme["scheme_id"], "year": args.year},
        )
        rows = rows_result.mappings().all()

        due_dates_by_levy_year: dict[int, list[str]] = {}
        eligible_rows = []
        for row in rows:
            m = _PERIOD_RE.search(row["description"] or "")
            if not m:
                continue
            period_num, period_year = int(m.group(1)), int(m.group(2))
            if period_year != int(args.year):
                continue
            if period_year not in due_dates_by_levy_year:
                due_dates_by_levy_year[period_year] = compute_period_due_dates(
                    period_year, levy_due_months, levy_due_day_type, levy_due_day,
                    num_periods, levy_due_custom_dates, fy_start_month=fy_start_month,
                )
            due_dates = due_dates_by_levy_year[period_year]
            idx = period_num - 1
            if not (0 <= idx < len(due_dates)):
                continue
            try:
                due_date = date.fromisoformat(str(due_dates[idx])[:10])
            except (ValueError, TypeError):
                continue
            if due_date > today:
                continue
            eligible_rows.append(row)

        for row in eligible_rows:
            idempotency_key = f"gap-fin-031-derived:{row['bank_transaction_id']}"
            external_reference = f"gap_fin_031_derived:{row['bank_transaction_id']}"
            metadata = {"provenance": "derived_reconstruction", "gap_fin_031_phase": "1"}
            entry = {
                "unit_number": row["unit_number"],
                "bank_transaction_id": str(row["bank_transaction_id"]),
                "amount_cents": row["amount_cents"],
                "transaction_date": row["transaction_date"].isoformat(),
            }
            if not args.apply:
                phase1_results["dry_run"].append(entry)
                per_lot_pg_paid_cents[row["unit_number"]] = (
                    per_lot_pg_paid_cents.get(row["unit_number"], 0) + row["amount_cents"]
                )
                continue

            receipt_id, error = await _post_pg_only_payment(
                session=session, scheme_ref=scheme_ref, unit_number=row["unit_number"],
                amount_cents=row["amount_cents"], received_on=row["transaction_date"],
                idempotency_key=idempotency_key, external_reference=external_reference,
                metadata=metadata, reconstruction_batch_id=row["reconstruction_batch_id"],
                system_user_id=system_user_id,
            )
            entry["receipt_id"] = receipt_id
            entry["error"] = error
            if receipt_id == "already_posted":
                phase1_results["already_posted"].append(entry)
                per_lot_pg_paid_cents[row["unit_number"]] = (
                    per_lot_pg_paid_cents.get(row["unit_number"], 0) + row["amount_cents"]
                )
            elif receipt_id:
                phase1_results["posted"].append(entry)
                per_lot_pg_paid_cents[row["unit_number"]] = (
                    per_lot_pg_paid_cents.get(row["unit_number"], 0) + row["amount_cents"]
                )
            else:
                phase1_results["failed"].append(entry)

        if args.apply:
            await session.commit()
            # set_tenant() uses SET LOCAL, scoped to the transaction that just
            # ended by the commit above — re-establish RLS tenant context before
            # any further tenant-scoped query (core.lots has no RLS bypass
            # clause; without this every lookup below silently returns zero
            # rows, not an error — this is the documented CLAUDE.md footgun).
            await set_tenant(session, str(scheme["tenant_id"]))

        # Phase 2: portal-reconciliation delta, per lot with an eligible-set payment.
        phase2_results = {"posted": [], "already_posted": [], "no_delta": [], "negative_delta_review": [], "failed": [], "dry_run": []}
        if not args.skip_reconciliation_delta:
            for unit_number, pg_paid_cents in per_lot_pg_paid_cents.items():
                ledger_doc = await db.unit_levy_ledger.find_one(
                    {"building_id": args.building_id, "unit_number": unit_number, "year": args.year},
                    {"_id": 0, "total_levied": 1, "net_balance": 1},
                )
                if not ledger_doc:
                    continue
                # Mirrors get_levy_status()'s levied_due_to_date computation shape but uses
                # total_levied directly (already YTD-to-date in this Mongo doc, per
                # GAP-FIN-033 Part B1's live-confirmed finding) rather than net_balance's
                # unreliable multi-year cousin, total_paid.
                mongo_levied_cents = round(float(ledger_doc.get("total_levied") or 0) * 100)
                mongo_net_balance_cents = round(float(ledger_doc.get("net_balance") or 0) * 100)
                mongo_paid_cents = mongo_levied_cents - mongo_net_balance_cents
                delta_cents = mongo_paid_cents - pg_paid_cents

                entry = {
                    "unit_number": unit_number,
                    "pg_paid_cents_after_phase1": pg_paid_cents,
                    "mongo_paid_this_year_cents": mongo_paid_cents,
                    "delta_cents": delta_cents,
                }
                if abs(delta_cents) <= 1:
                    phase2_results["no_delta"].append(entry)
                    continue
                if delta_cents < 0:
                    phase2_results["negative_delta_review"].append(entry)
                    continue

                idempotency_key = f"gap-fin-031-reconciliation-delta:{scheme['scheme_id']}:{unit_number}:{args.year}"
                external_reference = f"portal_scrape_reconciliation_delta:{args.year}"
                metadata = {
                    "provenance": "portal_scrape_reconciliation_delta",
                    "source": "civium_portal_scrape",
                    "target_levy_year": args.year,
                    "gap_fin_031_phase": "2",
                }
                if not args.apply:
                    phase2_results["dry_run"].append(entry)
                    continue

                receipt_id, error = await _post_pg_only_payment(
                    session=session, scheme_ref=scheme_ref, unit_number=unit_number,
                    amount_cents=delta_cents, received_on=today,
                    idempotency_key=idempotency_key, external_reference=external_reference,
                    metadata=metadata, reconstruction_batch_id=None,
                    system_user_id=system_user_id,
                )
                entry["receipt_id"] = receipt_id
                entry["error"] = error
                if receipt_id == "already_posted":
                    phase2_results["already_posted"].append(entry)
                elif receipt_id:
                    phase2_results["posted"].append(entry)
                else:
                    phase2_results["failed"].append(entry)

            if args.apply:
                await session.commit()

    summary = {
        "building_id": args.building_id,
        "year": args.year,
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "phase1_counts": {k: len(v) for k, v in phase1_results.items()},
        "phase1_amount_cents": {
            k: sum(e["amount_cents"] for e in v) for k, v in phase1_results.items()
        },
        "phase2_counts": {k: len(v) for k, v in phase2_results.items()} if not args.skip_reconciliation_delta else "skipped",
        "phase2_negative_delta_review_sample": phase2_results["negative_delta_review"][:20] if not args.skip_reconciliation_delta else [],
        "phase1_failed_sample": phase1_results["failed"][:20],
        "phase2_failed_sample": phase2_results["failed"][:20] if not args.skip_reconciliation_delta else [],
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
