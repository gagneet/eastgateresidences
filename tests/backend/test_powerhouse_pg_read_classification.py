from __future__ import annotations

# @featuretrace:powerhouse-foundation — Typed PG read classification so programming
# errors never masquerade as "PostgreSQL unavailable" and silently trigger Mongo continuity.
# Layer: test
# Data flow: PG read exception → classify_pg_exception → RepositoryReadStatus → continuity decision.
# Related: backend/services/powerhouse_command_foundation.py
#          backend/services/powerhouse_conversation_service.py

import asyncio

import pytest

from fastapi import HTTPException

from services.powerhouse_command_foundation import (
    RepositoryReadResult,
    RepositoryReadStatus,
    classify_pg_exception,
    continuity_reason_for_read_result,
    execute_pg_read,
)


class _FakePgError(Exception):
    """Mimic an asyncpg driver error carrying a SQLSTATE code."""

    def __init__(self, sqlstate: str, msg: str = "pg error"):
        super().__init__(msg)
        self.sqlstate = sqlstate


class _FakeWrappedError(Exception):
    """Mimic a SQLAlchemy DBAPIError wrapping the driver error via ``.orig``."""

    def __init__(self, orig: Exception, msg: str = "(psycopg) wrapped"):
        super().__init__(msg)
        self.orig = orig


class OperationalError(Exception):
    """Name-matches SQLAlchemy's connection-level error (no SQLSTATE)."""


# --------------------------------------------------------------------------- #
# classify_pg_exception
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("42703", RepositoryReadStatus.APPLICATION_ERROR),  # undefined_column (Finding 3)
        ("42601", RepositoryReadStatus.APPLICATION_ERROR),  # syntax_error
        ("23505", RepositoryReadStatus.APPLICATION_ERROR),  # unique_violation
        ("42P01", RepositoryReadStatus.SCHEMA_NOT_READY),   # undefined_table
        ("3F000", RepositoryReadStatus.SCHEMA_NOT_READY),   # invalid_schema_name
        ("08006", RepositoryReadStatus.UNAVAILABLE),        # connection_failure
        ("08003", RepositoryReadStatus.UNAVAILABLE),        # connection_does_not_exist
        ("57014", RepositoryReadStatus.TIMEOUT),            # query_canceled
        ("42501", RepositoryReadStatus.PERMISSION_FAILURE), # insufficient_privilege
        ("28P01", RepositoryReadStatus.PERMISSION_FAILURE), # invalid_password
    ],
)
def test_classify_by_sqlstate(sqlstate, expected):
    assert classify_pg_exception(_FakePgError(sqlstate)) == expected


def test_classify_unwraps_sqlalchemy_orig():
    # A SQLAlchemy-wrapped undefined_column must still be an application error,
    # not a silent Mongo fallback.
    wrapped = _FakeWrappedError(_FakePgError("42703"))
    assert classify_pg_exception(wrapped) == RepositoryReadStatus.APPLICATION_ERROR


def test_classify_timeout_error():
    assert classify_pg_exception(asyncio.TimeoutError()) == RepositoryReadStatus.TIMEOUT
    assert classify_pg_exception(TimeoutError()) == RepositoryReadStatus.TIMEOUT


def test_classify_connection_error():
    assert classify_pg_exception(ConnectionError("refused")) == RepositoryReadStatus.UNAVAILABLE
    assert classify_pg_exception(OSError("socket")) == RepositoryReadStatus.UNAVAILABLE


def test_classify_sqlalchemy_connection_error_by_class_name():
    # No SQLSTATE, but the class name identifies a connection-level failure.
    assert classify_pg_exception(OperationalError("server closed")) == RepositoryReadStatus.UNAVAILABLE


def test_classify_import_error_is_schema_not_ready():
    assert classify_pg_exception(ImportError("no db_postgres")) == RepositoryReadStatus.SCHEMA_NOT_READY


def test_classify_unknown_error_is_application_error_not_fallback():
    # The critical guardrail: an unclassified programming defect must fail loud,
    # never silently activate Mongo continuity.
    status = classify_pg_exception(ValueError("mapping bug"))
    assert status == RepositoryReadStatus.APPLICATION_ERROR
    assert continuity_reason_for_read_result(RepositoryReadResult(status=status, value=None)) is None


# --------------------------------------------------------------------------- #
# execute_pg_read
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_execute_pg_read_empty_list_is_success_not_fallback():
    async def _read():
        return []

    result = await execute_pg_read(_read)
    assert result.status == RepositoryReadStatus.SUCCESS
    assert result.value == []
    # Empty success must never permit continuity.
    assert continuity_reason_for_read_result(result) is None


@pytest.mark.asyncio
async def test_execute_pg_read_wraps_value():
    async def _read():
        return [{"id": "t1"}]

    result = await execute_pg_read(_read, wrap_value=lambda items: {"items": items, "limit": 25})
    assert result.status == RepositoryReadStatus.SUCCESS
    assert result.value == {"items": [{"id": "t1"}], "limit": 25}


@pytest.mark.asyncio
async def test_execute_pg_read_none_maps_to_unavailable_for_backward_compat():
    async def _read():
        return None

    result = await execute_pg_read(_read, none_detail="not configured")
    assert result.status == RepositoryReadStatus.UNAVAILABLE
    # Preserves the established fallback behaviour for a building with no PG context.
    assert continuity_reason_for_read_result(result) is not None


@pytest.mark.asyncio
async def test_execute_pg_read_programming_error_forbids_fallback():
    async def _read():
        raise _FakePgError("42703")  # undefined_column

    result = await execute_pg_read(_read)
    assert result.status == RepositoryReadStatus.APPLICATION_ERROR
    assert continuity_reason_for_read_result(result) is None


@pytest.mark.asyncio
async def test_execute_pg_read_connection_error_permits_fallback():
    async def _read():
        raise _FakePgError("08006")  # connection_failure

    result = await execute_pg_read(_read)
    assert result.status == RepositoryReadStatus.UNAVAILABLE
    assert continuity_reason_for_read_result(result) is not None


@pytest.mark.asyncio
async def test_execute_pg_read_reraises_http_exception():
    async def _read():
        raise HTTPException(status_code=403, detail="visibility denied")

    with pytest.raises(HTTPException) as excinfo:
        await execute_pg_read(_read)
    assert excinfo.value.status_code == 403
