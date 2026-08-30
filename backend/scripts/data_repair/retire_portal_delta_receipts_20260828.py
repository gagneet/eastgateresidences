#!/usr/bin/env python3
# @featuretrace:finance-evidence-gateway — Retire manufactured portal_scrape_reconciliation_delta receipts.
# Layer: script
# Data flow: CLI -> FinancialCoreService.reverse_entry (finance.journal_entries)
#            -> finance.receipts.retired_at -> [commit] -> Mongo levy_payments (reversed)
#            -> Mongo unit_levy_ledger.net_balance -> reconciliation_annual_exceptions.
# Related: backend/scripts/gap_fin_031_reverse_duplicate_derived_receipts.py  (reversal driver pattern)
#          backend/services/reconstruction_generators/reconciliation_exceptions.py
#          docs/architecture/finance_ledger_postgres_cutover_analysis_2026-08-27.md  §4.3-4.5
# Toggle: n/a (one-off authorised data repair)
"""SUPERSEDED — its premise was WRONG. Retained for the rollback path and the record.

DO NOT RUN THE RETIREMENT PHASES. Read this first.
-------------------------------------------------
This script was written on the reasoning that a receipt which is (a) unallocated and
(b) portal-sourced must be a manufactured payment. **That reasoning is wrong.** An
unallocated receipt is exactly how *unapplied credit* is represented — money paid
beyond what has been levied has nothing to allocate against, which is the documented
"collected in advance" concept, not a fabrication.

The operator's strata-portal position for all 87 lots (2026-08-28) settled it: those
owners genuinely hold those credits. Before this script ran, PostgreSQL matched the
portal to the cent on 7 of the 14 lots; after it, on none. It moved the ledger AWAY
from the source document.

It was applied on 2026-08-28 and rolled back the same day (``--rollback --apply``):
14 receipts restored by reversing the reversals, never by mutation, so the audit trail
carries both the error and its correction. The AR control break returned to its prior
-$13,478.55.

``--rollback`` remains freely available. The retirement phases (``--pg``,
``--exceptions``, ``--mongo``) now refuse to run without
``--i-have-re-adjudicated-against-source``, which exists so that re-running this
requires someone to have consciously re-checked the premise against a source document
rather than against the other datastore.

What the receipts actually are
------------------------------

Required task header
--------------------
Serving source before/after : MongoDB (unchanged — this does not promote anything)
Authoritative source        : PostgreSQL ``finance.*``
Evidence type               : ``portal_balance_snapshot`` misfiled as ``bank_receipt``
Posting command             : ``FinancialCoreService.reverse_entry`` (compensating entries only)
Affected journals           : one reversal per retired receipt, debit AR / credit Undeposited Funds
Metric definitions          : ``levy.unit_arrears_and_credit.v1`` (``due_date_grace_aware``)
Reconciliation invariants   : AR control (GL 1100) moves by exactly +Σ(retired amounts);
                              ``levy_items`` outstanding is UNCHANGED (these were never allocated)
Production mutation         : YES — authorised by the operator 2026-08-28

What these rows are
-------------------
A portal scrape reported a per-lot variance. Instead of being recorded as a
reconciliation exception, a *payment* was manufactured for each lot: posted to the
GL as ``debit Undeposited Funds / credit Accounts Receivable``, mirrored into Mongo
``levy_payments``, and used to force Mongo's ``unit_levy_ledger.net_balance`` down.
None was ever allocated to a levy item.

The controlling architecture is explicit that a portal snapshot is reconciliation
evidence and never a journal source, and that an unexplained variance is a visible
exception — never papered over with a manufactured payment.

**The null ``bank_transaction_id`` is NOT why these are wrong.** 2,232 of this
building's 2,233 receipts have it NULL, including every legitimate reconstructed
historical receipt — for 2021-2025 there was no feed to link to. What identifies
this cohort is that it *declares its own source as a portal scrape* and that it is
100% unallocated.

Precedent: GAP-FIN-073 retired 70 ``manual_adjustment`` back-solves on 2026-08-27
and reversed all 70 journals. It missed this cohort, which carries a different
``external_reference``. This closes that gap the same way.

Phases (each independently selectable; all are idempotent)
----------------------------------------------------------
``--pg``          reverse each receipt's journal entry, then stamp
                  ``retired_at``/``retired_reason`` on the receipt.
``--exceptions``  write one ``reconciliation_annual_exceptions`` row per lot so the
                  portal-vs-GL variance is visible rather than absorbed.
``--mongo``       reverse the mirrored ``levy_payments`` rows and repair
                  ``unit_levy_ledger.net_balance`` so it agrees with its own
                  ``total_levied``/``total_paid``.

Write ordering is Postgres-commit-then-Mongo (footgun #21): the two stores share no
transaction, so Mongo writes are replayed only after the Postgres commit succeeds.
Nothing is ever hard-deleted — 7-year retention.

Usage (from repo root):
    backend/venv/bin/python3 backend/scripts/data_repair/retire_portal_delta_receipts_20260828.py \
        --building-id 13195                                   # dry-run, all phases
    backend/venv/bin/python3 backend/scripts/data_repair/retire_portal_delta_receipts_20260828.py \
        --building-id 13195 --pg --exceptions --apply         # Postgres side only
    backend/venv/bin/python3 backend/scripts/data_repair/retire_portal_delta_receipts_20260828.py \
        --building-id 13195 --all --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from sqlalchemy import text  # noqa: E402

_REFERENCE_PREFIX = "portal_scrape_reconciliation_delta"
_RETIRED_REASON = (
    "Manufactured from a portal scrape, which is reconciliation evidence and never a "
    "journal source. Never allocated to any levy item; the GL posting has been reversed "
    "and the variance recorded as a reconciliation exception. Same artefact class as the "
    "70 back-solve receipts retired under GAP-FIN-073 on 2026-08-27, which carried a "
    "different external_reference and so were not caught by that campaign."
)
_REVERSAL_REASON = (
    "Portal-scrape back-solve: a manufactured payment posted debit Undeposited Funds / "
    "credit Accounts Receivable with no bank evidence and no allocation, breaking the AR "
    "control account against the levy_items subledger. Reversed per the Financial Evidence "
    "Gateway rule that a portal snapshot may never post to a journal."
)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

async def _find_targets(session, tenant_id: str) -> list[dict]:
    rows = (await session.execute(
        text("""
            SELECT r.receipt_id::text        AS receipt_id,
                   r.journal_entry_id::text  AS journal_entry_id,
                   r.amount_cents            AS amount_cents,
                   r.received_on             AS received_on,
                   r.external_reference      AS external_reference,
                   l.unit_number             AS unit_number,
                   l.lot_id::text            AS lot_id,
                   je.entry_number           AS entry_number,
                   COALESCE((SELECT sum(ra.allocated_cents)
                             FROM finance.receipt_allocations ra
                             WHERE ra.receipt_id = r.receipt_id), 0) AS allocated_cents,
                   EXISTS (SELECT 1 FROM finance.journal_entries x
                           WHERE x.tenant_id = r.tenant_id
                             AND (x.reversal_of_id = r.journal_entry_id
                                  OR x.narration LIKE '%' || r.journal_entry_id::text || '%')
                             AND x.source_type = 'reversal') AS already_reversed
            FROM finance.receipts r
            JOIN core.lots l ON l.lot_id = r.lot_id
            LEFT JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
            WHERE r.tenant_id = :tid
              AND r.external_reference LIKE :pref
              AND r.retired_at IS NULL
            ORDER BY l.unit_number
        """),
        {"tid": tenant_id, "pref": f"{_REFERENCE_PREFIX}%"},
    )).mappings().all()
    return [dict(r) for r in rows]


async def _ar_reconciliation(session, tenant_id: str) -> dict:
    """AR control account (GL 1100) against the levy_items subledger."""
    ar = (await session.execute(
        text("""SELECT COALESCE(sum(CASE WHEN jl.direction='debit' THEN jl.amount_cents
                                         ELSE -jl.amount_cents END), 0)
                FROM finance.journal_lines jl
                JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
                WHERE jl.tenant_id = :tid AND ga.account_code = '1100'"""),
        {"tid": tenant_id},
    )).scalar_one()
    sub = (await session.execute(
        text("""SELECT COALESCE(sum(li.principal_cents + li.gst_cents - li.paid_cents), 0)
                FROM finance.levy_items li WHERE li.tenant_id = :tid"""),
        {"tid": tenant_id},
    )).scalar_one()
    return {
        "ar_control_cents": int(ar),
        "levy_items_outstanding_cents": int(sub),
        "break_cents": int(ar) - int(sub),
    }


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

async def _phase_postgres(session, svc, scheme_ref, tenant_id: str,
                          targets: list[dict], apply: bool) -> dict:
    """Reverse each journal entry, then retire the receipt."""
    out = {"reversed": [], "already_reversed": [], "retired": [], "failed": []}

    for t in targets:
        row = {"unit_number": t["unit_number"], "amount_cents": t["amount_cents"],
               "receipt_id": t["receipt_id"], "entry_number": t["entry_number"]}

        if int(t["allocated_cents"] or 0) != 0:
            # Refuse: an allocated receipt is applied to a real obligation. Retiring it
            # would change what a lot owes, which is a different decision from removing
            # a phantom GL posting. None of the 14 are allocated today; this is the guard
            # that keeps that assumption from silently expiring.
            row["error"] = f"allocated {t['allocated_cents']}c — refusing, not a pure phantom"
            out["failed"].append(row)
            continue

        if t["already_reversed"]:
            out["already_reversed"].append(row)
        elif apply and t["journal_entry_id"]:
            from services.financial_core.domain.entities import ReverseEntryCommand
            try:
                async with session.begin_nested():
                    result = await svc.reverse_entry(ReverseEntryCommand(
                        scheme_ref=scheme_ref,
                        journal_entry_id=UUID(t["journal_entry_id"]),
                        reason=_REVERSAL_REASON,
                        idempotency_key=f"portal-delta-retire-20260828:{t['journal_entry_id']}",
                    ))
                row["reversal_entry_id"] = str(result.reversal_entry_id)
                out["reversed"].append(row)
            except Exception as exc:
                row["error"] = str(exc)
                (out["already_reversed"] if "already" in str(exc).lower()
                 or "duplicate" in str(exc).lower() else out["failed"]).append(row)
                continue
        else:
            out["reversed"].append(row)  # dry-run projection

        if apply:
            await session.execute(
                text("""UPDATE finance.receipts
                        SET retired_at = NOW(), retired_reason = :reason
                        WHERE receipt_id = :rid AND tenant_id = :tid AND retired_at IS NULL"""),
                {"rid": t["receipt_id"], "tid": tenant_id, "reason": _RETIRED_REASON},
            )
        out["retired"].append(row["unit_number"])

    return out


async def _phase_exceptions(mongo_db, building_id: str, targets: list[dict],
                            actor: str, apply: bool) -> dict:
    """One visible reconciliation exception per lot."""
    from services.reconstruction_generators.reconciliation_exceptions import create_exception

    existing = {
        (d.get("note") or "")
        for d in await mongo_db.reconciliation_annual_exceptions.find(
            {"building_id": building_id}, {"_id": 0, "note": 1}
        ).to_list(None)
    }

    written, skipped = [], []
    for t in targets:
        year = t["received_on"].year
        note = (
            f"Lot {t['unit_number']}: portal scrape reported a "
            f"${t['amount_cents'] / 100:,.2f} variance against the GL for FY{year}. A payment "
            f"was manufactured for it on {t['received_on']} (receipt {t['receipt_id']}, "
            f"reference {t['external_reference']}) with no bank evidence and no allocation; "
            f"that posting has been reversed and the receipt retired. The variance itself is "
            f"UNEXPLAINED and remains open — the portal and the general ledger disagree by this "
            f"amount for this lot. Informational only: it does not adjust any control balance."
        )
        if any(t["receipt_id"] in e for e in existing):
            skipped.append(t["unit_number"])
            continue
        if apply:
            await create_exception(
                mongo_db, building_id=building_id, year=year, fund_type="admin",
                amount_cents=int(t["amount_cents"]), direction="debit",
                reason_code="OWNER_ARREARS_OR_CREDITS", effect_scope="informational",
                note=note, created_by=actor,
            )
        written.append({"unit_number": t["unit_number"], "amount_cents": t["amount_cents"],
                        "year": year})
    return {"written": written, "already_present": skipped}


async def _phase_mongo(mongo_db, building_id: str, targets: list[dict], apply: bool) -> dict:
    """Reverse the mirrored levy_payments and repair the stale net_balance.

    Mongo's ``net_balance`` was written down by the manufactured payment while
    ``total_paid`` was deliberately left alone, so the row now contradicts itself.
    The repair sets ``net_balance`` back to what the row's own levied/paid imply —
    it does not invent a figure.
    """
    out = {"payments_reversed": [], "ledger_repaired": [], "ledger_already_consistent": []}
    receipt_ids = {t["receipt_id"] for t in targets}

    for t in targets:
        unit, year = t["unit_number"], str(t["received_on"].year)

        pmts = await mongo_db.levy_payments.find(
            {"building_id": building_id, "unit_number": unit,
             "status": {"$ne": "reversed"}}, {"_id": 0},
        ).to_list(200)
        for p in pmts:
            # Match on the receipt id the backfill recorded in its own note — the only
            # link between the Mongo mirror and the Postgres receipt it came from.
            if not any(rid in str(p.get("notes") or "") for rid in receipt_ids):
                continue
            if apply:
                await mongo_db.levy_payments.update_one(
                    {"building_id": building_id, "id": p.get("id")},
                    {"$set": {
                        "status": "reversed",
                        "reversed_at": datetime.now(UTC),
                        "reversal_reason": _RETIRED_REASON,
                    }},
                )
            out["payments_reversed"].append(
                {"unit_number": unit, "amount": p.get("amount"), "id": p.get("id")}
            )

        led = await mongo_db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit, "year": year}, {"_id": 0},
        )
        if not led:
            continue
        levied = round(float(led.get("total_levied") or 0), 2)
        paid = round(float(led.get("total_paid") or 0), 2)
        current = round(float(led.get("net_balance") or 0), 2)
        implied = round(levied - paid, 2)
        if abs(current - implied) < 0.005:
            out["ledger_already_consistent"].append(unit)
            continue
        if apply:
            await mongo_db.unit_levy_ledger.update_one(
                {"building_id": building_id, "unit_number": unit, "year": year},
                {"$set": {"net_balance": implied,
                          "net_balance_repaired_at": datetime.now(UTC),
                          "net_balance_repair_reason":
                              "portal-delta back-solve reversed; net_balance restored to "
                              "total_levied - total_paid (its own fields)"}},
            )
        out["ledger_repaired"].append(
            {"unit_number": unit, "levied": levied, "paid": paid,
             "was": current, "now": implied, "delta": round(implied - current, 2)}
        )
    return out


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

async def _phase_rollback(session, svc, scheme_ref, tenant_id: str, apply: bool) -> dict:
    """Undo this script's own retirement, restoring the receipts and their GL effect.

    WHY THIS EXISTS (2026-08-28): the original run was WRONG. It treated an
    unallocated receipt as evidence of a manufactured payment. It is not — an
    unallocated receipt is exactly how unapplied credit (money paid in excess of
    what has been levied) is represented, and the strata portal confirms these lots
    genuinely hold those credits: UA062 $1,530.30 CR, UA025 $59.46 CR, TH087
    $254.98 CR, and TH075 at $0.00 owing rather than $190.

    Before the retirement, PostgreSQL matched the portal on 8 of the 14 lots
    exactly. After it, it matched none. The retirement moved the ledger AWAY from
    the source document.

    Reversal is done by reversing the reversal — never by mutating or deleting the
    entries, so the audit trail shows the mistake and its correction rather than
    hiding both.
    """
    from services.financial_core.domain.entities import ReverseEntryCommand

    rows = (await session.execute(
        text("""
            SELECT r.receipt_id::text AS receipt_id, r.amount_cents, l.unit_number,
                   rev.journal_entry_id::text AS reversal_entry_id, rev.entry_number
            FROM finance.receipts r
            JOIN core.lots l ON l.lot_id = r.lot_id
            LEFT JOIN finance.journal_entries rev
                   ON rev.reversal_of_id = r.journal_entry_id
                  AND rev.source_type = 'reversal'
                  AND rev.tenant_id = r.tenant_id
            WHERE r.tenant_id = :tid
              AND r.external_reference LIKE :pref
              AND r.retired_at IS NOT NULL
              AND r.retired_reason LIKE 'Manufactured from a portal scrape%'
            ORDER BY l.unit_number
        """),
        {"tid": tenant_id, "pref": f"{_REFERENCE_PREFIX}%"},
    )).mappings().all()

    out = {"restored": [], "failed": []}
    for t in rows:
        row = {"unit_number": t["unit_number"], "amount_cents": t["amount_cents"],
               "reversal_entry_number": t["entry_number"]}
        if apply:
            try:
                if t["reversal_entry_id"]:
                    async with session.begin_nested():
                        res = await svc.reverse_entry(ReverseEntryCommand(
                            scheme_ref=scheme_ref,
                            journal_entry_id=UUID(t["reversal_entry_id"]),
                            reason=(
                                "ROLLBACK of an incorrect retirement (2026-08-28). The receipt "
                                "was real unapplied credit, not a manufactured payment: the "
                                "strata portal confirms this lot holds the corresponding credit "
                                "balance. Restoring the original posting."
                            ),
                            idempotency_key=f"portal-delta-rollback-20260828:{t['reversal_entry_id']}",
                            cascade_allocations=False,
                        ))
                    row["restoring_entry_id"] = str(res.reversal_entry_id)
                await session.execute(
                    text("""UPDATE finance.receipts
                            SET retired_at = NULL, retired_reason = NULL
                            WHERE receipt_id = :rid AND tenant_id = :tid"""),
                    {"rid": t["receipt_id"], "tid": tenant_id},
                )
            except Exception as exc:
                row["error"] = str(exc)
                out["failed"].append(row)
                continue
        out["restored"].append(row)
    return out


async def _phase_rollback_mongo(mongo_db, building_id: str, apply: bool) -> dict:
    """Withdraw the reconciliation exceptions this script wrote in error.

    Marked withdrawn, never deleted — a record that was wrong is still a record of
    what the system believed, and 7-year retention applies to the collection.
    """
    q = {"building_id": building_id,
         "created_by": {"$regex": "retire_portal_delta_receipts_20260828"},
         "withdrawn_at": {"$exists": False}}
    n = await mongo_db.reconciliation_annual_exceptions.count_documents(q)
    if apply and n:
        await mongo_db.reconciliation_annual_exceptions.update_many(q, {"$set": {
            "withdrawn_at": datetime.now(UTC),
            "withdrawn_reason":
                "Raised in error 2026-08-28. The underlying receipt was real unapplied "
                "credit confirmed by the strata portal, not a manufactured payment; there "
                "is no portal-vs-GL variance to record.",
            "effect_scope": "informational",
        }})
    return {"withdrawn": n}


# ---------------------------------------------------------------------------

async def _run_rollback(args) -> int:
    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from request_context import set_ctx_building_id
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import SchemeRef

    set_ctx_building_id(args.building_id)
    scheme = await config_repo.resolve_scheme_context(args.building_id)
    if scheme is None:
        print(json.dumps({"error": f"no Postgres scheme for building_id={args.building_id}"}))
        return 2
    tenant_id = str(scheme["tenant_id"])
    scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(str(scheme["scheme_id"])))

    report = {"building_id": args.building_id, "mode": "APPLY" if args.apply else "DRY-RUN",
              "action": "ROLLBACK"}
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        report["ar_before"] = await _ar_reconciliation(session, tenant_id)
        svc = get_financial_core_service(session)
        report["rollback"] = await _phase_rollback(session, svc, scheme_ref, tenant_id, args.apply)
        if args.apply:
            await session.commit()
    if args.apply:
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            report["ar_after"] = await _ar_reconciliation(session, tenant_id)
    from database import db as mongo_db
    report["exceptions"] = await _phase_rollback_mongo(mongo_db, args.building_id, args.apply)

    print(json.dumps(report, indent=2, default=str))
    b = report["ar_before"]
    print(f"\n{'=' * 78}\n{report['mode']}  ROLLBACK  building {args.building_id}")
    print(f"  receipts restored   {len(report['rollback']['restored'])}")
    print(f"  break before        {b['break_cents']:>12}c  (${b['break_cents'] / 100:,.2f})")
    if "ar_after" in report:
        a = report["ar_after"]
        print(f"  break AFTER         {a['break_cents']:>12}c  (${a['break_cents'] / 100:,.2f})")
    if not args.apply:
        print("\n  Dry-run. Re-run with --rollback --apply to write.")
    print("=" * 78)
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", required=True, help="Building/scheme number (never hardcoded).")
    ap.add_argument("--pg", action="store_true", help="Reverse journals + retire receipts.")
    ap.add_argument("--exceptions", action="store_true", help="Write reconciliation exceptions.")
    ap.add_argument("--mongo", action="store_true", help="Reverse mirrored payments + repair net_balance.")
    ap.add_argument("--all", action="store_true", help="All three phases.")
    ap.add_argument("--actor", default="data_repair:retire_portal_delta_receipts_20260828")
    ap.add_argument("--i-have-re-adjudicated-against-source", action="store_true",
                    help="Required for --apply. Confirms the premise was re-checked against a "
                         "source document, not against the other datastore.")
    ap.add_argument("--rollback", action="store_true",
                    help="Undo this script's retirement: restore receipts and their GL effect.")
    ap.add_argument("--apply", action="store_true", help="Write. Default: dry-run.")
    args = ap.parse_args()

    if args.rollback:
        return await _run_rollback(args)

    # The premise this script was built on was disproved (see the module docstring).
    # Anything that MUTATES now requires an explicit acknowledgement; --rollback and
    # dry-runs stay open so the record and the undo path remain usable.
    if args.apply and not args.i_have_re_adjudicated_against_source:
        print(
            "REFUSED: this script's premise was disproved on 2026-08-28 — an unallocated "
            "receipt is unapplied CREDIT, not a manufactured payment, and retiring these "
            "moved PostgreSQL away from the portal.\n"
            "  Use --rollback to undo a previous run.\n"
            "  To apply anyway, re-adjudicate against a SOURCE DOCUMENT (not the other "
            "datastore) and pass --i-have-re-adjudicated-against-source."
        )
        return 2

    do_pg = args.pg or args.all
    do_exc = args.exceptions or args.all
    do_mongo = args.mongo or args.all
    if not (do_pg or do_exc or do_mongo):
        do_pg = do_exc = do_mongo = True  # dry-run everything by default

    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from request_context import set_ctx_building_id
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import SchemeRef

    set_ctx_building_id(args.building_id)

    scheme = await config_repo.resolve_scheme_context(args.building_id)
    if scheme is None:
        print(json.dumps({"error": f"no Postgres scheme for building_id={args.building_id}"}))
        return 2
    tenant_id = str(scheme["tenant_id"])
    scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(str(scheme["scheme_id"])))

    report: dict = {
        "building_id": args.building_id,
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "phases": {"pg": do_pg, "exceptions": do_exc, "mongo": do_mongo},
    }

    # ---- Postgres side (commits before any Mongo write — footgun #21) ----
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        targets = await _find_targets(session, tenant_id)
        report["targets"] = [
            {"unit_number": t["unit_number"], "amount_cents": t["amount_cents"],
             "entry_number": t["entry_number"], "allocated_cents": int(t["allocated_cents"] or 0),
             "already_reversed": t["already_reversed"]}
            for t in targets
        ]
        report["target_count"] = len(targets)
        report["target_total_cents"] = sum(int(t["amount_cents"]) for t in targets)
        report["ar_before"] = await _ar_reconciliation(session, tenant_id)

        if not targets:
            print(json.dumps(report, indent=2, default=str))
            return 0

        if do_pg:
            svc = get_financial_core_service(session)
            report["pg"] = await _phase_postgres(
                session, svc, scheme_ref, tenant_id, targets, args.apply
            )
            if args.apply:
                await session.commit()

    if do_pg and args.apply:
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            report["ar_after"] = await _ar_reconciliation(session, tenant_id)

    # ---- Mongo side, only after Postgres has committed ----
    if do_exc or do_mongo:
        from database import db as mongo_db
        if do_exc:
            report["exceptions"] = await _phase_exceptions(
                mongo_db, args.building_id, targets, args.actor, args.apply
            )
        if do_mongo:
            report["mongo"] = await _phase_mongo(
                mongo_db, args.building_id, targets, args.apply
            )

    print(json.dumps(report, indent=2, default=str))

    tot = report["target_total_cents"]
    print(f"\n{'=' * 78}")
    print(f"{report['mode']}  building {args.building_id}")
    print(f"  receipts in scope     {report['target_count']}  (${tot / 100:,.2f})")
    b = report["ar_before"]
    print(f"  AR control before     {b['ar_control_cents']:>12}c")
    print(f"  levy_items subledger  {b['levy_items_outstanding_cents']:>12}c")
    print(f"  break before          {b['break_cents']:>12}c  (${b['break_cents'] / 100:,.2f})")
    if "ar_after" in report:
        a = report["ar_after"]
        print(f"  break AFTER           {a['break_cents']:>12}c  (${a['break_cents'] / 100:,.2f})")
    else:
        proj = b["break_cents"] + tot
        print(f"  break projected       {proj:>12}c  (${proj / 100:,.2f})  <- residual, separate cause")
    if not args.apply:
        print("\n  Dry-run. Re-run with --apply to write.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
