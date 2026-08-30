"""
East Gate — Reverse Literal Same-Lot/Date/Amount Duplicate Receipts (2026-08-04)
==================================================================================
Detects and reverses genuine literal duplicates in `finance.receipts` for FY2026:
two (or more) ACTIVE receipts for the same lot, the same `received_on` date, and
the same `amount_cents` — i.e. the identical obligation posted more than once.
This is the same failure class `gap_fin_031_reverse_duplicate_derived_receipts.py`
fixed for TH074 (2 entries, hardcoded), extended here to a live, comprehensive,
building-wide detection instead of a hand-maintained list.

Ran once, live, 2026-08-04, after GAP-FIN-045 (commit 46b95090) shipped the
permanent writer-level guard: found 3 units with a literal collision (UA008,
UA014, UA050), and the per-unit hard-assert below correctly refused all three
-- UA008/UA014's "collisions" turned out to be two legitimate distinct charges
(different quarters, same flat recurring amount, same catch-up payment date),
not duplicates, and UA050's gap didn't close via this pattern at all (its
residual was later understood to be accrued arrears interest, not a posting
defect). Net result: zero receipts reversed, confirming GAP-FIN-045's guard
had already closed the actual duplicate-posting gap at the source
(`FinancialCoreService._post_payment_journal`) rather than this script finding
residue it needed to clean up. See
`docs/fixes/duplicate_receipt_bank_transaction_guard_20260804.md` for the
confirmed root cause and fix location.

This does NOT distrust or re-derive anything based on how a receipt's source
`finance.bank_transactions` row was generated (real feed vs Demo Bank
reconstruction/estimate) — per this repo's Financial Evidence Gateway policy,
anything posted through Demo Bank is the transaction of record, and provenance
is not re-litigated here. The only thing this script acts on is an exact
(lot, date, amount) collision among ACTIVE receipts, which is a duplicate
regardless of source.

Per-unit safety discipline (same as reverse_duplicate_levy_payments_20260803.py):
for each unit with a candidate collision, this script recomputes what that
unit's active 2026 receipt total WOULD be after keeping the earliest-created
receipt in each colliding group and reversing the rest, then hard-asserts that
figure against the unit's own ledger-implied target
(`unit_levy_ledger.total_levied - unit_levy_ledger.net_balance`, the same
formula gap_fin_031_post_fy2026_derived_receipts.py's phase 2 already uses).
A unit is skipped entirely — nothing written — unless the projected total lands
on target within 2 cents. This deliberately catches cases where a same-amount
collision is NOT a duplicate at all (e.g. two distinct quarters that happen to
carry the same flat recurring levy amount, settled on the same real payment
date) — reversing those would move the unit further from its correct balance,
not closer, so the assert refuses.

Usage (from backend/):
    # Dry-run audit only (default, safe):
    venv/bin/python3 scripts/data_repair/reverse_gap_fin_031_literal_duplicate_receipts_20260804.py

    # Apply (writes to Postgres — requires explicit flag):
    venv/bin/python3 scripts/data_repair/reverse_gap_fin_031_literal_duplicate_receipts_20260804.py --apply

Safety guarantees:
    - Defaults to --dry-run; --apply required for any DB writes.
    - Hard-coded to building_id="13195" (East Gate) — will not touch any other building.
    - Reverses via the sanctioned `FinancialCoreService.reverse_entry()` — never raw SQL.
      Original journal entries are never mutated; a reversal entry is posted alongside.
    - Per-unit hard-assert against `unit_levy_ledger.total_levied - net_balance` before
      writing anything for that unit — units that fail are skipped and reported, never
      partially written.
    - Idempotent: `ReverseEntryCommand`'s idempotency_key is scoped to the journal_entry_id
      being reversed, so re-running --apply after a partial failure will not double-reverse.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import asyncpg

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
YEAR = "2026"

REVERSAL_REASON = (
    "GAP-FIN-031 literal-duplicate remediation (2026-08-04): exact same lot + "
    "received_on date + amount_cents posted twice as separate active receipts — "
    "the identical obligation recorded more than once. Reversing all but the "
    "earliest-created instance. Verified against unit_levy_ledger's own "
    "total_levied - net_balance before writing; provenance of the source "
    "bank_transactions row (real vs Demo Bank reconstruction) is not a factor."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reverse literal same-lot/date/amount duplicate receipts for East Gate FY2026."
    )
    p.add_argument("--apply", action="store_true", default=False,
                    help="Write to Postgres. Without this flag the script is read-only.")
    return p.parse_args()


async def _get_mongo():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(__import__("os").getenv("MONGO_URL"))
    return client[__import__("os").getenv("DB_NAME", "strataos_production")]


async def _detect_collisions(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    rows = await conn.fetch(
        """
        SELECT l.unit_number, r.receipt_id, r.journal_entry_id, r.received_on,
               r.amount_cents, r.created_at
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        WHERE r.tenant_id = $1 AND rev.journal_entry_id IS NULL AND r.received_on >= '2026-01-01'
        ORDER BY l.unit_number, r.received_on, r.created_at
        """,
        TENANT_ID,
    )
    by_unit: dict[str, list[dict]] = {}
    for r in rows:
        by_unit.setdefault(r["unit_number"], []).append(dict(r))
    return by_unit


async def main() -> int:
    args = parse_args()
    database_url = __import__("os").environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    conn = await asyncpg.connect(database_url)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)

    db = await _get_mongo()
    by_unit = await _detect_collisions(conn)

    plan: list[dict] = []
    skipped: list[dict] = []

    for unit_number, receipts in sorted(by_unit.items()):
        groups: dict[tuple, list[dict]] = {}
        for r in receipts:
            key = (r["received_on"], r["amount_cents"])
            groups.setdefault(key, []).append(r)

        collision_groups = {k: v for k, v in groups.items() if len(v) > 1}
        if not collision_groups:
            continue

        to_reverse = []
        for key, grp in collision_groups.items():
            to_reverse.extend(grp[1:])  # keep earliest-created, reverse the rest

        current_active_cents = sum(r["amount_cents"] for r in receipts)
        reverse_cents = sum(r["amount_cents"] for r in to_reverse)
        projected_cents = current_active_cents - reverse_cents

        ledger = await db.unit_levy_ledger.find_one(
            {"building_id": BUILDING_ID, "unit_number": unit_number, "year": YEAR}
        )
        if not ledger:
            skipped.append({"unit_number": unit_number, "reason": "no FY2026 unit_levy_ledger doc"})
            continue
        target_cents = round((ledger.get("total_levied", 0.0) - ledger.get("net_balance", 0.0)) * 100)

        entry = {
            "unit_number": unit_number,
            "collision_groups": len(collision_groups),
            "candidates_to_reverse": len(to_reverse),
            "current_active_cents": current_active_cents,
            "projected_cents": projected_cents,
            "target_cents": target_cents,
            "diff_cents": projected_cents - target_cents,
        }

        if abs(projected_cents - target_cents) >= 2:
            entry["reason"] = "reversing the collision does not land on unit_levy_ledger's target — not touching"
            skipped.append(entry)
            continue

        entry["journal_entry_ids"] = [str(r["journal_entry_id"]) for r in to_reverse]
        plan.append(entry)

    units_with_collision = len(plan) + len(skipped)
    print(f"Units with a literal (lot, date, amount) collision: {units_with_collision}")
    print(f"Units where reversal cleanly reconciles to ledger target (SAFE TO APPLY): {len(plan)}")
    print(f"Units skipped (collision found but does not reconcile cleanly, or already correct as-is): {len(skipped)}")
    print()

    if skipped:
        print("SKIPPED — nothing written for these units:")
        for s in skipped:
            print(f"  {s}")
        print()

    if not plan:
        print("Nothing safe to reverse. No literal duplicate reconciles cleanly to its unit's ledger target.")
        return 0

    print("PLANNED reversals:")
    for entry in plan:
        print(f"  {entry['unit_number']}: reverse {entry['candidates_to_reverse']} receipt(s), "
              f"${entry['current_active_cents']/100:,.2f} -> ${entry['projected_cents']/100:,.2f} "
              f"(target ${entry['target_cents']/100:,.2f})")

    if not args.apply:
        print()
        print("Dry-run complete. Re-run with --apply to write these reversals.")
        return 0

    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import ReverseEntryCommand, SchemeRef

    scheme = await config_repo.resolve_scheme_context(BUILDING_ID)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    print()
    print("Applying...")
    results = {"reversed": [], "failed": []}
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        svc = get_financial_core_service(session)

        for entry in plan:
            for je_id_str in entry["journal_entry_ids"]:
                je_id = UUID(je_id_str)
                try:
                    async with session.begin_nested():
                        cmd = ReverseEntryCommand(
                            scheme_ref=scheme_ref,
                            journal_entry_id=je_id,
                            reason=REVERSAL_REASON,
                            idempotency_key=f"gap-fin-031-literal-dup-reversal:{je_id}",
                        )
                        result = await svc.reverse_entry(cmd)
                    results["reversed"].append({"unit_number": entry["unit_number"], "journal_entry_id": je_id_str,
                                                 "reversal_journal_entry_id": str(result.reversal_entry_id)})
                except Exception as exc:
                    results["failed"].append({"unit_number": entry["unit_number"], "journal_entry_id": je_id_str, "error": str(exc)})

        await session.commit()

    print(json.dumps({"reversed": len(results["reversed"]), "failed": len(results["failed"])}, indent=2))
    if results["failed"]:
        print("FAILURES:", json.dumps(results["failed"], indent=2))
        return 1

    # Post-write verification against the same ledger target used to build the plan.
    mismatches = []
    for entry in plan:
        rows = await conn.fetch(
            """
            SELECT r.amount_cents FROM finance.receipts r
            JOIN core.lots l ON l.lot_id = r.lot_id
            LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
            WHERE r.tenant_id=$1 AND l.unit_number=$2 AND rev.journal_entry_id IS NULL AND r.received_on >= '2026-01-01'
            """,
            TENANT_ID, entry["unit_number"],
        )
        post_cents = sum(r["amount_cents"] for r in rows)
        if abs(post_cents - entry["target_cents"]) >= 2:
            mismatches.append((entry["unit_number"], post_cents, entry["target_cents"]))

    await conn.close()
    if mismatches:
        print(f"POST-WRITE VERIFICATION FAILED for {len(mismatches)} unit(s): {mismatches}")
        return 1
    print(f"Post-write verification passed for all {len(plan)} touched units.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
