from unittest.mock import AsyncMock, MagicMock
import importlib.util
from pathlib import Path

import pytest

try:
    from backend.services.ownership_transfer_detection_service import (
        create_initial_ownership_link,
        create_owner_bootstrap_invite,
    )
except ImportError:
    from services.ownership_transfer_detection_service import (
        create_initial_ownership_link,
        create_owner_bootstrap_invite,
    )


_REPAIR_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "bootstrap_initial_owner_links_20260819.py"
)
_REPAIR_SPEC = importlib.util.spec_from_file_location(
    "bootstrap_initial_owner_links_20260819", _REPAIR_SCRIPT_PATH
)
repair_script = importlib.util.module_from_spec(_REPAIR_SPEC)
assert _REPAIR_SPEC and _REPAIR_SPEC.loader
_REPAIR_SPEC.loader.exec_module(repair_script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _mock_db():
    db = MagicMock()
    for name in ["user_units", "users", "units", "owner_transfer_requests", "memberships", "strata_owners", "owner_invites"]:
        setattr(db, name, MagicMock())
    db.user_units.find_one = AsyncMock(return_value=None)
    db.user_units.insert_one = AsyncMock()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock()
    db.memberships.find_one = AsyncMock(return_value=None)
    db.memberships.insert_one = AsyncMock()
    db.memberships.update_one = AsyncMock()
    db.strata_owners.update_one = AsyncMock()
    db.owner_invites.find_one = AsyncMock(return_value=None)
    db.owner_invites.insert_one = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_bootstraps_new_owner_when_no_canonical_link_exists():
    db = _mock_db()

    result = await create_initial_ownership_link(
        db, "13195", "UA070", "Mr Lloyd Taylor", "lloyd_5293@hotmail.com",
        detected_at="2026-08-19T00:00:00+00:00",
    )

    assert result["created"] is True
    assert len(result["user_ids"]) == 1

    created_user = db.users.insert_one.call_args[0][0]
    assert created_user["email"] == "lloyd_5293@hotmail.com"
    assert created_user["is_active"] is False
    assert created_user["is_internal_contact_email"] is False

    link = db.user_units.insert_one.call_args[0][0]
    assert link["unit_number"] == "UA070"
    assert link["role_at_unit"] == "owner"
    assert link["is_active"] is True
    assert link["is_primary"] is True

    audit = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert audit["source"] == "initial_ownership_bootstrap"
    assert audit["status"] == "approved"
    assert audit["old_owners"] == []
    assert audit["new_owner"]["email"] == "lloyd_5293@hotmail.com"


@pytest.mark.asyncio
async def test_bootstraps_co_owners_with_second_owner_not_primary():
    db = _mock_db()

    result = await create_initial_ownership_link(
        db, "13195", "UA031",
        ["Dylan Martin Ashfield", "Brooke Louise Green"],
        [None, None],
        detected_at="2026-08-19T00:00:00+00:00",
    )

    assert result["created"] is True
    assert len(result["user_ids"]) == 2

    links = [call.args[0] for call in db.user_units.insert_one.call_args_list]
    assert links[0]["is_primary"] is True
    assert links[1]["is_primary"] is False

    users = [call.args[0] for call in db.users.insert_one.call_args_list]
    assert all(u["is_internal_contact_email"] is True for u in users)
    assert all(u["email"].endswith("@strataos.local") for u in users)


@pytest.mark.asyncio
async def test_co_owners_sharing_one_household_email_get_distinct_accounts():
    """Regression test for the 2026-08-19 East Gate bootstrap bug.

    owner_name/owner_name_b are two real, distinct co-owners (e.g. spouses)
    who share one household contact email in the legacy import
    (owner_email == owner_email_b). Deduping the provisional account lookup
    on email alone wrongly collapsed them into a single user_units link —
    confirmed live on UA013/UA015/UA045/UA054 and repaired via
    repair_duplicate_bootstrap_co_owner_links_20260819.py. Each co-owner must
    get their own account and their own user_units row.
    """
    db = _mock_db()

    result = await create_initial_ownership_link(
        db, "13195", "UA013",
        ["Holly Elizabeth Gregson", "Ms Kinjalben Vekariya"],
        ["sanket_9377@yahoo.co.in", "sanket_9377@yahoo.co.in"],
        detected_at="2026-08-19T00:00:00+00:00",
    )

    assert result["created"] is True
    assert len(result["user_ids"]) == 2
    assert result["user_ids"][0] != result["user_ids"][1]

    users = [call.args[0] for call in db.users.insert_one.call_args_list]
    assert len(users) == 2
    assert {u["full_name"] for u in users} == {"Holly Elizabeth Gregson", "Ms Kinjalben Vekariya"}
    assert all(u["email"] == "sanket_9377@yahoo.co.in" for u in users)

    links = [call.args[0] for call in db.user_units.insert_one.call_args_list]
    assert links[0]["user_id"] != links[1]["user_id"]
    assert links[0]["is_primary"] is True
    assert links[1]["is_primary"] is False


@pytest.mark.asyncio
async def test_refuses_when_owner_already_canonical():
    db = _mock_db()
    db.user_units.find_one = AsyncMock(return_value={"id": "existing-link"})

    result = await create_initial_ownership_link(
        db, "13195", "TH078", "Someone New", None,
    )

    assert result == {"created": False, "reason": "owner_already_canonical"}
    db.users.insert_one.assert_not_called()
    db.user_units.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_pending_transfer_request_exists():
    db = _mock_db()
    db.owner_transfer_requests.find_one = AsyncMock(
        return_value={"id": "req-1", "source": "external_ledger_owner_name_drift"}
    )

    result = await create_initial_ownership_link(
        db, "13195", "UA029", "Some Name", None,
    )

    assert result["created"] is False
    assert result["reason"] == "pending_transfer_request_exists"
    assert result["id"] == "req-1"
    db.users.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_makes_no_writes():
    db = _mock_db()

    result = await create_initial_ownership_link(
        db, "13195", "UA070", "Mr Lloyd Taylor", "lloyd_5293@hotmail.com", dry_run=True,
    )

    assert result["created"] is False
    assert result["would_create"] is True
    assert result["has_email"] is True
    db.users.insert_one.assert_not_called()
    db.user_units.insert_one.assert_not_called()
    db.owner_transfer_requests.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_no_owner_name_returns_reason():
    db = _mock_db()

    result = await create_initial_ownership_link(db, "13195", "UA999", [], None)

    assert result == {"created": False, "reason": "no_owner_name"}


@pytest.mark.asyncio
async def test_create_owner_bootstrap_invite_creates_pending_record():
    db = _mock_db()

    result = await create_owner_bootstrap_invite(
        db, "13195", "user-1", "UA070", "Mr Lloyd Taylor", "lloyd@example.com", "2026-08-19T00:00:00+00:00",
    )

    assert result["created"] is True
    invite = db.owner_invites.insert_one.call_args[0][0]
    assert invite["status"] == "pending"
    assert invite["sent_at"] is None
    assert invite["email"] == "lloyd@example.com"
    assert invite["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_create_owner_bootstrap_invite_idempotent():
    db = _mock_db()
    db.owner_invites.find_one = AsyncMock(return_value={"id": "existing-invite", "status": "pending"})

    result = await create_owner_bootstrap_invite(
        db, "13195", "user-1", "UA070", "Mr Lloyd Taylor", "lloyd@example.com", "2026-08-19T00:00:00+00:00",
    )

    assert result == {"created": False, "reason": "invite_already_exists", "id": "existing-invite", "status": "pending"}
    db.owner_invites.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_repair_script_dry_run_scans_units_and_reports_buckets(monkeypatch):
    raw_units = {
        "units": [
            {"unit_number": "UA070", "owner_name": "Mr Lloyd Taylor", "owner_email": "lloyd@example.com"},
            {"unit_number": "UA019", "owner_name": "Mr Niran Poglobe Karaeni", "owner_email": None},
        ]
    }

    class _RawCollection:
        def __init__(self, rows):
            self._rows = rows

        def find(self, *_args, **_kwargs):
            return self

        def sort(self, *_args, **_kwargs):
            return self

        async def to_list(self, *_args, **_kwargs):
            return self._rows

    class _RawDb(dict):
        pass

    raw_db = _RawDb()
    raw_db["units"] = _RawCollection(raw_units["units"])

    mock_db = MagicMock()
    mock_db._db = raw_db
    monkeypatch.setattr(repair_script, "db", mock_db)

    async def fake_create(db_like, building_id, unit_number, names, emails, **kwargs):
        return {
            "created": False,
            "would_create": True,
            "unit_number": unit_number,
            "owner_names": names if isinstance(names, list) else [names],
            "owner_email": emails[0] if isinstance(emails, list) else emails,
            "has_email": bool(emails[0] if isinstance(emails, list) else emails),
        }

    monkeypatch.setattr(repair_script, "create_initial_ownership_link", fake_create)

    result = await repair_script.run("13195", apply=False)

    assert result["scanned"] == 2
    assert result["bootstrapped"] == 2
    assert {r["unit_number"] for r in result["results"]} == {"UA070", "UA019"}
    assert result["units_with_no_owner_name"] == []
