"""
Tests for Tenant Maintenance Request Pydantic models.

Covers status constants, model validation, field defaults, and role constants
relevant to the tenant-facing maintenance request workflow.

All tests are pure unit tests — no async, no database, no mocking needed.
"""

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock, patch

from models.tenancy import (
    MaintenanceRequestStatus,
    MaintenanceUrgency,
    MaintenanceRequestCreate,
    MaintenanceRequestUpdate,
    MaintenanceRequestMessage,
    MaintenanceRequestResponse,
)
from models.user import UserRole


# ──────────────────────────────────────────────────────────────────────────────
# Status constants
# ──────────────────────────────────────────────────────────────────────────────

class TestStatusConstants:
    def test_maintenance_request_status_constants(self):
        assert MaintenanceRequestStatus.SUBMITTED == "submitted"
        assert MaintenanceRequestStatus.ACKNOWLEDGED == "acknowledged"
        assert MaintenanceRequestStatus.ASSIGNED == "assigned"
        assert MaintenanceRequestStatus.IN_PROGRESS == "in_progress"
        assert MaintenanceRequestStatus.COMPLETED == "completed"
        assert MaintenanceRequestStatus.CLOSED == "closed"
        assert MaintenanceRequestStatus.REJECTED == "rejected"


# ──────────────────────────────────────────────────────────────────────────────
# MaintenanceRequestCreate validation
# ──────────────────────────────────────────────────────────────────────────────

class TestMaintenanceRequestCreate:
    def test_maintenance_request_create_model(self):
        req = MaintenanceRequestCreate(
            property_id="prop-001",
            issue_title="Leaking tap in kitchen",
            issue_description="The kitchen tap has been dripping for two days.",
            urgency=MaintenanceUrgency.ROUTINE,
        )
        assert req.issue_title == "Leaking tap in kitchen"
        assert req.urgency == "routine"

    def test_maintenance_request_urgency_emergency(self):
        req = MaintenanceRequestCreate(
            eastgate_unit_number="TH087",
            issue_title="Burst pipe — flooding",
            issue_description="Water is flooding the bathroom from a burst pipe.",
            urgency=MaintenanceUrgency.EMERGENCY,
        )
        assert req.urgency == "emergency"

    def test_maintenance_request_requires_title(self):
        with pytest.raises(ValidationError) as exc_info:
            MaintenanceRequestCreate(
                property_id="prop-001",
                issue_description="Description without a title.",
            )
        field_names = [e["loc"][0] for e in exc_info.value.errors()]
        assert "issue_title" in field_names

    def test_photo_urls_optional(self):
        req = MaintenanceRequestCreate(
            property_id="prop-001",
            issue_title="Cracked tile",
            issue_description="Tile in bathroom is cracked.",
            photo_urls=[],
        )
        assert req.photo_urls == []

    def test_photo_urls_list(self):
        urls = [
            "https://storage.example.com/photo1.jpg",
            "https://storage.example.com/photo2.jpg",
        ]
        req = MaintenanceRequestCreate(
            property_id="prop-001",
            issue_title="Mould on ceiling",
            issue_description="Significant mould growth on bathroom ceiling.",
            photo_urls=urls,
        )
        assert len(req.photo_urls) == 2
        assert req.photo_urls[0] == urls[0]

    def test_eastgate_unit_or_property_id(self):
        # eastgate_unit_number set, property_id omitted
        req_eastgate = MaintenanceRequestCreate(
            eastgate_unit_number="UA063",
            issue_title="Stove not working",
            issue_description="Stove burners stopped working.",
        )
        assert req_eastgate.eastgate_unit_number == "UA063"
        assert req_eastgate.property_id is None

        # property_id set, eastgate_unit_number omitted
        req_external = MaintenanceRequestCreate(
            property_id="prop-ext-999",
            issue_title="Broken fence",
            issue_description="Side fence panel is broken.",
        )
        assert req_external.property_id == "prop-ext-999"
        assert req_external.eastgate_unit_number is None

    def test_urgency_defaults_to_routine(self):
        req = MaintenanceRequestCreate(
            property_id="prop-001",
            issue_title="Paint peeling",
            issue_description="Paint is peeling from the living room wall.",
        )
        assert req.urgency == MaintenanceUrgency.ROUTINE


# ──────────────────────────────────────────────────────────────────────────────
# MaintenanceRequestUpdate validation
# ──────────────────────────────────────────────────────────────────────────────

class TestMaintenanceRequestUpdate:
    def test_maintenance_request_update_model(self):
        update = MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.ACKNOWLEDGED,
            assigned_tradesperson="Plumber Co.",
            tradesperson_phone="0400 123 456",
        )
        assert update.status == "acknowledged"
        assert update.assigned_tradesperson == "Plumber Co."

    def test_request_update_all_none(self):
        # All fields optional — should succeed with no values supplied
        update = MaintenanceRequestUpdate()
        assert update.status is None
        assert update.assigned_tradesperson is None
        assert update.cost_estimate is None
        assert update.actual_cost is None
        assert update.resolution_notes is None


# ──────────────────────────────────────────────────────────────────────────────
# MaintenanceRequestMessage validation
# ──────────────────────────────────────────────────────────────────────────────

class TestMaintenanceRequestMessage:
    def test_maintenance_message_model(self):
        msg = MaintenanceRequestMessage(
            content="Tradesperson booked for Thursday 10 AM.",
            photo_urls=["https://storage.example.com/schedule.jpg"],
        )
        assert msg.content == "Tradesperson booked for Thursday 10 AM."
        assert len(msg.photo_urls) == 1


# ──────────────────────────────────────────────────────────────────────────────
# MaintenanceRequestResponse validation
# ──────────────────────────────────────────────────────────────────────────────

class TestMaintenanceRequestResponse:
    def _base_response(self, **overrides) -> dict:
        base = dict(
            id="maint-001",
            submitted_by_user_id="user-tenant-1",
            submitted_by_name="Emma Wilson",
            property_id="prop-001",
            issue_title="Leaking tap",
            issue_description="Kitchen tap dripping.",
            urgency=MaintenanceUrgency.ROUTINE,
            status=MaintenanceRequestStatus.SUBMITTED,
            created_at="2026-03-01T09:00:00Z",
            updated_at="2026-03-01T09:00:00Z",
        )
        base.update(overrides)
        return base

    def test_maintenance_response_model(self):
        resp = MaintenanceRequestResponse(**self._base_response())
        assert resp.id == "maint-001"
        assert resp.submitted_by_name == "Emma Wilson"
        assert resp.urgency == "routine"
        assert resp.status == "submitted"
        assert resp.photo_urls == []
        assert resp.message_count == 0

    def test_maintenance_status_defaults_to_submitted(self):
        resp = MaintenanceRequestResponse(**self._base_response())
        assert resp.status == MaintenanceRequestStatus.SUBMITTED


# ──────────────────────────────────────────────────────────────────────────────
# Role constant test
# ──────────────────────────────────────────────────────────────────────────────

class TestRoleConstants:
    def test_new_role_in_allowed_roles(self):
        assert UserRole.REAL_ESTATE_AGENT == "real_estate_agent"


class TestMaintenanceRequestAccess:
    @pytest.mark.asyncio
    async def test_ec_member_list_filters_to_shared_ownerhub_properties(self):
        from services.tenancy_service import get_maintenance_requests

        property_cursor = MagicMock()
        property_cursor.to_list = AsyncMock(return_value=[])
        request_cursor = MagicMock()
        request_cursor.to_list = AsyncMock(return_value=[])

        with patch("services.tenancy_service.db") as mock_db:
            mock_db.owner_properties.find.return_value = property_cursor
            mock_db.tenant_maintenance_requests.find.return_value = request_cursor

            result = await get_maintenance_requests(
                {},
                {"id": "ec-123", "role": "ec_member", "building_id": "13195"},
            )

        assert result == []
        assert mock_db.owner_properties.find.call_args[0][0] == {
            "$or": [
                {"shared_staff_ids": "ec-123"},
                {"shared_with_user_ids": "ec-123"},
                {"allowed_user_ids": "ec-123"},
                {"assigned_staff_ids": "ec-123"},
            ]
        }
        assert mock_db.tenant_maintenance_requests.find.call_args[0][0] == {
            "property_id": {"$in": []}
        }
