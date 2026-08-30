#!/usr/bin/env python3
"""Purge test-fixture rows from db.activities (the Community Feed source).

# @featuretrace:dashboard-feed — one-off cleanup of the activity feed's source collection.
# Layer: script
# Data flow: (manual run) -> db.activities (delete) (scope param: building|global).
# Related: backend/routers/analytics.py (get_activities), frontend/src/components/dashboard/premium/ActivityFeedPremium.tsx

WHY
---
The Management dashboard's Community Feed was showing test rows ("EC Private
Test", "To be edited", "Targeted Announcement", "New Resident Joined: Test
Owner"). Measured live 2026-08-28: db.activities held 8,066 documents, of which

  * 8,009 carried building_id="eastgate" — a TEST FIXTURE identifier. There is no
    such building. Real East Gate is "13195". These came from test runs that wrote
    through the real DATABASE_URL/MONGO_URL.
  * 51 more sat in the real 13195 tenant as announcement activities whose
    entity_id points at announcements that no longer exist — created and deleted
    by tests, leaving the feed row behind.

NOT ONE of them carried is_test_data, so neither the conftest sweep nor any
production query could exclude them (CLAUDE.md: "is_test_data defends nothing
unless something sets it").

DELETION RULES — deliberately structural, never title matching
--------------------------------------------------------------
Matching on "Test" in the title would delete real announcements that happen to
mention a test, and would miss test rows with innocuous titles. Both rules below
are provable from the data:

  Rule 1  building_id is not a known building.
          A row scoped to a building that does not exist cannot be shown to
          anyone and cannot become valid later.

  Rule 2  type == "announcement" AND entity_id does not resolve to a live
          announcement in the SAME building.
          The feed row is a pointer; a pointer to a deleted announcement is a
          dead tile by construction (the feed links to it and lands on nothing).

  Rule 3  the announcement ITSELF is a test fixture (db.announcements).
          Rule 2 alone was not enough: three feed rows survived it because their
          target announcement still EXISTS — and that announcement is itself test
          data ("To be edited" / body "Edit my expiry"; "Targeted Announcement" x2
          / body "For Owners only"). Deleting the feed row while leaving the
          announcement would put the same entry straight back on the next feed
          rebuild. So the root record goes, and rule 2 then reaps its pointer.

Rule 2 is applied ONLY to announcement rows, because those are the only ones whose
entity_id is guaranteed to reference a collection this script can check. Rows of
other types are left alone even if they look stale — an unverifiable guess is not
a deletion criterion.

Rule 3 matches on an EXACT (title, body) pair, never on a substring like "test".
A substring rule would delete a real notice that happens to mention a test and
would miss a fixture with an innocuous title. The three pairs were confirmed
against live data on 2026-08-28 and the operator named these entries explicitly.
East Gate's three real announcements of the same vintage (the AGM notice, the roof
repairs notice and the garage camera notice) do not match and are untouched.

Real records are never touched: rule 1 cannot match a real building, rule 2
requires the referenced announcement to be genuinely absent, and rule 3 is an
exact-match allowlist that is printed in full before anything is deleted.

USAGE
-----
    # report only (default)
    backend/venv/bin/python3 backend/scripts/data_repair/purge_test_activities_20260828.py

    # actually delete
    backend/venv/bin/python3 backend/scripts/data_repair/purge_test_activities_20260828.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

load_dotenv(os.path.join(_BACKEND, ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="perform the deletes (default is a dry run)")
    args = ap.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Known buildings, resolved from the data rather than hardcoded, so this stays
    # correct as buildings are added or archived.
    known = {b async for b in _known_building_ids(db)}
    print(f"known building_ids: {sorted(known)}")

    total = await db.activities.count_documents({})
    print(f"db.activities total: {total:,}\n")

    # ── Rule 1 — activities scoped to a building that does not exist ────────────
    orphan_building_ids = sorted(
        {bid async for bid in _distinct_building_ids(db)} - known
    )
    rule1_filter = {"building_id": {"$in": orphan_building_ids}} if orphan_building_ids else None
    rule1_count = await db.activities.count_documents(rule1_filter) if rule1_filter else 0
    print(f"RULE 1 — unknown building_id {orphan_building_ids or '(none)'}: {rule1_count:,} rows")

    # ── Rule 3 — the announcement itself is a fixture ───────────────────────────
    # Exact (title, body) pairs only. Printed in full so the operator sees exactly
    # what is going, and applied BEFORE rule 2 so the feed rows pointing at them
    # become verifiable orphans rather than needing a second rule of their own.
    TEST_ANNOUNCEMENTS = [
        ("To be edited", "Edit my expiry"),
        ("Targeted Announcement", "For Owners only"),
    ]
    ann_filter = {"$or": [{"title": t, "content": c} for t, c in TEST_ANNOUNCEMENTS]}
    ann_docs = await db.announcements.find(
        ann_filter, {"_id": 0, "id": 1, "title": 1, "content": 1, "building_id": 1, "created_at": 1}
    ).to_list(500)
    print(f"RULE 3 — test announcements in db.announcements: {len(ann_docs):,} rows")
    for a in ann_docs:
        print(f"           {a.get('building_id')} | {a.get('title')!r} | {str(a.get('content'))[:40]!r}"
              f" | {a.get('created_at')}")
    if args.apply and ann_docs:
        removed = (await db.announcements.delete_many(ann_filter)).deleted_count
        print(f"           deleted {removed} announcement(s)")

    # ── Rule 2 — announcement rows pointing at a deleted announcement ───────────
    rule2_ids: list[str] = []
    cursor = db.activities.find(
        {"type": "announcement", "building_id": {"$in": sorted(known)}},
        {"_id": 1, "entity_id": 1, "building_id": 1, "title": 1},
    )
    async for row in cursor:
        entity_id = row.get("entity_id")
        if not entity_id:
            continue  # cannot verify -> leave it alone
        exists = await db.announcements.find_one(
            {"id": entity_id, "building_id": row.get("building_id")}, {"_id": 1}
        )
        if not exists:
            rule2_ids.append(row["_id"])
    print(f"RULE 2 — announcement rows whose target is gone: {len(rule2_ids):,} rows")

    survivors = total - rule1_count - len(rule2_ids)
    print(f"\nwould delete {rule1_count + len(rule2_ids):,} of {total:,}; {survivors:,} remain")

    if not args.apply:
        print("\nDRY RUN — nothing deleted. Re-run with --apply.")
        client.close()
        return 0

    deleted = 0
    if rule1_filter:
        deleted += (await db.activities.delete_many(rule1_filter)).deleted_count
    if rule2_ids:
        deleted += (await db.activities.delete_many({"_id": {"$in": rule2_ids}})).deleted_count

    remaining = await db.activities.count_documents({})
    print(f"\nAPPLIED — deleted {deleted:,}; db.activities now holds {remaining:,}")
    client.close()
    return 0


async def _known_building_ids(db):
    """Active, non-archived buildings. Archived buildings still count as known —
    their activity rows are real history, not fixtures."""
    for bid in await db.buildings.distinct("building_id"):
        if bid:
            yield str(bid)


async def _distinct_building_ids(db):
    for bid in await db.activities.distinct("building_id"):
        yield str(bid) if bid is not None else ""


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
