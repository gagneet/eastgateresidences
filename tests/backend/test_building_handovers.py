"""Tests for manager-to-manager handover workflow — Gap 4.2"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _manager():
    return {"id": "u1", "full_name": "Manager", "role": "strata_manager",
            "effective_role": "strata_manager", "is_approved": True}


def _ec_member():
    return {"id": "u3", "role": "ec_member", "effective_role": "ec_member", "is_approved": True}


def _handover_doc(status="initiated"):
    from models.building_handovers import DEFAULT_CHECKLIST
    checklist = [{"completed": False, **item} for item in DEFAULT_CHECKLIST]
    return {
        "id": "h1", "building_id": "13195",
        "outgoing_manager_name": "Old Agency",
        "incoming_manager_name": "New Agency",
        "incoming_manager_email": "new@agency.com",
        "target_date": "2026-06-30",
        "status": status,
        "checklist": checklist,
        "initiated_at": "2026-04-27T00:00:00Z",
        "initiated_by": "u1",
        "notes": None,
    }


def _mock_db(handover=None, existing_active=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    db.building_handovers.find.return_value = cursor
    db.building_handovers.find_one = AsyncMock(return_value=handover)
    db.building_handovers.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.building_handovers.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    # For duplicate-active check
    if existing_active is not None:
        db.building_handovers.find_one = AsyncMock(side_effect=[existing_active, handover])
    db.units.find.return_value = cursor
    db.annual_levies.find_one = AsyncMock(return_value=None)
    db.trust_accounts_v2.find.return_value = cursor
    db.maintenance_requests.find.return_value = cursor
    db.manager_contracts.find.return_value = cursor
    db.compliance_items.find.return_value = cursor
    db.insurance_policies.find.return_value = cursor
    return db


class TestHandoverModels:
    def test_status_constants(self):
        from models.building_handovers import HandoverStatus
        assert "initiated" in HandoverStatus.OPEN
        assert "in_progress" in HandoverStatus.OPEN
        assert "completed" not in HandoverStatus.OPEN

    def test_default_checklist_has_all_categories(self):
        from models.building_handovers import DEFAULT_CHECKLIST, ChecklistCategory
        categories = {item["category"] for item in DEFAULT_CHECKLIST}
        assert ChecklistCategory.FINANCIAL in categories
        assert ChecklistCategory.LEGAL in categories
        assert ChecklistCategory.OPERATIONAL in categories
        assert ChecklistCategory.COMMUNICATION in categories
        assert ChecklistCategory.COMPLIANCE in categories

    def test_default_checklist_has_minimum_items(self):
        from models.building_handovers import DEFAULT_CHECKLIST
        assert len(DEFAULT_CHECKLIST) >= 20

    def test_create_schema(self):
        from models.building_handovers import HandoverCreate
        h = HandoverCreate(
            outgoing_manager_name="Old Agency",
            incoming_manager_name="New Agency",
            target_date="2026-06-30",
        )
        assert h.target_date == "2026-06-30"
        assert h.contract_id is None


class TestCompletionPercentage:
    def test_zero_pct_when_nothing_done(self):
        from routers.building_handovers import _completion_pct
        checklist = [{"completed": False}, {"completed": False}]
        assert _completion_pct(checklist) == 0.0

    def test_100_pct_when_all_done(self):
        from routers.building_handovers import _completion_pct
        checklist = [{"completed": True}, {"completed": True}]
        assert _completion_pct(checklist) == 100.0

    def test_partial_completion(self):
        from routers.building_handovers import _completion_pct
        checklist = [{"completed": True}, {"completed": False}, {"completed": False}, {"completed": False}]
        assert _completion_pct(checklist) == 25.0

    def test_empty_checklist_returns_zero(self):
        from routers.building_handovers import _completion_pct
        assert _completion_pct([]) == 0.0


class TestInitiateHandover:
    @pytest.mark.asyncio
    async def test_ec_member_cannot_initiate(self):
        from routers.building_handovers import initiate_handover
        from models.building_handovers import HandoverCreate
        from fastapi import HTTPException
        payload = HandoverCreate(outgoing_manager_name="Old", incoming_manager_name="New",
                                 target_date="2026-06-30")
        with pytest.raises(HTTPException) as exc:
            await initiate_handover(payload, building_id="13195", current_user=_ec_member())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_duplicate_active_handover_rejected(self):
        from routers.building_handovers import initiate_handover
        from models.building_handovers import HandoverCreate
        from fastapi import HTTPException
        existing = {"id": "h0", "status": "in_progress"}
        db = _mock_db(existing_active=existing)
        # find_one returns existing active handover on first call
        db.building_handovers.find_one = AsyncMock(return_value=existing)
        payload = HandoverCreate(outgoing_manager_name="Old", incoming_manager_name="New",
                                 target_date="2026-06-30")
        with patch("routers.building_handovers._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await initiate_handover(payload, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_initiate_creates_full_checklist(self):
        from routers.building_handovers import initiate_handover
        from models.building_handovers import HandoverCreate, DEFAULT_CHECKLIST
        db = _mock_db(handover=None)
        db.building_handovers.find_one = AsyncMock(return_value=None)
        payload = HandoverCreate(outgoing_manager_name="Old Agency", incoming_manager_name="New Agency",
                                 target_date="2026-06-30")
        with patch("routers.building_handovers._get_db", return_value=db):
            result = await initiate_handover(payload, building_id="13195", current_user=_manager())
        assert result["status"] == "initiated"
        assert result["checklist_items"] == len(DEFAULT_CHECKLIST)
        # Verify building_id injected from auth
        inserted = db.building_handovers.insert_one.call_args[0][0]
        assert inserted["building_id"] == "13195"


class TestChecklistUpdates:
    @pytest.mark.asyncio
    async def test_tick_item_advances_to_in_progress(self):
        from routers.building_handovers import update_checklist_item
        from models.building_handovers import ChecklistItemUpdate
        doc = _handover_doc("initiated")
        db = _mock_db(handover=doc)
        payload = ChecklistItemUpdate(completed=True)
        with patch("routers.building_handovers._get_db", return_value=db):
            result = await update_checklist_item("h1", 0, payload, building_id="13195", current_user=_manager())
        update_call = db.building_handovers.update_one.call_args[0][1]
        assert update_call["$set"]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_out_of_range_index_rejected(self):
        from routers.building_handovers import update_checklist_item
        from models.building_handovers import ChecklistItemUpdate
        from fastapi import HTTPException
        db = _mock_db(handover=_handover_doc())
        payload = ChecklistItemUpdate(completed=True)
        with patch("routers.building_handovers._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await update_checklist_item("h1", 9999, payload, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_completed_handover_cannot_be_updated(self):
        from routers.building_handovers import update_checklist_item
        from models.building_handovers import ChecklistItemUpdate
        from fastapi import HTTPException
        db = _mock_db(handover=_handover_doc("completed"))
        payload = ChecklistItemUpdate(completed=True)
        with patch("routers.building_handovers._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await update_checklist_item("h1", 0, payload, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400


class TestCompleteAndCancel:
    @pytest.mark.asyncio
    async def test_complete_warns_when_checklist_incomplete(self):
        from routers.building_handovers import complete_handover
        db = _mock_db(handover=_handover_doc("in_progress"))
        with patch("routers.building_handovers._get_db", return_value=db):
            result = await complete_handover("h1", notes=None, building_id="13195", current_user=_manager())
        assert result["warning"] is not None
        assert "0%" in result["warning"] or "%" in result["warning"]

    @pytest.mark.asyncio
    async def test_cancel_requires_manager(self):
        from routers.building_handovers import cancel_handover
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await cancel_handover("h1", reason=None, building_id="13195", current_user=_ec_member())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cancel_already_completed_rejected(self):
        from routers.building_handovers import cancel_handover
        from fastapi import HTTPException
        db = _mock_db(handover=_handover_doc("completed"))
        with patch("routers.building_handovers._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await cancel_handover("h1", reason=None, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400


class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_handover_list_scoped_to_building(self):
        from routers.building_handovers import list_handovers
        db = _mock_db()
        with patch("routers.building_handovers._get_db", return_value=db):
            await list_handovers(building_id="16244", current_user=_manager(), status=None)
        call_args = db.building_handovers.find.call_args[0][0]
        assert call_args["building_id"] == "16244"
