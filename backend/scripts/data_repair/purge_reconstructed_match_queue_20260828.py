#!/usr/bin/env python3
# NOTE — building-agnostic; --building-id defaults to "13195" only because that is where
# the residue was found. Nothing here special-cases East Gate.
# @featuretrace:financial_matching — Purge reconstructed-historical rows from the match review queue.
# Layer: script
# Data flow: match_review_queue (building-scoped) -> JSON backup -> delete_many.
# Related: backend/routers/financial_matching.py (the queue's UI/API)
#          backend/routers/bank_feeds.py (what puts rows here)
"""Purge match-review-queue rows that were never reviewable, and de-duplicate the rest.

Why
---
`match_review_queue` exists to match an OBSERVED bank transaction to a lot so a human
can confirm the allocation. A historical reconstruction run put 3,945 rows in it that
cannot serve that purpose:

  * every one is `transaction_origin = reconstructed_historical` — modelled, not
    observed. Their own assumption codes say so: `annual_lump_sum`,
    `half_yearly_lump_sum`, `quarterly_regular`, `late`, `arrears_catch_up`.
  * 117 are NEGATIVE — building-level expense summaries, not owner payments. One reads
    "Estimated 2021 spend summary: Utilities - Water & Sewerage (Admin Fund) —
    reported ANNUAL TOTAL, not a single dated transaction".
  * `best_score` is 0.0 on ALL of them. The matcher is not failing; there is genuinely
    nothing per-lot to match an annual building expense against.

Left in place they are permanent noise in the reviewer's queue, and worse, they invite
a bulk approve that would post ~$1.43M of modelled transactions to the ledger as though
confirmed — double-counting a 2021-2025 ledger that already reconciles to the portal.

What is kept
------------
  * Any row NOT tagged `reconstructed_historical`.
  * Any row already `allocated` — it has been actioned and posting history must stand.
  * Rows an identified actor decided — `decision` set AND `decided_by` populated.
    A `decision` with a NULL `decided_by` is an incomplete promotion, not a judgement,
    and is purged with the rest of its batch.

De-duplication
--------------
Separately, a queue row can be created twice for one transaction when matching is
dispatched more than once for it — `run_bank_feed_sync` fires the MatchingEngine as a
fire-and-forget background task, so an in-process caller that also dispatches manually
produces a second row. Rows sharing a `tx.provider_txn_id` are collapsed to the OLDEST,
which is the one any earlier reviewer would have been looking at.

Nothing is hard-deleted without a timestamped JSON backup first.

Usage (from repo root):
    backend/venv/bin/python3 backend/scripts/data_repair/purge_reconstructed_match_queue_20260828.py
    ... --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

BACKUP_DIR = ROOT / "backend" / "scripts" / "data_repair" / "backups"
_PURGE_ORIGIN = "reconstructed_historical"
_KEEP_STATUSES = {"allocated"}


def _origin(row: dict) -> str | None:
    return ((row.get("tx") or {}).get("metadata") or {}).get("transaction_origin")


def classify(row: dict) -> tuple[bool, str]:
    """(delete, reason). Non-delete reasons are printed too — a dry run should say why
    every surviving row survived, not only why the others go."""
    status = (row.get("status") or "").strip().lower()
    if status in _KEEP_STATUSES:
        return False, f"keep: already actioned (status={status})"
    # A `decision` object alone is NOT evidence someone decided. 348 East Gate rows
    # carry decision={"action":"allocate", ...,"notes":"Deterministic high-confidence
    # promotion"} with decided_by AND decided_at both NULL and status still pending —
    # promotions that were started and never completed. The 40 that did complete carry
    # decided_by='system:high_confidence_promotion' and status='allocated'.
    # Require an actual decider, or a half-finished promotion masquerades as a human
    # call and survives a purge it belongs in.
    if row.get("decision") and row.get("decided_by"):
        return False, f"keep: decided by {row.get('decided_by')!r}"
    if _origin(row) != _PURGE_ORIGIN:
        return False, f"keep: origin={_origin(row)!r}, not a reconstruction"
    return True, "reconstructed_historical, unactioned — modelled, never observed; best_score 0.0"


async def run(building_id: str, apply: bool) -> dict:
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    from database import db

    coll = db.match_review_queue
    rows = await coll.find({"building_id": building_id}, {"_id": 0}).to_list(None)

    to_delete, kept = [], Counter()
    for r in rows:
        delete, reason = classify(r)
        (to_delete.append((r, reason)) if delete else kept.update([reason.split(":")[0] + ": " + reason.split(": ", 1)[1][:48]]))

    # De-duplicate what survives, by the transaction's own provider id.
    #
    # Identity (`is`), not equality, and deliberately. `to_delete` holds the SAME dict
    # objects that `rows` holds — they are appended straight out of the loop above, not
    # copied — so identity is exact. Equality would be wrong here as well as slower: two
    # DIFFERENT queue rows can compare equal (same amount, same lot, same day, distinct
    # inbox_event_id is the only field that separates them), and `r not in [...]` would
    # drop a row that was never marked for deletion. A set of id()s keeps that exactness
    # at O(n) instead of the O(n^2) scan this used to do over 4,025 rows.
    doomed = {id(d) for d, _ in to_delete}
    survivors = [r for r in rows if id(r) not in doomed]
    by_txn: dict[str, list[dict]] = defaultdict(list)
    for r in survivors:
        pid = (r.get("tx") or {}).get("provider_txn_id")
        if pid:
            by_txn[str(pid)].append(r)
    dup_delete = []
    for pid, group in by_txn.items():
        if len(group) > 1:
            # Sort undated rows LAST so a dated row wins "oldest", which is the one a
            # reviewer would have been looking at. String-sorting `str(created_at)` got
            # this right only by accident — "None" happens to sort after "2026-..." —
            # and the obvious repair (falling back to datetime.min) inverts it, keeping
            # the undated row and deleting the real ones. Made explicit instead.
            group.sort(key=lambda r: (r.get("created_at") is None, str(r.get("created_at") or "")))
            for extra in group[1:]:
                dup_delete.append((extra, f"duplicate queue row for provider_txn_id={pid[:16]}…"))

    all_delete = to_delete + dup_delete

    print(f"\n{'=' * 88}")
    print(f"match_review_queue purge — building {building_id}   [{'APPLY' if apply else 'DRY-RUN'}]")
    print("=" * 88)
    print(f"  rows total            {len(rows)}")
    print(f"  delete (reconstruction){len(to_delete):>6}")
    print(f"  delete (duplicates)   {len(dup_delete):>6}")
    print(f"  keep                  {len(rows) - len(all_delete):>6}")
    print("\n  kept, by reason:")
    for reason, n in kept.most_common():
        print(f"    {n:>5}  {reason}")
    if dup_delete:
        print("\n  duplicates removed (oldest row of each pair is kept):")
        for r, reason in dup_delete[:5]:
            tx = r.get("tx") or {}
            print(f"    amount={tx.get('amount_cents')} lot={tx.get('lot_ref_raw')} "
                  f"created={str(r.get('created_at'))[:19]}")

    if not apply:
        print("\n  Dry-run. Re-run with --apply to delete.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0}

    if not all_delete:
        print("\n  Nothing to delete.")
        return {"total": len(rows), "would_delete": 0, "deleted": 0}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_DIR / f"match_review_queue_{building_id}_{stamp}.json"
    # Write, then PROVE it landed. This is the only copy of rows that are about to be
    # hard-deleted, and a write that fails on a full disk or a read-only mount must stop
    # the delete rather than precede it.
    try:
        backup.write_text(json.dumps([r for r, _ in all_delete], indent=2, default=str))
    except OSError as exc:
        print(f"  ABORT: could not write the backup ({exc}); nothing deleted.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0,
                "aborted": f"backup write failed: {exc}"}
    if not backup.exists() or backup.stat().st_size == 0:
        print("  ABORT: backup is missing or empty after writing; nothing deleted.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0,
                "aborted": "backup verification failed"}
    try:
        restored = json.loads(backup.read_text())
    except json.JSONDecodeError as exc:
        print(f"  ABORT: backup is not readable JSON ({exc}); nothing deleted.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0,
                "aborted": "backup unreadable"}
    if len(restored) != len(all_delete):
        print(f"  ABORT: backup holds {len(restored)} of {len(all_delete)} rows; nothing deleted.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0,
                "aborted": "backup incomplete"}
    print(f"\n  backup written and verified ({len(restored)} rows): {backup}")

    # match_review_queue rows carry NO `id`/`queue_id` field — verified live, 0 of 4,025.
    # `inbox_event_id` is the unique key (4,025 distinct across 4,025 rows). Deleting on
    # a field that does not exist would match nothing and report success.
    ids = [r.get("inbox_event_id") for r, _ in all_delete]
    ids = [i for i in ids if i]
    if len(ids) != len(all_delete):
        # Refuse a partial delete rather than guess at a key-less row.
        print(f"  ABORT: {len(all_delete) - len(ids)} row(s) have no inbox_event_id; nothing deleted.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0,
                "aborted": "rows without an inbox_event_id"}
    if len(set(ids)) != len(ids):
        print("  ABORT: inbox_event_id is not unique across the delete set; nothing deleted.")
        return {"total": len(rows), "would_delete": len(all_delete), "deleted": 0,
                "aborted": "non-unique key"}

    res = await coll.delete_many({"building_id": building_id, "inbox_event_id": {"$in": ids}})
    print(f"  deleted {res.deleted_count} rows")
    shortfall = len(all_delete) - res.deleted_count
    if shortfall:
        # Not an error — a concurrent writer may legitimately have removed a row between
        # the read and the delete. But it means the queue is not in the state this run
        # computed, so say so rather than let "deleted N" imply the plan was carried out.
        print(f"  NOTE: planned {len(all_delete)}, deleted {res.deleted_count} "
              f"({shortfall} already gone). Re-run to confirm the queue is settled; "
              f"the backup above still holds all {len(all_delete)} planned rows.")
    remaining = await coll.count_documents({"building_id": building_id})
    print(f"  remaining in queue: {remaining}")
    return {"total": len(rows), "would_delete": len(all_delete),
            "deleted": res.deleted_count, "remaining": remaining, "backup": str(backup)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", default="13195")
    ap.add_argument("--apply", action="store_true", help="Delete. Default is a dry run.")
    args = ap.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply)), indent=2, default=str))


if __name__ == "__main__":
    main()
