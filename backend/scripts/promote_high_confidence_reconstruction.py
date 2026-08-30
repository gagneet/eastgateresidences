# @featuretrace:demo_bank — CLI dry-run/execution wrapper for deterministic high-confidence promotion.
# Layer: cron
# Data flow: match_review_queue + demo_bank_transactions (Mongo, building-scoped)
#            → routers.financial_matching.promote_high_confidence_reconstruction()
#            → finance.receipts / unit_levy_ledger (via _post_payment_to_ledger).
# Related: backend/routers/financial_matching.py
#          backend/services/receivables_resolver.py
#          docs/migration/strataos_finance_ledger_segregation_next_phase_2026-07-05.md
"""
CLI wrapper for promote_high_confidence_reconstruction().

Doc Stage B requires a dry-run pass over a building's pending match_review_queue
items before any real promotion runs. This script lists every item whose
underlying Demo Bank transaction qualifies for deterministic promotion
(source_type in the promotable allowlist, confidence="high", requires_review=False)
and either reports them (--dry-run, the default) or promotes them for real.

This script is NOT intended to be run against a production building (e.g. East
Gate, 13195) without a separately verified small-slice sign-off — see the
"Deployment / cutover sequence" section of the migration doc referenced above.

Usage:
    python3 scripts/promote_high_confidence_reconstruction.py --building-id 13195 --dry-run
    python3 scripts/promote_high_confidence_reconstruction.py --building-id 13195   # real run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

_PROMOTABLE_STATUSES = {"pending", "unidentified", "review"}


async def _candidate_items(db, building_id: str) -> list[dict]:
    """Generated function header.

    Function: _candidate_items
    Path: backend/scripts/promote_high_confidence_reconstruction.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from routers.financial_matching import _PROMOTABLE_SOURCE_TYPES

    items = await db.match_review_queue.find(
        {"building_id": building_id, "status": {"$in": list(_PROMOTABLE_STATUSES)}}
    ).to_list(length=None)

    candidates = []
    for item in items:
        tx_doc = await db.demo_bank_transactions.find_one({
            "building_id": building_id,
            "finance_bank_transaction_ref": item.get("inbox_event_id"),
        })
        if not tx_doc:
            continue
        if tx_doc.get("source_type") not in _PROMOTABLE_SOURCE_TYPES:
            continue
        if tx_doc.get("confidence") != "high" or tx_doc.get("requires_review"):
            continue
        candidates.append({"item": item, "tx_doc": tx_doc})
    return candidates


async def run(building_id: str, dry_run: bool) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/promote_high_confidence_reconstruction.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from request_context import set_ctx_building_id
    from routers.financial_matching import promote_high_confidence_reconstruction

    set_ctx_building_id(building_id)
    candidates = await _candidate_items(db, building_id)

    result = {
        "building_id": building_id,
        "dry_run": dry_run,
        "candidates_found": len(candidates),
        "promoted": 0,
        "failed": 0,
        "items": [],
    }

    for candidate in candidates:
        item = candidate["item"]
        tx_doc = candidate["tx_doc"]
        item_id = str(item["_id"])
        row = {
            "queue_item_id": item_id,
            "unit_number": tx_doc.get("unit_number"),
            "amount_cents": (item.get("tx") or {}).get("amount_cents"),
            "source_type": tx_doc.get("source_type"),
        }
        if dry_run:
            row["action"] = "would_promote"
            result["items"].append(row)
            continue

        try:
            promoted = await promote_high_confidence_reconstruction(
                queue_item_id=item_id, building_id=building_id,
            )
            row["action"] = "promoted"
            row["receipt_id"] = promoted.get("receipt_id")
            result["promoted"] += 1
        except Exception as exc:  # noqa: BLE001 — surfaced per-item in the report
            row["action"] = "failed"
            row["error"] = str(exc)
            result["failed"] += 1
        result["items"].append(row)

    return result


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/promote_high_confidence_reconstruction.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description="Dry-run or execute deterministic high-confidence promotion for a building"
    )
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    args = parser.parse_args()

    result = await run(args.building_id, dry_run=args.dry_run)

    print("\n── High-Confidence Promotion Result ──────────────")
    print(f"  building_id:      {result['building_id']}")
    print(f"  dry_run:          {result['dry_run']}")
    print(f"  candidates_found: {result['candidates_found']}")
    print(f"  promoted:         {result['promoted']}")
    print(f"  failed:           {result['failed']}")
    for row in result["items"]:
        print(f"    {row}")
    if args.dry_run:
        print("\n  [DRY RUN] No records written. Re-run with --no-dry-run to apply.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
