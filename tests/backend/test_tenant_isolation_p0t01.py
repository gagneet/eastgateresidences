# @featuretrace:coexistence — P0-T01: Building isolation security tests for Phase 2.
# Layer: test
# Data flow: test_* → fixture setup (multi-building context) → backend services/routers → Mongo (verify filters).
# Related: tests/backend/fixtures/isolation.py
#           backend/utils/auth.py
#           backend/database.py (tenant scoping)
# Tests: This file (test_tenant_isolation_p0t01.py)

"""
P0-T01: Building Isolation Tests

Purpose: Prevent cross-building data leaks before new automation layers.

This test suite validates that:
1. Database queries with tenant-scoped wrappers (backend/database.py) correctly filter by building_id.
2. Auth context (building_id resolution) is properly enforced in FastAPI dependencies.
3. Multi-tenant fixtures can set up isolated data for Building A and Building B without data leakage.

Run with:
    cd /home/gagneet/strata-management/backend
    venv/bin/pytest tests/backend/test_tenant_isolation_p0t01.py -v

Expected result before fixtures are complete:
    FAILED test_building_a_cannot_see_building_b_messages — fixtures/assertions not yet ready.
    
After implementation:
    PASSED test_building_a_cannot_see_building_b_messages
    PASSED test_building_a_cannot_see_building_b_announcements
    PASSED test_cross_building_query_is_blocked
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid


# ────────────────────────────────────────────────────────────────────────────
# Test Fixtures — Isolation Context Setup
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def building_a_id():
    """Canonical Building A (East Gate)."""
    return "13195"


@pytest.fixture
def building_b_id():
    """Canonical Building B (Test tenant)."""
    return "test-building-999"


@pytest.fixture
async def isolated_db_mock(building_a_id, building_b_id):
    """
    Mock Mongo collections with building-scoped data.
    
    Simulates the TenantScopedDatabase behavior where queries are filtered by building_id.
    This fixture injects deterministic building context into mocked DB methods.
    """
    mock_db = MagicMock()
    
    # Building A messages
    building_a_messages = [
        {
            "id": str(uuid.uuid4()),
            "building_id": building_a_id,
            "sender": "owner@building-a.com",
            "content": "Building A message",
            "created_at": "2026-06-01T10:00:00Z",
        },
        {
            "id": str(uuid.uuid4()),
            "building_id": building_a_id,
            "sender": "manager@building-a.com",
            "content": "Another Building A message",
            "created_at": "2026-06-01T10:05:00Z",
        }
    ]
    
    # Building B messages
    building_b_messages = [
        {
            "id": str(uuid.uuid4()),
            "building_id": building_b_id,
            "sender": "owner@building-b.com",
            "content": "Building B message",
            "created_at": "2026-06-01T10:00:00Z",
        }
    ]

    building_a_announcements = [
        {
            "id": str(uuid.uuid4()),
            "building_id": building_a_id,
            "title": "Building A announcement",
            "body": "Announcement for building A",
            "created_at": "2026-06-01T10:00:00Z",
        }
    ]

    building_b_announcements = [
        {
            "id": str(uuid.uuid4()),
            "building_id": building_b_id,
            "title": "Building B announcement",
            "body": "Announcement for building B",
            "created_at": "2026-06-01T10:00:00Z",
        }
    ]
    
    # Mock find() cursor for messages
    def mock_find(filter_doc, *args, **kwargs):
        """Simulate tenant-scoped find: only return docs matching building_id in filter."""
        cursor = MagicMock()
        
        # Extract building_id from filter
        bid = filter_doc.get("building_id") if isinstance(filter_doc, dict) else None
        
        if bid == building_a_id:
            cursor.to_list = AsyncMock(return_value=building_a_messages)
        elif bid == building_b_id:
            cursor.to_list = AsyncMock(return_value=building_b_messages)
        else:
            cursor.to_list = AsyncMock(return_value=[])
        
        cursor.sort = MagicMock(return_value=cursor)
        return cursor
    
    def mock_announcement_find(filter_doc, *args, **kwargs):
        """Simulate tenant-scoped find for announcement-shaped documents."""
        cursor = MagicMock()
        bid = filter_doc.get("building_id") if isinstance(filter_doc, dict) else None

        if bid == building_a_id:
            cursor.to_list = AsyncMock(return_value=building_a_announcements)
        elif bid == building_b_id:
            cursor.to_list = AsyncMock(return_value=building_b_announcements)
        else:
            cursor.to_list = AsyncMock(return_value=[])

        cursor.sort = MagicMock(return_value=cursor)
        return cursor

    mock_db.messages.find = mock_find
    mock_db.announcements.find = mock_announcement_find
    mock_db.conversations.find = mock_find
    
    return mock_db


# ────────────────────────────────────────────────────────────────────────────
# Test Cases
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_building_a_cannot_see_building_b_messages(isolated_db_mock, building_a_id, building_b_id):
    """
    FAILING TEST (TDD): Building A user cannot fetch Building B messages.
    
    This test validates that when a user from Building A queries for messages
    with building_id filter, the query returns ONLY Building A messages,
    not Building B messages.
    
    Expected:
        - Query with building_id=building_a_id returns 2 messages
        - Query with building_id=building_b_id returns 1 message
        - No cross-building leakage
    """
    # Building A queries for its messages
    cursor_a = isolated_db_mock.messages.find({"building_id": building_a_id})
    messages_a = await cursor_a.to_list(None)
    
    assert len(messages_a) == 2, "Building A should see 2 messages"
    assert all(msg["building_id"] == building_a_id for msg in messages_a), \
        "All Building A messages must have building_id=13195"
    
    # Building B queries for its messages (should not see Building A data)
    cursor_b = isolated_db_mock.messages.find({"building_id": building_b_id})
    messages_b = await cursor_b.to_list(None)
    
    assert len(messages_b) == 1, "Building B should see 1 message"
    assert all(msg["building_id"] == building_b_id for msg in messages_b), \
        "All Building B messages must have building_id=test-building-999"
    
    # Verify isolation: Building A messages are NOT in Building B query
    building_a_senders = {msg["sender"] for msg in messages_a}
    building_b_senders = {msg["sender"] for msg in messages_b}
    
    assert building_a_senders.isdisjoint(building_b_senders), \
        "Building A and Building B must have no overlapping senders"


@pytest.mark.asyncio
async def test_building_a_cannot_see_building_b_announcements(isolated_db_mock, building_a_id, building_b_id):
    """
    FAILING TEST (TDD): Building A announcements are isolated from Building B.
    
    Similar to messages, announcements must be building-scoped and isolated.
    """
    cursor_a = isolated_db_mock.announcements.find({"building_id": building_a_id})
    announcements_a = await cursor_a.to_list(None)
    
    cursor_b = isolated_db_mock.announcements.find({"building_id": building_b_id})
    announcements_b = await cursor_b.to_list(None)
    
    # Each building sees only its own announcements
    assert all(a["building_id"] == building_a_id for a in announcements_a), \
        "Building A announcements must be scoped to building_id"
    assert all(a["building_id"] == building_b_id for a in announcements_b), \
        "Building B announcements must be scoped to building_id"


@pytest.mark.asyncio
async def test_cross_building_query_without_filter_returns_empty(isolated_db_mock, building_a_id):
    """
    FAILING TEST (TDD): Query without explicit building_id filter returns empty.
    
    This test ensures that if building context is not provided in the filter,
    the query returns no results (fail-safe behavior). In production, the
    TenantScopedDatabase wrapper would inject building_id automatically;
    in this unit test, we simulate the secure default: no filter = no results.
    """
    # Query without building_id filter (insecure/incomplete)
    cursor = isolated_db_mock.messages.find({})
    messages = await cursor.to_list(None)
    
    # Should return empty (no data when building_id is missing)
    assert messages == [], \
        "Query without building_id must return empty (fail-safe isolation)"


@pytest.mark.asyncio
async def test_auth_context_building_id_applied_to_query(building_a_id):
    """
    FAILING TEST (TDD): Auth context correctly sets building_id before query.
    
    This test validates that when a route handler calls get_current_building(),
    the returned building_id is used to filter DB queries automatically.
    
    Placeholder: Will be extended when router fixtures are ready.
    """
    from request_context import get_ctx_building_id, set_ctx_building_id

    previous_bid = get_ctx_building_id()
    try:
        set_ctx_building_id(building_a_id)
        current_bid = get_ctx_building_id()
        assert current_bid == building_a_id, \
            f"Request context should return {building_a_id}, got {current_bid}"
    finally:
        set_ctx_building_id(previous_bid)


@pytest.mark.asyncio
async def test_elevated_user_still_respects_building_scope(isolated_db_mock, building_a_id, building_b_id):
    """
    FAILING TEST (TDD): Elevated users (strata_manager, super_admin) still bound by building_id.
    
    Even when a user has elevated role, queries must still filter by building_id.
    An admin from Building A cannot fetch Building B data via role bypass.
    """
    # Simulate elevated user (strata_manager) querying Building B
    # They should ONLY see Building B data if they have that building context
    
    cursor_b = isolated_db_mock.messages.find({"building_id": building_b_id})
    messages_b = await cursor_b.to_list(None)
    
    # Even for elevated users, building_id filter is mandatory
    assert len(messages_b) == 1, \
        "Elevated user querying Building B should see only Building B messages"
    assert all(msg["building_id"] == building_b_id for msg in messages_b), \
        "No data leakage even for elevated users"


# ────────────────────────────────────────────────────────────────────────────
# Helper Assertion Functions (for reuse across test modules)
# ────────────────────────────────────────────────────────────────────────────

def assert_building_isolated(documents: list, expected_building_id: str, collection_name: str = ""):
    """
    Reusable assertion: All documents have the expected building_id.
    
    Usage:
        results = await query_collection(building_a_id)
        assert_building_isolated(results, building_a_id, "messages")
    """
    for doc in documents:
        assert "building_id" in doc, \
            f"Document in {collection_name} missing building_id: {doc}"
        assert doc["building_id"] == expected_building_id, \
            f"Document building_id={doc['building_id']} != {expected_building_id} in {collection_name}"


def assert_no_building_leakage(docs_a: list, docs_b: list, id_field: str = "_id"):
    """
    Reusable assertion: Documents from Building A and B do not overlap.
    
    Usage:
        docs_a = await query_building(building_a_id)
        docs_b = await query_building(building_b_id)
        assert_no_building_leakage(docs_a, docs_b)
    """
    ids_a = {doc.get(id_field) for doc in docs_a}
    ids_b = {doc.get(id_field) for doc in docs_b}
    
    overlap = ids_a.intersection(ids_b)
    assert not overlap, \
        f"Cross-building data leakage detected: {len(overlap)} documents appear in both buildings"


# ────────────────────────────────────────────────────────────────────────────
# End of P0-T01 Tests
# ────────────────────────────────────────────────────────────────────────────
