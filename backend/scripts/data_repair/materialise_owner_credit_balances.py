#!/usr/bin/env python3
# @featuretrace:finance-postgres-read-cutover — make unapplied credit explicit.
# Layer: script
# Data flow: unallocated finance.receipts -> finance.owner_credit_balances (building-scoped).
# Related: backend/scripts/data_repair/reconstruct_receipt_allocations.py
#          docs/architecture/allocation_trail_reconstruction_2026-08-30.md
# Tests: tests/backend/test_owner_credit_materialisation.py
"""Turn genuine unapplied receipts into explicit owner credit — and only genuine ones.

DRY-RUN BY DEFAULT. `--apply` is a production financial mutation.

WHY MOST OF THE "SURPLUS" IS NOT CREDIT
---------------------------------------
The unallocated-receipt total looks like ~$362,000 of owner credit. It is not. Broken
down by declared provenance:

    manual_adjustment, ref strata_web_portal_scrape_*   17   ~$315,000   EXCLUDED
    bank_transfer (organic)                             44    ~$29,000   credit
    civium_portal_scrape                                14     $9,084    EXCLUDED
    reconstructed_historical                            19     $4,741    credit
    eft                                                  3     $2,864    credit

The 17 large `manual_adjustment` rows are all dated a single day (2026-08-01) and all
reference a portal scrape. They are **application-manufactured balances, not payments the
owners corporation received** — the operator's standing decision is that these do not
count, and writing them as credit would invent roughly $315,000 of owner money.

The 14 `civium_portal_scrape` receipts ($9,084.23) are the known unbanked cohort that the
2026-08-27 retirement campaign missed. They are excluded for the same reason and are
tracked separately, not resolved here.

This is the same trap as 2026-08-28, when acting on "an unallocated receipt must be
wrong" retired 14 real credit receipts and had to be rolled back the same day. The rule
that survives both incidents: **an unallocated receipt is credit ONLY when its provenance
says money actually arrived.**

WHAT IT WRITES
--------------
One `finance.owner_credit_balances` row per (lot, fund), `available_cents` being the
lot's unallocated receipts of admitted provenance. It never allocates, never retires a
receipt, and never touches `paid_cents`.

    python3 backend/scripts/data_repair/materialise_owner_credit_balances.py --building-id 13195
    python3 backend/scripts/data_repair/materialise_owner_credit_balances.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402

BYPASS = "00000000-0000-0000-0000-000000000000"

# Provenance that means money actually arrived. An allow-list, not a deny-list: a new
# origin nobody has assessed must be EXCLUDED by default and reported, because the failure
# mode here is inventing owner credit, and that is not self-correcting.
ADMITTED_CHANNELS = {"bank_transfer", "eft", "direct_debit", "bpay", "cheque", "cash"}

# Even within an admitted channel, these references mark a manufactured balance.
EXCLUDED_REFERENCE_MARKERS = ("strata_web_portal_scrape", "civium_portal_scrape")


def _d(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def is_admitted(channel: str | None, reference: str | None, origin: str | None,
                source: str | None = None) -> tuple[bool, str]:
    """Does this receipt represent money that actually arrived? Pure, so it is testable.

    The portal marker can appear in EITHER `external_reference` or `metadata.source` —
    the 14 civium receipts carry it in the latter while presenting as channel
    `bank_transfer`, so a reference-only check admitted them as credit while this
    module's own docstring said they were excluded. Both fields are searched.
    """
    haystack = f"{(reference or '')} {(source or '')}".lower()
    for marker in EXCLUDED_REFERENCE_MARKERS:
        if marker in haystack:
            return False, f"portal-derived ({marker})"
    if (origin or "").lower() == "reconstructed_historical":
        # Reconstructed history is a deliberate, documented representation of payments
        # that genuinely occurred before the feed existed. Admitted.
        return True, "reconstructed_historical"
    ch = (channel or "").lower()
    if ch in ADMITTED_CHANNELS:
        return True, f"channel={ch}"
    return False, f"channel={ch or '(none)'} not in the admitted list"


async def run(building_id: str, apply: bool) -> int:
    pg = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        scheme = await pg.fetchrow(
            "SELECT scheme_id::text sid, tenant_id::text tid FROM core.schemes "
            "WHERE scheme_number = $1 AND is_test_data = FALSE",
            building_id,
        )
        if not scheme:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tid"])

        rows = await pg.fetch(
            """
            SELECT r.receipt_id::text, r.lot_id::text, l.unit_number, r.amount_cents,
                   r.channel::text AS channel, r.external_reference,
                   r.metadata->>'transaction_origin' AS origin,
                   r.metadata->>'source' AS source, r.payer_party_id::text
              FROM finance.receipts r
              JOIN core.lots l ON l.lot_id = r.lot_id
             WHERE r.scheme_id = $1::uuid AND r.retired_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM finance.receipt_allocations a
                                WHERE a.receipt_id = r.receipt_id)
            """,
            scheme["sid"],
        )

        admitted, excluded = [], {}
        for row in rows:
            ok, why = is_admitted(row["channel"], row["external_reference"],
                                  row["origin"], row["source"])
            if ok:
                admitted.append(dict(row))
            else:
                excluded.setdefault(why, [0, 0])
                excluded[why][0] += 1
                excluded[why][1] += int(row["amount_cents"])

        by_lot: dict[str, dict] = {}
        for row in admitted:
            entry = by_lot.setdefault(
                row["lot_id"], {"unit": row["unit_number"], "cents": 0,
                                "party": row["payer_party_id"]})
            entry["cents"] += int(row["amount_cents"])

        print("=" * 78)
        print(f"Owner credit materialisation — building {building_id}  "
              f"[{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 78)
        print(f"  unallocated receipts        : {len(rows)}  "
              f"{_d(sum(r['amount_cents'] for r in rows))}")
        print(f"  ADMITTED as owner credit    : {len(admitted)}  "
              f"{_d(sum(r['amount_cents'] for r in admitted))}  across {len(by_lot)} lot(s)")
        print("  EXCLUDED (not money received):")
        for why, (n, c) in sorted(excluded.items(), key=lambda kv: -kv[1][1]):
            print(f"      {why[:48]:<48} n={n:<4} {_d(c)}")
        print("\n  credit by lot (top 10):")
        for lot_id, e in sorted(by_lot.items(), key=lambda kv: -kv[1]["cents"])[:10]:
            print(f"      {e['unit']:<8} {_d(e['cents'])}")

        fund = await pg.fetchrow(
            "SELECT fund_id::text FROM finance.funds WHERE scheme_id=$1::uuid "
            "AND fund_type='admin' LIMIT 1", scheme["sid"])
        if not fund:
            print("\n  REFUSING — no admin fund for this scheme.")
            return 1

        if not apply:
            print("\n  DRY-RUN — nothing written. Re-run with --apply.")
            return 0

        async with pg.transaction():
            for lot_id, e in by_lot.items():
                await pg.execute(
                    """
                    INSERT INTO finance.owner_credit_balances
                        (tenant_id, scheme_id, lot_id, owner_party_id, fund_id, available_cents)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6)
                    """,
                    scheme["tid"], scheme["sid"], lot_id, e["party"],
                    fund["fund_id"], e["cents"],
                )
        total = await pg.fetchval(
            "SELECT COALESCE(SUM(available_cents),0) FROM finance.owner_credit_balances")
        print(f"\n  wrote {len(by_lot)} credit row(s). owner_credit_balances now {_d(total)}.")
        return 0
    finally:
        await pg.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true", help="PRODUCTION FINANCIAL MUTATION")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.apply)))


if __name__ == "__main__":
    main()
