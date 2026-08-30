"""
Tests for Comms Intake Router (S1).

Tests:
  - classify_message: keyword classification
  - compute_sla_deadline: SLA timing
  - process_inbound: full intake pipeline (mock DB)
  - get_request_timeline: audit log reconstruction
  - check_sla_breaches: scheduler breach detection and notification
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_unit_doc(unit_number="TH087", balance_owing=250.0, balance_credit=0.0):
    return {
        "unit_number": unit_number,
        "balance_owing": balance_owing,
        "balance_credit": balance_credit,
        "period_levy": 1772.51,
    }


def _make_request_doc(request_id="req-001", status="in_progress", category="maintenance_request"):
    return {
        "id": request_id,
        "building_id": BUILDING_ID,
        "request_number": "REQ-2026-0001",
        "request_type": category,
        "subject": "Test request",
        "body": "Test body",
        "status": status,
        "assigned_to": "user-manager-001",
        "unit_number": "TH087",
        "sla_due_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "sla_breached": False,
        "needs_human_review": False,
        "is_test_data": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. classify_message
# ─────────────────────────────────────────────────────────────────────────────


def test_maintenance_keyword_classified_correctly():
    from services.comms_intake_service import classify_message

    category, confidence = classify_message("Urgent", "There is a water leak in the bathroom pipe")
    assert category == "maintenance_request"
    assert confidence >= 0.85


def test_levy_query_classified_from_subject():
    from services.comms_intake_service import classify_message

    category, confidence = classify_message("Levy balance query", "Can you let me know my current balance?")
    assert category == "levy_query"
    assert confidence >= 0.80


def test_noise_complaint_classified_correctly():
    from services.comms_intake_service import classify_message

    category, confidence = classify_message("Noise issue", "There is loud music and noise from the upstairs unit")
    assert category == "noise_complaint"
    assert confidence >= 0.75


def test_renovation_approval_classified_correctly():
    from services.comms_intake_service import classify_message

    category, confidence = classify_message("Renovation", "I want to renovate my kitchen and bathroom floor")
    assert category == "renovation_approval"
    assert confidence >= 0.80


def test_low_confidence_message_flagged_for_human_review():
    from services.comms_intake_service import classify_message

    # Generic query that matches the lowest-confidence rule
    category, confidence = classify_message("Question", "How do I use the portal?")
    # Should be general_enquiry and confidence < 0.70
    assert category == "general_enquiry"
    assert confidence < 0.70


def test_payment_plan_classified_correctly():
    from services.comms_intake_service import classify_message

    category, confidence = classify_message(
        "Hardship", "I am struggling financially and need a payment plan"
    )
    assert category == "payment_plan"
    assert confidence >= 0.80


# ─────────────────────────────────────────────────────────────────────────────
# 2. compute_sla_deadline
# ─────────────────────────────────────────────────────────────────────────────


def test_sla_deadline_computed_correctly_per_category():
    from services.comms_intake_service import compute_sla_deadline, DEFAULT_SLAS

    now = datetime.now(timezone.utc)

    for category, hours in DEFAULT_SLAS.items():
        deadline = compute_sla_deadline(category, now)
        expected = now + timedelta(hours=hours)
        diff = abs((deadline - expected).total_seconds())
        assert diff < 1, f"SLA for {category} off by {diff}s"


def test_sla_deadline_unknown_category_defaults_to_48h():
    from services.comms_intake_service import compute_sla_deadline

    now = datetime.now(timezone.utc)
    deadline = compute_sla_deadline("unknown_type", now)
    expected = now + timedelta(hours=48)
    diff = abs((deadline - expected).total_seconds())
    assert diff < 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. process_inbound — full intake pipeline
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_number_sequential_per_building():
    """_generate_request_number returns REQ-YYYY-{seq} using atomic counter."""
    from services.comms_intake_service import _generate_request_number

    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 42})

    number = await _generate_request_number(mock_db, BUILDING_ID)
    year = datetime.now(timezone.utc).year
    assert number == f"REQ-{year}-0042"


@pytest.mark.asyncio
async def test_acknowledgement_notification_sent_on_intake():
    """process_inbound sends a notification to the submitting user."""
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 1})
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.workflow_requests.insert_one = AsyncMock()
    mock_db.units.find_one = AsyncMock(return_value=None)

    with (
        patch("services.comms_intake_service.create_user_notification", new_callable=AsyncMock) as mock_notif,
        patch("services.comms_intake_service.create_audit_log", new_callable=AsyncMock),
        patch("services.comms_intake_service.db", mock_db),
    ):
        from services.comms_intake_service import process_inbound

        await process_inbound(
            source_channel="portal_form",
            sender_user_id="user-001",
            sender_email="owner@test.com",
            unit_number="TH087",
            subject="Broken elevator",
            body="The lift is not working since yesterday",
            building_id=BUILDING_ID,
        )

    mock_notif.assert_called_once()
    call_kwargs = mock_notif.call_args[1]
    assert call_kwargs["user_id"] == "user-001"
    assert call_kwargs["notification_type"] == "request_acknowledgement"


@pytest.mark.asyncio
async def test_process_inbound_persists_is_test_data_flag():
    """Synthetic E2E smart requests must be marked so UI queries and cleanup can hide them."""
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 1})
    mock_db.workflow_requests.insert_one = AsyncMock()

    with (
        patch("services.comms_intake_service.create_user_notification", new_callable=AsyncMock),
        patch("services.comms_intake_service.create_audit_log", new_callable=AsyncMock),
        patch("services.comms_intake_service.db", mock_db),
    ):
        from services.comms_intake_service import process_inbound

        doc = await process_inbound(
            source_channel="web_portal",
            sender_user_id="user-001",
            sender_email="owner@test.com",
            unit_number="TH087",
            subject="E2E test request",
            body="Synthetic request created by test automation",
            building_id=BUILDING_ID,
            is_test_data=True,
        )

    inserted = mock_db.workflow_requests.insert_one.call_args[0][0]
    assert doc["is_test_data"] is True
    assert inserted["is_test_data"] is True


@pytest.mark.asyncio
async def test_levy_query_auto_resolved_when_ledger_has_data():
    """A levy query with unit_number and ledger data is auto-resolved."""
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 1})
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.workflow_requests.insert_one = AsyncMock()
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 250.0})

    with (
        patch("services.comms_intake_service.create_user_notification", new_callable=AsyncMock),
        patch("services.comms_intake_service.create_audit_log", new_callable=AsyncMock),
        patch("services.comms_intake_service.db", mock_db),
    ):
        from services.comms_intake_service import process_inbound

        doc = await process_inbound(
            source_channel="portal_form",
            sender_user_id="user-001",
            sender_email="owner@test.com",
            unit_number="TH087",
            subject="How much do I owe in levies?",
            body="What is my current levy balance owing?",
            building_id=BUILDING_ID,
        )

    assert doc["auto_resolved"] is True
    assert doc["status"] == "auto_resolved"
    assert "250.00" in (doc.get("auto_resolution_response") or "")


@pytest.mark.asyncio
async def test_levy_query_not_auto_resolved_without_unit():
    """A levy query without unit_number is NOT auto-resolved."""
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 1})
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.workflow_requests.insert_one = AsyncMock()

    with (
        patch("services.comms_intake_service.create_user_notification", new_callable=AsyncMock),
        patch("services.comms_intake_service.create_audit_log", new_callable=AsyncMock),
        patch("services.comms_intake_service.db", mock_db),
    ):
        from services.comms_intake_service import process_inbound

        doc = await process_inbound(
            source_channel="portal_form",
            sender_user_id="user-001",
            sender_email="owner@test.com",
            unit_number=None,  # no unit number
            subject="Levy balance",
            body="What is my levy balance owing?",
            building_id=BUILDING_ID,
        )

    # levy_query with confidence 0.85 >= 0.80 but no unit_number → not resolved
    assert doc["request_type"] == "levy_query"
    assert doc["auto_resolved"] is False


@pytest.mark.asyncio
async def test_low_confidence_flagged_needs_human():
    """Low-confidence messages are flagged needs_human_review=True."""
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 1})
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.workflow_requests.insert_one = AsyncMock()

    with (
        patch("services.comms_intake_service.create_user_notification", new_callable=AsyncMock),
        patch("services.comms_intake_service.create_audit_log", new_callable=AsyncMock),
        patch("services.comms_intake_service.db", mock_db),
    ):
        from services.comms_intake_service import process_inbound

        doc = await process_inbound(
            source_channel="portal_form",
            sender_user_id="user-001",
            sender_email="owner@test.com",
            unit_number=None,
            subject="Question",
            body="How do I use the portal?",
            building_id=BUILDING_ID,
        )

    assert doc["needs_human_review"] is True
    assert doc["status"] == "awaiting_review"


@pytest.mark.asyncio
async def test_high_confidence_no_human_review_needed():
    """High-confidence maintenance requests are NOT flagged for human review."""
    mock_db = MagicMock()
    mock_db._db = MagicMock()
    mock_db._db.counters.find_one_and_update = AsyncMock(return_value={"seq": 1})
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.workflow_requests.insert_one = AsyncMock()

    with (
        patch("services.comms_intake_service.create_user_notification", new_callable=AsyncMock),
        patch("services.comms_intake_service.create_audit_log", new_callable=AsyncMock),
        patch("services.comms_intake_service.db", mock_db),
    ):
        from services.comms_intake_service import process_inbound

        doc = await process_inbound(
            source_channel="portal_form",
            sender_user_id="user-001",
            sender_email="owner@test.com",
            unit_number="TH087",
            subject="Leak",
            body="There is a water leak coming through my ceiling",
            building_id=BUILDING_ID,
        )

    assert doc["needs_human_review"] is False
    assert doc["request_type"] == "maintenance_request"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _format_timeline_note — human-readable note formatting
# ─────────────────────────────────────────────────────────────────────────────


def test_format_timeline_note_empty_dict_returns_empty_string():
    from services.comms_intake_service import _format_timeline_note
    assert _format_timeline_note({}) == ""


def test_format_timeline_note_basic_category():
    from services.comms_intake_service import _format_timeline_note
    note = _format_timeline_note({"category": "noise_complaint"})
    assert "noise_complaint" in note
    assert "{" not in note  # no raw Python dict repr


def test_format_timeline_note_confidence_as_percentage():
    from services.comms_intake_service import _format_timeline_note
    note = _format_timeline_note({"confidence": 0.8})
    assert "80%" in note
    assert "0.8" not in note


def test_format_timeline_note_bool_as_yes_no():
    from services.comms_intake_service import _format_timeline_note
    note = _format_timeline_note({"auto_resolved": False})
    assert "No" in note
    note2 = _format_timeline_note({"auto_resolved": True})
    assert "Yes" in note2


def test_format_timeline_note_notified_users_skipped_when_empty():
    from services.comms_intake_service import _format_timeline_note
    note = _format_timeline_note({"notified_users": [], "category": "noise_complaint"})
    assert "notified" not in note.lower()
    assert "noise_complaint" in note


def test_format_timeline_note_full_create_entry():
    from services.comms_intake_service import _format_timeline_note
    details = {
        "category": "noise_complaint",
        "confidence": 0.8,
        "auto_resolved": False,
        "source_channel": "portal_form",
    }
    note = _format_timeline_note(details)
    assert "noise_complaint" in note
    assert "80%" in note
    assert "No" in note
    assert "portal_form" in note
    assert "·" in note  # separator between parts


def test_format_timeline_note_sla_entry_skips_empty_notified_users():
    from services.comms_intake_service import _format_timeline_note
    details = {
        "sla_due_at": "2026-04-03T12:15:37+00:00",
        "category": "noise_complaint",
        "notified_users": [],
    }
    note = _format_timeline_note(details)
    assert "SLA due" in note
    assert "noise_complaint" in note
    # empty list should be omitted
    assert "notified_users" not in note


def test_format_timeline_note_no_python_repr():
    """Critical: the note must never contain Python dict/bool literals."""
    from services.comms_intake_service import _format_timeline_note
    details = {"auto_resolved": False, "category": "levy_query", "confidence": 0.75}
    note = _format_timeline_note(details)
    assert "False" not in note
    assert "True" not in note
    assert "'" not in note


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_request_timeline — note field uses _format_timeline_note
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_note_field_is_human_readable_not_python_repr():
    """Timeline note must not contain Python dict repr like {'key': 'val'}."""
    audit_entries = [
        {
            "resource_id": "req-001",
            "resource_type": "workflow_request",
            "action": "create",
            "user_name": "owner@test.com",
            "created_at": "2026-01-01T09:00:00+00:00",
            "details": {"category": "noise_complaint", "confidence": 0.8, "auto_resolved": False},
        }
    ]

    async def _aiter(entries):
        for e in entries:
            yield e

    mock_db = MagicMock()
    mock_db.audit_logs.find.return_value.sort.return_value = _aiter(audit_entries)

    from services.comms_intake_service import get_request_timeline

    timeline = await get_request_timeline("req-001", BUILDING_ID, mock_db)

    assert len(timeline) == 1
    note = timeline[0]["note"]
    # Must not be raw Python repr
    assert "{'category'" not in note
    assert "'noise_complaint'" not in note
    # Must contain readable content
    assert "noise_complaint" in note or note == ""


# ─────────────────────────────────────────────────────────────────────────────
# 6. get_request_timeline — original behaviour preserved
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_built_from_audit_logs():
    """get_request_timeline returns entries sorted by created_at."""
    audit_entries = [
        {
            "resource_id": "req-001",
            "resource_type": "workflow_request",
            "action": "create",
            "user_name": "owner@test.com",
            "created_at": "2026-01-01T09:00:00+00:00",
            "details": {"category": "maintenance_request"},
        },
        {
            "resource_id": "req-001",
            "resource_type": "workflow_request",
            "action": "status_changed",
            "user_name": "manager@test.com",
            "created_at": "2026-01-01T10:00:00+00:00",
            "details": {"new_status": "in_progress"},
        },
    ]

    async def _aiter(entries):
        for e in entries:
            yield e

    mock_db = MagicMock()
    mock_db.audit_logs.find.return_value.sort.return_value = _aiter(audit_entries)

    from services.comms_intake_service import get_request_timeline

    timeline = await get_request_timeline("req-001", BUILDING_ID, mock_db)

    assert len(timeline) == 2
    assert timeline[0]["action"] == "create"
    assert timeline[1]["action"] == "status_changed"


# ─────────────────────────────────────────────────────────────────────────────
# 5. check_sla_breaches — scheduler
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sla_breach_detection_fires_correctly():
    """check_sla_breaches marks overdue open requests as sla_breached."""
    overdue_req = _make_request_doc("req-overdue-001", "in_progress", "maintenance_request")

    mock_db = MagicMock()
    mock_db._db.buildings.find.return_value.to_list = AsyncMock(
        return_value=[{"id": BUILDING_ID}]
    )
    mock_db.workflow_requests.find.return_value.to_list = AsyncMock(
        return_value=[overdue_req]
    )
    mock_db.workflow_requests.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("workers.scheduler.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("workers.scheduler.set_ctx_building_id"),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
        patch("workers.scheduler.check_sla_breaches.__globals__"
              if False else "services.event_emitter.emit_event", new_callable=AsyncMock, create=True),
        patch("workers.scheduler.create_user_notification", new_callable=AsyncMock) as mock_notif,
        patch("workers.scheduler.create_audit_log", new_callable=AsyncMock) as mock_audit,
    ):
        # Patch emit_event inside the function's lazy import
        with patch.dict("sys.modules", {
            "services.event_emitter": MagicMock(emit_event=AsyncMock()),
            "core.events": MagicMock(EventStream=MagicMock(OPERATIONS=MagicMock(value="ops")),
                                     EventType=MagicMock(SLA_BREACH_DETECTED=MagicMock(value="breach"))),
        }):
            from workers.scheduler import check_sla_breaches
            await check_sla_breaches()

    mock_db.workflow_requests.update_many.assert_called_once()
    mock_notif.assert_called_once()
    mock_audit.assert_called_once()
    audit_call = mock_audit.call_args[1]
    assert audit_call["action"] == "sla_breached"


@pytest.mark.asyncio
async def test_double_breach_not_flagged_twice():
    """check_sla_breaches is idempotent — already-breached items are not re-processed."""
    mock_db = MagicMock()
    mock_db._db.buildings.find.return_value.to_list = AsyncMock(
        return_value=[{"id": BUILDING_ID}]
    )
    mock_db.workflow_requests.find.return_value.to_list = AsyncMock(return_value=[])
    mock_db.workflow_requests.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("workers.scheduler.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("workers.scheduler.set_ctx_building_id"),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
        patch("workers.scheduler.create_user_notification", new_callable=AsyncMock) as mock_notif,
        patch("workers.scheduler.create_audit_log", new_callable=AsyncMock) as mock_audit,
    ):
        with patch.dict("sys.modules", {
            "services.event_emitter": MagicMock(emit_event=AsyncMock()),
            "core.events": MagicMock(EventStream=MagicMock(OPERATIONS=MagicMock(value="ops")),
                                     EventType=MagicMock(SLA_BREACH_DETECTED=MagicMock(value="breach"))),
        }):
            from workers.scheduler import check_sla_breaches
            await check_sla_breaches()

    mock_notif.assert_not_called()
    mock_audit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 6. V3 — null assigned_to fallback to building strata_managers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sla_breach_unassigned_notifies_building_managers():
    """
    When assigned_to is null, check_sla_breaches notifies all active
    strata_manager users in the building instead of silently dropping the alert.
    """
    unassigned_req = {
        **_make_request_doc("req-unassigned-001", "awaiting_review", "levy_query"),
        "assigned_to": None,  # explicitly unassigned
    }

    mock_db = MagicMock()
    mock_db._db.buildings.find.return_value.to_list = AsyncMock(
        return_value=[{"id": BUILDING_ID}]
    )
    mock_db.workflow_requests.find.return_value.to_list = AsyncMock(
        return_value=[unassigned_req]
    )
    mock_db.workflow_requests.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
    # Return two strata_manager users for the fallback query
    mock_db._db.users.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "mgr-001"}, {"id": "mgr-002"}]
    )
    mock_db.workflow_runs.insert_one = AsyncMock()

    with (
        patch("workers.scheduler.db", mock_db),
        patch("utils.workflow_runner.db", mock_db),
        patch("workers.scheduler.set_ctx_building_id"),
        patch("utils.workflow_runner.set_ctx_building_id", create=True),
        patch("workers.scheduler.create_user_notification", new_callable=AsyncMock) as mock_notif,
        patch("workers.scheduler.create_audit_log", new_callable=AsyncMock) as mock_audit,
    ):
        with patch.dict("sys.modules", {
            "services.event_emitter": MagicMock(emit_event=AsyncMock()),
            "core.events": MagicMock(
                EventStream=MagicMock(OPERATIONS=MagicMock(value="ops")),
                EventType=MagicMock(SLA_BREACH_DETECTED=MagicMock(value="breach")),
            ),
        }):
            from workers.scheduler import check_sla_breaches
            await check_sla_breaches()

    # Both managers should receive a notification
    assert mock_notif.call_count == 2
    notified_users = {call[1]["user_id"] for call in mock_notif.call_args_list}
    assert notified_users == {"mgr-001", "mgr-002"}
    # Audit entry must record both notified user IDs
    audit_call = mock_audit.call_args[1]
    assert "mgr-001" in audit_call["details"]["notified_users"]
    assert "mgr-002" in audit_call["details"]["notified_users"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. V2 — deflection rate excludes is_test_data
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deflection_rate_excludes_test_data():
    """
    get_engagement_stats must exclude is_test_data:true documents so a 100%
    deflection rate from synthetic test requests is never shown in production.
    """
    from routers.engagement import get_engagement_stats

    call_args_seen = []

    async def mock_count_documents(query, *a, **kw):
        call_args_seen.append(query)
        return 0

    mock_db = MagicMock()
    mock_db.workflow_requests.count_documents = AsyncMock(side_effect=mock_count_documents)

    with patch("routers.engagement.db", mock_db):
        manager = {"id": "u1", "role": "strata_manager"}
        await get_engagement_stats(days=7, current_user=manager, building_id=BUILDING_ID)

    # Every query must exclude test data
    for q in call_args_seen:
        assert q.get("is_test_data") == {"$ne": True}, (
            f"Query missing is_test_data filter: {q}"
        )


@pytest.mark.asyncio
async def test_deflection_rate_uses_trailing_7_days_not_all_time():
    """
    The total_received count must be scoped to trailing N days,
    not all-time — so an all-time 100% from seeded data is never shown.
    """
    from datetime import datetime, timezone
    from routers.engagement import get_engagement_stats

    call_args_seen = []

    async def mock_count_documents(query, *a, **kw):
        call_args_seen.append(query)
        return 5

    mock_db = MagicMock()
    mock_db.workflow_requests.count_documents = AsyncMock(side_effect=mock_count_documents)

    with patch("routers.engagement.db", mock_db):
        manager = {"id": "u1", "role": "strata_manager"}
        result = await get_engagement_stats(days=7, current_user=manager, building_id=BUILDING_ID)

    assert result["period_days"] == 7
    # The base query (total) must have a created_at >= filter
    base_queries = [q for q in call_args_seen if "created_at" in q]
    assert len(base_queries) >= 1, "Expected at least one query with created_at filter"
    for q in base_queries:
        if "$gte" in q.get("created_at", {}):
            cutoff = q["created_at"]["$gte"]
            cutoff_dt = datetime.fromisoformat(cutoff)
            if cutoff_dt.tzinfo is None:
                cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)
            # cutoff should be within the last 7+1 days
            assert (datetime.now(timezone.utc) - cutoff_dt).days <= 8
