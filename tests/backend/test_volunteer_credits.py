"""Tests for volunteer levy credit service (S4 - F3)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"


def _make_event(event_id, confirmed_participants=None):
    if confirmed_participants is None:
        confirmed_participants = [
            {"user_id": "user-001", "unit_number": "TH087", "confirmed": True, "hours_contributed": 4},
            {"user_id": "user-002", "unit_number": "TH018", "confirmed": True, "hours_contributed": 2},
        ]
    return {
        "id": event_id,
        "building_id": BUILDING_ID,
        "title": "Working Bee",
        "work_type": "gardening",
        "participants": confirmed_participants,
        "estimated_contractor_cost_cents": 60000,
        "actual_cost_cents": 0,
        "credits_applied": False,
    }


def _make_mock_db(event):
    mock_db = MagicMock()
    # Service now uses db.volunteer_events (tenant-scoped), not db._db.volunteer_events
    mock_db.volunteer_events = MagicMock()
    mock_db.volunteer_events.find_one = AsyncMock(return_value=event)
    mock_db.volunteer_events.update_one = AsyncMock()

    mock_db._db = MagicMock()

    # PyMongo: start_session() is SYNC (returns async CM directly — no await).
    # start_transaction() is a COROUTINE (requires await before async with).
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_txn = MagicMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=None)
    mock_session.start_transaction = AsyncMock(return_value=mock_txn)

    mock_db._db.client.start_session = MagicMock(return_value=mock_session)
    mock_db._db.unit_levy_ledger.update_one = AsyncMock()
    mock_db._db.journal_entries.insert_many = AsyncMock()
    mock_db._db.savings_events.insert_one = AsyncMock()
    mock_db.workflow_runs = MagicMock()
    mock_db.workflow_runs.insert_one = AsyncMock()
    return mock_db


@pytest.mark.asyncio
async def test_credit_allocation_proportional_to_hours():
    """Credits are allocated proportional to hours_contributed."""
    event = _make_event("evt-001")
    mock_db = _make_mock_db(event)

    with (
        patch("services.volunteer_credits_service.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("services.volunteer_credits_service.create_notifications_batch", new_callable=AsyncMock),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
    ):
        from services.volunteer_credits_service import apply_volunteer_credits
        result = await apply_volunteer_credits("evt-001", "admin-001")

    assert result["credits_applied"] == 2
    total_cents = result["total_credit_cents"]
    allocations = result["allocations"]

    alloc_001 = next(a for a in allocations if a["user_id"] == "user-001")
    alloc_002 = next(a for a in allocations if a["user_id"] == "user-002")
    assert alloc_001["credit_cents"] > alloc_002["credit_cents"]
    assert sum(a["credit_cents"] for a in allocations) == total_cents


@pytest.mark.asyncio
async def test_credits_applied_flag_prevents_double_application():
    """Calling apply_volunteer_credits twice raises ValueError on second call."""
    event_already_applied = {**_make_event("evt-002"), "credits_applied": True}
    mock_db = MagicMock()
    # Service now uses db.volunteer_events (tenant-scoped)
    mock_db.volunteer_events = MagicMock()
    mock_db.volunteer_events.find_one = AsyncMock(return_value=event_already_applied)
    mock_db._db = MagicMock()

    with (
        patch("services.volunteer_credits_service.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
    ):
        from services.volunteer_credits_service import apply_volunteer_credits
        with pytest.raises(ValueError, match="Credits already applied"):
            await apply_volunteer_credits("evt-002", "admin-001")


@pytest.mark.asyncio
async def test_savings_event_created_with_correct_amounts():
    """A savings_event is inserted with the correct amount_saved_cents."""
    event = _make_event("evt-003")
    mock_db = _make_mock_db(event)

    savings_insert_calls = []

    async def capture_insert(doc, *a, **kw):
        savings_insert_calls.append(doc)

    mock_db._db.savings_events.insert_one = AsyncMock(side_effect=capture_insert)

    with (
        patch("services.volunteer_credits_service.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("services.volunteer_credits_service.create_notifications_batch", new_callable=AsyncMock),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
    ):
        from services.volunteer_credits_service import apply_volunteer_credits
        await apply_volunteer_credits("evt-003", "admin-001")

    assert len(savings_insert_calls) == 1
    savings_doc = savings_insert_calls[0]
    assert savings_doc["amount_saved_cents"] == 60000
    assert savings_doc["verified"] is True
    assert savings_doc["category"] == "volunteer"


@pytest.mark.asyncio
async def test_workflow_run_written_for_volunteer_credits():
    """apply_volunteer_credits writes a workflow_run record."""
    event = _make_event("evt-004")
    mock_db = _make_mock_db(event)

    workflow_run_calls = []

    async def capture_run(doc, *a, **kw):
        workflow_run_calls.append(doc)

    mock_db.workflow_runs.insert_one = AsyncMock(side_effect=capture_run)

    with (
        patch("services.volunteer_credits_service.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("services.volunteer_credits_service.create_notifications_batch", new_callable=AsyncMock),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
    ):
        from services.volunteer_credits_service import apply_volunteer_credits
        await apply_volunteer_credits("evt-004", "admin-001")

    assert len(workflow_run_calls) == 1
    wr = workflow_run_calls[0]
    assert wr["workflow_id"] == "volunteer_credit_application"
    assert wr["status"] == "success"
    assert wr["items_processed"] == 2
