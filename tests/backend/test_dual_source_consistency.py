# @featuretrace:dashboard-feed — P0-T02 parity tests for PG-first activity feed with Mongo fallback.
# Layer: test
# Data flow: get_activities() PG query path ↔ Mongo fallback path → normalized dashboard activity items.
# Related: backend/routers/analytics.py
#          tests/backend/test_dashboard_v2_analytics_regressions.py

"""Dual-source consistency tests for /analytics/activities."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers.analytics import get_activities


class _AsyncSessionContext:
    """Small helper to emulate async_session_context() contract in router code."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _normalize_for_parity(items: list[dict]) -> list[dict]:
    """Keep only canonical fields shared across PG and Mongo variants."""
    normalized = []
    for item in items:
        normalized.append(
            {
                "type": item["type"],
                "title": item["title"],
                "visibility": item.get("visibility", "all"),
                "created_at": item["created_at"].isoformat() if hasattr(item["created_at"], "isoformat") else str(item["created_at"]),
            }
        )
    return normalized


@pytest.mark.asyncio
async def test_activities_pg_and_mongo_paths_are_canonically_equivalent():
    """
    Validate parity for the same logical activity returned from:
    1. PG-first path
    2. Mongo fallback path (activities collection)
    """
    created_at = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    pg_row = MagicMock(type="announcement", title="Board Notice", created_at=created_at)

    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: [pg_row]))

    current_user = {"id": "u1", "role": "owner", "unit_number": "101"}
    building_id = "13195"

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value={"scheme_id": "sid", "tenant_id": "tid"})),
        patch("db_postgres.session.async_session_context", return_value=_AsyncSessionContext(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        pg_items = await get_activities(limit=20, offset=0, current_user=current_user, building_id=building_id)

    mongo_activity = {
        "type": "announcement",
        "title": "Board Notice",
        "created_at": created_at.isoformat(),
        "visibility": "all",
    }
    mock_db = MagicMock()
    mock_db.activities.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[mongo_activity]
    )
    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(side_effect=RuntimeError("pg unavailable"))),
        patch("routers.analytics.db", mock_db),
    ):
        mongo_items = await get_activities(limit=20, offset=0, current_user=current_user, building_id=building_id)

    assert _normalize_for_parity(pg_items) == _normalize_for_parity(mongo_items)


@pytest.mark.asyncio
async def test_activities_fallback_generates_same_canonical_shape():
    """
    If db.activities is empty, router generates fallback activities from multiple Mongo collections.
    Parity contract here is shape consistency for dashboard consumers.
    """
    now = datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc).isoformat()
    current_user = {"id": "u1", "role": "owner", "unit_number": "101"}

    mock_db = MagicMock()
    mock_db.activities.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.announcements.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[{"title": "Public Notice", "created_at": now}]
    )
    mock_db.maintenance_requests.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.documents.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.listings.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.amenity_bookings.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(side_effect=RuntimeError("pg unavailable"))),
        patch("routers.analytics.db", mock_db),
    ):
        items = await get_activities(limit=20, offset=0, current_user=current_user, building_id="13195")

    assert items, "Fallback generator should return at least one synthesized activity"
    for item in items:
        assert {"type", "title", "created_at", "visibility"} <= set(item.keys())


@pytest.mark.asyncio
async def test_activities_pg_path_marks_source_postgres():
    """PG branch must label source for observability/debugging."""
    row = MagicMock(type="announcement", title="Board Notice", created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: [row]))

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value={"scheme_id": "sid", "tenant_id": "tid"})),
        patch("db_postgres.session.async_session_context", return_value=_AsyncSessionContext(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        items = await get_activities(limit=20, offset=0, current_user={"id": "u1", "role": "owner"}, building_id="13195")

    assert items[0]["source"] == "postgres"


@pytest.mark.asyncio
async def test_activities_fallback_enforces_non_privileged_visibility_filters():
    """Non-privileged users must query fallback collections with public filters."""
    mock_db = MagicMock()
    mock_db.activities.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.announcements.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.maintenance_requests.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.documents.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.listings.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.amenity_bookings.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)

    current_user = {"id": "u1", "role": "owner", "unit_number": "101"}
    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(side_effect=RuntimeError("pg unavailable"))),
        patch("routers.analytics.db", mock_db),
    ):
        await get_activities(limit=20, offset=0, current_user=current_user, building_id="13195")

    ann_query = mock_db.announcements.find.call_args.args[0]
    doc_query = mock_db.documents.find.call_args.args[0]
    list_query = mock_db.listings.find.call_args.args[0]
    assert ann_query["is_public"] is True
    assert list_query["is_public"] is True

    # Documents no longer carry a bare `is_public: True`. That single flag was the
    # bug: documents written by the levy-notice cron store is_private/owner_id and
    # have no is_public key at all, so the flag matched nothing while the feed
    # advertised them anyway. Visibility now comes from the shared filter, which
    # this asserts by behaviour rather than by shape.
    from utils.document_visibility import build_document_visibility_filter
    assert doc_query["$and"] == [build_document_visibility_filter(current_user)]

    # Every fallback query must also exclude test-created records.
    for query in (ann_query, doc_query, list_query):
        assert query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_activities_fallback_does_not_advertise_other_owners_documents():
    """The feed must never surface a document its destination page would hide.

    East Gate's Community Feed listed "Document uploaded: Levy Notice - TH083 -
    2026-09-01" for every viewer, while /documents showed nothing — the feed had
    no visibility filter at all and the reader used a field set the writer never
    wrote. Both now share one rule, so a feed entry always resolves.
    """
    from utils.document_visibility import build_document_visibility_filter

    other_owners_notice = {
        "id": "d1", "title": "Levy Notice - TH083 - 2026-09-01",
        "unit_number": "TH083", "owner_id": "someone-else", "is_private": True,
    }
    viewer = {"id": "u1", "role": "owner", "unit_number": "101"}
    clause = build_document_visibility_filter(viewer)

    # The viewer owns neither the document nor the unit it addresses.
    assert other_owners_notice["owner_id"] != viewer["id"]
    assert other_owners_notice["unit_number"] != viewer["unit_number"]
    # ...and the emitted filter offers no branch that would match it.
    for branch in clause["$or"]:
        if "$and" in branch:
            continue  # the not-private branch; this document IS private
        key, expected = next(iter(branch.items()))
        actual = other_owners_notice.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            assert actual not in expected["$in"]
        else:
            assert actual != expected


@pytest.mark.asyncio
async def test_activities_pg_failure_uses_mongo_collection_without_crashing():
    """If PG path raises, endpoint must continue with Mongo activities data."""
    mock_db = MagicMock()
    mock_db.activities.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[{"type": "announcement", "title": "Fallback", "created_at": "2026-06-01T00:00:00+00:00", "visibility": "all"}]
    )

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(side_effect=RuntimeError("pg unavailable"))),
        patch("routers.analytics.db", mock_db),
    ):
        items = await get_activities(limit=20, offset=0, current_user={"id": "u1", "role": "owner"}, building_id="13195")

    assert len(items) == 1
    assert items[0]["title"] == "Fallback"

