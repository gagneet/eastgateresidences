"""
tests/backend/db_postgres/test_ownership_repo.py

Regression coverage for a 2026-07-24 finding: close_ownership_period() used
to return None unconditionally, so a caller had no way to tell "closed the
prior period" apart from "there was nothing to close" — both looked
identical. That mattered because when nothing gets closed but a subsequent
open_ownership_period() call for the same lot still fires, the INSERT
silently collides with the still-open prior period via the table's EXCLUDE
constraint and gets dropped by ON CONFLICT DO NOTHING — invisible unless the
caller checks. Found live: unit TH078's owner-transfer approval left
core.ownership_periods stale for weeks with no error anywhere.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db_postgres.repos.ownership_repo import close_ownership_period


def _session_with_rowcount(rowcount: int) -> MagicMock:
    session = MagicMock()
    result = MagicMock(rowcount=rowcount)
    session.execute = AsyncMock(return_value=result)
    return session


class TestCloseOwnershipPeriod:
    @pytest.mark.asyncio
    async def test_returns_the_number_of_rows_closed(self):
        session = _session_with_rowcount(1)
        result = await close_ownership_period(session, "lot-1", date(2026, 7, 1), "tenant-1")
        assert result == 1

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_was_open_to_close(self):
        """A caller relying on the old None-always return could not distinguish
        this from a successful close — the exact ambiguity that let TH078's
        stale period go unnoticed."""
        session = _session_with_rowcount(0)
        result = await close_ownership_period(session, "lot-1", date(2026, 7, 1), "tenant-1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_closes_multiple_open_joint_owner_periods_for_the_same_lot(self):
        """A lot with joint owners (e.g. TH078: Olivia Rollings + Mark Raets,
        each their own primary/secondary ownership_periods row) must have BOTH
        rows closed by one call — the WHERE clause matches on lot_id alone,
        not owner_party_id, by design."""
        session = _session_with_rowcount(2)
        result = await close_ownership_period(session, "lot-78", date(2026, 7, 1), "tenant-1")
        assert result == 2
