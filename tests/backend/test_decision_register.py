"""
Tests for GAP-GOV-005: Decision Register (routers/decisions.py).

Covers:
- Create decision — DEC-YYYY-NNNN numbering, field validation, building_id override (BOLA guard)
- List decisions — pagination, filters (meeting_type, result, date range, tags)
- Search decisions — full-text regex across title/text/number/tags
- Get single decision — 404 for missing, 404 for wrong building
- Update decision — partial update, enum validation, role guard
- Role guards — _require_ec uses effective_role; _require_view allows owner/tenant
- Multi-tenant isolation — building 16244 data not visible to 13195 queries
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId

# ─── Helpers ──────────────────────────────────────────────────────────────────

_BATCH_OID = ObjectId("bbbbbbbbbbbbbbbbbbbbbbbb")


def _decision_doc(overrides: dict | None = None) -> dict:
    base = {
        "_id": _BATCH_OID,
        "building_id": "13195",
        "decision_number": "DEC-2026-0001",
        "meeting_type": "ec_meeting",
        "meeting_date": "2026-04-15",
        "motion_title": "Approve landscaping contract",
        "motion_text": "That the owners corporation approve the landscaping contract with GreenScape Pty Ltd for $12,000.",
        "resolution_type": "ordinary",
        "vote_for": 5,
        "vote_against": 0,
        "vote_abstain": 1,
        "result": "passed",
        "proposer": "J. Smith",
        "seconder": "M. Nguyen",
        "tags": ["finance", "maintenance"],
        "document_urls": [],
        "notes": None,
        "created_by": "u-chairman",
        "created_at": "2026-04-15T10:00:00+00:00",
        "updated_at": "2026-04-15T10:00:00+00:00",
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_db(decision: dict | None = None, decisions: list | None = None) -> MagicMock:
    db = MagicMock()

    # find_one
    db.decisions.find_one = AsyncMock(return_value=decision)

    # find — returns async iterable via MagicMock with __aiter__
    async def _aiter(docs):
        for d in docs:
            yield d

    cursor = MagicMock()
    docs_list = decisions if decisions is not None else ([] if decision is None else [decision])
    cursor.__aiter__ = MagicMock(return_value=_aiter(docs_list))
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs_list)
    db.decisions.find = MagicMock(return_value=cursor)

    db.decisions.count_documents = AsyncMock(return_value=len(docs_list))
    db.decisions.insert_one = AsyncMock(return_value=MagicMock(inserted_id=_BATCH_OID))
    db.decisions.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))

    # Counter collection for DEC numbering
    db.decisions_counter.find_one_and_update = AsyncMock(return_value={"year": 2026, "seq": 1})

    return db


def _ec_user(uid: str = "u-chairman", role: str = "ec_member") -> dict:
    return {"id": uid, "role": role, "effective_role": role, "full_name": "Chair Person", "building_id": "13195"}


def _owner_user() -> dict:
    return {"id": "u-owner", "role": "owner", "effective_role": "owner", "full_name": "Test Owner",
            "building_id": "13195"}


def _payload(**kw) -> dict:
    base = dict(
        building_id="13195",
        meeting_type="ec_meeting",
        meeting_date="2026-04-15",
        motion_title="Approve the annual fire safety inspection contract",
        motion_text="That the owners corporation approve the annual fire safety inspection for $3,500.",
        resolution_type="ordinary",
        result="passed",
        tags=["compliance"],
    )
    base.update(kw)
    return base


# ─── Create Decision ──────────────────────────────────────────────────────────

class TestCreateDecision:

    @pytest.mark.asyncio
    async def test_creates_decision_with_dec_number(self):
        db = _mock_db()
        with patch("routers.decisions._get_db", return_value=db), \
                patch("routers.decisions.create_audit_log", new_callable=AsyncMock), \
                patch("routers.decisions.asyncio") as mock_async:
            mock_async.create_task = MagicMock()
            from routers.decisions import create_decision, DecisionCreate
            result = await create_decision(
                payload=DecisionCreate(**_payload()),
                current_user=_ec_user(),
                building_id="13195",
            )
        assert result["decision_number"] == "DEC-2026-0001"
        assert result["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_building_id_is_overridden_by_server_context(self):
        """BOLA guard: payload.building_id is ignored; server building_id wins."""
        db = _mock_db()
        with patch("routers.decisions._get_db", return_value=db), \
                patch("routers.decisions.create_audit_log", new_callable=AsyncMock), \
                patch("routers.decisions.asyncio") as mock_async:
            mock_async.create_task = MagicMock()
            from routers.decisions import create_decision, DecisionCreate
            result = await create_decision(
                payload=DecisionCreate(**_payload(building_id="16244")),  # attacker spoofs building
                current_user=_ec_user(),
                building_id="13195",  # server-resolved building wins
            )
        assert result["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_owner_cannot_create_decision(self):
        """Owners cannot record decisions — EC/manager only."""
        from fastapi import HTTPException
        db = _mock_db()
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import create_decision, DecisionCreate
            with pytest.raises(HTTPException) as exc_info:
                await create_decision(
                    payload=DecisionCreate(**_payload()),
                    current_user=_owner_user(),
                    building_id="13195",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_elevated_owner_can_create_decision(self):
        """Owner temporarily elevated to ec_member passes the EC guard."""
        db = _mock_db()
        elevated = {"id": "u-elev", "role": "owner", "effective_role": "ec_member", "full_name": "Elev Owner"}
        with patch("routers.decisions._get_db", return_value=db), \
                patch("routers.decisions.create_audit_log", new_callable=AsyncMock), \
                patch("routers.decisions.asyncio") as mock_async:
            mock_async.create_task = MagicMock()
            from routers.decisions import create_decision, DecisionCreate
            result = await create_decision(
                payload=DecisionCreate(**_payload()),
                current_user=elevated,
                building_id="13195",
            )
        assert "decision_number" in result

    @pytest.mark.asyncio
    async def test_invalid_meeting_type_raises_400(self):
        """Unknown meeting_type returns 400."""
        from fastapi import HTTPException
        db = _mock_db()
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import create_decision, DecisionCreate
            with pytest.raises(HTTPException) as exc_info:
                await create_decision(
                    payload=DecisionCreate(**_payload(meeting_type="board_meeting")),
                    current_user=_ec_user(),
                    building_id="13195",
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_result_raises_400(self):
        """Unknown result value returns 400."""
        from fastapi import HTTPException
        db = _mock_db()
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import create_decision, DecisionCreate
            with pytest.raises(HTTPException) as exc_info:
                await create_decision(
                    payload=DecisionCreate(**_payload(result="withdrawn")),
                    current_user=_ec_user(),
                    building_id="13195",
                )
        assert exc_info.value.status_code == 400


# ─── List Decisions ────────────────────────────────────────────────────────────

class TestListDecisions:

    @pytest.mark.asyncio
    async def test_list_returns_paginated_decisions(self):
        docs = [_decision_doc(), _decision_doc(
            overrides={"_id": ObjectId("cccccccccccccccccccccccc"), "decision_number": "DEC-2026-0002"})]
        db = _mock_db(decisions=docs)
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import list_decisions
            result = await list_decisions(
                building_id="13195",
                meeting_type=None,
                resolution_type=None,
                result=None,
                date_from=None,
                date_to=None,
                tags=None,
                page=1,
                page_size=20,
                current_user=_owner_user(),
            )
        assert result["total"] == 2
        assert len(result["data"]) == 2
        assert result["page"] == 1

    @pytest.mark.asyncio
    async def test_list_scoped_to_building(self):
        """Query filter must include the resolved building_id."""
        db = _mock_db(decisions=[])
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import list_decisions
            await list_decisions(
                building_id="13195",
                meeting_type=None, resolution_type=None, result=None,
                date_from=None, date_to=None, tags=None,
                page=1, page_size=20,
                current_user=_owner_user(),
            )
        # count_documents must be called with building_id in the filter
        call_args = db.decisions.count_documents.call_args[0][0]
        assert call_args.get("building_id") == "13195"

    @pytest.mark.asyncio
    async def test_list_filters_by_meeting_type(self):
        db = _mock_db(decisions=[])
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import list_decisions
            await list_decisions(
                building_id="13195",
                meeting_type="agm",
                resolution_type=None, result=None,
                date_from=None, date_to=None, tags=None,
                page=1, page_size=20,
                current_user=_owner_user(),
            )
        call_args = db.decisions.count_documents.call_args[0][0]
        assert call_args.get("meeting_type") == "agm"

    @pytest.mark.asyncio
    async def test_guest_cannot_list_decisions(self):
        """Guest role is rejected by the view guard."""
        from fastapi import HTTPException
        db = _mock_db(decisions=[])
        guest = {"id": "u-guest", "role": "guest", "effective_role": "guest", "full_name": "Guest"}
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import list_decisions
            with pytest.raises(HTTPException) as exc_info:
                await list_decisions(
                    building_id="13195",
                    meeting_type=None, resolution_type=None, result=None,
                    date_from=None, date_to=None, tags=None,
                    page=1, page_size=20,
                    current_user=guest,
                )
        assert exc_info.value.status_code == 403


# ─── Get Single Decision ───────────────────────────────────────────────────────

class TestGetDecision:

    @pytest.mark.asyncio
    async def test_get_returns_decision(self):
        doc = _decision_doc()
        db = _mock_db(decision=doc)
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import get_decision
            result = await get_decision(
                decision_id=str(_BATCH_OID),
                building_id="13195",
                current_user=_owner_user(),
            )
        assert result["decision_number"] == "DEC-2026-0001"
        assert "id" in result

    @pytest.mark.asyncio
    async def test_get_404_for_missing(self):
        from fastapi import HTTPException
        db = _mock_db(decision=None)
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import get_decision
            with pytest.raises(HTTPException) as exc_info:
                await get_decision(
                    decision_id=str(_BATCH_OID),
                    building_id="13195",
                    current_user=_owner_user(),
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_not_visible_from_other_building(self):
        """Decision from building 13195 is not found when queried from 16244."""
        from fastapi import HTTPException
        db = _mock_db(decision=None)  # DB returns None because building_id mismatch
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import get_decision
            with pytest.raises(HTTPException) as exc_info:
                await get_decision(
                    decision_id=str(_BATCH_OID),
                    building_id="16244",
                    current_user=_owner_user(),
                )
        assert exc_info.value.status_code == 404


# ─── Update Decision ───────────────────────────────────────────────────────────

class TestUpdateDecision:

    @pytest.mark.asyncio
    async def test_update_partial_fields(self):
        doc = _decision_doc()
        db = _mock_db(decision=doc)
        with patch("routers.decisions._get_db", return_value=db), \
                patch("routers.decisions.create_audit_log", new_callable=AsyncMock), \
                patch("routers.decisions.asyncio") as mock_async:
            mock_async.create_task = MagicMock()
            from routers.decisions import update_decision, DecisionUpdate
            result = await update_decision(
                decision_id=str(_BATCH_OID),
                payload=DecisionUpdate(notes="Ratified at next meeting"),
                current_user=_ec_user(),
                building_id="13195",
            )
        assert "decision_id" in result
        assert "updated" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_update_rejects_invalid_resolution_type(self):
        from fastapi import HTTPException
        doc = _decision_doc()
        db = _mock_db(decision=doc)
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import update_decision, DecisionUpdate
            with pytest.raises(HTTPException) as exc_info:
                await update_decision(
                    decision_id=str(_BATCH_OID),
                    payload=DecisionUpdate(resolution_type="supermajority"),
                    current_user=_ec_user(),
                    building_id="13195",
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_404_for_missing(self):
        from fastapi import HTTPException
        db = _mock_db(decision=None)
        # update_decision uses update_one + matched_count (no prior find_one)
        db.decisions.update_one = AsyncMock(return_value=MagicMock(matched_count=0, modified_count=0))
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import update_decision, DecisionUpdate
            with pytest.raises(HTTPException) as exc_info:
                await update_decision(
                    decision_id=str(_BATCH_OID),
                    payload=DecisionUpdate(notes="note"),
                    current_user=_ec_user(),
                    building_id="13195",
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_owner_cannot_update_decision(self):
        """Owners are view-only; only EC/managers can update."""
        from fastapi import HTTPException
        doc = _decision_doc()
        db = _mock_db(decision=doc)
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import update_decision, DecisionUpdate
            with pytest.raises(HTTPException) as exc_info:
                await update_decision(
                    decision_id=str(_BATCH_OID),
                    payload=DecisionUpdate(notes="trying to change"),
                    current_user=_owner_user(),
                    building_id="13195",
                )
        assert exc_info.value.status_code == 403


# ─── Search ────────────────────────────────────────────────────────────────────

class TestSearchDecisions:

    @pytest.mark.asyncio
    async def test_search_includes_building_scope(self):
        """Search query filter must always include building_id."""
        db = _mock_db(decisions=[])
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import search_decisions
            await search_decisions(
                q="landscaping",
                building_id="13195",
                meeting_type=None, result=None, date_from=None, date_to=None,
                tags=None, page=1, page_size=20,
                current_user=_owner_user(),
            )
        call_args = db.decisions.count_documents.call_args[0][0]
        assert call_args.get("building_id") == "13195"
        assert "$or" in call_args

    @pytest.mark.asyncio
    async def test_search_returns_paginated_results(self):
        docs = [_decision_doc()]
        db = _mock_db(decisions=docs)
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import search_decisions
            result = await search_decisions(
                q="landscaping",
                building_id="13195",
                meeting_type=None, result=None, date_from=None, date_to=None,
                tags=None, page=1, page_size=20,
                current_user=_owner_user(),
            )
        assert result["total"] == 1
        assert result["query"] == "landscaping"
        assert "pages" in result


# ─── Decision Number Generation ────────────────────────────────────────────────

class TestDecisionNumbering:

    @pytest.mark.asyncio
    async def test_generates_dec_yyyy_nnnn_format(self):
        db = MagicMock()
        db.decisions_counter.find_one_and_update = AsyncMock(return_value={"year": 2026, "seq": 42})
        from routers.decisions import _generate_decision_number
        number = await _generate_decision_number(db, 2026)
        assert number == "DEC-2026-0042"

    @pytest.mark.asyncio
    async def test_pads_seq_to_four_digits(self):
        db = MagicMock()
        db.decisions_counter.find_one_and_update = AsyncMock(return_value={"year": 2026, "seq": 1})
        from routers.decisions import _generate_decision_number
        number = await _generate_decision_number(db, 2026)
        assert number == "DEC-2026-0001"


# ─── Multi-tenant isolation ────────────────────────────────────────────────────

class TestDecisionMultiTenant:

    @pytest.mark.asyncio
    async def test_create_forces_server_building_id(self):
        """payload.building_id='16244' is overridden by server building_id='13195'."""
        db = _mock_db()
        with patch("routers.decisions._get_db", return_value=db), \
                patch("routers.decisions.create_audit_log", new_callable=AsyncMock), \
                patch("routers.decisions.asyncio") as mock_async:
            mock_async.create_task = MagicMock()
            from routers.decisions import create_decision, DecisionCreate
            result = await create_decision(
                payload=DecisionCreate(**_payload(building_id="16244")),
                current_user=_ec_user(),
                building_id="13195",
            )
        inserted_doc = db.decisions.insert_one.call_args[0][0]
        assert inserted_doc["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_list_building_16244_sees_no_13195_data(self):
        """Empty result set for building 16244 when DB returns nothing for that scope."""
        db = _mock_db(decisions=[])
        with patch("routers.decisions._get_db", return_value=db):
            from routers.decisions import list_decisions
            result = await list_decisions(
                building_id="16244",
                meeting_type=None, resolution_type=None, result=None,
                date_from=None, date_to=None, tags=None,
                page=1, page_size=20,
                current_user=_owner_user(),
            )
        assert result["total"] == 0
        assert result["data"] == []
        # Verify the query is scoped to 16244, not 13195
        call_args = db.decisions.count_documents.call_args[0][0]
        assert call_args.get("building_id") == "16244"
