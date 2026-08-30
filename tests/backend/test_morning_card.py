"""Tests for Morning Card service (S5 - A1)."""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"
USER_ID = "user-test-001"


def _mock_db_empty():
    m = MagicMock()
    m.workflow_requests.count_documents = AsyncMock(return_value=0)
    m.unit_levy_ledger.find_one = AsyncMock(return_value=None)  # no outstanding levy
    m.parcels.find_one = AsyncMock(return_value=None)
    m.proposals.find_one = AsyncMock(return_value=None)
    m.work_orders.count_documents = AsyncMock(return_value=0)
    m.savings_events.find_one = AsyncMock(return_value=None)
    m.volunteer_events.find_one = AsyncMock(return_value=None)
    m.building_summaries.find_one = AsyncMock(return_value=None)
    return m


@pytest.mark.asyncio
async def test_critical_card_wins_over_action():
    """Levy overdue (critical) beats parcel waiting (action) in priority."""
    mock_db = _mock_db_empty()
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 500.0})
    mock_db.parcels.find_one = AsyncMock(return_value={
        "id": "p1", "carrier": "DHL",
        "received_date": datetime.now(timezone.utc).isoformat()
    })
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "owner", "TH087", BUILDING_ID)
    assert card["card_type"] == "levy_overdue"
    assert card["urgency"] == "critical"


@pytest.mark.asyncio
async def test_sla_breach_card_shown_to_manager():
    """Managers see SLA breach card when requests are overdue."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=3)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "strata_manager", None, BUILDING_ID)
    assert card["card_type"] == "sla_breach_manager"
    assert card["urgency"] == "critical"


@pytest.mark.asyncio
async def test_parcel_waiting_card():
    """Parcel waiting card computed correctly."""
    mock_db = _mock_db_empty()
    mock_db.parcels.find_one = AsyncMock(return_value={
        "id": "p1", "carrier": "auspost",
        "received_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    })
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "owner", "TH087", BUILDING_ID)
    assert card["card_type"] == "parcel_waiting"
    assert "2d" in card["title"]


@pytest.mark.asyncio
async def test_default_card_when_nothing_pending():
    """Default card returned when no candidates exist."""
    mock_db = _mock_db_empty()
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "owner", None, BUILDING_ID)
    assert card["card_type"] == "default"


@pytest.mark.asyncio
async def test_vote_closing_card_only_for_owners():
    """Vote closing card is only shown to owners who haven't voted."""
    mock_db = _mock_db_empty()
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    mock_db.proposals.find_one = AsyncMock(return_value={
        "id": "p1", "title": "Test Vote", "voting_closes_at": future, "votes": []
    })
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        # Owner should see it
        card_owner = await compute_morning_card(USER_ID, "owner", None, BUILDING_ID)
        # Tenant should NOT see it
        card_tenant = await compute_morning_card(USER_ID, "tenant", None, BUILDING_ID)
    assert card_owner["card_type"] == "vote_closing"
    assert card_tenant["card_type"] == "default"


@pytest.mark.asyncio
async def test_sla_breach_card_cta_links_to_requests_page():
    """SLA breach card must route to /requests — not the non-existent /dashboard/manager."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=2)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "strata_manager", None, BUILDING_ID)
    assert card["card_type"] == "sla_breach_manager"
    # /requests alone renders the request FORM CATALOGUE and ignores ?status=, so
    # the CTA must name the tracking view explicitly or the card links to a page
    # that cannot show the breaches it just counted.
    assert card["cta_link"] == "/requests?tab=my-requests&status=overdue", (
        f"cta_link must open the overdue request queue, got: {card.get('cta_link')}"
    )


@pytest.mark.asyncio
async def test_sla_breach_count_matches_overdue_endpoint_semantics():
    """The card's count must use the same "overdue" definition as the page it links to.

    GET /workflow-requests?status=overdue treats overdue as
    (sla_breached OR status == "overdue") AND status NOT IN the terminal set
    {closed, auto_resolved, completed, cancelled}. The card used to count only
    sla_breached=True while excluding just {closed, auto_resolved}, so a
    completed-but-breached request produced a "1 request past SLA" card over an
    empty queue.
    """
    from routers.workflow_requests import _TERMINAL_STATUSES

    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=3)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "strata_manager", None, BUILDING_ID)

    assert card["card_type"] == "sla_breach_manager"
    query = mock_db.workflow_requests.count_documents.await_args.args[0]
    assert query["$or"] == [{"sla_breached": True}, {"status": "overdue"}]
    assert set(query["status"]["$nin"]) == set(_TERMINAL_STATUSES)
    assert query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_sla_breach_card_cta_links_for_super_admin():
    """Super admin also gets the correct cta_link on SLA breach card."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=1)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "super_admin", None, BUILDING_ID)
    assert card["cta_link"] == "/requests?tab=my-requests&status=overdue"


@pytest.mark.asyncio
async def test_sla_breach_title_pluralises_correctly():
    """SLA breach title is plural for >1 breach and singular for exactly 1."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=1)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "strata_manager", None, BUILDING_ID)
    assert "1 request past SLA" == card["title"]

    mock_db.workflow_requests.count_documents = AsyncMock(return_value=5)
    with patch("services.morning_card_service.db", mock_db):
        card = await compute_morning_card(USER_ID, "strata_manager", None, BUILDING_ID)
    assert "5 requests past SLA" == card["title"]


@pytest.mark.asyncio
async def test_sla_breach_card_shown_to_ec_member_chairman():
    """EC member (chairman position) also sees SLA breach card — they manage requests via the same triage queue."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=2)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "ec_member", None, BUILDING_ID)
    assert card["card_type"] == "sla_breach_manager"
    assert card["cta_link"] == "/requests?tab=my-requests&status=overdue"


@pytest.mark.asyncio
async def test_sla_breach_card_shown_to_ec_member():
    """EC Member also sees SLA breach card — they manage requests via the same triage queue."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=1)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "ec_member", None, BUILDING_ID)
    assert card["card_type"] == "sla_breach_manager"
    assert card["cta_link"] == "/requests?tab=my-requests&status=overdue"


@pytest.mark.asyncio
async def test_sla_breach_card_not_shown_to_owner():
    """Owners must never see the SLA breach manager card."""
    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=5)
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "owner", None, BUILDING_ID)
    assert card.get("card_type") != "sla_breach_manager"


@pytest.mark.asyncio
async def test_sla_breach_query_excludes_auto_resolved():
    """auto_resolved requests must not be counted as SLA-breached work items."""
    captured_queries = []

    async def fake_count(query, *args, **kwargs):
        captured_queries.append(dict(query))
        return 0

    mock_db = _mock_db_empty()
    mock_db.workflow_requests.count_documents = fake_count
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        await compute_morning_card(USER_ID, "strata_manager", None, BUILDING_ID)

    assert len(captured_queries) > 0
    status_filter = captured_queries[0].get("status", {})
    excluded = status_filter.get("$nin", [])
    assert "auto_resolved" in excluded, f"auto_resolved not excluded from SLA query: {captured_queries[0]}"
    assert "closed" in excluded, f"closed not excluded from SLA query: {captured_queries[0]}"


@pytest.mark.asyncio
async def test_savings_milestone_marks_shown_to():
    """New savings milestone card marks the event as shown_to the user."""
    mock_db = _mock_db_empty()
    saving_event = {
        "id": "sev-001", "verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "shown_to": [], "amount_saved_cents": 120000,
        "resident_summary": "Competitive insurance quote",
    }
    mock_db.savings_events.find_one = AsyncMock(return_value=saving_event)
    mock_db.savings_events.update_one = AsyncMock()
    with patch("services.morning_card_service.db", mock_db):
        from services.morning_card_service import compute_morning_card
        card = await compute_morning_card(USER_ID, "owner", None, BUILDING_ID)
    assert card["card_type"] == "new_savings_milestone"
    mock_db.savings_events.update_one.assert_called_once()
