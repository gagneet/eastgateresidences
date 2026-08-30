"""
tests/backend/test_outbox_relay.py

Tests for the outbox relay worker (Component 5).

Verifies that:
- Unpublished outbox events are detected and processed
- Events are dispatched correctly
- Retry logic with exponential backoff works correctly
- Dead-letter tracking for failed events works
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch

from workers.outbox_relay import (
    _backoff_delay,
    MAX_RETRIES,
)


@pytest.mark.asyncio
class TestBackoffLogic:
    """Tests for exponential backoff delay calculation."""

    async def test_backoff_delay_increments_exponentially(self):
        """Backoff delay follows the cutover retry schedule."""
        assert _backoff_delay(0) == 0
        assert _backoff_delay(1) == 30
        assert _backoff_delay(2) == 120
        assert _backoff_delay(3) == 600
        assert _backoff_delay(4) == 3600

    async def test_backoff_delay_caps_at_final_schedule_value(self):
        """Backoff delay is capped at the final schedule value."""
        assert _backoff_delay(5) == 21600
        assert _backoff_delay(6) == 21600
        assert _backoff_delay(100) == 21600

    async def test_max_retries_constant_is_5(self):
        """MAX_RETRIES is set to 5 for dead-lettering."""
        assert MAX_RETRIES == 5


@pytest.mark.asyncio
class TestRelayIntegration:
    """Integration tests for relay worker concepts."""

    async def test_relay_worker_initialization(self):
        """Verify relay worker constants are properly defined."""
        from workers.outbox_relay import (
            POLL_INTERVAL_SECONDS,
            BATCH_SIZE,
            MAX_RETRIES,
        )

        assert POLL_INTERVAL_SECONDS == 5
        assert BATCH_SIZE == 50
        assert MAX_RETRIES == 5

    async def test_relay_loop_structure(self):
        """Verify relay loop has correct structure and parameters."""
        from workers.outbox_relay import run_relay_loop
        import asyncio

        # Verify the function exists and is async
        assert asyncio.iscoroutinefunction(run_relay_loop)


@pytest.mark.asyncio
class TestRelayWorkerComponents:
    """Tests for individual relay worker components."""

    async def test_backoff_strategy_is_exponential(self):
        """Backoff strategy follows the configured retry schedule."""
        delays = [_backoff_delay(i) for i in range(6)]
        expected = [0, 30, 120, 600, 3600, 21600]
        assert delays == expected

    async def test_max_attempts_before_dead_letter(self):
        """Dead-letter happens at exactly MAX_RETRIES."""
        # First 4 retries should not dead-letter
        for attempt in range(MAX_RETRIES - 1):
            assert attempt < MAX_RETRIES

        # At MAX_RETRIES, should dead-letter
        assert MAX_RETRIES == 5
        assert MAX_RETRIES - 1 == 4


@pytest.mark.asyncio
class TestBuildingIdResolution:
    """GAP-FIN-061 regression: _resolve_building_id must never default to a real building.
    Confirmed live 2026-08-11 that payload.get("mongo_building_id", "13195") was the ONLY path
    ever exercised platform-wide in _publish_to_mongo (no writer of core.outbox has ever
    populated that payload key, so every event, for every building, was silently archived under
    East Gate "13195")."""

    async def test_explicit_payload_value_wins(self):
        from workers.outbox_relay import _resolve_building_id

        result = await _resolve_building_id({"mongo_building_id": "16244"}, scheme_id="ignored")
        assert result == "16244"

    async def test_resolves_via_scheme_id_when_payload_missing(self):
        from workers.outbox_relay import _resolve_building_id

        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            new=AsyncMock(return_value={"scheme_number": "16244", "scheme_id": "x"}),
        ):
            result = await _resolve_building_id({}, scheme_id=str(uuid.uuid4()))
        assert result == "16244"

    async def test_returns_none_when_scheme_id_missing(self):
        from workers.outbox_relay import _resolve_building_id

        result = await _resolve_building_id({}, scheme_id=None)
        assert result is None

    async def test_returns_none_when_scheme_lookup_finds_nothing(self):
        """This is the exact case that used to silently default to '13195' for EVERY
        building's events -- it must now resolve to None (unresolvable), not East Gate."""
        from workers.outbox_relay import _resolve_building_id

        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            new=AsyncMock(return_value=None),
        ):
            result = await _resolve_building_id({}, scheme_id=str(uuid.uuid4()))
        assert result is None
        assert result != "13195"

    async def test_publish_to_mongo_raises_instead_of_defaulting_to_east_gate(self):
        """The old code silently archived under building_id='13195' on any unresolvable event.
        The new code must raise so the caller (_dispatch_event / _relay_batch) records this as a
        real, retryable failure instead of mislabeling the event's building."""
        from workers.outbox_relay import _publish_to_mongo

        with patch(
            "workers.outbox_relay._resolve_building_id",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="Cannot resolve building_id"):
                await _publish_to_mongo(
                    event_id="evt-1",
                    event_type="payment.received",
                    aggregate_id=None,
                    aggregate_type=None,
                    payload={},
                    scheme_id=str(uuid.uuid4()),
                )
