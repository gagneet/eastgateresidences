"""Tests for routers/levy_reminders.py — GAP-FIN-012."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class _AsyncIter:
    def __init__(self, items):
        self._it = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


BUILDING_A = "16244"
BUILDING_B = "13195"

_MANAGER = {"id": "mgr-1", "role": "strata_manager", "full_name": "Test Manager"}
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}

_DEFAULT_DOC = {
    "building_id": BUILDING_A,
    "enabled": True,
    "pre_due_days": [14, 7, 3],
    "overdue_days": [1, 7, 14, 30],
    "overdue_threshold_cents": 100,
    "channels": {"email": True, "in_app": True, "sms": False},
    "max_escalation_tier": 4,
    "updated_at": "2026-04-01T00:00:00+00:00",
    "updated_by": "mgr-1",
}


def _make_db(settings_doc=None, ledger_docs=None, audit_docs=None):
    db = MagicMock()

    db.levy_reminder_settings.find_one = AsyncMock(return_value=settings_doc)
    db.levy_reminder_settings.update_one = AsyncMock(return_value=MagicMock(matched_count=1, upserted_id=None))
    db.settings.find_one = AsyncMock(return_value=None)
    db.settings.update_one = AsyncMock(return_value=MagicMock(matched_count=1, upserted_id=None))

    ledger_cursor = MagicMock()
    ledger_cursor.to_list = AsyncMock(return_value=ledger_docs or [])
    db.unit_levy_ledger.find = MagicMock(return_value=ledger_cursor)

    db.audit_logs.count_documents = AsyncMock(return_value=len(audit_docs or []))
    audit_cursor = MagicMock()
    audit_cursor.__aiter__ = lambda self=None: _AsyncIter(audit_docs or [])
    db.audit_logs.find = MagicMock(return_value=MagicMock(
        sort=MagicMock(return_value=MagicMock(
            skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=audit_cursor)))
        ))
    ))

    return db


@pytest.mark.asyncio
async def test_get_settings_returns_defaults_when_not_configured():
    from routers.levy_reminders import get_reminder_settings
    db = _make_db(settings_doc=None)
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER), \
            patch(
                "services.levy_reminder_settings_service._get_postgres_levy_reminder_settings",
                new=AsyncMock(return_value=None),
            ):
        result = await get_reminder_settings(building_id=BUILDING_A, current_user=_MANAGER)
    assert result["enabled"] is True
    assert result["is_default"] is True
    assert result["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_get_settings_returns_db_doc_when_configured():
    from routers.levy_reminders import get_reminder_settings
    db = _make_db(settings_doc={**_DEFAULT_DOC, "_id": "x"})
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER):
        result = await get_reminder_settings(building_id=BUILDING_A, current_user=_MANAGER)
    assert result["is_default"] is False
    assert result["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_update_settings_upserts_building_id():
    from routers.levy_reminders import update_reminder_settings, LevyReminderSettingsUpdate
    db = _make_db()
    payload = LevyReminderSettingsUpdate(enabled=False, overdue_threshold_cents=500)
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER), \
            patch("routers.levy_reminders.create_audit_log", new_callable=AsyncMock), \
            patch(
                "services.levy_reminder_settings_service._get_postgres_levy_reminder_settings",
                new=AsyncMock(return_value=None),
            ), \
            patch(
                "services.levy_reminder_settings_service.config_repo.upsert_building_setting",
                new=AsyncMock(return_value=None),
            ):
        result = await update_reminder_settings(
            payload=payload, building_id=BUILDING_A, current_user=_MANAGER
        )
    assert result["building_id"] == BUILDING_A
    call_args = db.levy_reminder_settings.update_one.call_args
    filter_doc = call_args[0][0]
    assert filter_doc["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_update_settings_rejects_empty_payload():
    from routers.levy_reminders import update_reminder_settings, LevyReminderSettingsUpdate
    from fastapi import HTTPException
    db = _make_db()
    payload = LevyReminderSettingsUpdate()  # all None
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER):
        with pytest.raises(HTTPException) as exc:
            await update_reminder_settings(payload=payload, building_id=BUILDING_A, current_user=_MANAGER)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_trigger_dry_run_returns_overdue_units():
    from routers.levy_reminders import trigger_reminder_run
    ledger = [
        {"unit_number": "5", "admin_balance": 500.0, "sinking_balance": 250.0, "building_id": BUILDING_A},
        {"unit_number": "6", "admin_balance": 0.0, "sinking_balance": 0.5, "building_id": BUILDING_A},
    ]
    db = _make_db(settings_doc=_DEFAULT_DOC, ledger_docs=ledger)
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER), \
            patch("routers.levy_reminders.create_audit_log", new_callable=AsyncMock):
        result = await trigger_reminder_run(dry_run=True, building_id=BUILDING_A, current_user=_MANAGER)
    # unit 5 has 75000 cents outstanding, unit 6 has 50 cents (below threshold 100)
    assert result["dry_run"] is True
    assert result["overdue_unit_count"] == 1
    assert result["overdue_units"][0]["unit_number"] == "5"


@pytest.mark.asyncio
async def test_trigger_respects_building_isolation():
    """Ledger query must be scoped to building_id."""
    from routers.levy_reminders import trigger_reminder_run
    db = _make_db(settings_doc=_DEFAULT_DOC, ledger_docs=[])
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER), \
            patch("routers.levy_reminders.create_audit_log", new_callable=AsyncMock):
        await trigger_reminder_run(dry_run=True, building_id=BUILDING_A, current_user=_MANAGER)
    call_args = db.unit_levy_ledger.find.call_args
    query = call_args[0][0]
    assert query["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_owner_role_raises_403():
    from routers.levy_reminders import get_reminder_settings
    from fastapi import HTTPException
    db = _make_db()
    with patch("routers.levy_reminders._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await get_reminder_settings(building_id=BUILDING_A, current_user=_OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_reminder_log_paginates():
    from routers.levy_reminders import list_reminder_log
    db = _make_db(audit_docs=[])
    with patch("routers.levy_reminders._get_db", return_value=db), \
            patch("routers.levy_reminders._require_manager", return_value=_MANAGER):
        result = await list_reminder_log(page=1, page_size=10, building_id=BUILDING_A, current_user=_MANAGER)
    assert "data" in result
    assert result["page"] == 1
    assert result["page_size"] == 10


@pytest.mark.asyncio
async def test_reminder_log_scoped_to_building():
    """audit_logs query must include building_id — no cross-building leakage."""
    from routers.levy_reminders import list_reminder_log
    db = _make_db(audit_docs=[])
    with patch("routers.levy_reminders._get_db", return_value=db):
        await list_reminder_log(page=1, page_size=20, building_id=BUILDING_A, current_user=_MANAGER)
    count_query = db.audit_logs.count_documents.call_args[0][0]
    assert count_query["building_id"] == BUILDING_A
    assert count_query["building_id"] != BUILDING_B


@pytest.mark.asyncio
async def test_reminder_log_action_filter():
    """Log query must restrict to levy_reminder_triggered and levy_reminder_sent actions only."""
    from routers.levy_reminders import list_reminder_log
    db = _make_db(audit_docs=[])
    with patch("routers.levy_reminders._get_db", return_value=db):
        await list_reminder_log(page=1, page_size=20, building_id=BUILDING_A, current_user=_MANAGER)
    count_query = db.audit_logs.count_documents.call_args[0][0]
    assert "$in" in count_query["action"]
    assert "levy_reminder_triggered" in count_query["action"]["$in"]
    assert "levy_reminder_sent" in count_query["action"]["$in"]


@pytest.mark.asyncio
async def test_owner_cannot_access_reminder_log():
    """Owner role must not access the reminder log (manager-only endpoint)."""
    from routers.levy_reminders import list_reminder_log
    from fastapi import HTTPException
    db = _make_db()
    with patch("routers.levy_reminders._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await list_reminder_log(page=1, page_size=20, building_id=BUILDING_A, current_user=_OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner_on_log():
    """effective_role='strata_manager' on a raw-owner user must grant log access."""
    from routers.levy_reminders import list_reminder_log
    elevated_owner = {"id": "eo-1", "role": "owner", "effective_role": "strata_manager"}
    db = _make_db(audit_docs=[])
    with patch("routers.levy_reminders._get_db", return_value=db):
        result = await list_reminder_log(page=1, page_size=20, building_id=BUILDING_A, current_user=elevated_owner)
    assert "data" in result


@pytest.mark.asyncio
async def test_effective_role_blocks_downgraded_manager_on_log():
    """effective_role='guest' on a raw-manager user must be blocked."""
    from routers.levy_reminders import list_reminder_log
    from fastapi import HTTPException
    downgraded = {"id": "dm-1", "role": "strata_manager", "effective_role": "guest"}
    db = _make_db()
    with patch("routers.levy_reminders._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await list_reminder_log(page=1, page_size=20, building_id=BUILDING_A, current_user=downgraded)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner_on_settings():
    """effective_role='ec_member' on a raw-owner user must grant settings access."""
    from routers.levy_reminders import get_reminder_settings
    elevated_owner = {"id": "eo-2", "role": "owner", "effective_role": "ec_member"}
    db = _make_db(settings_doc=_DEFAULT_DOC)
    with patch("routers.levy_reminders._get_db", return_value=db):
        result = await get_reminder_settings(building_id=BUILDING_A, current_user=elevated_owner)
    assert result["building_id"] == BUILDING_A
