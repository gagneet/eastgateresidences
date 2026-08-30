"""GET /agm must not 500 on a legacy/dirty AGM doc.

A stored db.agm doc with a null agenda/date/location/created_at (or a missing
required field) previously raised ResponseValidationError against AGMResponse
and 500'd the whole /governance/agm page — the null agenda list also surfaced
client-side as "can't access property 'length'". The handler now coalesces the
known-nullable fields to safe defaults and skips+logs any doc that still can't
be parsed, so one dirty row never takes down the list.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cursor(items):
    cur = MagicMock()
    cur.sort = MagicMock(return_value=cur)
    cur.to_list = AsyncMock(return_value=items)
    return cur


def _good_agm(id_="agm-good"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": id_, "title": "AGM 2026", "date": "2026-05-01", "location": "Hall",
        "agenda": ["Welcome"], "status": "upcoming", "motions": [], "created_at": now,
    }


# ── shared helper unit tests (models.community.coalesce_agm_response) ──────────

def test_coalesce_agm_response_fixes_null_fields():
    from models.community import coalesce_agm_response

    result = coalesce_agm_response({
        "id": "x", "title": "Legacy", "date": None, "location": None,
        "agenda": None, "motions": None, "created_at": None, "status": None,
    })
    assert result is not None
    assert result.agenda == [] and result.motions == []
    assert result.location == "" and result.date == ""
    assert result.status == "upcoming"


def test_coalesce_agm_response_returns_none_for_unparseable_doc():
    from models.community import coalesce_agm_response

    # No 'id' — cannot build a valid AGMResponse; must return None (logged), not raise.
    assert coalesce_agm_response({"title": "No ID", "agenda": []}) is None


def test_coalesce_agm_response_preserves_valid_doc():
    from models.community import coalesce_agm_response

    result = coalesce_agm_response(_good_agm("keep"))
    assert result is not None
    assert result.id == "keep"
    assert result.agenda == ["Welcome"]


@pytest.mark.asyncio
async def test_agm_list_coalesces_null_fields_and_does_not_500():
    import server

    dirty = {
        "id": "agm-dirty", "title": "Legacy AGM", "date": None, "location": None,
        "agenda": None, "motions": None, "created_at": None, "status": None,
    }
    mock_db = MagicMock()
    mock_db.agm.find = MagicMock(return_value=_cursor([dirty, _good_agm()]))

    with patch("server.db", mock_db):
        result = await server.get_agms(
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    assert len(result) == 2  # neither doc 500s the list
    by_id = {r.id: r for r in result}
    assert by_id["agm-dirty"].agenda == []   # null list coalesced
    assert by_id["agm-dirty"].motions == []
    assert by_id["agm-dirty"].location == ""
    assert by_id["agm-dirty"].status == "upcoming"


@pytest.mark.asyncio
async def test_agm_list_skips_unparseable_doc_instead_of_500():
    import server

    # Missing 'id' cannot be coalesced into a valid AGMResponse — it must be
    # skipped (logged), NOT allowed to 500 the whole endpoint.
    broken = {
        "title": "No ID", "date": "2026-01-01", "location": "X",
        "agenda": [], "motions": [], "created_at": "2026-01-01T00:00:00Z",
    }
    mock_db = MagicMock()
    mock_db.agm.find = MagicMock(return_value=_cursor([broken, _good_agm("agm-ok")]))

    with patch("server.db", mock_db):
        result = await server.get_agms(
            current_user={"id": "u1", "role": "owner"}, building_id="13195",
        )

    assert len(result) == 1
    assert result[0].id == "agm-ok"
