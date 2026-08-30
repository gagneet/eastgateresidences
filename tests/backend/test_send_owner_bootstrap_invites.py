from unittest.mock import AsyncMock, MagicMock
import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "backend" / "scripts" / "send_owner_bootstrap_invites.py"
)
_SPEC = importlib.util.spec_from_file_location("send_owner_bootstrap_invites", _SCRIPT_PATH)
send_script = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(send_script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _mock_db(invites):
    db = MagicMock()
    db.owner_invites.find = MagicMock(return_value=_cursor(invites))
    db.owner_invites.update_one = AsyncMock()
    db.password_resets.insert_one = AsyncMock()
    db.users.update_one = AsyncMock()
    return db


_ONE_INVITE = [
    {
        "id": "invite-1",
        "building_id": "13195",
        "user_id": "user-1",
        "unit_number": "UA070",
        "full_name": "Mr Lloyd Taylor",
        "email": "lloyd_5293@hotmail.com",
        "status": "pending",
    }
]


@pytest.mark.asyncio
async def test_dry_run_makes_no_writes_and_no_email_calls(monkeypatch):
    db = _mock_db(_ONE_INVITE)
    monkeypatch.setattr(send_script, "db", db)
    monkeypatch.setattr(send_script, "send_email_async", AsyncMock())
    monkeypatch.setattr(send_script, "get_email_template", MagicMock(return_value=("<html>", "text")))

    result = await send_script.run("13195", False, None, None)

    assert result["send"] is False
    assert result["would_send"] == 1
    assert result["sent"] == 0
    send_script.send_email_async.assert_not_called()
    db.password_resets.insert_one.assert_not_called()
    db.users.update_one.assert_not_called()
    db.owner_invites.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_send_generates_token_emails_and_marks_sent(monkeypatch):
    db = _mock_db(_ONE_INVITE)
    monkeypatch.setattr(send_script, "db", db)
    fake_send = AsyncMock(return_value={"success": True, "provider": "resend", "id": "email-1"})
    monkeypatch.setattr(send_script, "send_email_async", fake_send)
    monkeypatch.setattr(send_script, "get_email_template", MagicMock(return_value=("<html>body</html>", "text body")))

    result = await send_script.run("13195", True, None, None)

    assert result["send"] is True
    assert result["sent"] == 1

    # Regression check for the is_active bug found in audit: routers/auth.py login
    # rejects is_active=False regardless of a successful password reset, so the
    # send step MUST flip it True — not just status="active".
    user_update = db.users.update_one.call_args[0][1]
    assert user_update["$set"]["status"] == "active"
    assert user_update["$set"]["is_active"] is True

    reset_doc = db.password_resets.insert_one.call_args[0][0]
    assert reset_doc["user_id"] == "user-1"
    assert reset_doc["building_id"] == "13195"  # password_resets is tenant-scoped, must be explicit
    assert reset_doc["used"] is False

    invite_update = db.owner_invites.update_one.call_args[0][1]
    assert invite_update["$set"]["status"] == "sent"
    assert invite_update["$set"]["sent_at"] is not None

    fake_send.assert_awaited_once()
    call_args = fake_send.call_args[0]
    assert call_args[0] == "lloyd_5293@hotmail.com"
    assert "UA070" in call_args[1]


@pytest.mark.asyncio
async def test_email_send_failure_does_not_activate_account_or_mark_sent(monkeypatch):
    """Regression test for the PR #660 review finding (Amazon Q): send_email_async
    returns {"success": False, ...} on failure rather than raising, so the account
    must NOT be activated and the invite must NOT be marked "sent" unless it actually
    succeeded — otherwise an owner ends up with an active-but-unreachable account.
    """
    db = _mock_db(_ONE_INVITE)
    monkeypatch.setattr(send_script, "db", db)
    monkeypatch.setattr(
        send_script, "send_email_async",
        AsyncMock(return_value={"success": False, "error": "No email provider configured"}),
    )
    monkeypatch.setattr(send_script, "get_email_template", MagicMock(return_value=("<html>", "text")))

    result = await send_script.run("13195", True, None, None)

    assert result["sent"] == 0
    assert result["failed"] == 1
    assert result["failed_detail"][0]["unit_number"] == "UA070"
    db.users.update_one.assert_not_called()
    db.owner_invites.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_limit_bounds_how_many_are_sent(monkeypatch):
    invites = [{**_ONE_INVITE[0], "id": f"invite-{i}", "user_id": f"user-{i}", "unit_number": f"UA0{i}"} for i in range(5)]
    db = _mock_db(invites)
    monkeypatch.setattr(send_script, "db", db)
    monkeypatch.setattr(send_script, "send_email_async", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(send_script, "get_email_template", MagicMock(return_value=("<html>", "text")))

    result = await send_script.run("13195", True, None, 2)

    assert result["sent"] == 2
    assert db.password_resets.insert_one.call_count == 2


@pytest.mark.asyncio
async def test_unit_number_filter_is_applied_to_query(monkeypatch):
    db = _mock_db([])
    monkeypatch.setattr(send_script, "db", db)

    await send_script.run("13195", False, "TH072", None)

    called_query = db.owner_invites.find.call_args[0][0]
    assert called_query["unit_number"] == "TH072"
    assert called_query["building_id"] == "13195"
    assert called_query["status"] == "pending"


@pytest.mark.asyncio
async def test_skips_invite_missing_email_or_user_id(monkeypatch):
    invites = [
        {"id": "invite-bad", "unit_number": "UA001", "user_id": None, "email": "x@example.com", "full_name": "X"},
        {"id": "invite-bad2", "unit_number": "UA002", "user_id": "user-2", "email": None, "full_name": "Y"},
    ]
    db = _mock_db(invites)
    monkeypatch.setattr(send_script, "db", db)
    monkeypatch.setattr(send_script, "send_email_async", AsyncMock())
    monkeypatch.setattr(send_script, "get_email_template", MagicMock(return_value=("<html>", "text")))

    result = await send_script.run("13195", True, None, None)

    assert result["sent"] == 0
    send_script.send_email_async.assert_not_called()
