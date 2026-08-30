"""Cached registry of common financial UI labels and calculations."""

# @featuretrace:financial-ui-calculation-register — Backend cacheable contract for shared financial labels and formulas.
# Data flow: GET /finance/calculation-registry -> get_finance_calculation_registry -> in-memory registry TTL cache (global definitions, building-scoped metadata).
# Related: backend/routers/finance.py, frontend/src/lib/finance/levyDisplay.ts,
#          docs/features/StrataOS_Financial_UI_Label_and_Calculation_Register.md
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from typing import Any


REGISTRY_VERSION = "2026-07-15.v1"
CACHE_TTL_SECONDS = 300

_registry_cache: dict[str, Any] | None = None
_registry_cache_created_monotonic: float = 0.0
_registry_cache_created_at: str | None = None


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    Used to stamp ``_registry_cache_created_at`` whenever the in-process
    registry cache (see ``get_finance_calculation_registry``) is rebuilt, so
    API responses can report how stale the cached contract is.
    """
    return datetime.now(timezone.utc).isoformat()


def _source(mongo: list[str] | None = None, postgres: list[str] | None = None) -> dict[str, list[str]]:
    """Build a ``{"mongodb": [...], "postgres": [...]}`` source-field block.

    Every entry in ``common_label_groups`` calls this to declare exactly
    which MongoDB collection.field paths (current operational store) and
    which PostgreSQL schema.table.column paths (cutover target store) back
    a given canonical financial UI label. This is the field-level answer to
    "which table/column is this dollar amount actually coming from" for
    each label — see docs/finances/financial-data-consolidation-map.md for
    the page-level view built on top of this registry.
    """
    return {
        "mongodb": mongo or [],
        "postgres": postgres or [],
    }


def _build_registry() -> dict[str, Any]:
    """Build the immutable registry payload before cache/building metadata is added."""
    return {
        "version": REGISTRY_VERSION,
        "status": "contract_cached_values_not_materialised",
        "implementation_scope": {
            "completed": [
                "Canonical label/formula/source registry is cached in-process for 300 seconds.",
                "Registry response includes building_id metadata so callers can debug the contract beside building-scoped finance payloads.",
                "Route, service, frontend helper, backend unit test, frontend unit test, and K6 benchmark scaffold exist.",
            ],
            "not_completed": [
                "Heavy financial values are not materialised or shared from a backend value cache yet.",
                "Existing finance UI pages are not rewired to fetch this registry at runtime yet.",
                "No Redis/distributed cache invalidation has been added; current cache is per-process only.",
            ],
        },
        "verification": {
            "postgres_metadata": "Read-only information_schema check on 2026-07-15 confirmed the listed finance/core/analytics table columns.",
            "mongodb_metadata": "Read-only sampled field checks on 2026-07-15 confirmed the listed operational collections and most fields. buildings.trust_config is code-supported and documented, but was not observed in the sampled live building documents during this audit.",
            "route_metadata": "Routes were cross-checked against backend router definitions in this branch.",
        },
        "merge_path": [
            {
                "step": 1,
                "owner": "backend",
                "action": "Use /finance/calculation-registry as the canonical label/formula contract for finance UI surfaces.",
            },
            {
                "step": 2,
                "owner": "frontend",
                "action": "Replace page-local label strings with registry keys or FINANCE_DISPLAY_LABELS aliases backed by the registry.",
            },
            {
                "step": 3,
                "owner": "backend",
                "action": "Move heavy repeated calculations behind cached read models only after their source precedence matches the registry.",
            },
            {
                "step": 4,
                "owner": "quality",
                "action": "Keep K6, backend, and frontend tests aligned to registry keys whenever a label/calculation is added.",
            },
        ],
        "common_label_groups": [
            {
                "canonical_key": "levy.annual_commitment",
                "canonical_label": "Annual Levy Commitment",
                "similar_labels": ["Annual Levy", "Total Annual", "Total levy", "Strata Levies", "Annual Levy Proposed"],
                "calculation_id": "levy.annual_commitment.v1",
                "surfaces": [
                    "Finance levy calculator",
                    "Unit Finance Detail",
                    "Owner Dashboard",
                    "OwnerHub TCO",
                    "My Finances annualised quarterly levy",
                ],
                "backend_routes": [
                    "GET /levy-calculator",
                    "GET /finance/unit-dashboard-overview/{unit_number}",
                    "GET /owner-hub/unit-tco",
                    "GET /owner-finance/levy-breakdown",
                ],
                "sources": _source(
                    [
                        "annual_levies.proposed_admin_expenses",
                        "annual_levies.proposed_sinking_expenses",
                        "annual_levies.admin_fund.levy_income",
                        "annual_levies.sinking_fund.levy_income",
                        "settings.gst_registered",
                        "settings.levy_gst_rate",
                        "buildings.trust_config.*_annual_budget_cents (code-supported override; not observed in sampled live building docs on 2026-07-15)",
                        "units.entitlement",
                    ],
                    [
                        "finance.levy_items.principal_cents",
                        "finance.levy_items.gst_cents",
                        "finance.levy_items.interest_cents",
                        "finance.levy_items.recovery_costs_cents",
                        "finance.levy_runs.financial_year",
                        "finance.funds.fund_type",
                        "core.lots.entitlement_units",
                    ],
                ),
                "cache_strategy": "Stable for a levy year. Future value cache should key by building_id + levy_year + GST/trust-config revision once a materialised cache is introduced.",
                "display_guidance": "Use Levy Year, not generic FY, unless the source is an Australian July-June tax/rates calculation.",
            },
            {
                "canonical_key": "levy.ytd_levied",
                "canonical_label": "YTD Levied",
                "similar_labels": ["Total Levied", "Charges Raised", "Levied"],
                "calculation_id": "levy.ytd_levied.v1",
                "surfaces": ["Finance overview", "Levy Status", "Collection Rate", "Unit Finance Detail"],
                "backend_routes": ["GET /finance/summary", "GET /unit-levy-ledger", "GET /finance/building-overview"],
                "sources": _source(
                    ["unit_levy_ledger.total_levied", "unit_levy_ledger.admin_levied", "unit_levy_ledger.sinking_levied"],
                    [
                        "finance.levy_items.principal_cents",
                        "finance.levy_items.gst_cents",
                        "finance.levy_items.interest_cents",
                        "finance.levy_items.recovery_costs_cents",
                    ],
                ),
                "cache_strategy": "Future value cache should key by building_id + levy_year; invalidate after levy run import, payment allocation, or ledger rebuild.",
                "display_guidance": "Do not present YTD levied as the annual levy commitment.",
            },
            {
                "canonical_key": "levy.ledger_paid",
                "canonical_label": "Ledger Payments Recorded",
                "similar_labels": ["Total Paid", "Paid to Date", "Total Collected", "Amount paid", "Ledger Paid"],
                "calculation_id": "levy.ledger_paid.v1",
                "surfaces": ["Finance overview", "Collection Rate", "Owner Dashboard", "Unit Finance Detail", "My Finances"],
                "backend_routes": [
                    "GET /finance/summary",
                    "GET /finance/building-overview",
                    "GET /finance/unit-dashboard-overview/{unit_number}",
                    "GET /unit-levy-ledger",
                ],
                "sources": _source(
                    ["unit_levy_ledger.total_paid", "unit_levy_ledger.admin_paid", "unit_levy_ledger.sinking_paid"],
                    ["finance.levy_items.paid_cents", "finance.receipt_allocations.allocated_cents"],
                ),
                "cache_strategy": "Future value cache should key by building_id + levy_year; invalidate after receipt verification, DEFT/BPAY import, Demo Bank allocation, or reversal.",
                "display_guidance": "Use levy_payments only for receipt lifecycle/status, not as the authoritative collected total.",
            },
            {
                "canonical_key": "levy.outstanding_balance",
                "canonical_label": "Amount Owing",
                "similar_labels": ["Outstanding", "Arrears", "Balance Due", "Current Positive Ledger Balance"],
                "calculation_id": "levy.amount_owing.v1",
                "surfaces": ["Finance overview", "Levy Status", "Owner Dashboard", "Arrears Recovery"],
                "backend_routes": ["GET /finance/summary", "GET /finance/levy-kpi", "GET /finance/unit-dashboard-overview/{unit_number}"],
                "sources": _source(
                    ["unit_levy_ledger.net_balance", "unit_levy_ledger.admin_closing", "unit_levy_ledger.sinking_closing"],
                    ["finance.journal_lines.amount_cents", "finance.gl_accounts.account_code=1100", "finance.journal_entries.status=posted"],
                ),
                "cache_strategy": "Future value cache should key by building_id + levy_year with short TTL; invalidate on any ledger write/reversal.",
                "display_guidance": "Clamp to max(signed balance, 0). Credits must not reduce another unit's arrears unless the metric explicitly says netted.",
            },
            {
                "canonical_key": "levy.credit_balance",
                "canonical_label": "Credit Balance",
                "similar_labels": ["CR", "Credit", "Balance Credit"],
                "calculation_id": "levy.credit_balance.v1",
                "surfaces": ["Finance Levy Status", "Owner Dashboard", "Unit Finance Detail", "My Finances"],
                "backend_routes": ["GET /finance/unit-dashboard-overview/{unit_number}", "GET /unit-levy-ledger"],
                "sources": _source(
                    ["unit_levy_ledger.net_balance"],
                    ["finance.journal_lines.amount_cents", "finance.gl_accounts.account_code=1100"],
                ),
                "cache_strategy": "Future value cache should use the same boundary as Amount Owing.",
                "display_guidance": "Display abs(min(signed balance, 0)) and include CR/Credit text.",
            },
            {
                "canonical_key": "levy.collection_rate",
                "canonical_label": "Collection Rate",
                "similar_labels": ["Effective Collection", "Levy Recovery", "Paid %", "Fund Health collection component"],
                "calculation_id": "levy.collection.current_year.v1",
                "surfaces": ["Collection Rate page", "Finance KPI dialog", "Dashboard cards"],
                "backend_routes": ["GET /finance/kpi-contract", "GET /finance/levy-kpi", "GET /finance/building-overview"],
                "sources": _source(
                    ["unit_levy_ledger.opening_arrears", "unit_levy_ledger.total_levied", "unit_levy_ledger.net_balance"],
                    ["finance.levy_items.paid_cents", "finance.levy_items.*_cents"],
                ),
                "cache_strategy": "Future value cache should key calculation inputs by building_id + levy_year + rate_family; invalidate on ledger writes.",
                "display_guidance": "Always identify rate family: current-year obligations, quarter KPI, historical, or portal snapshot.",
            },
            {
                "canonical_key": "fund.cash_position",
                "canonical_label": "Fund Cash Position",
                "similar_labels": ["Admin Fund balance", "Sinking Fund balance", "Cash Position", "Current Balance"],
                "calculation_id": "fund.cash_position.v1",
                "surfaces": ["Management Dashboard", "Owner Dashboard", "Finance overview"],
                "backend_routes": ["GET /finance/building-overview", "GET /finance/portal-bank-balances"],
                "sources": _source(
                    ["annual_levies.*_fund.current_balance", "annual_levies.*_fund.closing_balance", "bank_accounts.*_balance"],
                    ["analytics.fact_financial_balance.closing_balance_cents", "finance.journal_lines.amount_cents", "finance.funds.fund_type"],
                ),
                "cache_strategy": "Future value cache should key the latest fact row by building_id + financial_year + fund_type; invalidate after BI fact materialisation or journal posting.",
                "display_guidance": "Prefer as-of balance from analytics.fact_financial_balance; portal bank balances are read-only cross-checks.",
            },
            {
                "canonical_key": "expense.budget_vs_actual",
                "canonical_label": "Budget vs Actual",
                "similar_labels": ["Expense Breakdown", "Admin Fund Expenses", "Sinking Fund Expenses", "Budget Variance"],
                "calculation_id": "expense.budget_vs_actual.v1",
                "surfaces": ["Finance charts", "Budget vs Actual", "Invoices/AP"],
                "backend_routes": ["GET /finance/charts", "GET /finance/budget-vs-actual", "POST /invoices/{id}/confirm"],
                "sources": _source(
                    [
                        "levy_categories.budgeted_amount",
                        "levy_categories.actual_amount",
                        "financial_transactions.amount",
                        "financial_transactions.category_name",
                        "invoice_documents.confirmed_data.total",
                        "invoice_documents.confirmed_data.gst",
                    ],
                    ["finance.journal_lines.amount_cents", "finance.gl_accounts.account_type=expense"],
                ),
                "cache_strategy": "Future value cache should key by building_id + levy_year + category revision; invalidate on invoice confirmation, manual transaction, or category update.",
                "display_guidance": "Confirmed invoice data is accounting input; OCR parsed data is review aid.",
            },
            {
                "canonical_key": "owner.total_ownership_cost",
                "canonical_label": "Total Ownership Cost",
                "similar_labels": ["12-Month True Cost", "True Ownership Cost", "Total Annual Cost"],
                "calculation_id": "owner.total_ownership_cost.v1",
                "surfaces": ["OwnerHub TCO", "Owner Dashboard true cost card"],
                "backend_routes": ["GET /owner-hub/unit-tco", "POST /owner-hub/properties/{property_id}/tco"],
                "sources": _source(
                    ["annual_levies", "council_rates", "water_bills", "sinking_fund_plan", "units.entitlement"],
                    [],
                ),
                "cache_strategy": "Future value cache should key by building_id + unit_number + year + owner input hash; do not cache user-entered mortgage/rent assumptions globally.",
                "display_guidance": "Capital replacement is informational; do not add it to total_costs when sinking levy is already included.",
            },
        ],
        "calculation_definitions": [
            {
                "calculation_id": "levy.annual_commitment.v1",
                "formula": "(admin_payable_annual_per_uoe + sinking_payable_annual_per_uoe) * unit_entitlement",
                "source_precedence": [
                    "annual_levies.proposed_*_expenses",
                    "annual_levies.*_fund.levy_income compatibility fallback",
                    "legacy per-UOE fields",
                    "active-year buildings.trust_config payable budget cents override",
                ],
            },
            {
                "calculation_id": "levy.amount_owing.v1",
                "formula": "max(signed_unit_balance, 0)",
                "source_precedence": ["Postgres AR journal balance when route is PG-primary", "Mongo unit_levy_ledger.net_balance fallback"],
            },
            {
                "calculation_id": "levy.credit_balance.v1",
                "formula": "abs(min(signed_unit_balance, 0))",
                "source_precedence": ["Same signed balance as levy.amount_owing.v1"],
            },
            {
                "calculation_id": "levy.collection.current_year.v1",
                "formula": "(opening_arrears + levied - outstanding) / (opening_arrears + levied) * 100, clamped 0..100",
                "source_precedence": ["backend/domain/finance/formulas/collection.py"],
            },
            {
                "calculation_id": "expense.budget_vs_actual.v1",
                "formula": "actual_by_category - budgeted_amount",
                "source_precedence": ["financial_transactions grouped actual", "levy_categories.actual_amount fallback"],
            },
        ],
        "cache_invalidation_events_for_future_value_cache": [
            "levy_run_imported",
            "receipt_verified",
            "receipt_reversed",
            "bank_payment_allocated",
            "invoice_confirmed",
            "manual_financial_transaction_saved",
            "levy_category_updated",
            "bi_financial_balance_materialised",
        ],
    }


def get_finance_calculation_registry(*, building_id: str | None = None) -> dict[str, Any]:
    """Return a deep-copied registry payload with cache metadata.

    Definitions are global, but the response includes ``building_id`` so callers
    can cache the contract alongside building-scoped dashboard payloads without
    ambiguity.
    """
    global _registry_cache, _registry_cache_created_monotonic, _registry_cache_created_at

    now = monotonic()
    cache_hit = (
        _registry_cache is not None
        and now - _registry_cache_created_monotonic < CACHE_TTL_SECONDS
    )
    if not cache_hit:
        _registry_cache = _build_registry()
        _registry_cache_created_monotonic = now
        _registry_cache_created_at = _now_iso()

    payload = deepcopy(_registry_cache)
    payload["metadata"] = {
        "building_id": building_id,
        "generated_at": _registry_cache_created_at,
        "cache": {
            "hit": cache_hit,
            "ttl_seconds": CACHE_TTL_SECONDS,
            "age_seconds": round(now - _registry_cache_created_monotonic, 3),
            "key": f"finance_calculation_registry:{REGISTRY_VERSION}",
        },
    }
    return payload


def reset_finance_calculation_registry_cache() -> None:
    """Test/helper hook for deterministic cache assertions."""
    global _registry_cache, _registry_cache_created_monotonic, _registry_cache_created_at
    _registry_cache = None
    _registry_cache_created_monotonic = 0.0
    _registry_cache_created_at = None
