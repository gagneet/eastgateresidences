# @featuretrace:finance-shadow-reads — Tests for finance shadow-read comparison service.
# Layer: test
# Data flow: finance routes → finance_shadow_read_service → financial_read_service (mocked)
#            → record_shadow_diff (mocked) → core.shadow_diffs.
# Scope: (building-scoped)
# Related: backend/services/finance_shadow_read_service.py
#          backend/services/financial_read_service.py
#          backend/services/cutover_status_service.py
#          docs/migration/finance-shadow-reads.md
# Toggle: financial_shadow_reads_enabled
"""Tests for the finance shadow-read comparison service (Prompt 5).

Rules:
  - MongoDB is always the response source; PG is comparison-only.
  - All money values use integer cents for comparison.
  - Shadow failures must NEVER alter the API response.
  - Shadow reads only run when financial_shadow_reads_enabled is active for the building.
  - Cross-building isolation: Building A shadow state must not affect Building B.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.finance_shadow_read_service import (
    NOT_APPLICABLE,
    FieldDiff,
    ShadowCompareResult,
    _get_pg_payload_for_route,
    compare_count_fields,
    compare_list_lengths,
    compare_money_fields,
    compare_status_fields,
    get_building_finance_shadow_readiness,
    get_route_shadow_readiness,
    maybe_run_finance_shadow,
    run_shadow_compare,
    summarize_shadow_result,
)
from services.finance_shadow_read_service import _NotApplicable


# run_shadow_compare() calls shadow_read_service._safe_record_coverage() (added
# 2026-07-14 so finance feeds the same coverage-aware clean-day gate as other
# domains) via services.finance_shadow_read_service's own imported reference to it.
# _safe_record_coverage is best-effort/exception-swallowing by design, so an
# unmocked call doesn't fail a test — it silently opens a real DB connection and
# writes a real row to core.shadow_read_coverage_daily instead. Confirmed live:
# running this file without this fixture wrote 5 real rows, including one with
# is_test_data=False for real building 13195 (cleaned up same session). Autouse
# mock here for the same reason as test_identity_bootstrap_service.py's
# _no_real_cutover_status_writes fixture — no test in this file asserts on
# coverage-recording arguments, so this cannot mask a real assertion.
@pytest.fixture(autouse=True)
def _no_real_coverage_writes():
    with patch(
        "services.finance_shadow_read_service._safe_record_coverage",
        new=AsyncMock(),
    ) as mock_record:
        yield mock_record

BUILDING_A = "13195"
BUILDING_B = "16244"


# ---------------------------------------------------------------------------
# compare_money_fields
# ---------------------------------------------------------------------------

class TestCompareMoneyFields:
    def test_exact_match_returns_none(self):
        result = compare_money_fields(
            field_path="total_levied",
            mongo_aud=123.45,
            pg_cents=12345,
        )
        assert result is None

    def test_mismatch_returns_diff(self):
        result = compare_money_fields(
            field_path="total_levied",
            mongo_aud=123.46,
            pg_cents=12345,
        )
        assert result is not None
        assert result.field_path == "total_levied"
        assert result.mongo_value == 12346
        assert result.pg_value == 12345

    def test_within_tolerance_returns_none(self):
        result = compare_money_fields(
            field_path="total_levied",
            mongo_aud=123.459,   # rounds to 12346 → diff=1
            pg_cents=12345,
            tolerance_cents=1,
        )
        assert result is None

    def test_exceeds_tolerance_returns_diff(self):
        result = compare_money_fields(
            field_path="total_levied",
            mongo_aud=123.46,   # rounds to 12346 → diff=1
            pg_cents=12345,
            tolerance_cents=0,
        )
        assert result is not None

    def test_one_cent_diff_is_warn(self):
        result = compare_money_fields(
            field_path="total_levied",
            mongo_aud=123.46,   # rounds to 12346 → diff=1
            pg_cents=12345,
            tolerance_cents=0,
        )
        assert result is not None
        assert result.severity == "warn"

    def test_large_diff_is_critical(self):
        result = compare_money_fields(
            field_path="total",
            mongo_aud=200.00,
            pg_cents=10000,
        )
        assert result is not None
        assert result.severity == "critical"

    def test_none_aud_treated_as_zero(self):
        result = compare_money_fields(
            field_path="total",
            mongo_aud=None,
            pg_cents=0,
        )
        assert result is None

    def test_none_pg_treated_as_zero(self):
        result = compare_money_fields(
            field_path="total",
            mongo_aud=0.0,
            pg_cents=None,
        )
        assert result is None

    def test_diff_cents_computed_correctly(self):
        result = compare_money_fields(
            field_path="total",
            mongo_aud=150.00,
            pg_cents=10000,
        )
        assert result is not None
        assert result.diff_cents == 15000 - 10000


class TestCompareCountFields:
    def test_equal_counts_returns_none(self):
        assert compare_count_fields(field_path="x", mongo_count=5, pg_count=5) is None

    def test_different_counts_returns_diff(self):
        result = compare_count_fields(field_path="x", mongo_count=5, pg_count=3)
        assert result is not None
        assert result.mongo_value == 5
        assert result.pg_value == 3

    def test_none_treated_as_zero(self):
        assert compare_count_fields(field_path="x", mongo_count=None, pg_count=None) is None


class TestCompareStatusFields:
    def test_same_status_returns_none(self):
        assert compare_status_fields(
            field_path="status", mongo_status="active", pg_status="ACTIVE"
        ) is None

    def test_different_status_returns_diff(self):
        result = compare_status_fields(
            field_path="status", mongo_status="active", pg_status="inactive"
        )
        assert result is not None

    def test_none_treated_as_empty(self):
        assert compare_status_fields(
            field_path="status", mongo_status=None, pg_status=""
        ) is None


class TestCompareListLengths:
    def test_same_length_returns_none(self):
        assert compare_list_lengths(field_path="items", mongo_list=[1, 2], pg_list=[3, 4]) is None

    def test_different_length_returns_diff(self):
        result = compare_list_lengths(field_path="items", mongo_list=[1, 2, 3], pg_list=[1])
        assert result is not None


# ---------------------------------------------------------------------------
# ShadowCompareResult
# ---------------------------------------------------------------------------

class TestShadowCompareResult:
    def test_no_diffs_has_pass_severity(self):
        r = ShadowCompareResult(
            building_id=BUILDING_A, route_key="finance.summary", matched=True
        )
        assert r.severity == "pass"

    def test_critical_diff_is_critical(self):
        r = ShadowCompareResult(
            building_id=BUILDING_A,
            route_key="finance.summary",
            matched=False,
            diffs=[
                FieldDiff(
                    field_path="x",
                    mongo_value=1000,
                    pg_value=2000,
                    tolerance_cents=0,
                    severity="critical",
                    diff_cents=-1000,
                )
            ],
        )
        assert r.severity == "critical"

    def test_error_severity_from_error_field(self):
        r = ShadowCompareResult(
            building_id=BUILDING_A,
            route_key="finance.summary",
            matched=False,
            error="something_failed",
        )
        assert r.severity == "error"

    def test_mixed_severity_picks_most_severe(self):
        # Regression: the severity property previously used max() on a most-severe-first
        # list (index 0=critical, 2=info), which returned the LEAST severe diff.
        # Fixed to use min() so index 0 (critical) is correctly selected.
        r = ShadowCompareResult(
            building_id=BUILDING_A,
            route_key="finance.summary",
            matched=False,
            diffs=[
                FieldDiff(
                    field_path="x",
                    mongo_value=100,
                    pg_value=100,
                    tolerance_cents=0,
                    severity="info",
                    diff_cents=0,
                ),
                FieldDiff(
                    field_path="y",
                    mongo_value=1000,
                    pg_value=2000,
                    tolerance_cents=0,
                    severity="critical",
                    diff_cents=-1000,
                ),
            ],
        )
        assert r.severity == "critical", (
            "mixed critical+info must return 'critical', not 'info'; "
            "severity property must use min() on sev_order, not max()"
        )


# ---------------------------------------------------------------------------
# run_shadow_compare
# ---------------------------------------------------------------------------

class TestRunShadowCompare:
    @pytest.fixture
    def mock_record_diff(self):
        # _should_record_shadow_ok() queries the REAL database (a 30-minute
        # per-building/route throttle on "shadow_ok" rows only — field_mismatch
        # and pg_unavailable are always recorded). Without mocking it too, tests
        # asserting a "shadow_ok" diff was recorded become order/environment
        # dependent: they pass or fail based on whatever real shadow_diffs rows
        # already exist for BUILDING_A/"finance.summary" in the last 30 minutes
        # (e.g. from other test runs or real traffic against a shared dev DB).
        with patch(
            "services.finance_shadow_read_service.record_shadow_diff",
            new_callable=AsyncMock,
        ) as mock, patch(
            "services.finance_shadow_read_service._should_record_shadow_ok",
            new_callable=AsyncMock,
            return_value=True,
        ):
            yield mock

    @pytest.mark.asyncio
    async def test_exact_match_returns_matched_true(self, mock_record_diff):
        result = await run_shadow_compare(
            building_id=BUILDING_A,
            route_key="finance.summary",
            mongo_payload={"unit_ledger_summary": {"total_levied": 100.00, "total_outstanding": 0.0}},
            pg_payload={"levy_budgeted_cents": 10000, "total_arrears_cents": 0},
        )
        assert result.matched is True
        assert result.diffs == []
        mock_record_diff.assert_awaited_once()
        assert mock_record_diff.await_args.kwargs["diff_type"] == "shadow_ok"

    @pytest.mark.asyncio
    async def test_mismatch_records_diff(self, mock_record_diff):
        result = await run_shadow_compare(
            building_id=BUILDING_A,
            route_key="finance.summary",
            mongo_payload={"unit_ledger_summary": {"total_levied": 200.00}},
            pg_payload={"levy_budgeted_cents": 10000, "total_arrears_cents": 0},
        )
        assert result.matched is False
        assert len(result.diffs) > 0
        mock_record_diff.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pg_payload_none_returns_pg_unavailable(self, mock_record_diff):
        result = await run_shadow_compare(
            building_id=BUILDING_A,
            route_key="finance.summary",
            mongo_payload={},
            pg_payload=None,
        )
        assert result.pg_available is False
        assert result.matched is False
        mock_record_diff.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tolerance_allows_minor_diff(self, mock_record_diff):
        """1-cent tolerance should prevent diff recording for 1-cent diff."""
        result = await run_shadow_compare(
            building_id=BUILDING_A,
            route_key="finance.summary",
            mongo_payload={"unit_ledger_summary": {"total_levied": 100.01}},
            pg_payload={"levy_budgeted_cents": 10000, "total_arrears_cents": 0},
            tolerance_cents=1,
        )
        assert result.matched is True
        mock_record_diff.assert_awaited_once()
        assert mock_record_diff.await_args.kwargs["diff_type"] == "shadow_ok"

    @pytest.mark.asyncio
    async def test_building_id_stored_in_result(self, mock_record_diff):
        result = await run_shadow_compare(
            building_id=BUILDING_A,
            route_key="finance.summary",
            mongo_payload={},
            pg_payload={},
        )
        assert result.building_id == BUILDING_A

    @pytest.mark.asyncio
    async def test_is_test_data_passed_to_record_diff(self, mock_record_diff):
        """is_test_data flag must propagate to record_shadow_diff."""
        await run_shadow_compare(
            building_id=BUILDING_A,
            route_key="finance.summary",
            mongo_payload={"unit_ledger_summary": {"total_levied": 999.00}},
            pg_payload={"levy_budgeted_cents": 1, "total_arrears_cents": 0},
            is_test_data=True,
        )
        call_kwargs = mock_record_diff.call_args.kwargs
        assert call_kwargs.get("is_test_data") is True

    @pytest.mark.asyncio
    async def test_compare_error_returns_result_with_error(self, mock_record_diff):
        """Even if compare logic raises, result.error is set and no exception propagates."""
        with patch(
            "services.finance_shadow_read_service._compare_finance_payloads",
            side_effect=RuntimeError("forced compare failure"),
        ):
            result = await run_shadow_compare(
                building_id=BUILDING_A,
                route_key="finance.summary",
                mongo_payload={},
                pg_payload={},
            )
        assert result.error is not None
        assert "compare_error" in result.error


# ---------------------------------------------------------------------------
# maybe_run_finance_shadow — fire-and-forget, never raises
# ---------------------------------------------------------------------------

class TestMaybeRunFinanceShadow:
    @pytest.mark.asyncio
    async def test_does_nothing_when_toggle_disabled(self):
        """When the toggle is disabled, no shadow work happens."""
        with patch(
            "services.finance_shadow_read_service._is_shadow_route_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "services.finance_shadow_read_service._financial_read_service"
            ) as mock_svc:
                await maybe_run_finance_shadow(
                    building_id=BUILDING_A,
                    route_key="finance.summary",
                    mongo_payload={"total_levied": 1.0},
                )
            mock_svc.get_building_finance_pg_dashboard.assert_not_called()

    @pytest.mark.asyncio
    async def test_shadow_failure_does_not_raise(self):
        """Even if toggle check raises, maybe_run_finance_shadow must not propagate."""
        with patch(
            "services.finance_shadow_read_service._is_shadow_route_enabled",
            new_callable=AsyncMock,
            side_effect=RuntimeError("toggle service down"),
        ):
            # Should complete without raising
            await maybe_run_finance_shadow(
                building_id=BUILDING_A,
                route_key="finance.summary",
                mongo_payload={"total_levied": 1.0},
            )

    @pytest.mark.asyncio
    async def test_pg_fetch_failure_does_not_raise(self):
        """PG payload fetch failure must not propagate to the caller."""
        with patch(
            "services.finance_shadow_read_service._is_shadow_route_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch(
                "services.finance_shadow_read_service._financial_read_service",
            ) as mock_svc:
                mock_svc.get_building_finance_pg_dashboard = AsyncMock(
                    side_effect=RuntimeError("DB unavailable")
                )
                await maybe_run_finance_shadow(
                    building_id=BUILDING_A,
                    route_key="finance.summary",
                    mongo_payload={"total_levied": 1.0},
                )
            # Should complete without raising

    @pytest.mark.asyncio
    async def test_building_a_shadow_does_not_affect_building_b(self):
        """Shadow enabled for building A must not run for building B."""
        shadow_calls: list[str] = []

        async def _fake_shadow_enabled(building_id: str) -> bool:
            shadow_calls.append(building_id)
            return building_id == BUILDING_A

        with patch(
            "services.finance_shadow_read_service._is_shadow_route_enabled",
            new_callable=AsyncMock,
            side_effect=_fake_shadow_enabled,
        ):
            with patch(
                "services.finance_shadow_read_service._financial_read_service",
            ) as mock_svc:
                mock_svc.get_building_finance_pg_dashboard = AsyncMock(return_value={})
                mock_svc.get_fund_balances = AsyncMock(return_value={})
                mock_svc.get_arrears_summary = AsyncMock(return_value={})

                with patch(
                    "services.finance_shadow_read_service.run_shadow_compare",
                    new_callable=AsyncMock,
                ) as mock_compare:
                    await maybe_run_finance_shadow(
                        building_id=BUILDING_B,
                        route_key="finance.summary",
                        mongo_payload={"total_levied": 1.0},
                    )
                    # run_shadow_compare must NOT be called for building B
                    mock_compare.assert_not_called()


# ---------------------------------------------------------------------------
# summarize_shadow_result
# ---------------------------------------------------------------------------

class TestSummarizeShadowResult:
    def test_pass_result_summarised_correctly(self):
        result = ShadowCompareResult(
            building_id=BUILDING_A,
            route_key="finance.summary",
            matched=True,
        )
        summary = summarize_shadow_result(result)
        assert summary["matched"] is True
        assert summary["severity"] == "pass"
        assert summary["diff_count"] == 0
        assert summary["building_id"] == BUILDING_A

    def test_diffs_included_in_summary(self):
        result = ShadowCompareResult(
            building_id=BUILDING_A,
            route_key="finance.summary",
            matched=False,
            diffs=[
                FieldDiff(
                    field_path="admin_fund.total_levied",
                    mongo_value=10000,
                    pg_value=9999,
                    tolerance_cents=0,
                    severity="warn",
                    diff_cents=1,
                )
            ],
        )
        summary = summarize_shadow_result(result)
        assert summary["diff_count"] == 1
        assert summary["diffs"][0]["field_path"] == "admin_fund.total_levied"


# ---------------------------------------------------------------------------
# get_route_shadow_readiness
# ---------------------------------------------------------------------------

class TestGetRouteShadowReadiness:
    @pytest.mark.asyncio
    async def test_not_started_when_no_diffs(self):
        """When there are no diffs in the DB the status should be not_started."""
        mock_row = MagicMock()
        mock_row.total_diffs = 0
        mock_row.pass_samples = 0
        mock_row.critical_count = 0
        mock_row.last_at = None

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            return_value=mock_ctx,
        ):
            result = await get_route_shadow_readiness(
                building_id=BUILDING_A,
                route_key="finance.summary",
            )
        assert result["status"] == "not_started"

    @pytest.mark.asyncio
    async def test_shadow_fail_when_critical_diffs(self):
        mock_row = MagicMock()
        mock_row.total_diffs = 3
        mock_row.pass_samples = 0
        mock_row.critical_count = 2
        from datetime import UTC, datetime
        mock_row.last_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            return_value=mock_ctx,
        ):
            result = await get_route_shadow_readiness(
                building_id=BUILDING_A,
                route_key="finance.summary",
            )
        assert result["status"] == "shadow_fail"
        assert result["critical_count"] == 2

    @pytest.mark.asyncio
    async def test_shadow_pass_when_only_shadow_ok_samples(self):
        mock_row = MagicMock()
        mock_row.total_diffs = 0
        mock_row.pass_samples = 4
        mock_row.critical_count = 0
        from datetime import UTC, datetime
        mock_row.last_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            return_value=mock_ctx,
        ):
            result = await get_route_shadow_readiness(
                building_id=BUILDING_A,
                route_key="finance.summary",
            )
        assert result["status"] == "shadow_pass"
        assert result["pass_samples"] == 4

    @pytest.mark.asyncio
    async def test_db_error_returns_not_started(self):
        """DB errors must not propagate — return not_started with error key."""
        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            side_effect=RuntimeError("connection refused"),
        ):
            result = await get_route_shadow_readiness(
                building_id=BUILDING_A,
                route_key="finance.summary",
            )
        assert result["status"] == "not_started"
        assert "error" in result


# ---------------------------------------------------------------------------
# get_building_finance_shadow_readiness
# ---------------------------------------------------------------------------

class TestGetBuildingFinanceShadowReadiness:
    @pytest.mark.asyncio
    async def test_not_started_when_all_routes_not_started(self):
        not_started = {"status": "not_started", "diff_count": 0, "critical_count": 0, "last_compared_at": None}
        with patch(
            "services.finance_shadow_read_service.get_route_shadow_readiness",
            new_callable=AsyncMock,
            return_value=not_started,
        ):
            result = await get_building_finance_shadow_readiness(building_id=BUILDING_A)
        assert result["overall_status"] == "not_started"
        assert result["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_shadow_fail_when_any_route_critical(self):
        async def _fake_readiness(building_id, route_key, **kwargs):
            if route_key == "finance.summary":
                return {"status": "shadow_fail", "diff_count": 1, "critical_count": 1, "last_compared_at": None}
            return {"status": "not_started", "diff_count": 0, "critical_count": 0, "last_compared_at": None}

        with patch(
            "services.finance_shadow_read_service.get_route_shadow_readiness",
            new_callable=AsyncMock,
            side_effect=_fake_readiness,
        ):
            result = await get_building_finance_shadow_readiness(building_id=BUILDING_A)
        assert result["overall_status"] == "shadow_fail"

    @pytest.mark.asyncio
    async def test_cross_building_isolation(self):
        """Readiness for Building A must not bleed into Building B."""
        received_building_ids: list[str] = []

        async def _capture(building_id, route_key, **kwargs):
            received_building_ids.append(building_id)
            return {"status": "shadow_pass", "diff_count": 0, "critical_count": 0, "last_compared_at": None}

        with patch(
            "services.finance_shadow_read_service.get_route_shadow_readiness",
            new_callable=AsyncMock,
            side_effect=_capture,
        ):
            await get_building_finance_shadow_readiness(building_id=BUILDING_B)

        # All calls must have been for Building B
        assert all(bid == BUILDING_B for bid in received_building_ids)


# ---------------------------------------------------------------------------
# _get_pg_payload_for_route — finance.transactions dimension scoping
# ---------------------------------------------------------------------------

class TestFinanceTransactionsDimensionScoping:
    """GAP-FIN-062-adjacent bug: routers/finance.py's expense-transactions and
    income-transactions endpoints each fire a shadow comparison with only their
    own side of finance.transactions genuinely populated (the other side is an
    intentionally empty list). _get_pg_payload_for_route previously always
    returned BOTH total_expense_cents/total_income_cents from PG regardless of
    which side Mongo actually had data for, producing a guaranteed critical
    false-positive on every single call. These tests lock in the fix: the PG
    payload must only carry the dimension the caller actually populated.
    """

    _TRANSACTIONS = [
        {"transaction_type": "expense", "amount": 100.0},
        {"transaction_type": "income", "amount": 250.0},
    ]

    def _patch_transactions(self):
        return patch(
            "services.finance_shadow_read_service._financial_read_service.get_transactions_for_year",
            new_callable=AsyncMock,
            return_value=self._TRANSACTIONS,
        )

    @pytest.mark.asyncio
    async def test_expense_dimension_omits_income_cents(self):
        with self._patch_transactions():
            payload = await _get_pg_payload_for_route(
                building_id=BUILDING_A,
                route_key="finance.transactions",
                mongo_payload={"year": "2026", "_dimension": "expense"},
            )
        assert payload == {"total_expense_cents": 10000}

    @pytest.mark.asyncio
    async def test_income_dimension_omits_expense_cents(self):
        with self._patch_transactions():
            payload = await _get_pg_payload_for_route(
                building_id=BUILDING_A,
                route_key="finance.transactions",
                mongo_payload={"year": "2026", "_dimension": "income"},
            )
        assert payload == {"total_income_cents": 25000}

    @pytest.mark.asyncio
    async def test_missing_dimension_returns_both_as_before(self):
        """No `_dimension` key (an unexpected/legacy caller) falls back to the
        original both-sides behaviour rather than silently omitting a field."""
        with self._patch_transactions():
            payload = await _get_pg_payload_for_route(
                building_id=BUILDING_A,
                route_key="finance.transactions",
                mongo_payload={"year": "2026"},
            )
        assert payload == {"total_expense_cents": 10000, "total_income_cents": 25000}

    @pytest.mark.asyncio
    async def test_expense_dimension_with_pg_available_matches_mongo_empty_income(self):
        """End-to-end: an expense-only Mongo payload (income=[] -> total_income=0.0)
        must NOT diff against PG's real (omitted -> treated as 0) income side."""
        mongo_payload = {
            "year": "2026",
            "_dimension": "expense",
            "total_expense": 100.0,
            "total_income": 0.0,
        }
        with self._patch_transactions():
            pg_payload = await _get_pg_payload_for_route(
                building_id=BUILDING_A,
                route_key="finance.transactions",
                mongo_payload=mongo_payload,
            )
        from services.finance_shadow_read_service import _compare_transactions_payloads
        diffs = _compare_transactions_payloads(
            mongo_payload=mongo_payload, pg_payload=pg_payload, tolerance_cents=0,
        )
        assert diffs == []


# ---------------------------------------------------------------------------
# Unscopable comparisons must leave NO trace in the readiness signal
# ---------------------------------------------------------------------------

class TestUnscopableComparisonIsNotPgUnavailable:
    """A comparison that was never ATTEMPTED must not be recorded as a diff.

    ``get_route_shadow_readiness`` counts every non-``shadow_ok`` row in
    ``core.shadow_diffs`` toward ``diff_count``, and a route with diffs never
    reaches ``shadow_pass``. So recording ``pg_unavailable`` for a comparison the
    harness itself declined to scope blocks promotion on an artefact.

    Live evidence (East Gate 13195, 2026-08-27): 152 unresolved
    ``pg_unavailable`` rows on ``finance.summary``, none of which ever queried
    PostgreSQL — verified by running the same builder against the same live data
    with a year supplied, which returns a complete payload.
    """

    @pytest.mark.asyncio
    async def test_summary_without_year_is_not_applicable(self):
        payload = await _get_pg_payload_for_route(
            building_id=BUILDING_A, route_key="finance.summary", mongo_payload={},
        )
        assert isinstance(payload, _NotApplicable)
        assert payload is not None, "must be distinguishable from PG-unavailable"

    @pytest.mark.asyncio
    async def test_arrears_without_year_is_not_applicable(self):
        """Cross-year guard: without a year the PG side would resolve its own
        default financial year while Mongo stays on the caller's, producing a
        full-value critical mismatch that measures nothing."""
        payload = await _get_pg_payload_for_route(
            building_id=BUILDING_A,
            route_key="finance.arrears_detail",
            mongo_payload={"total_arrears": 0.0, "units_in_arrears": 0},
        )
        assert isinstance(payload, _NotApplicable)

    @pytest.mark.asyncio
    async def test_unit_dashboard_without_unit_number_is_not_applicable(self):
        payload = await _get_pg_payload_for_route(
            building_id=BUILDING_A,
            route_key="finance.unit_dashboard_overview",
            mongo_payload={"year": "2026"},
        )
        assert isinstance(payload, _NotApplicable)

    @pytest.mark.asyncio
    async def test_arrears_with_year_scopes_pg_to_that_year(self):
        """The year the caller measured is the year PG must be asked for —
        never PG's own default window."""
        summary = AsyncMock(return_value={
            "total_arrears_cents": 0, "units_in_arrears": 0, "basis": "due_date_grace_aware",
        })
        with patch(
            "services.finance_shadow_read_service._financial_read_service.get_arrears_summary",
            summary,
        ):
            payload = await _get_pg_payload_for_route(
                building_id=BUILDING_A,
                route_key="finance.arrears_detail",
                mongo_payload={"year": "2025", "total_arrears": 0.0, "units_in_arrears": 0},
            )
        assert payload == {
            "total_arrears_cents": 0, "units_in_arrears": 0, "basis": "due_date_grace_aware",
        }
        assert summary.await_args.kwargs["financial_year"] == "2025"

    @pytest.mark.asyncio
    async def test_not_applicable_records_no_diff_and_no_coverage(self):
        """The whole point: nothing reaches core.shadow_diffs."""
        with patch(
            "services.finance_shadow_read_service._is_shadow_route_enabled",
            new_callable=AsyncMock, return_value=True,
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new_callable=AsyncMock, return_value=NOT_APPLICABLE,
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new_callable=AsyncMock,
        ) as compare:
            await maybe_run_finance_shadow(
                building_id=BUILDING_A, route_key="finance.summary", mongo_payload={},
            )
        compare.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_genuine_pg_unavailable_still_records(self):
        """The fix must not silence real unavailability — None still compares
        (and run_shadow_compare records the pg_unavailable row)."""
        with patch(
            "services.finance_shadow_read_service._is_shadow_route_enabled",
            new_callable=AsyncMock, return_value=True,
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new_callable=AsyncMock,
        ) as compare:
            await maybe_run_finance_shadow(
                building_id=BUILDING_A, route_key="finance.summary",
                mongo_payload={"year": "2026"},
            )
        compare.assert_awaited_once()
        assert compare.await_args.kwargs["pg_payload"] is None


class TestResolvedDiffsDoNotBlockReadiness:
    """An adjudicated diff must stop vetoing promotion.

    ``get_route_shadow_readiness`` previously counted every row in the lookback
    window regardless of ``resolved``. That made the entire triage/annotate path
    inert — including ``east_gate_phase_d_activate.py``'s own documented step 4,
    which resolves stale ``pg_unavailable`` rows specifically to unblock the gate
    it then checks. The row stays in the table for audit; it just no longer counts.
    """

    @staticmethod
    def _session_returning(total, passes, critical, last_at="2026-08-27T00:00:00+00:00"):
        row = MagicMock(total_diffs=total, pass_samples=passes, critical_count=critical)
        row.last_at = MagicMock(isoformat=lambda: last_at)
        result = MagicMock()
        result.fetchone = MagicMock(return_value=row)
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx, session

    @pytest.mark.asyncio
    async def test_query_filters_out_resolved_rows(self):
        ctx, session = self._session_returning(0, 3, 0)
        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            return_value=ctx,
        ):
            await get_route_shadow_readiness(
                building_id=BUILDING_A, route_key="finance.arrears_detail",
            )
        sql = str(session.execute.await_args.args[0])
        assert "resolved = FALSE" in sql, (
            "readiness must ignore adjudicated diffs, or resolving one changes nothing"
        )

    @pytest.mark.asyncio
    async def test_all_resolved_plus_clean_samples_reads_as_pass(self):
        ctx, _ = self._session_returning(0, 5, 0)
        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            return_value=ctx,
        ):
            readiness = await get_route_shadow_readiness(
                building_id=BUILDING_A, route_key="finance.arrears_detail",
            )
        assert readiness["status"] == "shadow_pass"
        assert readiness["critical_count"] == 0

    @pytest.mark.asyncio
    async def test_unresolved_critical_still_fails(self):
        """The fix must not make real, open divergences invisible."""
        ctx, _ = self._session_returning(4, 0, 4)
        with patch(
            "services.finance_shadow_read_service._get_bypass_session_context",
            return_value=ctx,
        ):
            readiness = await get_route_shadow_readiness(
                building_id=BUILDING_A, route_key="finance.arrears_detail",
            )
        assert readiness["status"] == "shadow_fail"
        assert readiness["critical_count"] == 4
