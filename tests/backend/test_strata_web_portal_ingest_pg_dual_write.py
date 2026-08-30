"""Tests for scripts/ingest/strata_web_portal_ingest.py::run()'s PG dual-write (B2b).

Verifies the Mongo staging write stays authoritative and unconditional -- a PG dual-write
failure is caught, reported, and never raised, so this function's long-standing Mongo-staging
contract (consumed by POST /settings/strata-web-portal/sync) cannot regress.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, "backend")

from scripts.ingest import strata_web_portal_ingest as ingest_mod  # noqa: E402


_SNAPSHOT = {
    "snapshot_date": "2026-08-06",
    "financial_year": "2026",
    "source": "strata_web_portal_scrape",
    "per_unit_balances": [{"lot_number": "1", "balance_cents": 0}],
}


def _mock_mongo_db():
    coll = SimpleNamespace()
    coll.find_one = AsyncMock(return_value=None)
    coll.update_one = AsyncMock()
    mongo = SimpleNamespace(_db=SimpleNamespace(staging_strata_web_snapshots=coll))
    return mongo, coll


class TestRunPgDualWrite:
    @pytest.mark.asyncio
    async def test_pg_failure_never_breaks_mongo_staging(self):
        """The Mongo update_one must have already happened before the PG attempt -- a PG
        exception must not roll it back or prevent run() from returning successfully."""
        mongo, coll = _mock_mongo_db()
        with (
            patch.object(ingest_mod, "build_snapshot", AsyncMock(return_value=_SNAPSHOT)),
            patch.object(ingest_mod, "db", mongo),
            patch(
                "db_postgres.repos.config_repo.resolve_scheme_context",
                AsyncMock(side_effect=RuntimeError("simulated PG outage")),
            ),
        ):
            result = await ingest_mod.run("13195", "2026", None, dry_run=False)

        coll.update_one.assert_awaited_once()
        assert result["dry_run"] is False
        assert result["pg_dual_write"]["attempted"] is False
        assert "error" in result["pg_dual_write"]
        assert "simulated PG outage" in result["pg_dual_write"]["error"]

    @pytest.mark.asyncio
    async def test_skips_pg_write_when_building_has_no_pg_scheme_yet(self):
        mongo, coll = _mock_mongo_db()
        with (
            patch.object(ingest_mod, "build_snapshot", AsyncMock(return_value=_SNAPSHOT)),
            patch.object(ingest_mod, "db", mongo),
            patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=None)),
        ):
            result = await ingest_mod.run("NEW-BUILDING", "2026", None, dry_run=False)

        assert result["pg_dual_write"]["attempted"] is False
        assert result["pg_dual_write"]["skipped_reason"] == "no PostgreSQL scheme for this building_id yet"

    @pytest.mark.asyncio
    async def test_dry_run_never_attempts_pg_write(self):
        mongo, coll = _mock_mongo_db()
        with (
            patch.object(ingest_mod, "build_snapshot", AsyncMock(return_value=_SNAPSHOT)),
            patch.object(ingest_mod, "db", mongo),
        ):
            result = await ingest_mod.run("13195", "2026", None, dry_run=True)

        coll.update_one.assert_not_awaited()
        assert result["dry_run"] is True
        assert "pg_dual_write" not in result
