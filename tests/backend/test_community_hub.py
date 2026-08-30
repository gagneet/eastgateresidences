"""
@featuretrace:community-hub — Tests for Community Hub building summary endpoint.
Layer: test
Data flow: test_* → routers.community_hub.get_building_summary → community hub counters/aggregations (building-scoped).
Related: backend/routers/community_hub.py
         tests/backend/test_community_hub.py

Tests: building summary includes pet_count, open_smart_requests, upcoming_bookings.
Multi-tenant: verifies building isolation between 13195 and 16244.
Idempotent: all test data inserted in setup is cleaned up in teardown.
"""
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Unit tests — mock DB, no real connection required
# ---------------------------------------------------------------------------

class MockCollection:
    def __init__(self, count_return=0):
        self._count = count_return

    async def count_documents(self, query):
        return self._count

    def aggregate(self, pipeline):
        async def _gen():
            return
            yield  # make it an async generator

        return _gen()


def _make_db(
        units=10,
        arrears=2,
        work_orders=1,
        proposals=3,
        volunteer=5,
        workflow=4,
        pet_requests=7,
        amenity_bookings=2,
        maintenance=3,
        sinking_balance=0,
        compliance_overdue=0,
        compliance_total=0,
        disputes_unresolved=0,
        disputes_total=0,
):
    db = MagicMock()
    db.units.count_documents = AsyncMock(side_effect=lambda q: (
        units if "arrears_balance" not in str(q) else arrears
    ))
    db.work_orders.count_documents = AsyncMock(return_value=work_orders)
    db.proposals.count_documents = AsyncMock(return_value=proposals)
    db.volunteer_events.count_documents = AsyncMock(return_value=volunteer)
    db.workflow_requests.count_documents = AsyncMock(return_value=workflow)
    db.pet_requests.count_documents = AsyncMock(return_value=pet_requests)
    db.amenity_bookings.count_documents = AsyncMock(return_value=amenity_bookings)
    db.maintenance_requests.count_documents = AsyncMock(return_value=maintenance)
    # The compliance register feeds the health score's compliance axis. Two counts, not
    # one: _compliance() refuses to score "0 overdue" without a register size, because
    # "0 of 0 overdue" is an empty register rather than a clean bill of health. Defaults
    # are 0/0 so existing cases keep reporting the axis as unavailable.
    db.compliance_items.count_documents = AsyncMock(side_effect=lambda q: (
        compliance_overdue if "overdue" in str(q) else compliance_total
    ))
    # The by-law breach register feeds the dispute axis, on the same two-count principle
    # as compliance: an empty register is "never recorded a dispute", not "has none".
    # Defaults are 0/0 so existing cases keep reporting the axis as unavailable.
    db.by_law_breach_reports.count_documents = AsyncMock(side_effect=lambda q: (
        disputes_unresolved if "status" in q else disputes_total
    ))
    db.sinking_fund_accounts.aggregate = MagicMock(return_value=_async_iter([]))
    db.building_summaries.find_one = AsyncMock(return_value=None)
    db.building_summaries.update_one = AsyncMock(return_value=None)
    return db


def _async_iter(items):
    async def _gen():
        for item in items:
            yield item

    return _gen()


@pytest.mark.asyncio
async def test_build_health_data_includes_pet_count():
    """_build_health_data must include registered_pets count."""
    from routers.community_dashboard import _build_health_data
    with patch("routers.community_dashboard.db", _make_db(pet_requests=7)):
        result = await _build_health_data("13195")
    assert result["registered_pets"] == 7


@pytest.mark.asyncio
async def test_build_health_data_includes_smart_requests():
    """_build_health_data must include open_smart_requests count."""
    from routers.community_dashboard import _build_health_data
    with patch("routers.community_dashboard.db", _make_db(workflow=4)):
        result = await _build_health_data("13195")
    assert result["open_smart_requests"] == 4


@pytest.mark.asyncio
async def test_build_health_data_includes_upcoming_bookings():
    """_build_health_data must include upcoming_bookings count."""
    from routers.community_dashboard import _build_health_data
    with patch("routers.community_dashboard.db", _make_db(amenity_bookings=2)):
        result = await _build_health_data("13195")
    assert result["upcoming_bookings"] == 2


@pytest.mark.asyncio
async def test_building_id_scoped_in_queries():
    """All count queries must include building_id to prevent data leaks across buildings."""
    from routers.community_dashboard import _build_health_data
    captured_queries = []

    async def capturing_count(query):
        captured_queries.append(query)
        return 0

    db = _make_db()
    db.units.count_documents = capturing_count
    db.pet_requests.count_documents = capturing_count
    db.workflow_requests.count_documents = capturing_count
    db.amenity_bookings.count_documents = capturing_count
    db.work_orders.count_documents = capturing_count
    db.proposals.count_documents = capturing_count
    db.volunteer_events.count_documents = capturing_count

    with patch("routers.community_dashboard.db", db):
        await _build_health_data("16244")  # Sierra building

    for q in captured_queries:
        assert "building_id" in q, f"Query missing building_id: {q}"


@pytest.mark.asyncio
async def test_pet_count_zero_when_none():
    """registered_pets must return 0 when no pets are approved."""
    from routers.community_dashboard import _build_health_data
    with patch("routers.community_dashboard.db", _make_db(pet_requests=0)):
        result = await _build_health_data("13195")
    assert result["registered_pets"] == 0


@pytest.mark.asyncio
async def test_multi_tenant_isolation():
    """Sierra (16244) pet count must not include East Gate (13195) data."""
    from routers.community_dashboard import _build_health_data

    call_log = []

    async def track_pets(q):
        call_log.append(q.get("building_id"))
        return 5

    db = _make_db()
    db.pet_requests.count_documents = track_pets
    db.units.count_documents = AsyncMock(return_value=0)
    db.work_orders.count_documents = AsyncMock(return_value=0)
    db.proposals.count_documents = AsyncMock(return_value=0)
    db.volunteer_events.count_documents = AsyncMock(return_value=0)
    db.workflow_requests.count_documents = AsyncMock(return_value=0)
    db.amenity_bookings.count_documents = AsyncMock(return_value=0)

    with patch("routers.community_dashboard.db", db):
        await _build_health_data("16244")

    # All pet_requests queries must be for building_id "16244", never "13195"
    assert all(bid == "16244" for bid in call_log), f"Expected 16244 queries, got: {call_log}"
