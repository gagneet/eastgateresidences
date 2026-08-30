"""
Server-Sent Events (SSE) router — B2-01.

Replaces 5-second chat polling (ChatPage.jsx) with push-based real-time updates.
Uses in-memory per-connection queues — no Redis dependency required.
Heartbeat every 30 s keeps the connection alive through Cloudflare / nginx.

Supported event types
---------------------
chat_message    payload: {group_id, message}
parcel_logged   payload: {unit_number, carrier}
notification    payload: {title, message, type, link}
heartbeat       payload: {} (sent automatically every 30 s)

Authentication
--------------
Because the browser EventSource API does not support custom headers the
endpoint accepts the JWT via *either*:
  • Authorization: Bearer <token>  (preferred — used by programmatic clients)
  • ?token=<jwt>                   (fallback for browser EventSource)
"""
from __future__ import annotations

import json

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["SSE"])

# ─── Connection registry ──────────────────────────────────────────────────────
# Maps user_id → asyncio.Queue so we can push events to connected clients.
# This is process-local — fine for single-process Uvicorn deployment.
_connections: dict[str, asyncio.Queue] = {}


async def push_to_user(user_id: str, event_type: str, data: dict) -> None:
    """Push an event to a connected user.  No-op if the user is not connected."""
    queue = _connections.get(user_id)
    if queue is None:
        return
    try:
        queue.put_nowait({"event": event_type, "data": json.dumps(data)})
    except asyncio.QueueFull:
        # Drop the event — client will catch up on reconnect
        logger.debug("SSE queue full for user %s; dropping event %s", user_id, event_type)


# ─── Flexible auth (header OR query param) ────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def _get_current_user_flexible(
        request: Request,
        token_param: Optional[str] = Query(None, alias="token"),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """Resolve user from Authorization header *or* ?token= query parameter."""
    import jwt as pyjwt
    from config import JWT_SECRET, JWT_ALGORITHM

    raw_token: Optional[str] = None

    if credentials and credentials.credentials:
        raw_token = credentials.credentials
    elif token_param:
        raw_token = token_param

    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = pyjwt.decode(raw_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # JWT issued by utils.auth.create_token uses "user_id" — "sub"/"id" are
    # fallbacks for legacy NextAuth tokens or older issuers.
    user_id = payload.get("user_id") or payload.get("sub") or payload.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Build a minimal user dict from the JWT claims (avoids a DB round-trip per connect)
    return {
        "id": user_id,
        "email": payload.get("email", ""),
        "role": payload.get("role", ""),
        "building_id": payload.get("building_id", ""),
    }


# ─── SSE stream endpoint ──────────────────────────────────────────────────────

@router.get("/stream")
async def event_stream(
        request: Request,
        current_user: dict = Depends(_get_current_user_flexible),
):
    """
    SSE endpoint.  Subscribe and receive real-time events.
    A heartbeat is emitted every 30 s to keep the connection alive.
    The browser EventSource automatically reconnects on error.
    """
    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="sse-starlette is not installed. Add sse-starlette>=1.8.0 to requirements.txt",
        )

    user_id = current_user["id"]
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _connections[user_id] = queue

    async def generate():
        """Generated function header.

        Function: generate
        Path: backend/routers/sse.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            _connections.pop(user_id, None)
            logger.debug("SSE connection closed for user %s", user_id)

    return EventSourceResponse(generate())


__all__ = ["router", "push_to_user"]
