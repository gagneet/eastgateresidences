"""SQLAlchemy async engine factory for the PostgreSQL financial core.

The engine is created lazily on first use. If DATABASE_URL is not configured
(e.g. in existing dev/CI environments that have not set up Postgres yet) all
existing MongoDB routes continue working — calling get_engine() will raise a
clear RuntimeError only when a Postgres write is actually attempted.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from config import DATABASE_URL

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None


def get_engine() -> AsyncEngine:
    """Return the shared async engine, creating it on first call.

    Raises RuntimeError when DATABASE_URL is not configured so that the
    error surface is limited to callers that actually need Postgres.
    """
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not configured. "
                "Set DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db "
                "in backend/.env before using the PostgreSQL financial core."
            )
        _engine = create_async_engine(
            DATABASE_URL,
            # The backend runs as 4 separate uvicorn worker PROCESSES
            # (`uvicorn server:app --workers 4`), each importing this module
            # independently — the module-level `_engine` singleton below is
            # per-process, not shared, so these limits apply four times over.
            # Postgres on this host has max_connections=100 shared with other
            # apps (~11 connections observed from unrelated services); the
            # previous pool_size=20/max_overflow=10 meant up to 4*30=120
            # possible connections against ~89 actually available, and a
            # steady-state ~78-80 idle connections already consumed most of
            # that headroom even with no active load. A k6 burst of 40
            # concurrent finance.summary requests (2026-07-13) confirmed this:
            # the Postgres side of the shadow-read comparison failed with
            # "pg_unavailable" 711 times in 15 minutes purely from connection
            # exhaustion, not from any bug in the comparison logic itself.
            # pool_size=5/max_overflow=10 keeps steady-state idle connections
            # at 4*5=20 and caps burst usage at 4*15=60, comfortably under the
            # ~89 available.
            pool_size=5,
            max_overflow=10,
            pool_timeout=10,
            pool_recycle=1800,
            pool_pre_ping=True,
            # Enable echo only via SQLALCHEMY_ECHO env var to avoid log noise
            echo=False,
            # asyncpg JSONB codec
            json_serializer=_json_serializer,
            json_deserializer=_json_deserializer,
        )
        logger.info("PostgreSQL financial core engine initialised.")
    return _engine


async def dispose_engine() -> None:
    """Gracefully dispose the engine pool (call during application shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("PostgreSQL financial core engine disposed.")


def _json_serializer(obj) -> str:  # type: ignore[return]
    """Generated function header.

    Function: _json_serializer
    Path: backend/db_postgres/engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import json
    return json.dumps(obj, default=str)


def _json_deserializer(raw: str):  # type: ignore[return]
    """Generated function header.

    Function: _json_deserializer
    Path: backend/db_postgres/engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import json
    return json.loads(raw)
