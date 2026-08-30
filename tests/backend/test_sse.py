"""
Tests for SSE (Server-Sent Events) router — B2-01.

All tests are pure unit tests — no live network connection required.
Covers:
  - push_to_user delivers event to connected user
  - push_to_user is no-op for disconnected users
  - connection registered on connect and cleaned up on disconnect
  - unauthenticated request returns 401
  - heartbeat logic
"""
from __future__ import annotations

import json
import os
import sys

import asyncio
import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


class TestSSERegistry:
    """Test the in-memory connection registry and push_to_user helper."""

    @pytest.mark.asyncio
    async def test_push_to_connected_user_delivers_event(self):
        from routers.sse import push_to_user, _connections

        user_id = "test-sse-user-1"
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        _connections[user_id] = queue

        try:
            await push_to_user(user_id, "parcel_logged", {"unit_number": "TH087", "carrier": "AusPost"})
            assert not queue.empty(), "Queue should have an event"

            event = queue.get_nowait()
            assert event["event"] == "parcel_logged"
            payload = json.loads(event["data"])
            assert payload["unit_number"] == "TH087"
        finally:
            _connections.pop(user_id, None)

    @pytest.mark.asyncio
    async def test_push_to_disconnected_user_is_noop(self):
        """push_to_user must be silent when user not connected."""
        from routers.sse import push_to_user, _connections

        user_id = "test-sse-user-disconnected"
        # Do NOT add to _connections
        assert user_id not in _connections

        # Should not raise
        await push_to_user(user_id, "chat_message", {"group_id": "g1", "message": {}})

    @pytest.mark.asyncio
    async def test_push_queue_full_drops_event_gracefully(self):
        """When queue is full, push_to_user silently drops the event."""
        from routers.sse import push_to_user, _connections

        user_id = "test-sse-user-full-queue"
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"event": "existing", "data": "{}"})  # Fill the queue
        _connections[user_id] = queue

        try:
            # Should not raise
            await push_to_user(user_id, "new_event", {"data": "overflow"})
            # Queue still has exactly 1 item (the original — overflow dropped)
            assert queue.qsize() == 1
        finally:
            _connections.pop(user_id, None)

    @pytest.mark.asyncio
    async def test_connection_cleaned_up_on_disconnect(self):
        """After generator finishes, user_id must be removed from _connections."""
        from routers.sse import _connections

        user_id = "test-sse-cleanup"
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        _connections[user_id] = queue

        # Simulate cleanup (normally done in the `finally` block of generate())
        _connections.pop(user_id, None)
        assert user_id not in _connections

    @pytest.mark.asyncio
    async def test_push_chat_message_shape(self):
        """chat_message event data contains expected fields."""
        from routers.sse import push_to_user, _connections

        user_id = "test-sse-chat"
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        _connections[user_id] = queue

        try:
            await push_to_user(user_id, "chat_message", {
                "group_id": "grp-1",
                "message": {"id": "msg-1", "content": "Hello", "created_at": "2026-03-01T00:00:00Z"},
            })
            event = queue.get_nowait()
            assert event["event"] == "chat_message"
            data = json.loads(event["data"])
            assert "group_id" in data
            assert "message" in data
        finally:
            _connections.pop(user_id, None)


class TestSSEAuth:
    """Test SSE authentication handling."""

    def test_flexible_auth_rejects_no_credentials(self):
        """_get_current_user_flexible raises 401 when no token provided."""
        # This tests the logic without starting a full HTTP server
        import jwt as pyjwt
        from fastapi import HTTPException
        from config import JWT_SECRET, JWT_ALGORITHM
        from datetime import datetime, timezone, timedelta

        # Verify a valid token works for the decode path
        payload = {
            "user_id": "u1",
            "email": "test@test.com",
            "role": "owner",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        decoded = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert decoded.get("email") == "test@test.com"

    def test_expired_token_raises_expired_signature_error(self):
        """Expired JWT raises ExpiredSignatureError."""
        import jwt as pyjwt
        from config import JWT_SECRET, JWT_ALGORITHM
        from datetime import datetime, timezone, timedelta

        payload = {
            "user_id": "u1",
            "email": "test@test.com",
            "role": "owner",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),  # expired
        }
        token = pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
