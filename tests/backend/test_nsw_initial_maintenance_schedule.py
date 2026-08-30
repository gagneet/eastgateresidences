# @featuretrace:nsw-initial-maintenance-schedule — Tests for GAP-JUR-NSW-005 initial maintenance schedule (building-scoped).
# Layer: test
# Data flow: tests → backend/routers/nsw_initial_maintenance_schedule.py → nsw_initial_maintenance_schedules collection (building-scoped).
# Related: backend/routers/nsw_initial_maintenance_schedule.py
#           backend/seeds/feature_toggles.py (nsw_maintenance_schedule toggle)
# Collection: nsw_initial_maintenance_schedules
"""
Tests for GAP-JUR-NSW-005: NSW Initial Maintenance Schedule.

router: backend/routers/nsw_initial_maintenance_schedule.py
collection: nsw_initial_maintenance_schedules (tenant-scoped)
toggle: nsw_maintenance_schedule
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

BUILDING_A = "13195"
BUILDING_B = "16244"


def _manager(bid: str = BUILDING_A) -> dict:
    return {"id": "u-mgr", "full_name": "Manager", "role": "strata_manager",
            "effective_role": "strata_manager", "building_id": bid}


def _ec(bid: str = BUILDING_A) -> dict:
    return {"id": "u-ec", "full_name": "EC", "role": "ec_member",
            "effective_role": "ec_member", "building_id": bid}


def _item() -> dict:
    return {
        "description": "Clean and inspect roof gutters",
        "category": "roof_and_guttering",
        "frequency": "annually",
        "estimated_annual_cost_cents": 150000,
        "responsible_party": "owners_corporation",
    }


def _ims_payload() -> dict:
    return {
        "prepared_by": "StrataOS Pty Ltd",
        "prepared_by_type": "strata_manager",
        "strata_plan_registration_date": "2020-01-15",
        "plan_number": "SP98765",
        "effective_date": "2026-01-01",
        "review_at_agm_date": "2026-11-30",
        "items": [_item(), {**_item(), "description": "Fire safety inspection", "category": "fire_safety"}],
        "notes": "Initial IMS per SSMA 2015 Schedule 3",
    }


def _make_db(active_doc=None) -> MagicMock:
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[active_doc] if active_doc else [])

    db.nsw_initial_maintenance_schedules.find = MagicMock(return_value=cursor)
    db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=active_doc)
    db.nsw_initial_maintenance_schedules.insert_one = AsyncMock(return_value=MagicMock())
    db.nsw_initial_maintenance_schedules.update_one = AsyncMock()
    db.nsw_initial_maintenance_schedules.update_many = AsyncMock()
    db.nsw_initial_maintenance_schedules.count_documents = AsyncMock(return_value=1 if active_doc else 0)
    return db


# ── Role guards ───────────────────────────────────────────────────────────────

class TestRoleGuards:
    def test_require_manager_blocks_owner(self):
        from routers.nsw_initial_maintenance_schedule import _require_manager
        with pytest.raises(HTTPException) as exc:
            _require_manager({"role": "owner", "effective_role": "owner"})
        assert exc.value.status_code == 403

    def test_require_ec_passes_elevated_owner(self):
        from routers.nsw_initial_maintenance_schedule import _require_ec
        user = {"role": "owner", "effective_role": "ec_member"}
        assert _require_ec(user) == user


# ── Create IMS ────────────────────────────────────────────────────────────────

class TestCreateIMS:
    @pytest.mark.asyncio
    async def test_creates_with_correct_fields(self):
        from routers.nsw_initial_maintenance_schedule import create_initial_maintenance_schedule, IMSCreate
        db = _make_db()
        payload = IMSCreate(**_ims_payload())
        user = _manager()

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db), \
             patch("routers.nsw_initial_maintenance_schedule.create_audit_log", new_callable=AsyncMock):
            result = await create_initial_maintenance_schedule(payload, building_id=BUILDING_A, current_user=user)

        assert result["building_id"] == BUILDING_A
        assert result["status"] == "active"
        assert result["item_count"] == 2
        assert result["prepared_by_type"] == "strata_manager"

    @pytest.mark.asyncio
    async def test_auto_computes_total_cost(self):
        from routers.nsw_initial_maintenance_schedule import create_initial_maintenance_schedule, IMSCreate
        db = _make_db()
        payload_data = _ims_payload()
        payload_data.pop("total_estimated_annual_cost_cents", None)
        payload = IMSCreate(**payload_data)
        user = _manager()

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db), \
             patch("routers.nsw_initial_maintenance_schedule.create_audit_log", new_callable=AsyncMock):
            result = await create_initial_maintenance_schedule(payload, building_id=BUILDING_A, current_user=user)

        assert result["total_estimated_annual_cost_cents"] == 300000  # 150000 * 2

    @pytest.mark.asyncio
    async def test_supersedes_previous_active_ims(self):
        from routers.nsw_initial_maintenance_schedule import create_initial_maintenance_schedule, IMSCreate
        db = _make_db()
        payload = IMSCreate(**_ims_payload())
        user = _manager()

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db), \
             patch("routers.nsw_initial_maintenance_schedule.create_audit_log", new_callable=AsyncMock):
            await create_initial_maintenance_schedule(payload, building_id=BUILDING_A, current_user=user)

        db.nsw_initial_maintenance_schedules.update_many.assert_called_once()
        update_call = db.nsw_initial_maintenance_schedules.update_many.call_args
        assert update_call[0][1]["$set"]["status"] == "superseded"

    @pytest.mark.asyncio
    async def test_multi_tenant_building_id_isolation(self):
        from routers.nsw_initial_maintenance_schedule import create_initial_maintenance_schedule, IMSCreate
        db = _make_db()
        payload = IMSCreate(**_ims_payload())
        user = _manager(bid=BUILDING_B)

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db), \
             patch("routers.nsw_initial_maintenance_schedule.create_audit_log", new_callable=AsyncMock):
            result = await create_initial_maintenance_schedule(payload, building_id=BUILDING_B, current_user=user)

        assert result["building_id"] == BUILDING_B


# ── Compliance status ─────────────────────────────────────────────────────────

class TestComplianceStatus:
    @pytest.mark.asyncio
    async def test_no_ims_returns_non_compliant(self):
        from routers.nsw_initial_maintenance_schedule import get_ims_compliance_status
        db = _make_db(active_doc=None)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=None)

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db):
            result = await get_ims_compliance_status(building_id=BUILDING_A, current_user=_ec())

        assert result["ssma_2015_schedule_3_compliant"] is False
        assert result["has_active_ims"] is False

    @pytest.mark.asyncio
    async def test_active_ims_with_past_review_date_flags_overdue(self):
        from routers.nsw_initial_maintenance_schedule import get_ims_compliance_status
        doc = {
            "id": "ims-1",
            "building_id": BUILDING_A,
            "status": "active",
            "effective_date": "2024-01-01",
            "review_at_agm_date": "2024-11-30",  # past date → overdue
            "items": [],
        }
        db = _make_db(active_doc=doc)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=doc)

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db):
            result = await get_ims_compliance_status(building_id=BUILDING_A, current_user=_ec())

        assert result["has_active_ims"] is True
        assert result["agm_review_overdue"] is True

    @pytest.mark.asyncio
    async def test_active_ims_future_review_not_overdue(self):
        from routers.nsw_initial_maintenance_schedule import get_ims_compliance_status
        doc = {
            "id": "ims-2",
            "building_id": BUILDING_A,
            "status": "active",
            "effective_date": "2026-01-01",
            "review_at_agm_date": "2030-11-30",
            "items": [],
        }
        db = _make_db(active_doc=doc)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=doc)

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db):
            result = await get_ims_compliance_status(building_id=BUILDING_A, current_user=_ec())

        assert result["agm_review_overdue"] is False


# ── Current IMS ───────────────────────────────────────────────────────────────

class TestGetCurrentIMS:
    @pytest.mark.asyncio
    async def test_returns_placeholder_when_none_exists(self):
        from routers.nsw_initial_maintenance_schedule import get_current_ims
        db = _make_db(active_doc=None)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=None)

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db):
            result = await get_current_ims(building_id=BUILDING_A, current_user=_ec())

        assert result["has_ims"] is False
        assert "message" in result

    @pytest.mark.asyncio
    async def test_returns_active_doc_when_found(self):
        from routers.nsw_initial_maintenance_schedule import get_current_ims
        doc = {"id": "ims-3", "building_id": BUILDING_A, "status": "active", "effective_date": "2026-01-01"}
        db = _make_db(active_doc=doc)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=doc)

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db):
            result = await get_current_ims(building_id=BUILDING_A, current_user=_ec())

        assert result["has_ims"] is True
        assert result["id"] == "ims-3"


# ── AGM review log ────────────────────────────────────────────────────────────

class TestAGMReview:
    @pytest.mark.asyncio
    async def test_records_review_and_pushes_to_log(self):
        from routers.nsw_initial_maintenance_schedule import record_agm_review, AGMReviewLog
        doc = {"id": "ims-4", "building_id": BUILDING_A, "status": "active"}
        db = _make_db(active_doc=doc)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=doc)
        user = _manager()

        payload = AGMReviewLog(agm_date="2026-11-15", reviewed_by="Sarah Manager", outcome="no_changes")

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db), \
             patch("routers.nsw_initial_maintenance_schedule.create_audit_log", new_callable=AsyncMock):
            result = await record_agm_review("ims-4", payload, building_id=BUILDING_A, current_user=user)

        assert result["agm_date"] == "2026-11-15"
        assert result["outcome"] == "no_changes"
        update_call = db.nsw_initial_maintenance_schedules.update_one.call_args
        assert "$push" in update_call[0][1]

    @pytest.mark.asyncio
    async def test_superseded_outcome_marks_ims_superseded(self):
        from routers.nsw_initial_maintenance_schedule import record_agm_review, AGMReviewLog
        doc = {"id": "ims-5", "building_id": BUILDING_A, "status": "active"}
        db = _make_db(active_doc=doc)
        db.nsw_initial_maintenance_schedules.find_one = AsyncMock(return_value=doc)
        user = _manager()

        payload = AGMReviewLog(
            agm_date="2026-11-15",
            reviewed_by="Sarah Manager",
            outcome="superseded_by_new",
            superseded_ims_id="ims-6",
        )

        with patch("routers.nsw_initial_maintenance_schedule._get_db", return_value=db), \
             patch("routers.nsw_initial_maintenance_schedule.create_audit_log", new_callable=AsyncMock):
            await record_agm_review("ims-5", payload, building_id=BUILDING_A, current_user=user)

        update_call = db.nsw_initial_maintenance_schedules.update_one.call_args
        assert update_call[0][1]["$set"]["status"] == "superseded"
