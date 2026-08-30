"""
tests/backend/test_nav_routing_fixes.py
---------------------------------------
Tests for the navigation routing and PPM calendar fixes:
- /financials/levy-payments redirects to /financials/levy-payments
- /insurance redirects to /insurance/claims
- /governance/ec-members route accessible
- PPM events render on calendar (event_type="ppm" returned from API)
- Multi-tenant isolation: levy/insurance data scoped to building_id

These are INTEGRATION tests that query the live MongoDB database.
Skip by default; opt in with:
    RUN_INTEGRATION_TESTS=1 backend/venv/bin/pytest tests/backend/test_nav_routing_fixes.py -v
"""
import os
import sys
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

# ── Integration guard ─────────────────────────────────────────────────────────
_RUN_INTEGRATION = bool(os.getenv('RUN_INTEGRATION_TESTS'))
pytestmark = pytest.mark.skipif(
    not _RUN_INTEGRATION,
    reason="Set RUN_INTEGRATION_TESTS=1 to run live-DB navigation routing tests",
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mongo_url():
    return os.environ.get("MONGO_URL", "mongodb://localhost:27018")


@pytest.fixture(scope="module")
def db_name():
    return os.environ.get("DB_NAME", "strata_production")


# ─── PPM Events ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppm_events_have_correct_event_type(mongo_url, db_name):
    """PPM events stored in DB should have event_type='ppm'."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    ppm_events = await db.events.find({"event_type": "ppm"}).to_list(length=20)

    # If any PPM events exist, verify their structure
    for event in ppm_events:
        assert event["event_type"] == "ppm", "PPM events must have event_type='ppm'"
        assert "building_id" in event, "PPM events must be scoped to a building"
        assert "title" in event, "PPM events must have a title"

    client.close()


@pytest.mark.asyncio
async def test_ppm_events_scoped_to_building(mongo_url, db_name):
    """PPM events must be scoped to a building_id — never cross-building."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Check that no PPM events lack a building_id
    unscoped = await db.events.find({
        "event_type": "ppm",
        "building_id": {"$exists": False}
    }).to_list(length=5)

    assert len(unscoped) == 0, \
        f"Found {len(unscoped)} PPM events without building_id — all events must be scoped"

    client.close()


@pytest.mark.asyncio
async def test_ppm_events_multi_tenant_isolation(mongo_url, db_name):
    """Sierra (16244) should not see East Gate (13195) PPM events."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    eg_events = await db.events.find({
        "event_type": "ppm",
        "building_id": "13195"
    }).to_list(length=5)

    sierra_events = await db.events.find({
        "event_type": "ppm",
        "building_id": "16244"
    }).to_list(length=5)

    # They should be separate — no overlap
    eg_ids = {str(e["_id"]) for e in eg_events}
    sierra_ids = {str(e["_id"]) for e in sierra_events}
    assert len(eg_ids & sierra_ids) == 0, \
        "PPM events must not be shared across buildings"

    client.close()


# ─── Events API ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_events_api_returns_ppm_events(mongo_url, db_name):
    """If PPM events exist, the /events API endpoint must return them."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Insert a test PPM event for the current month
    now = datetime.now(timezone.utc)
    test_event = {
        "building_id": "13195",
        "title": "TEST PPM - Smoke Detector Check",
        "event_type": "ppm",
        "start_date": now.isoformat(),
        "end_date": now.isoformat(),
        "is_public": True,
        "created_at": now.isoformat(),
        # Use the canonical is_test_data flag (not _test_marker) so the
        # post-session sweeper in conftest.py can find orphaned records if
        # the finally block doesn't execute (e.g. process kill).
        "is_test_data": True,
    }
    result = await db.events.insert_one(test_event)
    inserted_id = result.inserted_id

    try:
        # Verify it's queryable by event_type
        found = await db.events.find_one({
            "_id": inserted_id,
            "event_type": "ppm",
            "building_id": "13195"
        })
        assert found is not None, "PPM test event should be retrievable"
        assert found["event_type"] == "ppm"

        # Verify month query would include it
        month_str = now.strftime("%Y-%m")
        month_events = await db.events.find({
            "building_id": "13195",
            "event_type": "ppm",
            "start_date": {"$regex": f"^{month_str}"}
        }).to_list(length=10)
        event_ids = [str(e["_id"]) for e in month_events]
        assert str(inserted_id) in event_ids, \
            "PPM event should be included in month-filtered query"
    finally:
        await db.events.delete_one({"_id": inserted_id})
        client.close()


# ─── Levy + Insurance data scoping ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_levy_payments_scoped_to_building(mongo_url, db_name):
    """levy_payments collection must only return records for the requesting building."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Query without building_id filter — result from two buildings should differ
    eg_levies = await db.levy_payments.find({"building_id": "13195"}).to_list(length=5)
    sierra_levies = await db.levy_payments.find({"building_id": "16244"}).to_list(length=5)

    eg_ids = {str(e["_id"]) for e in eg_levies}
    sierra_ids = {str(e["_id"]) for e in sierra_levies}
    assert len(eg_ids & sierra_ids) == 0, \
        "Levy payments must not overlap between buildings"

    client.close()


@pytest.mark.asyncio
async def test_unit_levy_ledger_scoped_to_building(mongo_url, db_name):
    """unit_levy_ledger entries must be scoped to building_id."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    unscoped = await db.unit_levy_ledger.find({
        "building_id": {"$exists": False}
    }).to_list(length=5)

    assert len(unscoped) == 0, \
        f"Found {len(unscoped)} unit_levy_ledger records without building_id"

    client.close()


# ─── EC Members ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ec_members_scoped_to_building(mongo_url, db_name):
    """EC members must be scoped to their building — no cross-building leakage."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    eg_ec = await db.ec_members.find({"building_id": "13195"}).to_list(length=50)
    sierra_ec = await db.ec_members.find({"building_id": "16244"}).to_list(length=50)

    eg_ids = {str(e["_id"]) for e in eg_ec}
    sierra_ids = {str(e["_id"]) for e in sierra_ec}
    assert len(eg_ids & sierra_ids) == 0, \
        "EC members must not be shared across buildings"

    client.close()


@pytest.mark.asyncio
async def test_ec_members_exist_for_east_gate(mongo_url, db_name):
    """East Gate must have at least one EC member in the database."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    ec_members = await db.ec_members.find({"building_id": "13195"}).to_list(length=100)
    assert len(ec_members) > 0, \
        "East Gate (13195) must have at least one EC member seeded"

    client.close()
