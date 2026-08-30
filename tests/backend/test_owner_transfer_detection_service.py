from unittest.mock import AsyncMock, MagicMock
import importlib.util
from pathlib import Path

import pytest

try:
    from backend.services.ownership_transfer_detection_service import (
        detect_and_create_portal_owner_transfer,
    )
except ImportError:
    from services.ownership_transfer_detection_service import (
        detect_and_create_portal_owner_transfer,
    )


_REPAIR_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "create_owner_transfer_requests_from_imported_owner_drift.py"
)
_REPAIR_SPEC = importlib.util.spec_from_file_location(
    "create_owner_transfer_requests_from_imported_owner_drift", _REPAIR_SCRIPT_PATH
)
repair_script = importlib.util.module_from_spec(_REPAIR_SPEC)
assert _REPAIR_SPEC and _REPAIR_SPEC.loader
_REPAIR_SPEC.loader.exec_module(repair_script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _mock_db():
    db = MagicMock()
    for name in [
        "user_units",
        "users",
        "units",
        "owner_transfer_requests",
        "memberships",
        "user_notifications",
    ]:
        setattr(db, name, MagicMock())
    return db


class _RawDb(dict):
    """Dictionary-backed raw db adapter for script tests.

    The repair script reads through ``db._db['collection']``. Keeping this as a
    small in-memory adapter prevents any production DB writes and makes cleanup
    unnecessary.
    """


@pytest.mark.asyncio
async def test_detects_imported_owner_name_drift_and_creates_pending_transfer():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.side_effect = [
        _cursor([
            {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
            {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
        ]),
        _cursor([
            {"id": "manager"},
            {"id": "chair"},
        ]),
    ]
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.memberships.find.return_value = _cursor([
        {"user_id": "manager"},
        {"user_id": "chair"},
    ])
    db.user_notifications.insert_many = AsyncMock()

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer & Mark Raets",
        detected_at="2026-07-02T00:00:00+00:00",
    )

    assert result["created"] is True
    transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert transfer["unit_number"] == "TH078"
    assert transfer["new_owner"]["full_name"] == "Tavis Christian Hamer"
    assert [owner["full_name"] for owner in transfer["old_owners"]] == ["Olivia Rollings", "Mark Raets"]
    assert transfer["suggested_remove_owner_ids"] == ["olivia"]
    assert transfer["status"] == "pending"
    assert transfer["source"] == "external_ledger_owner_name_drift"

    inserted_user = db.users.insert_one.call_args[0][0]
    assert inserted_user["full_name"] == "Tavis Christian Hamer"
    assert inserted_user["is_internal_contact_email"] is True
    assert inserted_user["is_active"] is False

    notifications = db.user_notifications.insert_many.call_args[0][0]
    assert {notification["user_id"] for notification in notifications} == {"manager", "chair"}


@pytest.mark.asyncio
async def test_single_imported_primary_replaces_existing_owner_set():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.side_effect = [
        _cursor([
            {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
            {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
        ]),
        _cursor([]),
    ]
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.memberships.find.return_value = _cursor([])

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer",
    )

    assert result["created"] is True
    transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert transfer["portal_detected_raw_owner_names"] == ["Tavis Christian Hamer"]
    assert transfer["portal_detected_owner_names"] == ["Tavis Christian Hamer"]
    assert set(transfer["suggested_remove_owner_ids"]) == {"olivia", "mark"}


@pytest.mark.asyncio
async def test_does_not_create_when_imported_names_match_current_owners():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.return_value = _cursor([
        {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
        {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
    ])

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Olivia Rollings & Mark Raets",
    )

    assert result == {"created": False, "reason": "owner_names_match"}
    db.owner_transfer_requests.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_explains_matching_imported_names_without_writes():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "tavis", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.return_value = _cursor([
        {"id": "tavis", "full_name": "Tavis Christian Hamer", "email": "tavis@example.com"},
        {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
    ])

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer & Mark Raets",
        dry_run=True,
    )

    assert result == {
        "created": False,
        "reason": "owner_names_match",
        "unit_number": "TH078",
        "current_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
        "imported_raw_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
        "projected_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
    }
    db.owner_transfer_requests.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_deduplicates_existing_pending_imported_owner_transfer():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.return_value = _cursor([
        {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
        {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
    ])
    db.owner_transfer_requests.find_one = AsyncMock(return_value={"id": "existing"})

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer & Mark Raets",
    )

    assert result == {"created": False, "reason": "pending_request_exists", "id": "existing"}
    db.owner_transfer_requests.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_reports_candidate_without_writes():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.return_value = _cursor([
        {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
        {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
    ])
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer & Mark Raets",
        dry_run=True,
    )

    assert result["would_create"] is True
    assert result["new_owner_name"] == "Tavis Christian Hamer"
    assert result["imported_raw_owner_names"] == ["Tavis Christian Hamer", "Mark Raets"]
    assert result["projected_owner_names"] == ["Tavis Christian Hamer", "Mark Raets"]
    assert result["suggested_remove_owner_names"] == ["Olivia Rollings"]
    db.users.insert_one.assert_not_called()
    db.owner_transfer_requests.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_previous_owner_override_recovers_already_overwritten_baseline():
    db = _mock_db()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer",
        previous_owner_names="Olivia Rollings & Mark Raets",
        dry_run=True,
    )

    assert result["would_create"] is True
    assert result["current_owner_names"] == ["Olivia Rollings", "Mark Raets"]
    assert result["projected_owner_names"] == ["Tavis Christian Hamer"]
    assert result["suggested_remove_owner_names"] == ["Mark Raets", "Olivia Rollings"]
    db.user_units.find.assert_not_called()


@pytest.mark.asyncio
async def test_internal_contact_owner_reuse_is_idempotent():
    db = _mock_db()
    existing_contact = {
        "id": "contact-tavis",
        "email": "owner-transfer+contact-tavis@strataos.local",
        "full_name": "Tavis Christian Hamer",
        "portal_detected_owner": True,
        "portal_detected_owner_name_key": "tavis christian hamer",
    }
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
        {"user_id": "mark", "is_primary": False},
    ])
    db.users.find.side_effect = [
        _cursor([
            {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
            {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
        ]),
        _cursor([]),
    ]
    db.users.find_one = AsyncMock(return_value=existing_contact)
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.memberships.find.return_value = _cursor([])

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer",
    )

    assert result["created"] is True
    transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert transfer["new_owner"]["user_id"] == "contact-tavis"
    db.users.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_repair_script_dry_run_uses_existing_snapshot_and_override_without_writes(monkeypatch):
    raw_db = _RawDb()
    raw_db["strata_owners"] = MagicMock()
    raw_db["strata_owners"].find.return_value.sort.return_value.to_list = AsyncMock(return_value=[
        {
            "building_id": "13195",
            "unit_number": "TH078",
            "owner": "Tavis Christian Hamer",
        }
    ])

    mock_db = MagicMock()
    mock_db._db = raw_db
    monkeypatch.setattr(repair_script, "db", mock_db)

    seen = {}

    async def fake_detect(db_like, building_id, unit_number, imported_names, **kwargs):
        seen.update({
            "db_like": db_like,
            "building_id": building_id,
            "unit_number": unit_number,
            "imported_names": imported_names,
            **kwargs,
        })
        return {
            "created": False,
            "would_create": True,
            "unit_number": unit_number,
            "new_owner_name": "Tavis Christian Hamer",
        }

    monkeypatch.setattr(repair_script, "detect_and_create_portal_owner_transfer", fake_detect)

    result = await repair_script.run(
        "13195",
        apply=False,
        unit_number="TH078",
        previous_owner_names="Olivia Rollings & Mark Raets",
    )

    assert result["candidates"] == 1
    assert result["scanned"] == 1
    assert seen["db_like"] is mock_db
    assert seen["imported_names"] == "Tavis Christian Hamer"
    assert seen["dry_run"] is True
    assert seen["previous_owner_names"] == "Olivia Rollings & Mark Raets"


@pytest.mark.asyncio
async def test_repair_script_apply_false_is_idempotent_for_empty_existing_snapshot(monkeypatch):
    raw_db = _RawDb()
    raw_db["strata_owners"] = MagicMock()
    raw_db["strata_owners"].find.return_value.sort.return_value.to_list = AsyncMock(return_value=[])

    mock_db = MagicMock()
    mock_db._db = raw_db
    monkeypatch.setattr(repair_script, "db", mock_db)

    detector = AsyncMock()
    monkeypatch.setattr(repair_script, "detect_and_create_portal_owner_transfer", detector)

    result = await repair_script.run("13195", apply=False, unit_number="TH999")

    assert result == {
        "building_id": "13195",
        "unit_number": "TH999",
        "previous_owner_names_override": None,
        "apply": False,
        "scanned": 0,
        "candidates": 0,
        "co_owner_additions": [],
        "non_candidates": [],
        "results": [],
    }
    detector.assert_not_called()


@pytest.mark.asyncio
async def test_repair_script_buckets_co_owner_additions_instead_of_dropping_them(monkeypatch):
    """A joint-owner addition is not a transfer candidate, but it still needs action
    (a co-owner link), so it must be reported rather than silently discarded."""
    raw_db = _RawDb()
    raw_db["strata_owners"] = MagicMock()
    raw_db["strata_owners"].find.return_value.sort.return_value.to_list = AsyncMock(
        return_value=[
            {"unit_number": "UA046", "owner": "Marcelo Ramos da Silva & Graciela Pezaroylo Topal"},
            {"unit_number": "UA029", "owner": "Sonja Zink"},
        ]
    )

    mock_db = MagicMock()
    mock_db._db = raw_db
    monkeypatch.setattr(repair_script, "db", mock_db)

    async def fake_detect(_db, _bid, unit_number, *_args, **_kwargs):
        if unit_number == "UA046":
            return {
                "created": False,
                "reason": repair_script.CO_OWNER_ADDITION_REASON,
                "unit_number": unit_number,
                "suggested_add_owner_names": ["Graciela Pezaroylo Topal"],
            }
        return {"created": False, "would_create": True, "unit_number": unit_number}

    monkeypatch.setattr(repair_script, "detect_and_create_portal_owner_transfer", fake_detect)

    result = await repair_script.run("13195", apply=False)

    assert result["candidates"] == 1
    assert [entry["unit_number"] for entry in result["results"]] == ["UA029"]
    assert [entry["unit_number"] for entry in result["co_owner_additions"]] == ["UA046"]


@pytest.mark.asyncio
async def test_active_owner_lookup_and_dedup_check_are_scoped_to_building_id():
    """Multi-tenant isolation: both the current-owner lookup and the pending-request
    dedup check must filter on the caller's building_id, not just unit_number. Without
    this, two buildings that happen to reuse the same unit_number label (e.g. "1") could
    read or dedup against each other's owner-transfer data."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
    ])
    db.users.find.side_effect = [
        _cursor([{"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"}]),
        _cursor([]),
    ]
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.memberships.find.return_value = _cursor([])

    await detect_and_create_portal_owner_transfer(
        db,
        "16244",
        "1",
        "Tavis Christian Hamer",
    )

    user_units_filter = db.user_units.find.call_args[0][0]
    assert user_units_filter["building_id"] == "16244"
    assert user_units_filter["unit_number"] == "1"

    dedup_filter = db.owner_transfer_requests.find_one.call_args[0][0]
    assert dedup_filter["building_id"] == "16244"
    assert dedup_filter["unit_number"] == "1"

    inserted_transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert inserted_transfer["building_id"] == "16244"


@pytest.mark.asyncio
async def test_previous_owner_override_still_dedups_against_existing_pending_request():
    """Edge case: previous_owner_names overrides the *current-owner baseline* lookup
    (db.user_units.find is skipped — already covered by
    test_previous_owner_override_recovers_already_overwritten_baseline), but it must NOT
    skip the pending-request dedup check. A caller re-running detection with an override
    while a transfer for the same projected change is already pending must still get
    'pending_request_exists', not a duplicate insert."""
    db = _mock_db()
    db.owner_transfer_requests.find_one = AsyncMock(return_value={"id": "already-pending"})
    db.owner_transfer_requests.insert_one = AsyncMock()

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer",
        previous_owner_names="Olivia Rollings & Mark Raets",
    )

    assert result == {"created": False, "reason": "pending_request_exists", "id": "already-pending"}
    db.user_units.find.assert_not_called()
    db.owner_transfer_requests.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_reviewer_roles_never_contains_literal_chairman_string():
    """Regression guard: 'chairman' is not a top-level user.role value anywhere in this
    codebase (see rules/post-compact-critical.md) — a chairman is a user with
    role='ec_member' and ec_position='CHAIRMAN'. If OWNER_TRANSFER_REVIEWER_ROLES ever
    regains the literal string 'chairman', that entry can never match a real user document
    and silently does nothing — the bug is easy to reintroduce by copy-pasting a role list
    from elsewhere in the app."""
    from services.ownership_transfer_detection_service import OWNER_TRANSFER_REVIEWER_ROLES

    assert "chairman" not in OWNER_TRANSFER_REVIEWER_ROLES
    assert "ec_member" in OWNER_TRANSFER_REVIEWER_ROLES


@pytest.mark.asyncio
async def test_ec_member_chairman_is_notified_via_ec_member_role_alone():
    """A real chairman (role='ec_member', ec_position='CHAIRMAN') must still receive the
    reviewer notification purely through the 'ec_member' entry in
    OWNER_TRANSFER_REVIEWER_ROLES — proving the removal of the impossible 'chairman'
    literal does not regress real chairman notification."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([
        {"user_id": "olivia", "is_primary": True},
    ])
    db.users.find.side_effect = [
        _cursor([{"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"}]),
        _cursor([{"id": "chair-1", "role": "ec_member", "ec_position": "CHAIRMAN"}]),
    ]
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.memberships.find.return_value = _cursor([{"user_id": "chair-1"}])
    db.user_notifications.insert_many = AsyncMock()

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer",
    )

    assert result["created"] is True
    assert result["notified"] == 1
    notifications = db.user_notifications.insert_many.call_args[0][0]
    assert notifications[0]["user_id"] == "chair-1"


@pytest.mark.asyncio
async def test_repair_script_single_unit_dry_run_reports_non_candidate_reason(monkeypatch):
    raw_db = _RawDb()
    raw_db["strata_owners"] = MagicMock()
    raw_db["strata_owners"].find.return_value.sort.return_value.to_list = AsyncMock(return_value=[
        {
            "building_id": "13195",
            "unit_number": "TH078",
            "owner": "Tavis Christian Hamer",
        }
    ])

    mock_db = MagicMock()
    mock_db._db = raw_db
    monkeypatch.setattr(repair_script, "db", mock_db)

    async def fake_detect(*args, **kwargs):
        return {
            "created": False,
            "reason": "owner_names_match",
            "unit_number": "TH078",
            "current_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
            "imported_raw_owner_names": ["Tavis Christian Hamer"],
            "projected_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
        }

    monkeypatch.setattr(repair_script, "detect_and_create_portal_owner_transfer", fake_detect)

    result = await repair_script.run("13195", apply=False, unit_number="TH078")

    assert result["candidates"] == 0
    assert result["non_candidates"] == [
        {
            "created": False,
            "reason": "owner_names_match",
            "unit_number": "TH078",
            "current_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
            "imported_raw_owner_names": ["Tavis Christian Hamer"],
            "projected_owner_names": ["Tavis Christian Hamer", "Mark Raets"],
        }
    ]
