"""
backend/domain/finance/formulas/collection.py — collection-rate formulas.

# @featuretrace:financial-metrics-canonical — levy.collection.current_year.v1, levy.collection.historical_year.v1
# Layer: domain
# Data flow: services/finance_metrics/facade.py → this module (pure) → MetricResult (global).
# Related: backend/server.py get_building_kpis() (original, still-live inline implementation
#          this module is extracted from — see METRIC[collection_rate] / METRIC[total_obligations]
#          comments there and tests/backend/test_metric_consistency.py, which this extraction
#          must continue to satisfy).

Both formulas are verbatim re-expressions of the logic already in
server.py::get_building_kpis — same inputs, same rounding, same clamping —
re-typed to Cents/Decimal per the backend/domain/ no-float rule. Verified
byte-identical against live East Gate (13195) data on 2026-07-12: the current-year
formula reproduces the live collection_rate value 86.36 exactly when given the
same inputs converted to cents (see tests/backend/test_domain_finance_formulas.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from domain import Cents
from domain.finance.money import cents_to_percentage, clamp_non_negative, raw_percentage


@dataclass(frozen=True)
class CurrentYearCollectionResult:
    collection_rate_pct: Decimal
    total_obligations_cents: Cents
    net_collected_cents: Cents


@dataclass(frozen=True)
class DueDateCollectionResult:
    collection_rate_pct: Decimal
    due_to_date_cents: Cents
    collected_to_date_cents: Cents


def due_date_collection_rate(
    *,
    due_to_date_cents: Cents,
    collected_to_date_cents: Cents,
    digits: int = 1,
) -> DueDateCollectionResult:
    """levy.collection.due_date.v1 — GAP-FIN-035, 2026-08-03.

    THE canonical "Collection Rate" figure: what fraction of amounts *actually
    due as of today* have actually been collected. This is deliberately a
    DIFFERENT metric from current_year_collection_rate() (aka "fund_health" /
    Full-Year Levy Coverage) above — that formula's denominator is the FULL
    annual levy (including not-yet-due instalments); this one's denominator is
    only the amount charged/due to date. Per the architectural review
    (docs/architecture/financial-summary-analysis-of-issues.md, Rule 53):
    "Collection performance is not fund health." Never relabel one as the
    other; never let a UI element display this function's output under a
    "Fund Health"/"coverage" heading or vice versa.

    Callers MUST compute due_to_date_cents/collected_to_date_cents per-unit,
    clamped, before summing across a building — never pass a raw aggregate
    `levied - Σ(signed net_balance)` figure in here, since that collapses
    into an unclamped, un-gated Σpaid_i the instant one unit pays ahead of
    its own due schedule. See finance_helpers.get_collection_rate_metrics()
    for the correct per-unit aggregation this feeds from: for each unit,
    due_to_date = that unit's own amount charged to date (never the full
    annual levy), and collected_to_date = due_to_date - max(net_balance, 0)
    — i.e. a unit's advance/credit can push its own collected_to_date up to
    (never past) its own due_to_date, but never reduce or inflate any other
    unit's contribution.

    rate = collected_to_date / due_to_date * 100, clamped [0, 100].
    """
    rate = cents_to_percentage(collected_to_date_cents, due_to_date_cents, digits=digits)
    return DueDateCollectionResult(
        collection_rate_pct=rate,
        due_to_date_cents=due_to_date_cents,
        collected_to_date_cents=collected_to_date_cents,
    )


def current_year_collection_rate(
    *,
    opening_arrears_cents: Cents,
    levied_cents: Cents,
    outstanding_cents: Cents,
    digits: int = 2,
) -> CurrentYearCollectionResult:
    """levy.collection.current_year.v1

    "As-of-today" collection rate for a financial year still in progress:
    what fraction of full-year obligations (opening arrears + annual levy)
    has been cleared, net of what's still outstanding.

    total_obligations = opening_arrears + levied
    net_collected     = max(0, total_obligations - outstanding)
    rate              = net_collected / total_obligations * 100, clamped [0, 100]

    This is also `finance.py::get_building_fund_overview`'s `fund_health` formula
    (GAP-FIN-016 Item C, 2026-07-21) — both endpoints display the same underlying
    concept, historically at different precision (`digits` defaults to 2dp for
    server.py's building-kpis callers; get_building_fund_overview passes
    `digits=1` to preserve its own pre-existing display precision).

    Denominator MUST match domain.finance.formulas.arrears.gross_outstanding's
    caller — both derive from the same ledger snapshot. Enforced by
    tests/backend/test_metric_consistency.py::TestFundHealthVsCollectionRate.
    """
    total_obligations_cents = opening_arrears_cents + levied_cents
    net_collected_cents = clamp_non_negative(total_obligations_cents - outstanding_cents)
    rate = cents_to_percentage(net_collected_cents, total_obligations_cents, digits=digits)
    return CurrentYearCollectionResult(
        collection_rate_pct=rate,
        total_obligations_cents=total_obligations_cents,
        net_collected_cents=net_collected_cents,
    )


def historical_year_collection_rate(
    *,
    levied_cents: Cents,
    closing_arrears_cents: Cents,
) -> Decimal:
    """levy.collection.historical_year.v1

    Fraction of a COMPLETED financial year's levy ultimately collected by
    year-end, using next year's opening arrears as this year's closing
    unpaid balance (immune to the DEFT/BPAY confirmed-payment gap that
    breaks the current-year formula for closed years — see server.py
    "Historical vs Current Year" comment block).

    rate = (1 - closing_arrears / levied) * 100, clamped [0, 100].

    closing_arrears_cents may legitimately exceed levied_cents (carry-forward
    arrears pre-dating the ledger, or imported under different GST
    conventions) — the clamp in cents_to_percentage() handles that, matching
    the existing max(0.0, min(100.0, ...)) behaviour exactly.
    """
    if levied_cents <= 0:
        return Decimal(0)
    collected_cents = clamp_non_negative(levied_cents - closing_arrears_cents)
    return cents_to_percentage(collected_cents, levied_cents)


def quarterly_collection_rate(
    *,
    paid_cents: Cents,
    levied_cents: Cents,
    digits: int = 1,
    zero_denominator_value: Decimal | None = Decimal(0),
) -> Decimal | None:
    """levy.quarterly_collection_rate.v1

    paid / levied * 100, NOT clamped to [0, 100] (a building can show >100% when
    overpaid — none of the 3 sites this consolidates ever clamped either, so this
    preserves that behaviour rather than silently changing it).

    GAP-FIN-016 Phase 2b: consolidates the 3 verified-identical percentage-shape
    duplicate sites — backend/routers/trust_phase1.py (get_financial_summary,
    digits=1, zero_denominator_value=Decimal("0.0")), backend/services/bi_service.py
    (_pct() call sites, digits=1, zero_denominator_value=None — this one differs from
    the other two and must stay None, not 0, or a caller checking `if rate is None`
    for "no data yet" would incorrectly see 0.0 instead), and
    backend/routers/external_api.py get_oc_summary (digits=2,
    zero_denominator_value=Decimal("0.00")). Each site passes its own `digits`/
    `zero_denominator_value` rather than this function picking one — the 3 sites
    display at different precision and have different zero-levy semantics; unifying
    those was explicitly flagged as a user-visible-number-change risk to avoid, not a
    duplication to remove (docs/GAP-FIN-016 Phase 2b).

    `get_levy_kpi`'s fraction-shape (0-1) rate and bi_etl_service.py's health-score
    rate are NOT covered by this function — different denominator logic, evaluated
    separately per the same GAP doc.
    """
    return raw_percentage(
        paid_cents,
        levied_cents,
        digits=digits,
        zero_denominator_value=zero_denominator_value,
    )


def cash_collection_percentage(
    *,
    cash_collected_cents: Cents,
    total_revenue_cents: Cents,
    digits: int = 2,
) -> Decimal:
    """cash.collection_percentage.v1

    Cash collected as a percentage of total revenue. A zero or negative revenue
    denominator has no meaningful ratio, so it returns 0 instead of raising a
    division-by-zero error.
    """
    return raw_percentage(
        cash_collected_cents,
        total_revenue_cents,
        digits=digits,
        zero_denominator_value=Decimal(0),
    )


def quarter_collection_fraction(
    *,
    numerator_cents: Cents,
    quarter_billed_cents: Cents,
    digits: int = 4,
) -> Decimal:
    """levy.collection.quarter.v1

    Quarter KPI fraction used by finance.py::get_levy_kpi(), returned in 0-1
    shape instead of display-percent shape. The denominator is the route's
    quarter_billed_display value, which intentionally prefers the annual levy
    contract's total_payable_quarterly target over a per-lot sum when available.

    Keep this separate from quarterly_collection_rate(): that helper returns a
    0-100 display percent and intentionally allows each caller to choose its
    own zero-denominator semantics. get_levy_kpi() has always returned 0 for
    no billed quarter; this helper keeps the 4dp contract but uses exact
    Decimal half-up quantization instead of the old route-local float round().
    """
    if quarter_billed_cents <= 0:
        return Decimal(0)
    quant = Decimal(1).scaleb(-digits)
    return (Decimal(numerator_cents) / Decimal(quarter_billed_cents)).quantize(
        quant,
        rounding=ROUND_HALF_UP,
    )


def levy_collection_fraction(*, paid_cents: Cents, levied_cents: Cents) -> Decimal:
    """levy.collection_fraction.v1

    paid / levied as a 0-1 fraction, not a display percentage and not clamped.
    This exists for score/composite consumers that intentionally apply their own
    downstream scaling and clamp, such as BI health snapshots.
    """
    if levied_cents <= 0:
        return Decimal(0)
    return Decimal(paid_cents) / Decimal(levied_cents)


def levy_collection_health_score(*, paid_cents: Cents, levied_cents: Cents) -> Decimal:
    """levy.collection_health_score.v1

    BI health-score component: paid / levied * 100, clamped to [0, 100].
    Kept separate from quarterly_collection_rate(), which is an unclamped
    display percentage that can legitimately exceed 100.
    """
    return cents_to_percentage(paid_cents, levied_cents)
