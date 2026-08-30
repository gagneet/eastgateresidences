"""Tests for routers/essential_services.py — GAP-COM-008."""
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
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}
_GUEST = {"id": "g-1", "role": "guest", "full_name": "Guest"}

_OID = ObjectId()
_ENTRY = {
    "_id": _OID,
    "building_id": BUILDING_A,
    "service_type": "lift",
    "asset_id": None,
    "asset_name": "Main Passenger Lift",
    "service_date": "2026-04-01",
    "technician": "KONE Technician",
    "company": "KONE Elevators",
    "licence_number": "LIC-9999",
    "status": "compliant",
    "certificate_url": None,
    "next_due": "2026-10-01",
    "notes": None,
    "tags": [],
    "created_by": "mgr-1",
    "created_at": "2026-04-01T00:00:00+00:00",
    "updated_at": "2026-04-01T00:00:00+00:00",
}


def _make_db(find_one_doc=None, count=0, list_docs=None, update_matched=1):
    db = MagicMock()
    db.essential_services_log.find_one = AsyncMock(return_value=find_one_doc)
    db.essential_services_log.insert_one = AsyncMock(return_value=MagicMock(inserted_id=_OID))
    db.essential_services_log.update_one = AsyncMock(return_value=MagicMock(matched_count=update_matched))
    db.essential_services_log.count_documents = AsyncMock(return_value=count)

    cursor = MagicMock()
    cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])
    paginated = MagicMock(
        sort=MagicMock(return_value=MagicMock(
            skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=cursor)))
        ))
    )
    overdue_cursor = MagicMock()
    overdue_cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])
    db.essential_services_log.find = MagicMock(side_effect=lambda q, *a, **kw: (
        paginated if "skip" not in str(q) else MagicMock(sort=MagicMock(return_value=overdue_cursor))
    ))

    return db


def _paginated_db(list_docs=None, count=0):
    db = MagicMock()
    db.essential_services_log.find_one = AsyncMock(return_value=None)
    db.essential_services_log.insert_one = AsyncMock(return_value=MagicMock(inserted_id=_OID))
    db.essential_services_log.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.essential_services_log.count_documents = AsyncMock(return_value=count)

    cursor = MagicMock()
    cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])
    db.essential_services_log.find = MagicMock(return_value=MagicMock(
        sort=MagicMock(return_value=MagicMock(
            skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=cursor)))
        ))
    ))
    return db


@pytest.mark.asyncio
async def test_log_service_event_creates_doc():
    from routers.essential_services import log_service_event, EssentialServiceCreate
    db = _paginated_db()
    payload = EssentialServiceCreate(
        service_type="lift",
        asset_name="Main Passenger Lift",
        service_date="2026-04-01",
        technician="KONE Technician",
        status="compliant",
        interval_months=6,
    )
    with patch("routers.essential_services._get_db", return_value=db), \
            patch("routers.essential_services.create_audit_log", new_callable=AsyncMock):
        result = await log_service_event(payload=payload, building_id=BUILDING_A, current_user=_MANAGER)
    assert result["building_id"] == BUILDING_A
    assert result["service_type"] == "lift"
    # auto-calculated next_due: 2026-04-01 + 6 months = 2026-10-01
    assert result["next_due"] == "2026-10-01"


@pytest.mark.asyncio
async def test_log_service_event_rejects_invalid_type():
    from routers.essential_services import log_service_event, EssentialServiceCreate
    from fastapi import HTTPException
    db = _paginated_db()
    payload = EssentialServiceCreate(
        service_type="jacuzzi",
        asset_name="Spa",
        service_date="2026-04-01",
        technician="Tech",
    )
    with patch("routers.essential_services._get_db", return_value=db), \
            patch("routers.essential_services.create_audit_log", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc:
            await log_service_event(payload=payload, building_id=BUILDING_A, current_user=_MANAGER)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_guest_cannot_log_service():
    from routers.essential_services import log_service_event, EssentialServiceCreate
    from fastapi import HTTPException
    payload = EssentialServiceCreate(
        service_type="lift",
        asset_name="Lift",
        service_date="2026-04-01",
        technician="Tech",
    )
    with pytest.raises(HTTPException) as exc:
        await log_service_event(payload=payload, building_id=BUILDING_A, current_user=_GUEST)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_list_services():
    from routers.essential_services import list_service_log
    db = _paginated_db(list_docs=[], count=0)
    with patch("routers.essential_services._get_db", return_value=db):
        result = await list_service_log(
            service_type=None, status=None, date_from=None, date_to=None,
            page=1, page_size=20, building_id=BUILDING_A, current_user=_OWNER,
        )
    assert result["total"] == 0
    assert result["data"] == []


@pytest.mark.asyncio
async def test_list_services_scoped_to_building():
    from routers.essential_services import list_service_log
    db = _paginated_db(list_docs=[], count=0)
    with patch("routers.essential_services._get_db", return_value=db):
        await list_service_log(
            service_type=None, status=None, date_from=None, date_to=None,
            page=1, page_size=20, building_id=BUILDING_A, current_user=_MANAGER,
        )
    query = db.essential_services_log.count_documents.call_args[0][0]
    assert query["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_get_entry_not_found_raises_404():
    from routers.essential_services import get_service_entry
    from fastapi import HTTPException
    db = _paginated_db()
    db.essential_services_log.find_one = AsyncMock(return_value=None)
    with patch("routers.essential_services._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await get_service_entry(entry_id=str(_OID), building_id=BUILDING_A, current_user=_MANAGER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_entry_sets_updated_at():
    from routers.essential_services import update_service_entry, EssentialServiceUpdate
    db = _paginated_db()
    db.essential_services_log.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    payload = EssentialServiceUpdate(certificate_url="https://example.com/cert.pdf")
    with patch("routers.essential_services._get_db", return_value=db):
        result = await update_service_entry(
            entry_id=str(_OID), payload=payload,
            building_id=BUILDING_A, current_user=_MANAGER,
        )
    assert "updated" in result["message"].lower()
    update_doc = db.essential_services_log.update_one.call_args[0][1]["$set"]
    assert "updated_at" in update_doc
    assert update_doc["certificate_url"] == "https://example.com/cert.pdf"


@pytest.mark.asyncio
async def test_cross_building_isolation_on_get():
    """Entry for building A is not reachable when querying building B."""
    from routers.essential_services import get_service_entry
    from fastapi import HTTPException
    db = _paginated_db()
    db.essential_services_log.find_one = AsyncMock(return_value=None)
    with patch("routers.essential_services._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await get_service_entry(entry_id=str(_OID), building_id=BUILDING_B, current_user=_MANAGER)
    assert exc.value.status_code == 404
    query = db.essential_services_log.find_one.call_args[0][0]
    assert query["building_id"] == BUILDING_B


# ── helpers for overdue endpoint ─────────────────────────────────────────────

def _overdue_db(docs=None):
    """Minimal mock for the GET /overdue endpoint (find().sort() pattern)."""
    db = MagicMock()
    sort_cursor = MagicMock()
    sort_cursor.__aiter__ = lambda self=None: _AsyncIter(docs or [])
    db.essential_services_log.find = MagicMock(
        return_value=MagicMock(sort=MagicMock(return_value=sort_cursor))
    )
    return db


_OVERDUE_ENTRY = {
    "_id": ObjectId(),
    "building_id": BUILDING_A,
    "service_type": "fire_safety",
    "asset_name": "Fire Hydrant System",
    "service_date": "2025-10-01",
    "technician": "FireCo",
    "company": "FireCo Australia",
    "status": "overdue",
    "next_due": "2026-01-01",  # past
    "days_overdue": None,
}


# ── GET /overdue tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_overdue_services_returns_entries():
    """Overdue entries (next_due < today, status != compliant) are returned."""
    from routers.essential_services import list_overdue_services
    entry = dict(_OVERDUE_ENTRY)
    db = _overdue_db([entry])
    with patch("routers.essential_services._get_db", return_value=db):
        result = await list_overdue_services(building_id=BUILDING_A, current_user=_MANAGER)
    assert result["total"] == 1
    assert result["data"][0]["service_type"] == "fire_safety"
    assert "days_overdue" in result["data"][0]


@pytest.mark.asyncio
async def test_list_overdue_services_scoped_to_building():
    """Query must carry the caller's building_id — no cross-building leakage."""
    from routers.essential_services import list_overdue_services
    db = _overdue_db([])
    with patch("routers.essential_services._get_db", return_value=db):
        await list_overdue_services(building_id=BUILDING_A, current_user=_MANAGER)
    query = db.essential_services_log.find.call_args[0][0]
    assert query["building_id"] == BUILDING_A
    assert query["building_id"] != BUILDING_B


@pytest.mark.asyncio
async def test_list_overdue_excludes_compliant_entries():
    """Compliant services must never appear in the overdue list."""
    from routers.essential_services import list_overdue_services
    db = _overdue_db([])
    with patch("routers.essential_services._get_db", return_value=db):
        await list_overdue_services(building_id=BUILDING_A, current_user=_MANAGER)
    query = db.essential_services_log.find.call_args[0][0]
    assert query["status"] == {"$ne": "compliant"}


@pytest.mark.asyncio
async def test_list_overdue_days_overdue_calculated():
    """days_overdue is computed and attached to each returned document."""
    from routers.essential_services import list_overdue_services
    from datetime import date, timedelta
    past_date = (date.today() - timedelta(days=10)).isoformat()
    entry = dict(_OVERDUE_ENTRY, next_due=past_date)
    db = _overdue_db([entry])
    with patch("routers.essential_services._get_db", return_value=db):
        result = await list_overdue_services(building_id=BUILDING_A, current_user=_MANAGER)
    assert result["data"][0]["days_overdue"] >= 10


@pytest.mark.asyncio
async def test_owner_can_view_overdue_services():
    """GET /overdue uses _require_view which allows owners."""
    from routers.essential_services import list_overdue_services
    db = _overdue_db([])
    with patch("routers.essential_services._get_db", return_value=db):
        result = await list_overdue_services(building_id=BUILDING_A, current_user=_OWNER)
    assert "data" in result


@pytest.mark.asyncio
async def test_guest_cannot_view_overdue_services():
    """Guest must not access the overdue endpoint."""
    from routers.essential_services import list_overdue_services
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await list_overdue_services(building_id=BUILDING_A, current_user=_GUEST)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_cannot_log_service_event():
    """Owner role must not be able to POST a service log event (manager-only)."""
    from routers.essential_services import log_service_event, EssentialServiceCreate
    from fastapi import HTTPException
    payload = EssentialServiceCreate(
        service_type="lift", asset_name="Lift", service_date="2026-04-01", technician="Tech",
    )
    with pytest.raises(HTTPException) as exc:
        await log_service_event(payload=payload, building_id=BUILDING_A, current_user=_OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_effective_role_overrides_raw_role_for_log():
    """A user with raw role='owner' but effective_role='strata_manager' can POST a log event."""
    from routers.essential_services import log_service_event, EssentialServiceCreate
    elevated_owner = {"id": "eo-1", "role": "owner", "effective_role": "strata_manager"}
    db = _paginated_db()
    payload = EssentialServiceCreate(
        service_type="lift", asset_name="Main Lift", service_date="2026-04-01", technician="Tech",
    )
    with patch("routers.essential_services._get_db", return_value=db), \
            patch("routers.essential_services.create_audit_log", new_callable=AsyncMock):
        result = await log_service_event(payload=payload, building_id=BUILDING_A, current_user=elevated_owner)
    assert result["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_effective_role_blocks_elevated_raw_manager():
    """A user with raw role='strata_manager' but effective_role='guest' must be blocked."""
    from routers.essential_services import log_service_event, EssentialServiceCreate
    from fastapi import HTTPException
    downgraded = {"id": "dm-1", "role": "strata_manager", "effective_role": "guest"}
    payload = EssentialServiceCreate(
        service_type="lift", asset_name="Main Lift", service_date="2026-04-01", technician="Tech",
    )
    with pytest.raises(HTTPException) as exc:
        await log_service_event(payload=payload, building_id=BUILDING_A, current_user=downgraded)
    assert exc.value.status_code == 403
