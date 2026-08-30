# @featuretrace:coexistence — Shared isolation fixtures and assertion helpers for Phase 2.
# Layer: test
# Data flow: test modules → fixtures (building context setup) → mocked db/auth → assertion helpers.
# Related: tests/backend/test_tenant_isolation_p0t01.py
#           tests/backend/test_dual_source_consistency.py (future)

"""
Isolation Fixtures and Helpers

Reusable pytest fixtures and assertion functions for building isolation,
multi-tenant test setup, and cross-building data leak detection.

Usage in tests:
    @pytest.mark.asyncio
    async def test_my_feature(isolated_db_mock, building_a_id, building_b_id):
        # Set up Building A context
        with patch("request_context.set_ctx_building_id") as mock_set:
            mock_set(building_a_id)
            # Run test with Building A context
        
        # Use assertion helpers
        assert_building_isolated(query_result, building_a_id, "my_collection")
"""

import uuid
from unittest.mock import MagicMock, AsyncMock, patch
import pytest


# ────────────────────────────────────────────────────────────────────────────
# Building Constants (Canonical Test Buildings)
# ────────────────────────────────────────────────────────────────────────────

BUILDING_A_ID = "13195"  # East Gate Residences (production)
BUILDING_B_ID = "test-building-999"  # Test tenant (isolated)
BUILDING_C_ID = "test-building-888"  # Optional: third building for multi-tenant tests


# ────────────────────────────────────────────────────────────────────────────
# Building Context Fixtures
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def building_a_id():
    """Building A context (East Gate)."""
    return BUILDING_A_ID


@pytest.fixture
def building_b_id():
    """Building B context (test tenant)."""
    return BUILDING_B_ID


@pytest.fixture
def building_c_id():
    """Building C context (optional third tenant)."""
    return BUILDING_C_ID


# ────────────────────────────────────────────────────────────────────────────
# Request Context Fixtures
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_building_context_a(building_a_id):
    """Mock request context for Building A user."""
    with patch("request_context.get_ctx_building_id") as mock_get:
        mock_get.return_value = building_a_id
        yield mock_get


@pytest.fixture
def mock_building_context_b(building_b_id):
    """Mock request context for Building B user."""
    with patch("request_context.get_ctx_building_id") as mock_get:
        mock_get.return_value = building_b_id
        yield mock_get


# ────────────────────────────────────────────────────────────────────────────
# Test Data Builders
# ────────────────────────────────────────────────────────────────────────────

def make_message(
    building_id: str,
    sender: str = "owner@example.com",
    content: str = "Test message",
    **kwargs
) -> dict:
    """
    Create a test message document.
    
    Args:
        building_id: The building_id for scoping
        sender: Email of message sender
        content: Message content
        **kwargs: Additional fields (created_at, thread_id, etc.)
    
    Returns:
        Message document dict
    """
    return {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "sender": sender,
        "content": content,
        "created_at": kwargs.get("created_at", "2026-06-01T10:00:00Z"),
        "thread_id": kwargs.get("thread_id", str(uuid.uuid4())),
        **{k: v for k, v in kwargs.items() if k not in ["created_at", "thread_id"]}
    }


def make_announcement(
    building_id: str,
    title: str = "Test Announcement",
    body: str = "Test body",
    **kwargs
) -> dict:
    """
    Create a test announcement document.
    
    Args:
        building_id: The building_id for scoping
        title: Announcement title
        body: Announcement body
        **kwargs: Additional fields
    
    Returns:
        Announcement document dict
    """
    return {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "title": title,
        "body": body,
        "created_at": kwargs.get("created_at", "2026-06-01T10:00:00Z"),
        "expiry_date": kwargs.get("expiry_date", "2026-12-31T23:59:59Z"),
        **{k: v for k, v in kwargs.items() if k not in ["created_at", "expiry_date"]}
    }


def make_conversation(
    building_id: str,
    participant_ids: list = None,
    subject: str = "Test Conversation",
    **kwargs
) -> dict:
    """
    Create a test conversation document.
    
    Args:
        building_id: The building_id for scoping
        participant_ids: List of user IDs in conversation
        subject: Conversation subject
        **kwargs: Additional fields
    
    Returns:
        Conversation document dict
    """
    return {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "participant_ids": participant_ids or [str(uuid.uuid4()), str(uuid.uuid4())],
        "subject": subject,
        "created_at": kwargs.get("created_at", "2026-06-01T10:00:00Z"),
        **{k: v for k, v in kwargs.items() if k not in ["created_at"]}
    }


# ────────────────────────────────────────────────────────────────────────────
# Mock DB Fixtures (with Tenant Scoping)
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db_mock_messages(building_a_id, building_b_id):
    """
    Mock messages collection with automatic building_id scoping.
    
    Simulates TenantScopedDatabase behavior:
    - find({"building_id": bid}) returns only docs for that building
    - find({}) returns empty (fail-safe: no building context = no data)
    """
    building_a_messages = [
        make_message(building_a_id, "owner-a@building.com", "Building A msg 1"),
        make_message(building_a_id, "manager-a@building.com", "Building A msg 2"),
    ]
    
    building_b_messages = [
        make_message(building_b_id, "owner-b@building.com", "Building B msg 1"),
    ]
    
    mock_collection = MagicMock()
    
    def scoped_find(filter_doc, *args, **kwargs):
        cursor = MagicMock()
        bid = filter_doc.get("building_id") if isinstance(filter_doc, dict) else None
        
        if bid == building_a_id:
            cursor.to_list = AsyncMock(return_value=building_a_messages)
        elif bid == building_b_id:
            cursor.to_list = AsyncMock(return_value=building_b_messages)
        else:
            cursor.to_list = AsyncMock(return_value=[])
        
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.skip = MagicMock(return_value=cursor)
        return cursor
    
    mock_collection.find = MagicMock(side_effect=scoped_find)
    mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id=str(uuid.uuid4())))
    mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    
    return mock_collection


@pytest.fixture
def isolated_db_mock_announcements(building_a_id, building_b_id):
    """Mock announcements collection with building_id scoping."""
    building_a_announcements = [
        make_announcement(building_a_id, "Building A Announcement 1"),
        make_announcement(building_a_id, "Building A Announcement 2"),
    ]
    
    building_b_announcements = [
        make_announcement(building_b_id, "Building B Announcement 1"),
    ]
    
    mock_collection = MagicMock()
    
    def scoped_find(filter_doc, *args, **kwargs):
        cursor = MagicMock()
        bid = filter_doc.get("building_id") if isinstance(filter_doc, dict) else None
        
        if bid == building_a_id:
            cursor.to_list = AsyncMock(return_value=building_a_announcements)
        elif bid == building_b_id:
            cursor.to_list = AsyncMock(return_value=building_b_announcements)
        else:
            cursor.to_list = AsyncMock(return_value=[])
        
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        return cursor
    
    mock_collection.find = MagicMock(side_effect=scoped_find)
    mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id=str(uuid.uuid4())))
    
    return mock_collection


@pytest.fixture
def isolated_full_db_mock(isolated_db_mock_messages, isolated_db_mock_announcements):
    """Full mocked DB with multiple collections pre-scoped."""
    mock_db = MagicMock()
    mock_db.messages = isolated_db_mock_messages
    mock_db.announcements = isolated_db_mock_announcements
    return mock_db


# ────────────────────────────────────────────────────────────────────────────
# Assertion Helpers (Reusable across all isolation tests)
# ────────────────────────────────────────────────────────────────────────────

def assert_building_isolated(
    documents: list,
    expected_building_id: str,
    collection_name: str = "",
    id_field: str = "_id"
) -> None:
    """
    Assert all documents belong to the expected building.
    
    Usage:
        results = await query_messages(building_a_id)
        assert_building_isolated(results, building_a_id, "messages")
    
    Raises:
        AssertionError if:
        - Any document is missing building_id
        - Any document's building_id != expected_building_id
    """
    for doc in documents:
        assert "building_id" in doc, \
            f"Document in {collection_name} missing building_id: {doc.get(id_field)}"
        assert doc["building_id"] == expected_building_id, \
            f"Document in {collection_name} (id={doc.get(id_field)}) has building_id={doc.get('building_id')} != {expected_building_id}"


def assert_no_building_leakage(
    docs_a: list,
    docs_b: list,
    id_field: str = "id",
    building_a_label: str = "Building A",
    building_b_label: str = "Building B"
) -> None:
    """
    Assert no documents overlap between two building result sets.
    
    Usage:
        docs_a = await query_building(building_a_id)
        docs_b = await query_building(building_b_id)
        assert_no_building_leakage(docs_a, docs_b)
    
    Raises:
        AssertionError if any document IDs appear in both sets.
    """
    ids_a = {doc.get(id_field) for doc in docs_a}
    ids_b = {doc.get(id_field) for doc in docs_b}
    
    overlap = ids_a.intersection(ids_b)
    assert not overlap, \
        f"Cross-building data leakage: {len(overlap)} documents from {building_a_label} " \
        f"also appear in {building_b_label} results: {overlap}"


def assert_query_building_scoped(
    filter_doc: dict,
    expected_building_id: str
) -> None:
    """
    Assert that a query filter includes the required building_id.
    
    Usage (in test setup):
        filter_obj = {"building_id": building_a_id, "status": "active"}
        assert_query_building_scoped(filter_obj, building_a_id)
    
    Raises:
        AssertionError if building_id is missing or mismatched.
    """
    assert "building_id" in filter_doc, \
        "Query filter must include building_id for tenant scoping"
    assert filter_doc["building_id"] == expected_building_id, \
        f"Filter building_id={filter_doc.get('building_id')} != {expected_building_id}"


def assert_no_unscoped_queries(mock_collection, forbidden_patterns: list = None) -> None:
    """
    Assert that collection was never queried without building_id.
    
    Usage (in test verification):
        assert_no_unscoped_queries(mock_db.messages, forbidden_patterns=[{}])
    
    Args:
        mock_collection: MagicMock of a collection
        forbidden_patterns: List of filter dicts that should never be used
    
    Raises:
        AssertionError if unscoped queries were made.
    """
    if forbidden_patterns is None:
        forbidden_patterns = [{}]  # Default: empty filter is forbidden
    
    if hasattr(mock_collection, "find") and hasattr(mock_collection.find, "call_args_list"):
        for call in mock_collection.find.call_args_list:
            filter_doc = call[0][0] if call[0] else None
            for forbidden in forbidden_patterns:
                assert filter_doc != forbidden, \
                    f"Unscoped query detected: {filter_doc} matches forbidden pattern {forbidden}"


# ────────────────────────────────────────────────────────────────────────────
# End of Isolation Fixtures and Helpers
# ────────────────────────────────────────────────────────────────────────────
