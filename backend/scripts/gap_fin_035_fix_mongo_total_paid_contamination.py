#!/usr/bin/env python3
"""GAP-FIN-035 Item 2 follow-up (round 3) — fix Mongo unit_levy_ledger.total_paid contamination.

unit_levy_ledger.total_paid/admin_paid/sinking_paid are documented (in each doc's own
reconciliation_note) as life-to-date cumulative figures, not year-scoped -- but the STORED
values for East Gate's 2026 docs still reflect a real duplicate-GL-credit bug that was fixed
in Postgres on 2026-08-02 (87 stray journal entries reversed via
FinancialCoreService.reverse_entry()) and never re-synced to Mongo. Every one of East Gate's
87 2026 unit_levy_ledger docs currently has total_paid wildly larger than net_balance and
total_levied would imply (building-wide sum $1,769,655.36 vs a real total_levied of
$220,187.56) -- confirmed still live 2026-08-03.

No downstream code actually trusts total_paid directly any more -- get_collection_rate_metrics()
and every other consumer already derives the correct year-scoped "collected" figure
algebraically as total_levied - net_balance (net_balance being the one field this codebase's
own header comment calls "always the authoritative accounting balance"). This script makes the
STORED total_paid/admin_paid/sinking_paid fields consistent with that same formula, so a
future reader of the raw document isn't misled by a field that visibly disagrees with the
ledger's own authoritative balance.

This intentionally changes what total_paid MEANS (from "life-to-date cumulative" to "this
year's levied minus this year's outstanding balance") for 2026 specifically, because the
life-to-date figure it currently holds is not recoverable to a correct value without a full
payment-history replay this session cannot perform -- and per CLAUDE.md's "never displayed as
$0.00 / zero and missing are distinct" rule, leaving a KNOWN-WRONG number in place is worse
than replacing it with a clearly-labelled, internally-consistent one. The old (contaminated)
value is preserved verbatim in a new total_paid_pre_gap_fin_035_correction field, never
deleted, so the original number remains auditable.

Usage:
    cd backend
    venv/bin/python3 scripts/gap_fin_035_fix_mongo_total_paid_contamination.py \\
        --building-id 13195 --year 2026                 # dry-run (default)
    venv/bin/python3 scripts/gap_fin_035_fix_mongo_total_paid_contamination.py \\
        --building-id 13195 --year 2026 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402


async def main(building_id: str, year: str, apply: bool) -> None:
    set_ctx_building_id(building_id)

    docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year, "is_test_data": {"$ne": True}},
    ).to_list(200)

    now = datetime.now(timezone.utc)
    corrections = []
    for d in docs:
        total_levied = float(d.get("total_levied") or 0)
        net_balance = float(d.get("net_balance") or 0)
        admin_levied = float(d.get("admin_levied") or 0)
        admin_closing = float(d.get("admin_closing") or 0)
        sinking_levied = float(d.get("sinking_levied") or 0)
        sinking_closing = float(d.get("sinking_closing") or 0)

        new_total_paid = round(total_levied - net_balance, 2)
        new_admin_paid = round(admin_levied - admin_closing, 2)
        new_sinking_paid = round(sinking_levied - sinking_closing, 2)

        old_total_paid = d.get("total_paid")
        old_admin_paid = d.get("admin_paid")
        old_sinking_paid = d.get("sinking_paid")

        if (
            abs((old_total_paid or 0) - new_total_paid) <= 0.01
            and abs((old_admin_paid or 0) - new_admin_paid) <= 0.01
            and abs((old_sinking_paid or 0) - new_sinking_paid) <= 0.01
        ):
            continue

        corrections.append({
            "unit_number": d["unit_number"],
            "old_total_paid": old_total_paid,
            "new_total_paid": new_total_paid,
            "old_admin_paid": old_admin_paid,
            "new_admin_paid": new_admin_paid,
            "old_sinking_paid": old_sinking_paid,
            "new_sinking_paid": new_sinking_paid,
        })

    print(f"building_id={building_id} year={year} mode={'APPLY' if apply else 'DRY-RUN'}")
    print(f"Total units checked: {len(docs)}")
    print(f"Units needing correction: {len(corrections)}")
    for c in corrections[:10]:
        print(f"  {c['unit_number']}: total_paid {c['old_total_paid']:,.2f} -> {c['new_total_paid']:,.2f}")
    if len(corrections) > 10:
        print(f"  ... and {len(corrections) - 10} more")

    old_sum = sum(c["old_total_paid"] or 0 for c in corrections)
    new_sum = sum(c["new_total_paid"] for c in corrections)
    print(f"\nSum total_paid: ${old_sum:,.2f} -> ${new_sum:,.2f}")

    if not apply:
        print("\nDry run -- not writing anything. Re-run with --apply to correct.")
        return

    for c in corrections:
        await db.unit_levy_ledger.update_one(
            {"building_id": building_id, "unit_number": c["unit_number"], "year": year},
            {
                "$set": {
                    "total_paid": c["new_total_paid"],
                    "admin_paid": c["new_admin_paid"],
                    "sinking_paid": c["new_sinking_paid"],
                    "total_paid_pre_gap_fin_035_correction": c["old_total_paid"],
                    "admin_paid_pre_gap_fin_035_correction": c["old_admin_paid"],
                    "sinking_paid_pre_gap_fin_035_correction": c["old_sinking_paid"],
                    "total_paid_correction_note": (
                        "GAP-FIN-035 (2026-08-03): total_paid/admin_paid/sinking_paid corrected "
                        "to be internally consistent with net_balance/admin_closing/sinking_closing "
                        "(total_levied - net_balance), replacing a stale life-to-date figure that "
                        "still reflected a duplicate-GL-credit bug fixed in Postgres on 2026-08-02 "
                        "but never re-synced to Mongo. Original value preserved in "
                        "total_paid_pre_gap_fin_035_correction."
                    ),
                    "total_paid_corrected_at": now,
                    "updated_at": now,
                }
            },
        )
    print(f"\nDone. Corrected {len(corrections)} unit(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", type=str, required=True)
    parser.add_argument("--year", type=str, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.building_id, args.year, args.apply))
