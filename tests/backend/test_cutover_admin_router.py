"""
Tests for routers/cutover_admin.py

Covers router-layer concerns: RBAC guards, endpoint shape, service delegation.
Service-layer safety rules (P0, mode transitions) are covered in
test_cutover_status_service.py.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.cutover_status import (
    AuditAction,
    CutoverActionResponse,
    CutoverMode,
    CutoverStatusDetailResponse,
    CutoverStatusListResponse,
    DataSource,
    DomainCutoverStatus,
    DomainCutoverStatusSummary,
    P0ReadinessReport,
    ReadinessStatus,
    ShadowDiffRecord,
)
from routers.cutover_admin import router
from utils.auth import get_current_building, get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app(user: dict, building_id: str = "13195") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


def _now() -> datetime:
    return datetime.now(UTC)


def _summary(building_id: str = "13195", domain: str = "finance_ledger",
             mode: CutoverMode = CutoverMode.mongo_primary) -> DomainCutoverStatusSummary:
    return DomainCutoverStatusSummary(
        building_id=building_id,
        domain=domain,
        mode=mode,
        readiness_status=ReadinessStatus.unknown,
        read_source=DataSource.mongo,
        write_source=DataSource.mongo,
        rollback_available=True,
    )


def _full_status(building_id: str = "13195", domain: str = "finance_ledger",
                 mode: CutoverMode = CutoverMode.mongo_primary) -> DomainCutoverStatus:
    now = _now()
    return DomainCutoverStatus(
        id=str(uuid4()),
        building_id=building_id,
        domain=domain,
        read_source=DataSource.mongo,
        write_source=DataSource.mongo,
        mode=mode,
        readiness_status=ReadinessStatus.unknown,
        rollback_available=True,
        created_at=now,
        updated_at=now,
    )


def _action_response(building_id="13195", domain="finance_ledger",
                     prev="mongo_primary", new="postgres_shadow") -> CutoverActionResponse:
    return CutoverActionResponse(
        building_id=building_id,
        domain=domain,
        previous_mode=prev,
        new_mode=new,
        action="entered_shadow",
        audit_id=str(uuid4()),
        message="ok",
    )


SUPER_ADMIN = {"id": "sa-1", "role": "super_admin", "effective_role": "super_admin",
               "building_id": "13195"}
STRATA_MANAGER = {"id": "sm-1", "role": "strata_manager", "effective_role": "strata_manager",
                  "building_id": "13195"}
EC_MEMBER = {"id": "ec-1", "role": "ec_member", "effective_role": "ec_member",
             "building_id": "13195"}
OWNER = {"id": "owner-1", "role": "owner", "effective_role": "owner", "building_id": "13195"}
TENANT = {"id": "tenant-1", "role": "tenant", "effective_role": "tenant", "building_id": "13195"}


# ---------------------------------------------------------------------------
# 6. Ordinary user cannot promote
# ---------------------------------------------------------------------------

class TestOrdinaryUserCannotPromote:
    """Owners, tenants, and guests must receive 403 on all mutating endpoints."""

    @pytest.mark.parametrize("user", [OWNER, TENANT])
    def test_owner_tenant_cannot_list_status(self, user):
        client = _build_app(user)
        resp = client.get("/api/admin/cutover/status")
        assert resp.status_code == 403

    @pytest.mark.parametrize("user", [OWNER, TENANT])
    def test_owner_tenant_cannot_promote_shadow(self, user):
        client = _build_app(user)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/shadow", json={})
        assert resp.status_code == 403

    @pytest.mark.parametrize("user", [OWNER, TENANT])
    def test_owner_tenant_cannot_promote_read(self, user):
        client = _build_app(user)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-read", json={})
        assert resp.status_code == 403

    @pytest.mark.parametrize("user", [OWNER, TENANT])
    def test_owner_tenant_cannot_promote_write(self, user):
        client = _build_app(user)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-write", json={})
        assert resp.status_code == 403

    @pytest.mark.parametrize("user", [OWNER, TENANT])
    def test_owner_tenant_cannot_rollback(self, user):
        client = _build_app(user)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/rollback", json={"reason": "test"})
        assert resp.status_code == 403

    @pytest.mark.parametrize("user", [OWNER, TENANT])
    def test_owner_tenant_cannot_view_diffs(self, user):
        client = _build_app(user)
        resp = client.get("/api/admin/cutover/13195/finance_ledger/diffs")
        assert resp.status_code == 403

    def test_strata_manager_cannot_promote(self):
        """strata_manager has inspect access but NOT promote access."""
        client = _build_app(STRATA_MANAGER)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/shadow", json={})
        assert resp.status_code == 403

    def test_ec_member_cannot_promote(self):
        client = _build_app(EC_MEMBER)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-read", json={})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 7. super_admin can inspect
# ---------------------------------------------------------------------------

class TestSuperAdminCanInspect:
    def test_list_all_status(self):
        items = [_summary(), _summary(building_id="16244")]
        with patch("routers.cutover_admin.svc.list_all_cutover_status",
                   new=AsyncMock(return_value=items)):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_list_building_status(self):
        items = [_summary()]
        with patch("routers.cutover_admin.svc.list_all_cutover_status",
                   new=AsyncMock(return_value=items)):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status/13195")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_domain_detail(self):
        status = _full_status()
        with patch("routers.cutover_admin.svc.get_cutover_status",
                   new=AsyncMock(return_value=status)), \
             patch("routers.cutover_admin.svc.list_shadow_diffs",
                   new=AsyncMock(return_value=[])), \
             patch("routers.cutover_admin.svc.list_audit_log",
                   new=AsyncMock(return_value=[])):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status/13195/finance_ledger")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"]["building_id"] == "13195"
        assert body["status"]["domain"] == "finance_ledger"

    def test_get_domain_detail_404_when_not_registered(self):
        with patch("routers.cutover_admin.svc.get_cutover_status",
                   new=AsyncMock(return_value=None)):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status/13195/unknown_domain")
        assert resp.status_code == 404

    def test_super_admin_can_view_all_buildings(self):
        """super_admin bypasses the building-scoping restriction."""
        items_a = [_summary(building_id="13195")]
        items_b = [_summary(building_id="16244")]
        with patch("routers.cutover_admin.svc.list_all_cutover_status",
                   new=AsyncMock(side_effect=[items_a, items_b])):
            client = _build_app(SUPER_ADMIN)
            resp_a = client.get("/api/admin/cutover/status/13195")
            resp_b = client.get("/api/admin/cutover/status/16244")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200


# ---------------------------------------------------------------------------
# Non-super-admin building scope restriction
# ---------------------------------------------------------------------------

class TestNonSuperAdminBuildingScope:
    def test_strata_manager_can_inspect_own_building(self):
        items = [_summary(building_id="13195")]
        with patch("routers.cutover_admin.svc.list_all_cutover_status",
                   new=AsyncMock(return_value=items)):
            client = _build_app(STRATA_MANAGER, building_id="13195")
            resp = client.get("/api/admin/cutover/status/13195")
        assert resp.status_code == 200

    def test_strata_manager_denied_on_other_building(self):
        """strata_manager for building 13195 cannot inspect building 16244."""
        client = _build_app(STRATA_MANAGER, building_id="13195")
        resp = client.get("/api/admin/cutover/status/16244")
        assert resp.status_code == 403

    def test_ec_member_can_inspect_own_building_detail(self):
        status = _full_status(building_id="13195")
        with patch("routers.cutover_admin.svc.get_cutover_status",
                   new=AsyncMock(return_value=status)), \
             patch("routers.cutover_admin.svc.list_shadow_diffs",
                   new=AsyncMock(return_value=[])), \
             patch("routers.cutover_admin.svc.list_audit_log",
                   new=AsyncMock(return_value=[])):
            client = _build_app(EC_MEMBER, building_id="13195")
            resp = client.get("/api/admin/cutover/status/13195/finance_ledger")
        assert resp.status_code == 200

    def test_ec_member_denied_on_other_building(self):
        client = _build_app(EC_MEMBER, building_id="13195")
        resp = client.get("/api/admin/cutover/status/16244/finance_ledger")
        assert resp.status_code == 403


class TestFinanceRouteReadinessEndpoint:
    def test_super_admin_can_view_finance_route_readiness(self):
        rows = [
            {
                "route_key": "finance.building_overview",
                "method": "GET",
                "path": "/finance/building-overview",
                "frontend_consumer": "Owner dashboard",
                "read_only": True,
                "shadow_supported": True,
                "postgres_read_supported": True,
                "current_source": "mongo",
                "domain_mode": "postgres_shadow",
                "readiness_status": "shadow_pass",
                "diff_count": 0,
                "critical_count": 0,
                "last_compared_at": None,
                "eligible_for_postgres_read": True,
                "blocked_reason": None,
                "notes": "ok",
            }
        ]
        with patch(
            "routers.cutover_admin.get_finance_route_readiness_table",
            new=AsyncMock(return_value=rows),
        ):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status-finance-routes/13195")
        assert resp.status_code == 200
        assert resp.json()[0]["route_key"] == "finance.building_overview"

    def test_non_super_admin_cannot_access_other_building_finance_routes(self):
        client = _build_app(STRATA_MANAGER, building_id="13195")
        resp = client.get("/api/admin/cutover/status-finance-routes/16244")
        assert resp.status_code == 403

    def test_super_admin_can_view_finance_write_route_readiness(self):
        rows = [
            {
                "route_key": "finance.levy_payment_create",
                "method": "POST",
                "path": "/levy-payments",
                "current_source": "postgres",
                "domain_mode": "postgres_write",
                "eligible_for_postgres_write": True,
                "blocked_reason": None,
            }
        ]
        with patch(
            "routers.cutover_admin.get_finance_write_readiness_table",
            new=AsyncMock(return_value=rows),
        ):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status-finance-write-routes/13195")
        assert resp.status_code == 200
        assert resp.json()[0]["route_key"] == "finance.levy_payment_create"

    def test_non_super_admin_cannot_access_other_building_finance_write_routes(self):
        client = _build_app(STRATA_MANAGER, building_id="13195")
        resp = client.get("/api/admin/cutover/status-finance-write-routes/16244")
        assert resp.status_code == 403


class TestPowerhouseCommandFoundationHealthEndpoint:
    def test_super_admin_can_view_command_foundation_health(self):
        payload = {
            "building_id": "13195",
            "scheme_found": True,
            "domains": [
                {"domain": "powerhouse_conversations", "mode": "mongo_primary", "readiness_status": "unknown"},
            ],
            "outbox": {"pending": 4, "oldest_pending": None, "dead_lettered": 0},
            "idempotency": {"incomplete_records": 0},
        }
        with patch(
            "routers.cutover_admin.get_powerhouse_command_foundation_health",
            new=AsyncMock(return_value=payload),
        ):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/powerhouse-command-foundation/13195")
        assert resp.status_code == 200
        assert resp.json()["outbox"]["pending"] == 4

    def test_non_super_admin_cannot_access_other_building_command_foundation_health(self):
        client = _build_app(STRATA_MANAGER, building_id="13195")
        resp = client.get("/api/admin/cutover/powerhouse-command-foundation/16244")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Shadow entry
# ---------------------------------------------------------------------------

class TestEnterShadowMode:
    def test_enter_shadow_from_primary(self):
        current = _full_status(mode=CutoverMode.mongo_primary)
        updated = _full_status(mode=CutoverMode.postgres_shadow)
        audit_id = str(uuid4())

        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)), \
             patch("routers.cutover_admin.svc.promote_domain",
                   new=AsyncMock(return_value=(updated, audit_id))):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/shadow", json={"reason": "starting shadow"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["new_mode"] == "postgres_shadow"
        assert body["action"] == "entered_shadow"
        assert body["audit_id"] == audit_id

    def test_enter_shadow_rejected_when_already_in_shadow(self):
        current = _full_status(mode=CutoverMode.postgres_shadow)
        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/shadow", json={})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Promote-read
# ---------------------------------------------------------------------------

class TestPromoteRead:
    def test_promote_read_from_shadow(self):
        current = _full_status(mode=CutoverMode.postgres_shadow)
        updated = _full_status(mode=CutoverMode.postgres_read)
        audit_id = str(uuid4())

        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)), \
             patch("routers.cutover_admin.has_any_promotable_finance_route",
                   new=AsyncMock(return_value=(True, []))), \
             patch("routers.cutover_admin.svc.promote_domain",
                   new=AsyncMock(return_value=(updated, audit_id))):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-read", json={})

        assert resp.status_code == 200
        assert resp.json()["new_mode"] == "postgres_read"

    def test_promote_read_rejected_when_in_wrong_mode(self):
        current = _full_status(mode=CutoverMode.mongo_primary)
        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-read", json={})
        assert resp.status_code == 409
        assert "promote-read is only valid from postgres_shadow" in resp.json()["detail"]

    def test_promote_read_rejected_when_no_finance_route_is_eligible(self):
        current = _full_status(mode=CutoverMode.postgres_shadow)
        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)), \
             patch(
                 "routers.cutover_admin.has_any_promotable_finance_route",
                 new=AsyncMock(
                     return_value=(
                         False,
                         [
                             {
                                 "route_key": "finance.building_overview",
                                 "postgres_read_supported": True,
                                 "eligible_for_postgres_read": False,
                                 "blocked_reason": "Route readiness is shadow_warn",
                                 "readiness_status": "shadow_warn",
                             }
                         ],
                     )
                 ),
             ):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-read", json={})
        assert resp.status_code == 409
        assert "no route is eligible" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Promote-write
# ---------------------------------------------------------------------------

class TestPromoteWrite:
    def test_promote_write_from_read(self):
        current = _full_status(mode=CutoverMode.postgres_read)
        updated = _full_status(mode=CutoverMode.postgres_write)
        audit_id = str(uuid4())

        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)), \
             patch("routers.cutover_admin.svc.promote_domain",
                   new=AsyncMock(return_value=(updated, audit_id))):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-write", json={})

        assert resp.status_code == 200
        assert resp.json()["new_mode"] == "postgres_write"

    def test_promote_write_rejected_when_in_shadow(self):
        current = _full_status(mode=CutoverMode.postgres_shadow)
        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-write", json={})
        assert resp.status_code == 409
        assert "promote-write is only valid from postgres_read" in resp.json()["detail"]

    def test_promote_write_rejected_when_in_primary(self):
        current = _full_status(mode=CutoverMode.mongo_primary)
        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/promote-write", json={})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_from_shadow_to_primary(self):
        current = _full_status(mode=CutoverMode.postgres_shadow)
        rolled_back = _full_status(mode=CutoverMode.mongo_primary)
        audit_id = str(uuid4())

        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)), \
             patch("routers.cutover_admin.svc.rollback_domain",
                   new=AsyncMock(return_value=(rolled_back, audit_id))):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/rollback",
                               json={"reason": "too many diffs"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["new_mode"] == "mongo_primary"
        assert body["action"] == "rolled_back"

    def test_rollback_reason_is_required(self):
        client = _build_app(SUPER_ADMIN)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/rollback", json={})
        # reason field is required by CutoverRollbackRequest
        assert resp.status_code == 422

    def test_rollback_reason_too_short_rejected(self):
        client = _build_app(SUPER_ADMIN)
        resp = client.post("/api/admin/cutover/13195/finance_ledger/rollback", json={"reason": "hi"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Shadow diffs endpoint
# ---------------------------------------------------------------------------

class TestShadowDiffsEndpoint:
    def test_list_diffs_returns_empty_list(self):
        with patch("routers.cutover_admin.svc.list_shadow_diffs",
                   new=AsyncMock(return_value=[])):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/13195/finance_ledger/diffs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_diffs_with_resolved_filter(self):
        with patch("routers.cutover_admin.svc.list_shadow_diffs",
                   new=AsyncMock(return_value=[])) as mock_list:
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/13195/finance_ledger/diffs?resolved=false")
        assert resp.status_code == 200
        mock_list.assert_called_once_with(
            building_id="13195", domain="finance_ledger", resolved=False, limit=50
        )

    def test_list_diffs_strata_manager_own_building(self):
        with patch("routers.cutover_admin.svc.list_shadow_diffs",
                   new=AsyncMock(return_value=[])):
            client = _build_app(STRATA_MANAGER, building_id="13195")
            resp = client.get("/api/admin/cutover/13195/finance_ledger/diffs")
        assert resp.status_code == 200

    def test_list_diffs_strata_manager_other_building_denied(self):
        client = _build_app(STRATA_MANAGER, building_id="13195")
        resp = client.get("/api/admin/cutover/16244/finance_ledger/diffs")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Record diff endpoint
# ---------------------------------------------------------------------------

class TestRecordDiffEndpoint:
    def test_super_admin_can_record_diff(self):
        diff_id = str(uuid4())
        with patch("routers.cutover_admin.svc.record_shadow_diff",
                   new=AsyncMock(return_value=diff_id)):
            client = _build_app(SUPER_ADMIN)
            resp = client.post(
                "/api/admin/cutover/13195/finance_ledger/record-diff",
                json={
                    "diff_type": "count_mismatch",
                    "mongo_value": {"count": 42},
                    "pg_value": {"count": 41},
                    "divergence_score": 0.5,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["diff_id"] == diff_id

    def test_strata_manager_cannot_record_diff(self):
        """record-diff is super_admin only."""
        client = _build_app(STRATA_MANAGER)
        resp = client.post(
            "/api/admin/cutover/13195/finance_ledger/record-diff",
            json={"diff_type": "field_mismatch"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Response model shape validation
# ---------------------------------------------------------------------------

class TestResponseShapes:
    def test_list_response_has_items_and_total(self):
        items = [_summary()]
        with patch("routers.cutover_admin.svc.list_all_cutover_status",
                   new=AsyncMock(return_value=items)):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status")
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert body["total"] == 1

    def test_detail_response_has_status_diffs_audits(self):
        status = _full_status()
        with patch("routers.cutover_admin.svc.get_cutover_status",
                   new=AsyncMock(return_value=status)), \
             patch("routers.cutover_admin.svc.list_shadow_diffs",
                   new=AsyncMock(return_value=[])), \
             patch("routers.cutover_admin.svc.list_audit_log",
                   new=AsyncMock(return_value=[])):
            client = _build_app(SUPER_ADMIN)
            resp = client.get("/api/admin/cutover/status/13195/finance_ledger")
        body = resp.json()
        assert "status" in body
        assert "recent_diffs" in body
        assert "recent_audits" in body

    def test_action_response_has_required_fields(self):
        current = _full_status(mode=CutoverMode.mongo_primary)
        updated = _full_status(mode=CutoverMode.postgres_shadow)
        audit_id = str(uuid4())
        with patch("routers.cutover_admin.svc.get_or_default_cutover_status",
                   new=AsyncMock(return_value=current)), \
             patch("routers.cutover_admin.svc.promote_domain",
                   new=AsyncMock(return_value=(updated, audit_id))):
            client = _build_app(SUPER_ADMIN)
            resp = client.post("/api/admin/cutover/13195/finance_ledger/shadow", json={})
        body = resp.json()
        for field in ("building_id", "domain", "previous_mode", "new_mode", "action", "audit_id", "message"):
            assert field in body, f"Missing field: {field}"
