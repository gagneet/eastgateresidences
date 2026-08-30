# Tests for repair_orphaned_owner_links_20260820.py — restoring the deleted user account
# behind an active but orphaned user_units owner link (East Gate UA038, 2026-08-20).
#
# The identity is reconstructed from the building's own records, so the tests focus on
# where the evidence comes from and, just as importantly, on the cases the script must
# REFUSE rather than guess.
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "repair_orphaned_owner_links_20260820.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "repair_orphaned_owner_links_20260820", _SCRIPT_PATH
)
script = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(script)


UA038_LINK = {
    "id": "c255b4d2",
    "user_id": "13f13588",
    "unit_number": "UA038",
    "is_primary": True,
    "start_date": "2020-12-01",
}


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


class _RawDb(dict):
    """In-memory stand-in for ``db._db`` so no test touches a real database."""


def _install_db(monkeypatch, *, links, users, units, transfers, active_link_count=1):
    raw = _RawDb()

    raw["user_units"] = MagicMock()
    raw["user_units"].find = MagicMock(return_value=_cursor(links))
    raw["user_units"].count_documents = AsyncMock(return_value=active_link_count)

    raw["users"] = MagicMock()
    raw["users"].find = MagicMock(return_value=_cursor(users))
    raw["users"].insert_one = AsyncMock()

    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(return_value=units)

    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(return_value=_cursor(transfers))
    raw["owner_transfer_requests"].find_one = AsyncMock(return_value=None)
    raw["owner_transfer_requests"].insert_one = AsyncMock()

    raw["memberships"] = MagicMock()
    raw["memberships"].find_one = AsyncMock(return_value=None)
    raw["memberships"].insert_one = AsyncMock()
    raw["memberships"].update_one = AsyncMock()

    mock_db = MagicMock()
    mock_db._db = raw
    # ensure_owner_membership reads collections off the db object itself.
    mock_db.memberships = raw["memberships"]
    monkeypatch.setattr(script, "db", mock_db)
    return raw


@pytest.mark.asyncio
async def test_restores_identity_from_approved_transfer_email(monkeypatch):
    """UA038: the approved transfer's new_owner.email matches units.owner_email."""
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],  # the linked user row is gone — that is the orphan
        units={
            "owner_name": "Alyx Ashley Ford",
            "owner_email": "ford.alyx23@gmail.com",
            "owner_name_b": "Isabella Celeste Lomax",
            "owner_email_b": "",
        },
        transfers=[
            {
                "id": "fd5936a4",
                "status": "approved",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "ford.alyx23@gmail.com"},
            }
        ],
        active_link_count=1,
    )

    result = await script.run("13195", apply=True)

    assert result["orphaned_links_found"] == 1
    entry = result["repaired"][0]
    assert entry["repaired"] is True
    assert entry["full_name"] == "Alyx Ashley Ford"
    assert entry["basis"] == "approved_transfer_email_matches_unit_owner_email"

    user = raw["users"].insert_one.call_args[0][0]
    # Original id: recreating it re-resolves notifications that still point at it.
    assert user["id"] == "13f13588"
    assert user["full_name"] == "Alyx Ashley Ford"
    assert user["email"] == "ford.alyx23@gmail.com"
    assert user["building_id"] == "13195"
    # Identity restored, access NOT restored.
    assert user["is_active"] is False
    assert user["is_approved"] is False
    assert user["requires_account_setup"] is True
    assert user["restored_from_orphaned_link"] is True

    # The existing link is history — it must not be rewritten.
    assert not hasattr(raw["user_units"], "update_one") or not raw["user_units"].update_one.called

    audit = raw["owner_transfer_requests"].insert_one.call_args[0][0]
    assert audit["source"] == script.ORPHAN_LINK_REPAIR_SOURCE
    assert audit["status"] == "approved"
    assert audit["old_owners"] == []
    assert audit["building_id"] == "13195"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch):
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],
        units={"owner_name": "Alyx Ashley Ford", "owner_email": "ford.alyx23@gmail.com"},
        transfers=[
            {
                "id": "fd5936a4",
                "status": "approved",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "ford.alyx23@gmail.com"},
            }
        ],
    )

    result = await script.run("13195", apply=False)

    assert result["repaired"][0]["would_repair"] is True
    raw["users"].insert_one.assert_not_awaited()
    raw["owner_transfer_requests"].insert_one.assert_not_awaited()
    raw["memberships"].insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_links_are_not_touched(monkeypatch):
    """A link whose user row exists is not an orphan."""
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[{"id": "13f13588"}],
        units={"owner_name": "Alyx Ashley Ford", "owner_email": "ford.alyx23@gmail.com"},
        transfers=[],
    )

    result = await script.run("13195", apply=True)

    assert result["orphaned_links_found"] == 0
    assert result["repaired"] == []
    raw["users"].insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_when_two_owner_names_and_no_transfer_evidence(monkeypatch):
    """Which of the two names the orphan was is unknowable — never guess."""
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],
        units={
            "owner_name": "Alyx Ashley Ford",
            "owner_email": "",
            "owner_name_b": "Isabella Celeste Lomax",
            "owner_email_b": "",
        },
        transfers=[],
    )

    result = await script.run("13195", apply=True)

    assert result["repaired"] == []
    entry = result["skipped_needs_manual_review"][0]
    assert entry["reason"] == "ambiguous_no_transfer_evidence"
    assert entry["imported_owner_names"] == ["Alyx Ashley Ford", "Isabella Celeste Lomax"]
    raw["users"].insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_when_transfer_email_matches_no_unit_owner(monkeypatch):
    """Conflicting evidence must stop the repair, not pick a winner."""
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],
        units={
            "owner_name": "Alyx Ashley Ford",
            "owner_email": "ford.alyx23@gmail.com",
            "owner_name_b": "Isabella Celeste Lomax",
            "owner_email_b": "lomax@example.com",
        },
        transfers=[
            {
                "id": "fd5936a4",
                "status": "approved",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "someone.else@example.com"},
            }
        ],
    )

    result = await script.run("13195", apply=True)

    assert result["repaired"] == []
    assert (
        result["skipped_needs_manual_review"][0]["reason"]
        == "transfer_email_matches_no_unit_owner_email"
    )
    raw["users"].insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_unapproved_transfer_is_not_treated_as_evidence(monkeypatch):
    """A pending/rejected request never established the identity — ignore it."""
    _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],
        units={
            "owner_name": "Alyx Ashley Ford",
            "owner_email": "ford.alyx23@gmail.com",
            "owner_name_b": "Isabella Celeste Lomax",
            "owner_email_b": "lomax@example.com",
        },
        transfers=[
            {
                "id": "pending-1",
                "status": "pending",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "ford.alyx23@gmail.com"},
            }
        ],
    )

    result = await script.run("13195", apply=True)

    assert result["repaired"] == []
    assert result["skipped_needs_manual_review"][0]["reason"] == "ambiguous_no_transfer_evidence"


@pytest.mark.asyncio
async def test_sole_owner_name_with_sole_link_is_unambiguous(monkeypatch):
    """One imported name, one active link, no transfer record — the mapping is certain."""
    raw = _install_db(
        monkeypatch,
        links=[{**UA038_LINK, "unit_number": "UA070"}],
        users=[],
        units={"owner_name": "Mr Lloyd Taylor", "owner_email": "lloyd@example.com"},
        transfers=[],
        active_link_count=1,
    )

    result = await script.run("13195", apply=True)

    entry = result["repaired"][0]
    assert entry["basis"] == "sole_imported_owner_name_and_sole_active_link"
    assert raw["users"].insert_one.call_args[0][0]["full_name"] == "Mr Lloyd Taylor"


@pytest.mark.asyncio
async def test_sole_owner_name_but_two_active_links_is_ambiguous(monkeypatch):
    """A second active link means the sole name may already belong to the other link."""
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[{"id": "someone-else"}],
        units={"owner_name": "Mr Lloyd Taylor", "owner_email": "lloyd@example.com"},
        transfers=[],
        active_link_count=2,
    )

    result = await script.run("13195", apply=True)

    assert result["repaired"] == []
    assert result["skipped_needs_manual_review"][0]["reason"] == "ambiguous_no_transfer_evidence"
    raw["users"].insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_scoped_to_building_id(monkeypatch):
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],
        units={"owner_name": "Alyx Ashley Ford", "owner_email": "ford.alyx23@gmail.com"},
        transfers=[
            {
                "id": "fd5936a4",
                "status": "approved",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "ford.alyx23@gmail.com"},
            }
        ],
    )

    await script.run("UP-DEMO-001", apply=True)

    assert raw["user_units"].find.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert raw["units"].find_one.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert raw["owner_transfer_requests"].find.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert raw["users"].insert_one.call_args[0][0]["building_id"] == "UP-DEMO-001"


@pytest.mark.asyncio
async def test_unit_filter_limits_scope(monkeypatch):
    _install_db(
        monkeypatch,
        links=[UA038_LINK, {**UA038_LINK, "unit_number": "UA099", "user_id": "other"}],
        users=[],
        units={"owner_name": "Alyx Ashley Ford", "owner_email": "ford.alyx23@gmail.com"},
        transfers=[
            {
                "id": "fd5936a4",
                "status": "approved",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "ford.alyx23@gmail.com"},
            }
        ],
    )

    result = await script.run("13195", apply=False, unit_number="UA038")

    assert result["orphaned_links_found"] == 1
    assert result["repaired"][0]["unit_number"] == "UA038"


@pytest.mark.asyncio
async def test_audit_row_is_not_duplicated_if_the_same_user_is_restored_twice(monkeypatch):
    """The audit id is deterministic; a second restoration records one entry, not a pair."""
    raw = _install_db(
        monkeypatch,
        links=[UA038_LINK],
        users=[],
        units={"owner_name": "Alyx Ashley Ford", "owner_email": "ford.alyx23@gmail.com"},
        transfers=[
            {
                "id": "fd5936a4",
                "status": "approved",
                "created_at": "2026-04-23T14:14:24+00:00",
                "new_owner": {"user_id": "13f13588", "email": "ford.alyx23@gmail.com"},
            }
        ],
    )
    raw["owner_transfer_requests"].find_one = AsyncMock(
        return_value={"id": f"{script.ORPHAN_LINK_REPAIR_SOURCE}:13f13588"}
    )

    result = await script.run("13195", apply=True)

    entry = result["repaired"][0]
    assert entry["repaired"] is True
    assert entry["audit_already_recorded"] is True
    raw["owner_transfer_requests"].insert_one.assert_not_awaited()
    # The account itself is still restored — only the duplicate audit row is skipped.
    raw["users"].insert_one.assert_awaited_once()
