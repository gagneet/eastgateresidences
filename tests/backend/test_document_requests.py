"""Tests for document request workflow — Gap 1.2"""
import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


def _owner():
    return {"id": "u1", "role": "owner", "effective_role": "owner",
            "is_approved": True, "unit_number": "12"}


def _manager():
    return {"id": "u2", "role": "strata_manager", "effective_role": "strata_manager",
            "is_approved": True}


def _mock_db(requests=None, documents=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=requests or [])
    db.document_requests.find.return_value = cursor
    db.document_requests.find_one = AsyncMock(return_value=None)
    db.document_requests.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.document_requests.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.documents.find_one = AsyncMock(return_value=documents)
    return db


class TestDocumentRequestModels:
    def test_status_constants(self):
        from models.document_requests import DocumentRequestStatus
        assert "submitted" in DocumentRequestStatus.ALL
        assert "fulfilled" in DocumentRequestStatus.ALL
        assert "submitted" in DocumentRequestStatus.OPEN
        assert "fulfilled" not in DocumentRequestStatus.OPEN

    def test_document_type_auto_fulfil_eligible(self):
        from models.document_requests import DocumentRequestType
        assert "by_laws" in DocumentRequestType.AUTO_FULFIL_ELIGIBLE
        assert "agm_minutes" in DocumentRequestType.AUTO_FULFIL_ELIGIBLE
        assert "building_plans" not in DocumentRequestType.AUTO_FULFIL_ELIGIBLE

    def test_create_schema(self):
        from models.document_requests import DocumentRequestCreate
        r = DocumentRequestCreate(document_type="by_laws", description="Current by-laws copy")
        assert r.document_type == "by_laws"
        assert r.urgency is None

    def test_response_has_is_overdue(self):
        from models.document_requests import DocumentRequestResponse
        r = DocumentRequestResponse(
            id="r1", building_id="13195", requester_id="u1",
            document_type="by_laws", description="test",
            status="submitted", requested_at="2026-01-01T00:00:00Z",
            due_by="2026-01-15",
        )
        assert r.is_overdue is False


class TestDueDateCalculation:
    def test_due_by_is_14_days_from_today(self):
        from routers.document_requests import _due_by
        expected = (date.today() + timedelta(days=14)).isoformat()
        assert _due_by() == expected

    def test_enrich_sets_overdue_for_open_past_due(self):
        from routers.document_requests import _enrich
        doc = {
            "status": "submitted",
            "due_by": "2000-01-01",  # well in the past
        }
        enriched = _enrich(doc)
        assert enriched["is_overdue"] is True

    def test_enrich_not_overdue_for_fulfilled(self):
        from routers.document_requests import _enrich
        doc = {"status": "fulfilled", "due_by": "2000-01-01"}
        enriched = _enrich(doc)
        assert enriched["is_overdue"] is False


class TestSubmitRequest:
    @pytest.mark.asyncio
    async def test_invalid_document_type_rejected(self):
        from routers.document_requests import submit_document_request
        from models.document_requests import DocumentRequestCreate
        from fastapi import HTTPException
        payload = DocumentRequestCreate(document_type="invalid_type", description="test")
        with pytest.raises(HTTPException) as exc:
            await submit_document_request(payload, building_id="13195", current_user=_owner())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_auto_fulfil_when_public_doc_exists(self):
        from routers.document_requests import submit_document_request
        from models.document_requests import DocumentRequestCreate
        public_doc = {"id": "doc-001", "category": "by_laws", "building_id": "13195", "is_private": False}
        db = _mock_db(documents=public_doc)
        payload = DocumentRequestCreate(document_type="by_laws", description="Need by-laws")
        with patch("routers.document_requests._get_db", return_value=db):
            result = await submit_document_request(payload, building_id="13195", current_user=_owner())
        assert result["status"] == "auto_fulfilled"
        assert result["document_id"] == "doc-001"

    @pytest.mark.asyncio
    async def test_manager_review_when_no_public_doc(self):
        from routers.document_requests import submit_document_request
        from models.document_requests import DocumentRequestCreate
        db = _mock_db(documents=None)
        payload = DocumentRequestCreate(document_type="by_laws", description="Need by-laws")
        with patch("routers.document_requests._get_db", return_value=db):
            result = await submit_document_request(payload, building_id="13195", current_user=_owner())
        assert result["status"] == "manager_review"


class TestFulfilAndDeny:
    @pytest.mark.asyncio
    async def test_fulfil_requires_manager(self):
        from routers.document_requests import fulfil_document_request
        from models.document_requests import DocumentRequestFulfil
        from fastapi import HTTPException
        payload = DocumentRequestFulfil(document_id="doc-001")
        with pytest.raises(HTTPException) as exc:
            await fulfil_document_request("r1", payload, building_id="13195", current_user=_owner())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_deny_requires_manager(self):
        from routers.document_requests import deny_document_request
        from models.document_requests import DocumentRequestDeny
        from fastapi import HTTPException
        payload = DocumentRequestDeny(denial_reason="Not available")
        with pytest.raises(HTTPException) as exc:
            await deny_document_request("r1", payload, building_id="13195", current_user=_owner())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_fulfil_404_on_missing_request(self):
        from routers.document_requests import fulfil_document_request
        from models.document_requests import DocumentRequestFulfil
        from fastapi import HTTPException
        db = _mock_db()  # find_one returns None
        payload = DocumentRequestFulfil(document_id="doc-001")
        with patch("routers.document_requests._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc:
            await fulfil_document_request("r1", payload, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 404


class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_owner_only_sees_own_requests(self):
        from routers.document_requests import list_document_requests
        db = _mock_db()
        with patch("routers.document_requests._get_db", return_value=db):
            await list_document_requests(
                building_id="13195", current_user=_owner(), status=None, page=1, page_size=30
            )
        call_args = db.document_requests.find.call_args[0][0]
        assert call_args["requester_id"] == "u1"
        assert call_args["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_manager_sees_all_requests_in_building(self):
        from routers.document_requests import list_document_requests
        db = _mock_db()
        with patch("routers.document_requests._get_db", return_value=db):
            await list_document_requests(
                building_id="13195", current_user=_manager(), status=None, page=1, page_size=30
            )
        call_args = db.document_requests.find.call_args[0][0]
        assert "requester_id" not in call_args  # manager sees all
        assert call_args["building_id"] == "13195"
