"""Async session factory and per-transaction tenant context helpers.

RLS tenant isolation relies on the PostgreSQL GUC ``app.tenant_id``.
Every transaction that touches a tenant-scoped table MUST call
``set_tenant(session, tenant_id)`` before issuing any query.

The GUC is set with ``SET LOCAL`` so it is scoped to the current
transaction and cannot leak across pooled connections.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
except ImportError:
    # SQLAlchemy < 2.0 — use sessionmaker with class_=AsyncSession
    from sqlalchemy.orm import sessionmaker as _async_sessionmaker  # type: ignore[assignment]

from .engine import get_engine


def make_session_factory():
    """Create an async_sessionmaker bound to the shared engine."""
    return _async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@asynccontextmanager
async def async_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager that yields a session, commits on success, rolls back on error.

    Usage::

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            result = await session.execute(...)
    """
    factory = make_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def set_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Set the RLS tenant context GUC for the current transaction.

    Must be called exactly once per transaction, before any query on a
    tenant-scoped table. Uses ``SET LOCAL`` so pooled connections cannot
    carry a stale tenant context into the next transaction.

    Args:
        session: The active AsyncSession.
        tenant_id: The UUID string of the tenant (maps to core.tenants.tenant_id).
    """
    # PostgreSQL SET LOCAL does not accept bind parameters — the value must
    # be a literal in the SQL string.  UUIDs only contain hex chars and
    # dashes, so string-formatting them here is safe from SQL injection.
    tid = str(tenant_id)
    await session.execute(text(f"SET LOCAL app.tenant_id = '{tid}'"))


async def clear_tenant(session: AsyncSession) -> None:
    """Clear the RLS tenant GUC (mainly for testing to prevent context bleed)."""
    await session.execute(text("SET LOCAL app.tenant_id = ''"))
