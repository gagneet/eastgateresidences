"""
Backfill author_id="system" on generated levy-notice documents (2026-08-24)
===========================================================================

## The problem

`cron_payment_reminders.py` writes a PDF levy notice per unit into `documents`,
stamped with a 30-day `expires_at`. `cron_notification_cleanup.py` purges expired
documents — but only those carrying `author_id == "system"`:

    await db.documents.delete_many({
        "building_id": {"$exists": True},
        "author_id": "system",                 # <-- the guard
        "expires_at": {"$lt": now_iso},
    })

That guard is deliberate and correct: a human-authored document that happens to
carry an expiry must never be hard-deleted by a cron. But the generator never set
the field, so **not one generated notice has ever been reapable** and they
accumulate without limit.

Live confirmation (East Gate, 13195, 2026-08-24):

    levy notices total           : 240
      with author_id field       :   0
    => reapable by cleanup cron  :   0

All 240 were written in a single run on 2026-08-18. They were still present long
after the levy data they describe had been deleted (`units`, `user_levy_ledger`,
`levy_payments`, `core.lots` are all zero for this building), and were still being
advertised in the dashboard activity feed as "Document uploaded: Levy Notice -
TH083 - 2026-09-01".

A 30-day expiry that nothing can act on is worse than no expiry at all: it reads
as "transient, self-cleaning" while the rows stay for ever.

The generator has been fixed to stamp `author_id` going forward. This script
repairs the documents already written.

## What it does

Sets `author_id = "system"` on documents that are unambiguously generator output.
It does NOT delete anything. Once tagged, `cron_notification_cleanup` reaps each
notice on its own schedule, once past its own `expires_at` — the normal lifecycle
these documents were always meant to have.

## Selection criteria (ALL must hold)

* `title` starts with "Levy Notice " — the generator's exact format
* `category == "finance"`
* `expires_at` present — proves it was written with a lifecycle
* `unit_number` present — generated notices are per-unit
* `author_id` absent — never touch a document that already has attribution

Deliberately conservative. East Gate's two genuine documents (one `ec_documents`,
one `financial_reports`, both April 2026) fail on category and title and are never
matched. Anything a human uploaded is left alone.

## Safety

* Dry-run by default; `--apply` required to write.
* Building-scoped: `--building` is REQUIRED. There is no "all buildings" mode —
  tagging documents for deletion across every tenant at once is not something
  this script should be able to do by accident.
* Idempotent: the `author_id: {"$exists": False}` filter means a second run
  matches nothing.
* Reversible: `--revert` removes the field from documents this script would have
  tagged, restoring the previous (unreapable) state.

Usage:

    python3 scripts/data_repair/backfill_generated_notice_author_id_20260824.py --building 13195
    python3 scripts/data_repair/backfill_generated_notice_author_id_20260824.py --building 13195 --apply
    python3 scripts/data_repair/backfill_generated_notice_author_id_20260824.py --building 13195 --revert --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_notice_author_id")

#: Marker the cleanup cron requires before it will purge an expired document.
SYSTEM_AUTHOR = "system"

#: Exact prefix cron_payment_reminders uses: f"Levy Notice - {unit} - {date}".
NOTICE_TITLE_PREFIX = "Levy Notice - "


def selection_filter(building_id: str, *, tagged: bool) -> dict:
    """Documents that are unambiguously generated levy notices.

    ``tagged`` selects the post-state (author_id already set) for --revert, so
    both directions share one definition and cannot drift apart.
    """
    return {
        "building_id": building_id,
        "title": {"$regex": f"^{NOTICE_TITLE_PREFIX}"},
        "category": "finance",
        "expires_at": {"$exists": True},
        "unit_number": {"$exists": True},
        "author_id": SYSTEM_AUTHOR if tagged else {"$exists": False},
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--building", required=True,
                   help="building_id to repair (required; there is no all-buildings mode)")
    p.add_argument("--apply", action="store_true", default=False,
                   help="actually write; omit for a dry run")
    p.add_argument("--revert", action="store_true", default=False,
                   help="remove author_id from documents this script tagged")
    return p.parse_args()


async def _main() -> int:
    args = parse_args()
    building_id = args.building

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        query = selection_filter(building_id, tagged=args.revert)
        matched = await db.documents.count_documents(query)

        total_docs = await db.documents.count_documents({"building_id": building_id})
        logger.info("building %s: %d documents total, %d match the %s criteria",
                    building_id, total_docs, matched,
                    "revert" if args.revert else "backfill")

        if matched == 0:
            logger.info("Nothing to do.")
            return 0

        # Show what is NOT being touched, so an operator can see the blast radius
        # rather than trusting the count.
        untouched = total_docs - matched
        if untouched:
            # Query the complement directly. Scanning a page of documents and
            # filtering client-side showed nothing here, because the first page was
            # entirely notices — an operator would have read that as "no documents
            # are being spared" when two were. The blast radius is the whole point
            # of a dry run, so it is queried, not sampled.
            matched_ids = await db.documents.distinct("id", query)
            logger.info("%d document(s) will be left alone:", untouched)
            async for doc in db.documents.find(
                {"building_id": building_id, "id": {"$nin": matched_ids}},
                {"_id": 0, "title": 1, "category": 1, "author_id": 1},
            ).limit(25):
                logger.info("    KEEP  [%s] %s (author_id=%s)",
                            doc.get("category"), doc.get("title"), doc.get("author_id"))

        sample = await db.documents.find(
            query, {"_id": 0, "title": 1, "unit_number": 1, "expires_at": 1}
        ).limit(3).to_list(3)
        for s in sample:
            logger.info("    %s  unit=%s expires=%s",
                        s.get("title"), s.get("unit_number"), s.get("expires_at"))

        if not args.apply:
            logger.warning("DRY RUN — %d document(s) would be %s. Re-run with --apply.",
                           matched, "reverted" if args.revert else "tagged author_id='system'")
            return 0

        update = ({"$unset": {"author_id": ""}} if args.revert
                  else {"$set": {"author_id": SYSTEM_AUTHOR}})
        result = await db.documents.update_many(query, update)
        logger.info("APPLIED — modified %d document(s).", result.modified_count)

        remaining = await db.documents.count_documents(
            selection_filter(building_id, tagged=args.revert)
        )
        if remaining:
            logger.error("%d document(s) still match after the write — investigate.", remaining)
            return 1

        if not args.revert:
            reapable = await db.documents.count_documents({
                "building_id": building_id,
                "author_id": SYSTEM_AUTHOR,
                "expires_at": {"$exists": True},
            })
            logger.info(
                "%d document(s) are now reapable by cron_notification_cleanup, "
                "each once past its own expires_at. Nothing was deleted.", reapable)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
