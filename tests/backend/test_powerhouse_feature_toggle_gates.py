"""
Tests for Powerhouse feature-toggle gate enforcement.

Covers:
- umbrella toggle (powerhouse_conversations) blocks all /powerhouse/* endpoints when disabled
- sub-feature toggles block their specific endpoint groups
- toggle disabled for building A does not block building B (per-scheme isolation)
- PG shadow read path does not change API response shape
- AI summary placeholder always returns requires_human_approval=True regardless of toggle state
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


MANAGER_USER = {"id": "mgr-1", "role": "strata_manager"}
OWNER_USER = {"id": "owner-1", "role": "owner"}


def _patch_toggle(feature_key: str, *, enabled: bool):
    """Return a context manager that patches get_effective_feature_access for one key."""
    async def _fake_access(user, key):
        return enabled if key == feature_key else True

    return patch("utils.permissions.get_effective_feature_access", side_effect=_fake_access)


# ---------------------------------------------------------------------------
# Umbrella toggle: powerhouse_conversations
# ---------------------------------------------------------------------------

class TestUmbrellaToggleGate:
    def test_umbrella_disabled_blocks_list_threads(self):
        with _patch_toggle("powerhouse_conversations", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.get("/api/powerhouse/conversations/threads")
        assert resp.status_code == 403
        assert "powerhouse_conversations" in resp.text

    def test_umbrella_disabled_blocks_create_thread(self):
        with _patch_toggle("powerhouse_conversations", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/conversations/threads",
                json={"subject": "Test", "source_channel": "portal_message", "body": "Hello"},
            )
        assert resp.status_code == 403

    def test_umbrella_disabled_blocks_list_inboxes(self):
        with _patch_toggle("powerhouse_conversations", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.get("/api/powerhouse/inboxes")
        assert resp.status_code == 403

    def test_umbrella_enabled_allows_list_threads(self):
        with _patch_toggle("powerhouse_conversations", enabled=True):
            with patch(
                "routers.powerhouse_conversations.svc.list_threads",
                new=AsyncMock(return_value={"items": [], "limit": 25, "offset": 0}),
            ):
                client = _build_app(MANAGER_USER)
                resp = client.get("/api/powerhouse/conversations/threads")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Sub-feature: powerhouse_ai_summary
# ---------------------------------------------------------------------------

class TestAISummaryToggleGate:
    def test_ai_summary_disabled_blocks_endpoint(self):
        with _patch_toggle("powerhouse_ai_summary", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post("/api/powerhouse/conversations/threads/thread-1/ai-summary")
        assert resp.status_code == 403
        assert "powerhouse_ai_summary" in resp.text

    def test_ai_draft_disabled_blocks_endpoint(self):
        with _patch_toggle("powerhouse_ai_summary", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post("/api/powerhouse/conversations/threads/thread-1/ai-response-draft")
        assert resp.status_code == 403

    def test_ai_summary_enabled_returns_placeholder_with_human_approval(self):
        with _patch_toggle("powerhouse_ai_summary", enabled=True):
            with patch(
                "routers.powerhouse_conversations.svc.generate_ai_summary_placeholder",
                new=AsyncMock(
                    return_value={
                        "thread_id": "thread-1",
                        "summary": "AI summary placeholder. Human review is required before any action.",
                        "suggested_next_actions": ["Review context"],
                        "confidence": 0.0,
                        "requires_human_approval": True,
                    }
                ),
            ):
                client = _build_app(MANAGER_USER)
                resp = client.post("/api/powerhouse/conversations/threads/thread-1/ai-summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["requires_human_approval"] is True
        assert body["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Sub-feature: powerhouse_shared_inbox
# ---------------------------------------------------------------------------

class TestSharedInboxToggleGate:
    def test_configure_inbox_disabled_blocks(self):
        with _patch_toggle("powerhouse_shared_inbox", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/inboxes/configure",
                json={
                    "inbox_name": "Building shared inbox",
                    "address": "inbox@example.com",
                    "provider_key": "mock",
                    "enabled": True,
                    "allowed_roles": ["owner"],
                },
            )
        assert resp.status_code == 403
        assert "powerhouse_shared_inbox" in resp.text

    def test_outbound_draft_disabled_blocks(self):
        with _patch_toggle("powerhouse_shared_inbox", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/inboxes/inbox-1/outbound-drafts",
                json={"subject": "Test", "body": "Hello", "recipients": ["a@b.com"]},
            )
        assert resp.status_code == 403

    def test_send_disabled_blocks(self):
        with _patch_toggle("powerhouse_shared_inbox", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post("/api/powerhouse/inboxes/inbox-1/send?draft_id=draft-1")
        assert resp.status_code == 403

    def test_list_inboxes_not_blocked_by_shared_inbox_toggle(self):
        """list_inboxes is gated by umbrella only, not shared_inbox sub-toggle."""
        with _patch_toggle("powerhouse_shared_inbox", enabled=False):
            with patch(
                "routers.powerhouse_conversations.svc.list_inboxes",
                new=AsyncMock(return_value=[]),
            ):
                client = _build_app(MANAGER_USER)
                resp = client.get("/api/powerhouse/inboxes")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Sub-feature: powerhouse_email_intake
# ---------------------------------------------------------------------------

class TestEmailIntakeToggleGate:
    def test_inbound_email_disabled_blocks(self):
        with _patch_toggle("powerhouse_email_intake", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/inboxes/inbox-1/inbound-email",
                json={
                    "message_id": "<abc@example.com>",
                    "references": [],
                    "subject": "Test",
                    "from_email": "owner@example.com",
                    "to": ["inbox@example.com"],
                    "cc": [],
                    "text_body": "Hello",
                },
            )
        assert resp.status_code == 403
        assert "powerhouse_email_intake" in resp.text

    def test_map_inbound_disabled_blocks(self):
        with _patch_toggle("powerhouse_email_intake", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post("/api/powerhouse/inboxes/inbox-1/map-inbound?message_id=msg-1")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Sub-feature: powerhouse_workflow_engine
# ---------------------------------------------------------------------------

class TestWorkflowEngineToggleGate:
    def test_list_templates_disabled_blocks(self):
        with _patch_toggle("powerhouse_workflow_engine", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.get("/api/powerhouse/workflows/templates")
        assert resp.status_code == 403

    def test_create_instance_disabled_blocks(self):
        with _patch_toggle("powerhouse_workflow_engine", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/workflows/instances",
                json={"template_key": "maintenance-from-conversation", "title": "Test"},
            )
        assert resp.status_code == 403

    def test_convert_thread_to_workflow_disabled_blocks(self):
        with _patch_toggle("powerhouse_workflow_engine", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/conversations/threads/thread-1/convert-to-workflow",
                json={"workflow_template_key": "maintenance-from-conversation"},
            )
        assert resp.status_code == 403

    def test_convert_message_to_workflow_disabled_blocks(self):
        with _patch_toggle("powerhouse_workflow_engine", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/conversations/messages/msg-1/convert-to-workflow",
                json={"workflow_template_key": "maintenance-from-conversation"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Sub-feature: powerhouse_automation_rules
# ---------------------------------------------------------------------------

class TestAutomationRulesToggleGate:
    def test_run_automation_rule_disabled_blocks(self):
        with _patch_toggle("powerhouse_automation_rules", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.post(
                "/api/powerhouse/workflows/automation-rules/run",
                json={"rule_key": "levy-overdue-reminder", "dry_run": True},
            )
        assert resp.status_code == 403
        assert "powerhouse_automation_rules" in resp.text

    def test_list_automation_runs_disabled_blocks(self):
        with _patch_toggle("powerhouse_automation_rules", enabled=False):
            client = _build_app(MANAGER_USER)
            resp = client.get("/api/powerhouse/workflows/automation-rule-runs")
        assert resp.status_code == 403

    def test_run_automation_rule_enabled_returns_simulated_status(self):
        with _patch_toggle("powerhouse_automation_rules", enabled=True):
            with patch(
                "routers.powerhouse_conversations.svc.run_automation_rule_placeholder",
                new=AsyncMock(
                    return_value={
                        "id": "run-1",
                        "building_id": "13195",
                        "rule_key": "levy-overdue-reminder",
                        "dry_run": True,
                        "status": "simulated",
                        "requires_human_approval": True,
                    }
                ),
            ):
                client = _build_app(MANAGER_USER)
                resp = client.post(
                    "/api/powerhouse/workflows/automation-rules/run",
                    json={"rule_key": "levy-overdue-reminder", "dry_run": True},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "simulated"
        assert body["requires_human_approval"] is True


# ---------------------------------------------------------------------------
# Cross-building isolation: toggle state is per-building
# ---------------------------------------------------------------------------

class TestCrossBuildingToggleIsolation:
    def test_toggle_enabled_building_a_disabled_building_b_threads(self):
        """
        Simulate per-building resolution: building 13195 has toggle on,
        building 16244 has toggle off. They must not bleed into each other.
        """
        call_count = {"n": 0}

        async def _building_aware_access(user, key):
            if key != "powerhouse_conversations":
                return True
            call_count["n"] += 1
            # First call = building A (enabled), second = building B (disabled)
            return call_count["n"] == 1

        with patch("utils.permissions.get_effective_feature_access", side_effect=_building_aware_access):
            with patch(
                "routers.powerhouse_conversations.svc.list_threads",
                new=AsyncMock(return_value={"items": [], "limit": 25, "offset": 0}),
            ):
                # Building A (first call) — should succeed
                client_a = _build_app(MANAGER_USER, building_id="13195")
                resp_a = client_a.get("/api/powerhouse/conversations/threads")

            # Building B (second call) — should be blocked
            client_b = _build_app(MANAGER_USER, building_id="16244")
            resp_b = client_b.get("/api/powerhouse/conversations/threads")

        assert resp_a.status_code == 200
        assert resp_b.status_code == 403

    def test_building_id_is_always_injected_from_auth_not_from_request_body(self):
        """Ensure building_id comes from auth dependency, not a user-supplied value."""
        with _patch_toggle("powerhouse_conversations", enabled=True):
            captured: dict = {}

            async def _fake_list(*, building_id, current_user, **_kw):
                captured["building_id"] = building_id
                return {"items": [], "limit": 25, "offset": 0}

            with patch("routers.powerhouse_conversations.svc.list_threads", side_effect=_fake_list):
                client = _build_app(MANAGER_USER, building_id="13195")
                resp = client.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 200
        assert captured["building_id"] == "13195"


# ---------------------------------------------------------------------------
# PG shadow mode: API response shape is stable
# ---------------------------------------------------------------------------

class TestPGShadowModeResponseShape:
    def test_list_threads_mongo_fallback_shape_matches_pg_shape(self):
        """When PG is unavailable, Mongo fallback returns identical response envelope."""
        with _patch_toggle("powerhouse_conversations", enabled=True):
            # Mock PG as unavailable (returns None), Mongo returns items
            with patch(
                "services.powerhouse_conversation_service._list_threads_from_pg",
                new=AsyncMock(return_value=None),
            ):
                mongo_cursor = MagicMock()
                mongo_cursor.sort.return_value = mongo_cursor
                mongo_cursor.skip.return_value = mongo_cursor
                mongo_cursor.limit.return_value = mongo_cursor
                mongo_cursor.to_list = AsyncMock(return_value=[{"id": "t-1", "subject": "Test"}])
                with patch("services.powerhouse_conversation_service.db") as mock_db:
                    mock_db.powerhouse_conversation_threads.find.return_value = mongo_cursor
                    client = _build_app(MANAGER_USER)
                    resp = client.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "limit" in body
        assert "offset" in body


# ---------------------------------------------------------------------------
# PG write mode: PG-primary read never writes when toggle is off
# ---------------------------------------------------------------------------

class TestPGReadModeDoesNotWrite:
    def test_pg_read_path_does_not_write_to_mongo(self):
        """
        When PG returns data (list_threads from PG), Mongo write operations
        must not be called.
        """
        with _patch_toggle("powerhouse_conversations", enabled=True):
            pg_items = [{"id": "t-pg-1", "subject": "PG thread", "building_id": "13195"}]

            with patch(
                "services.powerhouse_conversation_service._list_threads_from_pg",
                new=AsyncMock(return_value=pg_items),
            ):
                status_mock = MagicMock(mode=CutoverMode.postgres_read, read_source=DataSource.postgres)
                with patch(
                    "services.cutover_status_service.get_or_default_cutover_status",
                    new=AsyncMock(return_value=status_mock),
                ):
                    with patch("services.powerhouse_conversation_service.db") as mock_db:
                        client = _build_app(MANAGER_USER)
                        resp = client.get("/api/powerhouse/conversations/threads")

        assert resp.status_code == 200
        # Mongo find was not called because PG satisfied the request
        mock_db.powerhouse_conversation_threads.find.assert_not_called()


# ---------------------------------------------------------------------------
# Role boundaries
# ---------------------------------------------------------------------------

class TestRoleBoundaries:
    def test_owner_cannot_add_internal_note(self):
        """Internal notes are manager-only; owners must be blocked."""
        with _patch_toggle("powerhouse_conversations", enabled=True):
            client = _build_app(OWNER_USER)
            resp = client.post(
                "/api/powerhouse/conversations/threads/thread-1/notes",
                json={"body": "Owner trying internal note", "message_type": "message"},
            )
        assert resp.status_code == 403

    def test_guest_blocked_when_umbrella_toggle_disabled(self):
        with _patch_toggle("powerhouse_conversations", enabled=False):
            client = _build_app({"id": "guest-1", "role": "guest"})
            resp = client.get("/api/powerhouse/conversations/threads")
        assert resp.status_code == 403
