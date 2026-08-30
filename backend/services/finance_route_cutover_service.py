# @featuretrace:finance-postgres-read-cutover — Route-level finance read-source resolution.
# Layer: service
# Data flow: finance router → get_finance_route_runtime_state() → cutover_status_service + finance_shadow_read_service
#            + cutover_config_service → route-level source selection (building-scoped).
# Related: backend/routers/finance.py
#          backend/services/finance_shadow_read_service.py
#          backend/services/cutover_status_service.py
#          backend/routers/cutover_admin.py
# Toggle: financial_pg_reads_enabled
"""Route-level finance read cutover controls.

Prompt 6 constraints:
  - Promotion is building-scoped and route-scoped.
  - Only read-only routes are eligible for postgres_read.
  - Domain mode alone is not sufficient; route readiness must pass.
  - Write routes must remain Mongo-primary in this phase.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from models.cutover_status import CutoverMode
from services.cutover_config_service import (
    FINANCIAL_PG_READS_ENABLED,
    is_cutover_feature_enabled,
)
from services.cutover_status_service import get_or_default_cutover_status
from services.domain_source_guard import DomainSourceAuditContext, require_domain_source
from services.finance_shadow_read_service import get_route_shadow_readiness


@dataclass(frozen=True)
class FinanceRoutePolicy:
    route_key: str
    method: str
    path: str
    read_only: bool
    shadow_supported: bool
    postgres_read_supported: bool
    frontend_consumer: str
    notes: str


_ROUTE_POLICIES: tuple[FinanceRoutePolicy, ...] = (
    FinanceRoutePolicy(
        route_key="finance.summary",
        method="GET",
        path="/finance/summary",
        read_only=True,
        shadow_supported=True,
        # RE-ENABLED 2026-08-09: this is a PARTIAL override, not a full swap (like
        # arrears_detail, unlike building_overview) -- routers/finance.py::get_finance_summary
        # now sources unit_ledger_summary.total_levied/total_paid (get_oc_levy_summary),
        # admin/sinking_fund.actual_expenses/total_expenses (new
        # get_fund_expense_totals), grace-aware arrears/in_grace_amount (the same
        # get_arrears_summary(grace_aware=True) already verified for arrears_detail),
        # and ledger_quality/canonical_status_counts (new get_canonical_ledger_quality)
        # from Postgres. budgeted_expenses, portal_summary, collected_summary, and the
        # per-quarter period_status/in_grace_summary LABEL lists stay Mongo-sourced
        # permanently -- budgeted_expenses because PG's only fund-budget concept is
        # "what was levied", a different concept from Mongo's staff-entered planning
        # figure; the rest because they have no Postgres equivalent or (period labels)
        # live PG levy_runs data is annual-roll-up-only, not per-quarter.
        postgres_read_supported=True,
        frontend_consumer="Finance dashboard summary cards",
        notes="Partial override (see routers/finance.py::get_finance_summary comments); "
              "gated on live shadow-soak readiness now, not this flag.",
    ),
    FinanceRoutePolicy(
        route_key="finance.building_overview",
        method="GET",
        path="/finance/building-overview",
        read_only=True,
        shadow_supported=True,
        postgres_read_supported=True,
        frontend_consumer="Owner dashboard + cash position card",
        notes="Promotable when route readiness passes.",
    ),
    FinanceRoutePolicy(
        route_key="finance.unit_dashboard_overview",
        method="GET",
        path="/finance/unit-dashboard-overview/{unit_number}",
        read_only=True,
        shadow_supported=True,
        postgres_read_supported=True,
        frontend_consumer="Owner unit dashboard",
        notes="Promotable when route readiness passes.",
    ),
    FinanceRoutePolicy(
        route_key="finance.levy_kpi",
        method="GET",
        path="/finance/levy-kpi",
        read_only=True,
        shadow_supported=True,
        # RE-ENABLED 2026-08-09: both preconditions from the 2026-08-07 revert are now
        # met. (1) _compare_levy_kpi_payloads() is a real comparator (0d566ae, GAP-FIN-058
        # B1) comparing quarter_billed_total_display <-> levy_budgeted_cents, no longer an
        # unconditional `return []`. (2) GAP-FIN-057 (orphaned receipt_allocations never
        # un-allocated on reversal) is fixed and verified live for building 13195 — all 14
        # affected units confirmed reconciled, 057 dry-run returns zero remaining stale
        # allocations building-wide. A live manual check the same day found a genuine
        # 2.0x divergence (quarter_billed_total_display mongo=$110,093.78 vs
        # pg=$220,187.56) -- ROOT-CAUSED AND FIXED same day: the old PG source
        # (get_oc_levy_summary's total_budgeted) summed EVERY levy_run raised this
        # financial year (YTD-cumulative), while Mongo's quarter_billed_total_display is
        # a flat single-quarter target rate that never grows -- a genuine concept
        # mismatch, not a data bug. East Gate had exactly 1 quarter raised in PG when
        # first checked (2026-07-12, coincidental 1:1 match) and 2 by the time of the
        # 2026-08-09 recheck, producing an exact, deterministic 2.0x. New
        # get_current_quarter_levy_total() (financial_read_service.py) sources only the
        # most-recently-issued levy_run's total instead -- confirmed live post-fix:
        # $110,093.78 both sides, exact match.
        postgres_read_supported=True,
        frontend_consumer="Collection rate / fund-health KPI views",
        notes="Comparator + GAP-FIN-057 both fixed (2026-08-09); quarter_billed_cents "
              "concept-mismatch root-caused and fixed same day (see comment above); "
              "gated on live shadow-soak readiness now, not this flag.",
    ),
    FinanceRoutePolicy(
        route_key="finance.arrears_detail",
        method="GET",
        path="/arrears/detail",
        read_only=True,
        shadow_supported=True,
        # RE-ENABLED 2026-08-09: both preconditions from the 2026-08-07 revert are now
        # met. (1) _compare_arrears_payloads() is a real comparator (0d566ae, GAP-FIN-058
        # B1) — _get_pg_payload_for_route now sources the PG side from
        # get_arrears_summary(grace_aware=True) (levy_items.grace_deadline_date <
        # CURRENT_DATE, Alembic 0077), the same due-date-aware concept Mongo's total_arrears
        # computes, resolving the orders-of-magnitude concept mismatch at the source rather
        # than masking it with a no-op. (2) GAP-FIN-057 is fixed and verified live for
        # building 13195 (see finance.levy_kpi's note above for detail). The comparator's
        # own docstring says "re-check after 057 lands" — it has landed.
        # postgres_read_supported=True only re-enables the LIVE shadow-soak evaluation
        # (get_route_shadow_readiness); promotion still requires 7 consecutive clean days
        # from that evaluation, not asserted here.
        postgres_read_supported=True,
        frontend_consumer="Arrears recovery board",
        notes="Comparator (grace_aware=True, GAP-FIN-058 B1) + GAP-FIN-057 both fixed "
              "(2026-08-09); gated on live shadow-soak readiness now, not this flag.",
    ),
    FinanceRoutePolicy(
        route_key="finance.quarterly_budget",
        method="GET",
        path="/finance/quarterly-budget",
        read_only=True,
        # No Postgres query or shadow comparator exists for this route yet -- both
        # must be built before shadow_supported/postgres_read_supported can flip to
        # True. finance.levy_runs/levy_items already has the data this route needs
        # (a levy_run row's mere existence for a (scheme_id, financial_year,
        # quarter_no) IS the "this quarter has been raised" signal -- see
        # FinancialReadService.get_oc_levy_summary(), financial_read_service.py
        # lines 327-463, which already returns quarter_no/issue_date/due_date/status
        # per levy_runs row). Until that's wired up, this route is honestly
        # Mongo-only -- registering it here (rather than leaving it undispatched)
        # is what makes it visible in the cutover readiness table for future
        # promotion, instead of silently never appearing at all.
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="Finance Overview per-quarter budget/actual cards",
        notes="No Postgres query or shadow comparator built yet; remains Mongo-only.",
    ),
    FinanceRoutePolicy(
        route_key="finance.unit_levy_ledger",
        method="GET",
        path="/unit-levy-ledger",
        read_only=True,
        shadow_supported=True,
        # GAP-FIN-030 Fix 4 (2026-08-02): postgres_read_supported deliberately False --
        # FinancialReadService.get_unit_levy_balance_list() exists and is wired for
        # shadow comparison only. Do NOT flip this to True without first confirming
        # GAP-FIN-031 (FY2026 bank-transaction-to-receipt matching) is complete --
        # promoting before that is done would repeat the exact wrong-balance near-miss
        # this same session found and rolled back for finance.unit_dashboard_overview.
        postgres_read_supported=False,
        frontend_consumer="Finance Overview Levy Status tab",
        notes="Shadow-only; do not promote until GAP-FIN-031 (FY2026 receipt matching) is verified complete.",
    ),
    FinanceRoutePolicy(
        route_key="finance.transactions",
        method="GET",
        path="/expense-transactions,/income-transactions",
        read_only=True,
        shadow_supported=True,
        # See finance.unit_levy_ledger's note above -- same GAP-FIN-031 dependency.
        postgres_read_supported=False,
        frontend_consumer="Finance Overview Transactions tab",
        notes="Shadow-only; do not promote until GAP-FIN-031 (FY2026 receipt matching) is verified complete.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # GAP-FIN-052 (2026-08-05): the entries below extend coverage from the
    # original 8 routes to every other finance-adjacent route identified in
    # docs/finances/financial-data-consolidation-map.md and
    # tasks/GAP-FIN-048-postgres-primary-full-consolidation-master-plan.md.
    # Every one of these is registered ONLY so it becomes visible in the
    # cutover readiness table (get_finance_route_readiness_table /
    # has_any_promotable_finance_route) -- exactly the same rationale already
    # documented on finance.quarterly_budget above. None of these routers
    # call get_finance_route_runtime_state() yet, so this is purely additive:
    # zero change to runtime behaviour for any existing page. Wiring each
    # router to actually consult its policy (and, later, flipping
    # shadow_supported/postgres_read_supported once a PG query + comparator
    # exist) is tracked per-cluster in GAP-FIN-049/050/051 and GAP-FIN-044.
    # ─────────────────────────────────────────────────────────────────────
    FinanceRoutePolicy(
        route_key="trust.v2_accounts",
        method="GET",
        path="/trust/v2/accounts",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="TrustBankAccountsPage / TrustAccountingPage",
        notes="Tracked in GAP-FIN-049; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="trust.v1_accounts",
        method="GET",
        path="/trust/accounts",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="TrustAccountingPage (legacy V1 API)",
        notes="Legacy V1 trust API, non-interoperable with /trust/v2/*; GAP-FIN-049 decides its disposition.",
    ),
    FinanceRoutePolicy(
        route_key="trust.reconciliation_runs",
        method="GET",
        path="/trust/reconciliation/runs",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="TrustReconciliationPage",
        notes="Tracked in GAP-FIN-049; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="trust.period_locks",
        method="GET",
        path="/trust/period-locks",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="TrustReconciliationPage",
        notes="Tracked in GAP-FIN-049; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="financial.matching_queue",
        method="GET",
        path="/financial/matching/queue",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="MatchingReviewPage",
        notes="Tracked in GAP-FIN-049; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="financial.ap_invoices",
        method="GET",
        path="/ap/invoices",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="APApprovalQueuePage",
        notes="Tracked in GAP-FIN-049; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="intelligence.levy_fairness",
        method="GET",
        path="/intelligence/levy-fairness",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="LevyFairnessPage",
        notes=(
            "GAP-FIN-052 item 2 (2026-08-06): intelligence.py's get_levy_fairness_v2 now "
            "consults get_finance_route_runtime_state() for this route_key, replacing its "
            "former inline, ungoverned try-Postgres/fall-back pattern (which gated only on "
            "financial_pg_reads_enabled and skipped the shadow-soak/critical-diff gates). "
            "Still Mongo-primary: no PG shadow comparator exists yet, so postgres_read_supported "
            "stays False -- the governed handler resolves to MongoDB until this route is promoted."
        ),
    ),
    FinanceRoutePolicy(
        route_key="intelligence.capital_shock",
        method="GET",
        path="/intelligence/capital-shock",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="BuildingStrengthCard / CapitalForMeCard (main dashboard pages)",
        notes=(
            "GAP-FIN-052 item 2 (2026-08-06): intelligence.py's get_capital_shock now consults "
            "get_finance_route_runtime_state() for this route_key, replacing the same inline, "
            "ungoverned PG-fallback pattern get_levy_fairness_v2 carried. Still Mongo-primary: "
            "no PG shadow comparator exists yet, so postgres_read_supported stays False."
        ),
    ),
    FinanceRoutePolicy(
        route_key="intelligence.levy_stability",
        method="GET",
        path="/intelligence/levy-stability",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="LevyStabilityPage",
        notes="Tracked in GAP-FIN-044; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="intelligence.special_levy_forecast",
        method="GET",
        path="/intelligence/special-levy-forecast",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="FinanceIntelligencePage",
        notes="Tracked in GAP-FIN-044; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="levy_scenarios.list",
        method="GET",
        path="/levy-scenarios",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="LevyScenariosPage",
        notes="Tracked in GAP-FIN-048 §1b; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="finance_intelligence.forecast",
        method="GET",
        path="/finance/forecast",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="FinanceIntelligencePage",
        notes="Tracked in GAP-FIN-044; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="finance_intelligence.anomalies",
        method="GET",
        path="/finance/anomalies",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="FinanceIntelligencePage",
        notes="Tracked in GAP-FIN-044; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="finance_intelligence.health",
        method="GET",
        path="/finance/health",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="FinanceIntelligencePage / useFinanceData.ts (importer unconfirmed -- see consolidation map)",
        notes="Also backs GAP-FIN-040 B1 (get_unit_ledger_stats.units_owing bypass); tracked there and in GAP-FIN-044.",
    ),
    FinanceRoutePolicy(
        route_key="finance_intelligence.lot_summary",
        method="GET",
        path="/finance/lot-summary",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="FinanceIntelligencePage Unit Impacts",
        notes="This is GAP-FIN-040 B2 (lot_finance_service.arrears_flag bypass); tracked there.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # GAP-FIN-052 item 2 cont'd (2026-08-06): analytics.py carried the SAME
    # ungoverned inline try-Postgres/fall-back pattern intelligence.py did --
    # each of these four routes gated only on financial_pg_reads_enabled (via a
    # local is_cutover_feature_enabled import) and bypassed the shadow-soak /
    # critical-diff gates. All four now consult get_finance_route_runtime_state()
    # and serve their existing analytics_pg_service PG read model only when the
    # engine resolves the route to postgres. postgres_read_supported stays False
    # (no PG shadow comparator exists for these yet) so today they resolve to
    # MongoDB. None of these recompute arrears/collection, so they are NOT gated
    # on GAP-FIN-040 B1/B2 (that blocker applies to GAP-FIN-050's bi_service.py
    # arrears/collection canonicalization, a separate concern).
    # ─────────────────────────────────────────────────────────────────────
    FinanceRoutePolicy(
        route_key="analytics.sinking_fund_forecast",
        method="GET",
        path="/analytics/sinking-fund-forecast",
        read_only=True,
        shadow_supported=True,
        postgres_read_supported=False,
        frontend_consumer="OwnerDashboard Fund Health gauge / sinking-fund projection",
        notes="GAP-FIN-052 item 2: governed. GAP-FIN-055 shadow comparator: current_balance (cents) vs Mongo AUD via _compare_generic_payloads. Mongo-primary until a clean soak promotes it. Note: PG (journal_lines) and Mongo (sinking_fund_plan) source this differently — review diffs before promoting.",
    ),
    FinanceRoutePolicy(
        route_key="analytics.levy_benchmarks",
        method="GET",
        path="/analytics/levy-benchmarks",
        read_only=True,
        shadow_supported=True,
        postgres_read_supported=False,
        frontend_consumer="Levy trend / ACT-median benchmark charts",
        notes="GAP-FIN-052 item 2: governed. GAP-FIN-055 shadow comparator: total_levied = Σ per-year Building (cents) vs Mongo AUD. Mongo-primary until a clean soak promotes it.",
    ),
    FinanceRoutePolicy(
        route_key="analytics.expense_breakdown",
        method="GET",
        path="/analytics/expense-breakdown",
        read_only=True,
        shadow_supported=True,
        postgres_read_supported=False,
        frontend_consumer="Management dashboard expense-by-category breakdown",
        notes="GAP-FIN-052 item 2: governed. GAP-FIN-055 shadow comparator: total_expense = Σ category amount (cents) vs Mongo AUD. Mongo-primary until a clean soak promotes it. Note: PG (GL journal_lines) and Mongo (financial_transactions) source this differently — review diffs before promoting.",
    ),
    FinanceRoutePolicy(
        route_key="analytics.budget_variance",
        method="GET",
        path="/analytics/budget-variance",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="Budget vs Actual variance analysis",
        notes="GAP-FIN-052 item 2: governed via get_finance_route_runtime_state(); PG via analytics_pg_service.get_budget_variance_pg. Mongo-primary until promoted.",
    ),
    FinanceRoutePolicy(
        route_key="analytics.levy_allocation_breakdown",
        method="GET",
        path="/analytics/levy-allocation-breakdown",
        read_only=True,
        shadow_supported=True,
        postgres_read_supported=False,
        frontend_consumer="Owner Dashboard v2 'Where your levy goes' donut",
        notes="GAP-FIN-052 follow-up: governed via get_finance_route_runtime_state(). Shadow comparator ships admin/sinking/total fund totals (finance.levy_items, cents) vs the Mongo AUD figures via _compare_generic_payloads; PG payload from analytics_pg_service.get_levy_allocation_totals_pg. Mongo-primary until shadow_pass promotes it.",
    ),
    FinanceRoutePolicy(
        route_key="savings.list",
        method="GET",
        path="/savings",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="SavingsLedgerPage",
        notes="Tracked in GAP-FIN-048 §1b; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="building_stress.snapshot",
        method="GET",
        path="/building-stress/snapshot",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="BuildingFinancialStressPage",
        notes="Suspected GAP-FIN-040-style bypass (re-derives arrears/collection independently); tracked in GAP-FIN-048 §1d.",
    ),
    FinanceRoutePolicy(
        route_key="investor.building_health_report",
        method="GET",
        path="/intelligence/investor/building-health-report",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="InvestorDashboardPage",
        notes="Tracked in GAP-FIN-050; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="portfolio.dashboard",
        method="GET",
        path="/portfolio/dashboard",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="PortfolioDashboardPage",
        notes=(
            "Cross-building rollup; backing service (portfolio.py) is the sole remaining "
            "allowlist entry in test_finance_calculation_singularity_guardrail.py (GAP-FIN-040 "
            "FU-1 / B13). Tracked in GAP-FIN-050."
        ),
    ),
    FinanceRoutePolicy(
        route_key="bi.building_financial_summary",
        method="GET",
        path="/bi/building/{building_id}/financial-summary",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="BIAnalyticsPage and variants",
        notes=(
            "Postgres-native ETL (bi_etl_service.py) by design, but outside this policy engine "
            "and carrying the B5-B9 canonical-formula bypasses (GAP-FIN-040 §3). Tracked in GAP-FIN-050."
        ),
    ),
    FinanceRoutePolicy(
        route_key="owner_hub.properties",
        method="GET",
        path="/owner-hub/properties",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="OwnerHubPage / TrueOwnershipCostPage / PropertiesPage",
        notes="Tracked in GAP-FIN-051; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="owner_hub.property_ledger",
        method="GET",
        path="/owner-hub/properties/{property_id}/ledger",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="TenancyLedgerPage",
        notes="Tracked in GAP-FIN-051; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="council_rates.get",
        method="GET",
        path="/council-rates/{unit_number}",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="/financials/council-rates (feeds owner.total_ownership_cost.v1)",
        notes="Tracked in GAP-FIN-051; no PG query or shadow comparator built yet.",
    ),
    FinanceRoutePolicy(
        route_key="water_bills.get",
        method="GET",
        path="/water-bills/{unit_number}",
        read_only=True,
        shadow_supported=False,
        postgres_read_supported=False,
        frontend_consumer="/financials/water-bills (feeds owner.total_ownership_cost.v1)",
        notes="Tracked in GAP-FIN-051; no PG query or shadow comparator built yet.",
    ),
)

_POLICY_BY_KEY = {p.route_key: p for p in _ROUTE_POLICIES}
_PROMOTABLE_ROUTE_KEYS = frozenset(
    p.route_key for p in _ROUTE_POLICIES if p.postgres_read_supported and p.read_only
)

# GAP-FIN-058 (2026-08-07) — substantive-comparator gate.
#
# `finance.levy_kpi` and `finance.arrears_detail` were promoted (postgres_read_supported=True)
# on a "shadow_pass 0/0 = parity proof" claim that turned out to be false: their shadow
# comparators (`_compare_levy_kpi_payloads` / `_compare_arrears_payloads` in
# finance_shadow_read_service.py) unconditionally `return []`. A comparator that never compares
# anything always reports 0 diffs — that is guaranteed by construction, not evidence PG and Mongo
# agree. The static `postgres_read_supported` flag alone cannot prevent this: it's set by the same
# person/session making the same mistake, with nothing in the runtime state to catch it.
#
# This is an explicit, manually-curated allowlist of route_keys whose comparator has been verified
# — by a human/session actually reading the comparator body and confirming it substantively
# compares the field(s) that matter for that route's correctness, cross-checked against a
# known-correct reference figure — NOT a route that merely happens to show shadow_pass. A route
# NOT on this list is refused promotion regardless of its `postgres_read_supported` flag or shadow
# status; see the gate in get_finance_route_runtime_state() below.
#
# Verification does not require 100% field coverage — it requires that the field(s) checked are
# the ones where a real divergence would actually matter for that route, and that this was
# confirmed by reading the comparator's code, not inferred from a passing diff count. Add a route
# here only after doing that reading yourself; cite what you checked in the comment.
#
# - finance.unit_dashboard_overview: comparator (`_get_pg_payload_for_route`'s
#   unit_dashboard_overview branch, via get_unit_levy_balance()) checks `closing_balance` — the
#   journal_lines-derived AR balance, which reverse_entry() correctly reverses (verified 2026-08-07
#   against the GAP-FIN-057 incident's reversal journal entries). `paid_amount`/per-quarter status
#   is NOT covered by the comparator and IS sourced from the same finance.levy_items.paid_cents
#   GAP-FIN-057 affects — but separately verified 2026-08-07 that 0/87 East Gate units have their
#   non-reversed ("legitimate") receipts falling short of their levied amount, so removing
#   GAP-FIN-057's un-reversed contribution would not flip any unit's aggregate paid status today.
#   This is a live risk assessment, not a structural guarantee — re-verify if GAP-FIN-057's
#   underlying data changes, and prefer fixing GAP-FIN-057 over relying on this caveat long-term.
_SUBSTANTIVE_COMPARATOR_ROUTE_KEYS = frozenset({
    "finance.unit_dashboard_overview",
    # Added 2026-08-09: _compare_levy_kpi_payloads() and _compare_arrears_payloads()
    # (0d566ae, GAP-FIN-058 B1) are real same-concept comparators, not the old
    # unconditional `return []` — see the notes on each route's FinanceRoutePolicy
    # above. This allowlist entry was the missing half of that fix: the comparator
    # code shipped but this gate was never updated, so postgres_read_supported=True
    # alone still could not have made either route eligible.
    "finance.levy_kpi",
    "finance.arrears_detail",
    # Added 2026-08-09: _compare_summary_payloads() extended from a single
    # total_levied-only check to a real multi-field comparator (total_levied,
    # in_grace_summary.true_arrears_amount via the same grace-aware call already
    # verified for arrears_detail, admin/sinking_fund.actual_expenses via new
    # get_fund_expense_totals, and units_owing/paid_up/credit via new
    # get_canonical_ledger_quality) -- see finance_shadow_read_service.py for what
    # each field checks and why budgeted_expenses stays excluded. routers/finance.py's
    # get_finance_summary is a PARTIAL override (see its own route_state comment) --
    # this allowlist entry only vouches for the fields actually gated on
    # route_state["source"], not the whole response.
    "finance.summary",
    # Added 2026-08-18 (GAP-FIN-064): postgres_read_supported=True was set on this
    # route's policy but it was never added here when GAP-FIN-058's allowlist
    # hardening shipped 2026-08-09 -- an oversight, not a deliberate exclusion.
    # _compare_building_overview_payloads() is a real, substantive comparator at
    # the same bar as the three entries above: field-by-field (total_levied via
    # levy_budgeted_cents, total_paid via levy_collected_cents), with a documented,
    # reasoned exclusion of total_outstanding (a genuinely different concept on
    # each side -- cumulative AR closing balance vs this-year-only net_balance --
    # not a stub or an unconditional pass). The total_paid comparison already cites
    # and was fixed against a real live incident (GAP-FIN-056, the total_paid_this_year
    # vs cumulative-portal-back-solve divergence). Its handler (get_building_fund_overview,
    # routers/finance.py) has a complete, working PG read branch -- this was purely a
    # governance-signal gap, not a missing-code one (see GAP-FIN-063 for the contrasting
    # case where the handler itself has no PG branch at all).
    "finance.building_overview",
})


def list_finance_route_policies() -> list[FinanceRoutePolicy]:
    """Return the static route policy inventory for finance read cutover."""
    return list(_ROUTE_POLICIES)


def get_finance_route_policy(route_key: str) -> FinanceRoutePolicy | None:
    """Generated function header.

    Function: get_finance_route_policy
    Path: backend/services/finance_route_cutover_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _POLICY_BY_KEY.get(route_key)


# Per-request memo for resolved route state.
#
# Resolving one route costs ~50 ms: two `require_domain_source()` calls (each of
# which persists a hash-chained authorisation-audit row), a cutover-status read, a
# shadow-readiness read and a feature-toggle read — all against the control plane.
# A dashboard fan-out asks the SAME question repeatedly: `_governed_read_source()`
# and `_fire_analytics_shadow()` in routers/analytics.py both call this for the same
# (building_id, route_key) inside a single HTTP request, so every governed endpoint
# was paying that ~50 ms twice and writing two identical audit rows per decision.
#
# The control plane cannot change midway through one request, so the answer is
# memoised for the life of the request. Each Starlette request runs in its own
# task, which copies this ContextVar (default None) — a `.set()` here is visible
# only within that request and never leaks to another, so there is no cross-tenant
# or cross-request bleed. The audit trail keeps one row per decision per request,
# which is the meaningful granularity; it is the duplicates that are dropped.
_runtime_state_memo: ContextVar[dict[tuple[str, str], dict[str, Any]] | None] = ContextVar(
    "finance_route_runtime_state_memo", default=None
)


def reset_finance_route_runtime_state_cache() -> None:
    """Drop the per-request memo.

    Long-lived non-HTTP callers (cron loops, workers, tests) that deliberately want
    to observe a control-plane change mid-process should call this between cycles.
    """
    _runtime_state_memo.set(None)


async def get_finance_route_runtime_state(
    *,
    building_id: str,
    route_key: str,
) -> dict[str, Any]:
    """Resolve the runtime read source for a finance route.

    Returns:
      source: "mongo" | "postgres"
      run_shadow: bool
      eligible_for_postgres_read: bool
      blocked_reason: str | None
      domain_mode: str
      route_readiness: dict

    Memoised per request — see ``_runtime_state_memo``.
    """
    memo = _runtime_state_memo.get()
    if memo is None:
        memo = {}
        _runtime_state_memo.set(memo)
    memo_key = (building_id, route_key)
    cached = memo.get(memo_key)
    if cached is not None:
        return cached

    state = await _resolve_finance_route_runtime_state(
        building_id=building_id, route_key=route_key
    )
    memo[memo_key] = state
    return state


async def _resolve_finance_route_runtime_state(
    *,
    building_id: str,
    route_key: str,
) -> dict[str, Any]:
    """Uncached resolution — see :func:`get_finance_route_runtime_state`."""
    policy = get_finance_route_policy(route_key)
    if policy is None:
        return {
            "route_key": route_key,
            "source": "mongo",
            "run_shadow": False,
            "eligible_for_postgres_read": False,
            "blocked_reason": "Unknown route policy",
            "domain_mode": "mongo_primary",
            "route_readiness": {"status": "not_started"},
            "policy": None,
        }

    status = await get_or_default_cutover_status(building_id, "finance_ledger")
    audit_context = DomainSourceAuditContext(
        route=policy.path,
        source_service="finance_route_cutover_service",
        feature_toggle_key=FINANCIAL_PG_READS_ENABLED,
        metadata={"route_key": route_key},
    )
    domain_decision = await require_domain_source(
        domain="finance_ledger",
        building_id=building_id,
        operation="read",
        requested_source="postgres",
        audit_context=audit_context,
    )
    shadow_decision = await require_domain_source(
        domain="finance_ledger",
        building_id=building_id,
        operation="shadow_read",
        audit_context=audit_context,
    )
    domain_mode = status.mode
    route_readiness = await get_route_shadow_readiness(
        building_id=building_id,
        route_key=route_key,
    )
    pg_reads_enabled = await is_cutover_feature_enabled(
        building_id, FINANCIAL_PG_READS_ENABLED
    )
    # NOTE 2026-08-09: this used to also resolve FINANCIAL_PG_READS_BYPASS_SHADOW and
    # extract readiness_status/diff_count/critical_count/shadow_has_run from
    # route_readiness to gate eligibility on PG-vs-Mongo shadow-comparison agreement.
    # That gate is removed below (see the comment on the `else:` branch) -- Mongo
    # disagreeing with PG is no longer treated as evidence PG is wrong. route_readiness
    # itself is still fetched and returned for observability.

    blocked_reason: str | None = None
    eligible = False
    source = "mongo"
    run_shadow = policy.shadow_supported and shadow_decision.shadow_enabled
    shadow_waiver_applied = False

    if domain_decision.blocked_reason:
        blocked_reason = domain_decision.blocked_reason
    elif not policy.read_only:
        blocked_reason = "Route is not read-only"
    elif not policy.postgres_read_supported:
        blocked_reason = policy.notes
    elif route_key not in _SUBSTANTIVE_COMPARATOR_ROUTE_KEYS:
        # GAP-FIN-058 gate: a route needs an actual, non-no-op comparator implemented
        # before it can be trusted at all -- this is an implementation-readiness check
        # ("does real code exist for this route"), not a Mongo-agreement check, so it
        # stays even after the 2026-08-09 removal of shadow-agreement gating below.
        blocked_reason = (
            "Comparator not on the verified-substantive allowlist (GAP-FIN-058) "
            "— no real Postgres implementation verified for this route yet"
        )
    elif domain_mode not in {
        CutoverMode.postgres_read,
        CutoverMode.postgres_write,
        CutoverMode.mongo_archive,
    }:
        blocked_reason = f"Domain mode is {domain_mode.value}; route remains Mongo-primary"
    elif not pg_reads_enabled:
        blocked_reason = f"Feature toggle {FINANCIAL_PG_READS_ENABLED} is disabled"
    elif not domain_decision.postgres_allowed:
        blocked_reason = domain_decision.blocked_reason or "Domain source state does not allow postgres read"
    else:
        # REMOVED 2026-08-09 (explicit direction): this used to require critical_count
        # == 0 and readiness_status == "shadow_pass" before serving PG -- i.e. it
        # treated "PostgreSQL disagrees with MongoDB" as evidence PostgreSQL was wrong,
        # and blocked promotion on that basis alone, with no way to distinguish a real
        # PG defect from Mongo simply being stale. That assumption is backwards for
        # this migration: PostgreSQL is the system of record (CLAUDE.md), Mongo is the
        # store being retired, and this session repeatedly proved PG correct against
        # independent evidence (the live portal balance, exact to the cent on multiple
        # units) while Mongo held pre-correction figures nobody was updating. A route
        # reaching this branch has already passed every gate that actually reflects
        # implementation readiness: read-only, postgres_read_supported, a real
        # (non-no-op) comparator on the verified-substantive allowlist, domain mode,
        # the feature toggle, and the domain-source guard. That is what "ready" means
        # now -- not "does the store being retired happen to currently agree."
        # route_readiness / shadow_diffs are still computed and returned below for
        # observability (a genuinely new, unexplained PG defect should still be
        # investigated via core.shadow_diffs directly) -- they just no longer gate
        # eligibility. run_shadow stops once a route serves PG, same as every other
        # promoted route (there is nothing left to shadow once PG is primary).
        eligible = True
        source = "postgres"
        run_shadow = False

    return {
        "route_key": route_key,
        "source": source,
        "run_shadow": run_shadow,
        "eligible_for_postgres_read": eligible,
        "shadow_waiver_applied": shadow_waiver_applied,
        "blocked_reason": blocked_reason,
        "domain_mode": domain_mode.value,
        "route_readiness": route_readiness,
        "policy": {
            "method": policy.method,
            "path": policy.path,
            "read_only": policy.read_only,
            "shadow_supported": policy.shadow_supported,
            "postgres_read_supported": policy.postgres_read_supported,
            "frontend_consumer": policy.frontend_consumer,
            "notes": policy.notes,
        },
    }


async def get_finance_route_readiness_table(
    *,
    building_id: str,
) -> list[dict[str, Any]]:
    """Return route-level readiness rows for the cutover admin UI."""
    rows: list[dict[str, Any]] = []
    for policy in _ROUTE_POLICIES:
        state = await get_finance_route_runtime_state(
            building_id=building_id,
            route_key=policy.route_key,
        )
        rr = state["route_readiness"]
        # shadow_monitoring_active == state["run_shadow"]: once a route serves postgres,
        # run_shadow is permanently False for it (see the eligible branch in
        # get_finance_route_runtime_state -- "there is nothing left to shadow once PG is
        # primary"), so readiness_status/diff_count/critical_count/last_compared_at stop
        # being refreshed and are a FROZEN snapshot from whenever the last comparison ran
        # before promotion -- which can show a stale, already-fixed divergence (see
        # finance.levy_kpi's 2026-08-09 "PG=2.0x Mongo" root-cause writeup: the persisted
        # diff record kept showing the pre-fix bug for hours after the fix shipped, purely
        # because nothing re-ran the comparison). Surfacing this explicitly lets
        # /admin/cutover distinguish "actively monitored, currently failing" from
        # "promoted, last known state as of last_compared_at, no longer monitored" instead
        # of rendering both identically.
        rows.append(
            {
                "route_key": policy.route_key,
                "method": policy.method,
                "path": policy.path,
                "frontend_consumer": policy.frontend_consumer,
                "read_only": policy.read_only,
                "shadow_supported": policy.shadow_supported,
                "postgres_read_supported": policy.postgres_read_supported,
                "current_source": state["source"],
                "domain_mode": state["domain_mode"],
                "readiness_status": rr.get("status", "not_started"),
                "diff_count": int(rr.get("diff_count") or 0),
                "critical_count": int(rr.get("critical_count") or 0),
                "last_compared_at": rr.get("last_compared_at"),
                "shadow_monitoring_active": bool(state.get("run_shadow")),
                "eligible_for_postgres_read": bool(state["eligible_for_postgres_read"]),
                "shadow_waiver_applied": bool(state.get("shadow_waiver_applied")),
                "blocked_reason": state["blocked_reason"],
                "notes": policy.notes,
            }
        )
    return rows


async def has_any_promotable_finance_route(
    *,
    building_id: str,
) -> tuple[bool, list[dict[str, Any]]]:
    """Return whether at least one finance route is eligible for postgres_read."""
    rows = await get_finance_route_readiness_table(building_id=building_id)
    for row in rows:
        if row["route_key"] in _PROMOTABLE_ROUTE_KEYS and row["eligible_for_postgres_read"]:
            return True, rows
    return False, rows

