#!/usr/bin/env python3
# @featuretrace:financial_core — Retire the 88 duplicate portal-scrape receipts.
# Layer: script
# Data flow: finance.receipts (manual_adjustment, portal scrape) -> retired_at set (building-scoped).
# Related: backend/alembic/versions/0098_receipt_retirement.py
#          tasks/GAP-FIN-073-post-restore-finance-audit.md
"""Retire the duplicate portal-scrape receipts. Marks, never deletes.

    python3 scripts/data_repair/eastgate_retire_duplicate_receipts.py --dry-run
    python3 scripts/data_repair/eastgate_retire_duplicate_receipts.py --apply

East Gate holds 88 `manual_adjustment` receipts totalling $1,771,185.66, dated
2026-08-01 to 2026-08-05, every one carrying `external_reference` beginning
`strata_web_portal_scrape`.

They are duplicates on the evidence, not by assumption:

  * they cover all 87 lots, and for each lot the manual amount EXACTLY equals the sum of
    that lot's ordinary receipts
  * none is allocated to any levy item
  * the receipts table sums to $3,564,955.45 against $1,771,930.86 of levy income

The general ledger is already correct — proof 4 in GAP-FIN-073 shows the Bank Account
moved $0.02 and Accounts Receivable equals the Mongo net_balance sum to the cent, so the
127 reversal entries did their job. This is therefore a hygiene action on the receipts
table, NOT a financial correction. Nothing about cash, income or arrears changes.

Marking rather than deleting is not a preference. ACT/NSW seven-year retention forbids
destroying the record of a posted receipt, and the journal entries behind these are
immutable regardless.

SAFETY: the selection is deliberately narrow and each condition is re-verified at run
time rather than trusted from this docstring — channel, reference prefix, zero
allocations, and an exact per-lot match against the lot's non-manual receipts. A row that
fails ANY of them is reported and skipped, because a manual adjustment that is genuinely
someone's payment must not be retired on a pattern match.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("retire_receipts")

TENANT = "9e9d75c2-bd92-4695-8487-1592018c3af9"
REASON = ("duplicate of the lot's ordinary receipts; sourced from a portal scrape, which "
          "is reconciliation evidence and never a journal source. Already offset in the "
          "GL — see GAP-FIN-073 proof 4.")


async def main(args) -> int:
    pg = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await pg.execute(f"SET app.tenant_id = '{TENANT}'")

        # Every condition re-checked here, not assumed:
        #   channel + reference prefix  -> the population
        #   no allocations              -> it funds no levy item
        #   manual total == other total -> an exact per-lot duplicate
        candidates = await pg.fetch("""
            WITH per_lot AS (
                SELECT lot_id,
                       SUM(amount_cents) FILTER (WHERE channel = 'manual_adjustment')     AS manual,
                       SUM(amount_cents) FILTER (WHERE channel <> 'manual_adjustment')    AS other
                  FROM finance.receipts
                 WHERE retired_at IS NULL
                 GROUP BY lot_id
            )
            SELECT r.receipt_id, r.lot_id, r.amount_cents, r.received_on,
                   l.unit_number, pl.manual, pl.other,
                   (SELECT count(*) FROM finance.receipt_allocations ra
                     WHERE ra.receipt_id = r.receipt_id) AS allocs
              FROM finance.receipts r
              JOIN per_lot pl ON pl.lot_id = r.lot_id
              LEFT JOIN core.lots l ON l.lot_id = r.lot_id
             WHERE r.channel = 'manual_adjustment'
               AND r.external_reference LIKE 'strata_web_portal_scrape%'
               AND r.retired_at IS NULL
             ORDER BY l.unit_number
        """)

        retire, skip = [], []
        for c in candidates:
            if c["allocs"]:
                skip.append((c, f"has {c['allocs']} allocation(s) — it funds a levy item"))
            elif c["other"] is None or c["manual"] != c["other"]:
                skip.append((c, f"manual {c['manual']} != other {c['other']} — not an exact duplicate"))
            else:
                retire.append(c)

        total = sum(int(c["amount_cents"]) for c in retire)
        logger.info("%s candidate(s); %s retire, %s skipped", len(candidates), len(retire), len(skip))
        logger.info("  value to retire: $%s", f"{total/100:,.2f}")
        for c, why in skip:
            logger.info("  SKIP %-7s $%-12s %s", c["unit_number"], f"{int(c['amount_cents'])/100:,.2f}", why)

        if not args.apply:
            logger.info("DRY-RUN — re-run with --apply. Nothing about cash, income or "
                        "arrears changes; only aggregates over finance.receipts.")
            return 0

        ids = [c["receipt_id"] for c in retire]
        await pg.execute("""
            UPDATE finance.receipts
               SET retired_at = NOW(), retired_reason = $2
             WHERE receipt_id = ANY($1::uuid[])
        """, ids, REASON)

        live = await pg.fetchval("SELECT SUM(amount_cents) FROM finance.receipts WHERE retired_at IS NULL")
        logger.info("APPLIED: %s receipt(s) retired. finance.receipts live total is now $%s",
                    len(ids), f"{float(live)/100:,.2f}")
        return 0
    finally:
        await pg.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
