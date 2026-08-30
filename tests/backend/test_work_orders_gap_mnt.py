"""Tests for GAP-MNT-004 (SWMS) and GAP-MNT-005 (before/after photos) in work_orders.py.

All tests are fully mocked — no live DB or server required.
Multi-tenant isolation is verified for BUILDING_A vs BUILDING_B.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Constants ─────────────────────────────────────────────────────────────────

BUILDING_A = "16244"
BUILDING_B = "13195"

WO_ID = "wo-test-001"

_MANAGER = {
    "id": "mgr-1",
    "role": "strata_manager",
    "full_name": "Test Manager",
}
_CHAIRMAN = {
    "id": "chair-1",
    "role": "ec_member",
    "full_name": "Test Chairman",
}
_EC = {
    "id": "ec-1",
    "role": "ec_member",
    "full_name": "EC Member",
}
_ADMIN_STAFF = {
    "id": "staff-1",
    "role": "admin_staff",
    "full_name": "Admin Staff",
}
_OWNER = {
    "id": "own-1",
    "role": "owner",
    "full_name": "Test Owner",
}


def _make_wo(status="in_progress", has_swms=False, building_id=BUILDING_A):
    return {
        "id": WO_ID,
        "building_id": building_id,
        "title": "Fix Lift",
        "status": status,
        "swms_required": True,
        "swms_document_id": "doc-swms-old" if has_swms else None,
        "before_photos": [],
        "after_photos": [],
    }


def _make_db(wo_doc=None, update_matched=1):
    db = MagicMock()
    db.work_orders.find_one = AsyncMock(return_value=wo_doc)
    db.work_orders.update_one = AsyncMock(
        return_value=MagicMock(matched_count=update_matched)
    )
    return db


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-MNT-004: PUT /work-orders/{wo_id}/swms
# ═══════════════════════════════════════════════════════════════════════════════

class TestAttachSWMS:

    @pytest.mark.asyncio
    async def test_manager_can_attach_swms(self):
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-123")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            result = await attach_swms(WO_ID, payload, _MANAGER, BUILDING_A)
        assert result["swms_document_id"] == "doc-swms-123"
        assert result["wo_id"] == WO_ID
        db.work_orders.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_chairman_can_attach_swms(self):
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-chair")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            result = await attach_swms(WO_ID, payload, _CHAIRMAN, BUILDING_A)
        assert "swms_document_id" in result

    @pytest.mark.asyncio
    async def test_ec_member_can_attach_swms(self):
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-ec")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            result = await attach_swms(WO_ID, payload, _EC, BUILDING_A)
        assert "swms_document_id" in result

    @pytest.mark.asyncio
    async def test_admin_staff_can_attach_swms(self):
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-staff")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            result = await attach_swms(WO_ID, payload, _ADMIN_STAFF, BUILDING_A)
        assert "swms_document_id" in result

    @pytest.mark.asyncio
    async def test_owner_cannot_attach_swms(self):
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        from fastapi import HTTPException
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-owner")
        with patch("routers.work_orders.db", db):
            with pytest.raises(HTTPException) as exc:
                await attach_swms(WO_ID, payload, _OWNER, BUILDING_A)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_completed_work_order_blocked(self):
        """SWMS cannot be attached after a work order is completed."""
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        from fastapi import HTTPException
        db = _make_db(wo_doc=_make_wo(status="completed"))
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-late")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            with pytest.raises(HTTPException) as exc:
                await attach_swms(WO_ID, payload, _MANAGER, BUILDING_A)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_work_order_not_found_returns_404(self):
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        from fastapi import HTTPException
        db = _make_db(wo_doc=None)
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-missing")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            with pytest.raises(HTTPException) as exc:
                await attach_swms(WO_ID, payload, _MANAGER, BUILDING_A)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_swms_query_scoped_to_building(self):
        """DB query must include building_id — no cross-building SWMS attachment."""
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-scope")
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            await attach_swms(WO_ID, payload, _MANAGER, BUILDING_A)
        find_args = db.work_orders.find_one.call_args[0][0]
        assert find_args["building_id"] == BUILDING_A
        update_args = db.work_orders.update_one.call_args[0][0]
        assert update_args["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_elevated_owner_ec_role_can_attach(self):
        """Temporarily elevated owner with effective_role=ec_member should be allowed."""
        from routers.work_orders import attach_swms
        from models.work_order import WorkOrderSWMSUpdate
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderSWMSUpdate(swms_document_id="doc-swms-elevated")
        elevated_owner = {
            "id": "elev-1",
            "role": "owner",
            "effective_role": "ec_member",
            "full_name": "Elevated Owner",
        }
        with patch("routers.work_orders.db", db), \
                patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
                patch("routers.work_orders.asyncio.create_task"):
            result = await attach_swms(WO_ID, payload, elevated_owner, BUILDING_A)
        assert "swms_document_id" in result


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-MNT-005: POST /work-orders/{wo_id}/before-photos
# ═══════════════════════════════════════════════════════════════════════════════

class TestAddBeforePhoto:

    @pytest.mark.asyncio
    async def test_manager_can_add_before_photo(self):
        from routers.work_orders import add_before_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-before-001")
        with patch("routers.work_orders.db", db):
            result = await add_before_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        assert result["document_id"] == "photo-before-001"
        assert result["wo_id"] == WO_ID

    @pytest.mark.asyncio
    async def test_owner_can_add_before_photo(self):
        """Any approved user can add photos — not just managers."""
        from routers.work_orders import add_before_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-before-owner")
        with patch("routers.work_orders.db", db):
            result = await add_before_photo(WO_ID, payload, _OWNER, BUILDING_A)
        assert result["document_id"] == "photo-before-owner"

    @pytest.mark.asyncio
    async def test_before_photo_not_found_returns_404(self):
        from routers.work_orders import add_before_photo
        from models.work_order import WorkOrderPhotoAdd
        from fastapi import HTTPException
        db = _make_db(wo_doc=None)
        payload = WorkOrderPhotoAdd(document_id="photo-before-missing")
        with patch("routers.work_orders.db", db):
            with pytest.raises(HTTPException) as exc:
                await add_before_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_before_photo_uses_add_to_set(self):
        """$addToSet prevents duplicate photo IDs."""
        from routers.work_orders import add_before_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-before-001")
        with patch("routers.work_orders.db", db):
            await add_before_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        update_doc = db.work_orders.update_one.call_args[0][1]
        assert "$addToSet" in update_doc
        assert "before_photos" in update_doc["$addToSet"]

    @pytest.mark.asyncio
    async def test_before_photo_scoped_to_building(self):
        from routers.work_orders import add_before_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-b-scope")
        with patch("routers.work_orders.db", db):
            await add_before_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        find_args = db.work_orders.find_one.call_args[0][0]
        assert find_args["building_id"] == BUILDING_A
        update_filter = db.work_orders.update_one.call_args[0][0]
        assert update_filter["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_multi_tenant_before_photo_cross_building(self):
        """A work order from BUILDING_B cannot have a before-photo added via BUILDING_A."""
        from routers.work_orders import add_before_photo
        from models.work_order import WorkOrderPhotoAdd
        from fastapi import HTTPException
        # DB returns None because building_id doesn't match
        db = _make_db(wo_doc=None)
        payload = WorkOrderPhotoAdd(document_id="photo-cross")
        with patch("routers.work_orders.db", db):
            with pytest.raises(HTTPException) as exc:
                await add_before_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-MNT-005: POST /work-orders/{wo_id}/after-photos
# ═══════════════════════════════════════════════════════════════════════════════

class TestAddAfterPhoto:

    @pytest.mark.asyncio
    async def test_manager_can_add_after_photo(self):
        from routers.work_orders import add_after_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-after-001")
        with patch("routers.work_orders.db", db):
            result = await add_after_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        assert result["document_id"] == "photo-after-001"

    @pytest.mark.asyncio
    async def test_owner_can_add_after_photo(self):
        from routers.work_orders import add_after_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-after-owner")
        with patch("routers.work_orders.db", db):
            result = await add_after_photo(WO_ID, payload, _OWNER, BUILDING_A)
        assert result["document_id"] == "photo-after-owner"

    @pytest.mark.asyncio
    async def test_after_photo_not_found_returns_404(self):
        from routers.work_orders import add_after_photo
        from models.work_order import WorkOrderPhotoAdd
        from fastapi import HTTPException
        db = _make_db(wo_doc=None)
        payload = WorkOrderPhotoAdd(document_id="photo-after-missing")
        with patch("routers.work_orders.db", db):
            with pytest.raises(HTTPException) as exc:
                await add_after_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_after_photo_uses_add_to_set(self):
        from routers.work_orders import add_after_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-after-001")
        with patch("routers.work_orders.db", db):
            await add_after_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        update_doc = db.work_orders.update_one.call_args[0][1]
        assert "$addToSet" in update_doc
        assert "after_photos" in update_doc["$addToSet"]

    @pytest.mark.asyncio
    async def test_after_photo_scoped_to_building(self):
        from routers.work_orders import add_after_photo
        from models.work_order import WorkOrderPhotoAdd
        db = _make_db(wo_doc=_make_wo())
        payload = WorkOrderPhotoAdd(document_id="photo-a-scope")
        with patch("routers.work_orders.db", db):
            await add_after_photo(WO_ID, payload, _MANAGER, BUILDING_A)
        find_args = db.work_orders.find_one.call_args[0][0]
        assert find_args["building_id"] == BUILDING_A
        update_filter = db.work_orders.update_one.call_args[0][0]
        assert update_filter["building_id"] == BUILDING_A


# ═══════════════════════════════════════════════════════════════════════════════
# Model field validation (GAP-MNT-004 + GAP-MNT-005)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkOrderModels:

    def test_swms_update_requires_document_id(self):
        from models.work_order import WorkOrderSWMSUpdate
        import pytest
        with pytest.raises(Exception):
            WorkOrderSWMSUpdate()  # missing required field

    def test_swms_update_valid(self):
        from models.work_order import WorkOrderSWMSUpdate
        m = WorkOrderSWMSUpdate(swms_document_id="doc-abc")
        assert m.swms_document_id == "doc-abc"

    def test_photo_add_requires_document_id(self):
        from models.work_order import WorkOrderPhotoAdd
        with pytest.raises(Exception):
            WorkOrderPhotoAdd()

    def test_photo_add_valid(self):
        from models.work_order import WorkOrderPhotoAdd
        m = WorkOrderPhotoAdd(document_id="photo-xyz")
        assert m.document_id == "photo-xyz"

    def test_work_order_base_defaults(self):
        """New work order fields should default to safe values."""
        from models.work_order import WorkOrderCreate
        wo = WorkOrderCreate(
            title="Fix Roof",
            description="Leak above Unit 5",
            priority="high",
            category="building",
            building_id=BUILDING_A,
            supplier_type="contractor",
            maintenance_request_id="req-001",
        )
        assert wo.swms_required is False
        assert wo.swms_document_id is None
        assert wo.before_photos == []
        assert wo.after_photos == []
