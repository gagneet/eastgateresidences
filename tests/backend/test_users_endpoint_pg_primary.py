"""GAP-IDENTITY-UI-DB-001 — GET /users gated pure-Postgres path.

When identity_core is promoted to postgres_write for a building, GET /users must
serve the user list purely from Postgres (core.users via
list_active_users_for_scheme) and SKIP the legacy Mongo membership aggregation
entirely. Un-promoted buildings keep the Mongo∪Postgres union (covered by
test_users_endpoint_pg_union.py). A transient Postgres outage falls back to the
Mongo pipeline (rule 8) rather than returning an empty admin list.

Pure-mock tests (no live DB) — CI runs them in the venv.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

import server  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_composition_writes():
    with patch(
        "services.identity_shadow_read_service.record_user_list_composition",
        new=AsyncMock(),
    ) as mock_record:
        yield mock_record


def _admin_user() -> dict:
    return {
        "id": "admin-1", "email": "admin@eastgate.com", "full_name": "Site Admin",
        "role": "super_admin", "is_active": True, "is_approved": True,
        "status": "active", "building_id": "13195",
    }


def _pg_row(email: str, full_name: str, role: str = "owner") -> dict:
    return {
        "id": f"pg-{email}", "email": email, "full_name": full_name, "role": role,
        "effective_role": None, "is_active": True, "is_approved": True, "status": "active",
        "is_test_data": False, "phone": None, "created_at": datetime.now(timezone.utc),
        "last_login_at": None, "last_login_ip": None, "is_name_flagged": False,
        "flag_reason": None, "totp_enabled": False, "tenant_id": "tenant-1",
        "lot_numbers": [], "relationship": role,
    }


def _mock_db(membership_rows: list[dict]) -> MagicMock:
    mock_db = MagicMock()
    mock_db.memberships.count_documents = AsyncMock(return_value=len(membership_rows))
    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=membership_rows)
    mock_db.memberships.aggregate = AsyncMock(return_value=agg_cursor)

    class _Empty:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    mock_db.units.find = MagicMock(return_value=_Empty())
    return mock_db


def _decision(*, postgres_allowed: bool):
    d = MagicMock()
    d.postgres_allowed = postgres_allowed
    d.shadow_enabled = False
    return d


@pytest.mark.asyncio
async def test_promoted_building_serves_pure_postgres_and_skips_mongo_agg():
    """postgres_write → PG is the sole source; Mongo aggregation is never run."""
    pg_rows = [_pg_row("owner1@eastgate.com", "Owner One"),
               _pg_row("owner2@eastgate.com", "Owner Two")]
    mock_db = _mock_db([{"user_info": {"id": "m1", "email": "should-not-appear@x.com"}}])
    perms = MagicMock(can_manage_users=True)
    server_agg = MagicMock()  # must NOT be called in the pure-PG path

    with patch.object(server, "db", mock_db), \
            patch.object(server, "_server_agg", server_agg), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()) as repair, \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
            patch("db_postgres.repos.identity_repo.list_active_users_for_scheme",
                  AsyncMock(return_value=pg_rows)), \
            patch("services.domain_source_guard.require_domain_source",
                  AsyncMock(return_value=_decision(postgres_allowed=True))):
        result = await server.get_users(current_user=_admin_user(), building_id="13195")

    emails = {r.email for r in result}
    assert emails == {"owner1@eastgate.com", "owner2@eastgate.com"}
    server_agg.assert_not_called()          # Mongo membership aggregation skipped
    repair.assert_not_called()              # Mongo-only repair skipped too


@pytest.mark.asyncio
async def test_pg_outage_on_promoted_building_falls_back_to_mongo():
    """postgres_write but the PG read raises → rule-8 fallback runs the Mongo pipeline."""
    mongo_rows = [{"user_info": {
        "id": "m-fallback", "email": "fallback@eastgate.com", "full_name": "Fallback User",
        "unit_number": "TH001", "role": "owner", "is_active": True, "is_approved": True,
        "status": "active", "is_test_data": False, "created_at": datetime.now(timezone.utc).isoformat(),
    }}]
    mock_db = _mock_db(mongo_rows)
    perms = MagicMock(can_manage_users=True)
    server_agg = AsyncMock(return_value=mongo_rows)  # fallback path calls this

    with patch.object(server, "db", mock_db), \
            patch.object(server, "_server_agg", server_agg), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
            patch("db_postgres.repos.identity_repo.list_active_users_for_scheme",
                  AsyncMock(side_effect=RuntimeError("pg down"))), \
            patch("services.domain_source_guard.require_domain_source",
                  AsyncMock(return_value=_decision(postgres_allowed=True))):
        result = await server.get_users(current_user=_admin_user(), building_id="13195")

    server_agg.assert_awaited()  # fell back to Mongo rather than returning empty
    assert {r.email for r in result} == {"fallback@eastgate.com"}
