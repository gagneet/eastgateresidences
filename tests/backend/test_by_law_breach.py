"""Tests for by-law breach workflow — Gap 2.3"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _manager():
    return {"id": "u1", "full_name": "Manager", "role": "strata_manager",
            "effective_role": "strata_manager", "is_approved": True}


def _owner():
    return {"id": "u2", "role": "owner", "effective_role": "owner",
            "is_approved": True, "unit_number": "12"}


def _report_doc(status="reported"):
    return {
        "id": "r1", "building_id": "13195", "reporter_id": "u2", "reporter_unit": "12",
        "alleged_unit": "14", "by_law_section": "Section 4 — Parking",
        "description": "Car in visitor spot for 3 weeks", "severity": "minor",
        "status": status, "notices": [], "is_repeat_offence": False,
        "evidence_file_ids": [], "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
    }


def _mock_db(report=None, by_laws=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    db.by_law_breach_reports.find.return_value = cursor
    db.by_law_breach_reports.find_one = AsyncMock(return_value=report)
    db.by_law_breach_reports.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.by_law_breach_reports.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.by_laws.find_one = AsyncMock(return_value=by_laws)
    db.documents.find_one = AsyncMock(return_value=None)
    return db


class TestBreachModels:
    def test_status_constants(self):
        from models.by_law_breach import BreachStatus
        assert "reported" in BreachStatus.ALL
        assert "tribunal_referred" in BreachStatus.TERMINAL
        assert "reported" in BreachStatus.OPEN

    def test_valid_transitions_from_reported(self):
        from models.by_law_breach import BreachStatus
        allowed = BreachStatus.TRANSITIONS[BreachStatus.REPORTED]
        assert BreachStatus.ACKNOWLEDGED in allowed
        assert BreachStatus.FORMAL_NOTICE_SENT not in allowed  # must go through courtesy first

    def test_valid_transitions_terminal_states_empty(self):
        from models.by_law_breach import BreachStatus
        assert BreachStatus.TRANSITIONS[BreachStatus.RESOLVED] == []
        assert BreachStatus.TRANSITIONS[BreachStatus.TRIBUNAL_REFERRED] == []

    def test_create_schema(self):
        from models.by_law_breach import ByLawBreachCreate
        b = ByLawBreachCreate(alleged_unit="14", description="Parking violation", severity="minor")
        assert b.alleged_unit == "14"
        assert b.evidence_file_ids == []

    def test_notice_schema(self):
        from models.by_law_breach import BreachNotice
        n = BreachNotice(notice_type="courtesy", issued_at="2026-04-01T00:00:00Z",
                         issued_by="u1", template_used="courtesy_notice_v1")
        assert n.delivery_method == "email"


class TestSubmitReport:
    @pytest.mark.asyncio
    async def test_invalid_severity_rejected(self):
        from routers.by_law_breach import submit_breach_report
        from models.by_law_breach import ByLawBreachCreate
        from fastapi import HTTPException
        payload = ByLawBreachCreate(alleged_unit="14", description="test", severity="extreme")
        with pytest.raises(HTTPException) as exc:
            await submit_breach_report(payload, building_id="13195", current_user=_owner())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unapproved_user_cannot_report(self):
        from routers.by_law_breach import submit_breach_report
        from models.by_law_breach import ByLawBreachCreate
        from fastapi import HTTPException
        unapproved = {**_owner(), "is_approved": False}
        payload = ByLawBreachCreate(alleged_unit="14", description="test", severity="minor")
        with pytest.raises(HTTPException) as exc:
            await submit_breach_report(payload, building_id="13195", current_user=unapproved)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_report_stored_with_building_id(self):
        from routers.by_law_breach import submit_breach_report
        from models.by_law_breach import ByLawBreachCreate
        db = _mock_db()
        payload = ByLawBreachCreate(alleged_unit="14", description="Parking", severity="minor")
        with patch("routers.by_law_breach._get_db", return_value=db):
            result = await submit_breach_report(payload, building_id="13195", current_user=_owner())
        assert result["status"] == "reported"
        inserted = db.by_law_breach_reports.insert_one.call_args[0][0]
        assert inserted["building_id"] == "13195"
        assert inserted["reporter_id"] == "u2"


class TestAcknowledgeAndNotice:
    @pytest.mark.asyncio
    async def test_acknowledge_requires_manager(self):
        from routers.by_law_breach import acknowledge_breach_report
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await acknowledge_breach_report("r1", notes=None, building_id="13195", current_user=_owner())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_acknowledge_sets_status(self):
        from routers.by_law_breach import acknowledge_breach_report
        db = _mock_db(report=_report_doc("reported"))
        with patch("routers.by_law_breach._get_db", return_value=db):
            result = await acknowledge_breach_report("r1", notes=None, building_id="13195", current_user=_manager())
        assert result["message"] == "Breach report acknowledged."
        update_call = db.by_law_breach_reports.update_one.call_args[0][1]
        assert update_call["$set"]["status"] == "acknowledged"

    @pytest.mark.asyncio
    async def test_cannot_acknowledge_already_acknowledged(self):
        from routers.by_law_breach import acknowledge_breach_report
        from fastapi import HTTPException
        db = _mock_db(report=_report_doc("acknowledged"))
        with patch("routers.by_law_breach._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await acknowledge_breach_report("r1", notes=None, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_issue_invalid_notice_type_rejected(self):
        from routers.by_law_breach import issue_breach_notice
        from models.by_law_breach import BreachNotice
        from fastapi import HTTPException
        db = _mock_db(report=_report_doc("acknowledged"))
        notice = BreachNotice(notice_type="invalid", issued_at="2026-04-01T00:00:00Z",
                              issued_by="u1", template_used="tpl")
        with patch("routers.by_law_breach._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await issue_breach_notice("r1", notice, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400


class TestStatusTransitions:
    @pytest.mark.asyncio
    async def test_invalid_transition_rejected(self):
        from routers.by_law_breach import update_breach_status
        from models.by_law_breach import BreachStatusUpdate
        from fastapi import HTTPException
        db = _mock_db(report=_report_doc("reported"))
        payload = BreachStatusUpdate(new_status="tribunal_referred")  # not allowed from reported
        with patch("routers.by_law_breach._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await update_breach_status("r1", payload, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_transition_acknowledged_to_resolved(self):
        from routers.by_law_breach import update_breach_status
        from models.by_law_breach import BreachStatusUpdate
        db = _mock_db(report=_report_doc("acknowledged"))
        payload = BreachStatusUpdate(new_status="resolved", resolution_outcome="Owner apologised and moved car")
        with patch("routers.by_law_breach._get_db", return_value=db):
            result = await update_breach_status("r1", payload, building_id="13195", current_user=_manager())
        assert result["message"] == "Status updated to 'resolved'."


class TestTribunalExport:
    @pytest.mark.asyncio
    async def test_export_includes_all_sections(self):
        from routers.by_law_breach import export_breach_report
        db = _mock_db(report=_report_doc("escalated"))
        with patch("routers.by_law_breach._get_db", return_value=db):
            result = await export_breach_report("r1", building_id="13195", current_user=_manager())
        assert "breach_report" in result
        assert "timeline" in result
        assert "notices" in result
        assert "jurisdiction_note" in result
        assert result["export_type"] == "tribunal_preparation"


class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_report_list_scoped_to_building(self):
        from routers.by_law_breach import list_breach_reports
        db = _mock_db()
        with patch("routers.by_law_breach._get_db", return_value=db):
            await list_breach_reports(building_id="16244", current_user=_manager(),
                                      status=None, alleged_unit=None, page=1, page_size=30)
        call_args = db.by_law_breach_reports.find.call_args[0][0]
        assert call_args["building_id"] == "16244"
