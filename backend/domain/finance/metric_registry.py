"""
backend/domain/finance/metric_registry.py — canonical financial metric catalogue.

# @featuretrace:financial-metrics-canonical — the metric ID registry.
# Layer: domain
# Data flow: docs/architecture/financial_metrics_registry.md ← render_table() (global).
# Related: docs/architecture/financial_metrics_registry.md (human-readable rendering of this file)
#          docs/architecture/financial-calculations-deep-dive.md ("Metric registry — proposed initial catalogue")
#          backend/domain/finance/formulas/collection.py

Every financially meaningful number displayed anywhere in the app should have
exactly one entry here. `status="canonical"` means a pure formula function in
domain/finance/formulas/ implements it and at least one live endpoint
delegates to it. `status="planned"` means the concept is registered and named
(so nobody re-derives it under a different name) but the formula still lives
inline at `legacy_location` pending its own migration phase — see
docs/architecture/financial-calculations-deep-dive.md "Implementation
sequence" for the phase each one is scheduled in.

Run `python3 -m domain.finance.metric_registry` to print the catalogue as a
table (used to regenerate docs/architecture/financial_metrics_registry.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    label: str
    formula: str
    must_remain_distinct_from: str
    status: str  # "canonical" | "planned"
    legacy_location: str
    phase: str
    replaces: tuple[str, ...] = field(default_factory=tuple)


METRIC_REGISTRY: dict[str, MetricDefinition] = {
    d.metric_id: d
    for d in (
        MetricDefinition(
            metric_id="levy.gross_outstanding.v1",
            label="Gross outstanding",
            formula="Sum of positive per-lot ledger balances as of date",
            must_remain_distinct_from="Post-grace arrears",
            status="planned",
            legacy_location="backend/routers/finance.py (unit_ledger_summary aggregation, ~line 1127)",
            phase="Phase 1 (extraction pending — depends on a per-unit-facts adapter, see plan)",
        ),
        MetricDefinition(
            metric_id="levy.unit_arrears_and_credit.v1",
            label="Unit arrears / credit",
            formula="Per unit: if net_balance <= 0, arrears=0 and credit=-net_balance (never "
            "netted against any other unit); else arrears=max(0, net_balance - "
            "in_grace_portion) where in_grace_portion excludes the still-not-yet-overdue "
            "slice of the currently-charged period. net_balance already rolls prior-period "
            "debt into the current period (levy periods are charged incrementally as they "
            "become due), so there is no separate prior-year-carry-forward bucket once a "
            "period is past its own grace deadline.",
            must_remain_distinct_from="Portal-scraped arrears snapshot (reconciliation.portal_ledger_delta.v1 "
            "— comparison-only, never merged into this figure)",
            status="canonical",
            legacy_location="backend/utils/finance_helpers.py get_arrears_metrics(); consumed "
            "identically by backend/routers/finance.py get_finance_summary(), "
            "get_finance_kpi_contract(), get_levy_kpi(), get_arrears_board(), and "
            "backend/server.py get_building_kpis() — all 5 endpoints now agree by "
            "construction (live-verified against all 87 East Gate 13195 lots: 14 units, "
            "1,135,973 cents, 2026-08-03).",
            phase="2026-08-03: replaces levy.recoverable_arrears.v1 and "
            "levy.quarter_true_arrears.v1, retired below. Both of those formulas "
            "independently reconstructed a unit's obligation from opening_debt/"
            "arrears_balance plus a separately-derived period levy rather than trusting "
            "unit_levy_ledger.net_balance directly — the same defect that inflated a real "
            "UA042 figure from 96331 cents to 276800 cents in an earlier, reverted attempt, "
            "and that independently produced East Gate's live '31 units / 146949 cents' "
            "arrears bug (true count: 14 units / 1,135,973 cents). See "
            "domain/finance/formulas/arrears.py module docstring for the full incident "
            "account.",
            replaces=("levy.recoverable_arrears.v1", "levy.quarter_true_arrears.v1"),
        ),
        MetricDefinition(
            metric_id="levy.true_arrears.v1",
            label="True arrears",
            formula="Gross outstanding less unpaid obligations still within the configured grace window",
            must_remain_distinct_from="Opening debt; prior-period debt",
            status="canonical",
            legacy_location="Alias of levy.unit_arrears_and_credit.v1's arrears half, summed "
            "building-wide — see that entry for the implementation.",
            phase="2026-08-03: promoted from planned — implemented by "
            "levy.unit_arrears_and_credit.v1.",
        ),
        MetricDefinition(
            metric_id="levy.opening_debt.v1",
            label="Opening debt",
            formula="Sum of (admin_opening + sinking_opening) where greater than one cent, per unit_levy_ledger doc",
            must_remain_distinct_from="Current-year unpaid",
            status="planned",
            legacy_location="backend/utils/finance_helpers.py get_unit_ledger_stats (total_opening_arrears)",
            phase="Phase 1/2 (2026-07-21: confirmed get_unit_ledger_stats() is the one place every "
            "in-Python caller routes through, and fixed a real is_test_data filter gap in it — see "
            "GAP-FIN-016. Still 'planned', not 'canonical', by this file's own definition: the "
            "computation is a MongoDB $group/$cond aggregation, not a pure domain/finance/formulas/ "
            "function a live endpoint delegates to — there is no natural pure-function extraction here "
            "without first changing the aggregation to a post-fetch Python sum, which is a larger, "
            "separate change not done this session. Do not re-promote to canonical without actually "
            "adding that formulas/ file.) NOTE: backend/routers/finance.py's Mongo-fallback branch of "
            "get_building_fund_overview independently sums the $opening_arrears document field instead "
            "of calling this function — a second, only-partially-reconciled aggregation, not yet merged "
            "(test_metric_consistency.py::TestOpeningArrearsConsistency enforces a $10 tolerance between "
            "the two rather than identity). See GAP-FIN-016 for why merging that site needs live-data "
            "verification before it's safe.",
        ),
        MetricDefinition(
            metric_id="levy.prior_period_arrears.v1",
            label="Prior-period arrears",
            formula="Debt attributable to periods before the selected period",
            must_remain_distinct_from="Total arrears",
            status="planned",
            legacy_location="backend/routers/finance.py ~line 2227-2228 (top_true_arrears per-lot)",
            phase="Phase 4",
        ),
        MetricDefinition(
            metric_id="levy.collection.current_year.v1",
            label="Current-year collection rate",
            formula="net_collected / total_obligations * 100, where total_obligations = opening_arrears + levied",
            must_remain_distinct_from="Full-year budget ratio; historical-year rate",
            status="canonical",
            legacy_location="backend/server.py get_building_kpis, current-year branch (~line 8043-8078)",
            phase="Phase 1 (extracted — see domain/finance/formulas/collection.py)",
            replaces=("backend/server.py get_building_kpis current-year branch",),
        ),
        MetricDefinition(
            metric_id="levy.collection.historical_year.v1",
            label="Historical-year collection rate",
            formula="(1 - closing_arrears/levied) * 100, using next year's opening arrears as this year's closing",
            must_remain_distinct_from="Current-year due-to-date rate",
            status="canonical",
            legacy_location="backend/server.py get_building_kpis, historical-year branch (~line 8020-8041)",
            phase="Phase 1 (extracted — see domain/finance/formulas/collection.py)",
            replaces=("backend/server.py get_building_kpis historical-year branch",),
        ),
        MetricDefinition(
            metric_id="levy.collection.quarter.v1",
            label="Quarter collection rate",
            formula="Quarter-attributable numerator / quarter_billed_display, returned as a 0-1 "
            "fraction quantized to 4dp with Decimal half-up rounding",
            must_remain_distinct_from="Annual collection rate; levy.collection.quarter_display.v1 "
            "(different denominator and 0-100 display-percent shape — see that entry)",
            status="canonical",
            legacy_location="backend/routers/finance.py get_levy_kpi() collection_rate, "
            "net_cash_realisation_rate and prior_period_arrears_rate — all delegate to "
            "domain/finance/formulas/collection.py::quarter_collection_fraction()",
            phase="Phase 2b B2 (2026-07-21: extracted get_levy_kpi's 0-1 quarter fraction helper "
            "without changing quarter_billed_display fallback semantics; audit correction: this "
            "standardizes exact half-tie rounding to Decimal half-up rather than claiming "
            "byte-identical route-local binary round() behavior)",
        ),
        MetricDefinition(
            metric_id="levy.collection.quarter_display.v1",
            label="Quarter collection rate (display %)",
            formula="paid / levied * 100, rounded to each site's own display precision, NOT clamped "
            "to [0, 100] (an overpaid/prepaid building legitimately shows >100%)",
            must_remain_distinct_from="levy.collection.quarter.v1 (get_levy_kpi's fraction-shape rate — "
            "materially different denominator, quarter_billed_display with fallback logic layered on "
            "top; NOT the same metric despite the name similarity, see GAP-FIN-016 Phase 2b)",
            status="canonical",
            legacy_location="backend/routers/trust_phase1.py get_financial_summary (~line 1736), "
            "backend/services/bi_service.py _financial_summary_pg/_financial_summary_mongo "
            "(~line 197/247), backend/routers/external_api.py get_oc_summary (~line 762) — all 3 now "
            "delegate to domain/finance/formulas/collection.py::quarterly_collection_rate()",
            phase="Phase 2b (2026-07-21: consolidated the 3 verified-byte-identical percentage-shape "
            "sites GAP-FIN-016 scoped as safe. Also fixed a real, live bug found while doing this: "
            "external_api.py's Mongo-fallback finance section (get_oc_summary, get_oc_levies, "
            "get_finance_summary, get_budget, get_transactions, get_unit_levy_balance) queried "
            "annual_levies/unit_levy_ledger/levy_payments/levy_categories using field names "
            "(`financial_year`, flat `admin_fund_income`, `opening_balance` etc.) that do not exist "
            "on the real Mongo schema — confirmed live against East Gate (13195) documents. Blast "
            "radius, precisely checked rather than assumed: get_oc_summary/get_budget/get_transactions "
            "have NO Postgres path at all, so they were unconditionally broken for every building "
            "including East Gate (confirmed live post-fix: real, non-zero figures). "
            "get_oc_levies/get_finance_summary/get_unit_levy_balance are gated by "
            "financial_pg_reads_enabled+external_api_finance_pg_enabled — globally FALSE by default "
            "(so any OTHER building without an override hits this same broken Mongo path), but East "
            "Gate specifically has an explicit core.feature_toggle_overrides row enabling both since "
            "2026-05-11 ('Parity audit confirmed 11/11 domains match. Full PG cutover for East Gate') "
            "— found only by re-querying with East Gate's real tenant_id, not the RLS bypass id, per "
            "CLAUDE.md's own documented gotcha for this exact table. So for East Gate today these 3 "
            "endpoints already serve from the separate, already-correct Postgres path and were not "
            "user-visibly broken; this fix is still required for CLAUDE.md's 'must work for ALL "
            "buildings' rule and as a safety net if that override is ever reverted. Fixed by using "
            "finance_helpers.get_levy_fund_data()/get_latest_levy_year()/get_levy_proposed_amounts()/"
            "compute_combined_fund_totals() — the same CLAUDE.md-mandated canonical helpers, not new "
            "ad hoc field access.)",
        ),
        MetricDefinition(
            metric_id="levy.fund_collection_rate.v1",
            label="Per-fund collection rate (admin/sinking)",
            formula="paid / levied * 100, clamped [0, 100], 1dp — per admin/sinking fund",
            must_remain_distinct_from="levy.collection.quarter_display.v1 (whole-building, not "
            "per-fund); risk.fund_health.v1 (includes opening arrears in the denominator)",
            status="canonical",
            legacy_location="backend/routers/finance.py get_building_fund_overview — admin_fund."
            "collection_rate/sinking_fund.collection_rate, both the Mongo fallback (~line 426-427) "
            "and Postgres (~line 2132-2133) branches (line numbers re-verified 2026-07-21 "
            "deep-dive audit pass)",
            phase="Phase 2b Item B1 (2026-07-21: this exact formula was written out 4 times — twice "
            "per branch, once per fund — within this single function; consolidated onto "
            "cents_to_percentage(), live-verified against real East Gate 13195 admin/sinking figures, "
            "byte-identical. See GAP-FIN-016.)",
        ),
        MetricDefinition(
            metric_id="levy.collection.health_score.v1",
            label="BI levy collection health score",
            formula="paid / levied * 100, clamped [0, 100], with the raw paid/levied fraction stored "
            "separately as levy_collection_rate",
            must_remain_distinct_from="levy.collection.quarter_display.v1 (unclamped display %, can exceed "
            "100); levy.collection.quarter.v1 (get_levy_kpi quarter denominator)",
            status="canonical",
            legacy_location="backend/services/bi_etl_service.py etl_fact_building_health_snapshot — "
            "financial_score and levy_collection_rate now delegate to domain/finance/formulas/"
            "collection.py::levy_collection_health_score()/levy_collection_fraction()",
            phase="Phase 2b follow-up (2026-07-21: named the BI health-score 0-1 fraction plus clamped "
            "score shape without forcing it through quarterly_collection_rate(), preserving the composite "
            "health-score semantics GAP-FIN-016 warned about.)",
        ),
        MetricDefinition(
            metric_id="levy.status_distribution.v1",
            label="Lot payment status distribution",
            formula="Canonical lots classified paid-up/owing/credit using canonical balance/obligation rules",
            must_remain_distinct_from="Raw ledger-document counts",
            status="planned",
            legacy_location="backend/routers/finance.py get_ledger_quality canonical_status_counts",
            phase="Phase 1 (extraction pending — already canonical-unit-based, registering only)",
        ),
        MetricDefinition(
            metric_id="budget.variance.v1",
            label="Budget variance",
            formula="Actual recognised amount minus approved budget for period/category",
            must_remain_distinct_from="Collection performance",
            status="planned",
            legacy_location="backend/routers/finance.py get_budget_vs_actual",
            phase="Phase 4",
        ),
        MetricDefinition(
            metric_id="portal.arrears_snapshot.v1",
            label="Portal arrears snapshot",
            formula="Last external portal amount",
            must_remain_distinct_from="Ledger arrears",
            status="planned",
            legacy_location="backend/routers/finance.py portal_summary (building_summaries collection)",
            phase="Phase 2 (already presented as comparison-only in CollectionRatePage.jsx)",
        ),
        MetricDefinition(
            metric_id="reconciliation.portal_ledger_delta.v1",
            label="Portal/ledger delta",
            formula="Portal snapshot minus matching ledger metric",
            must_remain_distinct_from="A KPI used to reduce ledger balance — comparison only, never merged",
            status="planned",
            legacy_location="backend/routers/finance.py portal_cross_check block (~line 1576-1619)",
            phase="Phase 2 (already implemented — registering only)",
        ),
        MetricDefinition(
            metric_id="risk.building_stress.v1",
            label="Building stress",
            formula="Composite risk score consuming canonical inputs (5 weighted components)",
            must_remain_distinct_from="Building health",
            status="planned",
            legacy_location="backend/services/building_stress_service.py compute_phase2_stress_score",
            phase="Phase 3",
        ),
        MetricDefinition(
            metric_id="risk.fund_health.v1",
            label="Fund health",
            formula="Current-year collection health: max(0, opening_arrears + levied - outstanding) / "
            "(opening_arrears + levied) * 100, clamped [0, 100], 1dp in the building overview",
            must_remain_distinct_from="levy.fund_collection_rate.v1 (paid/levied per-fund); "
            "levy.collection.current_year.v1 (same pure formula, different metric consumer/label)",
            status="canonical",
            legacy_location="backend/routers/finance.py get_building_fund_overview fund_health — delegates "
            "to domain/finance/formulas/collection.py::current_year_collection_rate(digits=1) in both "
            "Mongo fallback and Postgres branches. The older registry text incorrectly described "
            "get_levy_kpi()'s admin_fund_health/sinking_cash_coverage balance-vs-quarterly-budget "
            "fields; corrected 2026-07-21 while closing GAP-FIN-016 registry drift.",
            phase="Phase 2b Item C (2026-07-21: formula structure consolidated through "
            "current_year_collection_rate(); Postgres total_opening_arrears remains an explicit data gap "
            "rather than a fabricated Mongo overlay — see GAP-FIN-016)",
        ),
        MetricDefinition(
            metric_id="investor.true_cost.v1",
            label="True cost of ownership",
            formula="Levy, shock and ownership-cost model",
            must_remain_distinct_from="Annual levy alone",
            status="planned",
            legacy_location="backend/services/true_cost_service.py compute_true_cost_of_ownership",
            phase="Phase 4 (PR #500 already fixed the dollars/cents and year-field defects in the legacy implementation)",
        ),
    )
}


def render_table() -> str:
    """Render the registry as a markdown table (used to regenerate the docs copy)."""
    header = "| Metric ID | Label | Status | Formula | Must remain distinct from | Legacy location | Phase |\n"
    header += "|---|---|---|---|---|---|---|\n"
    rows = []
    for d in METRIC_REGISTRY.values():
        rows.append(
            f"| `{d.metric_id}` | {d.label} | {d.status} | {d.formula} | "
            f"{d.must_remain_distinct_from} | {d.legacy_location} | {d.phase} |"
        )
    return header + "\n".join(rows) + "\n"


if __name__ == "__main__":
    print(render_table())
