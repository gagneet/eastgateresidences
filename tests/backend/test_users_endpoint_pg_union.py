"""
Tests for the GET /users endpoint's resolver + Postgres-union fix.

What this covers
----------------
1.  Mongo users whose denormalised ``full_name`` / ``unit_number`` are blank
    get them backfilled in-memory from the canonical owner map (matched
    primarily by email, secondarily by name).
2.  Users that exist only in Postgres (``core.users`` + ``core.user_units``)
    are unioned into the response so they show up in the UI even when the
    legacy Mongo ``memberships`` / ``users`` collections haven't been
    backfilled yet (Mongo→Postgres cutover in progress).
3.  System / hidden accounts (``SYSTEM_HIDDEN_EMAIL`` and the per-building
    ``system-cutover+<n>@system.strataos.local`` shell) are never surfaced.
4.  Multi-tenant isolation: a 13195 caller does not get 16244 users from the
    Postgres union path.

Run from project root:
    backend/venv/bin/python3 -m pytest tests/backend/test_users_endpoint_pg_union.py -v
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

import server  # noqa: E402


# server.get_users() schedules composition instrumentation via asyncio.create_task()
# (added 2026-07-14, PostgreSQL shadow-read expansion Phase D) — a real, unmocked
# background write to core.shadow_diffs/core.shadow_read_coverage_daily. Whether that
# task actually executes before pytest-asyncio tears down the event loop is a timing
# accident, not a guarantee — see pattern_test_no_db_override_writes_to_real_control_plane
# memory for two prior real-DB-write incidents this same session caused by exactly this
# kind of unmocked fire-and-forget call. Autouse mock here removes the risk entirely
# rather than relying on favorable scheduling.
@pytest.fixture(autouse=True)
def _no_real_composition_writes():
    with patch(
        "services.identity_shadow_read_service.record_user_list_composition",
        new=AsyncMock(),
    ) as mock_record:
        yield mock_record


def _force_mongo_identity_source():
    """Force GET /users' GAP-IDENTITY-UI-DB-001 gate (server.py:2503-2512) to
    stay on the Mongo∪PG union path this file's tests exercise, instead of the
    live/shared identity_core cutover-status lookup deciding it for building
    "13195" (which can be postgres_write in a shared dev DB, skipping the
    Mongo membership fetch this file's mocks rely on entirely). The second,
    unconditional require_domain_source(operation="shadow_read") call further
    down get_users() only reads .shadow_enabled, which this SimpleNamespace
    doesn't have — that AttributeError is caught by get_users()'s own
    broad except Exception around the composition-instrumentation block
    (server.py:2805-2806), so it's safe to leave undiscriminated here.
    """
    from types import SimpleNamespace
    return patch(
        "services.domain_source_guard.require_domain_source",
        new=AsyncMock(return_value=SimpleNamespace(postgres_allowed=False)),
    )


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _admin_user(building: str = "13195") -> dict:
    return {
        "id": "admin-1",
        "email": "admin@eastgate.com",
        "full_name": "Site Admin",
        "role": "super_admin",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "building_id": building,
        "created_at": _iso(),
    }


def _mongo_user(
    *,
    user_id: str,
    email: str,
    full_name: str = "",
    unit_number: str = "",
    role: str = "owner",
) -> dict:
    return {
        "id": user_id,
        "email": email,
        "full_name": full_name,
        "unit_number": unit_number,
        "role": role,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "is_test_data": False,
        "created_at": _iso(),
    }


def _build_mock_db(membership_rows: list[dict], units_docs: list[dict] | None = None) -> MagicMock:
    """A minimal mock db sufficient for server.get_users().

    - ``memberships.aggregate`` returns the lookup-style rows
      ``[{"user_info": {...}}]`` that the pipeline at server.py:2194 emits.
    - ``memberships.count_documents`` returns the membership count (for the
      empty-state warning guard).
    - ``units.find`` yields documents (async iterator) for the lot→unit map
      built inside the endpoint.
    """
    mock_db = MagicMock()
    mock_db.memberships.count_documents = AsyncMock(return_value=len(membership_rows))

    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=membership_rows)
    mock_db.memberships.aggregate = AsyncMock(return_value=agg_cursor)

    docs = list(units_docs or [])

    class _UnitsCursor:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            self._iter = iter(self._items)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration as exc:  # pragma: no cover - asyncio plumbing
                raise StopAsyncIteration from exc

    mock_db.units.find = MagicMock(return_value=_UnitsCursor(docs))
    return mock_db


@pytest.mark.asyncio
async def test_resolves_blank_full_name_and_unit_via_email():
    """Riyu has blank legacy fields in Mongo — owner_map by email fills them."""
    riyu_membership = {
        "user_info": _mongo_user(
            user_id="riyu-id",
            email="riyuroy@gmail.com",
            full_name="",
            unit_number="",
        )
    }
    owner_map = {
        "TH086": {
            "owner_name": "Riyu Kurian Abraham",
            "owner_email": "riyuroy@gmail.com",
            "owner_name_b": "Reshma Shaji",
            "owner_email_b": "reshma@example.com",
        }
    }
    units_docs = [{"lot_number": "LOT86", "unit_number": "TH086"}]

    mock_db = _build_mock_db([riyu_membership], units_docs=units_docs)
    perms = MagicMock(can_manage_users=True)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value=owner_map)), \
            patch(
                "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                AsyncMock(return_value=[]),
            ), \
            _force_mongo_identity_source():
        result = await server.get_users(
            current_user=_admin_user(),
            building_id="13195",
        )

    assert len(result) == 1
    riyu = result[0]
    assert riyu.email == "riyuroy@gmail.com"
    assert riyu.full_name == "Riyu Kurian Abraham"
    assert riyu.unit_number == "TH086"
    assert riyu.unit_owner_name == "Riyu Kurian Abraham"
    assert riyu.co_owner_name == "Reshma Shaji"


@pytest.mark.asyncio
async def test_resolves_unit_via_name_when_email_does_not_match():
    """EC member with work email matches by full_name → unit assigned."""
    anthony = {
        "user_info": _mongo_user(
            user_id="anthony-id",
            email="anthony@eastgateresidences.com.au",
            full_name="Anthony McDonald",
            unit_number="",
            role="ec_member",
        )
    }
    owner_map = {
        "UA063": {
            "owner_name": "Anthony McDonald",
            "owner_email": "anthony.personal@gmail.com",
            "owner_name_b": None,
            "owner_email_b": None,
        }
    }

    mock_db = _build_mock_db([anthony], units_docs=[{"lot_number": "LOT63", "unit_number": "UA063"}])
    perms = MagicMock(can_manage_users=True)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value=owner_map)), \
            patch(
                "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                AsyncMock(return_value=[]),
            ), \
            _force_mongo_identity_source():
        result = await server.get_users(
            current_user=_admin_user(),
            building_id="13195",
        )

    assert len(result) == 1
    assert result[0].unit_number == "UA063"
    assert result[0].full_name == "Anthony McDonald"


@pytest.mark.asyncio
async def test_pg_only_users_are_unioned_with_user_facing_unit_number():
    """U84/U71 exist only in Postgres — they surface in the response, mapped to TH-style codes."""
    # Existing Mongo: only the system admin (irrelevant) — no Shouvik / Jaime
    mongo_rows: list[dict] = []
    owner_map = {
        "TH071": {
            "owner_name": "Lorna Stansfield",
            "owner_email": "lorna@example.com",
            "owner_name_b": None,
            "owner_email_b": None,
        },
        "TH084": {
            "owner_name": "Shouvik Aditya",
            "owner_email": "adityashouvik@gmail.com",
            "owner_name_b": "Cassandra Jane McGufficke",
            "owner_email_b": "cassandra@example.com",
        },
    }
    units_docs = [
        {"lot_number": "LOT71", "unit_number": "TH071"},
        {"lot_number": "LOT84", "unit_number": "TH084"},
    ]
    pg_rows = [
        {
            "id": "shouvik-id",
            "email": "adityashouvik@gmail.com",
            "full_name": "Shouvik Aditya",
            "role": "owner",
            "effective_role": None,
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "is_test_data": False,
            "phone": None,
            "created_at": datetime.now(timezone.utc),
            "last_login_at": None,
            "last_login_ip": None,
            "is_name_flagged": False,
            "flag_reason": None,
            "totp_enabled": False,
            "tenant_id": "tenant-1",
            "lot_numbers": ["84"],
            "relationship": "owner",
        },
        {
            "id": "jaime-id",
            "email": "jaime.a.aviles@gmail.com",
            "full_name": "Jaime Aviles",
            "role": "owner",
            "effective_role": None,
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "is_test_data": False,
            "phone": None,
            "created_at": datetime.now(timezone.utc),
            "last_login_at": None,
            "last_login_ip": None,
            "is_name_flagged": False,
            "flag_reason": None,
            "totp_enabled": False,
            "tenant_id": "tenant-1",
            "lot_numbers": ["71"],
            "relationship": "owner",  # partner-of-owner: still 'owner' in user_units
        },
    ]

    mock_db = _build_mock_db(mongo_rows, units_docs=units_docs)
    perms = MagicMock(can_manage_users=True)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value=owner_map)), \
            patch(
                "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                AsyncMock(return_value=pg_rows),
            ):
        result = await server.get_users(
            current_user=_admin_user(),
            building_id="13195",
        )

    emails = {r.email for r in result}
    assert "adityashouvik@gmail.com" in emails
    assert "jaime.a.aviles@gmail.com" in emails

    shouvik = next(r for r in result if r.email == "adityashouvik@gmail.com")
    assert shouvik.unit_number == "TH084"
    assert shouvik.full_name == "Shouvik Aditya"
    assert shouvik.owned_units == ["TH084"]
    assert shouvik.co_owner_name == "Cassandra Jane McGufficke"

    jaime = next(r for r in result if r.email == "jaime.a.aviles@gmail.com")
    assert jaime.unit_number == "TH071"
    assert jaime.full_name == "Jaime Aviles"


@pytest.mark.asyncio
async def test_system_cutover_account_is_hidden_from_union():
    """system-cutover+13195@system.strataos.local must NOT appear in /users."""
    pg_rows = [
        {
            "id": "sys-id",
            "email": "system-cutover+13195@system.strataos.local",
            "full_name": "System Financial Cutover",
            "role": "strata_manager",
            "effective_role": None,
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "is_test_data": False,
            "phone": None,
            "created_at": datetime.now(timezone.utc),
            "last_login_at": None,
            "last_login_ip": None,
            "is_name_flagged": False,
            "flag_reason": None,
            "totp_enabled": False,
            "tenant_id": "tenant-1",
            "lot_numbers": [],
            "relationship": None,
        }
    ]

    mock_db = _build_mock_db([], units_docs=[])
    perms = MagicMock(can_manage_users=True)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
            patch(
                "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                AsyncMock(return_value=pg_rows),
            ):
        result = await server.get_users(
            current_user=_admin_user(),
            building_id="13195",
        )

    assert result == []


@pytest.mark.asyncio
async def test_pg_union_does_not_duplicate_users_already_in_mongo():
    """When the user exists in both, the Mongo row wins (deduped by id and email)."""
    mongo_rows = [
        {
            "user_info": _mongo_user(
                user_id="dup-id",
                email="dup@example.com",
                full_name="Mongo Name",
                unit_number="TH001",
                role="owner",
            )
        }
    ]
    pg_rows = [
        {
            "id": "dup-id",
            "email": "dup@example.com",
            "full_name": "Postgres Name",
            "role": "owner",
            "effective_role": None,
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "is_test_data": False,
            "phone": None,
            "created_at": datetime.now(timezone.utc),
            "last_login_at": None,
            "last_login_ip": None,
            "is_name_flagged": False,
            "flag_reason": None,
            "totp_enabled": False,
            "tenant_id": "tenant-1",
            "lot_numbers": ["1"],
            "relationship": "owner",
        }
    ]

    mock_db = _build_mock_db(mongo_rows, units_docs=[{"lot_number": "LOT1", "unit_number": "TH001"}])
    perms = MagicMock(can_manage_users=True)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
            patch(
                "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                AsyncMock(return_value=pg_rows),
            ), \
            _force_mongo_identity_source():
        result = await server.get_users(
            current_user=_admin_user(),
            building_id="13195",
        )

    assert len(result) == 1
    assert result[0].full_name == "Mongo Name"


@pytest.mark.asyncio
async def test_pg_union_respects_role_filter():
    """role=owner query param must filter out non-owners from the PG union too."""
    pg_rows = [
        {
            "id": "manager-pg-id",
            "email": "manager@scheme.com",
            "full_name": "PG Manager",
            "role": "strata_manager",
            "effective_role": None,
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "is_test_data": False,
            "phone": None,
            "created_at": datetime.now(timezone.utc),
            "last_login_at": None,
            "last_login_ip": None,
            "is_name_flagged": False,
            "flag_reason": None,
            "totp_enabled": False,
            "tenant_id": "tenant-1",
            "lot_numbers": [],
            "relationship": None,
        }
    ]

    mock_db = _build_mock_db([], units_docs=[])
    perms = MagicMock(can_manage_users=True)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms), \
            patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
            patch(
                "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                AsyncMock(return_value=pg_rows),
            ):
        result = await server.get_users(
            role="owner",
            current_user=_admin_user(),
            building_id="13195",
        )

    assert result == []


@pytest.mark.asyncio
async def test_403_when_user_lacks_can_manage_users():
    mock_db = _build_mock_db([], units_docs=[])
    perms = MagicMock(can_manage_users=False)

    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch.object(server, "get_user_permissions", return_value=perms):
        with pytest.raises(Exception) as ei:
            await server.get_users(
                current_user={"id": "x", "email": "x@y.com", "role": "owner"},
                building_id="13195",
            )
        assert "403" in str(ei.value) or "Not authorized" in str(ei.value)


class TestCompositionInstrumentationGating:
    """Regression tests for the 2026-07-14 fix: composition-stats recording originally had
    NO gate at all — confirmed live it fired on every single GET /users call regardless of
    any toggle or domain state (846 rows written in 15 minutes during a load test against
    identity_core, which was still mongo_primary the whole time). Now gated behind
    require_domain_source(operation='shadow_read'), matching every other shadow-read entry
    point in this rollout.
    """

    @pytest.mark.asyncio
    async def test_recording_skipped_when_shadow_not_enabled(self, _no_real_composition_writes):
        mock_db = _build_mock_db([], units_docs=[])
        perms = MagicMock(can_manage_users=True)
        decision = MagicMock(shadow_enabled=False)

        with patch.object(server, "db", mock_db), \
                patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
                patch.object(server, "get_user_permissions", return_value=perms), \
                patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
                patch(
                    "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                    AsyncMock(return_value=[]),
                ), \
                patch(
                    "services.domain_source_guard.require_domain_source",
                    AsyncMock(return_value=decision),
                ):
            await server.get_users(current_user=_admin_user(), building_id="13195")

        _no_real_composition_writes.assert_not_called()

    @pytest.mark.asyncio
    async def test_recording_fires_when_shadow_enabled(self, _no_real_composition_writes):
        mock_db = _build_mock_db([], units_docs=[])
        perms = MagicMock(can_manage_users=True)
        decision = MagicMock(shadow_enabled=True)

        with patch.object(server, "db", mock_db), \
                patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
                patch.object(server, "get_user_permissions", return_value=perms), \
                patch.object(server, "_get_all_unit_owners", AsyncMock(return_value={})), \
                patch(
                    "db_postgres.repos.identity_repo.list_active_users_for_scheme",
                    AsyncMock(return_value=[]),
                ), \
                patch(
                    "services.domain_source_guard.require_domain_source",
                    AsyncMock(return_value=decision),
                ):
            await server.get_users(current_user=_admin_user(), building_id="13195")
            await asyncio.sleep(0)  # let the scheduled asyncio.create_task run

        _no_real_composition_writes.assert_called_once()
