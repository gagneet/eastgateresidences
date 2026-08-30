"""Alembic migration environment — async-capable via asyncpg.

Run migrations from the backend/ directory:

    DATABASE_URL=postgresql+asyncpg://user:pass@host/db alembic upgrade head
    DATABASE_URL=postgresql+asyncpg://user:pass@host/db alembic downgrade -1

The Alembic version table is stored in the ``core`` schema
(``core.alembic_version``) so it co-locates with the financial core tables.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

# Make backend/ importable so alembic can resolve db_postgres.base.Base
sys.path.insert(0, str(Path(__file__).parent.parent))

# Match the rest of the backend: load backend/.env before resolving DATABASE_URL
load_dotenv(Path(__file__).parent.parent / ".env")

from db_postgres.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use all ORM metadata so autogenerate can detect table drift.
# For initial migrations this is empty until models are defined.
target_metadata = Base.metadata

# Resolve DATABASE_URL from environment (env var takes priority over alembic.ini).
_DATABASE_URL = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url", "")

if not _DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set before running alembic migrations. "
        "Example: DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/strataos"
    )


def run_migrations_offline() -> None:
    """Emit SQL DDL to stdout without a live connection (for review/auditing)."""
    context.configure(
        url=_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema="core",
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    # Alembic creates its own bookkeeping table (core.alembic_version, per
    # version_table_schema below) before running any migration's upgrade() —
    # including 0001's own "CREATE SCHEMA IF NOT EXISTS core". On a genuinely
    # fresh database that ordering fails with "schema core does not exist"
    # before migration 0001 ever runs. Idempotent no-op against an
    # already-migrated database.
    """Generated function header.

    Function: _do_run_migrations
    Path: backend/alembic/env.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS core"))
    # Commit immediately, independent of Alembic's own transaction. Otherwise
    # this statement implicitly auto-begins a transaction on the connection
    # before context.begin_transaction() below opens its own — Alembic then
    # nests inside that pre-existing transaction rather than owning it, and
    # closing the connection without an explicit outer commit silently rolls
    # back everything (every migration in the run), not just this statement.
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema="core",
        # Compare server defaults for accurate autogenerate
        compare_server_defaults=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async database connection."""
    engine = create_async_engine(_DATABASE_URL)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run_migrations)
    await engine.dispose()


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/alembic/env.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())


main()
