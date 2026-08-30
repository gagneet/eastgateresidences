# @featuretrace:finance-shadow-reads — PostgreSQL finance shadow-read comparison service.
# Layer: service
# Data flow: finance route hook → run_shadow_compare() → FinancialReadService → core.shadow_diffs
#            (building-scoped). MongoDB response is returned unchanged; PG values are comparison-only.
# Related: backend/routers/finance.py
#          backend/services/financial_read_service.py
#          backend/services/cutover_status_service.py
#          backend/scripts/postgres_cutover_p0_readiness.py
#          docs/migration/finance-shadow-reads.md
# Toggle: financial_shadow_reads_enabled
"""Finance shadow-read comparison service.

Rules:
  - MongoDB is always the response source during shadow mode. PG is comparison only.
  - Money comparisons use integer cents. Never compare formatted strings.
  - Comparison failures must never raise user-facing errors.
  - Diff records are building-scoped and never contain sensitive owner PII.
  - Shadow reads only run when financial_shadow_reads_enabled is active for the building.
  - Building A shadow state must not affect Building B.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from services.cutover_status_service import (
    _get_bypass_session_context,
    get_or_default_cutover_status,
    record_shadow_diff,
)
from services.financial_read_service import FinancialReadService
from services.cutover_config_service import (
    FINANCIAL_SHADOW_READS_ENABLED,
    is_cutover_feature_enabled,
)
# FieldDiff/ShadowCompareResult and the four comparator primitives moved to
# shadow_read_service.py (2026-07-14, Phase B of the shadow-read expansion) so
# identity/trust domains don't each redefine them. This module keeps its own
# run_shadow_compare/_safe_record_diff/_should_record_shadow_ok orchestration
# (rather than delegating to shadow_read_service.run_shadow_compare) because the
# existing test suite (test_finance_shadow_read_service.py, test_finance_shadow_parity.py)
# patches these names directly on this module — patching services.finance_shadow_read_service.X
# only affects calls made from within a function actually defined in this module.
from services.shadow_read_service import (
    FieldDiff,
    ShadowCompareResult,
    compare_money_fields,
    compare_count_fields,
    compare_status_fields,
    compare_list_lengths,
    _safe_record_coverage,
)

logger = logging.getLogger(__name__)

_DOMAIN = "finance_ledger"
_DEFAULT_TOLERANCE_CENTS = 0  # strict by default
_ROUTE_TOLERANCES: dict[str, int] = {
    # 2026-08-09: widened 1c -> 5c when finance.summary's comparator gained N-unit
    # cumulative fields (fund-split expense totals, grace-aware arrears, canonical
    # status counts) -- matching finance.building_overview's own precedent below, which
    # needed 5c for the same class of figure (cumulative per-unit rounding residual
    # across 87 lots, not a bug). Provisional: re-measure against live core.shadow_diffs
    # once this route has real traffic and tighten if the observed gap is smaller.
    "finance.summary": 5,
    # building_overview.total_paid has shown a perfectly stable, deterministic
    # 4-cent gap (pg=$101,671.73 vs mongo=$101,671.69) across every recorded
    # shadow-diff for East Gate (87 units) since the diagnosis in
    # tasks/current-status.md's 2026-07-13 entry — confirmed still exactly -4
    # cents on every occurrence as of 2026-07-19/20, live-checked via
    # core.shadow_diffs before raising this from 1c. This is cumulative
    # per-unit rounding residual across 87 lots, not a bug: a single-value 1c
    # tolerance was sized for a rounding-conversion gap, not an N-unit
    # cumulative one. 5c leaves a small margin above the observed 4c while
    # still catching a materially different (e.g. 50c+) future divergence.
    "finance.building_overview": 5,
    "finance.unit_dashboard_overview": 1,
    "finance.levy_kpi": 1,
    "finance.arrears": 0,
    "finance.arrears_detail": 0,
    "finance.fund_balances": 0,
}

class _NotApplicable:
    """Sentinel: this comparison could not be SCOPED, so it was never attempted.

    Distinct from ``None`` (PostgreSQL was queried and was genuinely unavailable /
    errored / had no data). The difference matters because
    ``get_route_shadow_readiness`` counts every non-``shadow_ok`` row in
    ``core.shadow_diffs`` as a diff, so recording a ``pg_unavailable`` row for a
    comparison that was never attempted permanently suppresses a route's readiness
    with a harness artefact rather than a data finding.

    Root cause this fixes (2026-08-27): ``finance.summary`` accumulated 152
    unresolved ``pg_unavailable`` rows, and the PG side of every one of them was
    never actually queried — the payload carried no financial year to scope it by.
    A skipped comparison must leave no trace in the readiness signal.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "<comparison not applicable>"


NOT_APPLICABLE = _NotApplicable()

_financial_read_service = FinancialReadService()


# ---------------------------------------------------------------------------
# Core shadow compare orchestrator
# ---------------------------------------------------------------------------

async def run_shadow_compare(
        *,
        building_id: str,
        route_key: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any] | None,
        tolerance_cents: int | None = None,
        is_test_data: bool = False,
) -> ShadowCompareResult:
    """Compare Mongo and PG payloads for a finance route.

    Records diffs to core.shadow_diffs. Never raises — failures are caught
    and returned in the result.error field so callers can always return the
    original MongoDB response unchanged.

    Args:
        building_id: Building identifier (must be non-empty).
        route_key: Logical route name (e.g. "finance.summary").
        mongo_payload: Existing MongoDB-derived response dict.
        pg_payload: PostgreSQL-derived comparison dict (None if PG unavailable).
        tolerance_cents: Override per-field tolerance. Defaults to route-specific
            or global default.
        is_test_data: When True, diffs are marked is_test_data in the DB.
    """
    tol = tolerance_cents if tolerance_cents is not None else _ROUTE_TOLERANCES.get(route_key, _DEFAULT_TOLERANCE_CENTS)

    if pg_payload is None:
        result = ShadowCompareResult(
            building_id=building_id,
            route_key=route_key,
            matched=False,
            pg_available=False,
            error="pg_payload_unavailable",
        )
        await _safe_record_diff(
            building_id=building_id,
            route_key=route_key,
            diff_type="pg_unavailable",
            mongo_value=None,
            pg_value=None,
            divergence_score=0.5,
            is_test_data=is_test_data,
        )
        await _safe_record_coverage(
            building_id=building_id, domain=_DOMAIN, route=route_key,
            pg_unavailable=1, is_test_data=is_test_data,
        )
        return result

    try:
        diffs = _compare_finance_payloads(
            route_key=route_key,
            mongo_payload=mongo_payload,
            pg_payload=pg_payload,
            tolerance_cents=tol,
        )
    except Exception as exc:
        logger.error("finance_shadow_read: compare failed for %s/%s: %s", building_id, route_key, exc)
        await _safe_record_coverage(
            building_id=building_id, domain=_DOMAIN, route=route_key,
            compare_error=1, is_test_data=is_test_data,
        )
        return ShadowCompareResult(
            building_id=building_id,
            route_key=route_key,
            matched=False,
            error=f"compare_error: {exc}",
        )

    matched = len(diffs) == 0
    result = ShadowCompareResult(
        building_id=building_id,
        route_key=route_key,
        matched=matched,
        diffs=diffs,
    )

    if diffs:
        worst_severity = result.severity
        divergence_score = {"critical": 1.0, "warn": 0.6, "info": 0.2}.get(worst_severity, 0.5)

        # Store a single aggregate diff record (not one per field — keep payload small)
        diff_summary = {
            f.field_path: {"mongo": f.mongo_value, "pg": f.pg_value, "diff_cents": f.diff_cents}
            for f in diffs
        }
        await _safe_record_diff(
            building_id=building_id,
            route_key=route_key,
            diff_type=f"field_mismatch:{worst_severity}",
            mongo_value={"fields": diff_summary},
            pg_value=None,
            divergence_score=divergence_score,
            is_test_data=is_test_data,
        )
        await _safe_record_coverage(
            building_id=building_id, domain=_DOMAIN, route=route_key,
            mismatch=1, is_test_data=is_test_data,
        )
    else:
        if await _should_record_shadow_ok(building_id=building_id, route_key=route_key):
            await _safe_record_diff(
                building_id=building_id,
                route_key=route_key,
                diff_type="shadow_ok",
                mongo_value={"matched": True},
                pg_value=None,
                divergence_score=0.0,
                is_test_data=is_test_data,
            )
        await _safe_record_coverage(
            building_id=building_id, domain=_DOMAIN, route=route_key,
            matched=1, is_test_data=is_test_data,
        )

    return result


def _compare_finance_payloads(
        *,
        route_key: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Dispatch to per-route comparison logic."""
    comparators = {
        "finance.summary": _compare_summary_payloads,
        "finance.building_overview": _compare_building_overview_payloads,
        "finance.unit_dashboard_overview": _compare_unit_dashboard_payloads,
        "finance.levy_kpi": _compare_levy_kpi_payloads,
        "finance.fund_balances": _compare_fund_balances_payloads,
        "finance.arrears": _compare_arrears_payloads,
        "finance.arrears_detail": _compare_arrears_payloads,
        "finance.unit_levy_ledger": _compare_unit_levy_ledger_payloads,
        "finance.transactions": _compare_transactions_payloads,
    }
    comparator = comparators.get(route_key, _compare_generic_payloads)
    return comparator(
        mongo_payload=mongo_payload,
        pg_payload=pg_payload,
        tolerance_cents=tolerance_cents,
    )


def _compare_summary_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_summary_payloads
    Path: backend/services/finance_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []

    # Compare total levied (admin + sinking combined — matches pg_payload's
    # levy_budgeted_cents, which sums both fund types via get_oc_levy_summary()).
    # mongo_payload["admin_fund"] is the raw annual_levies.admin_fund sub-document
    # (levy_income/total_income/closing_balance only) — it has never had a
    # "total_levied" key, so comparing against it always compared PG's real
    # budgeted total against a hard-coded 0. The actual computed total lives at
    # unit_ledger_summary.total_levied (routers/finance.py get_finance_summary).
    mongo_ledger_totals = mongo_payload.get("unit_ledger_summary") or {}
    _d = compare_money_fields(
        field_path="unit_ledger_summary.total_levied",
        mongo_aud=mongo_ledger_totals.get("total_levied"),
        pg_cents=pg_payload.get("levy_budgeted_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    # in_grace_summary.true_arrears_amount vs grace_aware_arrears_cents (GAP-FIN-058 B1,
    # 2026-08-09): both sides are NOW the same due-date-aware "currently overdue" concept
    # -- Mongo's true_arrears_amount already only counts units past their grace deadline
    # (get_arrears_metrics()), and pg_payload["grace_aware_arrears_cents"] sources from
    # get_arrears_summary(grace_aware=True), the same call already verified correct for
    # finance.arrears_detail (levy_items.grace_deadline_date < CURRENT_DATE, Alembic
    # 0077). This resolves the prior version of this comment's documented concept
    # mismatch at the source, the same way _compare_arrears_payloads was fixed --
    # unit_ledger_summary.total_outstanding is NOT compared directly since it can also
    # hold the raw ledger aggregate on the in-grace-periods branch (see
    # get_finance_summary's own in_grace_count branch); true_arrears_amount is the one
    # field guaranteed to be the grace-aware figure on every branch.
    mongo_in_grace = mongo_payload.get("in_grace_summary") or {}
    _d = compare_money_fields(
        field_path="in_grace_summary.true_arrears_amount",
        mongo_aud=mongo_in_grace.get("true_arrears_amount"),
        pg_cents=pg_payload.get("grace_aware_arrears_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    # admin_fund/sinking_fund.actual_expenses vs PG's fund-split expense totals
    # (get_fund_expense_totals, 2026-08-09). budgeted_expenses is deliberately NOT
    # compared -- PG's only fund-budget concept is "what was levied"
    # (get_oc_levy_summary), a different concept from Mongo's staff-entered planning
    # figure (levy_categories.budgeted_amount); forcing these to agree would produce
    # exactly the kind of meaningless divergence total_outstanding used to (see git
    # history on this function) -- same exclusion precedent as
    # _compare_building_overview_payloads' total_outstanding.
    mongo_admin_fund = mongo_payload.get("admin_fund") or {}
    mongo_sinking_fund = mongo_payload.get("sinking_fund") or {}
    _d = compare_money_fields(
        field_path="admin_fund.actual_expenses",
        mongo_aud=mongo_admin_fund.get("actual_expenses"),
        pg_cents=pg_payload.get("admin_expense_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)
    _d = compare_money_fields(
        field_path="sinking_fund.actual_expenses",
        mongo_aud=mongo_sinking_fund.get("actual_expenses"),
        pg_cents=pg_payload.get("sinking_expense_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    # unit_ledger_summary.units_owing/units_paid_up/units_credit vs PG's canonical
    # status counts (get_canonical_ledger_quality). Both sides are already the
    # canonical-unit-count concept (GAP-FIN-014) on the Mongo side.
    mongo_units_owing = mongo_ledger_totals.get("units_owing")
    _d = compare_count_fields(
        field_path="unit_ledger_summary.units_owing",
        mongo_count=mongo_units_owing,
        pg_count=pg_payload.get("units_owing"),
    )
    if _d:
        diffs.append(_d)
    _d = compare_count_fields(
        field_path="unit_ledger_summary.units_paid_up",
        mongo_count=mongo_ledger_totals.get("units_paid_up"),
        pg_count=pg_payload.get("units_paid_up"),
    )
    if _d:
        diffs.append(_d)
    _d = compare_count_fields(
        field_path="unit_ledger_summary.units_credit",
        mongo_count=mongo_ledger_totals.get("units_credit"),
        pg_count=pg_payload.get("units_credit"),
    )
    if _d:
        diffs.append(_d)

    return diffs


def _compare_building_overview_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_building_overview_payloads
    Path: backend/services/finance_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []

    _d = compare_money_fields(
        field_path="total_levied",
        mongo_aud=mongo_payload.get("total_levied"),
        pg_cents=pg_payload.get("levy_budgeted_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    # total_paid: compare PG's year-scoped collected figure against Mongo's YEAR-SCOPED,
    # per-unit-clamped collected figure (total_paid_this_year) — NOT the raw total_paid.
    # GAP-FIN-056 (2026-08-07): this comparison used to read mongo_payload["total_paid"]
    # and passed within the 5c tolerance (a stable 4c rounding residual) UNTIL GAP-FIN-035
    # (2026-08-01) repurposed unit_levy_ledger.total_paid into a cumulative all-time
    # portal back-solve — ~8x the year figure ($1,769,655.36 vs a real $220,187.56 for
    # East Gate's 87 units). PG's levy_collected_cents (Σ finance.levy_items.paid_cents,
    # per-item-capped via finance.receipt_allocations — see get_oc_levy_summary) stayed
    # correctly year-scoped, so comparing it against the now-inflated raw total_paid
    # produced a false field_mismatch:critical on every run (the live building_overview
    # 46-critical shadow_fail observed 2026-08-07). total_paid_this_year — the
    # per-unit-clamped, due-date collected figure the live dashboard actually renders
    # (_get_building_overview_mongo_fallback, routers/finance.py) — is the correct
    # like-for-like target. Falls back to total_paid for older payloads that predate it.
    # This does NOT weaken the gate: if PG genuinely diverges from the year-scoped Mongo
    # figure, it still records a critical diff and blocks promotion.
    mongo_total_paid = mongo_payload.get("total_paid_this_year")
    if mongo_total_paid is None:
        mongo_total_paid = mongo_payload.get("total_paid")
    _d = compare_money_fields(
        field_path="total_paid",
        mongo_aud=mongo_total_paid,
        pg_cents=pg_payload.get("levy_collected_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    # total_outstanding is deliberately NOT compared here. pg_payload["levy_outstanding_cents"]
    # (get_oc_levy_summary's outstanding_result query) is a genuine cumulative AR closing
    # balance — all posted debits/credits on the AR account up to financial-year end, no lower
    # date bound — consistent with the accrual-accounting convention used by the sibling
    # get_unit_levy_balance() in the same file (its own closing_balance query has the same
    # unbounded shape). mongo_payload["total_outstanding"] (get_building_fund_overview,
    # routers/finance.py) is a DIFFERENT, narrower concept: net_balance summed for unit_levy_ledger
    # rows matching {"year": year} only — this year's charges vs payments, with prior-year
    # arrears tracked separately as opening_arrears. These are not the same figure and comparing
    # them produces a large, meaningless "critical" divergence on every run (a ~90x factor
    # observed in production) rather than a real signal. Reconciling them requires a product
    # decision on what "outstanding" should mean across systems, not a shadow-read fix — until
    # that decision is made, this field is intentionally excluded from automated comparison.
    # Correction 2026-07-11: this comment previously claimed finance.arrears_detail's
    # total_arrears check (_compare_arrears_payloads) "IS a valid, apples-to-apples
    # year-scoped comparison". Live evidence found that claim wrong — see
    # _compare_arrears_payloads' own comment for why it's now also excluded, for the same
    # due-date-aware-vs-agnostic reason as this function's total_outstanding exclusion.

    return diffs


def _compare_unit_dashboard_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_unit_dashboard_payloads
    Path: backend/services/finance_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []

    _d = compare_money_fields(
        field_path="total_outstanding",
        mongo_aud=mongo_payload.get("total_outstanding"),
        pg_cents=pg_payload.get("total_outstanding_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    return diffs


def _compare_levy_kpi_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Substantive same-concept comparator for GET /finance/levy-kpi (GAP-FIN-058, B1).

    Compares Mongo's ``quarter_billed_total_display`` (a FLAT single-quarter target
    rate: total_payable_quarterly * total_uoe, never cumulative) against
    ``pg_payload["quarter_billed_cents"]`` (get_current_quarter_levy_total() -- the sum
    of finance.levy_items for only the MOST RECENTLY ISSUED levy_run's issue_date, not
    the whole financial year).

    FIXED 2026-08-09 (root cause of the "PG=2.0x Mongo" divergence): this used to
    compare against get_oc_levy_summary's total_budgeted, a YTD-CUMULATIVE sum across
    every levy_run raised so far this financial year -- a genuinely different concept
    from Mongo's flat per-quarter figure, not a bug in either side. It happened to match
    1:1 when first checked (2026-07-12) purely because East Gate had exactly 1 quarter
    raised in PG at that moment; once a 2nd quarter was raised, PG's cumulative total
    doubled while Mongo's flat rate stayed constant, producing an exact, deterministic
    2.0x that looked like a bug but was the old comparator targeting the wrong PG
    concept. get_current_quarter_levy_total() fixes this at the source instead of
    excluding the field or widening tolerance to paper over it.

    ``total_annual_gross`` stays EXCLUDED: it is Mongo's forward-looking PROPOSED annual
    budget (admin_a_gross + sinking_a_gross via get_levy_proposed_amounts()), for which PG
    has no equivalent figure at all — comparing it against levied-to-date produced the
    meaningless "critical" divergence (mongo $400,341.00 vs pg $110,093.74, live
    2026-07-12) that previously forced this whole comparator to a no-op ``return []``.
    Re-add it here only once PG stores an actual proposed-budget figure.
    """
    diffs: list[FieldDiff] = []
    m_levied = mongo_payload.get("quarter_billed_total_display")
    pg_levied = pg_payload.get("quarter_billed_cents")
    if m_levied is not None and pg_levied is not None:
        _d = compare_money_fields(
            field_path="quarter_billed_total_display",
            mongo_aud=m_levied,
            pg_cents=pg_levied,
            tolerance_cents=tolerance_cents,
        )
        if _d:
            diffs.append(_d)
    return diffs


def _compare_fund_balances_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_fund_balances_payloads
    Path: backend/services/finance_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []
    _d = compare_money_fields(
        field_path="admin_balance",
        mongo_aud=mongo_payload.get("admin_balance"),
        pg_cents=pg_payload.get("admin_balance_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)
    _d = compare_money_fields(
        field_path="sinking_balance",
        mongo_aud=mongo_payload.get("sinking_balance"),
        pg_cents=pg_payload.get("sinking_balance_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)
    return diffs


def _compare_arrears_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Substantive same-concept comparator for GET /arrears/detail (GAP-FIN-058, B1).

    Live caller: routers/finance.py get_arrears_board ("finance.arrears" has no live call
    site despite being registered in the dispatch table). Mongo's total_arrears is a
    per-unit sum of true_arrears (due-date-aware; only units currently past their grace
    deadline, sub-cent skipped) and units_in_arrears the count of those units.

    Both sides are now the SAME concept: _get_pg_payload_for_route sources the PG side from
    get_arrears_summary(grace_aware=True), i.e. levy_items whose
    grace_deadline_date < CURRENT_DATE (Alembic 0077) — "currently overdue arrears" — the
    same measure the Mongo route computes. The prior version returned [] because the PG
    source was the due-date-AGNOSTIC year-scoped aggregate (all unpaid levy_items),
    a different concept (mongo=$619.01/10 vs pg=$16,128.08/16, live 2026-07-11); that
    exclusion is now resolved at the source, not by ignoring the fields.

    PENDING LIVE VALIDATION (GAP-FIN-057): the PG arrears figure derives from
    levy_items.paid_cents, which is overstated building-wide until the orphaned
    receipt_allocations reversal is applied. Until GATE A runs, this comparator will
    correctly report the PG side as UNDERSTATED vs Mongo — that divergence is the intended
    signal, not a defect in this function. Do NOT widen tolerance to mask it; re-check
    after 057 lands (and note GATE A's own per-unit [REVIEW] entanglement with
    GAP-FIN-046 Bug-B may leave a residual gap on some units even post-057).
    """
    diffs: list[FieldDiff] = []
    m_arrears = mongo_payload.get("total_arrears")
    pg_arrears = pg_payload.get("total_arrears_cents")
    if m_arrears is not None and pg_arrears is not None:
        _d = compare_money_fields(
            field_path="total_arrears",
            mongo_aud=m_arrears,
            pg_cents=pg_arrears,
            tolerance_cents=tolerance_cents,
        )
        if _d:
            diffs.append(_d)
    m_units = mongo_payload.get("units_in_arrears")
    pg_units = pg_payload.get("units_in_arrears")
    if m_units is not None and pg_units is not None:
        _d = compare_count_fields(
            field_path="units_in_arrears",
            mongo_count=m_units,
            pg_count=pg_units,
        )
        if _d:
            diffs.append(_d)
    return diffs


def _compare_unit_levy_ledger_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """GAP-FIN-030 Fix 4: shadow comparator for finance.unit_levy_ledger (Levy Status
    tab). Aggregate-level only (building-wide totals across all units), not
    per-unit -- sufficient to detect systemic divergence without needing a
    full per-row diff. Both sides deliberately built as pre-aggregated summary
    dicts by their respective callers, not the raw per-unit list/response."""
    diffs: list[FieldDiff] = []

    _d = compare_money_fields(
        field_path="total_levied",
        mongo_aud=mongo_payload.get("total_levied"),
        pg_cents=pg_payload.get("total_levied_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    _d = compare_money_fields(
        field_path="total_paid",
        mongo_aud=mongo_payload.get("total_paid"),
        pg_cents=pg_payload.get("total_paid_cents"),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    _d = compare_count_fields(
        field_path="unit_count",
        mongo_count=mongo_payload.get("unit_count"),
        pg_count=pg_payload.get("unit_count"),
    )
    if _d:
        diffs.append(_d)

    return diffs


def _compare_transactions_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """GAP-FIN-030 Fix 4: shadow comparator for finance.transactions (Transactions
    tab). Aggregate totals only, same rationale as _compare_unit_levy_ledger_payloads.

    ONLY the dimension the caller actually populated is compared (fixed 2026-08-29).
    ``finance.transactions`` is fired from two separate endpoints — expense-transactions
    and income-transactions — and each passes an EMPTY list for the other side, so the
    un-populated total is a structural 0.0 rather than a measurement. Comparing that 0.0
    against PG's real building-wide total produced a guaranteed ``field_mismatch:critical``
    on every single production call to either endpoint: live values were
    ``total_income pg=201321215 mongo=7700`` and ``total_expense pg=14565265 mongo=250000``.

    ``routers/finance.py::_maybe_shadow_transactions`` has passed ``_dimension`` for
    exactly this purpose, and its own docstring describes this behaviour — but nothing
    here ever read the key, so the documented fix was never actually in force. A caller
    that omits ``_dimension`` still gets both fields compared, preserving the old
    behaviour for any direct/test invocation that legitimately populates both sides.
    """
    diffs: list[FieldDiff] = []
    dimension = mongo_payload.get("_dimension")

    # An UNRECOGNISED dimension must not silently disable the comparator. With a naive
    # `if dimension in (None, "expense")` / `elif ... "income"` pair, a typo or a future
    # third dimension makes neither branch fire, nothing is compared, and the route
    # reports a clean shadow forever — a false PASS, which is strictly worse than the
    # false FAIL this function was fixed to stop producing. Fall back to comparing both
    # sides, the pre-2026-08-29 behaviour, and say so.
    if dimension is not None and dimension not in ("expense", "income"):
        logger.warning(
            "finance shadow: unrecognised _dimension %r on finance.transactions — "
            "comparing BOTH dimensions rather than silently skipping the comparison",
            dimension,
        )
        dimension = None

    if dimension in (None, "expense"):
        _d = compare_money_fields(
            field_path="total_expense",
            mongo_aud=mongo_payload.get("total_expense"),
            pg_cents=pg_payload.get("total_expense_cents"),
            tolerance_cents=tolerance_cents,
        )
        if _d:
            diffs.append(_d)

    if dimension in (None, "income"):
        _d = compare_money_fields(
            field_path="total_income",
            mongo_aud=mongo_payload.get("total_income"),
            pg_cents=pg_payload.get("total_income_cents"),
            tolerance_cents=tolerance_cents,
        )
        if _d:
            diffs.append(_d)

    return diffs


def _compare_generic_payloads(
        *,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
        tolerance_cents: int,
) -> list[FieldDiff]:
    """Fallback: compare any matching numeric keys as money fields."""
    diffs: list[FieldDiff] = []
    for key in set(mongo_payload) & set(pg_payload):
        m_val = mongo_payload[key]
        p_val = pg_payload[key]
        if isinstance(m_val, (int, float)) and isinstance(p_val, int):
            _d = compare_money_fields(
                field_path=key,
                mongo_aud=float(m_val),
                pg_cents=p_val,
                tolerance_cents=tolerance_cents,
            )
            if _d:
                diffs.append(_d)
    return diffs


# ---------------------------------------------------------------------------
# Readiness queries
# ---------------------------------------------------------------------------

async def get_route_shadow_readiness(
        *,
        building_id: str,
        route_key: str,
        lookback_hours: int = 24,
) -> dict[str, Any]:
    """Return shadow readiness for one route.

    Queries core.shadow_diffs for recent records for this building/route.
    Returns:
        status: "not_started" | "shadow_running" | "shadow_pass" |
                "shadow_warn" | "shadow_fail" | "ready_for_postgres_read"
        diff_count: int
        critical_count: int
        last_compared_at: str | None
    """
    try:
        async with _get_bypass_session_context() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        COUNT(CASE WHEN diff_type != 'shadow_ok' THEN 1 END) AS total_diffs,
                        COUNT(CASE WHEN diff_type = 'shadow_ok' THEN 1 END) AS pass_samples,
                        COUNT(CASE WHEN diff_type LIKE :crit_pattern THEN 1 END) AS critical_count,
                        MAX(created_at) AS last_at
                    FROM core.shadow_diffs
                    WHERE building_id = :building_id
                      AND route = :route
                      AND domain = :domain
                      AND is_test_data = FALSE
                      -- Adjudicated diffs must stop counting (2026-08-27). Without this
                      -- clause, resolving a diff had NO effect on readiness, which made
                      -- the whole triage/annotate path inert -- including
                      -- east_gate_phase_d_activate.py's own documented step 4 ("marks the
                      -- stale 'pg_unavailable' shadow_diffs as resolved"), which was a
                      -- no-op for the gate it was written to unblock. A resolved row keeps
                      -- its audit trail in the table; it just no longer vetoes promotion.
                      AND resolved = FALSE
                      AND created_at > NOW() - INTERVAL '1 hour' * :hours
                    """
                ),
                {
                    "building_id": building_id,
                    "route": route_key,
                    "domain": _DOMAIN,
                    "hours": lookback_hours,
                    # ':critical' inside a LIKE string literal is parsed by SQLAlchemy as a
                    # named bind parameter; passing it as a value avoids that mis-parse.
                    "crit_pattern": "%:critical%",
                },
            )
            row = result.fetchone()
    except Exception as exc:
        logger.warning("get_route_shadow_readiness: DB query failed: %s", exc)
        return {
            "status": "not_started",
            "diff_count": 0,
            "critical_count": 0,
            "last_compared_at": None,
            "error": str(exc),
        }

    total = int((row.total_diffs if row is not None else 0) or 0)
    pass_samples = int((row.pass_samples if row is not None else 0) or 0)
    critical = int((row.critical_count if row is not None else 0) or 0)
    last_at = row.last_at.isoformat() if row and row.last_at else None

    if row is None or (total == 0 and pass_samples == 0 and last_at is None):
        return {
            "status": "not_started",
            "diff_count": 0,
            "critical_count": 0,
            "last_compared_at": None,
        }

    if critical > 0:
        status = "shadow_fail"
    elif pass_samples > 0 and total == 0:
        status = "shadow_pass"
    elif total > 0:
        status = "shadow_warn"
    else:
        status = "shadow_running" if pass_samples > 0 else "not_started"

    return {
        "status": status,
        "diff_count": total,
        "critical_count": critical,
        "last_compared_at": last_at,
        "pass_samples": pass_samples,
    }


async def get_building_finance_shadow_readiness(
        *,
        building_id: str,
) -> dict[str, Any]:
    """Return shadow readiness summary across all finance routes for a building."""
    route_keys = list(_ROUTE_TOLERANCES.keys())

    readiness_tasks = [
        get_route_shadow_readiness(building_id=building_id, route_key=r)
        for r in route_keys
    ]
    results = await asyncio.gather(*readiness_tasks, return_exceptions=True)

    per_route: dict[str, Any] = {}
    overall_critical = 0
    overall_diffs = 0
    started = 0

    for route_key, res in zip(route_keys, results):
        if isinstance(res, Exception):
            per_route[route_key] = {"status": "error", "error": str(res)}
        else:
            per_route[route_key] = res
            if res.get("status") != "not_started":
                started += 1
                overall_diffs += res.get("diff_count", 0)
                overall_critical += res.get("critical_count", 0)

    if started == 0:
        overall_status = "not_started"
    elif overall_critical > 0:
        overall_status = "shadow_fail"
    elif overall_diffs > 0:
        overall_status = "shadow_warn"
    else:
        overall_status = "shadow_pass"

    return {
        "building_id": building_id,
        "overall_status": overall_status,
        "routes_started": started,
        "total_diff_count": overall_diffs,
        "total_critical_count": overall_critical,
        "per_route": per_route,
    }


# ---------------------------------------------------------------------------
# Population-scope guard
# ---------------------------------------------------------------------------

# route_key -> (mongo payload key, pg payload key) naming the POPULATION each
# aggregate covers.
#
# A population marker is a denominator, not a measurement: "these totals describe
# N units". When the two sides cover different populations their money totals are
# not comparable at all, and recording the difference as a money divergence is a
# category error — it says "Postgres is wrong by $208,623.26" when what happened is
# that one side was handed a single unit and the other the whole building.
#
# Live evidence (2026-08-29, building 13195): thirteen unresolved
# ``finance.unit_levy_ledger`` diffs carried
# ``unit_count {pg: 87, mongo: 1}`` alongside
# ``total_paid {pg: 21214626, mongo: 352300}``. Measured directly the same day the two
# stores agree to the cent on that route — Mongo FY2026 is 87 units, $220,187.56 levied
# and $212,146.26 paid; Postgres is 22018756 and 21214626 cents over 87 lots. There was
# no divergence to find. The diffs were a scope artefact and they blocked the finance
# read gate regardless.
#
# Deliberately NOT applied to finance.arrears/arrears_detail: ``units_in_arrears`` is a
# MEASURED value there (how many units are in arrears), not a population. Guarding on it
# would suppress exactly the divergence that route exists to detect.
_POPULATION_KEYS: dict[str, tuple[str, str]] = {
    "finance.unit_levy_ledger": ("unit_count", "unit_count"),
}


def population_scope_conflict(
        *,
        route_key: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any],
) -> str | None:
    """Return a human-readable reason when the two payloads cover different populations.

    ``None`` means the comparison is scoped consistently and may proceed. A returned
    string means the comparison must be abandoned (recorded as nothing, exactly like
    NOT_APPLICABLE), never downgraded to a warning — a mis-scoped comparison carries no
    information about the data at any severity.
    """
    keys = _POPULATION_KEYS.get(route_key)
    if not keys:
        return None
    mongo_key, pg_key = keys
    mongo_n = mongo_payload.get(mongo_key)
    pg_n = pg_payload.get(pg_key)
    if mongo_n is None or pg_n is None:
        # One side does not declare its population. Refusing here would silently
        # disable the route's shadow coverage, so proceed and let the field
        # comparators speak — the same choice the year guard makes.
        return None
    if int(mongo_n) != int(pg_n):
        return (
            f"population mismatch on {mongo_key}: mongo={int(mongo_n)} pg={int(pg_n)} "
            f"— aggregates cover different unit sets and are not comparable"
        )
    return None


# ---------------------------------------------------------------------------
# Route hook helper — call from finance router
# ---------------------------------------------------------------------------

async def maybe_run_finance_shadow(
        *,
        building_id: str,
        route_key: str,
        mongo_payload: dict[str, Any],
        is_test_data: bool = False,
) -> None:
    """Fire-and-forget shadow compare for a finance route.

    Called from finance router handlers after computing the Mongo response.
    Does NOT alter the response. Always catches all exceptions so the API
    caller is never affected.

    The PG payload is computed internally by querying FinancialReadService.
    """
    try:
        enabled = await _is_shadow_route_enabled(building_id)
        if not enabled:
            return
    except Exception as exc:
        logger.debug("finance shadow: toggle check failed for %s: %s", building_id, exc)
        return

    try:
        pg_payload = await _get_pg_payload_for_route(
            building_id=building_id,
            route_key=route_key,
            mongo_payload=mongo_payload,
        )
    except Exception as exc:
        logger.info("finance shadow: PG payload fetch failed for %s/%s: %s", building_id, route_key, exc)
        pg_payload = None

    if isinstance(pg_payload, _NotApplicable):
        # Never attempted -> record nothing. Recording a pg_unavailable row here
        # would count against get_route_shadow_readiness' diff_count and block
        # promotion on a harness artefact. See _NotApplicable's docstring.
        logger.debug(
            "finance shadow: comparison not applicable for %s/%s (payload could not be scoped)",
            building_id, route_key,
        )
        return

    conflict = population_scope_conflict(
        route_key=route_key,
        mongo_payload=mongo_payload,
        pg_payload=pg_payload or {},
    )
    if conflict:
        # Record nothing, for the same reason as NOT_APPLICABLE above: a comparison
        # between two different populations is not a divergence at any severity, and
        # writing it would count against this route's shadow readiness forever.
        logger.info(
            "finance shadow: skipped mis-scoped comparison for %s/%s — %s",
            building_id, route_key, conflict,
        )
        return

    try:
        await run_shadow_compare(
            building_id=building_id,
            route_key=route_key,
            mongo_payload=mongo_payload,
            pg_payload=pg_payload,
            is_test_data=is_test_data,
        )
    except Exception as exc:
        logger.info("finance shadow: compare failed for %s/%s: %s", building_id, route_key, exc)


async def _get_pg_payload_for_route(
        *,
        building_id: str,
        route_key: str,
        mongo_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Fetch the PG read-model payload for a given route."""
    year = (
        mongo_payload.get("year")
        or mongo_payload.get("financial_year")
        or None
    )

    if route_key == "finance.summary":
        # Own branch (2026-08-09, GAP-FIN-058 B1) -- get_building_finance_pg_dashboard
        # (used by building_overview/levy_kpi below) has none of the fields
        # _compare_summary_payloads now checks. Keys are distinctly named from that
        # shared branch's (e.g. grace_aware_arrears_cents, not total_arrears_cents) so
        # they can never collide if a future refactor merges these branches.
        if not year:
            # get_canonical_ledger_quality requires a real financial_year (unlike the
            # other three sub-calls, which resolve their own via
            # _get_financial_year_window when None). routers/finance.py::get_finance_summary
            # always sets mongo_payload["year"] (from levy["year"], after its own
            # `if not levy:` early return), so this is a defensive branch -- but it must
            # return NOT_APPLICABLE, not None: None is recorded as a pg_unavailable diff
            # and counts against this route's shadow readiness forever.
            return NOT_APPLICABLE
        fy = str(year)
        try:
            oc_levy, arrears_grace, fund_expenses, ledger_quality = await asyncio.gather(
                _financial_read_service.get_oc_levy_summary(
                    building_id=building_id, financial_year=fy,
                ),
                _financial_read_service.get_arrears_summary(
                    building_id=building_id, financial_year=fy, grace_aware=True,
                ),
                _financial_read_service.get_fund_expense_totals(
                    building_id=building_id, financial_year=fy,
                ),
                _financial_read_service.get_canonical_ledger_quality(
                    building_id=building_id, financial_year=fy,
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.info("finance shadow: finance.summary PG gather failed: %s", exc)
            return None
        for name, result in (
            ("get_oc_levy_summary", oc_levy),
            ("get_arrears_summary", arrears_grace),
            ("get_fund_expense_totals", fund_expenses),
            ("get_canonical_ledger_quality", ledger_quality),
        ):
            if isinstance(result, BaseException):
                logger.info("finance shadow: finance.summary sub-call %s failed: %r", name, result)
                return None
        if oc_levy is None:
            return None
        return {
            "levy_budgeted_cents": int(round((oc_levy or {}).get("total_budgeted", 0) * 100)),
            "levy_collected_cents": int(round((oc_levy or {}).get("total_collected", 0) * 100)),
            "grace_aware_arrears_cents": (arrears_grace or {}).get("total_arrears_cents", 0),
            "units_in_arrears": (arrears_grace or {}).get("units_in_arrears", 0),
            "admin_expense_cents": (fund_expenses or {}).get("admin_expense_cents", 0),
            "sinking_expense_cents": (fund_expenses or {}).get("sinking_expense_cents", 0),
            "units_owing": (ledger_quality or {}).get("canonical_status_counts", {}).get("owing", 0),
            "units_paid_up": (ledger_quality or {}).get("canonical_status_counts", {}).get("paid_up", 0),
            "units_credit": (ledger_quality or {}).get("canonical_status_counts", {}).get("credit", 0),
        }
    if route_key == "finance.levy_kpi":
        # Own branch (2026-08-09) -- root cause of the "PG=2.0x Mongo" divergence:
        # get_building_finance_pg_dashboard's levy_budgeted_cents (used by
        # building_overview below, correctly for ITS concept) is YTD-cumulative across
        # every levy_run raised this financial year, while Mongo's
        # quarter_billed_total_display is a flat single-quarter target rate. See
        # get_current_quarter_levy_total()'s docstring for the full root-cause writeup.
        quarter_total = await _financial_read_service.get_current_quarter_levy_total(
            building_id=building_id, financial_year=str(year) if year else None,
        )
        if quarter_total is None:
            return None
        return {"quarter_billed_cents": quarter_total.get("quarter_billed_cents", 0)}
    if route_key == "finance.building_overview":
        return await _financial_read_service.get_building_finance_pg_dashboard(
            building_id=building_id,
            financial_year=str(year) if year else None,
        )
    if route_key == "finance.unit_dashboard_overview":
        unit_number = mongo_payload.get("unit_number") or ""
        if not unit_number:
            # Cannot scope the PG side to a lot -> never attempted, not "unavailable".
            return NOT_APPLICABLE
        balance = await _financial_read_service.get_unit_levy_balance(
            building_id=building_id,
            unit_number=unit_number,
            financial_year=str(year) if year else None,
        )
        if balance is None:
            return None
        return {
            "total_outstanding_cents": int(round((balance.get("arrears") or 0) * 100)),
        }
    if route_key == "finance.fund_balances":
        return await _financial_read_service.get_fund_balances(building_id=building_id)
    if route_key in ("finance.arrears", "finance.arrears_detail"):
        # grace_aware=True (GAP-FIN-058 / B1, 2026-08-09): source the PG side from
        # levy_items whose grace_deadline_date < CURRENT_DATE (Alembic 0077) so it
        # measures the SAME "currently overdue arrears" concept as the Mongo
        # GET /arrears/detail figure (per-unit true_arrears, skipping units not yet past
        # grace). The prior year-scoped-all-unpaid source made this a different accounting
        # concept, which is why _compare_arrears_payloads used to return [] — see that
        # function for the full evidence trail.
        if not year:
            # CROSS-YEAR GUARD (2026-08-27). Mongo's side is scoped to the year the
            # caller asked for; passing financial_year=None here makes the PG side
            # resolve its OWN default window instead. When those two years differ the
            # comparator reports a full-value critical mismatch that is purely an
            # artefact of the two sides measuring different years -- e.g. PG on FY2026
            # ($8,041.30 / 14 units) against Mongo on FY2025 ($0.00 / 0 units).
            # get_arrears_board now always puts its resolved year in the payload;
            # refuse to guess if it is ever absent.
            return NOT_APPLICABLE
        arrears = await _financial_read_service.get_arrears_summary(
            building_id=building_id,
            financial_year=str(year),
            grace_aware=True,
        )
        if arrears is None:
            return None
        # Keep total_arrears_cents in cents, matching every other branch of this function
        # and what _compare_arrears_payloads' compare_money_fields(pg_cents=...) expects.
        # A prior version converted to AUD under the key "total_arrears" here, which meant
        # pg_payload.get("total_arrears_cents") in the comparator always returned None,
        # silently comparing mongo's real total against a hard-coded 0 (confirmed in
        # production 2026-07-11: mongo=$619.01/10 units vs pg=$0.00/16 units on the same
        # request — the units_in_arrears count compared correctly since its key name was
        # never mismatched; only the money field was affected).
        return {
            "total_arrears_cents": int(arrears.get("total_arrears_cents") or 0),
            "units_in_arrears": int(arrears.get("units_in_arrears") or 0),
            "basis": arrears.get("basis"),
        }
    if route_key == "finance.unit_levy_ledger":
        balances = await _financial_read_service.get_unit_levy_balance_list(
            building_id=building_id,
            financial_year=str(year) if year else None,
        )
        if balances is None:
            return None
        return {
            "unit_count": len(balances),
            "total_levied_cents": int(round(sum(b.get("levied_amount") or 0 for b in balances) * 100)),
            "total_paid_cents": int(round(sum(b.get("paid_amount") or 0 for b in balances) * 100)),
        }
    if route_key == "analytics.levy_allocation_breakdown":
        # Scalar admin/sinking/total fund totals in CENTS — compared field-for-field
        # against the Mongo route's AUD figures by _compare_generic_payloads.
        from services.analytics_pg_service import get_levy_allocation_totals_pg
        return await get_levy_allocation_totals_pg(
            building_id, str(year) if year else None,
        )
    if route_key == "analytics.sinking_fund_forecast":
        # Derived scalar: current sinking-fund balance in CENTS (GAP-FIN-055).
        from services.analytics_pg_service import get_sinking_fund_forecast_pg
        pg = await get_sinking_fund_forecast_pg(building_id, 10)
        cb = (pg or {}).get("current_balance")
        if cb is None:
            return None
        return {"current_balance": int(round(float(cb) * 100))}
    if route_key == "analytics.expense_breakdown":
        # Derived scalar: total expense (Σ category amount) in CENTS (GAP-FIN-055).
        from services.analytics_pg_service import get_expense_breakdown_pg
        rows = await get_expense_breakdown_pg(building_id, str(year) if year else "2026")
        total = sum(float(r.get("amount", 0) or 0) for r in (rows or []) if isinstance(r, dict))
        return {"total_expense": int(round(total * 100))}
    if route_key == "analytics.levy_benchmarks":
        # Derived scalar: total building levy (Σ per-year Building) in CENTS (GAP-FIN-055).
        from services.analytics_pg_service import get_levy_benchmarks_pg
        rows = await get_levy_benchmarks_pg(building_id, None)
        total = sum(float(r.get("Building", 0) or 0) for r in (rows or []) if isinstance(r, dict))
        return {"total_levied": int(round(total * 100))}
    if route_key == "finance.transactions":
        transactions = await _financial_read_service.get_transactions_for_year(
            building_id=building_id,
            financial_year=str(year) if year else None,
        )
        if transactions is None:
            return None
        # Only compute the dimension the Mongo side actually populated -- the
        # caller (routers/finance.py's expense-transactions/income-transactions
        # endpoints) each fire this comparison with only their own side real
        # and the other passed as an empty list. Returning both cents totals
        # unconditionally here previously produced a guaranteed false-positive
        # critical diff against the intentionally-empty side on every call.
        dimension = mongo_payload.get("_dimension")
        pg_payload: dict[str, Any] = {}
        if dimension != "income":
            pg_payload["total_expense_cents"] = int(round(
                sum(t.get("amount") or 0 for t in transactions if t.get("transaction_type") == "expense") * 100
            ))
        if dimension != "expense":
            pg_payload["total_income_cents"] = int(round(
                sum(t.get("amount") or 0 for t in transactions if t.get("transaction_type") == "income") * 100
            ))
        return pg_payload

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _safe_record_diff(
        *,
        building_id: str,
        route_key: str,
        diff_type: str,
        mongo_value: dict[str, Any] | None,
        pg_value: dict[str, Any] | None,
        divergence_score: float,
        is_test_data: bool,
) -> None:
    """Record a shadow diff without raising."""
    try:
        await record_shadow_diff(
            building_id=building_id,
            domain=_DOMAIN,
            route=route_key,
            diff_type=diff_type,
            mongo_value=mongo_value,
            pg_value=pg_value,
            divergence_score=divergence_score,
            is_test_data=is_test_data,
        )
    except Exception as exc:
        logger.error("finance_shadow_read: record_shadow_diff failed: %s", exc)


def summarize_shadow_result(result: ShadowCompareResult) -> dict[str, Any]:
    """Serialise a ShadowCompareResult to a plain dict (for logging/responses)."""
    return {
        "building_id": result.building_id,
        "route_key": result.route_key,
        "matched": result.matched,
        "severity": result.severity,
        "diff_count": len(result.diffs),
        "pg_available": result.pg_available,
        "error": result.error,
        "compared_at": result.compared_at.isoformat(),
        "diffs": [
            {
                "field_path": d.field_path,
                "mongo_value": d.mongo_value,
                "pg_value": d.pg_value,
                "diff_cents": d.diff_cents,
                "severity": d.severity,
            }
            for d in result.diffs
        ],
    }

async def _is_shadow_route_enabled(building_id: str) -> bool:
    """Shadow runs when toggle is enabled OR finance domain is in postgres_shadow."""
    if await is_cutover_feature_enabled(building_id, FINANCIAL_SHADOW_READS_ENABLED):
        return True
    status = await get_or_default_cutover_status(building_id, "finance_ledger")
    return status.mode.value == "postgres_shadow"


async def _should_record_shadow_ok(*, building_id: str, route_key: str) -> bool:
    """Bound storage: persist at most one shadow_ok sample per 30 minutes per route."""
    try:
        async with _get_bypass_session_context() as session:
            result = await session.execute(
                text(
                    """
                    SELECT 1
                    FROM core.shadow_diffs
                    WHERE building_id = :building_id
                      AND domain = :domain
                      AND route = :route
                      AND diff_type = 'shadow_ok'
                      AND is_test_data = FALSE
                      AND created_at > NOW() - INTERVAL '30 minutes'
                    LIMIT 1
                    """
                ),
                {"building_id": building_id, "domain": _DOMAIN, "route": route_key},
            )
            return result.fetchone() is None
    except Exception:
        return False
