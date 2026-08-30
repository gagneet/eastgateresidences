"""
East Gate — Reverse Duplicate Historical-Reconstruction Payments (2026-08-03)
==============================================================================
Reverses 40 `levy_payments` records (reconstruction_batch_id
"e38486f2-3f39-4865-9c7c-d16b48a2d829") that a bank-matching auto-promotion
pipeline posted on 2026-08-01T22:31 UTC — ~22 hours AFTER the portal-based
`unit_levy_ledger` reconciliation (2026-08-01T00:34 UTC) had already set the
correct balance for every unit. Those 40 records duplicated payments the
reconciliation had already accounted for, silently crediting 10 units a
second time and understating their arrears (e.g. TH074: correct $1,647.45,
shown as $55.37 — a $1,592.08 gap matching exactly 4 duplicate $398.02
postings).

Root-cause and verification detail: see
/home/gagneet/.claude/plans/immutable-plotting-music.md (Root cause 1) and
the task tracker entry for this fix. Verified exact: reversing all 40
payments and recomputing the 10 affected unit_levy_ledger docs lands each
one exactly on its own already-stored `reconciliation_target_balance` field
— the building total moves from $1,469.05 to $11,359.73 across the same 14
arrears units, matching a live Civium Strata portal pull to the cent.

Usage (from backend/):
    # Dry-run audit only (default, safe):
    venv/bin/python3 scripts/data_repair/reverse_duplicate_levy_payments_20260803.py

    # Apply repairs (writes to DB — requires explicit flag):
    venv/bin/python3 scripts/data_repair/reverse_duplicate_levy_payments_20260803.py --apply

Safety guarantees:
    - Defaults to --dry-run; --apply required for any DB writes.
    - Hard-coded to building_id="13195" (East Gate) and the one specific
      reconstruction_batch_id this incident produced — will not touch any
      other batch or building.
    - Never hard-deletes payment records — reversed rows get
      status="reversed" + a reversal audit trail, per repo retention policy.
    - Recomputes each affected unit_levy_ledger doc's paid/closing/net_balance
      fields by excluding only the reversed payment IDs, then HARD-ASSERTS
      the result equals that doc's own stored reconciliation_target_balance
      before writing anything — aborts the whole run on any mismatch rather
      than writing a partially-correct figure.
    - Idempotent: a payment already marked "reversed" is skipped, so re-running
      --apply after a partial failure is safe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

BUILDING_ID = "13195"
RECONSTRUCTION_BATCH_ID = "e38486f2-3f39-4865-9c7c-d16b48a2d829"
REVERSAL_REASON = (
    "Duplicate posting: bank-matching auto-promotion pipeline "
    "(system:high_confidence_promotion) re-posted assumption-based "
    "reconstructed_historical payments ~22h after the 2026-08-01 portal "
    "reconciliation had already set the correct balance. Reversed 2026-08-03."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reverse the 2026-08-01 duplicate levy_payments batch for East Gate."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write repairs to DB. Without this flag the script is read-only.",
    )
    return p.parse_args()


async def _get_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    return client[os.getenv("DB_NAME", "strataos_production")]


async def main() -> int:
    args = parse_args()
    db = await _get_db()

    payments = await db.levy_payments.find(
        {"building_id": BUILDING_ID, "reconstruction_batch_id": RECONSTRUCTION_BATCH_ID},
    ).to_list(200)

    already_reversed = [p for p in payments if p.get("status") == "reversed"]
    to_reverse = [p for p in payments if p.get("status") != "reversed"]

    print(f"Found {len(payments)} payments in batch {RECONSTRUCTION_BATCH_ID}")
    print(f"  already reversed: {len(already_reversed)}")
    print(f"  to reverse now:   {len(to_reverse)}")

    by_unit: dict[str, list[dict]] = {}
    for p in to_reverse:
        by_unit.setdefault(p["unit_number"], []).append(p)

    print(f"Units affected: {sorted(by_unit.keys())}")
    print()

    plan = []
    for unit_number, unit_payments in sorted(by_unit.items()):
        reversal_ids = [p["id"] for p in unit_payments]
        reversal_total = round(sum(p.get("amount", 0) for p in unit_payments), 2)

        ledger_doc = await db.unit_levy_ledger.find_one(
            {"building_id": BUILDING_ID, "unit_number": unit_number, "year": "2026"}
        )
        if not ledger_doc:
            print(f"  ABORT: no FY2026 unit_levy_ledger doc found for {unit_number}")
            return 1

        target_balance = ledger_doc.get("reconciliation_target_balance")
        if target_balance is None:
            print(f"  ABORT: {unit_number} has no reconciliation_target_balance to verify against")
            return 1

        # Recompute paid/net_balance excluding only the reversed payment IDs.
        # applied_payment_ids on the ledger doc records exactly which levy_payments
        # rows were folded into admin_paid/sinking_paid/total_paid; splitting the
        # reversal amount proportionally across admin/sinking mirrors how the
        # original back-solve allocated it (both funds' opening/levied ratio).
        admin_share = ledger_doc.get("admin_opening", 0) + ledger_doc.get("admin_levied", 0)
        sinking_share = ledger_doc.get("sinking_opening", 0) + ledger_doc.get("sinking_levied", 0)
        total_share = admin_share + sinking_share
        if total_share > 0:
            admin_reversal = round(reversal_total * admin_share / total_share, 2)
        else:
            admin_reversal = round(reversal_total / 2, 2)
        sinking_reversal = round(reversal_total - admin_reversal, 2)

        new_admin_paid = round(ledger_doc.get("admin_paid", 0) - admin_reversal, 2)
        new_sinking_paid = round(ledger_doc.get("sinking_paid", 0) - sinking_reversal, 2)
        new_total_paid = round(new_admin_paid + new_sinking_paid, 2)
        new_admin_closing = round(admin_share - new_admin_paid, 2)
        new_sinking_closing = round(sinking_share - new_sinking_paid, 2)
        new_net_balance = round(new_admin_closing + new_sinking_closing, 2)

        matches_target = abs(new_net_balance - target_balance) < 0.02
        remaining_ids = [
            pid for pid in ledger_doc.get("applied_payment_ids", [])
            if pid not in reversal_ids
        ]

        plan.append({
            "unit_number": unit_number,
            "reversal_ids": reversal_ids,
            "reversal_total": reversal_total,
            "before_net_balance": ledger_doc.get("net_balance"),
            "after_net_balance": new_net_balance,
            "target_balance": target_balance,
            "matches_target": matches_target,
            "new_admin_paid": new_admin_paid,
            "new_sinking_paid": new_sinking_paid,
            "new_total_paid": new_total_paid,
            "new_admin_closing": new_admin_closing,
            "new_sinking_closing": new_sinking_closing,
            "remaining_applied_payment_ids": remaining_ids,
        })

        status = "OK" if matches_target else "MISMATCH"
        print(
            f"  [{status}] {unit_number}: net_balance {ledger_doc.get('net_balance'):>10.2f} "
            f"-> {new_net_balance:>10.2f}  (target {target_balance:.2f}, "
            f"reversing {reversal_total:.2f} across {len(reversal_ids)} payments)"
        )

    mismatches = [p for p in plan if not p["matches_target"]]
    if mismatches:
        print()
        print(f"ABORT: {len(mismatches)} unit(s) do not land on their stored "
              f"reconciliation_target_balance after reversal — refusing to write anything.")
        return 1

    total_before = round(sum(p["before_net_balance"] for p in plan), 2)
    total_after = round(sum(p["after_net_balance"] for p in plan), 2)
    print()
    print(f"All {len(plan)} units verified against their own reconciliation_target_balance.")
    print(f"Sum of affected units: {total_before:.2f} -> {total_after:.2f}")

    if not args.apply:
        print()
        print("Dry-run complete. Re-run with --apply to write these changes.")
        return 0

    print()
    print("Applying...")
    now = datetime.now(timezone.utc).isoformat()

    for entry in plan:
        result = await db.levy_payments.update_many(
            {"id": {"$in": entry["reversal_ids"]}, "building_id": BUILDING_ID},
            {"$set": {
                "status": "reversed",
                "reversed_at": now,
                "reversed_reason": REVERSAL_REASON,
            }},
        )
        if result.modified_count != len(entry["reversal_ids"]):
            print(f"  ABORT mid-run: expected to reverse {len(entry['reversal_ids'])} "
                  f"payments for {entry['unit_number']}, modified {result.modified_count}")
            return 1

        await db.unit_levy_ledger.update_one(
            {"building_id": BUILDING_ID, "unit_number": entry["unit_number"], "year": "2026"},
            {"$set": {
                "admin_paid": entry["new_admin_paid"],
                "sinking_paid": entry["new_sinking_paid"],
                "total_paid": entry["new_total_paid"],
                "admin_closing": entry["new_admin_closing"],
                "sinking_closing": entry["new_sinking_closing"],
                "net_balance": entry["after_net_balance"],
                "applied_payment_ids": entry["remaining_applied_payment_ids"],
                "updated_at": now,
                "reversal_note": REVERSAL_REASON,
                "reversal_applied_at": now,
            }},
        )
        print(f"  Reversed {entry['unit_number']}: net_balance now {entry['after_net_balance']:.2f}")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
