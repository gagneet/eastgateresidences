"""Tests for scripts/data_repair/dedupe_generated_levy_notices_20260825.py.

Focus is the keeper-selection ordering. The script deletes every document in a
title group except the one that sorts first, so a bug in ``_sort_key`` is a
silent wrong-document-deleted bug — it does not raise, and the counts still look
right afterwards.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bson import ObjectId

BACKEND = Path(__file__).resolve().parents[2] / "backend"
SCRIPT = BACKEND / "scripts" / "data_repair" / "dedupe_generated_levy_notices_20260825.py"

sys.path.insert(0, str(BACKEND))
_spec = importlib.util.spec_from_file_location("_dedupe_levy_notices", SCRIPT)
dedupe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dedupe)


def _oid_at(dt: datetime) -> ObjectId:
    """An ObjectId whose embedded generation_time is ``dt``."""
    return ObjectId.from_datetime(dt)


BASE = datetime(2026, 8, 18, 4, 0, 5, tzinfo=timezone.utc)


class TestSortKeyOrdering:
    def test_orders_by_created_at_earliest_first(self):
        docs = [
            {"_id": _oid_at(BASE), "created_at": "2026-08-18T22:00:27.337131+00:00"},
            {"_id": _oid_at(BASE), "created_at": "2026-08-18T04:00:05.794946+00:00"},
            {"_id": _oid_at(BASE), "created_at": "2026-08-18T04:00:05.827494+00:00"},
        ]
        docs.sort(key=dedupe._sort_key)
        assert [d["created_at"] for d in docs] == [
            "2026-08-18T04:00:05.794946+00:00",
            "2026-08-18T04:00:05.827494+00:00",
            "2026-08-18T22:00:27.337131+00:00",
        ]

    def test_separates_writes_33ms_apart(self):
        """The real East Gate case: two runs 33 milliseconds apart must not tie."""
        early = {"_id": _oid_at(BASE), "created_at": "2026-08-18T04:00:05.794946+00:00"}
        late = {"_id": _oid_at(BASE), "created_at": "2026-08-18T04:00:05.827494+00:00"}
        assert dedupe._sort_key(early) < dedupe._sort_key(late)

    def test_missing_created_at_falls_back_to_objectid_time_not_the_front(self):
        """A document with no created_at must NOT automatically become the keeper.

        The original implementation returned "" for a missing created_at, which
        sorts before every ISO date and silently promoted the undated document to
        keeper regardless of when it was actually written.
        """
        dated = {"_id": _oid_at(BASE), "created_at": "2026-08-18T04:00:05.794946+00:00"}
        undated_later = {"_id": _oid_at(BASE + timedelta(hours=18))}  # no created_at
        docs = [undated_later, dated]
        docs.sort(key=dedupe._sort_key)
        assert docs[0] is dated, "the genuinely earlier document must be the keeper"

    def test_missing_created_at_can_still_win_when_genuinely_earliest(self):
        """The fallback orders by real write time — it does not just sort undated last."""
        undated_earlier = {"_id": _oid_at(BASE - timedelta(hours=1))}  # no created_at
        dated = {"_id": _oid_at(BASE), "created_at": "2026-08-18T04:00:05.794946+00:00"}
        docs = [dated, undated_earlier]
        docs.sort(key=dedupe._sort_key)
        assert docs[0] is undated_earlier

    def test_sort_key_is_deterministic_for_identical_timestamps(self):
        """Equal created_at must still yield a stable, total order via _id."""
        a = {"_id": ObjectId(), "created_at": "2026-08-18T04:00:05.794946+00:00"}
        b = {"_id": ObjectId(), "created_at": "2026-08-18T04:00:05.794946+00:00"}
        first, second = sorted([a, b], key=dedupe._sort_key)
        assert sorted([b, a], key=dedupe._sort_key) == [first, second]


class TestSelectionFilter:
    def test_is_building_scoped(self):
        f = dedupe.selection_filter("13195")
        assert f["building_id"] == "13195"

    def test_requires_generator_attribution(self):
        """A human-uploaded document must never be selectable."""
        f = dedupe.selection_filter("13195")
        assert f["author_id"] == dedupe.SYSTEM_AUTHOR
        assert f["category"] == "finance"
        assert f["expires_at"] == {"$exists": True}
        assert f["unit_number"] == {"$exists": True}
        assert f["title"]["$regex"].startswith("^Levy Notice - ")

    def test_matches_the_backfill_scripts_definition(self):
        """The two data-repair scripts must agree on what counts as generator output."""
        spec = importlib.util.spec_from_file_location(
            "_backfill_author_id",
            BACKEND / "scripts" / "data_repair" / "backfill_generated_notice_author_id_20260824.py",
        )
        backfill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backfill)

        ours = dedupe.selection_filter("13195")
        theirs = backfill.selection_filter("13195", tagged=True)
        assert ours == theirs, "selection filters have drifted apart"


class TestRestoreValidation:
    @pytest.mark.asyncio
    async def test_rejects_malformed_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        with pytest.raises(SystemExit) as exc:
            await dedupe.restore(None, bad, apply=False)
        assert exc.value.code == 2

    @pytest.mark.asyncio
    async def test_rejects_non_list_payload(self, tmp_path):
        bad = tmp_path / "obj.json"
        bad.write_text('{"id": "abc"}')
        with pytest.raises(SystemExit) as exc:
            await dedupe.restore(None, bad, apply=False)
        assert exc.value.code == 2

    @pytest.mark.asyncio
    async def test_rejects_list_of_non_objects(self, tmp_path):
        bad = tmp_path / "scalars.json"
        bad.write_text('["a", "b"]')
        with pytest.raises(SystemExit) as exc:
            await dedupe.restore(None, bad, apply=False)
        assert exc.value.code == 2

    @pytest.mark.asyncio
    async def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            await dedupe.restore(None, tmp_path / "nope.json", apply=False)
        assert exc.value.code == 2
