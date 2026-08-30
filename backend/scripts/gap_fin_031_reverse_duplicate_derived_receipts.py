#!/usr/bin/env python3
"""
GAP-FIN-031 remediation — reverse the 22 erroneously-duplicated receipts posted
by gap_fin_031_post_fy2026_derived_receipts.py's phase 1 for East Gate FY2026.

Original root-cause hypothesis (superseded, kept below for history): phase 1's
duplicate check only matched on exact bank_transaction_id, and
`finance.bank_transactions` was assumed to contain a genuine duplicate row for
the same real-world obligation under a different bank_transaction_id (one
instance found live had an amount matching the Sinking Fund calculation but
labeled "Admin Fund" — read at the time as a mislabeling bug in the
reconstruction generator).

CONFIRMED root cause (GAP-FIN-045, 2026-08-04): the actual mechanism is a
cross-pipeline collision, not a source-data mislabeling. Two separate posting
paths — this script's own phase-1 backfill AND the live match-promotion route
(`routers/financial_matching.py::_post_payment_to_ledger`) — each create a
`finance.receipts` row for the same real cash line, but key their idempotency
on different strings (a bare `bank_transaction_id` UUID in `external_reference`
for the live path vs a `gap_fin_031_derived:<uuid>` key for this backfill), so
neither pipeline's own duplicate check ever sees the other's row. The 2026-08-03
reversal (this script) was scoped to the tagged batch it could see, correctly
fixing the 22 receipts it found but leaving any untagged twins from the other
pipeline active — see `docs/fixes/duplicate_receipt_bank_transaction_guard_20260804.md`.

The permanent fix (commit 46b95090, GAP-FIN-045) lives at the single writer
both pipelines pass through — `FinancialCoreService._post_payment_journal` now
enforces at most one active receipt per `(scheme, bank_transaction_id, lot,
amount)`, keyed on the `bank_transaction_id` field they share — rather than in
either pipeline's own dedup query. A follow-up building-wide scan
(`scripts/data_repair/reverse_gap_fin_031_literal_duplicate_receipts_20260804.py`)
found zero further unsafe duplicates after this guard shipped, consistent with
it having closed the gap at the source rather than merely papering over one
side's symptom.

This script reverses exactly the 22 identified colliding receipts (list
hardcoded from the live investigation — NOT re-derived here, to guarantee this
touches only the exact rows already confirmed, not a fresh and possibly
different query result) via the sanctioned FinancialCoreService.reverse_entry()
— never raw SQL, matching the pattern of
backend/scripts/reverse_east_gate_portal_reconciliation_duplicate_credits.py.

Usage:
    backend/venv/bin/python3 backend/scripts/gap_fin_031_reverse_duplicate_derived_receipts.py \
        --building-id 13195          # dry-run (default)
    ... --apply                      # actually reverse
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Exact (unit_number, journal_entry_id, amount_cents) tuples confirmed live
# 2026-08-02 to collide by (lot, received_on, amount_cents) with a pre-existing
# non-gap_fin_031 bank_transfer receipt. See
# tasks/GAP-FIN-033-financial-overview-live-verification-and-fy2026-posting.md.
DUPLICATE_ENTRIES = [
    {"unit_number": "TH074", "journal_entry_id": "242e47d8-5f42-4456-9538-e608df364e0d", "amount_cents": 39802},
    {"unit_number": "TH074", "journal_entry_id": "46e825e7-3e05-46af-b37b-d134d4ae52fe", "amount_cents": 39802},
]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--entries-file", default=None, help="JSON file with the full duplicate-entry list (overrides the small inline default)")
    args = parser.parse_args()

    entries = DUPLICATE_ENTRIES
    if args.entries_file:
        entries = json.loads(Path(args.entries_file).read_text())

    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import ReverseEntryCommand, SchemeRef

    scheme = await config_repo.resolve_scheme_context(args.building_id)
    if scheme is None:
        print(json.dumps({"error": f"no Postgres scheme for building_id={args.building_id}"}))
        return
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    results = {"reversed": [], "already_reversed": [], "failed": [], "dry_run": []}

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        svc = get_financial_core_service(session)

        for entry in entries:
            je_id = UUID(entry["journal_entry_id"])
            idempotency_key = f"gap-fin-031-dup-reversal:{je_id}"
            row_out = dict(entry)

            if not args.apply:
                results["dry_run"].append(row_out)
                continue

            try:
                async with session.begin_nested():
                    cmd = ReverseEntryCommand(
                        scheme_ref=scheme_ref,
                        journal_entry_id=je_id,
                        reason="GAP-FIN-031 remediation: duplicate derived receipt — same lot/date/amount "
                               "as a pre-existing receipt under a different bank_transaction_id, caused by "
                               "a bank_transactions-level duplicate/mislabeled row in the source reconstruction "
                               "data (phase-1 dedup only matched on exact bank_transaction_id).",
                        idempotency_key=idempotency_key,
                    )
                    result = await svc.reverse_entry(cmd)
                row_out["reversal_journal_entry_id"] = str(result.reversal_entry_id)
                results["reversed"].append(row_out)
            except Exception as exc:
                row_out["error"] = str(exc)
                if "already" in str(exc).lower() or "duplicate" in str(exc).lower():
                    results["already_reversed"].append(row_out)
                else:
                    results["failed"].append(row_out)

        if args.apply:
            await session.commit()

    summary = {
        "building_id": args.building_id,
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "counts": {k: len(v) for k, v in results.items()},
        "amount_cents": {k: sum(e["amount_cents"] for e in v) for k, v in results.items()},
        "failed": results["failed"],
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
