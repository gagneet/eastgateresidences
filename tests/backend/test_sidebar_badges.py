"""
@featuretrace:sidebar-badges — Tests for /engagement/nav/badges endpoint.
Layer: test
Data flow: test_* → routers.engagement.get_nav_badges → notices/compliance/workflow aggregates (building-scoped).
Related: backend/routers/engagement.py
         tests/backend/test_sidebar_badges.py

Tests: notices count, compliance count, multi-tenant isolation, zero returns for regular users.
Idempotent: uses mock DB, no real data created.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


def _make_nav_badges_db(
        notifications=0,
        open_requests=0,
        unread_notices=3,
        overdue_compliance=2,
        tenant_approvals=0,
        user_approvals=0,
):
    db = MagicMock()
    db.user_notifications.count_documents = AsyncMock(return_value=notifications)
    db.workflow_requests.count_documents = AsyncMock(return_value=open_requests)
    db.notices.count_documents = AsyncMock(return_value=unread_notices)
    db.compliance_items.count_documents = AsyncMock(return_value=overdue_compliance)
    db.users.count_documents = AsyncMock(return_value=tenant_approvals)
    db.memberships.aggregate.return_value.to_list = AsyncMock(return_value=[{"count": user_approvals}])
    return db


def _make_user(role="owner", user_id="u1"):
    return {"id": user_id, "role": role, "effective_role": role}


@pytest.mark.asyncio
async def test_nav_badges_returns_notices_count():
    """GET /nav/badges must return 'notices' field with unread count."""
    from routers.engagement import get_nav_badges
    db = _make_nav_badges_db(unread_notices=5)
    user = _make_user("owner")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        result = await get_nav_badges(current_user=user, building_id="13195")

    assert "notices" in result
    assert result["notices"] == 5


@pytest.mark.asyncio
async def test_nav_badges_compliance_zero_for_regular_user():
    """Regular owner must receive compliance=0 (not a management role)."""
    from routers.engagement import get_nav_badges
    db = _make_nav_badges_db(overdue_compliance=4)
    user = _make_user("owner")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        result = await get_nav_badges(current_user=user, building_id="13195")

    assert result["compliance"] == 0


@pytest.mark.asyncio
async def test_nav_badges_compliance_returned_for_ec_chairman():
    """EC Chairman (ec_member with ec_position=CHAIRMAN) gets compliance count."""
    from routers.engagement import get_nav_badges
    db = _make_nav_badges_db(overdue_compliance=3)
    # Per migration 0025: chairman is an ec_position, not a role.
    user = _make_user("ec_member")
    user["ec_position"] = "CHAIRMAN"

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        result = await get_nav_badges(current_user=user, building_id="13195")

    assert result["compliance"] == 3


@pytest.mark.asyncio
async def test_nav_badges_notices_building_scoped():
    """notices count query must include building_id to prevent data leak."""
    from routers.engagement import get_nav_badges
    captured = []

    async def track(q):
        captured.append(q)
        return 0

    db = _make_nav_badges_db()
    db.notices.count_documents = track
    user = _make_user("owner")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        await get_nav_badges(current_user=user, building_id="16244")

    assert len(captured) == 1
    # notices query uses $and: [{"building_id": ..., ...}, ...]
    and_clause = captured[0].get("$and", [])
    assert any(c.get("building_id") == "16244" for c in and_clause), (
        f"Notices query must be scoped to building_id '16244', got: {captured[0]}"
    )


@pytest.mark.asyncio
async def test_nav_badges_multi_tenant_no_leak():
    """Notices for Sierra (16244) must not include East Gate (13195) notices."""
    from routers.engagement import get_nav_badges
    call_bids = []

    async def track_notices(q):
        # notices query uses $and: [{"building_id": ..., ...}, ...]
        and_clause = q.get("$and", [])
        bid = next((c.get("building_id") for c in and_clause if "building_id" in c), None)
        call_bids.append(bid)
        return 0

    db = _make_nav_badges_db()
    db.notices.count_documents = track_notices
    user = _make_user("ec_member")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        await get_nav_badges(current_user=user, building_id="16244")

    assert all(bid == "16244" for bid in call_bids), f"All queries must be for 16244, got: {call_bids}"


@pytest.mark.asyncio
async def test_nav_badges_has_all_required_fields():
    """Response must include notifications, requests, notices, compliance, chat."""
    from routers.engagement import get_nav_badges
    db = _make_nav_badges_db()
    user = _make_user("owner")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        result = await get_nav_badges(current_user=user, building_id="13195")

    required_fields = {"notifications", "requests", "notices", "compliance", "chat", "tenant_approvals",
                       "user_approvals"}
    assert required_fields.issubset(set(result.keys()))


@pytest.mark.asyncio
async def test_nav_badges_returns_user_approval_count_for_manager():
    """Management roles must receive pending admin approval counts for Resident Management."""
    from routers.engagement import get_nav_badges
    db = _make_nav_badges_db(user_approvals=4)
    user = _make_user("strata_manager")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        result = await get_nav_badges(current_user=user, building_id="13195")

    assert result["user_approvals"] == 4


@pytest.mark.asyncio
async def test_nav_badges_hides_user_approval_count_for_owner():
    """Owners must not receive management-only pending admin approval counts."""
    from routers.engagement import get_nav_badges
    db = _make_nav_badges_db(user_approvals=4)
    user = _make_user("owner")

    with patch("routers.engagement.db", db), \
            patch("routers.engagement.repair_orphan_staff_memberships", AsyncMock()):
        result = await get_nav_badges(current_user=user, building_id="13195")

    assert result["user_approvals"] == 0
