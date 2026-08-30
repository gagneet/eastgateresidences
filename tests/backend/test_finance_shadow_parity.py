# @featuretrace:finance-shadow-reads — Parity contract tests for finance shadow read service.
# Layer: test
# Data flow: test → run_shadow_compare / maybe_run_finance_shadow → (mock) record_shadow_diff
# Related: backend/services/finance_shadow_read_service.py
#          backend/services/cutover_status_service.py
#          backend/scripts/east_gate_phase_d_activate.py
"""Finance shadow-read parity contract tests.

Tests:
  - compare_money_fields: exact match, within tolerance, beyond tolerance, severity
  - compare_count_fields: match, mismatch
  - compare_status_fields: case-insensitive match, mismatch
  - run_shadow_compare: match, divergence, pg_unavailable, compare error
  - _is_shadow_route_enabled: toggle path, domain mode path, both disabled
  - maybe_run_finance_shadow: fires when enabled, silently skips when disabled,
      never propagates exceptions to caller
  - Multi-tenant isolation: building A diffs do not affect building B
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.finance_shadow_read_service import (
    FieldDiff,
    ShadowCompareResult,
    compare_count_fields,
    compare_money_fields,
    compare_status_fields,
    maybe_run_finance_shadow,
    run_shadow_compare,
)


# See test_finance_shadow_read_service.py's _no_real_coverage_writes fixture for why this
# is needed: run_shadow_compare() calls the best-effort, exception-swallowing
# shadow_read_service._safe_record_coverage(), which — unmocked — silently opens a real DB
# connection instead of failing the test.
@pytest.fixture(autouse=True)
def _no_real_coverage_writes():
    with patch(
        "services.finance_shadow_read_service._safe_record_coverage",
        new=AsyncMock(),
    ) as mock_record:
        yield mock_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_status(mode: str) -> object:
    """Minimal cutover status stub with .mode.value."""
    status = SimpleNamespace()
    status.mode = SimpleNamespace(value=mode)
    return status


# ---------------------------------------------------------------------------
# compare_money_fields
# ---------------------------------------------------------------------------

class TestCompareMoneyFields:
    def test_exact_match_returns_none(self):
        assert compare_money_fields(field_path="x", mongo_aud=100.0, pg_cents=10000, tolerance_cents=0) is None

    def test_within_tolerance_returns_none(self):
        # 100.00 AUD → 10000c; PG = 10001 → 1c off, tolerance=1 → pass
        assert compare_money_fields(field_path="x", mongo_aud=100.0, pg_cents=10001, tolerance_cents=1) is None

    def test_exceeds_tolerance_returns_diff(self):
        diff = compare_money_fields(field_path="arrears", mongo_aud=100.05, pg_cents=10000, tolerance_cents=0)
        assert diff is not None
        assert diff.field_path == "arrears"
        assert diff.diff_cents == 5  # 10005 - 10000

    def test_large_diff_is_critical(self):
        diff = compare_money_fields(field_path="x", mongo_aud=200.0, pg_cents=10000, tolerance_cents=0)
        assert diff is not None
        assert diff.severity == "critical"

    def test_small_diff_is_warn(self):
        # 100.50 → 10050c; PG=10040 → diff=10, which is ≤100 → warn
        diff = compare_money_fields(field_path="x", mongo_aud=100.50, pg_cents=10040, tolerance_cents=0)
        assert diff is not None
        assert diff.severity == "warn"

    def test_none_mongo_treated_as_zero(self):
        assert compare_money_fields(field_path="x", mongo_aud=None, pg_cents=0, tolerance_cents=0) is None

    def test_none_pg_treated_as_zero(self):
        diff = compare_money_fields(field_path="x", mongo_aud=50.0, pg_cents=None, tolerance_cents=0)
        assert diff is not None
        assert diff.diff_cents == 5000


class TestShadowCompareResultSeverity:
    """Ensure ShadowCompareResult.severity picks the MOST severe diff, not least severe."""

    def test_single_critical_diff_returns_critical(self):
        result = ShadowCompareResult(
            building_id="13195",
            route_key="finance.summary",
            matched=False,
            diffs=[FieldDiff(field_path="x", mongo_value=0, pg_value=10000,
                             tolerance_cents=0, severity="critical", diff_cents=10000)],
        )
        assert result.severity == "critical"

    def test_mixed_critical_and_info_returns_critical(self):
        # Before the max→min fix this would wrongly return "info".
        result = ShadowCompareResult(
            building_id="13195",
            route_key="finance.summary",
            matched=False,
            diffs=[
                FieldDiff(field_path="a", mongo_value=0, pg_value=0, tolerance_cents=0, severity="info"),
                FieldDiff(field_path="b", mongo_value=0, pg_value=0, tolerance_cents=0, severity="critical"),
            ],
        )
        assert result.severity == "critical"

    def test_mixed_warn_and_info_returns_warn(self):
        result = ShadowCompareResult(
            building_id="13195",
            route_key="finance.summary",
            matched=False,
            diffs=[
                FieldDiff(field_path="a", mongo_value=0, pg_value=0, tolerance_cents=0, severity="info"),
                FieldDiff(field_path="b", mongo_value=0, pg_value=0, tolerance_cents=0, severity="warn"),
            ],
        )
        assert result.severity == "warn"

    def test_no_diffs_returns_pass(self):
        result = ShadowCompareResult(building_id="13195", route_key="r", matched=True)
        assert result.severity == "pass"

    def test_error_overrides_severity(self):
        result = ShadowCompareResult(
            building_id="13195", route_key="r", matched=False, error="boom"
        )
        assert result.severity == "error"


class TestCompareCountFields:
    def test_equal_counts_returns_none(self):
        assert compare_count_fields(field_path="c", mongo_count=5, pg_count=5) is None

    def test_unequal_counts_returns_diff(self):
        diff = compare_count_fields(field_path="units_in_arrears", mongo_count=3, pg_count=4)
        assert diff is not None
        assert diff.severity == "warn"

    def test_none_treated_as_zero(self):
        assert compare_count_fields(field_path="c", mongo_count=None, pg_count=None) is None


class TestCompareStatusFields:
    def test_case_insensitive_match(self):
        assert compare_status_fields(field_path="s", mongo_status="Active", pg_status="active") is None

    def test_mismatch_returns_info(self):
        diff = compare_status_fields(field_path="s", mongo_status="active", pg_status="pending")
        assert diff is not None
        assert diff.severity == "info"

    def test_none_vs_empty_match(self):
        assert compare_status_fields(field_path="s", mongo_status=None, pg_status="") is None


# ---------------------------------------------------------------------------
# run_shadow_compare
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRunShadowCompare:
    async def test_matching_payloads_returns_matched(self):
        # _compare_summary_payloads reads mongo["unit_ledger_summary"]["total_levied"]
        # and mongo["unit_ledger_summary"]["total_outstanding"] — must use nested
        # structure. (Not mongo["admin_fund"]["total_levied"] — that sub-document is
        # the raw annual_levies.admin_fund doc and has never had a total_levied key.)
        mongo = {
            "unit_ledger_summary": {"total_levied": 100.0, "total_outstanding": 10.0},
        }
        pg = {"levy_budgeted_cents": 10000, "total_arrears_cents": 1000}
        with patch("services.finance_shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_record, \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is True
        assert result.diffs == []
        assert result.severity == "pass"

    async def test_levy_kpi_ignores_total_annual_gross_scope_mismatch(self):
        """total_annual_gross is intentionally excluded from finance.levy_kpi
        comparison: mongo's total_annual_gross (admin_a_gross + sinking_a_gross via
        get_levy_proposed_amounts()) is the full-year PROPOSED budget. pg_payload's
        quarter_billed_cents (get_current_quarter_levy_total(), 2026-08-09 fix — see
        that method's docstring for the "PG=2.0x Mongo" root-cause writeup) is scoped to
        only the most-recently-issued levy_run, matching mongo's OWN
        quarter_billed_total_display for that quarter (a real, verified-live parity —
        confirmed live 2026-08-09: mongo=$110,093.78, pg=$110,093.78, exact match).
        mongo's total_annual_gross is a forward-looking proposed figure PG has no
        equivalent of yet (mongo total_annual_gross=$400,341.00 vs pg
        quarter_billed_cents=$110,093.74) — comparing "proposed annual budget" against
        "this quarter's billed total" produced a large, meaningless "critical"
        divergence on every request. Skipped rather than treated as a false
        data-integrity signal, same pattern as
        test_summary_ignores_total_outstanding_scope_mismatch above."""
        mongo = {"total_annual_gross": 400341.0}
        pg = {"quarter_billed_cents": 11009374}  # wildly different — would be "critical" if compared
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.levy_kpi",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is True
        assert result.diffs == []

    async def test_divergent_payloads_records_diff(self):
        # total_levied is the only field _compare_summary_payloads still compares
        # (total_outstanding is intentionally excluded — see
        # test_summary_ignores_total_outstanding_scope_mismatch below) — off by 500c.
        mongo = {
            "unit_ledger_summary": {"total_levied": 105.0, "total_outstanding": 20.0},  # → 10500c
        }
        pg = {"levy_budgeted_cents": 10000, "total_arrears_cents": 1500}
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is False
        assert len(result.diffs) == 1
        assert result.diffs[0].field_path == "unit_ledger_summary.total_levied"
        mock_record.assert_awaited_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["diff_type"].startswith("field_mismatch:")
        assert call_kwargs["is_test_data"] is True

    async def test_summary_ignores_total_outstanding_scope_mismatch(self):
        """unit_ledger_summary.total_outstanding is intentionally excluded from
        finance.summary comparison — same reasoning as the building_overview
        exclusion below. For the current calendar year, when there are no
        in-grace periods, routers/finance.py get_finance_summary() overrides the
        raw ledger-aggregate total_outstanding with get_arrears_metrics()'s
        per-unit "true arrears" figure (opening_arrears + past-due periods -
        confirmed_paid), specifically because the raw ledger net_balance sum
        counts undue future levies as "owing". (When there ARE in-grace periods,
        total_outstanding stays as the raw ledger aggregate instead — see
        get_finance_summary's own in_grace_count branch — which carries the same
        "undue levies counted as owing" property, so the mismatch below applies
        either way, just via a different Mongo-side value.) pg_payload's
        total_arrears_cents (get_arrears_summary()) sums ALL unpaid levy_items for
        the whole financial year regardless of due date — the same "raw ledger
        aggregate" concept the Mongo override (when active) exists to move away
        from. Comparing them produces a large, meaningless divergence on the
        common no-in-grace-periods path (a real observed case:
        mongo=$122,061.03/85 units vs pg=$16,128.08/16 units on the same
        request) — not a data bug, a comparison-target mismatch.
        total_levied IS apples-to-apples and must still compare."""
        mongo = {
            "unit_ledger_summary": {"total_levied": 100.0, "total_outstanding": 122061.03},
        }
        pg = {
            "levy_budgeted_cents": 10000,
            # Wildly different from mongo's total_outstanding — would be "critical" if compared.
            "total_arrears_cents": 1612808,
        }
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is True
        assert result.diffs == []

    async def test_building_overview_ignores_total_outstanding_scope_mismatch(self):
        """total_outstanding is intentionally excluded from comparison:
        pg_payload's levy_outstanding_cents is a cumulative all-time AR closing
        balance (get_oc_levy_summary's outstanding_result has no lower date bound,
        matching the accrual-accounting convention used by get_unit_levy_balance's
        own closing_balance query), while mongo's total_outstanding is this
        financial year's net movement only. Comparing them produces a large,
        meaningless divergence — a real observed case was ~90x — so the field is
        skipped rather than treated as a false "critical" data-integrity signal.
        total_levied and total_paid ARE both apples-to-apples and must still compare."""
        mongo = {"total_levied": 100.0, "total_paid": 100.0, "total_outstanding": 10.0}
        pg = {
            "levy_budgeted_cents": 10000,
            "levy_collected_cents": 10000,
            # Wildly different from mongo's 1000c — would be "critical" if compared.
            "levy_outstanding_cents": 139801418,
        }
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.building_overview",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is True
        assert result.diffs == []

    async def test_building_overview_prefers_total_paid_this_year_over_inflated_raw(self):
        """GAP-FIN-056: the total_paid comparison must use mongo's YEAR-SCOPED
        total_paid_this_year, not the raw total_paid. After GAP-FIN-035 repurposed
        unit_levy_ledger.total_paid into a cumulative all-time back-solve (~8x the year
        figure), comparing PG's year-scoped levy_collected_cents against the raw
        total_paid falsely flags critical every run. With total_paid_this_year matching
        PG, the route is parity-clean; the inflated raw total_paid must be ignored."""
        mongo = {
            "total_levied": 220187.56,
            "total_paid": 1769655.36,          # inflated cumulative back-solve — must NOT be used
            "total_paid_this_year": 101671.69,  # year-scoped, per-unit-clamped — the right target
        }
        pg = {
            "levy_budgeted_cents": 22018756,
            "levy_collected_cents": 10167173,   # 4c rounding residual vs total_paid_this_year, within 5c tol
        }
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.building_overview",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is True, f"expected parity, got diffs: {result.diffs}"
        assert result.diffs == []

    async def test_building_overview_still_flags_a_genuine_paid_divergence(self):
        """The GAP-FIN-056 fix must not blunt the gate: when the year-scoped mongo
        figure genuinely diverges from PG's collected, it still records a diff."""
        mongo = {"total_levied": 220187.56, "total_paid_this_year": 101671.69}
        pg = {"levy_budgeted_cents": 22018756, "levy_collected_cents": 5000000}  # $50k vs $101k
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.building_overview",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is False
        assert any(d.field_path == "total_paid" for d in result.diffs)

    async def test_pg_unavailable_records_pg_unavailable_diff(self):
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={"total_levied": 100.0},
                pg_payload=None,
                is_test_data=True,
            )
        assert result.matched is False
        assert result.pg_available is False
        assert result.error == "pg_payload_unavailable"
        mock_record.assert_awaited_once()
        assert mock_record.call_args.kwargs["diff_type"] == "pg_unavailable"

    async def test_compare_error_returns_error_result_without_raising(self):
        with patch(
            "services.finance_shadow_read_service._compare_finance_payloads",
            side_effect=RuntimeError("boom"),
        ):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={},
                pg_payload={"x": 1},
                is_test_data=True,
            )
        assert result.matched is False
        assert result.error is not None
        assert "boom" in result.error

    async def test_arrears_detail_matching_same_concept_payloads_pass(self):
        """GAP-FIN-058 / B1: _compare_arrears_payloads is now a substantive same-concept
        comparator. The PG side is sourced from get_arrears_summary(grace_aware=True)
        (levy_items past grace_deadline_date), the same "currently overdue arrears"
        concept as Mongo's total_arrears/units_in_arrears. When both sides agree, the
        route passes clean."""
        mongo = {"total_arrears": 619.01, "units_in_arrears": 10}
        pg = {"total_arrears_cents": 61901, "units_in_arrears": 10}  # $619.01, same units
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.arrears_detail",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is True
        assert result.diffs == []

    async def test_arrears_detail_flags_same_concept_divergence(self):
        """The mirror of the pass case: once both sides measure the same due-date-aware
        arrears concept, a real divergence (e.g. the PG-understatement expected while
        GAP-FIN-057's paid_cents overstatement is still live) is now surfaced as a diff
        rather than silently ignored. This is the intended pre-GATE-A behaviour."""
        mongo = {"total_arrears": 619.01, "units_in_arrears": 10}
        pg = {"total_arrears_cents": 30000, "units_in_arrears": 6}  # $300.00, 6 units
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record), \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=True)):
            result = await run_shadow_compare(
                building_id="13195",
                route_key="finance.arrears_detail",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert result.matched is False
        diff_fields = {d.field_path for d in result.diffs}
        assert "total_arrears" in diff_fields
        assert "units_in_arrears" in diff_fields

    async def test_divergence_score_critical_is_1_0(self):
        # 100 AUD → 10000c vs PG=0 → 10000c diff → critical → score=1.0
        mongo = {
            "unit_ledger_summary": {"total_levied": 100.0, "total_outstanding": 0.0},
        }
        pg = {"levy_budgeted_cents": 0, "total_arrears_cents": 0}
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record):
            await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert mock_record.called
        assert mock_record.call_args.kwargs["divergence_score"] == 1.0


# ---------------------------------------------------------------------------
# _is_shadow_route_enabled (tested via maybe_run_finance_shadow)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestIsShadowRouteEnabled:
    async def test_toggle_enabled_activates_shadow(self):
        """When financial_shadow_reads_enabled toggle is True, shadow fires."""
        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new=AsyncMock(return_value={"levy_budgeted_cents": 10000}),
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new=AsyncMock(return_value=ShadowCompareResult(building_id="13195", route_key="r", matched=True)),
        ) as mock_compare:
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={"total_levied": 100.0},
                is_test_data=True,
            )
        mock_compare.assert_awaited_once()

    async def test_domain_postgres_shadow_activates_shadow_without_toggle(self):
        """When toggle is off but domain mode=postgres_shadow, shadow still fires."""
        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.finance_shadow_read_service.get_or_default_cutover_status",
            new=AsyncMock(return_value=_make_status("postgres_shadow")),
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new=AsyncMock(return_value={"levy_budgeted_cents": 10000}),
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new=AsyncMock(return_value=ShadowCompareResult(building_id="13195", route_key="r", matched=True)),
        ) as mock_compare:
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={"total_levied": 100.0},
                is_test_data=True,
            )
        mock_compare.assert_awaited_once()

    async def test_mongo_primary_mode_and_toggle_off_skips_shadow(self):
        """When toggle is off and domain mode=mongo_primary, shadow is skipped."""
        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.finance_shadow_read_service.get_or_default_cutover_status",
            new=AsyncMock(return_value=_make_status("mongo_primary")),
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new=AsyncMock(),
        ) as mock_compare:
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={},
                is_test_data=True,
            )
        mock_compare.assert_not_awaited()


# ---------------------------------------------------------------------------
# maybe_run_finance_shadow — fire-and-forget guarantees
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMaybeRunFinanceShadow:
    async def test_never_raises_on_toggle_check_exception(self):
        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            # Must not raise
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={},
                is_test_data=True,
            )

    async def test_never_raises_on_pg_fetch_exception(self):
        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new=AsyncMock(side_effect=ConnectionError("pg down")),
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new=AsyncMock(),
        ) as mock_compare:
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={},
                is_test_data=True,
            )
        # compare should still be called with pg_payload=None
        mock_compare.assert_awaited_once()
        assert mock_compare.call_args.kwargs["pg_payload"] is None

    async def test_never_raises_on_compare_exception(self):
        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new=AsyncMock(return_value={"x": 1}),
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new=AsyncMock(side_effect=RuntimeError("compare exploded")),
        ):
            # Must not raise
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={},
                is_test_data=True,
            )


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMultiTenantIsolation:
    async def test_shadow_diffs_include_building_id(self):
        """Diffs recorded for building 13195 must carry that building_id, not another building's."""
        # _compare_summary_payloads reads mongo["unit_ledger_summary"]["total_levied"],
        # not top-level. Use nested structure to produce a real divergence (99900c vs 0).
        mongo = {
            "unit_ledger_summary": {"total_levied": 999.0, "total_outstanding": 0.0},
        }
        pg = {"levy_budgeted_cents": 0, "total_arrears_cents": 0}  # total_levied diverges: 99900c
        mock_record = AsyncMock()
        with patch("services.finance_shadow_read_service._safe_record_diff", new=mock_record):
            await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload=mongo,
                pg_payload=pg,
                is_test_data=True,
            )
        assert mock_record.called
        assert mock_record.call_args.kwargs["building_id"] == "13195"

    async def test_different_buildings_produce_independent_compares(self):
        """Two concurrent shadow compares for different buildings do not share state."""
        mongo_a = {
            "unit_ledger_summary": {"total_levied": 100.0, "total_outstanding": 0.0},
        }
        pg_a = {"levy_budgeted_cents": 10000, "total_arrears_cents": 0}  # match

        mongo_b = {
            "unit_ledger_summary": {"total_levied": 200.0, "total_outstanding": 5.0},
        }
        pg_b = {"levy_budgeted_cents": 0, "total_arrears_cents": 0}  # large mismatch

        with patch("services.finance_shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_record, \
             patch("services.finance_shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result_a = await run_shadow_compare(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload=mongo_a,
                pg_payload=pg_a,
                is_test_data=True,
            )
            result_b = await run_shadow_compare(
                building_id="16244",
                route_key="finance.summary",
                mongo_payload=mongo_b,
                pg_payload=pg_b,
                is_test_data=True,
            )

        assert result_a.matched is True
        assert result_b.matched is False
        assert result_a.building_id == "13195"
        assert result_b.building_id == "16244"

    async def test_shadow_disabled_for_building_a_does_not_affect_building_b(self):
        """If 13195 shadow is disabled, 16244 can still be enabled independently."""
        call_count = 0

        async def mock_is_enabled(building_id: str, feature_key: str) -> bool:
            return building_id == "16244"  # only B is enabled via toggle

        with patch(
            "services.finance_shadow_read_service.is_cutover_feature_enabled",
            new=AsyncMock(side_effect=mock_is_enabled),
        ), patch(
            "services.finance_shadow_read_service.get_or_default_cutover_status",
            new=AsyncMock(return_value=_make_status("mongo_primary")),
        ), patch(
            "services.finance_shadow_read_service._get_pg_payload_for_route",
            new=AsyncMock(return_value={"levy_budgeted_cents": 5000}),
        ), patch(
            "services.finance_shadow_read_service.run_shadow_compare",
            new=AsyncMock(return_value=ShadowCompareResult(building_id="16244", route_key="r", matched=True)),
        ) as mock_compare:
            # 13195 → skipped
            await maybe_run_finance_shadow(
                building_id="13195",
                route_key="finance.summary",
                mongo_payload={},
                is_test_data=True,
            )
            # 16244 → should fire
            await maybe_run_finance_shadow(
                building_id="16244",
                route_key="finance.summary",
                mongo_payload={},
                is_test_data=True,
            )

        assert mock_compare.await_count == 1
        assert mock_compare.call_args.kwargs["building_id"] == "16244"
