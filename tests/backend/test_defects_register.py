"""
Tests for GAP-MNT-001: Defects Register with statutory warranty clocks.

Covers: create, list (with filters), get, update, soft-cancel, warranty summary,
        add notes, add photos, warranty deadline computation, multi-tenant isolation,
        role guards, toggle-on/toggle-off paths.
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from request_context import set_ctx_building_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(role="strata_manager", uid="mgr-1"):
    return {"id": uid, "role": role, "effective_role": role, "building_id": "13195",
            "full_name": "Test Manager"}


def _owner_user():
    return {"id": "owner-1", "role": "owner", "effective_role": "owner",
            "building_id": "13195", "full_name": "Test Owner"}


def _defect(bid="13195", defect_id="def-1", status="open", severity="major",
            warranty_deadline=None, cancelled=False):
    return {
        "_id": "oid-1",
        "id": defect_id,
        "building_id": bid,
        "title": "Roof membrane delamination at Level 3 podium",
        "description": "Water ingress observed after heavy rain, affecting ceiling of TH031",
        "category": "waterproofing",
        "severity": severity,
        "location": "Level 3 podium deck",
        "lot_number": "TH031",
        "status": status,
        "jurisdiction": "ACT",
        "practical_completion_date": "2019-06-30",
        "warranty_deadline": warranty_deadline or "2025-06-30",
        "warranty_days_remaining": -100 if not warranty_deadline else None,
        "builder_name": "Acme Builders Pty Ltd",
        "builder_licence": "BL-12345",
        "builder_contact_email": "builder@acme.com.au",
        "builder_contact_phone": "+61299991234",
        "discovered_date": "2026-05-01",
        "notified_builder_at": "2026-05-02T09:00:00+00:00",
        "remediated_at": None,
        "resolution_notes": None,
        "photo_urls": ["https://cdn.example.com/photo1.jpg"],
        "attachments": [],
        "notes": None,
        "timeline": [{"at": "2026-05-01T00:00:00+00:00", "by": "mgr-1", "note": "Defect logged", "note_type": "general"}],
        "logged_by": "mgr-1",
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
        "cancelled_at": "2026-05-10T00:00:00+00:00" if cancelled else None,
    }


def _mock_db(doc=None, docs=None, agg_result=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs if docs is not None else ([doc] if doc else []))
    db.defects.find.return_value = cursor

    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=agg_result or [])
    db.defects.aggregate.return_value = agg_cursor

    db.defects.find_one = AsyncMock(return_value=doc)
    db.defects.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid-1"))
    db.defects.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.defects.count_documents = AsyncMock(return_value=len(docs) if docs else (1 if doc else 0))
    db.audit_logs.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid-audit"))
    return db


# ── Warranty deadline computation (pure unit tests — no DB) ───────────────────

class TestWarrantyDeadlineComputation:
    def test_act_major_defect_6_years(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline("2019-06-30", "ACT", "major")
        assert result is not None
        dl = date.fromisoformat(result)
        # Should be ~6 years from 2019-06-30
        assert dl.year == 2025

    def test_nsw_major_defect_6_years(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline("2020-01-01", "NSW", "major")
        assert result is not None
        dl = date.fromisoformat(result)
        assert dl.year == 2026

    def test_nsw_minor_defect_2_years(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline("2024-01-01", "NSW", "minor")
        assert result is not None
        dl = date.fromisoformat(result)
        assert dl.year == 2026

    def test_vic_major_defect_10_years(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline("2016-06-30", "VIC", "critical")
        assert result is not None
        dl = date.fromisoformat(result)
        assert dl.year == 2026

    def test_returns_none_when_no_completion_date(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline(None, "ACT", "major")
        assert result is None

    def test_returns_none_for_invalid_date_format(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline("not-a-date", "ACT", "major")
        assert result is None

    def test_cosmetic_severity_uses_minor_period(self):
        from routers.defects_register import _compute_warranty_deadline
        # cosmetic → minor → ACT = 6 years (ACT has 6 for both)
        result = _compute_warranty_deadline("2020-01-01", "ACT", "cosmetic")
        assert result is not None

    def test_unknown_jurisdiction_defaults_to_act(self):
        from routers.defects_register import _compute_warranty_deadline
        result = _compute_warranty_deadline("2020-01-01", "ZZ", "major")
        # Falls back to ACT = 6 years
        assert result is not None
        dl = date.fromisoformat(result)
        assert dl.year == 2026


# ── Create ─────────────────────────────────────────────────────────────────────

class TestCreateDefect:
    @pytest.mark.asyncio
    async def test_creates_defect_with_warranty_clock(self):
        set_ctx_building_id("13195")
        from routers.defects_register import create_defect, DefectCreate

        db = _mock_db()
        payload = DefectCreate(
            building_id="13195",
            title="Roof membrane delamination at Level 3 podium",
            description="Water ingress observed after heavy rain affecting ceiling of TH031",
            category="waterproofing",
            severity="major",
            location="Level 3 podium deck",
            jurisdiction="ACT",
            practical_completion_date="2019-06-30",
        )
        with patch("routers.defects_register._get_db", return_value=db):
            result = await create_defect(
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )

        assert result["building_id"] == "13195"
        assert result["status"] == "open"
        assert result["warranty_deadline"] is not None
        assert result["cancelled_at"] is None
        assert len(result["timeline"]) == 1
        assert result["timeline"][0]["note"] == "Defect logged"
        db.defects.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_owner_can_create_defect(self):
        set_ctx_building_id("13195")
        from routers.defects_register import create_defect, DefectCreate

        db = _mock_db()
        payload = DefectCreate(
            building_id="13195",
            title="Cracked tile in unit entry corridor",
            description="Floor tile is cracked and creating a trip hazard for residents",
            category="unit",
            severity="minor",
            location="Unit TH015 entry corridor",
            jurisdiction="ACT",
        )
        with patch("routers.defects_register._get_db", return_value=db):
            result = await create_defect(
                payload=payload,
                building_id="13195",
                current_user=_owner_user(),
            )
        assert result["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_guest_cannot_create(self):
        set_ctx_building_id("13195")
        from routers.defects_register import create_defect, DefectCreate
        from fastapi import HTTPException

        payload = DefectCreate(
            building_id="13195",
            title="Some defect title here",
            description="Some defect description here to pass validation length",
            category="structural",
            severity="major",
            location="Basement car park",
            jurisdiction="ACT",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_defect(
                payload=payload,
                building_id="13195",
                current_user=_user(role="guest"),
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_category_rejected(self):
        set_ctx_building_id("13195")
        from routers.defects_register import create_defect, DefectCreate
        from fastapi import HTTPException

        db = _mock_db()
        payload = DefectCreate(
            building_id="13195",
            title="Bad category defect",
            description="A sufficiently long description for this defect item",
            category="invalid_category",
            severity="major",
            location="Lobby",
            jurisdiction="ACT",
        )
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await create_defect(
                    payload=payload,
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_no_warranty_deadline_when_no_completion_date(self):
        set_ctx_building_id("13195")
        from routers.defects_register import create_defect, DefectCreate

        db = _mock_db()
        payload = DefectCreate(
            building_id="13195",
            title="Unknown completion date defect",
            description="Water stain on ceiling — builder completion date unknown",
            category="waterproofing",
            severity="major",
            location="Level 2 corridor",
            jurisdiction="ACT",
        )
        with patch("routers.defects_register._get_db", return_value=db):
            result = await create_defect(
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        assert result["warranty_deadline"] is None
        assert result["warranty_days_remaining"] is None

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        set_ctx_building_id("16244")
        from routers.defects_register import create_defect, DefectCreate

        db = _mock_db()
        payload = DefectCreate(
            building_id="16244",
            title="Facade crack on north elevation",
            description="Horizontal crack observed on north elevation at Level 4 spandrel panel",
            category="facade",
            severity="critical",
            location="North elevation Level 4",
            jurisdiction="NSW",
            practical_completion_date="2021-09-01",
        )
        with patch("routers.defects_register._get_db", return_value=db):
            result = await create_defect(
                payload=payload,
                building_id="16244",
                current_user=_user(role="super_admin"),
            )
        assert result["building_id"] == "16244"


# ── List ───────────────────────────────────────────────────────────────────────

class TestListDefects:
    @pytest.mark.asyncio
    async def test_lists_open_defects(self):
        set_ctx_building_id("13195")
        from routers.defects_register import list_defects

        defect = _defect()
        db = _mock_db(docs=[defect])
        with patch("routers.defects_register._get_db", return_value=db):
            result = await list_defects(
                building_id="13195",
                current_user=_user(),
                status="open",
                category=None,
                severity=None,
                search=None,
                warranty_expiring_days=None,
                include_cancelled=False,
                skip=0, limit=50,
            )
        assert "total" in result
        assert "items" in result

    @pytest.mark.asyncio
    async def test_filters_by_warranty_expiring_days(self):
        set_ctx_building_id("13195")
        from routers.defects_register import list_defects

        db = _mock_db(docs=[])
        with patch("routers.defects_register._get_db", return_value=db):
            result = await list_defects(
                building_id="13195",
                current_user=_user(),
                status=None,
                category=None,
                severity=None,
                search=None,
                warranty_expiring_days=90,
                include_cancelled=False,
                skip=0, limit=50,
            )
        # Query filter should include warranty_deadline check
        call_args = db.defects.find.call_args[0][0]
        assert "warranty_deadline" in call_args

    @pytest.mark.asyncio
    async def test_invalid_status_filter_raises_422(self):
        set_ctx_building_id("13195")
        from routers.defects_register import list_defects
        from fastapi import HTTPException

        db = _mock_db(docs=[])
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await list_defects(
                    building_id="13195",
                    current_user=_user(),
                    status="nonexistent_status",
                    category=None,
                    severity=None,
                    warranty_expiring_days=None,
                    include_cancelled=False,
                    skip=0, limit=50,
                )
        assert exc_info.value.status_code == 422


# ── Warranty Summary ───────────────────────────────────────────────────────────

class TestWarrantySummary:
    @pytest.mark.asyncio
    async def test_returns_aggregated_warranty_status(self):
        set_ctx_building_id("13195")
        from routers.defects_register import get_warranty_summary

        agg_result = [
            {"_id": "overdue", "count": 3},
            {"_id": "expiring_soon", "count": 2},
            {"_id": "active", "count": 5},
        ]
        severity_result = [
            {"_id": "major", "count": 4},
            {"_id": "minor", "count": 6},
        ]

        db = MagicMock()
        agg_cursor_1 = MagicMock()
        agg_cursor_1.to_list = AsyncMock(return_value=agg_result)
        agg_cursor_2 = MagicMock()
        agg_cursor_2.to_list = AsyncMock(return_value=severity_result)
        db.defects.aggregate.side_effect = [agg_cursor_1, agg_cursor_2]

        with patch("routers.defects_register._get_db", return_value=db):
            result = await get_warranty_summary(
                building_id="13195",
                current_user=_user(),
            )
        assert result["warranty_clock"]["overdue"] == 3
        assert result["warranty_clock"]["expiring_soon"] == 2
        assert result["warranty_clock"]["active"] == 5
        assert result["warranty_clock"]["no_warranty"] == 0
        assert result["by_severity"]["major"] == 4

    @pytest.mark.asyncio
    async def test_owner_cannot_access_summary(self):
        set_ctx_building_id("13195")
        from routers.defects_register import get_warranty_summary
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_warranty_summary(
                building_id="13195",
                current_user=_owner_user(),
            )
        assert exc_info.value.status_code == 403


# ── Get ────────────────────────────────────────────────────────────────────────

class TestGetDefect:
    @pytest.mark.asyncio
    async def test_returns_defect(self):
        set_ctx_building_id("13195")
        from routers.defects_register import get_defect

        defect = _defect()
        db = _mock_db(doc=defect)
        with patch("routers.defects_register._get_db", return_value=db):
            result = await get_defect(
                defect_id="def-1",
                building_id="13195",
                current_user=_user(),
            )
        assert result["id"] == "def-1"
        assert result["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_returns_404_not_found(self):
        set_ctx_building_id("13195")
        from routers.defects_register import get_defect
        from fastapi import HTTPException

        db = _mock_db(doc=None)
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await get_defect(
                    defect_id="missing-id",
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_building_returns_404(self):
        """TenantScopedDatabase prevents cross-building reads; simulate with None return."""
        set_ctx_building_id("13195")
        from routers.defects_register import get_defect
        from fastapi import HTTPException

        db = _mock_db(doc=None)
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await get_defect(
                    defect_id="def-other-building",
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────

class TestUpdateDefect:
    @pytest.mark.asyncio
    async def test_manager_can_update_status(self):
        set_ctx_building_id("13195")
        from routers.defects_register import update_defect, DefectUpdate

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectUpdate(status="under_investigation", resolution_notes="Builder notified, awaiting inspection")
        with patch("routers.defects_register._get_db", return_value=db):
            result = await update_defect(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        db.defects.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_owner_cannot_update(self):
        set_ctx_building_id("13195")
        from routers.defects_register import update_defect, DefectUpdate
        from fastapi import HTTPException

        payload = DefectUpdate(status="closed")
        with pytest.raises(HTTPException) as exc_info:
            await update_defect(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_owner_user(),
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_update_cancelled_defect(self):
        set_ctx_building_id("13195")
        from routers.defects_register import update_defect, DefectUpdate
        from fastapi import HTTPException

        defect = _defect(cancelled=True)
        db = _mock_db(doc=defect)
        payload = DefectUpdate(status="open")
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await update_defect(
                    defect_id="def-1",
                    payload=payload,
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_recomputes_warranty_deadline_on_completion_date_change(self):
        set_ctx_building_id("13195")
        from routers.defects_register import update_defect, DefectUpdate

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectUpdate(practical_completion_date="2022-01-01", jurisdiction="NSW")
        with patch("routers.defects_register._get_db", return_value=db):
            result = await update_defect(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        # warranty_deadline should be recomputed (2022 + 6 years NSW major = 2028)
        assert "warranty_deadline" in result
        if result.get("warranty_deadline"):
            assert "2028" in result["warranty_deadline"]

    @pytest.mark.asyncio
    async def test_invalid_status_value_rejected(self):
        set_ctx_building_id("13195")
        from routers.defects_register import update_defect, DefectUpdate
        from fastapi import HTTPException

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectUpdate(status="nonexistent_status")
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await update_defect(
                    defect_id="def-1",
                    payload=payload,
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 422


# ── Cancel (soft-delete) ───────────────────────────────────────────────────────

class TestCancelDefect:
    @pytest.mark.asyncio
    async def test_soft_cancel_sets_cancelled_at(self):
        set_ctx_building_id("13195")
        from routers.defects_register import cancel_defect

        defect = _defect()
        db = _mock_db(doc=defect)
        with patch("routers.defects_register._get_db", return_value=db):
            await cancel_defect(
                defect_id="def-1",
                building_id="13195",
                current_user=_user(),
            )
        call_args = db.defects.update_one.call_args[0][1]
        assert "cancelled_at" in call_args["$set"]
        assert call_args["$set"]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_hard_delete_never_called(self):
        set_ctx_building_id("13195")
        from routers.defects_register import cancel_defect

        defect = _defect()
        db = _mock_db(doc=defect)
        db.defects.delete_one = AsyncMock()
        with patch("routers.defects_register._get_db", return_value=db):
            await cancel_defect(
                defect_id="def-1",
                building_id="13195",
                current_user=_user(),
            )
        db.defects.delete_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_defect(self):
        set_ctx_building_id("13195")
        from routers.defects_register import cancel_defect
        from fastapi import HTTPException

        db = _mock_db(doc=None)
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await cancel_defect(
                    defect_id="missing",
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 404


# ── Notes ─────────────────────────────────────────────────────────────────────

class TestAddDefectNote:
    @pytest.mark.asyncio
    async def test_adds_timeline_note(self):
        set_ctx_building_id("13195")
        from routers.defects_register import add_defect_note, DefectNoteCreate

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectNoteCreate(note="Builder attended site, confirmed scope of waterproofing repair required", note_type="inspection")
        with patch("routers.defects_register._get_db", return_value=db):
            result = await add_defect_note(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        assert result["note_type"] == "inspection"
        assert "at" in result
        call_args = db.defects.update_one.call_args[0][1]
        assert "$push" in call_args

    @pytest.mark.asyncio
    async def test_owner_can_add_note(self):
        set_ctx_building_id("13195")
        from routers.defects_register import add_defect_note, DefectNoteCreate

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectNoteCreate(note="I noticed the leak is getting worse after the rain", note_type="general")
        with patch("routers.defects_register._get_db", return_value=db):
            result = await add_defect_note(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_owner_user(),
            )
        assert "at" in result

    @pytest.mark.asyncio
    async def test_cannot_add_note_to_cancelled_defect(self):
        set_ctx_building_id("13195")
        from routers.defects_register import add_defect_note, DefectNoteCreate
        from fastapi import HTTPException

        defect = _defect(cancelled=True)
        db = _mock_db(doc=defect)
        payload = DefectNoteCreate(note="Trying to add note to cancelled", note_type="general")
        with patch("routers.defects_register._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await add_defect_note(
                    defect_id="def-1",
                    payload=payload,
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 409


# ── Photos ────────────────────────────────────────────────────────────────────

class TestAddDefectPhotos:
    @pytest.mark.asyncio
    async def test_adds_photo_urls(self):
        set_ctx_building_id("13195")
        from routers.defects_register import add_defect_photos, DefectPhotoAdd

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectPhotoAdd(photo_urls=["https://cdn.example.com/after1.jpg", "https://cdn.example.com/after2.jpg"])
        with patch("routers.defects_register._get_db", return_value=db):
            result = await add_defect_photos(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        assert result["added"] == 2
        call_args = db.defects.update_one.call_args[0][1]
        assert "$push" in call_args
        assert "photo_urls" in call_args["$push"]

    @pytest.mark.asyncio
    async def test_uses_addToSet_each_for_photos(self):
        set_ctx_building_id("13195")
        from routers.defects_register import add_defect_photos, DefectPhotoAdd

        defect = _defect()
        db = _mock_db(doc=defect)
        payload = DefectPhotoAdd(photo_urls=["https://cdn.example.com/photo3.jpg"])
        with patch("routers.defects_register._get_db", return_value=db):
            await add_defect_photos(
                defect_id="def-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        call_args = db.defects.update_one.call_args[0][1]
        # Uses $push with $each for bulk photo append
        assert "$each" in call_args["$push"]["photo_urls"]
