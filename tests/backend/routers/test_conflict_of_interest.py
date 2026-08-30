"""Tests for routers/conflict_of_interest.py — GAP-GOV-007."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId


class _AsyncIter:
    def __init__(self, items):
        self._it = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


BUILDING_A = "16244"
BUILDING_B = "13195"

_MANAGER = {"id": "mgr-1", "role": "strata_manager", "full_name": "Test Manager"}
_EC = {"id": "ec-1", "role": "ec_member", "full_name": "EC Member"}
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}

_OID = ObjectId()
_DECL = {
    "_id": _OID,
    "building_id": BUILDING_A,
    "member_id": "ec-1",
    "member_name": "EC Member",
    "conflict_type": "financial",
    "description": "I own shares in the contractor company being engaged.",
    "related_matter": "Motion 3 — Approve plumbing contract",
    "resolution": "abstained",
    "meeting_date": "2026-04-23",
    "notes": None,
    "declared_at": "2026-04-23T00:00:00+00:00",
    "created_at": "2026-04-23T00:00:00+00:00",
    "updated_at": "2026-04-23T00:00:00+00:00",
    "retracted": False,
}


def _make_db(find_one_doc=None, count=0, list_docs=None):
    db = MagicMock()
    db.conflict_of_interest.find_one = AsyncMock(return_value=find_one_doc)
    db.conflict_of_interest.insert_one = AsyncMock(return_value=MagicMock(inserted_id=_OID))
    db.conflict_of_interest.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.conflict_of_interest.count_documents = AsyncMock(return_value=count)

    cursor = MagicMock()
    cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])
    db.conflict_of_interest.find = MagicMock(return_value=MagicMock(
        sort=MagicMock(return_value=MagicMock(
            skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=cursor)))
        ))
    ))
    return db


@pytest.mark.asyncio
async def test_declare_conflict_creates_document():
    from routers.conflict_of_interest import declare_conflict, ConflictCreate
    db = _make_db()
    payload = ConflictCreate(
        conflict_type="financial",
        description="I own shares in the contractor being hired.",
        resolution="abstained",
    )
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest.create_audit_log", new_callable=AsyncMock):
        result = await declare_conflict(payload=payload, building_id=BUILDING_A, current_user=_EC)
    assert result["building_id"] == BUILDING_A
    assert result["conflict_type"] == "financial"
    assert result["retracted"] is False
    db.conflict_of_interest.insert_one.assert_called_once()
    inserted = db.conflict_of_interest.insert_one.call_args[0][0]
    assert inserted["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_declare_conflict_rejects_invalid_type():
    from routers.conflict_of_interest import declare_conflict, ConflictCreate
    from fastapi import HTTPException
    db = _make_db()
    payload = ConflictCreate(
        conflict_type="invalid_type",
        description="Some description long enough here.",
        resolution="abstained",
    )
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest.create_audit_log", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc:
            await declare_conflict(payload=payload, building_id=BUILDING_A, current_user=_EC)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_owner_cannot_declare_conflict():
    from routers.conflict_of_interest import declare_conflict, ConflictCreate
    from fastapi import HTTPException
    payload = ConflictCreate(
        conflict_type="financial",
        description="Some description long enough here.",
        resolution="pending",
    )
    with pytest.raises(HTTPException) as exc:
        await declare_conflict(payload=payload, building_id=BUILDING_A, current_user=_OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_conflicts_scoped_to_building():
    from routers.conflict_of_interest import list_conflicts
    db = _make_db(count=0, list_docs=[])
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest._require_ec", return_value=_MANAGER):
        await list_conflicts(
            member_id=None, conflict_type=None, include_retracted=False,
            page=1, page_size=20, building_id=BUILDING_A, current_user=_MANAGER,
        )
    call_args = db.conflict_of_interest.count_documents.call_args[0][0]
    assert call_args["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_list_conflicts_excludes_retracted_by_default():
    from routers.conflict_of_interest import list_conflicts
    db = _make_db(count=0, list_docs=[])
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest._require_ec", return_value=_MANAGER):
        await list_conflicts(
            member_id=None, conflict_type=None, include_retracted=False,
            page=1, page_size=20, building_id=BUILDING_A, current_user=_MANAGER,
        )
    query = db.conflict_of_interest.count_documents.call_args[0][0]
    assert query.get("retracted") == {"$ne": True}


@pytest.mark.asyncio
async def test_get_conflict_not_found_raises_404():
    from routers.conflict_of_interest import get_conflict
    from fastapi import HTTPException
    db = _make_db(find_one_doc=None)
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest._require_ec", return_value=_MANAGER):
        with pytest.raises(HTTPException) as exc:
            await get_conflict(declaration_id=str(_OID), building_id=BUILDING_A, current_user=_MANAGER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_conflict_requires_manager():
    from routers.conflict_of_interest import update_conflict, ConflictUpdate
    from fastapi import HTTPException
    payload = ConflictUpdate(resolution="abstained")
    with pytest.raises(HTTPException) as exc:
        await update_conflict(
            declaration_id=str(_OID), payload=payload,
            building_id=BUILDING_A, current_user=_OWNER,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_retract_conflict_soft_deletes():
    from routers.conflict_of_interest import retract_conflict
    db = _make_db(find_one_doc=_DECL)
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest.create_audit_log", new_callable=AsyncMock):
        result = await retract_conflict(
            declaration_id=str(_OID), building_id=BUILDING_A, current_user=_MANAGER
        )
    assert "retracted" in result["message"].lower()
    update_call = db.conflict_of_interest.update_one.call_args[0][1]
    assert update_call["$set"]["retracted"] is True


@pytest.mark.asyncio
async def test_cross_building_isolation():
    """A declaration in building A must not be reachable from building B."""
    from routers.conflict_of_interest import get_conflict
    from fastapi import HTTPException
    db = _make_db(find_one_doc=None)  # returns None for wrong building
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest._require_ec", return_value=_MANAGER):
        with pytest.raises(HTTPException) as exc:
            await get_conflict(declaration_id=str(_OID), building_id=BUILDING_B, current_user=_MANAGER)
    assert exc.value.status_code == 404
    query = db.conflict_of_interest.find_one.call_args[0][0]
    assert query["building_id"] == BUILDING_B


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner_to_declare():
    """effective_role='ec_member' on a raw-owner user must be able to declare a conflict."""
    from routers.conflict_of_interest import declare_conflict, ConflictCreate
    elevated_owner = {"id": "eo-1", "role": "owner", "effective_role": "ec_member", "full_name": "Elevated Owner"}
    payload = ConflictCreate(
        conflict_type="financial",
        description="I have a financial interest in the matter.",
        related_matter="Motion 4 — Approve roofing contract",
        meeting_date="2026-04-23",
    )
    db = _make_db()
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest.create_audit_log", new_callable=AsyncMock):
        result = await declare_conflict(payload=payload, building_id=BUILDING_A, current_user=elevated_owner)
    assert result["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_effective_role_blocks_downgraded_ec_from_declare():
    """effective_role='guest' on a raw-ec_member user must be blocked from declaring."""
    from routers.conflict_of_interest import declare_conflict, ConflictCreate
    from fastapi import HTTPException
    downgraded = {"id": "dm-1", "role": "ec_member", "effective_role": "guest", "full_name": "Downgraded EC"}
    payload = ConflictCreate(
        conflict_type="financial",
        description="Test conflict.",
        related_matter="Test matter",
        meeting_date="2026-04-23",
    )
    with pytest.raises(HTTPException) as exc:
        await declare_conflict(payload=payload, building_id=BUILDING_A, current_user=downgraded)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner_to_retract():
    """effective_role='strata_manager' on a raw-owner user can retract a declaration."""
    from routers.conflict_of_interest import retract_conflict
    elevated_owner = {"id": "eo-2", "role": "owner", "effective_role": "strata_manager"}
    db = _make_db(find_one_doc=_DECL)
    with patch("routers.conflict_of_interest._get_db", return_value=db), \
            patch("routers.conflict_of_interest.create_audit_log", new_callable=AsyncMock):
        result = await retract_conflict(
            declaration_id=str(_OID), building_id=BUILDING_A, current_user=elevated_owner
        )
    assert "retracted" in result["message"].lower()


@pytest.mark.asyncio
async def test_ec_member_cannot_retract_declaration():
    """EC member (non-manager) must not be able to retract a conflict of interest declaration."""
    from routers.conflict_of_interest import retract_conflict
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await retract_conflict(
            declaration_id=str(_OID), building_id=BUILDING_A, current_user=_EC
        )
    assert exc.value.status_code == 403
