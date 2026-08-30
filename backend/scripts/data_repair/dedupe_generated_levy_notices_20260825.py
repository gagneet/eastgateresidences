"""
De-duplicate generated levy-notice documents (2026-08-25)
=========================================================

## The problem

`cron_payment_reminders.py` was scheduled TWICE on the host (two crontab lines for
the same job, a pre-sentinel entry outside the block `setup_cron_jobs.sh` manages).
On 2026-08-18 it fired three times and wrote a full set of per-unit levy notices
each time:

    2026-08-18T04:00:05.794946   80 docs  ┐ duplicated crontab entry,
    2026-08-18T04:00:05.827494   80 docs  ┘ firing 33 MILLISECONDS apart
    2026-08-18T22:00:27.337131   80 docs

Live confirmation (East Gate, 13195, 2026-08-25):

    documents total          : 242
      levy notices           : 240
      distinct notice titles :  80
      surplus copies         : 160

Every one of the 240 carries `expires_at = 2026-09-17`, so `cron_notification_cleanup`
would eventually reap all of them — but not for another three weeks, during which the
dashboard activity feed keeps advertising each notice three times over
("Document uploaded: Levy Notice - TH083 - 2026-09-01", ×3).

Two upstream fixes already shipped in PR #689 and are verified in place:

* the notice write is now idempotent (`uuid5` natural key + `$setOnInsert` upsert),
  so a double-fire can no longer create a second copy; and
* `setup_cron_jobs.sh` now sweeps legacy strata lines outside its markers — run on
  the host 2026-08-25, taking `cron_payment_reminders` from 2 entries to 1 and the
  strata block from 26 lines (21 unique) to 21/21.

Neither is retroactive. This script removes the copies already written.

## What it does

For each group of generated levy notices sharing a `title` within one building,
keeps the EARLIEST document by `created_at` and deletes the rest.

It is a de-duplication, not a purge: after a successful run every notice still
exists exactly once, and each surviving copy expires on its own original schedule
via the normal `cron_notification_cleanup` lifecycle. Groups that already have a
single copy are never touched.

## On hard-deleting, given the 7-year retention rule

The platform rule is that real records are never hard-deleted. These rows are not
records — they are regenerable artifacts written by a cron with a 30-day
`expires_at`, whose designed end-of-life IS a hard delete by
`cron_notification_cleanup.delete_many(...)`. This script does not shorten that
lifecycle for any notice; it only discards redundant byte-identical copies that a
scheduling fault created, keeping the canonical first write of each. Every deleted
document is written to a timestamped JSON backup first, so the operation is
reversible via --restore.

## Selection criteria (ALL must hold)

* `title` starts with "Levy Notice " — the generator's exact format
* `category == "finance"`
* `expires_at` present — proves it was written with a lifecycle
* `unit_number` present — generated notices are per-unit
* `author_id == "system"` — generator attribution (backfilled 2026-08-24)

Identical to the filter used by the author_id backfill, plus the `author_id`
requirement, so the two scripts cannot drift apart in what they consider
"generator output". A human-uploaded document matches none of these and can never
be selected. East Gate's two genuine documents fail on both title and category.

## Safety

* Dry-run by default; `--apply` required to write.
* Building-scoped: `--building` is REQUIRED. There is no "all buildings" mode.
* Never deletes the last copy: the keeper is chosen per title group and excluded
  from the delete set by `_id`, so a group of one is a no-op by construction.
* Backs up every document it deletes to
  `backend/scripts/data_repair/backups/levy_notice_dedupe_<building>_<ts>.json`
  before issuing any delete.
* Idempotent: a second run finds every group already at one copy and matches nothing.
* Reversible: `--restore <backup.json>` re-inserts the deleted documents verbatim.

Usage:

    python3 scripts/data_repair/dedupe_generated_levy_notices_20260825.py --building 13195
    python3 scripts/data_repair/dedupe_generated_levy_notices_20260825.py --building 13195 --apply
    python3 scripts/data_repair/dedupe_generated_levy_notices_20260825.py --building 13195 \
        --restore backups/levy_notice_dedupe_13195_20260825T041500Z.json --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("dedupe_levy_notices")

#: Marker the cleanup cron requires before it will purge an expired document.
SYSTEM_AUTHOR = "system"

#: Exact prefix cron_payment_reminders uses: f"Levy Notice - {unit} - {date}".
NOTICE_TITLE_PREFIX = "Levy Notice - "

BACKUP_DIR = Path(__file__).resolve().parent / "backups"


def selection_filter(building_id: str) -> dict:
    """Documents that are unambiguously generated levy notices.

    Mirrors backfill_generated_notice_author_id_20260824.selection_filter(tagged=True)
    so the two scripts share one definition of "generator output".
    """
    return {
        "building_id": building_id,
        "title": {"$regex": f"^{NOTICE_TITLE_PREFIX}"},
        "category": "finance",
        "expires_at": {"$exists": True},
        "unit_number": {"$exists": True},
        "author_id": SYSTEM_AUTHOR,
    }


def _sort_key(doc: dict):
    """Earliest-first, by ``created_at`` when present.

    When ``created_at`` is absent, falls back to the ObjectId's embedded creation
    timestamp — ``_id`` is monotonic by creation — so an undated document still
    sorts by when it was actually written.

    An earlier version returned ``""`` for a missing ``created_at``, which sorted
    such documents to the FRONT of their group and silently made them the keeper.
    Sorting them to the BACK would be no better: it would make "has no timestamp"
    a reason to delete a document. Neither end is correct, because the document's
    real position is knowable from ``_id``.
    """
    created = doc.get("created_at")
    oid = doc.get("_id")
    if not created:
        # ObjectId.generation_time is tz-aware UTC, matching created_at's shape, so
        # the two orderings interleave correctly (to one-second granularity).
        gen = getattr(oid, "generation_time", None)
        created = gen.isoformat() if gen is not None else ""
    return (str(created), str(oid))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="De-duplicate generated levy-notice documents, keeping the earliest of each title."
    )
    p.add_argument("--building", required=True, help="building_id to operate on (REQUIRED, no all-buildings mode)")
    p.add_argument("--apply", action="store_true", help="actually write; omit for a dry run")
    p.add_argument("--restore", metavar="BACKUP_JSON", help="re-insert documents from a backup file instead of deleting")
    return p.parse_args()


async def _connect():
    url, name = os.getenv("MONGO_URL"), os.getenv("DB_NAME")
    if not url or not name:
        logger.error("MONGO_URL / DB_NAME not set — is backend/.env present?")
        sys.exit(2)
    client = AsyncIOMotorClient(url)
    return client, client[name]


async def restore(db, path: Path, apply: bool) -> int:
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()
    if not path.exists():
        logger.error("backup file not found: %s", path)
        sys.exit(2)
    try:
        docs = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error("backup file is not valid JSON (%s): %s", path, exc)
        sys.exit(2)
    if not isinstance(docs, list):
        logger.error("backup file must contain a JSON list of documents, got %s: %s",
                     type(docs).__name__, path)
        sys.exit(2)
    if not all(isinstance(d, dict) for d in docs):
        logger.error("backup file contains non-object entries: %s", path)
        sys.exit(2)
    logger.info("backup contains %d document(s)", len(docs))

    missing = []
    for d in docs:
        # Scoped by building_id as well as the uuid, so this can never read across
        # tenants even if a backup file were hand-edited or mixed.
        existing = await db.documents.find_one(
            {"id": d.get("id"), "building_id": d.get("building_id")}
        )
        if not existing:
            missing.append(d)
    logger.info("%d already present, %d to re-insert", len(docs) - len(missing), len(missing))
    if not missing:
        logger.info("nothing to restore — all documents already present")
        return 0
    if not apply:
        logger.info("DRY RUN — pass --apply to re-insert")
        return len(missing)
    await db.documents.insert_many(missing)
    logger.info("restored %d document(s)", len(missing))
    return len(missing)


async def main() -> int:
    args = parse_args()
    client, db = await _connect()
    try:
        if args.restore:
            return await restore(db, Path(args.restore), args.apply)

        cursor = db.documents.find(selection_filter(args.building))
        groups: dict[str, list[dict]] = defaultdict(list)
        async for doc in cursor:
            groups[doc.get("title", "")].append(doc)

        total = sum(len(v) for v in groups.values())
        logger.info("building %s: %d generated levy notice(s) in %d distinct title group(s)",
                    args.building, total, len(groups))
        if not groups:
            logger.info("nothing matched — nothing to do")
            return 0

        doomed: list[dict] = []
        for title, docs in groups.items():
            if len(docs) < 2:
                continue
            docs.sort(key=_sort_key)
            keeper, surplus = docs[0], docs[1:]
            doomed.extend(surplus)
            logger.debug("  %s: %d copies, keeping created_at=%s", title, len(docs), keeper.get("created_at"))

        logger.info("groups already single-copy : %d", sum(1 for v in groups.values() if len(v) < 2))
        logger.info("groups with duplicates     : %d", sum(1 for v in groups.values() if len(v) > 1))
        logger.info("SURPLUS COPIES TO DELETE   : %d", len(doomed))
        logger.info("documents remaining after  : %d", total - len(doomed))

        if not doomed:
            logger.info("every group already has exactly one copy — nothing to do")
            return 0

        if not args.apply:
            logger.info("DRY RUN — no documents deleted. Pass --apply to perform the deletion.")
            for d in doomed[:5]:
                logger.info("   would delete: %s  created_at=%s  id=%s",
                            d.get("title"), d.get("created_at"), d.get("id"))
            if len(doomed) > 5:
                logger.info("   ... and %d more", len(doomed) - 5)
            return len(doomed)

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = BACKUP_DIR / f"levy_notice_dedupe_{args.building}_{ts}.json"
        serialisable = []
        for d in doomed:
            copy = dict(d)
            copy.pop("_id", None)  # let Mongo mint a fresh _id on restore; `id` is the stable key
            serialisable.append(copy)
        backup.write_text(json.dumps(serialisable, indent=2, default=str))
        logger.info("backed up %d document(s) to %s", len(serialisable), backup)

        ids = [d["_id"] for d in doomed]
        result = await db.documents.delete_many({"_id": {"$in": ids}})
        logger.info("deleted %d document(s)", result.deleted_count)

        remaining = await db.documents.count_documents(selection_filter(args.building))
        all_docs = await db.documents.count_documents({"building_id": args.building})
        logger.info("post-state: %d levy notices, %d documents total for building %s",
                    remaining, all_docs, args.building)
        return result.deleted_count
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
