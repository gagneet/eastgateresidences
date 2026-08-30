"""
Tests for services/shadow_read_service.py — the generic, domain-agnostic shadow-read
comparison engine extracted from finance_shadow_read_service.py on 2026-07-14.

Covers:
  1.  comparator primitives (money/count/status/list-length/boolean-match)
  2.  run_shadow_compare with a caller-supplied comparator
  3.  coverage-counter day classification (all 5 outcomes)
  4.  safe background-task scheduler: exception logging, bounded concurrency, skip-on-capacity
  5.  domain canonicalization passthrough
  6.  multi-tenant isolation (building_id never leaks across calls)
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.shadow_read_service import (
    FieldDiff,
    ShadowCompareResult,
    compare_boolean_match_fields,
    compare_count_fields,
    compare_list_lengths,
    compare_money_fields,
    compare_status_fields,
    classify_route_day,
    get_domain_concurrency_limit,
    run_shadow_compare,
    schedule_shadow_compare,
)

BUILDING_A = "13195"
BUILDING_B = "16244"


def _mock_ctx(mock_session: AsyncMock) -> MagicMock:
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx


# ---------------------------------------------------------------------------
# 1. Comparator primitives
# ---------------------------------------------------------------------------

class TestComparatorPrimitives:
    def test_compare_money_fields_within_tolerance_returns_none(self):
        assert compare_money_fields(field_path="x", mongo_aud=100.50, pg_cents=10050) is None

    def test_compare_money_fields_outside_tolerance_returns_diff(self):
        diff = compare_money_fields(field_path="x", mongo_aud=100.50, pg_cents=9000)
        assert diff is not None
        assert diff.severity == "critical"
        assert diff.diff_cents == 10050 - 9000

    def test_compare_money_fields_small_diff_is_warn_not_critical(self):
        diff = compare_money_fields(field_path="x", mongo_aud=100.50, pg_cents=10000)
        assert diff is not None
        assert diff.severity == "warn"

    def test_compare_count_fields_equal_returns_none(self):
        assert compare_count_fields(field_path="n", mongo_count=5, pg_count=5) is None

    def test_compare_count_fields_unequal_returns_diff(self):
        diff = compare_count_fields(field_path="n", mongo_count=5, pg_count=3)
        assert diff is not None
        assert diff.severity == "warn"

    def test_compare_status_fields_case_insensitive_match(self):
        assert compare_status_fields(field_path="s", mongo_status="Active", pg_status="active") is None

    def test_compare_status_fields_mismatch_returns_diff(self):
        diff = compare_status_fields(field_path="s", mongo_status="active", pg_status="archived")
        assert diff is not None
        assert diff.severity == "info"

    def test_compare_list_lengths_delegates_to_count(self):
        assert compare_list_lengths(field_path="l", mongo_list=[1, 2], pg_list=[1, 2]) is None
        diff = compare_list_lengths(field_path="l", mongo_list=[1, 2, 3], pg_list=[1])
        assert diff is not None

    def test_compare_boolean_match_fields_equal_returns_none(self):
        assert compare_boolean_match_fields(field_path="email_hash", mongo_value="abc", pg_value="abc") is None

    def test_compare_boolean_match_fields_mismatch_returns_diff_without_raw_values_semantics(self):
        # The caller is responsible for hashing before calling this — this primitive
        # itself just compares whatever it's given, which must already be redacted.
        diff = compare_boolean_match_fields(field_path="email_hash", mongo_value="hash1", pg_value="hash2")
        assert diff is not None
        assert diff.severity == "warn"


# ---------------------------------------------------------------------------
# 2. run_shadow_compare with a caller-supplied comparator
# ---------------------------------------------------------------------------

class TestRunShadowCompareGeneric:
    @pytest.mark.asyncio
    async def test_pg_unavailable_records_diff_and_coverage(self):
        recorded_diffs = []
        recorded_coverage = []

        async def fake_record_diff(**kwargs):
            recorded_diffs.append(kwargs)

        async def fake_record_coverage(**kwargs):
            recorded_coverage.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage):
            result = await run_shadow_compare(
                building_id=BUILDING_A, domain="identity_core", route_key="identity.users.list",
                mongo_payload={"count": 5}, pg_payload=None,
                comparator=lambda **kw: [],
            )

        assert result.pg_available is False
        assert result.error == "pg_payload_unavailable"
        assert recorded_diffs[0]["diff_type"] == "pg_unavailable"
        assert recorded_coverage[0]["pg_unavailable"] == 1

    @pytest.mark.asyncio
    async def test_comparator_exception_is_caught_and_recorded_as_compare_error(self):
        recorded_coverage = []

        async def fake_record_coverage(**kwargs):
            recorded_coverage.append(kwargs)

        def bad_comparator(**kwargs):
            raise ValueError("boom")

        with patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage):
            result = await run_shadow_compare(
                building_id=BUILDING_A, domain="identity_core", route_key="identity.users.list",
                mongo_payload={}, pg_payload={},
                comparator=bad_comparator,
            )

        assert result.matched is False
        assert "compare_error" in result.error
        assert recorded_coverage[0]["compare_error"] == 1

    @pytest.mark.asyncio
    async def test_matching_payloads_record_matched_coverage_not_diff(self):
        recorded_diffs = []
        recorded_coverage = []

        async def fake_record_diff(**kwargs):
            recorded_diffs.append(kwargs)

        async def fake_record_coverage(**kwargs):
            recorded_coverage.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await run_shadow_compare(
                building_id=BUILDING_A, domain="identity_core", route_key="identity.users.list",
                mongo_payload={"n": 1}, pg_payload={"n": 1},
                comparator=lambda **kw: [],
            )

        assert result.matched is True
        assert recorded_diffs == []  # shadow_ok suppressed by the throttle mock
        assert recorded_coverage[0]["matched"] == 1

    @pytest.mark.asyncio
    async def test_mismatched_payloads_record_field_mismatch_diff(self):
        recorded_diffs = []
        recorded_coverage = []

        async def fake_record_diff(**kwargs):
            recorded_diffs.append(kwargs)

        async def fake_record_coverage(**kwargs):
            recorded_coverage.append(kwargs)

        def comparator(**kwargs):
            return [FieldDiff(field_path="n", mongo_value=1, pg_value=2, tolerance_cents=0, severity="critical")]

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage):
            result = await run_shadow_compare(
                building_id=BUILDING_A, domain="identity_core", route_key="identity.users.list",
                mongo_payload={"n": 1}, pg_payload={"n": 2},
                comparator=comparator,
            )

        assert result.matched is False
        assert recorded_diffs[0]["diff_type"] == "field_mismatch:critical"
        assert recorded_coverage[0]["mismatch"] == 1

    @pytest.mark.asyncio
    async def test_domain_alias_canonicalized_before_recording(self):
        recorded_diffs = []

        async def fake_record_diff(**kwargs):
            recorded_diffs.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await run_shadow_compare(
                building_id=BUILDING_A, domain="identity", route_key="x",  # alias, not canonical
                mongo_payload={}, pg_payload=None, comparator=lambda **kw: [],
            )
        assert result.domain == "identity_core"
        assert recorded_diffs[0]["domain"] == "identity_core"


# ---------------------------------------------------------------------------
# 3. Coverage-counter day classification
# ---------------------------------------------------------------------------

class TestClassifyRouteDay:
    async def _classify_with_row(self, row: MagicMock | None) -> str:
        mock_result = MagicMock()
        mock_result.fetchone.return_value = row
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("services.shadow_read_service._get_bypass_session_context",
                   return_value=_mock_ctx(mock_session)):
            return await classify_route_day(
                building_id=BUILDING_A, domain="identity_core",
                route="identity.users.list", coverage_date=date.today(),
            )

    @pytest.mark.asyncio
    async def test_no_row_is_no_traffic(self):
        assert await self._classify_with_row(None) == "no_traffic"

    @pytest.mark.asyncio
    async def test_zero_attempted_is_no_traffic(self):
        row = MagicMock(attempted_count=0, matched_count=0, mismatch_count=0,
                         pg_unavailable_count=0, compare_error_count=0, timeout_count=0)
        assert await self._classify_with_row(row) == "no_traffic"

    @pytest.mark.asyncio
    async def test_below_minimum_attempts_is_insufficient_coverage(self):
        row = MagicMock(attempted_count=1, matched_count=1, mismatch_count=0,
                         pg_unavailable_count=0, compare_error_count=0, timeout_count=0)
        assert await self._classify_with_row(row) == "insufficient_coverage"

    @pytest.mark.asyncio
    async def test_infrastructure_failure_when_pg_unavailable_present(self):
        row = MagicMock(attempted_count=5, matched_count=2, mismatch_count=0,
                         pg_unavailable_count=3, compare_error_count=0, timeout_count=0)
        assert await self._classify_with_row(row) == "infrastructure_failure"

    @pytest.mark.asyncio
    async def test_infrastructure_failure_when_timeout_present(self):
        row = MagicMock(attempted_count=5, matched_count=2, mismatch_count=0,
                         pg_unavailable_count=0, compare_error_count=0, timeout_count=3)
        assert await self._classify_with_row(row) == "infrastructure_failure"

    @pytest.mark.asyncio
    async def test_dirty_when_adequate_coverage_with_mismatches(self):
        row = MagicMock(attempted_count=10, matched_count=7, mismatch_count=3,
                         pg_unavailable_count=0, compare_error_count=0, timeout_count=0)
        assert await self._classify_with_row(row) == "dirty"

    @pytest.mark.asyncio
    async def test_clean_when_adequate_coverage_all_matched(self):
        row = MagicMock(attempted_count=10, matched_count=10, mismatch_count=0,
                         pg_unavailable_count=0, compare_error_count=0, timeout_count=0)
        assert await self._classify_with_row(row) == "clean"

    @pytest.mark.asyncio
    async def test_query_failure_is_infrastructure_failure_not_silently_clean(self):
        with patch("services.shadow_read_service._get_bypass_session_context",
                   side_effect=RuntimeError("db down")):
            result = await classify_route_day(
                building_id=BUILDING_A, domain="identity_core",
                route="identity.users.list", coverage_date=date.today(),
            )
        assert result == "infrastructure_failure"


# ---------------------------------------------------------------------------
# 4. Safe background-task scheduler
# ---------------------------------------------------------------------------

class TestScheduleShadowCompare:
    @pytest.mark.asyncio
    async def test_successful_coroutine_runs_to_completion(self):
        ran = {"done": False}

        async def work():
            ran["done"] = True

        task = schedule_shadow_compare(work(), domain="identity_core", context={"building_id": BUILDING_A})
        assert task is not None
        await task
        assert ran["done"] is True

    @pytest.mark.asyncio
    async def test_exception_in_coroutine_is_caught_and_logged_not_raised(self):
        async def bad_work():
            raise ValueError("boom")

        task = schedule_shadow_compare(bad_work(), domain="identity_core", context={})
        # Must not raise — the whole point is the caller never awaits/propagates this.
        await task

    @pytest.mark.asyncio
    async def test_skips_and_records_when_domain_concurrency_exhausted(self):
        recorded = []

        async def fake_record_coverage(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage), \
             patch("services.shadow_read_service.get_domain_concurrency_limit", return_value=1):
            # Force a fresh semaphore for this domain at the patched limit of 1.
            import services.shadow_read_service as srs
            srs._domain_semaphores.pop("governance", None)

            blocker = asyncio.Event()

            async def hold():
                await blocker.wait()

            first_task = schedule_shadow_compare(hold(), domain="governance", context={"building_id": BUILDING_A})
            await asyncio.sleep(0)  # let first task acquire the semaphore

            second_task = schedule_shadow_compare(
                asyncio.sleep(0), domain="governance",
                context={"building_id": BUILDING_A, "route": "governance.meetings.list"},
            )
            await second_task

            blocker.set()
            await first_task

        assert any(r.get("skipped_capacity") == 1 for r in recorded)

    def test_get_domain_concurrency_limit_defaults(self):
        assert get_domain_concurrency_limit("some_new_domain") == 5


# ---------------------------------------------------------------------------
# 5. Multi-tenant isolation
# ---------------------------------------------------------------------------

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_different_buildings_never_share_a_coverage_row_lookup(self):
        calls = []

        async def fake_record_coverage(**kwargs):
            calls.append(kwargs["building_id"])

        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()), \
             patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage):
            await run_shadow_compare(
                building_id=BUILDING_A, domain="identity_core", route_key="x",
                mongo_payload={}, pg_payload=None, comparator=lambda **kw: [],
            )
            await run_shadow_compare(
                building_id=BUILDING_B, domain="identity_core", route_key="x",
                mongo_payload={}, pg_payload=None, comparator=lambda **kw: [],
            )

        assert calls == [BUILDING_A, BUILDING_B]

    @pytest.mark.asyncio
    async def test_classify_route_day_queries_scoped_to_building_id(self):
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("services.shadow_read_service._get_bypass_session_context",
                   return_value=_mock_ctx(mock_session)):
            await classify_route_day(
                building_id=BUILDING_B, domain="identity_core",
                route="identity.users.list", coverage_date=date.today(),
            )

        bound_params = mock_session.execute.await_args.args[1]
        assert bound_params["building_id"] == BUILDING_B
