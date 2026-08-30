"""
Tests for scripts/validation/trust_v1_usage_report.py (BUG-TRUST-001 Stage 2 review
tooling). Fully mocked — never touches real Mongo, so there is no test data to clean
up and these tests are safe to run against production credentials in .env.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SCRIPTS_VALIDATION_DIR = str(Path(__file__).resolve().parents[3] / "scripts" / "validation")
if _SCRIPTS_VALIDATION_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_VALIDATION_DIR)

import trust_v1_usage_report as report_mod  # noqa: E402


class TestIsWriteRoute:
    def test_get_is_never_a_write(self):
        assert report_mod._is_write_route("/api/trust/accounts", "GET") is False

    def test_post_is_a_write(self):
        assert report_mod._is_write_route("/api/trust/accounts", "POST") is True

    def test_case_insensitive_method(self):
        assert report_mod._is_write_route("/api/trust/journal", "post") is True
        assert report_mod._is_write_route("/api/trust/journal", "get") is False

    def test_missing_route_or_method_is_never_a_write(self):
        assert report_mod._is_write_route(None, "POST") is False
        assert report_mod._is_write_route("/api/trust/accounts", None) is False


def _fake_client(events: list[dict]):
    """Build a mock AsyncIOMotorClient whose db.trust_v1_usage_telemetry.find(...)
    resolves to `events` via .to_list()."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=events)
    mock_db = MagicMock()
    mock_db.trust_v1_usage_telemetry.find = MagicMock(return_value=cursor)
    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_client.close = MagicMock()
    return mock_client


@pytest.mark.asyncio
class TestBuildReport:
    async def test_empty_collection_produces_zeroed_report(self, monkeypatch):
        monkeypatch.setenv("MONGO_URL", "mongodb://fake/")
        monkeypatch.setenv("DB_NAME", "fake_db")
        with patch.object(report_mod, "AsyncIOMotorClient", return_value=_fake_client([])):
            report = await report_mod.build_report(days=30)

        assert report["total_events"] == 0
        assert report["write_event_count"] == 0
        assert report["earliest_event"] is None
        assert report["latest_event"] is None
        assert report["by_route_method"] == {}

    async def test_aggregates_by_route_method_status_role_building(self, monkeypatch):
        monkeypatch.setenv("MONGO_URL", "mongodb://fake/")
        monkeypatch.setenv("DB_NAME", "fake_db")
        now = datetime.now(timezone.utc)
        events = [
            {"route": "/api/trust/accounts", "method": "GET", "response_status": 200,
             "actor_role": "strata_manager", "building_id": "13195", "created_at": now},
            {"route": "/api/trust/accounts", "method": "GET", "response_status": 200,
             "actor_role": "strata_manager", "building_id": "13195", "created_at": now},
            {"route": "/api/trust/journal", "method": "GET", "response_status": 403,
             "actor_role": None, "building_id": None, "created_at": now - timedelta(days=1)},
        ]
        with patch.object(report_mod, "AsyncIOMotorClient", return_value=_fake_client(events)):
            report = await report_mod.build_report(days=30)

        assert report["total_events"] == 3
        assert report["by_route_method"]["GET /api/trust/accounts"] == 2
        assert report["by_route_method"]["GET /api/trust/journal"] == 1
        assert report["by_response_status"]["200"] == 2
        assert report["by_response_status"]["403"] == 1
        assert report["by_actor_role"]["strata_manager"] == 2
        assert report["by_actor_role"]["(unknown)"] == 1
        assert report["by_building_id"]["13195"] == 2
        assert report["by_building_id"]["(unknown)"] == 1
        assert report["write_event_count"] == 0

    async def test_write_route_events_are_flagged_and_detailed(self, monkeypatch):
        monkeypatch.setenv("MONGO_URL", "mongodb://fake/")
        monkeypatch.setenv("DB_NAME", "fake_db")
        now = datetime.now(timezone.utc)
        events = [
            {"route": "/api/trust/accounts", "method": "POST", "response_status": 201,
             "actor_role": "strata_manager", "building_id": "13195",
             "actor_hash": "deadbeef01234567", "request_id": "req-1", "created_at": now},
            {"route": "/api/trust/accounts", "method": "GET", "response_status": 200,
             "actor_role": "owner", "building_id": "13195", "created_at": now},
        ]
        with patch.object(report_mod, "AsyncIOMotorClient", return_value=_fake_client(events)):
            report = await report_mod.build_report(days=30)

        assert report["write_event_count"] == 1
        write_event = report["write_route_events"][0]
        assert write_event["route"] == "/api/trust/accounts"
        assert write_event["method"] == "POST"
        assert write_event["actor_hash"] == "deadbeef01234567"
        assert write_event["request_id"] == "req-1"

    async def test_earliest_and_latest_computed_correctly(self, monkeypatch):
        monkeypatch.setenv("MONGO_URL", "mongodb://fake/")
        monkeypatch.setenv("DB_NAME", "fake_db")
        early = datetime(2026, 7, 1, tzinfo=timezone.utc)
        late = datetime(2026, 7, 15, tzinfo=timezone.utc)
        mid = datetime(2026, 7, 8, tzinfo=timezone.utc)
        events = [
            {"route": "/api/trust/accounts", "method": "GET", "response_status": 200, "created_at": mid},
            {"route": "/api/trust/accounts", "method": "GET", "response_status": 200, "created_at": early},
            {"route": "/api/trust/accounts", "method": "GET", "response_status": 200, "created_at": late},
        ]
        with patch.object(report_mod, "AsyncIOMotorClient", return_value=_fake_client(events)):
            report = await report_mod.build_report(days=30)

        assert report["earliest_event"] == early.isoformat()
        assert report["latest_event"] == late.isoformat()

    async def test_client_closed_after_query(self, monkeypatch):
        monkeypatch.setenv("MONGO_URL", "mongodb://fake/")
        monkeypatch.setenv("DB_NAME", "fake_db")
        mock_client = _fake_client([])
        with patch.object(report_mod, "AsyncIOMotorClient", return_value=mock_client):
            await report_mod.build_report(days=30)
        mock_client.close.assert_called_once()


class TestRenderMarkdown:
    def test_zero_events_shows_no_data_message(self):
        report = {
            "generated_at": "2026-07-16T00:00:00+00:00", "window_days": 30,
            "window_start": "2026-06-16T00:00:00+00:00", "total_events": 0,
            "earliest_event": None, "latest_event": None, "write_event_count": 0,
        }
        markdown = report_mod.render_markdown(report)
        assert "No V1 telemetry recorded" in markdown
        assert "Total V1 events recorded: **0**" in markdown

    def test_scope_limit_disclaimer_always_present(self):
        report = {
            "generated_at": "x", "window_days": 30, "window_start": "y",
            "total_events": 0, "earliest_event": None, "latest_event": None,
            "write_event_count": 0,
        }
        markdown = report_mod.render_markdown(report)
        assert "app-telemetry data source only" in markdown

    def test_nonzero_report_renders_all_sections(self):
        report = {
            "generated_at": "2026-07-16T00:00:00+00:00", "window_days": 30,
            "window_start": "2026-06-16T00:00:00+00:00", "total_events": 2,
            "earliest_event": "2026-07-01T00:00:00+00:00",
            "latest_event": "2026-07-15T00:00:00+00:00",
            "by_route_method": {"GET /api/trust/accounts": 2},
            "by_response_status": {"200": 2},
            "by_actor_role": {"strata_manager": 2},
            "by_building_id": {"13195": 2},
            "write_route_events": [],
            "write_event_count": 0,
        }
        markdown = report_mod.render_markdown(report)
        assert "## By route + method" in markdown
        assert "## By response status" in markdown
        assert "## By actor role" in markdown
        assert "## By building" in markdown
        assert "## Write-route events" in markdown
        assert "None recorded in this window" in markdown

    def test_write_route_events_rendered_as_table_rows(self):
        report = {
            "generated_at": "x", "window_days": 30, "window_start": "y",
            "total_events": 1, "earliest_event": "e", "latest_event": "l",
            "by_route_method": {"POST /api/trust/accounts": 1},
            "by_response_status": {"201": 1}, "by_actor_role": {"strata_manager": 1},
            "by_building_id": {"13195": 1}, "write_event_count": 1,
            "write_route_events": [{
                "route": "/api/trust/accounts", "method": "POST", "building_id": "13195",
                "actor_role": "strata_manager", "actor_hash": "deadbeef01234567",
                "response_status": 201, "request_id": "req-1", "created_at": "2026-07-16T00:00:00+00:00",
            }],
        }
        markdown = report_mod.render_markdown(report)
        assert "`/api/trust/accounts`" in markdown
        assert "deadbeef01234567" in markdown
        assert "Classify each" in markdown
