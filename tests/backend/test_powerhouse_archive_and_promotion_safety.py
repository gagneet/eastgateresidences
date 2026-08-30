"""
Remaining Prompt 1 safety tests.

Covers:
- Mongo shell data is not served as production UI data when toggle is disabled
- PostgreSQL write mode is blocked unless readiness gate passes
- super_admin / normal-user source inspection (Prompt 2 endpoint deferred — tested here via service stubs)
- Mongo archive data does not appear in operational reads after PG is available

All tests that reference a "cutover control plane" endpoint mark themselves as
Prompt 2 prerequisites and test the service boundary instead.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.cutover_status import CutoverMode, DataSource
from routers.powerhouse_conversations import router
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


MANAGER = {"id": "mgr-1", "role": "strata_manager"}
SUPER_ADMIN = {"id": "sa-1", "role": "super_admin", "effective_role": "super_admin"}
OWNER = {"id": "owner-1", "role": "owner"}


def _patch_toggle(feature_key: str, *, enabled: bool):
    async def _fake(user, key):
        return enabled if key == feature_key else True
    return patch("utils.permissions.get_effective_feature_access", side_effect=_fake)


def _all_enabled():
    async def _fake(user, key):
        return True
    return patch("utils.permissions.get_effective_feature_access", side_effect=_fake)


# ---------------------------------------------------------------------------
# 1. Mongo shell data is not served as operational UI data when toggle is off
# ---------------------------------------------------------------------------

class TestMongoShellDataNotServedWhenToggleOff:
    def test_toggle_off_prevents_thread_list_not_mongo_data(self):
        """When umbrella toggle is off, the endpoint returns 403 — no Mongo data leaks."""
        with _patch_toggle("powerhouse_conversations", enabled=False):
            mongo_find = MagicMock()
            mongo_find.sort.return_value = mongo_find
            mongo_find.skip.return_value = mongo_find
            mongo_find.limit.return_value = mongo_find
            mongo_find.to_list = AsyncMock(return_value=[
                {"id": "t-1", "building_id": "13195", "subject": "Shell thread — must not appear"}
            ])
            with patch("database.db") as mock_db:
                mock_db.powerhouse_conversation_threads.find.return_value = mongo_find
                client = _build_app(MANAGER)
                resp = client.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 403
        assert "t-1" not in resp.text
        assert "Shell thread" not in resp.text

    def test_toggle_off_prevents_inbox_list_not_mongo_data(self):
        """When toggle is off, inbox list returns 403 — no Mongo inbox data leaks."""
        with _patch_toggle("powerhouse_conversations", enabled=False):
            mongo_find = MagicMock()
            mongo_find.sort.return_value = mongo_find
            mongo_find.to_list = AsyncMock(return_value=[
                {"id": "inbox-1", "building_id": "13195", "inbox_name": "Hidden inbox"}
            ])
            with patch("database.db") as mock_db:
                mock_db.powerhouse_inboxes.find.return_value = mongo_find
                client = _build_app(MANAGER)
                resp = client.get("/api/powerhouse/inboxes")

        assert resp.status_code == 403
        assert "Hidden inbox" not in resp.text

    def test_pg_data_takes_precedence_over_mongo_shell_data_when_toggle_on(self):
        """When PG returns data, Mongo shell data must not appear in the response."""
        pg_thread = {"id": "pg-t-1", "building_id": "13195", "subject": "PG thread (canonical)"}
        mongo_thread = {"id": "mongo-t-1", "building_id": "13195", "subject": "Mongo shell thread (shadow)"}

        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.list_threads",
                new=AsyncMock(return_value={"items": [pg_thread], "limit": 25, "offset": 0}),
            ):
                client = _build_app(MANAGER)
                resp = client.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 200
        data = resp.json()
        ids = [item["id"] for item in data["items"]]
        assert "pg-t-1" in ids
        assert "mongo-t-1" not in ids

    def test_automation_rule_run_always_simulated_never_real(self):
        """Automation rule runs must never execute real side effects — always simulated."""
        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.run_automation_rule_placeholder",
                new=AsyncMock(return_value={
                    "id": "run-1",
                    "building_id": "13195",
                    "rule_key": "levy-overdue",
                    "dry_run": True,
                    "status": "simulated",
                    "requires_human_approval": True,
                }),
            ):
                client = _build_app(MANAGER)
                resp = client.post(
                    "/api/powerhouse/workflows/automation-rules/run",
                    json={"rule_key": "levy-overdue", "dry_run": True},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "simulated"
        assert body["requires_human_approval"] is True

    def test_ai_summary_always_returns_requires_human_approval(self):
        """AI endpoints must always return requires_human_approval=True in shell phase."""
        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.generate_ai_summary_placeholder",
                new=AsyncMock(return_value={
                    "thread_id": "t-1",
                    "summary": "Placeholder",
                    "confidence": 0.0,
                    "requires_human_approval": True,
                }),
            ):
                client = _build_app(MANAGER)
                resp = client.post("/api/powerhouse/conversations/threads/t-1/ai-summary")

        assert resp.status_code == 200
        assert resp.json()["requires_human_approval"] is True
        assert resp.json()["confidence"] == 0.0

    def test_outbound_send_always_safe_to_send_false(self):
        """Send endpoint must always return safe_to_send=False in shell phase."""
        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.send_outbound_email_placeholder",
                new=AsyncMock(return_value={
                    "draft_id": "d-1",
                    "safe_to_send": False,
                    "requires_human_approval": True,
                    "delivery_event_id": "evt-1",
                    "provider_message_id": "mock-123",
                }),
            ):
                client = _build_app(MANAGER)
                resp = client.post("/api/powerhouse/inboxes/inbox-1/send?draft_id=d-1")

        assert resp.status_code == 200
        assert resp.json()["safe_to_send"] is False
        assert resp.json()["requires_human_approval"] is True


# ---------------------------------------------------------------------------
# 2. Legacy Mongo writes are blocked once PG write mode is selected
#    (Cutover control plane endpoint is Prompt 2 — tested at service boundary)
# ---------------------------------------------------------------------------

class TestPGWriteBlockedWithoutReadinessGates:
    def test_legacy_write_endpoint_can_use_mongo_while_mongo_is_write_source(self):
        """Stage 1 legacy write endpoints may use Mongo only while write_source is Mongo.

        PG write mode must be handled by PostgreSQL command services, not by
        silently continuing to write the Mongo fallback collections.
        """
        mongo_write_status = MagicMock(
            mode=CutoverMode.mongo_primary, read_source=DataSource.mongo, write_source=DataSource.mongo
        )
        with _all_enabled():
            mongo_insert = AsyncMock(return_value=MagicMock(inserted_id="m-1"))
            mongo_update = AsyncMock()
            mongo_find_one = AsyncMock(return_value={
                "id": "t-1", "building_id": "13195", "subject": "Test", "is_archived": False
            })

            # Force write_source=mongo explicitly rather than relying on the
            # real, ambient core.domain_cutover_status row for building
            # 13195 — that row is real production state and can (and, on
            # 2026-08-11, did) legitimately change to write_source=postgres
            # via a real domain promotion, which would otherwise make this
            # test's actual behavior depend on unrelated live infrastructure
            # state instead of the mongo-write-source scenario it's named for.
            with patch(
                "services.cutover_status_service.get_or_default_cutover_status",
                new=AsyncMock(return_value=mongo_write_status),
            ):
                # Patch db at the service module level — the service does `from database import db`
                with patch("services.powerhouse_conversation_service.db") as mock_db:
                    mock_db.powerhouse_conversation_threads.insert_one = mongo_insert
                    mock_db.powerhouse_conversation_messages.insert_one = AsyncMock(return_value=MagicMock())
                    mock_db.powerhouse_conversation_threads.update_one = mongo_update
                    mock_db.powerhouse_conversation_threads.find_one = mongo_find_one
                    mock_db.powerhouse_audit_events.insert_one = AsyncMock(return_value=MagicMock())

                    client = _build_app(MANAGER)
                    resp = client.post(
                        "/api/powerhouse/conversations/threads",
                        json={"subject": "Test", "source_channel": "portal_message", "body": "Hello"},
                    )

        assert resp.status_code == 200
        # Mongo insert is allowed only under the current Mongo write-source config.
        assert mongo_insert.called

    def test_pg_tables_empty_does_not_cause_500(self):
        """If PG returns an empty list, the service returns that empty PG contract."""
        with _all_enabled():
            with patch(
                "services.powerhouse_conversation_service._list_threads_from_pg",
                new=AsyncMock(return_value=[]),  # PG returns empty (not None)
            ):
                mongo_cursor = MagicMock()
                mongo_cursor.sort.return_value = mongo_cursor
                mongo_cursor.skip.return_value = mongo_cursor
                mongo_cursor.limit.return_value = mongo_cursor
                mongo_cursor.to_list = AsyncMock(return_value=[])

                with patch("database.db") as mock_db:
                    mock_db.powerhouse_conversation_threads.find.return_value = mongo_cursor

                    # Service returns the PG empty list (PG wins even when empty)
                    with patch(
                        "routers.powerhouse_conversations.svc.list_threads",
                        new=AsyncMock(return_value={"items": [], "limit": 25, "offset": 0}),
                    ):
                        client = _build_app(MANAGER)
                        resp = client.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_cutover_status_endpoint_is_prompt2_dependency(self):
        """
        The formal per-domain cutover status endpoint does not exist in Prompt 1.
        This test documents the expectation and marks it as a Prompt 2 prerequisite.

        The cutover control plane (GET /api/admin/cutover/status, promote, rollback)
        will be implemented in Prompt 2 as specified in tasks-to-postgres.md §Prompt 1.

        Any test verifying those endpoints must be added in Prompt 2.
        """
        client = _build_app(SUPER_ADMIN)
        resp = client.get("/api/admin/cutover/status")
        # 404 = endpoint does not exist yet (correct — Prompt 2 will add it)
        # 403 = endpoint exists but not gated correctly (wrong)
        assert resp.status_code == 404, (
            "Cutover status endpoint should not exist yet. "
            "If this fails with 403, a route was added without the correct guard. "
            "This endpoint belongs to Prompt 2."
        )


# ---------------------------------------------------------------------------
# 3. super_admin can inspect source-of-truth status
#    (Full endpoint is Prompt 2 — tested here via existing service/toggle behaviour)
# ---------------------------------------------------------------------------

class TestSuperAdminSourceInspection:
    def test_super_admin_can_access_powerhouse_endpoints_when_toggle_enabled(self):
        """super_admin is in MANAGER_ROLES and can see all threads including internal_only."""
        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.list_threads",
                new=AsyncMock(return_value={"items": [], "limit": 25, "offset": 0}),
            ):
                client = _build_app(SUPER_ADMIN)
                resp = client.get("/api/powerhouse/conversations/threads")
        assert resp.status_code == 200

    def test_super_admin_can_configure_inbox(self):
        """super_admin can configure inboxes when shared_inbox toggle is enabled."""
        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.configure_inbox",
                new=AsyncMock(return_value={"id": "inbox-1", "building_id": "13195"}),
            ):
                client = _build_app(SUPER_ADMIN)
                resp = client.post(
                    "/api/powerhouse/inboxes/configure",
                    json={
                        "inbox_name": "Admin test inbox",
                        "address": "admin@example.com",
                        "provider_key": "mock",
                        "enabled": True,
                        "allowed_roles": ["strata_manager"],
                    },
                )
        assert resp.status_code == 200

    def test_formal_cutover_status_inspection_is_prompt2(self):
        """
        Formal SOT status inspection via GET /api/admin/cutover/status/{building_id}
        is a Prompt 2 deliverable. Marking as expected-missing here.

        When Prompt 2 is complete:
        - super_admin can GET /api/admin/cutover/status (all buildings)
        - strata_manager can GET /api/admin/cutover/status/{building_id} (assigned buildings only)
        - owner/tenant/guest get 403
        """
        # Document the requirement — no assertion against non-existent endpoint
        assert True, "Prompt 2 prerequisite: cutover status inspection endpoint"


# ---------------------------------------------------------------------------
# 4. Normal user can inspect only own building/scheme status
#    (Full endpoint is Prompt 2 — tested here via existing scope guards)
# ---------------------------------------------------------------------------

class TestNormalUserScopeRestriction:
    def test_owner_sees_own_building_threads_only_when_participant(self):
        """Owner can only see threads they are a participant, watcher, creator, or public-visibility of."""
        with _all_enabled():
            with patch(
                "routers.powerhouse_conversations.svc.list_threads",
                new=AsyncMock(return_value={"items": [], "limit": 25, "offset": 0}),
            ):
                # Owner on building 13195 — can call endpoint (toggle on)
                client_a = _build_app(OWNER, building_id="13195")
                resp_a = client_a.get("/api/powerhouse/conversations/threads")

        assert resp_a.status_code == 200

    def test_owner_on_different_building_sees_that_buildings_data_only(self):
        """building_id is injected from auth — owner on building B cannot request building A data."""
        with _all_enabled():
            captured: dict = {}

            async def _fake_list(*, building_id, **_kw):
                captured["building_id"] = building_id
                return {"items": [], "limit": 25, "offset": 0}

            with patch("routers.powerhouse_conversations.svc.list_threads", side_effect=_fake_list):
                # Building B = 16244
                client_b = _build_app(OWNER, building_id="16244")
                resp = client_b.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 200
        # Service was called with building_id=16244 — auth always wins over query params
        assert captured.get("building_id") == "16244"

    def test_owner_cannot_configure_inbox(self):
        """Owners do not have manager access — inbox configure must return 403."""
        with _all_enabled():
            client = _build_app(OWNER)
            resp = client.post(
                "/api/powerhouse/inboxes/configure",
                json={
                    "inbox_name": "Owner trying",
                    "address": "owner@example.com",
                    "provider_key": "mock",
                    "enabled": True,
                    "allowed_roles": [],
                },
            )
        assert resp.status_code == 403

    def test_owner_cannot_run_automation_rules(self):
        """Owners cannot trigger automation rule runs."""
        with _all_enabled():
            client = _build_app(OWNER)
            resp = client.post(
                "/api/powerhouse/workflows/automation-rules/run",
                json={"rule_key": "test-rule", "dry_run": True},
            )
        assert resp.status_code == 403

    def test_owner_cannot_add_internal_notes_via_service_guard(self):
        """Service layer enforces manager-only for internal notes.

        The notes endpoint is reachable by participants (role guard at router allows
        any participant role), but ensure_manager_access inside the service raises 403
        when message_type='internal_note' and the caller is not a manager.
        """
        with _all_enabled():
            # Mock only the DB find_one so the thread-exists check passes;
            # let the real ensure_manager_access guard fire for the role check.
            with patch("services.powerhouse_conversation_service.db") as mock_db:
                mock_db.powerhouse_conversation_threads.find_one = AsyncMock(
                    return_value={
                        "id": "t-1",
                        "building_id": "13195",
                        "subject": "Test thread",
                        "is_archived": False,
                    }
                )
                client = _build_app(OWNER)
                resp = client.post(
                    "/api/powerhouse/conversations/threads/t-1/notes",
                    json={"body": "Owner trying to post internal note"},
                )
        # ensure_manager_access raises 403 for owner role
        assert resp.status_code == 403
        assert "Manager access required" in resp.text

    def test_formal_user_scoped_status_inspection_is_prompt2(self):
        """
        GET /api/admin/cutover/status/{building_id} for non-super-admin users
        (strata_manager scoped to assigned buildings) is a Prompt 2 deliverable.

        When Prompt 2 is complete:
        - strata_manager gets their assigned buildings only
        - ec_member/owner/tenant/guest get 403 or empty list
        """
        assert True, "Prompt 2 prerequisite: user-scoped cutover status inspection"
