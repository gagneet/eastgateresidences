import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from models.user import UserRole
from models.cutover_status import CutoverMode, DataSource, ReadinessStatus
from services.powerhouse_command_foundation import RepositoryReadResult, RepositoryReadStatus
from services.powerhouse_conversation_service import (
    _assert_write_target,
    _execute_shadow_read,
    compare_threads_list,
    _normalize_thread,
)

# Set up some standard mock users for testing
MOCK_SUPER_ADMIN = {"id": "admin-123", "role": UserRole.SUPER_ADMIN, "effective_role": UserRole.SUPER_ADMIN}
MOCK_MANAGER = {"id": "manager-456", "role": UserRole.STRATA_MANAGER, "effective_role": UserRole.STRATA_MANAGER}
MOCK_OWNER = {"id": "owner-789", "role": UserRole.OWNER, "effective_role": UserRole.OWNER}


@pytest.mark.asyncio
async def test_assert_write_target_allows_controlled_mongo_fallback_write():
    """Verify that legacy write handlers may run only while write_source is Mongo."""
    status_mock = MagicMock(
        mode=CutoverMode.mongo_primary,
        write_source=DataSource.mongo,
    )
    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)):
        # Should complete cleanly without raising
        await _assert_write_target("13195", "powerhouse_conversations")


@pytest.mark.asyncio
async def test_assert_write_target_fails_disabled():
    """Verify that _assert_write_target raises 503 if the domain is disabled."""
    status_mock = MagicMock(
        mode=CutoverMode.disabled,
        write_source=DataSource.none,
    )
    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)):
        with pytest.raises(HTTPException) as exc_info:
            await _assert_write_target("13195", "powerhouse_conversations")
        assert exc_info.value.status_code == 503
        assert "disabled" in exc_info.value.detail


@pytest.mark.asyncio
async def test_assert_write_target_blocks_legacy_mongo_write_in_pg_mode():
    """Verify PG-write mode cannot silently keep writing legacy Mongo collections."""
    status_mock = MagicMock(
        mode=CutoverMode.postgres_write,
        write_source=DataSource.postgres,
    )
    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)):
        with pytest.raises(HTTPException) as exc_info:
            await _assert_write_target("13195", "powerhouse_conversations")
        assert exc_info.value.status_code == 501
        assert "legacy Mongo fallback writes are blocked" in exc_info.value.detail


@pytest.mark.asyncio
async def test_execute_shadow_read_keeps_mongo_when_configured_mongo_primary():
    """Verify legacy mongo_primary config still routes to Mongo without PG-empty heuristics."""
    status_mock = MagicMock(
        mode=CutoverMode.mongo_primary,
        read_source=DataSource.mongo,
    )
    mongo_coro = AsyncMock(return_value={"data": "mongo_raw"})
    pg_read_mock = AsyncMock(return_value=RepositoryReadResult(status=RepositoryReadStatus.UNAVAILABLE))

    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)):
        res = await _execute_shadow_read(
            building_id="13195",
            domain="powerhouse_conversations",
            route="GET /threads",
            mongo_read=mongo_coro,
            pg_read=pg_read_mock,
            comparator_func=MagicMock(),
        )
        assert res == {"data": "mongo_raw"}
        assert pg_read_mock.await_count == 0


@pytest.mark.asyncio
async def test_execute_shadow_read_does_not_fallback_when_postgres_empty():
    """Verify empty PostgreSQL results are authoritative and do not activate Mongo fallback."""
    status_mock = MagicMock(
        mode=CutoverMode.postgres_read,
        read_source=DataSource.postgres,
    )
    mongo_coro = AsyncMock(return_value={"items": [{"id": "mongo-thread"}]})
    pg_read_mock = AsyncMock(
        return_value=RepositoryReadResult(status=RepositoryReadStatus.SUCCESS, value={"items": []})
    )
    fallback_event = AsyncMock()

    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)), \
         patch("services.powerhouse_conversation_service._record_mongo_fallback_event", fallback_event):
        res = await _execute_shadow_read(
            building_id="13195",
            domain="powerhouse_conversations",
            route="GET /threads",
            mongo_read=mongo_coro,
            pg_read=pg_read_mock,
            comparator_func=MagicMock(),
        )
        assert res == {"items": []}
        assert mongo_coro.await_count == 0
        fallback_event.assert_not_called()


@pytest.mark.asyncio
async def test_execute_shadow_read_records_controlled_fallback_when_postgres_unavailable():
    """Verify Mongo fallback is reason-coded when the PostgreSQL path is unavailable."""
    status_mock = MagicMock(
        mode=CutoverMode.postgres_read,
        read_source=DataSource.postgres,
    )
    mongo_coro = AsyncMock(return_value={"items": [{"id": "mongo-thread"}]})
    pg_read_mock = AsyncMock(return_value=RepositoryReadResult(status=RepositoryReadStatus.UNAVAILABLE))
    fallback_event = AsyncMock()

    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)), \
         patch("services.powerhouse_conversation_service._record_mongo_fallback_event", fallback_event):
        res = await _execute_shadow_read(
            building_id="13195",
            domain="powerhouse_conversations",
            route="GET /threads",
            mongo_read=mongo_coro,
            pg_read=pg_read_mock,
            comparator_func=MagicMock(),
        )
        assert res == {"items": [{"id": "mongo-thread"}]}
        fallback_event.assert_awaited_once()
        assert fallback_event.call_args.kwargs["reason"] == "postgres_unavailable"


@pytest.mark.asyncio
async def test_execute_shadow_read_records_diff_on_mismatch():
    """Verify that postgres_shadow mode records difference in core.shadow_diffs on mismatch."""
    status_mock = MagicMock(
        mode=CutoverMode.postgres_shadow,
        read_source=DataSource.mongo,
    )
    mongo_coro = AsyncMock(return_value={"data": "mongo_raw"})
    pg_read = AsyncMock(
        return_value=RepositoryReadResult(status=RepositoryReadStatus.SUCCESS, value={"data": "pg_divergent"})
    )

    comparator = MagicMock(return_value=(False, "field_mismatch", 0.8, "divergence detected"))
    record_diff_mock = AsyncMock()

    with patch("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=status_mock)), \
         patch("services.cutover_status_service.record_shadow_diff", record_diff_mock):

        res = await _execute_shadow_read(
            building_id="13195",
            domain="powerhouse_conversations",
            route="GET /threads",
            mongo_read=mongo_coro,
            pg_read=pg_read,
            comparator_func=comparator,
        )
        # Authoritative response must still be mongo
        assert res == {"data": "mongo_raw"}
        record_diff_mock.assert_called_once()
        assert record_diff_mock.call_args[1]["diff_type"] == "field_mismatch"
        assert record_diff_mock.call_args[1]["divergence_score"] == 0.8


def test_normalize_thread_handles_id():
    """Verify thread dictionary normalization maps Mongo '_id' to string 'id'."""
    raw = {
        "_id": "thread-mongo-123",
        "building_id": "13195",
        "subject": "Test thread",
        "participant_ids": ["user-1"],
    }
    normalized = _normalize_thread(raw)
    assert normalized["id"] == "thread-mongo-123"
    assert normalized["building_id"] == "13195"
    assert normalized["participant_ids"] == ["user-1"]


def test_compare_threads_list_identifies_count_mismatch():
    """Verify threads list comparator flags mismatched lengths."""
    mongo_res = {"items": [{"id": "1", "subject": "Thread 1"}]}
    pg_res = {"items": []}

    is_match, diff_type, score, details = compare_threads_list(mongo_res, pg_res)
    assert not is_match
    assert diff_type == "count_mismatch"
    assert score == 1.0
    assert "count mismatch" in details
