"""Tests for server-authoritative request catalogue eligibility."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

from services.request_catalogue_service import (  # noqa: E402
    REQUEST_DEFINITIONS,
    RequestPolicyStage,
    enforce_request_policy,
    evaluate_request_policy,
    is_role_eligible,
    resolve_request_catalogue,
)


def _definition(key: str) -> dict:
    return next(item for item in REQUEST_DEFINITIONS if item["key"] == key)


class TestRequestCatalogueRoles:
    def test_uses_canonical_real_estate_agent_role(self):
        user = {"role": "real_estate_agent"}
        assert is_role_eligible(_definition("alterations"), user)

    def test_retired_role_aliases_are_not_accepted(self):
        assert not is_role_eligible(_definition("alterations"), {"role": "chairman"})
        assert not is_role_eligible(_definition("alterations"), {"role": "agent"})

    def test_tenant_cannot_access_owner_financial_workflow(self):
        assert not is_role_eligible(_definition("reimbursement"), {"role": "tenant"})

    def test_admin_staff_can_access_work_orders(self):
        assert is_role_eligible(_definition("work-order"), {"role": "admin_staff"})


class TestRequestCatalogueFeatureResolution:
    @pytest.mark.asyncio
    async def test_disabled_definition_is_omitted(self):
        async def feature_access(_user, key):
            return key != "insurance_claims"

        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(side_effect=feature_access),
        ):
            result = await resolve_request_catalogue(
                {"role": "owner", "building_id": "TEST-REQUEST-A", "unit_number": "101"},
                "TEST-REQUEST-A",
            )

        keys = {item["key"] for item in result}
        assert "insurance-claim" not in keys
        assert "maintenance" in keys

    @pytest.mark.asyncio
    async def test_effective_role_is_honoured(self):
        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            result = await resolve_request_catalogue(
                {
                    "role": "owner",
                    "effective_role": "ec_member",
                    "building_id": "TEST-REQUEST-A",
                },
                "TEST-REQUEST-A",
            )

        assert "work-order" in {item["key"] for item in result}

    @pytest.mark.asyncio
    async def test_internal_role_metadata_is_not_exposed(self):
        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            result = await resolve_request_catalogue(
                {"role": "owner", "building_id": "TEST-REQUEST-A", "unit_number": "101"},
                "TEST-REQUEST-A",
            )

        assert result
        assert all("allowed_roles" not in item for item in result)
        assert all("required_feature" not in item for item in result)
        assert all("stage_permissions" not in item for item in result)


class _FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.seen_filters = []

    async def find_one(self, query, *_args, **_kwargs):
        self.seen_filters.append(query)
        for document in self.documents:
            if all(self._matches(document, key, value) for key, value in query.items()):
                return document
        return None

    @staticmethod
    def _matches(document, key, value):
        if isinstance(value, dict) and "$ne" in value:
            return document.get(key) != value["$ne"]
        return document.get(key) == value


class _FakeDb:
    def __init__(self, user_units=None, units=None):
        self.user_units = _FakeCollection(user_units or [])
        self.units = _FakeCollection(units or [])


class TestRequestEligibilityPolicy:
    @pytest.mark.asyncio
    async def test_submission_requires_same_building_lot_relationship(self):
        user = {
            "id": "user-a",
            "role": "owner",
            "building_id": "TEST-REQUEST-A",
            "unit_number": "101",
            "is_approved": True,
        }
        db = _FakeDb(
            user_units=[
                {
                    "building_id": "TEST-REQUEST-B",
                    "user_id": "user-a",
                    "unit_number": "101",
                    "is_active": True,
                }
            ],
            units=[{"building_id": "TEST-REQUEST-B", "unit_number": "101"}],
        )

        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            result = await evaluate_request_policy(
                user,
                "TEST-REQUEST-A",
                "pet",
                version=1,
                stage=RequestPolicyStage.SUBMISSION,
                db=db,
            )

        assert result.allowed is False
        assert "lot relationship" in result.reason
        assert all(
            query.get("building_id") == "TEST-REQUEST-A"
            for query in db.user_units.seen_filters + db.units.seen_filters
        )

    @pytest.mark.asyncio
    async def test_same_building_unit_without_user_relation_is_not_enough(self):
        user = {
            "id": "user-a",
            "role": "owner",
            "building_id": "TEST-REQUEST-A",
            "unit_number": "101",
            "is_approved": True,
        }
        db = _FakeDb(
            user_units=[],
            units=[{"building_id": "TEST-REQUEST-A", "unit_number": "101"}],
        )

        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            result = await evaluate_request_policy(
                user,
                "TEST-REQUEST-A",
                "pet",
                version=1,
                stage=RequestPolicyStage.SUBMISSION,
                db=db,
            )

        assert result.allowed is False
        assert "lot relationship" in result.reason
        assert db.units.seen_filters == []

    @pytest.mark.asyncio
    async def test_inactive_lot_relationship_is_not_eligible(self):
        user = {
            "id": "user-a",
            "role": "owner",
            "building_id": "TEST-REQUEST-A",
            "unit_number": "101",
            "is_approved": True,
        }
        db = _FakeDb(
            user_units=[{
                "building_id": "TEST-REQUEST-A",
                "user_id": "user-a",
                "unit_number": "101",
                "is_active": False,
            }],
            units=[{"building_id": "TEST-REQUEST-A", "unit_number": "101"}],
        )

        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            result = await evaluate_request_policy(
                user,
                "TEST-REQUEST-A",
                "pet",
                version=1,
                stage=RequestPolicyStage.SUBMISSION,
                db=db,
            )

        assert result.allowed is False
        assert "lot relationship" in result.reason
        assert db.units.seen_filters == []

    @pytest.mark.asyncio
    async def test_user_feature_override_is_scoped_by_user_and_building_context(self):
        users = [
            {
                "id": "owner-a",
                "role": "owner",
                "building_id": "TEST-REQUEST-A",
                "unit_number": "101",
                "is_approved": True,
            },
            {
                "id": "owner-b",
                "role": "owner",
                "building_id": "TEST-REQUEST-B",
                "unit_number": "101",
                "is_approved": True,
            },
        ]

        async def feature_access(user, feature_key):
            assert feature_key == "requests"
            return user["building_id"] == "TEST-REQUEST-A"

        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(side_effect=feature_access),
        ):
            a = await evaluate_request_policy(users[0], "TEST-REQUEST-A", "pet", version=1, stage="discovery")
            b = await evaluate_request_policy(users[1], "TEST-REQUEST-B", "pet", version=1, stage="discovery")

        assert a.allowed is True
        assert b.allowed is False

    @pytest.mark.asyncio
    async def test_reimbursement_review_requires_workflow_permission(self):
        user = {
            "id": "owner-a",
            "role": "owner",
            "building_id": "TEST-REQUEST-A",
            "unit_number": "101",
            "is_approved": True,
        }
        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            result = await evaluate_request_policy(
                user,
                "TEST-REQUEST-A",
                "reimbursement",
                version=1,
                stage="review",
            )

        assert result.allowed is False
        assert "permission" in result.reason

    @pytest.mark.asyncio
    async def test_version_conflict_raises_409(self):
        with patch(
            "services.request_catalogue_service.get_effective_feature_access",
            AsyncMock(return_value=True),
        ):
            with pytest.raises(Exception) as exc:
                await enforce_request_policy(
                    {"role": "owner", "building_id": "TEST-REQUEST-A", "unit_number": "101", "is_approved": True},
                    "TEST-REQUEST-A",
                    "pet",
                    version=999,
                    stage="submission",
                )

        assert exc.value.status_code == 409
