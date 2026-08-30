"""Tests for routers/pool_safety.py — GAP-COM-005.

Pool Safety Inspection Register.
All tests use mock DB — no live connection required.
Multi-tenant isolation asserted for buildings BUILDING_A and BUILDING_B.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId

# ── Constants ─────────────────────────────────────────────────────────────────

BUILDING_A = "16244"  # Sierra demo
BUILDING_B = "13195"  # East Gate production

_MANAGER = {"id": "mgr-1", "role": "strata_manager", "full_name": "Test Manager"}
_CHAIRMAN = {"id": "chair-1", "role": "ec_member", "full_name": "Test Chairman"}
_EC = {"id": "ec-1", "role": "ec_member", "full_name": "EC Member"}
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}
_GUEST = {"id": "g-1", "role": "guest", "full_name": "Guest"}

_OID = ObjectId()
_OID_B = ObjectId()  # different building

_INSP_A = {
    "_id": _OID,
    "building_id": BUILDING_A,
    "pool_name": "Main Pool",
    "pool_type": "swimming_pool",
    "inspection_date": "2026-04-01",
    "inspector_name": "Jane Smith",
    "inspector_company": "Pool Safe Pty Ltd",
    "licence_number": "POOL-999",
    "result": "pass",
    "defects_found": None,
    "rectification_due": None,
    "certificate_url": "https://example.com/cert.pdf",
    "next_due": "2026-10-01",
    "notes": None,
    "created_by": "mgr-1",
    "created_at": "2026-04-01T00:00:00+00:00",
    "updated_at": "2026-04-01T00:00:00+00:00",
}


# ── Async iteration helper ─────────────────────────────────────────────────────

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


# ── DB factory ────────────────────────────────────────────────────────────────

def _make_db(find_one_doc=None, count=0, list_docs=None, update_matched=1, insert_id=None):
    db = MagicMock()
    _oid = insert_id or _OID
    db.pool_safety_inspections.find_one = AsyncMock(return_value=find_one_doc)
    db.pool_safety_inspections.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id=_oid)
    )
    db.pool_safety_inspections.update_one = AsyncMock(
        return_value=MagicMock(matched_count=update_matched)
    )
    db.pool_safety_inspections.count_documents = AsyncMock(return_value=count)

    cursor = MagicMock()
    cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])

    paginated = MagicMock(
        sort=MagicMock(return_value=MagicMock(
            skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=cursor)))
        ))
    )
    overdue_cursor = MagicMock()
    overdue_cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])
    overdue_sort = MagicMock(return_value=overdue_cursor)

    def _find_side_effect(query, *a, **kw):
        # overdue endpoint calls .sort(); list endpoint chains .sort().skip().limit()
        result = MagicMock()
        result.sort = MagicMock(side_effect=lambda *a, **kw: (
            overdue_cursor if "next_due" in query else MagicMock(
                skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=cursor)))
            )
        ))
        return result

    db.pool_safety_inspections.find = MagicMock(side_effect=_find_side_effect)
    return db


# ═══════════════════════════════════════════════════════════════════════════════
# POST /compliance/pool-safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogPoolInspection:

    @pytest.mark.asyncio
    async def test_manager_can_create_inspection(self):
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Main Pool",
            inspection_date="2026-04-01",
            inspector_name="Jane Smith",
            result="pass",
            interval_months=6,
        )
        with patch("routers.pool_safety._get_db", return_value=db), \
                patch("routers.pool_safety.create_audit_log", new_callable=AsyncMock), \
                patch("routers.pool_safety.asyncio.create_task"):
            result = await log_pool_inspection(payload, BUILDING_A, _MANAGER)

        assert result["pool_name"] == "Main Pool"
        assert result["building_id"] == BUILDING_A
        # next_due auto-calculated from interval_months
        assert result["next_due"] == "2026-10-01"
        db.pool_safety_inspections.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_chairman_can_create(self):
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Spa",
            pool_type="spa",
            inspection_date="2026-04-01",
            inspector_name="Bob",
            result="advisory",
        )
        with patch("routers.pool_safety._get_db", return_value=db), \
                patch("routers.pool_safety.create_audit_log", new_callable=AsyncMock), \
                patch("routers.pool_safety.asyncio.create_task"):
            result = await log_pool_inspection(payload, BUILDING_A, _CHAIRMAN)
        assert result["pool_type"] == "spa"

    @pytest.mark.asyncio
    async def test_owner_cannot_create_inspection(self):
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Pool", inspection_date="2026-04-01",
            inspector_name="Jo", result="pass",
        )
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await log_pool_inspection(payload, BUILDING_A, _OWNER)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_cannot_create_inspection(self):
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Pool", inspection_date="2026-04-01",
            inspector_name="Jo", result="pass",
        )
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await log_pool_inspection(payload, BUILDING_A, _GUEST)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_pool_type_rejected(self):
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Main",
            pool_type="jacuzzi",  # invalid
            inspection_date="2026-04-01",
            inspector_name="Jo",
            result="pass",
        )
        with patch("routers.pool_safety._get_db", return_value=db), \
                patch("routers.pool_safety.create_audit_log", new_callable=AsyncMock), \
                patch("routers.pool_safety.asyncio.create_task"):
            with pytest.raises(HTTPException) as exc:
                await log_pool_inspection(payload, BUILDING_A, _MANAGER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_result_rejected(self):
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Main",
            inspection_date="2026-04-01",
            inspector_name="Jo",
            result="excellent",  # invalid
        )
        with patch("routers.pool_safety._get_db", return_value=db), \
                patch("routers.pool_safety.create_audit_log", new_callable=AsyncMock), \
                patch("routers.pool_safety.asyncio.create_task"):
            with pytest.raises(HTTPException) as exc:
                await log_pool_inspection(payload, BUILDING_A, _MANAGER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_next_due_explicit_beats_interval(self):
        """Explicit next_due takes precedence over computed interval_months."""
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Pool",
            inspection_date="2026-04-01",
            inspector_name="Jo",
            result="pass",
            next_due="2027-01-01",  # explicit
            interval_months=6,  # would compute 2026-10-01
        )
        with patch("routers.pool_safety._get_db", return_value=db), \
                patch("routers.pool_safety.create_audit_log", new_callable=AsyncMock), \
                patch("routers.pool_safety.asyncio.create_task"):
            result = await log_pool_inspection(payload, BUILDING_A, _MANAGER)
        assert result["next_due"] == "2027-01-01"

    @pytest.mark.asyncio
    async def test_building_id_scoped_to_requesting_building(self):
        """Inspection is stored under the requesting building, not another."""
        from routers.pool_safety import log_pool_inspection, PoolSafetyCreate
        db = _make_db()
        payload = PoolSafetyCreate(
            pool_name="Pool",
            inspection_date="2026-04-01",
            inspector_name="Jo",
            result="pass",
        )
        with patch("routers.pool_safety._get_db", return_value=db), \
                patch("routers.pool_safety.create_audit_log", new_callable=AsyncMock), \
                patch("routers.pool_safety.asyncio.create_task"):
            result = await log_pool_inspection(payload, BUILDING_B, _MANAGER)
        assert result["building_id"] == BUILDING_B
        inserted_doc = db.pool_safety_inspections.insert_one.call_args[0][0]
        assert inserted_doc["building_id"] == BUILDING_B


# ═══════════════════════════════════════════════════════════════════════════════
# GET /compliance/pool-safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestListPoolInspections:

    @pytest.mark.asyncio
    async def test_manager_can_list(self):
        from routers.pool_safety import list_pool_inspections
        doc = dict(_INSP_A)
        db = _make_db(count=1, list_docs=[doc])
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await list_pool_inspections(
                pool_name=None, result=None, date_from=None, date_to=None,
                page=1, page_size=20,
                building_id=BUILDING_A, current_user=_MANAGER,
            )
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_owner_can_list(self):
        from routers.pool_safety import list_pool_inspections
        db = _make_db(count=0, list_docs=[])
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await list_pool_inspections(
                pool_name=None, result=None, date_from=None, date_to=None,
                page=1, page_size=20,
                building_id=BUILDING_A, current_user=_OWNER,
            )
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_guest_cannot_list(self):
        from routers.pool_safety import list_pool_inspections
        from fastapi import HTTPException
        db = _make_db()
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await list_pool_inspections(
                    pool_name=None, result=None, date_from=None, date_to=None,
                    page=1, page_size=20,
                    building_id=BUILDING_A, current_user=_GUEST,
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        """Query filter must include building_id so building B data can't leak to building A."""
        from routers.pool_safety import list_pool_inspections
        db = _make_db(count=0, list_docs=[])
        with patch("routers.pool_safety._get_db", return_value=db):
            await list_pool_inspections(
                pool_name=None, result=None, date_from=None, date_to=None,
                page=1, page_size=20,
                building_id=BUILDING_A, current_user=_MANAGER,
            )
        call_args = db.pool_safety_inspections.find.call_args
        query = call_args[0][0]
        assert query["building_id"] == BUILDING_A


# ═══════════════════════════════════════════════════════════════════════════════
# GET /compliance/pool-safety/{inspection_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetPoolInspection:

    @pytest.mark.asyncio
    async def test_returns_inspection_by_id(self):
        from routers.pool_safety import get_pool_inspection
        doc = dict(_INSP_A)
        db = _make_db(find_one_doc=doc)
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await get_pool_inspection(str(_OID), BUILDING_A, _MANAGER)
        assert result["pool_name"] == "Main Pool"
        assert "id" in result
        assert "_id" not in result

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self):
        from routers.pool_safety import get_pool_inspection
        from fastapi import HTTPException
        db = _make_db(find_one_doc=None)
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_pool_inspection(str(_OID), BUILDING_A, _MANAGER)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_id_returns_400(self):
        from routers.pool_safety import get_pool_inspection
        from fastapi import HTTPException
        db = _make_db()
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_pool_inspection("not-a-valid-oid", BUILDING_A, _MANAGER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_cross_building_access_blocked(self):
        """Inspection from building B must not be accessible via building A."""
        from routers.pool_safety import get_pool_inspection
        from fastapi import HTTPException
        # DB returns None because building_id filter doesn't match
        db = _make_db(find_one_doc=None)
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_pool_inspection(str(_OID_B), BUILDING_A, _MANAGER)
        assert exc.value.status_code == 404
        # Confirm building_id was passed in the query
        call_args = db.pool_safety_inspections.find_one.call_args
        assert call_args[0][0]["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_owner_can_view(self):
        from routers.pool_safety import get_pool_inspection
        doc = dict(_INSP_A)
        db = _make_db(find_one_doc=doc)
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await get_pool_inspection(str(_OID), BUILDING_A, _OWNER)
        assert result["pool_name"] == "Main Pool"

    @pytest.mark.asyncio
    async def test_guest_cannot_view(self):
        from routers.pool_safety import get_pool_inspection
        from fastapi import HTTPException
        db = _make_db(find_one_doc=dict(_INSP_A))
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_pool_inspection(str(_OID), BUILDING_A, _GUEST)
        assert exc.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# PUT /compliance/pool-safety/{inspection_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdatePoolInspection:

    @pytest.mark.asyncio
    async def test_manager_can_update(self):
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        db = _make_db(update_matched=1)
        payload = PoolSafetyUpdate(result="pass", certificate_url="https://example.com/c.pdf")
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await update_pool_inspection(str(_OID), payload, BUILDING_A, _MANAGER)
        assert result["message"] == "Inspection updated."
        db.pool_safety_inspections.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_owner_cannot_update(self):
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyUpdate(result="pass")
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await update_pool_inspection(str(_OID), payload, BUILDING_A, _OWNER)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_payload_returns_400(self):
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyUpdate()  # all None
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await update_pool_inspection(str(_OID), payload, BUILDING_A, _MANAGER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_not_found_returns_404(self):
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        from fastapi import HTTPException
        db = _make_db(update_matched=0)
        payload = PoolSafetyUpdate(result="pass")
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await update_pool_inspection(str(_OID), payload, BUILDING_A, _MANAGER)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_result_on_update_rejected(self):
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        from fastapi import HTTPException
        db = _make_db()
        payload = PoolSafetyUpdate(result="amazing")  # invalid
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await update_pool_inspection(str(_OID), payload, BUILDING_A, _MANAGER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_scoped_to_building(self):
        """Update filter must contain building_id so cross-building writes are impossible."""
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        db = _make_db(update_matched=1)
        payload = PoolSafetyUpdate(result="pass")
        with patch("routers.pool_safety._get_db", return_value=db):
            await update_pool_inspection(str(_OID), payload, BUILDING_A, _MANAGER)
        filter_doc = db.pool_safety_inspections.update_one.call_args[0][0]
        assert filter_doc["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_ec_member_can_update(self):
        from routers.pool_safety import update_pool_inspection, PoolSafetyUpdate
        db = _make_db(update_matched=1)
        payload = PoolSafetyUpdate(notes="Checked and confirmed.")
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await update_pool_inspection(str(_OID), payload, BUILDING_A, _EC)
        assert result["message"] == "Inspection updated."


# ═══════════════════════════════════════════════════════════════════════════════
# GET /compliance/pool-safety/overdue
# ═══════════════════════════════════════════════════════════════════════════════

class TestListOverduePoolInspections:

    @pytest.mark.asyncio
    async def test_returns_overdue_entries(self):
        from routers.pool_safety import list_overdue_pool_inspections
        overdue_doc = {**dict(_INSP_A), "next_due": "2025-01-01", "result": "fail"}
        db = _make_db(list_docs=[overdue_doc])
        with patch("routers.pool_safety._get_db", return_value=db):
            result = await list_overdue_pool_inspections(BUILDING_A, _MANAGER)
        assert "data" in result
        assert "as_of" in result

    @pytest.mark.asyncio
    async def test_overdue_query_includes_building_filter(self):
        from routers.pool_safety import list_overdue_pool_inspections
        db = _make_db(list_docs=[])
        with patch("routers.pool_safety._get_db", return_value=db):
            await list_overdue_pool_inspections(BUILDING_A, _MANAGER)
        call_query = db.pool_safety_inspections.find.call_args[0][0]
        assert call_query["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_guest_cannot_view_overdue(self):
        from routers.pool_safety import list_overdue_pool_inspections
        from fastapi import HTTPException
        db = _make_db()
        with patch("routers.pool_safety._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await list_overdue_pool_inspections(BUILDING_A, _GUEST)
        assert exc.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# _require_manager / _require_view role coverage
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoleGuards:

    def test_require_manager_admin_staff(self):
        from routers.pool_safety import _require_manager
        from fastapi import HTTPException
        admin_staff = {"id": "a", "role": "admin_staff"}
        # Should not raise
        _require_manager(admin_staff)

    def test_require_manager_rejects_tenant(self):
        from routers.pool_safety import _require_manager
        from fastapi import HTTPException
        tenant = {"id": "t", "role": "tenant"}
        with pytest.raises(HTTPException) as exc:
            _require_manager(tenant)
        assert exc.value.status_code == 403

    def test_require_view_allows_tenant(self):
        from routers.pool_safety import _require_view
        tenant = {"id": "t", "role": "tenant"}
        _require_view(tenant)  # should not raise

    def test_require_view_rejects_guest(self):
        from routers.pool_safety import _require_view
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_view(_GUEST)
        assert exc.value.status_code == 403

    def test_effective_role_used_for_elevated_users(self):
        """Temporarily elevated owner gets effective_role checked, not raw role."""
        from routers.pool_safety import _require_manager
        elevated_owner = {
            "id": "eo-1",
            "role": "owner",  # raw role
            "effective_role": "ec_member",  # elevated
        }
        # Should not raise — ec_member is in the allowed set
        _require_manager(elevated_owner)

    def test_effective_role_raw_owner_blocked(self):
        """Plain owner (no elevation) is still blocked from manager endpoints."""
        from routers.pool_safety import _require_manager
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _require_manager(_OWNER)
