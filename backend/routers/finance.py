"""
# @featuretrace:finance-postgres-write-lifecycle — Lifecycle routes for PG write mode: verify, reject (reversal), safe delete, adjustment journals.
# Layer: router
# Data flow: PATCH /levy-payments/{id}/verify → finance_postgres_write_service.verify_receipt (building-scoped)
#            PATCH /levy-payments/{id}/reject → finance_postgres_write_service.reject_receipt + FinancialCoreService.reverse_entry (building-scoped)
#            DELETE /levy-payments/{id}       → finance_postgres_write_service.delete_receipt or 409 POSTED_RECORD_IMMUTABLE (building-scoped)
#            POST /finance/adjustment-journals → finance_postgres_write_service.create_adjustment_journal (building-scoped)
# Related: backend/services/finance_postgres_write_service.py
#          backend/services/finance_write_cutover_service.py
#          backend/services/financial_core/service.py
# Toggle: financial_pg_writes_enabled
# Table: finance.receipts, finance.journal_entries, finance.journal_lines, core.outbox
# Tests: tests/backend/test_finance_payment_lifecycle.py

# @featuretrace:finance-postgres-write-cutover — Route-gated PostgreSQL write-primary support for selected finance write routes.
# Data flow: POST /levy-payments -> finance_write_cutover_service -> finance_postgres_write_service -> finance.* + core.outbox (building-scoped).
# Related: backend/services/finance_write_cutover_service.py
#          backend/services/finance_postgres_write_service.py

# @featuretrace:finance-postgres-read-cutover — Route-level finance read-source resolution (gate consulted by every finance read route, including building/unit overview).
# Layer: router
# Data flow: GET /finance/building-overview, /finance/unit-dashboard-overview/{unit} -> get_finance_route_runtime_state() -> core.domain_cutover_status -> Mongo or Postgres primary (building-scoped).
# Related: backend/services/finance_route_cutover_service.py
#          backend/services/domain_source_guard.py
#          backend/services/finance_shadow_read_service.py
# Toggle: financial_pg_reads_enabled
# Collection: unit_levy_ledger, annual_levies
# Table: core.domain_cutover_status, core.shadow_diffs, finance.levy_items, finance.levy_runs
# Tests: tests/backend/test_dashboard_pg_first.py

# @featuretrace:arrears — Arrears Recovery Board endpoint and aging analysis.
# Layer: router
# Data flow: ArrearsRecoveryPage → /finance/arrears* → unit_levy_ledger + levy_payments (building-scoped).
# Related: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
# Scope: (building-scoped)
# @featuretrace:levy — Finance router: all levy, payment, arrears, fund-health, and ledger endpoints.
# Layer: router
# Data flow: FinancePage / OwnerDashboard / ArrearsRecoveryPage → /unit-levy-ledger, /levy-status,
#            /levy-payments, /finance/summary, /finance/levy-kpi → unit_levy_ledger + levy_payments
#            (building-scoped). Portal snapshots from strata_owners joined at query time (read-only).
# PAYMENT WRITE PATH: levy_payments record → _update_ledger_after_payment() → unit_levy_ledger.
#   Never write net_balance/total_paid directly to unit_levy_ledger from external imports.
# Related: backend/utils/finance_helpers.py (compute_next_estimated_payment)
#           frontend/src/pages/dashboard/FinancePage.tsx
#           frontend/src/pages/dashboard/OwnerDashboard.tsx
#           frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
#           frontend/src/app/(dashboard)/dashboard/page.tsx
#           frontend/src/components/dashboard/CashPositionCard.tsx
#           scripts/db/import_strata_web_payments.py (generic quarterly import)
Finance router module — Clean Architecture (2026-02-19)

Collections used (levy system — single source of truth for all accounting):
  - annual_levies     : one per calendar year ("2025", "2026") with fund totals and levy rates
  - levy_categories   : expense categories per year per fund type
  - unit_levy_ledger  : per-unit opening/levied/paid/closing balances per year
  - levy_payments     : actual payment records
  - projections       : multi-year financial projections

Portal snapshot collections (written by Strata Web scraper — READ-ONLY from this router):
  - strata_owners     : per-unit portal balance snapshots (balance, status, owner names)
  - strata_financials : per-category planned vs actual from Strata Web portal (financial_year key)
  - bank_accounts     : building bank account balances (admin/sinking) from portal
  - building_summaries: aggregated portal stats (arrears/credit totals, collection rate)

Single source of truth rule: portal data is joined at query time (never copied into levy collections).
net_balance in unit_levy_ledger is always the authoritative accounting balance.
Payment write path: levy_payments → _update_ledger_after_payment() → unit_levy_ledger.

Old collections dropped: finance, budgets, budget_categories
"""

import csv
import io
import math
import time
import uuid
import logging
from collections import defaultdict
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal

import asyncio
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from typing import List, Optional

from database import db
from domain.finance.formulas.arrears import unit_arrears_and_credit
from domain.finance.formulas.collection import (
    current_year_collection_rate,
    due_date_collection_rate,
    quarter_collection_fraction,
)
from domain.finance.money import cents_to_percentage, raw_percentage
from models.finance import (
    AnnualLevyResponse,
    LevyCategoryCreate,
    LevyCategoryResponse,
    LevyCategoryUpdate,
    UnitLevyLedgerResponse,
    LevyPaymentCreate,
    LevyPaymentResponse,
    LevyPaymentVerify,
    LevyPaymentReject,
    FinancialProjectionCreate,
    FinancialProjectionResponse,
    BudgetProposalCreate,
    ExpenseTransactionCreate,
    ExpenseTransactionResponse,
    IncomeTransactionCreate,
    IncomeTransactionResponse,
    SpecialLevyCreate,
    SpecialLevyResponse,
    SpecialLevyPaymentResponse,
    BankReconciliationCreate,
    BankReconciliationResponse,
    InsurancePolicyCreate,
    InsurancePolicyResponse,
    InterestRateResponse,
    SpecialResolutionRateCreate,
    SpecialResolutionRateResponse,
    MessageAck,
    DeleteWithIdAck,
    LevyReminderSettingsAck,
)
from services.arrears_interest_service import (
    compute_accrued_interest,
    get_building_interest_rate,
    get_effective_interest_rate,
    record_special_resolution_rate,
)
from services.gst_service import parse_levy_gst_settings
from services.levy_reminder_settings_service import (
    get_levy_reminder_settings as get_pg_levy_reminder_settings,
    upsert_levy_reminder_settings as upsert_pg_levy_reminder_settings,
)
from services.financial_read_service import FinancialReadService
from services.finance_route_cutover_service import get_finance_route_runtime_state
from services.store_router import read_through
from services.finance_metrics.lot_true_balance import (
    _is_plain_calendar_year,
    building_unapplied_credit_cents,
    compute_lot_true_balances,
)
from services.finance_pg_read_dr import (
    read_pg_first_with_mongo_dr,
    get_dr_snapshot,
    SERVED_MONGO_DR_FALLBACK,
)
from services.finance_write_cutover_service import get_finance_write_route_runtime_state
from services.finance_postgres_write_service import finance_postgres_write_service
from services.finance_shadow_read_service import maybe_run_finance_shadow
from services.finance_calculation_registry import get_finance_calculation_registry
from services.settings_service import get_general_settings as _get_general_settings
from services.settings_service import get_unit_display_rules as _get_unit_display_rules
from services.financial_core.domain.entities import (
    RecordPaymentCommand,
    AllocatePaymentCommand,
    ReverseEntryCommand,
    SchemeRef,
    PaymentChannel,
)
from utils.auth import get_current_user, get_approved_user, get_current_building, effective_role
from utils.finance_helpers import (
    compute_period_installment_amounts,
    compute_period_due_dates,
    compute_mongo_quarter_statuses,
    sum_ledger_collected_outstanding,
    compute_remaining_payment_obligation,
    normalize_effective_total_paid,
    normalise_fund_type,
    legacy_fund_type,
    get_annual_fund_balance,
    get_fy_date_range,
    get_fy_label,
    get_levy_rates,
    get_levy_rate_breakdown,
    get_ledger_quality,
    get_unit_ledger_stats,
    get_arrears_metrics,
    get_collection_rate_metrics,
    get_fund_collections_by_unit_type,
    get_levy_proposed_amounts,
    compute_grace_period_counts,
)
from utils.helpers import create_user_notification, create_audit_log, get_current_timestamp
from utils.file_scan import scan_upload
from utils.permissions import get_user_permissions, require_permission, require_feature
from utils.unit_number import resolve_canonical_unit_number, user_unit_matches
from utils.financial_strangler import route_financial_write
# GAP-SEC-005 group 2, tier A. Added as an ADDITIONAL dependency alongside each
# route's existing check, never as a replacement — the effective rule is the
# intersection, so this can only narrow access. See
# docs/security/group2_financial_route_tiers_2026_08_24.md
from services.capability_registry import require_capability

# ─────────────────────────────────────────────────────────────────────────────

# Fallback total UOE — only used when annual_levies record is missing for the building/year.
# East Gate = 10000. Sierra = 9. Harbourview = 3. Never use this constant for production
# calculations; always fetch total_uoe from annual_levies first.
TOTAL_UOE = 10000

router = APIRouter(prefix="")
security = HTTPBearer(auto_error=False)


async def _unit_display_rules_safe(building_id: str) -> list:
    """Per-building unit display rules, or [] when settings are unavailable.

    Canonical unit resolution must degrade to the generic candidate expansion
    rather than fail the finance read when the settings collection cannot be
    reached (startup races, mocked test databases).
    """
    try:
        return await _get_unit_display_rules(building_id)
    except Exception:
        return []
logger = logging.getLogger(__name__)
_financial_read_service = FinancialReadService()

# Task #17 diagnostic (2026-07-13): per-building timestamps of recent
# GET /finance/summary invocations, so the next "unit_levy_ledger aggregate
# empty despite annual_levies found" occurrence can log how many other
# finance.summary calls for the same building were in flight at that moment —
# without needing to catch a live occurrence with external tooling. Concurrent
# read-only reproduction (asyncio.gather bursts against the real DB) did not
# reproduce the empty aggregate on its own, so this checks whether the
# defect requires the concurrency this counts, or is unrelated to it.
#
# KNOWN LIMITATION (audit finding, 2026-07-13): this module-level dict is
# per-PROCESS state. The backend runs as 4 separate `uvicorn --workers 4` OS
# processes, each importing this module independently, so
# `overlapping_summary_calls_5s` only ever counts calls handled by the SAME
# worker process — it is blind to true concurrent calls that land on a
# sibling process (which nginx/uvicorn's connection distribution can easily
# do for two different real users' near-simultaneous requests). A logged
# value of 0 therefore does NOT prove no real concurrency occurred; it only
# proves no *same-process* concurrency occurred. Treat this diagnostic as
# informative, not conclusive, on that specific question.
_recent_summary_calls: dict[str, list[float]] = defaultdict(list)


def _aud_from_cents(cents: int | float | None) -> float:
    """Generated function header.

    Function: _aud_from_cents
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return round(float(cents or 0) / 100, 2)


def _empty_building_overview_pg_response(year: str | None) -> dict:
    """Generated function header.

    Function: _empty_building_overview_pg_response
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    fy = year or str(date.today().year)
    return {
        "year": fy,
        "admin_fund": {
            "total_levied": 0.0,
            "total_paid": 0.0,
            "collection_rate": 0.0,
            "closing_balance": 0.0,
            "current_balance": 0.0,
            "balance_cents": 0,
        },
        "sinking_fund": {
            "total_levied": 0.0,
            "total_paid": 0.0,
            "collection_rate": 0.0,
            "closing_balance": 0.0,
            "current_balance": 0.0,
            "balance_cents": 0,
        },
        "total_levied": 0.0,
        "total_paid": 0.0,
        "total_opening_arrears": 0.0,
        "total_outstanding": 0.0,
        "levies_paid_pct": 0.0,
        "fund_health": 0.0,
        "total_obligations": 0.0,
        "admin_collection_rate": 0.0,
        "sinking_collection_rate": 0.0,
        "arrears_delta_pct": None,
        "units_in_arrears": 0,
        "admin_fund_trend": [],
        "sinking_fund_trend": [],
        "source": "postgres_ledger",
    }


async def _get_building_overview_mongo_fallback(building_id: str, year: Optional[str]) -> dict:
    """MongoDB fallback for GET /finance/building-overview, used only when the
    PostgreSQL ledger read raises a genuine error (connection/query failure) —
    NOT when PG is reachable but simply has no ledger data yet for this
    building/year (that's a valid state; _empty_building_overview_pg_response
    handles it without touching Mongo). Restores the pre-PR-495 Mongo
    computation (unit_levy_ledger aggregate) so "PG has an issue" degrades to
    a real answer instead of an empty dashboard card or a 503.
    """
    if not year:
        year = await _resolve_default_levy_year(building_id, fallback=str(date.today().year))

    collection_rate_metrics = await get_collection_rate_metrics(year, building_id)

    pipeline = [
        {"$match": {"building_id": building_id, "year": year, "is_test_data": {"$ne": True}}},
        {"$group": {
            "_id": None,
            "admin_levied": {"$sum": "$admin_levied"},
            "admin_paid": {"$sum": "$admin_paid"},
            "sinking_levied": {"$sum": "$sinking_levied"},
            "sinking_paid": {"$sum": "$sinking_paid"},
            # $sum skips nulls and returns 0 for an all-null group, so the summed value
            # alone cannot tell "every unit was charged nothing" from "no unit carries a
            # fund split at all". These counts make the difference visible.
            #
            # It is not hypothetical: East Gate's 2026 ledger has admin_levied and
            # sinking_levied NULL on all 87 rows while total_levied is populated
            # ($220,187.56), because 2026 was back-solved from a portal balance rather
            # than built from itemised per-fund charges (GAP-FIN-035). Without these
            # counts the dashboard reported "Admin Fund: $0.00 levied, 0% collected"
            # beside "Total: $220,187.56, 96.4%" — a false statement, not a gap.
            "admin_levied_known": {
                "$sum": {"$cond": [{"$ne": [{"$ifNull": ["$admin_levied", None]}, None]}, 1, 0]}
            },
            "sinking_levied_known": {
                "$sum": {"$cond": [{"$ne": [{"$ifNull": ["$sinking_levied", None]}, None]}, 1, 0]}
            },
            "total_levied": {"$sum": "$total_levied"},
            "total_paid": {"$sum": "$total_paid"},
            "total_opening_arrears": {"$sum": "$opening_arrears"},
            "total_outstanding": {
                "$sum": {"$cond": [{"$gt": ["$net_balance", 0]}, "$net_balance", 0]}
            },
            # Signed (not clamped) sums, needed to derive a genuinely year-scoped "paid so far"
            # figure per fund -- see the paid_this_year comment below for why the raw *_paid sums
            # above cannot be used directly for this.
            "net_balance_signed_sum": {"$sum": "$net_balance"},
            "admin_closing_sum": {"$sum": "$admin_closing"},
            "sinking_closing_sum": {"$sum": "$sinking_closing"},
        }},
    ]
    result = await db.unit_levy_ledger.aggregate(pipeline).to_list(1)
    agg = result[0] if result else {}

    # A fund whose split is recorded on NO unit is unknown, not zero. Reporting 0.0 for
    # it states something false — CLAUDE.md's missing-vs-measurement rule, the same class
    # already fixed in /owner-finance/health-explanation. Consumers must render None as
    # "not available", never as $0.
    _admin_split_known = int(agg.get("admin_levied_known", 0) or 0) > 0
    _sinking_split_known = int(agg.get("sinking_levied_known", 0) or 0) > 0

    admin_levied = round(float(agg.get("admin_levied", 0) or 0), 2) if _admin_split_known else None
    admin_paid = round(float(agg.get("admin_paid", 0) or 0), 2) if _admin_split_known else None
    sinking_levied = round(float(agg.get("sinking_levied", 0) or 0), 2) if _sinking_split_known else None
    sinking_paid = round(float(agg.get("sinking_paid", 0) or 0), 2) if _sinking_split_known else None
    total_levied = round(float(agg.get("total_levied", 0) or 0), 2)
    total_paid = round(float(agg.get("total_paid", 0) or 0), 2)
    total_opening_arrears = round(float(agg.get("total_opening_arrears", 0) or 0), 2)
    total_outstanding = round(float(agg.get("total_outstanding", 0) or 0), 2)

    # admin_paid/sinking_paid/total_paid (summed straight from unit_levy_ledger) are NOT
    # reliably scoped to one year -- confirmed live 2026-08-01: every one of East Gate's 87
    # FY2026 unit_levy_ledger documents carries a reconciliation_note explicitly describing
    # total_paid as "back-solved from the portal's live outstanding balance... cumulative
    # payment history through the scrape date, not payments received within this calendar year
    # specifically." Building-wide this summed to $1,769,655.36 against a real total_levied of
    # $220,187.56 -- 8x inflated, not a one-unit issue. admin_closing/sinking_closing/net_balance
    # ARE genuine per-fund/overall running balances (verified: for one unit, admin_levied -
    # admin_closing_sum and sinking_levied - sinking_closing_sum summed to that unit's own
    # independently-reported paid-this-year figure exactly), so the correctly-scoped amount paid
    # toward THIS year's own levied charges is derived algebraically the same way as the
    # per-unit fix in _get_unit_dashboard_overview_mongo_fallback.
    #
    # GAP-FIN-035 (2026-08-03): the algebraic derivation above (levied - Σ signed
    # closing/net_balance, summed BEFORE clamping) is itself the advance-payment
    # leak — a unit that pays ahead for a not-yet-due period drives its own
    # closing/net_balance negative, and summing signed values before subtracting
    # nets that credit against every OTHER unit's arrears at the building level.
    # Use the per-unit-clamped get_collection_rate_metrics() result instead: each
    # unit's own credit can only ever fill its own due_to_date, never leak into or
    # net against another unit's figure. See domain.finance.formulas.collection.
    # due_date_collection_rate().
    admin_paid_this_year = collection_rate_metrics["admin_fund"]["collected_to_date"]
    sinking_paid_this_year = collection_rate_metrics["sinking_fund"]["collected_to_date"]
    total_paid_this_year = collection_rate_metrics["collected_to_date"]

    units_agg = await db.unit_levy_ledger.aggregate([
        {"$match": {
            "building_id": building_id, "year": year, "net_balance": {"$gt": 0.01},
            "is_test_data": {"$ne": True},
        }},
        {"$count": "count"},
    ]).to_list(1)
    units_in_arrears_count = int(units_agg[0]["count"]) if units_agg else 0

    # GAP-DASH-001 P0-6: units_in_arrears must be the grace-aware canonical count from
    # get_arrears_metrics() — the SAME basis as /finance/summary, /arrears/detail and
    # /stats/building-kpis — never a raw net_balance>0 tally, which over-counts units still
    # inside their instalment grace window and produces the "31 vs 14 units" mismatch class
    # (CLAUDE.md Rule 10: the count and the dollar total must share one basis). Reuse the
    # canonical _compute_grace_aware_arrears helper; for a historical (non-current) year it
    # returns the raw count unchanged, matching /finance/summary's own behaviour. Falls back
    # to the raw count only if grace resolution fails, so the card never errors.
    try:
        _grace_settings = await _get_general_settings(building_id, {"_id": 0})
        _grace = await _compute_grace_aware_arrears(
            year=year,
            building_id=building_id,
            settings_doc=_grace_settings,
            today=date.today(),
            levy_year_int=_year_int(year) or date.today().year,
            raw_units_owing=units_in_arrears_count,
            raw_total_outstanding=total_outstanding,
        )
        units_in_arrears_count = int(_grace["units_owing"])
    except Exception as exc:
        logger.warning(
            "finance/building-overview: grace-aware units_in_arrears unavailable for "
            "building %s year %s; using raw net_balance>0 count %s: %s",
            building_id, year, units_in_arrears_count, exc,
        )

    try:
        current_levy_year = await _resolve_current_levy_year(building_id)
    except Exception as exc:
        logger.warning(
            "finance/building-overview: could not resolve levy-year settings for Mongo fallback "
            "for building %s; using request/calendar year cap: %s",
            building_id,
            exc,
        )
        current_levy_year = _year_int(year) or date.today().year
    latest_trend_docs = await db.annual_levies.find(
        {"building_id": building_id, "year": {"$lte": str(current_levy_year)}},
        {
            "year": 1,
            "admin_fund": 1,
            "sinking_fund": 1,
            "_id": 0,
        },
    ).sort("year", -1).limit(8).to_list(8)
    trend_docs = [
        d for d in reversed(latest_trend_docs)
        if (_year_int(d.get("year")) is not None and _year_int(d.get("year")) <= current_levy_year)
    ][-8:]

    def _fund_actual_or_current_point(doc: dict, fund_key: str) -> float:
        """Generated function header.

        Function: _fund_actual_or_current_point
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        # GAP-DASH-001 (Cash Position trend): every point on the admin/sinking cash sparkline
        # must be the SAME quantity — the fund BALANCE — so the line is a real balance
        # trajectory across years. Previously prior/completed years returned `total_expenses`
        # (a spend figure) while the current year returned the fund balance; mixing the two
        # crushed the small prior-year expense points flat against the large current-year
        # balance, which read as "only the current year shows, the rest are zero" (sinking fund).
        # get_annual_fund_balance prefers current_balance, then the audited closing_balance for
        # completed years, then opening_balance — one consistent basis for every year.
        fund = doc.get(fund_key, {}) or {}
        balance, _source = get_annual_fund_balance(fund)
        return balance

    admin_fund_trend = [_fund_actual_or_current_point(d, "admin_fund") for d in trend_docs]
    sinking_fund_trend = [_fund_actual_or_current_point(d, "sinking_fund") for d in trend_docs]

    selected_levy = next((d for d in trend_docs if str(d.get("year")) == str(year)), None)
    if not selected_levy:
        selected_levy = await db.annual_levies.find_one(
            {"building_id": building_id, "year": str(year), "is_test_data": {"$ne": True}},
            {"_id": 0, "admin_fund": 1, "sinking_fund": 1},
        )
    admin_balance, admin_balance_source = get_annual_fund_balance((selected_levy or {}).get("admin_fund"))
    sinking_balance, sinking_balance_source = get_annual_fund_balance((selected_levy or {}).get("sinking_fund"))
    try:
        consolidated_balances = await _financial_read_service.get_consolidated_fund_balances(
            building_id=building_id,
            financial_year=str(year),
        )
    except Exception as exc:
        logger.warning(
            "finance/building-overview: consolidated fund balance overlay unavailable "
            "for building %s year %s: %s",
            building_id,
            year,
            exc,
        )
        consolidated_balances = None
    if consolidated_balances:
        # The route may still be Mongo-primary for levy/arrears while finance
        # cutover is in shadow mode. Fund cash is safe to overlay from the
        # consolidated BI fact because that fact is materialised from the same
        # Mongo operational sources and indexed for dashboard reads.
        admin_balance = _aud_from_cents(consolidated_balances.get("admin_balance_cents"))
        sinking_balance = _aud_from_cents(consolidated_balances.get("sinking_balance_cents"))
        admin_balance_source = "analytics.fact_financial_balance"
        sinking_balance_source = "analytics.fact_financial_balance"
        if trend_docs and _year_int(trend_docs[-1].get("year")) == _year_int(year):
            # Keep the sparkline endpoint contract aligned with the headline
            # fund balance: the current-year point is today's/as-of cash
            # position, while prior points remain actual annual spend.
            if admin_fund_trend:
                admin_fund_trend[-1] = admin_balance
            if sinking_fund_trend:
                sinking_fund_trend[-1] = sinking_balance

    # admin_rate/sinking_rate (GAP-FIN-035, 2026-08-03): the due-date Collection
    # Rate per fund, sourced directly from get_collection_rate_metrics()'s
    # per-unit-clamped result — never re-derived from a signed aggregate sum.
    # This is a DIFFERENT metric from fund_health below (full-year coverage);
    # see docs/architecture/financial-summary-analysis-of-issues.md Rule 53:
    # "Collection performance is not fund health." Do not consolidate the two.
    # Gated on the SAME known-split flags as admin_levied/sinking_levied above. Without
    # a per-fund split there is no per-fund denominator, so the rate is unknown — and
    # reporting 0.0% collected for a fund that in fact collected 96.4% is the same false
    # statement the levied figures were just fixed for. This was missed in that pass:
    # the levied/paid values come from `agg`, these come from collection_rate_metrics,
    # so gating one did not gate the other.
    admin_rate = (collection_rate_metrics["admin_fund"]["collection_rate_pct"]
                  if _admin_split_known else None)
    sinking_rate = (collection_rate_metrics["sinking_fund"]["collection_rate_pct"]
                    if _sinking_split_known else None)
    # GAP-FIN-016 Item C (2026-07-21): fund_health now delegates to the canonical
    # domain formula (levy.collection.current_year.v1) — same formula server.py's
    # building-kpis collection_rate already uses, at this endpoint's own 1dp
    # precision. This is also the fix for a real PG/Mongo parity gap (see the
    # Postgres branch below): both branches now compute this identically.
    # NOTE: fund_health/current_year_collection_rate() is Full-Year Levy Coverage
    # (metric 2), not Collection Rate (metric 1, admin_rate/sinking_rate above) —
    # the two are intentionally different denominators and must never be merged
    # or relabelled as one another.
    _collection = current_year_collection_rate(
        opening_arrears_cents=round(total_opening_arrears * 100),
        levied_cents=round(total_levied * 100),
        outstanding_cents=round(total_outstanding * 100),
        digits=1,
    )
    total_obligations = float(_collection.total_obligations_cents) / 100
    net_collected = float(_collection.net_collected_cents) / 100
    fund_health = float(_collection.collection_rate_pct)
    levies_paid_pct = float(raw_percentage(
        round((total_levied - total_outstanding) * 100), round(total_levied * 100),
        digits=1, zero_denominator_value=Decimal("0.0"),
    ))
    arrears_delta_pct = (
        round((total_outstanding - total_opening_arrears) / total_opening_arrears * 100, 1)
        if total_opening_arrears > 0 else None
    )

    return {
        "year": year,
        "admin_fund": {
            "total_levied": admin_levied,
            "total_paid": admin_paid,
            "paid_this_year": admin_paid_this_year,
            "collection_rate": admin_rate,
            "closing_balance": round(float(((selected_levy or {}).get("admin_fund") or {}).get("closing_balance") or 0), 2),
            "current_balance": admin_balance,
            "balance": admin_balance,
            "balance_source": admin_balance_source,
            "balance_cents": int(round(admin_balance * 100)),
        },
        "sinking_fund": {
            "total_levied": sinking_levied,
            "total_paid": sinking_paid,
            "paid_this_year": sinking_paid_this_year,
            "collection_rate": sinking_rate,
            "closing_balance": round(float(((selected_levy or {}).get("sinking_fund") or {}).get("closing_balance") or 0), 2),
            "current_balance": sinking_balance,
            "balance": sinking_balance,
            "balance_source": sinking_balance_source,
            "balance_cents": int(round(sinking_balance * 100)),
        },
        "total_levied": total_levied,
        "total_paid": total_paid,
        "total_paid_this_year": total_paid_this_year,
        "total_opening_arrears": total_opening_arrears,
        "total_outstanding": total_outstanding,
        "levies_paid_pct": levies_paid_pct,
        "fund_health": fund_health,
        "total_obligations": total_obligations,
        "admin_collection_rate": admin_rate,
        "sinking_collection_rate": sinking_rate,
        # collected_in_advance (metric 3, GAP-FIN-035): unapplied credit + receipts
        # for periods not yet due — never folded into admin_rate/sinking_rate/
        # fund_health above.
        "collected_in_advance": collection_rate_metrics["collected_in_advance"],
        # Metric 1 — the DUE-DATE collection rate, exposed alongside metrics 2 and 3 so a
        # consumer no longer has to choose between an unlabelled coverage figure and a
        # second round trip to /stats/building-kpis.
        #
        # levies_paid_pct and fund_health below are both metric 2 (full-year coverage:
        # (levied - outstanding) / levied), and the Management dashboard was reading
        # levies_paid_pct and captioning it "collection" — the same mislabelling already
        # corrected in /stats/building-kpis. CLAUDE.md: coverage is never allowed to
        # carry the Collection Rate label in a UI or API response.
        "due_date_collection_rate_pct": collection_rate_metrics["collection_rate_pct"],
        "due_to_date": collection_rate_metrics["due_to_date"],
        "arrears_delta_pct": arrears_delta_pct,
        "units_in_arrears": units_in_arrears_count,
        "admin_fund_trend": admin_fund_trend,
        "sinking_fund_trend": sinking_fund_trend,
        "source": "mongodb_fallback",
    }


def _empty_unit_dashboard_overview_pg_response(unit_number: str, year: str | None) -> dict:
    """Generated function header.

    Function: _empty_unit_dashboard_overview_pg_response
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "unit_number": unit_number,
        "financial_year": year or str(date.today().year),
        "unit_entitlement": 0,
        "admin_fund": {"annual": 0.0, "paid": 0.0},
        "sinking_fund": {"annual": 0.0, "paid": 0.0},
        "total_levied": 0.0,
        "total_paid": 0.0,
        "balance_owing": 0.0,
        "balance_credit": 0.0,
        "next_due_date": None,
        "quarters": [],
        "source": "postgres_ledger",
    }


async def _get_unit_dashboard_overview_mongo_fallback(
        building_id: str, unit_number: str, year: Optional[str],
) -> dict:
    """MongoDB fallback for GET /finance/unit-dashboard-overview/{unit}, used only
    when the PostgreSQL ledger read raises a genuine error — see
    _get_building_overview_mongo_fallback's docstring for the same distinction
    (PG reachable-but-empty is a valid state and does not reach here). Restores
    the pre-PR-495 Mongo computation (unit_levy_ledger / annual_levies).
    """
    unit_task = db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0, "entitlement": 1})
    if year:
        ledger_task = db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number, "year": year}, {"_id": 0}
        )
        levy_task = db.annual_levies.find_one({"building_id": building_id, "year": year}, {"_id": 0})
        unit, ledger, levy = await asyncio.gather(unit_task, ledger_task, levy_task)
    else:
        unit = await unit_task
        resolved_year = await _resolve_default_levy_year(building_id, fallback=str(date.today().year))
        year = resolved_year
        ledger_task = db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number, "year": resolved_year}, {"_id": 0}
        )
        levy_task = db.annual_levies.find_one({"building_id": building_id, "year": resolved_year}, {"_id": 0})
        ledger, levy = await asyncio.gather(ledger_task, levy_task)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    fy = (ledger or {}).get("year") or (levy or {}).get("year") or year or str(date.today().year)
    entitlement = float(unit.get("entitlement") or 0)
    rates = await get_levy_rates(str(fy), building_id)
    admin_annual = round(float(rates.get("admin_annual") or 0) * entitlement, 2)
    sinking_annual = round(float(rates.get("sinking_annual") or 0) * entitlement, 2)

    total_levied = round(float((ledger or {}).get("total_levied") or (admin_annual + sinking_annual)), 2)
    total_paid = round(float((ledger or {}).get("total_paid") or 0), 2)
    net_balance = round(float((ledger or {}).get("net_balance") or (total_levied - total_paid)), 2)

    # total_paid is NOT reliably "paid toward this year" -- confirmed live 2026-08-01 via this
    # exact ledger doc's own reconciliation_note: it is "back-solved from the portal's live
    # outstanding balance (opening + levied - target_balance), not a sum of individual observed
    # payment transactions... cumulative payment history through the scrape date, not payments
    # received within this calendar year specifically." Using it directly for "Paid to date"
    # produced numbers unrelated to what an owner actually paid this year (e.g. $28,783.04
    # against a $7,090.04 annual levy). net_balance, in contrast, IS a genuine, correctly
    # reconciled running balance (reconciliation_target_balance) -- so the amount actually paid
    # toward THIS year's own levied charges can be derived algebraically as
    # total_levied - net_balance (equivalently: what's been charged so far, minus what's still
    # owed/plus what's overpaid). Verified against a real example: total_levied=3545.02,
    # net_balance=-254.98 (in credit) -> paid_this_year=3800.00, matching the owner's own
    # independently-reported bank-side figure exactly.
    paid_this_year = round(total_levied - net_balance, 2)

    payment_schedule = levy.get("payment_schedule", []) if isinstance((levy or {}).get("payment_schedule"), list) else []
    # paid_this_year, NOT total_paid: compute_mongo_quarter_statuses waterfalls this amount
    # against quarters earliest-first to derive each one's paid/partial/overdue/unpaid status --
    # feeding it total_paid (uncapped at potentially many years' worth of cumulative payment)
    # would waterfall an amount several years too large across a single year's quarters, marking
    # all of them "paid" regardless of what was actually paid toward THIS year's charges. Missed
    # in the 2026-08-01 paid_this_year fix because TH087 (the reporting unit) has
    # payment_schedule=None -> quarters=[] regardless of which figure was passed in, which masked
    # this same bug existing here too.
    quarters = compute_mongo_quarter_statuses(payment_schedule, total_levied, paid_this_year)

    # Audit correction (2026-07-13), tightened after PR #502 review feedback:
    # total_outstanding must be clamped to 0, not the raw signed net_balance.
    # _compare_unit_dashboard_payloads compares this against PG's
    # get_unit_levy_balance()["arrears"], which is itself clamped to
    # max(0, closing_cents) — "arrears"/"outstanding" is a non-negative amount-
    # owed concept by definition, never a signed balance. Confirmed live: 12 of
    # 87 units for 13195/FY2026 currently have a negative net_balance (credit).
    # Passing the raw signed value here would flag every one of those 12 units
    # as a false shadow-diff mismatch (Mongo negative vs PG's always->=0 figure)
    # the first time each is viewed. Computed once and reused for both
    # balance_owing and total_outstanding — same concept, must never diverge.
    balance_owing_amount = round(max(net_balance, 0), 2)

    # ledger.next_due_date is frequently absent (confirmed live 2026-08-01: East Gate's own
    # unit_levy_ledger docs don't carry it, and annual_levies.payment_schedule -- the other thing
    # this could have come from -- is also None for FY2026), which showed as "Not scheduled" on
    # the Owner Dashboard even though the building's own configured levy schedule (settings.
    # levy_due_months/levy_due_day_type/levy_due_custom_dates) already defines the next due date.
    # Falls back to computing it directly from that schedule -- the same helper
    # (compute_period_due_dates) other endpoints in this file already use -- rather than only
    # ever showing a date for a quarter that has already been formally run/invoiced. This mirrors
    # what a human would expect: "when is my next levy due" is answerable from the building's
    # published schedule even before that period's finance.levy_runs row has been generated.
    next_due_date = (ledger or {}).get("next_due_date")
    next_payment_amount = None
    if not next_due_date:
        settings_doc = await _get_general_settings(building_id, {"_id": 0})
        due_months = (settings_doc or {}).get("levy_due_months") or [3, 6, 9, 12]
        due_day_type = (settings_doc or {}).get("levy_due_day_type") or "first"
        due_day = (settings_doc or {}).get("levy_due_day")
        custom_dates = (settings_doc or {}).get("levy_due_custom_dates") or {}
        fy_start_month = int((settings_doc or {}).get("financial_year_start_month") or 1)
        try:
            fy_year_int = int(str(fy).split("-")[0])
        except (TypeError, ValueError):
            fy_year_int = date.today().year
        computed_dates = _compute_period_due_dates(
            fy_year_int, due_months, due_day_type, due_day, len(due_months) or 4,
            custom_dates, fy_start_month=fy_start_month,
        )
        today_iso = date.today().isoformat()
        upcoming = sorted(d for d in computed_dates if d >= today_iso)
        next_due_date = upcoming[0] if upcoming else None
        # quarters is [] whenever payment_schedule is missing (confirmed live 2026-08-01: East
        # Gate's FY2026 annual_levies doc has payment_schedule=None), which left the Owner
        # Dashboard showing "$0.00 · Not scheduled" -- the date fix above handles "Not scheduled",
        # this handles the "$0.00": an even split of the full annual levy across this building's
        # own configured number of periods is the base estimate for an upcoming, not-yet-invoiced
        # instalment.
        #
        # Only computed when quarters has no genuinely-derivable upcoming amount -- mirrors the
        # frontend's own nextUnpaidQuarter test (status != "paid" and (no due_date or due_date >=
        # today)) exactly, so this can never override real per-quarter data with an estimate.
        # A prior version of this comment claimed the frontend's fallback chain (next_payment_
        # adjusted ?? next_payment_amount ?? next-unpaid-quarter) already guaranteed this, which
        # is false: that chain checks next_payment_amount BEFORE the quarters-derived value, so
        # without this explicit guard, setting next_payment_amount here would have taken priority
        # over real quarters data whenever payment_schedule happens to be populated but
        # ledger.next_due_date specifically isn't (a real, independently possible combination,
        # not the same condition).
        has_real_upcoming_quarter = any(
            q.get("status") != "paid" and (
                not q.get("due_date") or str(q["due_date"])[:10] >= today_iso
            )
            for q in quarters
        )
        # net_balance offsets the base estimate, not just an even split alone: a unit already
        # sitting in credit (net_balance < 0, money already paid that hasn't been consumed by an
        # actual charge yet) will have that credit applied against the next invoice, so what
        # they'll actually need to pay next time is correspondingly less -- and symmetrically, a
        # unit already in arrears (net_balance > 0) would need MORE than the base instalment to
        # be caught up. Confirmed with the reporting owner's own numbers (TH087, FY2026): base
        # estimate $1,772.51 (7,090.04 / 4), net_balance -254.98 (in credit, the same amount the
        # portal scrape independently reported) -> $1,772.51 + (-254.98) = $1,517.53, clamped to
        # a minimum of $0 (never shown as a negative "amount due").
        #
        # Integer cents throughout (CLAUDE.md mandates cents-only arithmetic for
        # ledger-adjacent money) -- NOT the dollar-float version this block used
        # until 2026-08-01. (admin_annual + sinking_annual) / len(due_months) can
        # carry thirds-of-a-cent (e.g. an annual total of $6,121.22 / 4 =
        # $1,530.305), which never exactly cancels against a cents-rounded stored
        # net_balance -- the sub-cent residue surfaced as a nonsensical "$0.01 due"
        # instead of resolving cleanly to $0.00. Confirmed live against UA063 (Lot
        # 63, FY2026): net_balance=-1530.30, exactly one quarter's credit, produced
        # "$0.01 due 1 Sept" instead of "$1,530.30 due 1 Dec".
        #
        # A unit sitting on a full quarter's credit or more has already effectively
        # pre-paid the chronologically-next due date(s) -- the real "next amount
        # owing" period is further out, not the immediately-next calendar due date.
        # The date bug (Sept instead of Dec for UA063) was this: the fallback above
        # always picked upcoming[0] regardless of how many quarters of credit
        # already cover it. Arrears never skip a due date forward this way -- an
        # arrears balance is added to the very next instalment, it doesn't defer
        # which date is "next" -- so quarters_prepaid is 0 whenever net_balance is
        # not negative, leaving that case unchanged from before.
        if next_due_date and due_months and not has_real_upcoming_quarter:
            num_periods = len(due_months)
            base_instalment_cents = round((admin_annual + sinking_annual) * 100 / num_periods)
            net_balance_cents = round(net_balance * 100)

            quarters_prepaid = 0
            if net_balance_cents < 0 and base_instalment_cents > 0:
                quarters_prepaid = (-net_balance_cents) // base_instalment_cents

            if quarters_prepaid > 0:
                next_due_date = (
                    upcoming[quarters_prepaid] if len(upcoming) > quarters_prepaid
                    else (upcoming[-1] if upcoming else None)
                )

            remaining_cents = net_balance_cents + quarters_prepaid * base_instalment_cents
            next_payment_amount = round(max(0, base_instalment_cents + remaining_cents) / 100, 2)

    return {
        "unit_number": unit_number,
        "financial_year": str(fy),
        "unit_entitlement": int(entitlement),
        "admin_fund": {"annual": admin_annual, "paid": round(float((ledger or {}).get("admin_paid") or 0), 2)},
        "sinking_fund": {"annual": sinking_annual, "paid": round(float((ledger or {}).get("sinking_paid") or 0), 2)},
        "total_levied": total_levied,
        "total_paid": total_paid,
        "paid_this_year": paid_this_year,
        "balance_owing": balance_owing_amount,
        "balance_credit": round(abs(min(net_balance, 0)), 2),
        "next_due_date": next_due_date,
        "next_payment_amount": next_payment_amount,
        "quarters": quarters,
        "total_outstanding": balance_owing_amount,
        "source": "mongodb_fallback",
    }


async def _maybe_shadow_building_overview(building_id: str, year: Optional[str]) -> None:
    """Fire-and-forget shadow comparison for finance.building_overview.

    Unlike finance.summary (Mongo-primary + PG-shadow), this route reads
    Postgres directly as primary — there is no Mongo computation in the live
    response to compare. This independently computes the Mongo-side figure
    purely to feed the existing (previously unwired) _compare_building_
    overview_payloads comparator, so Phase D's shadow_diffs actually receives
    traffic for this route. Never blocks or affects the response —
    maybe_run_finance_shadow swallows all exceptions internally.
    """
    try:
        route_state = await get_finance_route_runtime_state(
            building_id=building_id, route_key="finance.building_overview",
        )
        if not route_state.get("run_shadow"):
            return
        mongo_payload = await _get_building_overview_mongo_fallback(building_id, year)
    except Exception as exc:
        logger.debug("finance.building_overview shadow: mongo fallback fetch failed: %s", exc)
        return
    await maybe_run_finance_shadow(
        building_id=building_id,
        route_key="finance.building_overview",
        mongo_payload=mongo_payload,
    )


async def _maybe_shadow_unit_dashboard_overview(
        building_id: str, unit_number: str, year: Optional[str],
) -> None:
    """Fire-and-forget shadow comparison for finance.unit_dashboard_overview.

    Same rationale as _maybe_shadow_building_overview — this route is also
    Postgres-primary with no Mongo value in the live response, so the
    Mongo-side figure is computed independently purely for the shadow check.
    """
    try:
        route_state = await get_finance_route_runtime_state(
            building_id=building_id, route_key="finance.unit_dashboard_overview",
        )
        if not route_state.get("run_shadow"):
            return
        mongo_payload = await _get_unit_dashboard_overview_mongo_fallback(building_id, unit_number, year)
    except Exception as exc:
        logger.debug("finance.unit_dashboard_overview shadow: mongo fallback fetch failed: %s", exc)
        return
    await maybe_run_finance_shadow(
        building_id=building_id,
        route_key="finance.unit_dashboard_overview",
        mongo_payload=mongo_payload,
    )


async def _maybe_shadow_unit_levy_ledger(
        building_id: str, year: Optional[str], enriched_entries: list[dict],
) -> None:
    """Fire-and-forget shadow comparison for finance.unit_levy_ledger (GAP-FIN-030
    Fix 4 -- Levy Status tab). Aggregate-level only, matching
    _compare_unit_levy_ledger_payloads. Never blocks or affects the response --
    maybe_run_finance_shadow swallows all exceptions internally. Deliberately
    postgres_read_supported=False in the route policy (see finance_route_
    cutover_service.py) -- this is shadow-only until GAP-FIN-031 (FY2026 receipt
    matching) is verified complete; promoting before that would repeat the
    unit_dashboard_overview wrong-balance near-miss found and rolled back
    2026-08-02 for this exact reason.
    """
    try:
        route_state = await get_finance_route_runtime_state(
            building_id=building_id, route_key="finance.unit_levy_ledger",
        )
        if not route_state.get("run_shadow"):
            return
        mongo_payload = {
            "year": year,
            "unit_count": len(enriched_entries),
            "total_levied": round(sum(float(e.get("total_levied") or 0) for e in enriched_entries), 2),
            "total_paid": round(sum(float(e.get("paid_this_year") or 0) for e in enriched_entries), 2),
        }
    except Exception as exc:
        logger.debug("finance.unit_levy_ledger shadow: mongo aggregate failed: %s", exc)
        return
    await maybe_run_finance_shadow(
        building_id=building_id,
        route_key="finance.unit_levy_ledger",
        mongo_payload=mongo_payload,
    )


async def _maybe_shadow_transactions(
        building_id: str, year: Optional[str], expenses: list, income: list,
        dimension: str,
) -> None:
    """Fire-and-forget shadow comparison for finance.transactions (GAP-FIN-030
    Fix 4 -- Transactions tab). Fired independently from the expense-transactions
    and income-transactions endpoints, each contributing only its own dimension
    (the other side's amount is passed as an empty list from the caller that
    doesn't have it) -- two separate shadow_diffs records rather than one merged
    comparison, consistent with these being two separate live endpoints.
    Same postgres_read_supported=False caution as _maybe_shadow_unit_levy_ledger.

    `dimension` ("expense" or "income") tells the comparator which single side
    this call actually populated -- a real, previously-live bug: comparing the
    *other*, intentionally-empty side against PG's real (non-partial) total
    produced a guaranteed false-positive critical diff on every single call
    (a building with genuinely zero transactions of one type would also
    infer wrongly from an empty-list check, so this must be passed explicitly
    by the caller, not inferred). See `_compare_transactions_payloads`/
    `_get_pg_payload_for_route` in `finance_shadow_read_service.py`.
    """
    try:
        route_state = await get_finance_route_runtime_state(
            building_id=building_id, route_key="finance.transactions",
        )
        if not route_state.get("run_shadow"):
            return
        mongo_payload = {
            "year": year,
            "_dimension": dimension,
            "total_expense": round(sum(float(e.get("amount") or 0) for e in expenses), 2),
            "total_income": round(sum(float(i.get("amount") or 0) for i in income), 2),
        }
        if dimension not in ("expense", "income"):
            raise ValueError(f"invalid dimension: {dimension!r}")
    except Exception as exc:
        logger.debug("finance.transactions shadow: mongo aggregate failed: %s", exc)
        return
    await maybe_run_finance_shadow(
        building_id=building_id,
        route_key="finance.transactions",
        mongo_payload=mongo_payload,
    )


def _normalise_category_name(value: object) -> str:
    """Generated function header.

    Function: _normalise_category_name
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return " ".join(str(value or "").strip().lower().split())


async def _get_actual_overrides_from_financial_transactions(
        year: str,
        building_id: str,
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """
    Build category actual overrides from financial_transactions expense rows.

    Returns two maps:
      - by category id:   {(fund_type_legacy, category_id): total_amount}
      - by category name: {(fund_type_legacy, category_name_norm): total_amount}

    fund_type_legacy is normalized to levy_categories-compatible values:
    "administrative" | "sinking".

    NOTE:
    - This intentionally reads the mirrored Mongo financial_transactions layer.
    - On Mongo write-primary paths this is immediate.
    - On Postgres write-primary paths (invoice confirm), values appear after outbox relay
      mirrors journal entries into financial_transactions.
    """
    pipeline = [
        {"$match": {
            "building_id": building_id,
            "financial_year": year,
            "transaction_type": "expense",
        }},
        {"$group": {
            "_id": {
                "fund_type": "$fund_type",
                "category_id": "$category_id",
                "category_name": "$category_name",
            },
            "total": {"$sum": "$amount"},
        }},
    ]

    try:
        cursor = db.financial_transactions.aggregate(pipeline)
        tx_groups = await cursor.to_list(length=None)
    except Exception:
        logger.warning("_get_actual_overrides_from_financial_transactions: aggregation failed; returning empty overrides")
        return {}, {}

    by_id: dict[tuple[str, str], float] = {}
    by_name: dict[tuple[str, str], float] = {}

    for row in tx_groups:
        group = row.get("_id") or {}
        total = round(float(row.get("total") or 0), 2)
        if total == 0:
            continue

        tx_fund_raw = str(group.get("fund_type") or "").strip().lower()
        tx_fund = legacy_fund_type(normalise_fund_type(tx_fund_raw))
        if tx_fund not in {"administrative", "sinking"}:
            continue

        cat_id = str(group.get("category_id") or "").strip()
        if cat_id:
            key = (tx_fund, cat_id)
            by_id[key] = round(by_id.get(key, 0) + total, 2)

        cat_name_key = _normalise_category_name(group.get("category_name"))
        if cat_name_key:
            key = (tx_fund, cat_name_key)
            by_name[key] = round(by_name.get(key, 0) + total, 2)

    return by_id, by_name


def _resolve_category_actual_amount(
        category_doc: dict,
        tx_actual_by_id: dict[tuple[str, str], float],
        tx_actual_by_name: dict[tuple[str, str], float],
) -> float:
    """
    Resolve category actual with transaction-first precedence.

    1) Match by (fund_type, category id)
    2) Match by (fund_type, category name)
    3) Fallback to stored levy_categories.actual_amount (legacy/manual path)

    Category-id matching is preferred to avoid accidental collisions where different
    categories share similar names. Name matching is a compatibility fallback for older
    invoice transactions that only carry category_name.
    """
    fund_type_raw = str(category_doc.get("fund_type") or "").strip().lower()
    fund_type = legacy_fund_type(normalise_fund_type(fund_type_raw))
    category_id = str(category_doc.get("id") or "").strip()
    category_name_key = _normalise_category_name(category_doc.get("name"))

    if category_id:
        key = (fund_type, category_id)
        if key in tx_actual_by_id:
            return tx_actual_by_id[key]

    if category_name_key:
        key = (fund_type, category_name_key)
        if key in tx_actual_by_name:
            return tx_actual_by_name[key]

    return round(float(category_doc.get("actual_amount") or 0), 2)


def _year_match_filter(year: Optional[str]) -> dict:
    if not year:
        return {}
    return {"$or": [{"financial_year": year}, {"year": year}, {"levy_year": year}]}


def _transaction_amount(row: dict) -> float:
    if row.get("amount") is not None:
        return round(float(row.get("amount") or 0), 2)
    if row.get("amount_cents") is not None:
        return round(float(row.get("amount_cents") or 0) / 100, 2)
    return 0.0


async def _fallback_financial_transactions(
        *,
        building_id: str,
        year: Optional[str],
        transaction_type: str,
) -> list[dict]:
    """Map financial_transactions mirror rows — and, when those are empty, Demo Bank / scraped-portal
    rows from demo_bank_transactions — into the legacy tab response shape."""
    type_aliases = {
        transaction_type,
        transaction_type.lower(),
        transaction_type.upper(),
        transaction_type.title(),
    }
    query = {
        "building_id": building_id,
        "$or": [
            {"transaction_type": {"$in": sorted(type_aliases)}},
            {"type": {"$in": sorted(type_aliases)}},
            {"direction": "debit" if transaction_type == "expense" else "credit"},
        ],
    }
    if year:
        query = {"$and": [query, _year_match_filter(year)]}

    rows: list[dict] = []
    is_unposted_evidence = False
    try:
        rows = await db.financial_transactions.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
    except Exception:
        logger.warning(
            "financial_transactions fallback failed for %s/%s/%s",
            building_id, year, transaction_type,
        )

    # Demo Bank imports, Strata Web/portal scrapes, and the historical-year rebuilds ALL write to
    # demo_bank_transactions — never to financial_transactions/expense_transactions/income_transactions.
    # So the Transactions tab was blank for every year whose only data is Demo Bank / scraped. Surface
    # those rows (active, non-archived) when the legacy mirrors are empty. Income = credit, expense =
    # debit; amounts are amount_cents, dates are posted_date (handled by the mapper below).
    #
    # Financial Evidence Gateway (docs/architecture/financial-summary-analysis-of-issues.md):
    # demo_bank_transactions is staging/evidence, not a posted ledger — a row surfaced from here is
    # NOT yet confirmed operational truth (e.g. it may later be reversed/re-matched during posting).
    # is_unposted_evidence + a disclosure string are stamped on every mapped row below so the
    # frontend can render them as "pending posting" rather than indistinguishably from a posted
    # financial_transactions row -- see get_levy_status's payment_history is_reconstructed/disclosure
    # pattern above for the established precedent this mirrors. Reviewed exception recorded in
    # tests/backend/test_finance_input_source_guardrails.py (2026-08-05 audit).
    if not rows:
        is_unposted_evidence = True
        demo_query: dict = {
            "building_id": building_id,
            "direction": "debit" if transaction_type == "expense" else "credit",
            "is_archived": {"$ne": True},
        }
        if year:
            # Match the year across every field Demo Bank writers use, PLUS the posted_date prefix —
            # Strata Web/portal scrapes may carry only posted_date ("2026-03-15"), not a levy_year,
            # so a field-only match would silently drop them.
            demo_query["$or"] = [
                {"levy_year": year}, {"financial_year": year},
                {"year": year}, {"strata_year": year},
                {"posted_date": {"$regex": f"^{year}-"}},
            ]
        try:
            rows = await db.demo_bank_transactions.find(
                demo_query, {"_id": 0}
            ).sort("posted_date", -1).to_list(1000)
        except Exception:
            logger.warning(
                "demo_bank_transactions fallback failed for %s/%s/%s",
                building_id, year, transaction_type,
            )
            rows = []

    mapped = []
    for row in rows:
        date_value = row.get("date") or row.get("posted_date") or row.get("transaction_date") or row.get("created_at") or _now()[:10]
        raw_desc = row.get("description") or row.get("memo") or ""
        # Category grouping: Demo Bank rows carry NO category_name field, so everything collapsed
        # into one null category. Levy income is a single "Levy" category; an expense's category is
        # embedded in its description ("... spend summary: X (Admin Fund)") and parsed by the
        # canonical _extract_category_name (the SAME parser expense_posting_service uses — one
        # source). Fall back to the fund label if a row has neither a category_name nor a parseable
        # description.
        category_name = row.get("category_name")
        if not category_name:
            if transaction_type == "income":
                category_name = "Levy"
            else:
                from services.reconstruction_generators.expense_posting_service import _extract_category_name
                parsed = _extract_category_name(raw_desc)
                if parsed and parsed != raw_desc:
                    category_name = parsed
                else:
                    fund_label = str(row.get("fund_type") or "").strip().title()
                    category_name = f"{fund_label} Fund expense" if fund_label else "Uncategorised"
        common = {
            "id": row.get("id") or row.get("transaction_id") or row.get("journal_entry_id") or str(uuid.uuid4()),
            "building_id": building_id,
            "plan_id": building_id,
            "financial_year": str(row.get("financial_year") or row.get("year") or row.get("levy_year") or year or ""),
            "amount": _transaction_amount(row),
            "date": str(date_value)[:10],
            "description": raw_desc or category_name,
            "category_name": category_name,
            "fund_type": row.get("fund_type"),
            "created_at": str(row.get("created_at") or date_value),
            "updated_at": str(row.get("updated_at") or row.get("created_at") or date_value),
            "created_by": row.get("created_by") or "system",
            "is_unposted_evidence": is_unposted_evidence,
        }
        if is_unposted_evidence:
            common["disclosure"] = (
                "Sourced from Demo Bank / portal-scrape staging data — not yet posted to the "
                "ledger. Amounts may change once this year's transactions are reconciled and "
                "posted through the standard pipeline."
            )
        if transaction_type == "expense":
            common.update({
                "category_id": row.get("category_id") or "financial_transactions",
                "supplier_name": row.get("supplier_name") or row.get("vendor_name") or row.get("payee") or "Recorded expense",
                "invoice_number": row.get("invoice_number"),
                "is_gst_inclusive": bool(row.get("is_gst_inclusive", True)),
                "gst_amount": round(float(row.get("gst_amount") or 0), 2),
            })
        else:
            common.update({
                "source": row.get("source") or row.get("category_name") or "financial_transactions",
            })
        mapped.append(common)
    return mapped


class BulkUploadResult(BaseModel):
    """Shared response shape for all CSV bulk-upload endpoints in this router."""
    message: str
    inserted: Optional[int] = None
    updated: Optional[int] = None
    imported: Optional[int] = None
    skipped: Optional[int] = None
    not_found: Optional[List[str]] = None
    errors: List[str] = []


# ─── AUDIT-11 6f: inline response models for previously-unmodelled endpoints ──

class _LevyImpact(BaseModel):
    total_uoe: float
    proposed_admin_per_uoe_annual: float
    proposed_sinking_per_uoe_annual: float
    proposed_total_per_uoe_annual: float
    proposed_total_per_uoe_quarterly: float


class BudgetProposalResponse(BaseModel):
    """POST /budget-proposals."""
    status: str
    target_year: int
    base_year: int
    inflation_rate: float
    categories_saved: int
    admin_total: float
    sinking_total: float
    grand_total: float
    levy_impact: _LevyImpact
    levy_preview: List[dict]


class FundBalancesResponse(BaseModel):
    """POST /finance/fund-balances."""
    message: str
    admin_balance: float
    sinking_balance: float
    total: float
    financial_year: str
    as_of_date: str


class ReconcileResponse(BaseModel):
    """POST /expense-transactions/reconcile."""
    success: bool
    actual_amount: float


class SuccessAck(BaseModel):
    """Minimal success acknowledgement — endpoints that return only {success: bool}."""
    success: bool


class DcaReferResponse(BaseModel):
    """POST /arrears/{unit}/refer-dca."""
    success: bool
    dca_reference: str


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def _get_scheme_ref_from_building(building_id: str) -> SchemeRef:
    """Resolve scheme_ref from building_id (lookup in Postgres scheme table).
    
    For now, use building_id as scheme_id (1:1 mapping in Phase F).
    """
    from uuid import UUID
    try:
        scheme_id = UUID(building_id)
    except ValueError:
        # If building_id is not a UUID, try to lookup from scheme table
        # For now, raise error (should be UUID in Phase F)
        raise ValueError(f"Invalid building_id format: {building_id}")

    return SchemeRef(
        tenant_id=UUID('00000000-0000-0000-0000-000000000000'),  # TODO: Get from session context
        scheme_id=scheme_id
    )


def _normalize_annual_levy_for_response(
        levy_doc: dict,
        settings_doc: Optional[dict] = None,
        trust_config: Optional[dict] = None,
) -> dict:
    """
    Normalize ambiguous stored per-UOE fields into owner-payable API values.

    Annual levy documents still contain mixed historical per-UOE semantics across
    import paths. The API contract for `/annual-levies` is therefore normalized
    to the payable rates owners/admin users expect to see, derived from ex-GST
    fund totals plus the building GST settings.
    """
    normalized = dict(levy_doc or {})
    rates = get_levy_rate_breakdown(
        normalized,
        settings_doc=settings_doc,
        trust_config=trust_config,
    )
    normalized["admin_levy_per_uoe_annual"] = rates["admin_payable_annual"]
    normalized["admin_levy_per_uoe_quarterly"] = rates["admin_payable_quarterly"]
    normalized["sinking_levy_per_uoe_annual"] = rates["sinking_payable_annual"]
    normalized["sinking_levy_per_uoe_quarterly"] = rates["sinking_payable_quarterly"]
    return normalized


def _decode_upload_text(content: bytes) -> str:
    """Generated function header.

    Function: _decode_upload_text
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _parse_upload_float(value, default: float = 0.0) -> float:
    """Generated function header.

    Function: _parse_upload_float
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value in (None, ""):
        return default
    try:
        return round(float(str(value).strip().replace("$", "").replace(",", "")), 2)
    except (TypeError, ValueError):
        return default


def _normalize_cats_fund_type(cats: list) -> list:
    """Canonicalise every levy_categories doc's fund_type to the legacy long
    form ("administrative"/"sinking") in place, before any of this module's
    `c.get("fund_type") == "administrative"` filters run.

    Live-verified 2026-08-01: every levy_categories document actually stored
    for East Gate uses the SHORT form ("admin"/"sinking") -- this is the
    canonical form per utils/finance_helpers.py's own FUND_TYPE_NORM comment
    ("the refactored financial_categories / financial_transactions
    collections"), and it's what backend/routers/onboarding.py's generic CSV
    category-import path writes for ANY building (not an East-Gate-only
    quirk). The `== "administrative"` comparisons scattered through this file
    (admin_budgeted/admin_actual and several other cats-derived aggregates)
    were never updated to match, so they silently matched zero documents --
    admin/sinking fund "actual expense" figures read as $0.00 regardless of
    year, for every building onboarded through the standard flow. Normalising
    once, right after each `cats` fetch, fixes every downstream comparison
    without touching each one individually and risking a missed site.
    """
    for c in cats:
        c["fund_type"] = legacy_fund_type(normalise_fund_type(c.get("fund_type")))
    return cats


def _levy_category_fund_filter(value: Optional[str]) -> Optional[object]:
    """Build a storage-compatible levy_categories fund filter while keeping the
    public API contract in legacy UI terms ("administrative"/"sinking").
    """
    if not value:
        return None
    normalized = normalise_fund_type(value)
    legacy = legacy_fund_type(normalized)
    if legacy == "administrative":
        return {"$in": ["administrative", "admin"]}
    if legacy == "sinking":
        return {"$in": ["sinking", "capital_works", "capital works"]}
    return value


def _normalize_upload_fund_type(value: Optional[str]) -> Optional[str]:
    """Generated function header.

    Function: _normalize_upload_fund_type
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = (value or "").strip().lower()
    if raw in ("admin", "administrative"):
        return "administrative"
    if raw == "sinking":
        return "sinking"
    return None


def _csv_field(value: Optional[str]) -> str:
    """Generated function header.

    Function: _csv_field
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return (value or "").strip()


async def _upsert_ledger_for_payment(
        unit_number: str, year: str, amount: float, payment_id: str, building_id: str,
        fund_type: Optional[str] = None,
) -> None:
    """
    Update (or create) the unit_levy_ledger row for unit/year after a payment
    is confirmed.  Called as a background task from verify_levy_payment.

    fund_type: the payment's own fund_type ("administrative"/"admin"/"sinking"),
    if known. When it names a single fund, the full amount is attributed to that
    fund only. When absent/unrecognized (the "combined" case — also the only
    case for callers that have no fund_type concept at all, e.g. the Demo Bank
    matching queue and settlement-report imports), falls back to the previous
    ratio-split-by-current-levy-rate behaviour unchanged.
    """
    try:
        levy_doc = await db.annual_levies.find_one(
            {"year": year, "building_id": building_id}, {"_id": 0}
        )
        if not levy_doc:
            return

        admin_rate = levy_doc.get("admin_levy_per_uoe_annual", 0)
        sinking_rate = levy_doc.get("sinking_levy_per_uoe_annual", 0)

        normalized_fund = normalise_fund_type(fund_type) if fund_type else None
        if normalized_fund == "admin":
            admin_amount = round(amount, 2)
            sinking_amount = 0.0
        elif normalized_fund == "sinking":
            admin_amount = 0.0
            sinking_amount = round(amount, 2)
        else:
            total_rate = admin_rate + sinking_rate
            admin_ratio = admin_rate / total_rate if total_rate > 0 else 0.77
            admin_amount = round(amount * admin_ratio, 2)
            sinking_amount = round(amount - admin_amount, 2)

        existing = await db.unit_levy_ledger.find_one(
            {"year": year, "building_id": building_id, "unit_number": unit_number}, {"_id": 0}
        )

        # Idempotency: skip if this payment_id was already applied
        if existing and payment_id and payment_id in (existing.get("applied_payment_ids") or []):
            import logging
            logging.getLogger(__name__).info(
                "Payment %s already applied to %s/%s ledger — skipping", payment_id, unit_number, year
            )
            return

        if existing:
            new_admin_paid = round(existing.get("admin_paid", 0) + admin_amount, 2)
            new_sinking_paid = round(existing.get("sinking_paid", 0) + sinking_amount, 2)
            new_total_paid = round(existing.get("total_paid", 0) + amount, 2)

            # Normalize to annual levy amounts for closing-balance arithmetic.
            # Scraper-created records store admin_levied = quarterly_amount (one quarter
            # of the annual levy). If a full-year or multi-quarter payment arrives, using
            # the quarterly admin_levied produces a misleadingly large credit.
            # We resolve this by preferring the annual rate × UOE from levy_doc; this
            # keeps the closing formula (opening + annual_levied + interest − paid) correct
            # for any payment size. Records where UOE is unknown fall back to the stored
            # admin_levied (preserving the previous behaviour).
            _uoe = float(existing.get("uoe") or 0)
            if _uoe > 0 and admin_rate > 0:
                _ref_admin_levied = round(admin_rate * _uoe, 2)
                _ref_sinking_levied = round(sinking_rate * _uoe, 2)
            else:
                _ref_admin_levied = float(existing.get("admin_levied", 0) or 0)
                _ref_sinking_levied = float(existing.get("sinking_levied", 0) or 0)

            # Include stored interest so that closing = opening + levied + interest − paid.
            # For most units admin_interest is 0; for portal-synced records it may be non-zero
            # (representing fees/interest charges from the Strata Web portal not broken out elsewhere).
            # Downstream arithmetic (closing/net_balance) is the shared pure formula also used by
            # unit_levy_ledger_service.py's historical-charge projection rebuild — extracted here
            # byte-identically, this branch's own output is unchanged by the extraction.
            from services.unit_levy_ledger_service import compute_ledger_levied_and_closing
            _computed = compute_ledger_levied_and_closing(
                admin_opening=existing.get("admin_opening", 0),
                sinking_opening=existing.get("sinking_opening", 0),
                admin_interest=existing.get("admin_interest", 0),
                sinking_interest=existing.get("sinking_interest", 0),
                admin_paid=new_admin_paid,
                sinking_paid=new_sinking_paid,
                admin_levied=_ref_admin_levied,
                sinking_levied=_ref_sinking_levied,
            )
            update_doc = {"$set": {
                **_computed,
                "admin_paid": new_admin_paid,
                "sinking_paid": new_sinking_paid,
                "total_paid": new_total_paid,
                "updated_at": _now(),
            }}
            if payment_id:
                update_doc["$addToSet"] = {"applied_payment_ids": payment_id}
            await db.unit_levy_ledger.update_one(
                {"year": year, "building_id": building_id, "unit_number": unit_number},
                update_doc,
            )
            # Keep units.balance_owing / balance_credit in sync with the live ledger
            await db.units.update_one(
                {"building_id": building_id, "unit_number": unit_number},
                {"$set": {
                    "balance_owing": round(max(0.0, _computed["net_balance"]), 2),
                    "balance_credit": round(abs(min(0.0, _computed["net_balance"])), 2),
                    "updated_at": _now(),
                }},
            )
        else:
            # Create new ledger entry from scratch
            unit_doc = await db.units.find_one(
                {"building_id": building_id, "unit_number": unit_number},
                {"_id": 0, "entitlement": 1, "lot_number": 1, "property_type": 1},
            )
            if not unit_doc:
                return
            uoe = unit_doc.get("entitlement", 0)
            if not uoe:
                return

            prev_year = str(int(year) - 1) if year.isdigit() else None
            admin_opening = 0.0
            sinking_opening = 0.0
            if prev_year:
                prev_ledger = await db.unit_levy_ledger.find_one(
                    {"year": prev_year, "building_id": building_id, "unit_number": unit_number}, {"_id": 0}
                )
                if prev_ledger:
                    admin_opening = round(prev_ledger.get("admin_closing", 0.0), 2)
                    sinking_opening = round(prev_ledger.get("sinking_closing", 0.0), 2)

            admin_levied = round(admin_rate * uoe, 2)
            sinking_levied = round(sinking_rate * uoe, 2)
            total_levied = round(admin_levied + sinking_levied, 2)

            admin_closing = round(admin_opening + admin_levied - admin_amount, 2)
            sinking_closing = round(sinking_opening + sinking_levied - sinking_amount, 2)
            net_balance = round(admin_closing + sinking_closing, 2)

            import uuid as _uuid
            doc = {
                "id": str(_uuid.uuid4()),
                "building_id": building_id,
                "year": year,
                "unit_number": unit_number,
                "lot_number": unit_doc.get("lot_number", ""),
                "uoe": uoe,
                "property_type": unit_doc.get("property_type", ""),
                "admin_opening": admin_opening,
                "admin_levied": admin_levied,
                "admin_paid": admin_amount,
                "admin_closing": admin_closing,
                "sinking_opening": sinking_opening,
                "sinking_levied": sinking_levied,
                "sinking_paid": sinking_amount,
                "sinking_closing": sinking_closing,
                "total_levied": total_levied,
                "total_paid": round(amount, 2),
                "net_balance": net_balance,
                "applied_payment_ids": [payment_id] if payment_id else [],
                "created_at": _now(),
                "updated_at": _now(),
            }
            await db.unit_levy_ledger.replace_one(
                {"year": year, "building_id": building_id, "unit_number": unit_number},
                doc,
                upsert=True,
            )
            # Keep units.balance_owing / balance_credit in sync with the live ledger
            await db.units.update_one(
                {"building_id": building_id, "unit_number": unit_number},
                {"$set": {
                    "balance_owing": round(max(0.0, net_balance), 2),
                    "balance_credit": round(abs(min(0.0, net_balance)), 2),
                    "updated_at": _now(),
                }},
            )
    except Exception as exc:
        # Never block payment confirmation — log and continue
        import logging
        logging.getLogger(__name__).error(
            "Failed to upsert ledger after payment %s: %s", payment_id, exc
        )


# Local alias — implementation now lives in utils.finance_helpers.compute_period_due_dates
_compute_period_due_dates = compute_period_due_dates


# ─────────────────────────────────────────────────────────────────────────────
# Available years / current-levy-year resolution
# ─────────────────────────────────────────────────────────────────────────────
#
# A building's LEVY year is NOT the Australian tax financial year (Jul-Jun) —
# it defaults to the calendar year (financial_year_start_month=1) unless
# configured otherwise. annual_levies can carry a row for a not-yet-started
# levy year (e.g. an early "partial_actual" next-year budget import ahead of
# an AGM). Throughout this file, ~8 endpoints used to independently default a
# missing `year` query param via `db.annual_levies.find_one(..., sort=[("year",
# -1)])` — "whichever row sorts highest" — which happily returns that
# not-yet-started year instead of the real current one. Confirmed live (2026-
# 07-02): with a "2027" row present, this made GET /levy-status/{unit} (used
# by LevyPaymentPage.tsx with no year param), GET /arrears/detail (Arrears
# Recovery Board, no year param at all), and GET /finance/levy-kpi (whenever
# selectedYear hasn't loaded yet) resolve to 2027 and return wrong/empty
# current-year figures. annual_levies.status is NOT a reliable filter for
# this: seed/import data uses "partial_actual" to mean both "current year,
# still in progress" and "future year, prematurely imported" depending on
# data source. Comparing the year itself to today is the robust signal — every
# "no year specified" fallback in this file must resolve through
# `_resolve_current_levy_year` / `_resolve_default_levy_year` below, not a raw
# sort-desc-pick-first query.


def _year_int(y) -> Optional[int]:
    """Generated function header.

    Function: _year_int
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return int(str(y)[:4])
    except (TypeError, ValueError):
        return None


async def _resolve_current_levy_year(building_id: str, settings_doc: Optional[dict] = None) -> int:
    """
    The calendar year that is CURRENT for this building's own levy cycle today.

    Pass `settings_doc` (any projection that includes financial_year_start_month,
    or the full settings doc) when the caller has already fetched it, to avoid a
    redundant settings lookup.
    """
    if settings_doc is None:
        settings_doc = await _get_general_settings(building_id, {"_id": 0, "financial_year_start_month": 1})
    fy_start_month = int((settings_doc or {}).get("financial_year_start_month") or 1)
    now = datetime.now(timezone.utc)
    return now.year if (fy_start_month <= 1 or now.month >= fy_start_month) else now.year - 1


async def _resolve_default_levy_year(
        building_id: str,
        fallback: Optional[str] = None,
        settings_doc: Optional[dict] = None,
) -> Optional[str]:
    """
    Resolve which levy year to use when a caller doesn't specify one: the
    newest annual_levies year that is not later than this building's current
    levy year — i.e. the same "never default to a not-yet-started year" rule
    as GET /years, applied server-side wherever `year` is optional. Falls back
    to `fallback` if the building has no annual_levies row at or before the
    current levy year yet (e.g. a brand-new building).
    """
    current_levy_year = await _resolve_current_levy_year(building_id, settings_doc=settings_doc)
    years = await db.annual_levies.distinct("year", {"building_id": building_id})
    eligible = sorted(
        (y for y in years if (_year_int(y) is not None and _year_int(y) <= current_levy_year)),
        key=lambda y: _year_int(y),
        reverse=True,
    )
    return eligible[0] if eligible else fallback


@router.get("/years", response_model=List[str])
async def get_available_years(
        current_user: dict = Depends(require_feature("finance")),
        building_id: str = Depends(get_current_building)
):
    """
    Get list of levy years that have levy data, excluding any year that has
    not started yet under this building's OWN levy-year cycle (see module
    note above). Returns years sorted newest first.
    """
    current_levy_year = await _resolve_current_levy_year(building_id)

    async def _mongo_years() -> list[str]:
        return await db.annual_levies.distinct("year", {"building_id": building_id})

    # One of the very few finance reads that maps cleanly: a bare year string on both
    # sides, and no response model to satisfy beyond list[str], so there is nothing to
    # invent. Most finance routes are NOT like this — their response models encode
    # MongoDB document semantics (a verification workflow status, a quarter label, a
    # nested arrears_metadata sub-document) that the Postgres tables deliberately do not
    # model. See scripts/validation/audit_route_wireability.py.
    _read = await read_through(
        domain="finance_ledger",
        building_id=building_id,
        route="finance.available_years",
        postgres=lambda: _financial_read_service.get_available_levy_years(building_id=building_id),
        mongo=_mongo_years,
    )

    # The not-yet-started filter stays HERE for both stores. Which years are selectable
    # is a levy-cycle rule belonging to this route, not a property of the rows; applying
    # it inside the Postgres reader would be a second implementation of it.
    years = [y for y in _read.items if (_year_int(y) is None or _year_int(y) <= current_levy_year)]
    return sorted(years, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Annual Levies
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/annual-levies", response_model=List[AnnualLevyResponse])
async def get_annual_levies(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all annual levy summaries sorted by year (newest first).
    Requires can_view_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")

    levies_task = db.annual_levies.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", -1).to_list(10)
    settings_task = _get_general_settings(building_id, {"_id": 0})
    building_task = db.buildings.find_one({"id": building_id}, {"_id": 0, "trust_config": 1})
    levies, settings_doc, building_doc = await asyncio.gather(levies_task, settings_task, building_task)
    trust_config = (building_doc or {}).get("trust_config")
    return [
        AnnualLevyResponse(**_normalize_annual_levy_for_response(lv, settings_doc, trust_config))
        for lv in levies
    ]


@router.get("/annual-levies/{year}", response_model=AnnualLevyResponse)
async def get_annual_levy(
        year: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get annual levy summary for a specific year.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    levy_task = db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})
    settings_task = _get_general_settings(building_id, {"_id": 0})
    building_task = db.buildings.find_one({"id": building_id}, {"_id": 0, "trust_config": 1})
    levy, settings_doc, building_doc = await asyncio.gather(levy_task, settings_task, building_task)
    if not levy:
        raise HTTPException(status_code=404, detail=f"No levy data found for year {year}")
    return AnnualLevyResponse(
        **_normalize_annual_levy_for_response(levy, settings_doc, (building_doc or {}).get("trust_config"))
    )


@router.put("/annual-levies/{year}", response_model=MessageAck)
async def update_annual_levy(
        year: str,
        data: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update annual levy summary (e.g. when actuals are known at year end).
    Requires can_manage_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    data["updated_at"] = _now()
    result = await db.annual_levies.update_one(
        {"year": year, "building_id": building_id},
        {"$set": data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail=f"No levy data found for year {year}")
    return {"message": f"Annual levy for {year} updated"}


def _build_ledger_quality_warnings(ledger_quality: dict) -> List[str]:
    """Human-readable warnings for ledger/unit reconciliation gaps.

    Shared by /finance/summary and /finance/kpi-contract so the two
    contracts never phrase the same data-quality issue differently.
    """
    warnings: List[str] = []
    dup = ledger_quality.get("duplicate_ledger_units") or []
    missing = ledger_quality.get("missing_ledger_units") or []
    extra = ledger_quality.get("extra_ledger_units") or []
    malformed = ledger_quality.get("malformed_ledger_row_count") or 0
    if dup:
        warnings.append(
            f"{len(dup)} duplicate ledger unit(s) detected: {', '.join(dup)}"
        )
    if missing:
        warnings.append(
            f"{len(missing)} canonical unit(s) missing a ledger row for this year: {', '.join(missing)}"
        )
    if extra:
        warnings.append(
            f"{len(extra)} ledger row(s) reference units not in the canonical unit list: {', '.join(extra)}"
        )
    if malformed:
        warnings.append(
            f"{malformed} ledger row(s) had a missing/null unit_number and were excluded from reconciliation."
        )
    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Finance Summary (main dashboard endpoint)
# ─────────────────────────────────────────────────────────────────────────────

# @featuretrace:financial-ui-calculation-register — Route contract for shared finance UI labels, formulas, and source fields.
# Data flow: finance UI/debug tooling -> GET /finance/calculation-registry -> finance_calculation_registry service (scope param: building|global).
# Related: backend/services/finance_calculation_registry.py, frontend/src/lib/finance/levyDisplay.ts,
#          tests/backend/test_finance_calculation_registry.py
@router.get("/finance/calculation-registry")
async def get_finance_ui_calculation_registry(
        response: Response,
        current_user: dict = Depends(require_feature("finance")),
        building_id: str = Depends(get_current_building),
):
    """
    Return the canonical finance UI label/calculation registry.

    This is intentionally lightweight and cacheable: it consolidates common
    labels, similar labels, formulas, source fields, cache boundaries, and merge
    guidance so frontend pages do not need to keep re-defining financial terms.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    payload = get_finance_calculation_registry(building_id=building_id)
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Finance-Calculation-Registry-Version"] = payload["version"]
    response.headers["X-Finance-Calculation-Registry-Cache"] = (
        "hit" if payload["metadata"]["cache"]["hit"] else "miss"
    )
    return payload


async def _compute_grace_aware_arrears(
        *,
        year: str,
        building_id: str,
        settings_doc: Optional[dict],
        today: date,
        levy_year_int: int,
        raw_units_owing: int,
        raw_total_outstanding: float,
) -> dict:
    """Shared grace-aware arrears computation for /finance/summary and
    /finance/kpi-contract — extracted 2026-08-03 so the two endpoints cannot
    diverge again (they previously carried byte-for-byte-duplicated logic).

    Never nets one unit's credit against another unit's arrears — credits are
    tracked as a wholly separate total_credit_amount/credit_unit_count via
    get_arrears_metrics(), not subtracted from the arrears figure.
    """
    # Schedule/grace math is the shared canonical primitive (GAP-FIN-040) so this
    # endpoint and every dashboard/BI consumer derive num_overdue/in_grace_count
    # from ONE implementation. See utils.finance_helpers.compute_grace_period_counts.
    counts = compute_grace_period_counts(
        settings_doc=settings_doc, levy_year_int=levy_year_int, today=today,
    )
    grace_days = counts["grace_days"]
    total_periods = counts["total_periods"]
    computed_dates = counts["computed_dates"]
    overdue_periods = counts["overdue_periods"]
    in_grace_periods = counts["in_grace_periods"]
    num_overdue = counts["num_overdue"]
    in_grace_count = counts["in_grace_count"]

    units_owing = raw_units_owing
    total_outstanding_before = round(raw_total_outstanding, 2)
    true_arrears_amount = total_outstanding_before
    total_credit_amount = 0.0
    credit_unit_count = 0
    if levy_year_int == today.year:
        arrears = await get_arrears_metrics(
            year, num_overdue, building_id, total_periods,
            subtract_payments=True, in_grace_periods=in_grace_count,
        )
        units_owing = arrears["unit_count"]
        true_arrears_amount = round(arrears["total_amount"], 2)
        total_credit_amount = arrears.get("total_credit_amount", 0.0)
        credit_unit_count = arrears.get("credit_unit_count", 0)

    # The in-grace portion is whatever get_arrears_metrics already subtracted
    # from the raw positive-balance total to reach true_arrears_amount — no
    # separate Mongo pipeline needed (removed 2026-08-03; this is equivalent
    # and avoids a second full-ledger aggregation per request).
    in_grace_amount = round(max(0.0, total_outstanding_before - true_arrears_amount), 2) if in_grace_count > 0 else 0.0

    return {
        "grace_days": grace_days,
        "total_periods": total_periods,
        "computed_dates": computed_dates,
        "overdue_periods": overdue_periods,
        "in_grace_periods": in_grace_periods,
        "num_overdue": num_overdue,
        "in_grace_count": in_grace_count,
        "units_owing": units_owing,
        "total_outstanding": true_arrears_amount,
        "true_arrears_amount": true_arrears_amount,
        "in_grace_amount": in_grace_amount,
        "total_credit_amount": total_credit_amount,
        "credit_unit_count": credit_unit_count,
    }


@router.get("/finance/summary")
async def get_finance_summary(
        year: Optional[str] = None,
        current_user: dict = Depends(require_feature("finance")),
        building_id: str = Depends(get_current_building)
):
    """
    Get finance summary for a given year.

    Returns fund totals, levy rates, budget vs actual, payment status summary.
    Defaults to most recent year if not specified.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Gates a PARTIAL override, not a full swap (unlike building_overview): even when
    # source=="postgres" this route still does the full Mongo read below for every field
    # not explicitly overridden — settings, annual_levies (proposed budgets, payment
    # schedule, status), levy_categories, levy_payments, portal snapshot. Only
    # unit_ledger_summary money figures, admin/sinking actual_expenses (never
    # budgeted_expenses — see the override site below), grace-aware arrears, and
    # ledger_quality are gated on PG readiness. "postgres" here does not mean
    # PG-primary/Mongo-DR-only the way building_overview's docstring implies for its route.
    route_state = await get_finance_route_runtime_state(
        building_id=building_id, route_key="finance.summary",
    )

    # Task #17 diagnostic — record this invocation so a same-building empty-ledger
    # occurrence further down can report how many finance.summary calls overlapped it.
    # Pruned here too (not just at the failure site) so the list never grows unbounded
    # on the happy path.
    _now_ts = time.monotonic()
    _recent_summary_calls[building_id] = [
        t for t in _recent_summary_calls[building_id] if _now_ts - t < 5.0
    ]
    _recent_summary_calls[building_id].append(_now_ts)

    # Performance Optimization⚡: Parallelize levy data and building settings to reduce sequential RTs
    settings_task = _get_general_settings(building_id, {"_id": 0})
    if year:
        levy_task = db.annual_levies.find_one({"building_id": building_id, "year": year}, {"_id": 0})
        levy, settings_doc = await asyncio.gather(levy_task, settings_task)
        resolved_year = year
    else:
        # No year specified — must resolve via _resolve_default_levy_year (never a
        # not-yet-started year), not a raw sort=[("year", -1)] query — see module note
        # above get_available_years.
        settings_doc = await settings_task
        resolved_year = await _resolve_default_levy_year(building_id, settings_doc=settings_doc)
        levy = (
            await db.annual_levies.find_one({"building_id": building_id, "year": resolved_year}, {"_id": 0})
            if resolved_year else None
        )

    if not levy:
        # Diagnostic (added 2026-07-12, corrected same day): this branch `return`s before
        # ever reaching the maybe_run_finance_shadow() call further down this function, so
        # it CANNOT be the source of a "finance.summary" shadow_diffs row (route_key
        # "finance.summary" is only ever triggered from that later call site) — the
        # original comment here wrongly assumed it was. Kept as a genuinely separate,
        # still-useful signal for "annual_levies truly missing for this
        # building/year" (a real condition, just not the one that produced the
        # 2026-07-12 08:05-08:07 UTC shadow-diff burst). See the corrected diagnostic
        # at the `if not ledger_summary_res:` check below — that is the branch capable
        # of producing that specific field_mismatch.
        logger.warning(
            "finance.summary: annual_levies not found — building_id=%r year_param=%r "
            "resolved_year=%r", building_id, year, resolved_year,
        )
        # No accounting data yet — still return portal snapshot so the collection-rate
        # page can display the Strata Web Portal Snapshot card after a scraper run.
        # ledger_quality is still meaningful here: it reports the canonical unit count
        # (and any orphaned ledger rows) even before an annual_levies document exists.
        portal_fallback_task = db.building_summaries.find_one(
            {"building_id": building_id}, {"_id": 0}
        )
        ledger_quality_task = get_ledger_quality(resolved_year or "N/A", building_id)
        portal_fallback, ledger_quality = await asyncio.gather(portal_fallback_task, ledger_quality_task)
        return {
            "year": resolved_year or "N/A",
            "admin_fund": {},
            "sinking_fund": {},
            "payment_schedule": [],
            "levy_rates": {},
            "unit_ledger_summary": {},
            "in_grace_summary": {},
            "ledger_quality": ledger_quality,
            "warnings": _build_ledger_quality_warnings(ledger_quality),
            "portal_summary": {
                "arrears_total": round(float(portal_fallback.get("arrears_total") or 0), 2),
                "credit_total": round(float(portal_fallback.get("credit_total") or 0), 2),
                "arrears_count": portal_fallback.get("arrears_count", 0),
                "credit_count": portal_fallback.get("credit_count", 0),
                "clear_count": portal_fallback.get("clear_count", 0),
                "total_lots": portal_fallback.get("total_lots", 0),
                "collection_rate": portal_fallback.get("collection_rate"),
                "risk_level": portal_fallback.get("risk_level"),
                "updated_at": portal_fallback.get("updated_at"),
            } if portal_fallback else None,
        }

    year = levy["year"]

    # 1. Levy categories for budget vs actual
    cats_task = db.levy_categories.find(
        {"year": year, "building_id": building_id}, {"_id": 0}
    ).to_list(100)

    # 2. Unit ledger summary aggregation
    ledger_pipeline = [
        {"$match": {"year": year, "building_id": building_id}},
        {"$group": {
            "_id": None,
            "total_levied": {"$sum": "$total_levied"},
            "total_paid": {"$sum": "$total_paid"},
            "total_net_balance": {"$sum": "$net_balance"},
            "units_owing": {"$sum": {"$cond": [{"$gt": ["$net_balance", 0]}, 1, 0]}},
            "units_credit": {"$sum": {"$cond": [{"$lt": ["$net_balance", 0]}, 1, 0]}},
            "units_paid_up": {"$sum": {"$cond": [{"$eq": ["$net_balance", 0]}, 1, 0]}},
            "total_outstanding": {"$sum": {"$cond": [{"$gt": ["$net_balance", 0]}, "$net_balance", 0]}},
        }}
    ]
    ledger_task = db.unit_levy_ledger.aggregate(ledger_pipeline).to_list(1)

    # 3. Direct payment totals from levy_payments (authoritative per-status breakdown)
    payments_pipeline = [
        {"$match": {"year": year, "building_id": building_id}},
        {"$group": {
            "_id": "$status",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1}
        }}
    ]
    payments_task = db.levy_payments.aggregate(payments_pipeline).to_list(10)

    # 4. Period status (overdue/in-grace) is computed once, alongside the corrected
    # arrears figures, via _compute_grace_aware_arrears() below — shared with
    # /finance/kpi-contract so the two endpoints cannot diverge.
    today = date.today()
    try:
        levy_year_int = int(year)
    except (ValueError, TypeError):
        levy_year_int = today.year

    # ledger_quality is the only piece of the parallel fetch below gated on
    # route_state["source"] — mirrors arrears_detail's _arrears_ledger_task pattern
    # (only the ledger INPUT is re-sourced; canonical_status_counts consumers below
    # are unchanged). get_canonical_ledger_quality returns the same key shape as
    # Mongo's get_ledger_quality, so _build_ledger_quality_warnings needs no changes.
    async def _ledger_quality_task() -> dict:
        if route_state["source"] != "postgres":
            return await get_ledger_quality(year, building_id)
        try:
            pg_quality = await _financial_read_service.get_canonical_ledger_quality(
                building_id=building_id, financial_year=year,
            )
        except Exception as exc:
            logger.warning("finance.summary: PG ledger_quality fetch failed, falling back to Mongo: %s", exc)
            pg_quality = None
        if pg_quality is not None:
            return pg_quality
        return await get_ledger_quality(year, building_id)

    # 5. Parallel execute independent database queries
    tasks = [
        cats_task,
        ledger_task,
        payments_task,
        db.building_summaries.find_one({"building_id": building_id}, {"_id": 0}),
        _ledger_quality_task(),
        get_collection_rate_metrics(year, building_id),
    ]
    ledger_quality_idx = 4

    # Execute all independent tasks in parallel - Reduces sequential RTs from 4 to 1.
    results = await asyncio.gather(*tasks)

    cats = _normalize_cats_fund_type(results[0])
    ledger_summary_res = results[1]
    payments_by_status = results[2]
    portal_doc = results[3]
    ledger_quality = results[ledger_quality_idx]
    collection_rate_metrics = results[5]

    if not ledger_summary_res:
        # Diagnostic (2026-07-12, corrected): the 2026-07-12 08:05-08:07 UTC shadow-diff
        # burst (field_mismatch:critical on unit_ledger_summary.total_levied, building
        # 13195) CANNOT originate from the `if not levy:` branch above — that branch
        # `return`s before ever reaching the maybe_run_finance_shadow() call at the
        # bottom of this function (route_key="finance.summary" is only ever triggered
        # from there). The prior diagnostic commit (f27c9842) instrumented the wrong
        # branch. The only code path that can produce that specific field diff is THIS
        # one: annual_levies found, but the unit_levy_ledger aggregate for the same
        # year/building_id came back empty, so `ls = {}` and total_levied renders as
        # 0.0 while Postgres has real data. Direct query confirmed unit_levy_ledger has
        # 87 rows/year for 13195 across 2021-2027 with consistent string-typed `year`
        # fields, so this is not a data-shape bug — log + a same-request
        # count_documents re-check so the next occurrence pins down whether it's a
        # genuine transient query miss (count also 0 here) or something downstream.
        recheck_count = await db.unit_levy_ledger.count_documents(
            {"year": year, "building_id": building_id}
        )
        # Task #17 diagnostic (2026-07-13): standalone Mongo (no replica set — confirmed
        # live via `hello`), no cold-start/fork correlation (occurred 13h+ into a stable,
        # unforked worker uptime), and a 180-call concurrent-read reproduction against
        # real data did not reproduce this alone — so the next data point is whether
        # *this specific request* overlapped with other finance.summary calls for the
        # same building. Prune to a 5s window and count.
        now_ts = time.monotonic()
        _recent_summary_calls[building_id] = [
            t for t in _recent_summary_calls[building_id] if now_ts - t < 5.0
        ]
        overlapping_calls = len(_recent_summary_calls[building_id])
        logger.warning(
            "finance.summary: unit_levy_ledger aggregate empty despite annual_levies "
            "found — building_id=%r year=%r ledger_summary_res=%r recheck_count=%d "
            "overlapping_summary_calls_5s=%d",
            building_id, year, ledger_summary_res, recheck_count, overlapping_calls,
        )

    ls = ledger_summary_res[0] if ledger_summary_res else {}
    # Ensure ls is mutable for potential overrides below
    ls = dict(ls)

    # total_levied/total_paid: lowest-risk PG override in this route -- total_levied is
    # already the one field _compare_summary_payloads validates today (mongo
    # unit_ledger_summary.total_levied <-> pg levy_budgeted_cents). Never overrides
    # total_outstanding here -- that's the grace-aware arrears override further below,
    # a different (due-date-aware) concept from get_oc_levy_summary's own
    # total_outstanding (a due-date-agnostic cumulative AR-GL balance).
    if route_state["source"] == "postgres":
        try:
            pg_oc_levy = await _financial_read_service.get_oc_levy_summary(
                building_id=building_id, financial_year=year,
            )
        except Exception as exc:
            logger.warning("finance.summary: PG oc_levy_summary fetch failed, keeping Mongo: %s", exc)
            pg_oc_levy = None
        if pg_oc_levy is not None:
            ls["total_levied"] = pg_oc_levy["total_budgeted"]
            ls["total_paid"] = pg_oc_levy["total_collected"]

    # GAP-FIN-014: units_paid_up/units_credit must reflect canonical distinct
    # units, not a raw unit_levy_ledger aggregate count (which double-counts
    # duplicate rows and undercounts/overcounts vs the true unit roster —
    # the "59 of 156 units" class of bug). units_owing keeps this same
    # canonical default too, but the current-year arrears override below
    # (a distinct, already-canonical-units-based "in arrears" definition)
    # takes precedence for units_owing when it applies.
    canonical_status_counts = ledger_quality["canonical_status_counts"]
    ls["units_paid_up"] = canonical_status_counts["paid_up"]
    ls["units_credit"] = canonical_status_counts["credit"]
    ls["units_owing"] = canonical_status_counts["owing"]

    # Build collected_summary from levy_payments by status.
    # NOTE: levy_payments tracks portal/Stripe payments only (not DEFT/bank imports).
    # Authoritative "Total Collected" = unit_levy_ledger.total_paid (in unit_ledger_summary).
    portal_confirmed_total = 0.0
    confirmed_count = 0
    pending_total = 0.0
    pending_count = 0
    for row in payments_by_status:
        if row["_id"] == "confirmed":
            portal_confirmed_total = round(row["total"], 2)
            confirmed_count = row["count"]
        elif row["_id"] == "pending_verification":
            pending_total = round(row["total"], 2)
            pending_count = row["count"]

    admin_budgeted_from_categories = round(
        sum(c.get("budgeted_amount", 0) for c in cats if c.get("fund_type") == "administrative"),
        2,
    )
    sinking_budgeted_from_categories = round(
        sum(c.get("budgeted_amount", 0) for c in cats if c.get("fund_type") == "sinking"),
        2,
    )

    # GAP-FIN-030 Root Cause B (2026-08-02): admin_actual/sinking_actual previously summed
    # only the stale levy_categories.actual_amount field, which nothing keeps in sync
    # (written solely by the manual /expense-transactions/reconcile endpoint) -- causing
    # this summary tile to disagree with the "Budget vs Actual" chart on the same page,
    # which already derives actuals live from financial_transactions via
    # _resolve_category_actual_amount()/_get_actual_overrides_from_financial_transactions().
    # Reuse that exact same live-aggregate path here so both agree by construction; it
    # transparently falls back to the legacy actual_amount field when no transactions
    # exist for a category, so this is a strict improvement, never a regression.
    tx_actual_by_id, tx_actual_by_name = await _get_actual_overrides_from_financial_transactions(
        year=year,
        building_id=building_id,
    )
    admin_actual = round(sum(
        _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
        for c in cats if c.get("fund_type") == "administrative"
    ), 2)
    sinking_actual = round(sum(
        _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
        for c in cats if c.get("fund_type") == "sinking"
    ), 2)
    mongo_admin_actual = admin_actual
    mongo_sinking_actual = sinking_actual
    mongo_total_actual = round(mongo_admin_actual + mongo_sinking_actual, 2)

    # PG override for actual_expenses/total_expenses ONLY -- never budgeted_expenses.
    # Postgres's only fund-level "budget" concept (get_oc_levy_summary's
    # admin_fund_budgeted) is "sum of what was actually LEVIED" (finance.levy_items),
    # while Mongo's budgeted_expenses is "sum of levy_categories.budgeted_amount", a
    # planning figure entered by staff through one of several independent write paths --
    # not guaranteed to equal what was levied. Same treatment as the existing
    # total_outstanding exclusion in _compare_building_overview_payloads: never force
    # these two concepts to agree.
    if route_state["source"] == "postgres":
        try:
            pg_fund_expenses = await _financial_read_service.get_fund_expense_totals(
                building_id=building_id, financial_year=year,
            )
        except Exception as exc:
            logger.warning("finance.summary: PG fund expense fetch failed, keeping Mongo: %s", exc)
            pg_fund_expenses = None
        if pg_fund_expenses is not None:
            pg_admin_actual = _aud_from_cents(pg_fund_expenses["admin_expense_cents"])
            pg_sinking_actual = _aud_from_cents(pg_fund_expenses["sinking_expense_cents"])
            populated_mongo_split = mongo_total_actual > 0
            pg_lost_sinking_split = mongo_sinking_actual > 0 and pg_sinking_actual == 0 and pg_admin_actual > 0
            if populated_mongo_split and pg_lost_sinking_split:
                logger.warning(
                    "finance.summary: PG fund expense totals conflict with category actuals; "
                    "keeping category totals. building_id=%s year=%s "
                    "mongo_admin=%.2f mongo_sinking=%.2f pg_admin=%.2f pg_sinking=%.2f pg_unassigned=%.2f",
                    building_id,
                    year,
                    mongo_admin_actual,
                    mongo_sinking_actual,
                    pg_admin_actual,
                    pg_sinking_actual,
                    _aud_from_cents(pg_fund_expenses.get("unassigned_expense_cents", 0)),
                )
            else:
                admin_actual = pg_admin_actual
                sinking_actual = pg_sinking_actual

    # Grace-aware arrears — shared with /finance/kpi-contract so the two
    # endpoints cannot diverge (see _compute_grace_aware_arrears docstring).
    # Never nets one unit's credit against another unit's arrears.
    grace_result = await _compute_grace_aware_arrears(
        year=year,
        building_id=building_id,
        settings_doc=settings_doc,
        today=today,
        levy_year_int=levy_year_int,
        raw_units_owing=ls.get("units_owing", 0),
        raw_total_outstanding=ls.get("total_outstanding", 0),
    )
    grace_days = grace_result["grace_days"]
    total_periods = grace_result["total_periods"]
    overdue_periods = grace_result["overdue_periods"]
    in_grace_periods = grace_result["in_grace_periods"]
    in_grace_count = grace_result["in_grace_count"]
    ls["units_owing"] = grace_result["units_owing"]
    ls["total_outstanding"] = grace_result["total_outstanding"]
    ls["units_credit"] = grace_result["credit_unit_count"]
    canonical_unit_count = int(ledger_quality.get("canonical_unit_count") or 0)
    if canonical_unit_count:
        # Backend status buckets stay mutually exclusive. UI "clear" displays can
        # combine paid_up + credit, but this contract keeps paid_up + owing +
        # credit == canonical_unit_count.
        ls["units_paid_up"] = max(
            0,
            canonical_unit_count - int(ls.get("units_owing") or 0) - int(ls.get("units_credit") or 0),
        )
    in_grace_amount = grace_result["in_grace_amount"]
    true_arrears_amount = grace_result["true_arrears_amount"]
    total_credit_amount = grace_result["total_credit_amount"]

    # PG override for arrears totals only -- grace_days/total_periods/overdue_periods/
    # in_grace_periods/in_grace_count stay as computed above (settings-derived period
    # LABELS, e.g. ["Q1","Q2"], which current PG data can't honestly produce yet: live
    # finance.levy_runs only has one annual roll-up row per year, not real per-quarter
    # rows -- a data-population gap, not something to fabricate around here). Sources
    # the same already-verified get_arrears_summary(grace_aware=True) call used by
    # arrears_detail -- never get_oc_levy_summary's or get_finance_summary's own
    # total_outstanding/levy_arrears_total, which are a due-date-agnostic cumulative
    # AR-GL balance, a third and different arrears concept.
    if route_state["source"] == "postgres":
        try:
            pg_arrears_grace = await _financial_read_service.get_arrears_summary(
                building_id=building_id, financial_year=year, grace_aware=True,
            )
            pg_arrears_all = await _financial_read_service.get_arrears_summary(
                building_id=building_id, financial_year=year, grace_aware=False,
            )
        except Exception as exc:
            logger.warning("finance.summary: PG arrears fetch failed, keeping Mongo: %s", exc)
            pg_arrears_grace = None
            pg_arrears_all = None
        if pg_arrears_grace is not None:
            true_arrears_amount = _aud_from_cents(pg_arrears_grace["total_arrears_cents"])
            ls["units_owing"] = pg_arrears_grace["units_in_arrears"]
            ls["total_outstanding"] = true_arrears_amount
            if canonical_unit_count:
                ls["units_paid_up"] = max(
                    0,
                    canonical_unit_count - int(ls.get("units_owing") or 0) - int(ls.get("units_credit") or 0),
                )
            if pg_arrears_all is not None:
                # Mirrors Mongo's own before/after pattern: in_grace_amount is the slice
                # of all-unpaid that hasn't reached its grace deadline yet, i.e. the
                # difference between the due-date-agnostic and grace-aware totals.
                in_grace_amount = max(
                    0.0,
                    round(_aud_from_cents(pg_arrears_all["total_arrears_cents"]) - true_arrears_amount, 2),
                )

    # For partial/current years, the ledger and annual_levies income fields can contain
    # only YTD actuals (e.g. Q1/Q2). Expose proposed_income/annual_levy_proposed
    # unconditionally so the UI never has to guess whether raw levy_income is annual or
    # YTD. When category rows are missing, budgeted_expenses falls back to the selected
    # annual_levies proposed fund total; that is a data-quality fallback, not proof that
    # category-level proposed spend was configured.
    proposed_admin = float(levy.get("proposed_admin_expenses") or 0)
    proposed_sinking = float(levy.get("proposed_sinking_expenses") or 0)

    # admin_fund/sinking_fund below are spread directly from the annual_levies document —
    # their levy_income/total_income fields are YTD actuals, not the full-year budget
    # (see get_levy_proposed_amounts()'s docstring). annual_levy_proposed is added here,
    # unconditionally, via that same canonical helper so any consumer that needs "the real
    # annual total" has an unambiguous field to read instead of risking levy_income being
    # misread as annual — CLAUDE.md documents this exact YTD-vs-annual mix-up as a
    # recurring bug source in this codebase.
    admin_annual_proposed, sinking_annual_proposed = get_levy_proposed_amounts(levy)
    gst_config = parse_levy_gst_settings(settings_doc)
    annual_levy_total_ex_gst = round(admin_annual_proposed + sinking_annual_proposed, 2)
    annual_levy_total_inc_gst = round(annual_levy_total_ex_gst * gst_config["gst_multiplier"], 2)
    admin_budgeted = admin_budgeted_from_categories or round(admin_annual_proposed, 2)
    sinking_budgeted = sinking_budgeted_from_categories or round(sinking_annual_proposed, 2)
    is_partial = levy.get("status") == "partial_actual"

    admin_fund_extra = {
        "budgeted_expenses": admin_budgeted,
        # actual_expenses (existing key, kept for any other consumer) and total_expenses
        # (the key FinancePage.tsx's tile actually reads) must carry the SAME corrected
        # value -- previously only actual_expenses was set here, while total_expenses
        # silently fell through to whatever (usually unset) value the raw annual_levies
        # passthrough below carried, showing $0.00 regardless of admin_actual.
        "actual_expenses": admin_actual,
        "total_expenses": admin_actual,
        "annual_levy_proposed": round(admin_annual_proposed, 2),
        "proposed_income": round(admin_annual_proposed or proposed_admin, 2),
    }
    sinking_fund_extra = {
        "budgeted_expenses": sinking_budgeted,
        "actual_expenses": sinking_actual,
        "total_expenses": sinking_actual,
        "annual_levy_proposed": round(sinking_annual_proposed, 2),
        "proposed_income": round(sinking_annual_proposed or proposed_sinking, 2),
    }
    # ytd_levy_income/ytd_total_income are only meaningful — and only added — for a
    # partial/in-progress year with a real proposed budget to contrast against; a
    # fully confirmed (or not-yet-reconciled, status=null) year has no "YTD vs
    # annual" ambiguity to flag, and the field would just be a confusing, non-
    # actionable duplicate of annual_levy_proposed/proposed_income above.
    # GAP-FIN-033 Part A1: `9314759fc` (2026-08-02) made this unconditional, which
    # is what produced the null-crash on /finance/summary — a year whose status is
    # null (not "partial_actual", East Gate FY2026's real live state) can still
    # store levy_income/total_income as an explicit null, and round(None) raised.
    # Restoring the original gate removes the crash at its root for the common
    # case; the `or 0` guard below is kept as defense-in-depth for the gated case.
    if is_partial and proposed_admin > 0:
        admin_fund_extra["ytd_levy_income"] = round(levy.get("admin_fund", {}).get("levy_income") or 0, 2)
        admin_fund_extra["ytd_total_income"] = round(levy.get("admin_fund", {}).get("total_income") or 0, 2)
    if is_partial and proposed_sinking > 0:
        sinking_fund_extra["ytd_levy_income"] = round(levy.get("sinking_fund", {}).get("levy_income") or 0, 2)
        sinking_fund_extra["ytd_total_income"] = round(levy.get("sinking_fund", {}).get("total_income") or 0, 2)

    # Fetch portal snapshot — additive only, never affects levy accounting.
    # building_summaries is written exclusively by the Strata Web scraper.
    # Performance Optimization⚡: Hoisted from sequential execution to parallel execution above.

    _summary_response = {
        "year": year,
        "status": levy.get("status"),
        # admin_fund/sinking_fund spread the raw annual_levies sub-document — levy_income/
        # total_income there are YTD actuals; use annual_levy_proposed (above) for the
        # full-year figure, or unit_ledger_summary.total_levied for the combined total.
        "admin_fund": {
            **levy.get("admin_fund", {}),
            **admin_fund_extra,
        },
        "sinking_fund": {
            **levy.get("sinking_fund", {}),
            **sinking_fund_extra,
        },
        "payment_schedule": levy.get("payment_schedule", []),
        "levy_rates": {
            "admin_per_uoe_annual": levy.get("admin_levy_per_uoe_annual"),
            "admin_per_uoe_quarterly": levy.get("admin_levy_per_uoe_quarterly"),
            "sinking_per_uoe_annual": levy.get("sinking_levy_per_uoe_annual"),
            "sinking_per_uoe_quarterly": levy.get("sinking_levy_per_uoe_quarterly"),
            "total_uoe": levy.get("total_uoe", TOTAL_UOE),
        },
        "unit_ledger_summary": {
            "total_levied": round(ls.get("total_levied", 0), 2),
            # total_paid/total_collected_ytd (GAP-FIN-035, 2026-08-03): per-unit-clamped,
            # due-date Collection Rate basis — never includes a not-yet-due advance
            # payment. Previously `levied - Σ(signed net_balance)`, an aggregate that
            # collapses into an unclamped, cross-unit-netted Σpaid_i the instant one
            # unit pays ahead of its own due schedule. See
            # domain.finance.formulas.collection.due_date_collection_rate() and
            # docs/architecture/financial-summary-analysis-of-issues.md Rule 53.
            "total_paid": collection_rate_metrics["collected_to_date"],
            "raw_total_paid": round(ls.get("total_paid", 0), 2),
            "net_balance": round(ls.get("total_net_balance", 0), 2),
            "units_owing": ls.get("units_owing", 0),
            "units_credit": ls.get("units_credit", 0),
            "units_paid_up": ls.get("units_paid_up", 0),
            "total_outstanding": round(ls.get("total_outstanding", 0), 2),
            # total_credit_amount: the actual accumulated advance-payment/credit balance
            # across units in credit — never subtracted from total_outstanding/arrears
            # above; a unit's overpayment does not reduce any other unit's arrears.
            "total_credit_amount": total_credit_amount,
            # collected_in_advance: same figure as total_credit_amount above, exposed
            # under the metric-3 name this endpoint's consumers should read for the
            # Collection Rate page's "collected in advance" sub-text.
            "collected_in_advance": collection_rate_metrics["collected_in_advance"],
            "total_collected_ytd": collection_rate_metrics["collected_to_date"],
            "collection_rate_due_to_date_pct": collection_rate_metrics["collection_rate_pct"],
            "due_to_date": collection_rate_metrics["due_to_date"],
            # annual_levy_total = the full-year proposed budget (admin + sinking), NOT
            # admin_fund/sinking_fund.total_income — those are YTD actuals for partial
            # years and understate the annual figure (GAP-FIN-014 fix; see
            # get_levy_proposed_amounts()'s docstring for the proposed-amount tiers).
            "annual_levy_total": annual_levy_total_ex_gst,
            "annual_levy_total_inc_gst": annual_levy_total_inc_gst,
        },
        "collected_summary": {
            # portal_confirmed_total = payments made via the portal (Stripe/DEFT in-platform).
            # Authoritative total is unit_ledger_summary.total_paid (includes all external payments).
            "portal_confirmed_total": portal_confirmed_total,
            "pending_total": pending_total,
            "confirmed_count": confirmed_count,
            "pending_count": pending_count,
        },
        "period_status": {
            "any_overdue": len(overdue_periods) > 0,
            "overdue_periods": overdue_periods,  # list of labels e.g. ["Q1", "Q2"]
            "overdue_count": len(overdue_periods),  # integer count for easy frontend math
            "total_periods": total_periods,  # total billing periods this year
            "grace_period_days": grace_days,
            "in_grace_periods": in_grace_periods,  # periods past due but within grace window
            "in_grace_count": in_grace_count,
        },
        "in_grace_summary": {
            # Amounts past their due date but still within the grace window.
            # Owners may have paid via DEFT/BPAY but reconciliation is pending.
            # This is NOT counted as hard arrears until the grace deadline passes.
            "in_grace_periods": in_grace_periods,
            "in_grace_count": in_grace_count,
            "in_grace_amount": in_grace_amount,
            "true_arrears_amount": true_arrears_amount,
            "grace_period_days": grace_days,
        },
        # Portal snapshot — sourced from building_summaries (written by Strata Web scraper).
        # Read-only cross-check; does NOT replace unit_ledger_summary figures.
        "portal_summary": {
            "arrears_total": round(float(portal_doc.get("arrears_total") or 0), 2),
            "credit_total": round(float(portal_doc.get("credit_total") or 0), 2),
            "arrears_count": portal_doc.get("arrears_count", 0),
            "credit_count": portal_doc.get("credit_count", 0),
            "clear_count": portal_doc.get("clear_count", 0),
            "total_lots": portal_doc.get("total_lots", 0),
            "collection_rate": portal_doc.get("collection_rate"),
            "risk_level": portal_doc.get("risk_level"),
            "updated_at": portal_doc.get("updated_at"),
        } if portal_doc else None,
        "ledger_quality": ledger_quality,
        "warnings": _build_ledger_quality_warnings(ledger_quality),
    }

    # route_state was already resolved at the top of this function (gates the
    # unit_ledger_summary/actual_expenses/arrears/ledger_quality overrides above).
    if route_state.get("run_shadow"):
        asyncio.create_task(maybe_run_finance_shadow(
            building_id=building_id,
            route_key="finance.summary",
            mongo_payload=_summary_response,
        ))
    return _summary_response


# ─────────────────────────────────────────────────────────────────────────────
# Finance KPI Contract (GAP-FIN-014 — ledger source-of-truth reconciliation)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/kpi-contract")
async def get_finance_kpi_contract(
        year: Optional[str] = None,
        current_user: dict = Depends(require_feature("finance")),
        building_id: str = Depends(get_current_building)
):
    """
    Ledger-derived finance KPI contract (GAP-FIN-014).

    Additive alongside /finance/summary — does not replace it. Building-wide
    collection rate, arrears split and unit-status distribution are computed
    here, backend-side, from unit_levy_ledger reconciled against the
    canonical `units` roster, so operational pages (FinancePage,
    CollectionRatePage) stop independently re-deriving these numbers
    client-side, which is how the same building/year can show different
    figures on different pages. Portal/scraper data is surfaced only as a
    labelled cross-check with an explicit reconciliation delta — it never
    feeds into unit_counts/collection_mix/arrears_metrics directly.

    Mongo-only. Unrelated to the Postgres shadow-read/cutover machinery used
    elsewhere in this router.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    settings_task = _get_general_settings(building_id, {"_id": 0})
    if year:
        levy_task = db.annual_levies.find_one({"building_id": building_id, "year": year}, {"_id": 0})
        levy, settings_doc = await asyncio.gather(levy_task, settings_task)
    else:
        settings_doc = await settings_task
        year = await _resolve_default_levy_year(building_id, settings_doc=settings_doc)
        levy = (
            await db.annual_levies.find_one({"building_id": building_id, "year": year}, {"_id": 0})
            if year else None
        )
    year = year or "N/A"

    today = date.today()
    try:
        levy_year_int = int(year)
    except (ValueError, TypeError):
        levy_year_int = today.year

    tasks = [
        get_ledger_quality(year, building_id),
        get_unit_ledger_stats(year, building_id),
        db.building_summaries.find_one({"building_id": building_id}, {"_id": 0}),
        get_collection_rate_metrics(year, building_id),
    ]
    results = await asyncio.gather(*tasks)
    ledger_quality = results[0]
    ledger_stats = results[1]
    portal_doc = results[2]
    collection_rate_metrics = results[3]

    canonical_counts = ledger_quality["canonical_status_counts"]

    ledger_levied_ytd = round(ledger_stats.get("total_levied", 0), 2)
    ledger_paid_ytd = round(ledger_stats.get("total_paid", 0), 2)
    # GAP-FIN-035 (2026-08-03): "collected" here means the due-date Collection
    # Rate metric — per-unit-clamped so one unit's advance payment for a
    # not-yet-due period can never inflate another unit's or the building's
    # collected-to-date figure. Previously derived as
    # `ledger_levied_ytd - ledger_stats["net_balance"]` (an aggregate signed
    # sum across all units), which collapses into an unclamped, cross-unit-
    # netted Σpaid_i the instant one unit pays ahead of its own due schedule.
    # See domain.finance.formulas.collection.due_date_collection_rate() and
    # docs/architecture/financial-summary-analysis-of-issues.md Rule 53:
    # "Collection performance is not fund health" — this is a DIFFERENT
    # metric from fund_health/current_year_collection_rate() below.
    ledger_collected_ytd = collection_rate_metrics["collected_to_date"]
    collected_in_advance = collection_rate_metrics["collected_in_advance"]

    # Grace-aware arrears — shared with /finance/summary so the two endpoints
    # cannot diverge (see _compute_grace_aware_arrears docstring). Never nets
    # one unit's credit against another unit's arrears.
    grace_result = await _compute_grace_aware_arrears(
        year=year,
        building_id=building_id,
        settings_doc=settings_doc,
        today=today,
        levy_year_int=levy_year_int,
        raw_units_owing=canonical_counts["owing"],
        raw_total_outstanding=ledger_stats.get("total_outstanding", 0),
    )
    total_outstanding_val = grace_result["total_outstanding"]
    units_owing = grace_result["units_owing"]
    in_grace_amount = grace_result["in_grace_amount"]
    true_arrears_amount = grace_result["true_arrears_amount"]
    total_credit_amount = grace_result["total_credit_amount"]

    annual_levy_total = 0.0
    if levy:
        admin_annual_proposed, sinking_annual_proposed = get_levy_proposed_amounts(levy)
        annual_levy_total = round(admin_annual_proposed + sinking_annual_proposed, 2)
    not_yet_due_amount = round(
        max(0.0, annual_levy_total - ledger_collected_ytd - in_grace_amount - true_arrears_amount), 2
    )

    portal_arrears_total = round(float(portal_doc.get("arrears_total") or 0), 2) if portal_doc else 0.0
    requires_reconciliation = (
        portal_doc is not None and abs(portal_arrears_total - true_arrears_amount) > 0.01
    )

    warnings = _build_ledger_quality_warnings(ledger_quality)
    if requires_reconciliation:
        warnings.append(
            f"Portal arrears (${portal_arrears_total:.2f}) differ from ledger arrears "
            f"(${true_arrears_amount:.2f}) by ${abs(portal_arrears_total - true_arrears_amount):.2f} — "
            "reconciliation required before treating the portal figure as truth."
        )

    return {
        "year": year,
        "source_contract_version": "ledger-kpi-v1",
        "ledger_quality": ledger_quality,
        "unit_counts": {
            "canonical_unit_count": ledger_quality["canonical_unit_count"],
            "paid_up": canonical_counts["paid_up"],
            "owing": units_owing,
            "credit": canonical_counts["credit"],
            "missing_ledger": len(ledger_quality["missing_ledger_units"]),
            "duplicate_ledger_rows": ledger_quality["duplicate_ledger_row_count"],
        },
        "collection_mix": {
            "ledger_levied_ytd": ledger_levied_ytd,
            "ledger_paid_ytd": ledger_paid_ytd,
            # Due-date Collection Rate basis (metric 1) — per-unit-clamped, never
            # includes a not-yet-due advance payment. See GAP-FIN-035.
            "ledger_collected_ytd": ledger_collected_ytd,
            "collection_rate_due_to_date_pct": collection_rate_metrics["collection_rate_pct"],
            "due_to_date": collection_rate_metrics["due_to_date"],
            # Collected-in-advance (metric 3) — unapplied credit + receipts for
            # periods not yet due. Never folded into ledger_collected_ytd above.
            "collected_in_advance": collected_in_advance,
            "in_grace_amount": in_grace_amount,
            "true_arrears_amount": true_arrears_amount,
            "not_yet_due_amount": not_yet_due_amount,
        },
        "arrears_metrics": {
            "units_owing": units_owing,
            "total_outstanding": total_outstanding_val,
            "true_arrears_amount": true_arrears_amount,
            "in_grace_amount": in_grace_amount,
            # Never subtracted from true_arrears_amount above — a unit's credit
            # never reduces any other unit's arrears.
            "total_credit_amount": total_credit_amount,
        },
        "portal_cross_check": {
            "available": portal_doc is not None,
            "portal_arrears_total": portal_arrears_total,
            "ledger_arrears_total": true_arrears_amount,
            "delta": round(portal_arrears_total - true_arrears_amount, 2) if portal_doc is not None else 0.0,
            "requires_reconciliation": requires_reconciliation,
            "last_scraped_at": portal_doc.get("updated_at") if portal_doc else None,
        },
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fund Collections by Unit Type (GAP-FIN-035 Item 3, 2026-08-03)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/fund-collections-by-unit-type")
async def get_fund_collections_by_unit_type_endpoint(
        years: Optional[str] = None,
        current_user: dict = Depends(require_feature("fund_collections_by_unit_type_report")),
        building_id: str = Depends(get_current_building),
):
    """
    Life-to-date Admin Fund / Sinking Fund collected totals, broken down by
    unit-type group (Apartment/Townhouse/etc). GAP-FIN-035 Item 3.

    `years`: optional comma-separated list (e.g. "2021,2022,2023"). Omit to
    include every year found in this building's unit_levy_ledger.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    years_list = [y.strip() for y in years.split(",") if y.strip()] if years else None
    return await get_fund_collections_by_unit_type(building_id, years_list)


# ─────────────────────────────────────────────────────────────────────────────
# Building-Wide Fund Overview (accessible to all authenticated users)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/building-overview")
async def get_building_fund_overview(
        year: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Return building-wide levy collection rates and fund health percentages.

    Aggregates levy, arrears, and fund balances from PostgreSQL finance ledger
    tables across all lots in the building. Accessible to every
    authenticated user (owner, tenant, guest) so dashboard cards can display
    building-wide numbers.

    Source is gated by the finance cutover control plane
    (get_finance_route_runtime_state), like every other finance route — this
    route does not read Postgres unconditionally. While finance_ledger has not
    been promoted to postgres_read for this route, MongoDB (unit_levy_ledger)
    serves as primary and Postgres is a shadow-compare target only. Once
    promoted, MongoDB is used only as a fallback when the PG read itself fails
    (connection/query error) — see _get_building_overview_mongo_fallback. A
    reachable PG with no ledger data yet for this building/year is a valid
    state (empty contract, no fallback).
    """
    route_state = await get_finance_route_runtime_state(
        building_id=building_id, route_key="finance.building_overview",
    )
    source = route_state["source"]
    if source != "postgres":
        # Serving Mongo as primary — keep the shadow comparison running alongside.
        asyncio.create_task(_maybe_shadow_building_overview(building_id, year))

    async def _pg_read():
        # An empty PG ledger (scheme not yet onboarded, or no levy_runs for this
        # building/year) returns an empty-but-valid PG contract — this is normal for a
        # brand-new building, not a failure, and does NOT trigger the MongoDB DR fallback
        # (read_pg_first_with_mongo_dr serves a non-None PG payload as-is).
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant
        from sqlalchemy import text

        scheme = await resolve_scheme_context(building_id)
        if not scheme:
            return _empty_building_overview_pg_response(year)

        scheme_id = str(scheme["scheme_id"])
        tenant_id = str(scheme["tenant_id"])
        fy = str(year) if year else None
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            if not fy:
                fy_result = await session.execute(
                    text("""
                        SELECT financial_year
                        FROM finance.levy_runs
                        WHERE scheme_id = CAST(:sid AS UUID)
                        ORDER BY issue_date DESC NULLS LAST, due_date DESC NULLS LAST
                        LIMIT 1
                    """),
                    {"sid": scheme_id},
                )
                fy = fy_result.scalar()
            if not fy:
                return _empty_building_overview_pg_response(year)

            levy_result = await session.execute(
                text("""
                    SELECT
                        f.fund_type::text AS fund_type,
                        COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0) AS levied_cents,
                        COALESCE(SUM(li.paid_cents), 0) AS paid_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    JOIN finance.funds f ON f.fund_id = li.fund_id
                    -- GAP-FIN-062: only count posted journal entries -- a levy_item whose
                    -- entry is still draft/pending_approval (mid dual-control review) or
                    -- voided is not yet a real financial fact.
                    JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
                      AND je.status = 'posted'
                    WHERE li.scheme_id = CAST(:sid AS UUID)
                      AND lr.financial_year = :fy
                    GROUP BY f.fund_type
                """),
                {"sid": scheme_id, "fy": str(fy)},
            )
            arrears_result = await session.execute(
                text("""
                    SELECT
                        l.lot_id::text AS lot_id,
                        COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents - li.paid_cents), 0) AS arrears_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    JOIN core.lots l ON l.lot_id = li.lot_id
                    -- GAP-FIN-062: same posted-only gate as levy_result above.
                    JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
                      AND je.status = 'posted'
                    WHERE li.scheme_id = CAST(:sid AS UUID)
                      AND lr.financial_year = :fy
                    GROUP BY l.lot_id
                    HAVING COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents - li.paid_cents), 0) > 0
                """),
                {"sid": scheme_id, "fy": str(fy)},
            )

            # Unapplied credit — delegated to the canonical per-lot module, NOT computed
            # here. This block used to carry its own GREATEST(0, received - levied) query.
            # It was the origin of that shape, and the copy drifted away from it in four
            # ways that all over-counted credit; measured live on East Gate FY2026 the
            # inline query returned $1,783,940.36 against a true $13,478.55.
            #
            #   * it anti-joined reversals on `reversal_of_id` alone. 127 of East Gate's
            #     155 reversal entries carry a NULL reversal_of_id and name their target
            #     in `source_reference` only, so $1,769,655.36 of REVERSED back-solve
            #     receipts counted as money owners hold.
            #   * it had no reversal-of-reversal test. Journal entries are immutable, so
            #     undoing a reversal means posting a second one on top; a receipt reversed
            #     and then un-reversed is live again, and an "any reversal exists?" test
            #     excludes it forever (UA005, $467.51).
            #   * it had no `retired_at IS NULL`, so the 70 receipts retired under
            #     GAP-FIN-073 kept counting.
            #   * it INNER-JOINed the levied subquery, dropping a lot that paid something
            #     and was levied nothing — the extreme GAP-FIN-036 case, and the one where
            #     the credit is largest.
            #
            # Registry: docs/architecture/canonical_owners.yaml, concept `lot-true-balance`.
            # The aggregate is Σ per-lot credit, so it stays strictly per-lot and one
            # lot's credit can still never reduce another's arrears (CLAUDE.md rule 10).
            if _is_plain_calendar_year(str(fy)):
                lot_balances = await compute_lot_true_balances(
                    session,
                    scheme_id=scheme_id,
                    tenant_id=tenant_id,
                    financial_year=str(fy),
                )
                unapplied_credit_cents = building_unapplied_credit_cents(lot_balances)
            else:
                # A FY *label* ("2025-2026") cannot be cast to the int the received_on
                # window needs. The inline query raised a Postgres error here, taking the
                # whole overview down; report no credit and say so instead. Credit is an
                # additive dimension, so 0 degrades the response rather than corrupting
                # the arrears and levied figures beside it.
                logger.warning(
                    "building-overview: unapplied credit skipped for building %s — "
                    "financial_year %r is a label, not a calendar year",
                    building_id, fy,
                )
                unapplied_credit_cents = 0

        levy_rows = levy_result.fetchall()
        arrears_rows = arrears_result.fetchall()
        admin_levied_cents = 0
        admin_paid_cents = 0
        sinking_levied_cents = 0
        sinking_paid_cents = 0
        for row in levy_rows:
            if row.fund_type == "admin":
                admin_levied_cents += int(row.levied_cents or 0)
                admin_paid_cents += int(row.paid_cents or 0)
            elif row.fund_type in ("sinking", "capital_works"):
                sinking_levied_cents += int(row.levied_cents or 0)
                sinking_paid_cents += int(row.paid_cents or 0)

        total_levied_cents = admin_levied_cents + sinking_levied_cents
        total_paid_cents = admin_paid_cents + sinking_paid_cents
        total_outstanding_cents = sum(int(row.arrears_cents or 0) for row in arrears_rows)

        admin_levied = _aud_from_cents(admin_levied_cents)
        sinking_levied = _aud_from_cents(sinking_levied_cents)
        total_levied = _aud_from_cents(total_levied_cents)
        total_paid = _aud_from_cents(total_paid_cents)
        total_outstanding = _aud_from_cents(total_outstanding_cents)

        # Cash Position must be today's/as-of fund position from the
        # consolidated financial fact, not a projected levy-year closing value.
        # The BI fact is indexed by scheme/year/fund/period, so this avoids
        # expensive raw ledger scans on dashboard load. Operational ledger is a
        # fallback only for buildings whose BI rows have not been materialised.
        fund_balances = await _financial_read_service.get_consolidated_fund_balances(
            building_id=building_id,
            financial_year=str(fy),
        )
        if not fund_balances:
            fund_balances = await _financial_read_service.get_fund_balances(building_id=building_id) or {}
        admin_balance = _aud_from_cents(fund_balances.get("admin_balance_cents"))
        sinking_balance = _aud_from_cents(fund_balances.get("sinking_balance_cents"))

        admin_paid = _aud_from_cents(admin_paid_cents)
        sinking_paid = _aud_from_cents(sinking_paid_cents)
        # GAP-FIN-016 Phase 2b Item B1 (2026-07-21): same consolidation as the Mongo
        # fallback's admin_rate/sinking_rate above — see that call site's comment.
        # This branch already has cents values, so no dollar round-trip needed.
        #
        # GAP-FIN-035 (2026-08-03) audit note: unlike the Mongo fallback above, this
        # branch's `paid_cents` sum is NOT the same advance-payment-leak pattern —
        # FinancialCoreService.allocate_payment() caps `allocatable = min(remaining,
        # item.outstanding_cents)` per levy_item (service.py:717), so a unit's
        # advance/credit can never over-allocate past what a specific due levy_item
        # actually charges. SUM(paid_cents) here is therefore already per-item
        # clamped and structurally safe as a due-date Collection Rate — do not
        # "fix" it to route through get_collection_rate_metrics() (a Mongo-only
        # helper); that would be the wrong direction for a Postgres-primary branch.
        admin_rate = float(cents_to_percentage(admin_paid_cents, admin_levied_cents, digits=1))
        sinking_rate = float(cents_to_percentage(sinking_paid_cents, sinking_levied_cents, digits=1))

        # GAP-FIN-016 Item C (2026-07-21, corrected in audit same day): this
        # branch's total_obligations previously omitted opening arrears
        # entirely (hardcoded total_opening_arrears=0.0 in the response even
        # though the field exists) and fund_health had no floor clamp (could
        # go negative if total_outstanding > total_obligations). Both are
        # genuine correctness gaps, fixed below by delegating to
        # current_year_collection_rate() with opening_arrears_cents=0 (honest
        # — see the paragraph below, not silently wrong).
        #
        # levies_paid_pct is UNCHANGED from its original paid/levied formula
        # — a same-day audit found `docs/features/
        # StrataOS_Financial_UI_Label_and_Calculation_Register.md` (dated
        # 2026-07-14, one week before this session) explicitly documents "PG
        # overview: paid / levied" as a distinct, accepted rate family
        # alongside the Mongo path's opening-arrears-inclusive formula ("There
        # are multiple valid rate families; do not compare them without
        # checking denominator"). The first version of this fix changed this
        # branch's levies_paid_pct to match Mongo's (total_levied-
        # total_outstanding)/total_levied formula — an unsupported assumption
        # that the two branches must agree, not backed by any test (the only
        # test asserting that formula, test_metric_consistency.py::
        # TestLeviesPaidPctRelationship, only ever exercises the live Mongo
        # path — finance_ledger is postgres_shadow, never postgres_read — so
        # it has never validated the PG branch) or by this documentation.
        # Reverted to preserve the documented, pre-existing PG behaviour.
        levies_paid_pct = float(cents_to_percentage(total_paid_cents, total_levied_cents, digits=1))
        #
        # total_opening_arrears is still 0 here, NOT a bug in this fix:
        # opening_arrears is a curated/reconciled per-unit field (prior-year
        # carry-forward, == admin_opening + sinking_opening — see
        # server.py's opening_arrears sync helpers) that Postgres has no
        # source for yet (finance.levy_items/levy_runs have no carry-forward
        # concept). An existing test (test_dashboard_pg_first.py::
        # test_building_overview_uses_postgres_ledger_contract_without_mongo)
        # deliberately asserts this branch never reads Mongo — a cross-store
        # overlay here (matching the fund-balance overlay pattern used
        # elsewhere in this function) would violate that invariant, so this
        # fix keeps the PG branch fully self-contained. The residual
        # fund_health/total_obligations gap against the Mongo path for
        # buildings/years with real opening arrears is a genuine PG data gap
        # (needs a carry-forward migration into Postgres), not a formula bug —
        # tracked as a follow-up in GAP-FIN-016, not fixed here.
        _collection = current_year_collection_rate(
            opening_arrears_cents=0,
            levied_cents=total_levied_cents,
            outstanding_cents=total_outstanding_cents,
            digits=1,
        )
        total_obligations = float(_collection.total_obligations_cents) / 100
        fund_health = float(_collection.collection_rate_pct)

        asyncio.create_task(_maybe_shadow_building_overview(building_id, str(fy)))

        return {
            "year": str(fy),
            "admin_fund": {
                "total_levied": admin_levied,
                "total_paid": admin_paid,
                "collection_rate": admin_rate,
                "closing_balance": admin_balance,
                "current_balance": admin_balance,
                "balance_cents": int(fund_balances.get("admin_balance_cents") or 0),
            },
            "sinking_fund": {
                "total_levied": sinking_levied,
                "total_paid": sinking_paid,
                "collection_rate": sinking_rate,
                "closing_balance": sinking_balance,
                "current_balance": sinking_balance,
                "balance_cents": int(fund_balances.get("sinking_balance_cents") or 0),
            },
            "total_levied": total_levied,
            "total_paid": total_paid,
            # Additive, 2026-08-05 -- does NOT change total_paid/levies_paid_pct above
            # (documented distinct PG rate family, see credit_result's comment). A
            # lot that has paid MORE than its own total_levied for the year (e.g. paid
            # ahead, or a prior-quarter catch-up) has that excess here, never netted
            # against another lot's shortfall. "True total received this year" for a
            # given lot = its own total_paid share + its own share of this figure.
            "unapplied_credit": _aud_from_cents(unapplied_credit_cents),
            "total_received_including_credit": _aud_from_cents(total_paid_cents + unapplied_credit_cents),
            # 0.0: Postgres has no opening-arrears source yet — see the comment
            # above fund_health's computation for why this stays 0 rather than
            # overlaying from Mongo.
            "total_opening_arrears": 0.0,
            "total_outstanding": total_outstanding,
            "levies_paid_pct": levies_paid_pct,
            "fund_health": fund_health,
            "total_obligations": total_obligations,
            "admin_collection_rate": admin_rate,
            "sinking_collection_rate": sinking_rate,
            "arrears_delta_pct": None,
            # GAP-DASH-001 P0-6 (PG side, deferred to Phase 1 reconciliation): this is a raw
            # positive-arrears lot count and is NOT yet grace-aware, unlike the Mongo path which
            # now routes through get_arrears_metrics(). finance.levy_items carries is_past_grace /
            # days_overdue, so the grace-aware equivalent belongs in this SQL — but changing a PG
            # read-model number is Phase-1 shadow-verified work, so it is intentionally left raw
            # here for now. The Mongo path (which serves this route today) is the corrected one;
            # the resulting shadow divergence on units_in_arrears is a true signal that PG needs
            # this same fix, not noise. See tasks/GAP-DASH-001-*.md.
            "units_in_arrears": len(arrears_rows),
            "admin_fund_trend": [],
            "sinking_fund_trend": [],
            "source": "postgres_ledger",
        }
    # (end of _pg_read)

    async def _mongo_read():
        return await _get_building_overview_mongo_fallback(building_id, year)

    # PG-first with Mongo as a disaster-recovery fallback: serve PG (including an empty
    # contract) when resolved to postgres; fall back to Mongo only on a genuine PG failure.
    # reraise=(HTTPException,) so a deliberate 4xx from the PG path propagates instead of
    # masking behind Mongo. A Mongo failure on the DR path surfaces as 503 (both stores down).
    try:
        payload, _served = await read_pg_first_with_mongo_dr(
            route_key="finance.building_overview",
            building_id=building_id,
            source=source,
            pg_read=_pg_read,
            mongo_read=_mongo_read,
            reraise=(HTTPException,),
        )
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "finance/building-overview: read failed (both PostgreSQL and MongoDB) for building %s: %s",
            building_id, exc,
        )
        raise HTTPException(status_code=503, detail="Finance building-overview read failed (both PostgreSQL and MongoDB).")


# ─────────────────────────────────────────────────────────────────────────────
# Owner-transfer paid split (Postgres-sourced, shared by two routes)
#
# "How much of a unit's levy receipts this financial year were paid while the
# CURRENT owner held title, vs by a previous owner before a mid-year transfer."
# Only Postgres can answer this accurately: finance.receipts carries payer_party_id,
# resolved at posting time from core.ownership_periods as of each receipt's own
# received_on date, so a receipt already records WHICH owner paid it across a
# transfer. The Mongo payment collections cannot -- their `paid_by` is the user who
# *recorded* the payment (usually the manager), not the owning party, and they are
# not even a complete payment set (DEFT/bank imports never write a Mongo
# levy_payments row). So both the PG branch of unit-dashboard-overview and the
# Mongo-served /levy-status route source this one figure from here.
# ─────────────────────────────────────────────────────────────────────────────
async def _compute_owner_paid_split(
        *, scheme_id: str, unit_number: str, financial_year: str, session,
) -> Optional[dict]:
    """Compute the current-vs-previous-owner receipts split for one unit/FY on an
    already-open, tenant-scoped Postgres ``session``.

    Returns ``{"current_owner_cents", "previous_owners_cents", "current_owner_since"}``
    (cents as ints, ``current_owner_since`` a date), or ``None`` when the lot has no
    current primary-owner ownership_periods row -- i.e. it isn't onboarded into the
    identity/ownership domain yet. ``None`` means "unknown", NOT "zero paid": missing
    is not the same as zero, so callers surface it as null rather than $0.00.
    """
    from sqlalchemy import text

    # The split query casts the FY to int (``CAST(:fy AS int)``), so only a plain 4-digit
    # calendar year is valid. A financial-year *label* like "2025-2026" would raise in
    # Postgres -- reject it up front and return None (unknown). This matters at BOTH call
    # sites: for the Mongo-served /levy-status wrapper an uncaught raise is merely swallowed
    # as a warning, but for the unit-dashboard-overview PG branch (which calls this inside
    # its own try/except) an uncaught raise would abort the WHOLE route to its Mongo
    # fallback. Fail soft on just this one additive figure instead of the entire response.
    fy = str(financial_year).strip()
    if not (len(fy) == 4 and fy.isdigit()):
        return None

    owner_result = await session.execute(
        text("""
            SELECT op.owner_party_id::text AS owner_party_id, op.valid_from
            FROM core.ownership_periods op
            JOIN core.lots l ON l.lot_id = op.lot_id
            WHERE l.scheme_id = CAST(:sid AS UUID)
              AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
              AND op.is_primary_owner = TRUE AND op.recorded_to IS NULL AND op.valid_to IS NULL
            LIMIT 1
        """),
        {"sid": scheme_id, "unit_number": unit_number},
    )
    owner_row = owner_result.mappings().first()
    if not owner_row:
        return None

    # payer_party_id = current owner -> current-owner slice; anyone else (a prior
    # title-holder), INCLUDING a NULL payer_party_id (schema allows it -- see
    # finance.receipts DDL, migration 0004) -> previous-owners slice. Using
    # "IS DISTINCT FROM" rather than "!=" is deliberate: a plain "!=" against NULL
    # evaluates to NULL (neither branch matches), silently excluding that receipt's
    # amount from BOTH sums and breaking the documented invariant "current +
    # previous = this FY's receipts total". Every receipt written through the
    # canonical RecordPaymentCommand path has a required (non-optional) payer_party_id
    # (backend/services/financial_core/domain/entities.py), so this has not yet been
    # observed live (verified 2026-08-05: 0 of 2,229 East Gate receipts have a NULL
    # payer_party_id) -- this is defensive against any future direct-write path that
    # doesn't go through that command, not a fix for an observed live discrepancy.
    # Reversed receipts are excluded via the journal_entries anti-join. Calendar-year
    # received_on window, identical to the unit-dashboard-overview PG branch this was
    # factored out of -- do not diverge.
    split_result = await session.execute(
        text("""
            SELECT
                COALESCE(SUM(CASE WHEN r.payer_party_id IS NOT DISTINCT FROM CAST(:owner_id AS UUID)
                    THEN r.amount_cents ELSE 0 END), 0) AS current_owner_cents,
                COALESCE(SUM(CASE WHEN r.payer_party_id IS DISTINCT FROM CAST(:owner_id AS UUID)
                    THEN r.amount_cents ELSE 0 END), 0) AS previous_owners_cents
            FROM finance.receipts r
            JOIN core.lots l ON l.lot_id = r.lot_id
            LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
            WHERE l.scheme_id = CAST(:sid AS UUID)
              AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
              AND rev.journal_entry_id IS NULL
              AND r.received_on >= CAST(:fy || '-01-01' AS date)
              AND r.received_on < CAST((CAST(:fy AS int) + 1) || '-01-01' AS date)
        """),
        {"sid": scheme_id, "unit_number": unit_number, "fy": fy,
         "owner_id": owner_row["owner_party_id"]},
    )
    split_row = split_result.mappings().first() or {}
    return {
        "current_owner_cents": int(split_row.get("current_owner_cents") or 0),
        "previous_owners_cents": int(split_row.get("previous_owners_cents") or 0),
        "current_owner_since": owner_row["valid_from"],
    }


async def _get_owner_paid_split_standalone(
        building_id: str, unit_number: str, financial_year: Optional[str],
) -> Optional[dict]:
    """Owner paid-split for Mongo-served callers (e.g. get_levy_status) that don't
    already hold a Postgres session. Opens its own tenant-scoped session and delegates
    to _compute_owner_paid_split.

    Fully non-fatal and directional (PG attempt -> None on any failure): this figure is
    a Postgres-sourced addition to an otherwise Mongo-served response, so PG being
    unreachable or unpromoted must never break that response -- it just omits the split
    (returns None, rendered as null). Never invert this into a hard dependency.
    """
    if not financial_year:
        return None
    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant

        scheme = await resolve_scheme_context(building_id)
        if not scheme:
            return None
        async with async_session_context() as session:
            await set_tenant(session, str(scheme["tenant_id"]))
            return await _compute_owner_paid_split(
                scheme_id=str(scheme["scheme_id"]),
                unit_number=unit_number,
                financial_year=str(financial_year),
                session=session,
            )
    except Exception as exc:
        logger.warning(
            "owner paid-split (PG) unavailable for %s/%s: %s", building_id, unit_number, exc
        )
        return None


def _scale_lifetime_owner_split(
        pg_current_cents: int, pg_previous_cents: int, tile_total_paid: float,
) -> tuple[Optional[float], Optional[float]]:
    """Scale the authoritative displayed lifetime total (``tile_total_paid``, dollars, from
    Mongo ``total_paid_all_years``) by the PG tenure-based attribution ratio
    ``pg_current / (pg_current + pg_previous)`` (GAP-FIN-054).

    Returns ``(current, previous)`` in dollars that sum to ``tile_total_paid`` to the cent
    (``previous`` absorbs the rounding residual), or ``(None, None)`` when there are no PG
    receipts to form a ratio (total == 0 — "unknown", not "zero"). PG supplies only the
    who-paid-what-fraction ratio; the magnitude is the Mongo tile total, so this can never
    surface a second, conflicting lifetime figure.
    """
    total_cents = int(pg_current_cents or 0) + int(pg_previous_cents or 0)
    if total_cents <= 0:
        return None, None
    ratio_current = int(pg_current_cents or 0) / total_cents
    current = round(float(tile_total_paid) * ratio_current, 2)
    previous = round(float(tile_total_paid) - current, 2)
    return current, previous


async def _compute_owner_lifetime_split(
        *, scheme_id: str, unit_number: str, session,
) -> Optional[dict]:
    """Lifetime (ALL-years) receipts split by ownership TENURE for one unit, on an
    already-open, tenant-scoped Postgres ``session`` (GAP-FIN-054).

    Returns ``{"current_owner_cents", "previous_owners_cents", "total_cents",
    "current_owner_since"}`` (cents ints, ``current_owner_since`` a date), or ``None`` when
    the lot has no current primary-owner ownership_periods row (``None`` = unknown, not zero).

    Partitions the unit's non-reversed ``finance.receipts`` by whether each receipt was
    received during the current owner's tenure (``received_on >= valid_from`` -> current
    owner) or before it (-> previous owners). Deliberately partitions by **tenure date**,
    NOT ``payer_party_id`` like the FY split (``_compute_owner_paid_split``): reconstructed
    historical receipts can carry the CURRENT party's ``payer_party_id`` for years the unit
    was owned by someone else, so a payer-based lifetime split would mis-credit every
    pre-transfer payment to the current owner. Tenure-date is robust to that and needs no
    prior-owner ``ownership_periods`` rows to exist. These cents are used only to derive the
    attribution RATIO; the displayed magnitude is scaled to the authoritative Mongo lifetime
    total (``total_paid_all_years``) by the caller, so the two never conflict.
    """
    from sqlalchemy import text

    owner_result = await session.execute(
        text("""
            SELECT op.owner_party_id::text AS owner_party_id, op.valid_from
            FROM core.ownership_periods op
            JOIN core.lots l ON l.lot_id = op.lot_id
            WHERE l.scheme_id = CAST(:sid AS UUID)
              AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
              AND op.is_primary_owner = TRUE AND op.recorded_to IS NULL AND op.valid_to IS NULL
            LIMIT 1
        """),
        {"sid": scheme_id, "unit_number": unit_number},
    )
    owner_row = owner_result.mappings().first()
    if not owner_row or owner_row.get("valid_from") is None:
        return None

    # Non-reversed receipts partitioned by tenure. Mirrors the FY split's receipts query
    # (same reversal anti-join via finance.journal_entries) minus the calendar-year window,
    # plus the received_on-vs-valid_from tenure split.
    split_result = await session.execute(
        text("""
            SELECT
                COALESCE(SUM(CASE WHEN r.received_on >= :valid_from
                    THEN r.amount_cents ELSE 0 END), 0) AS current_owner_cents,
                COALESCE(SUM(CASE WHEN r.received_on < :valid_from
                    THEN r.amount_cents ELSE 0 END), 0) AS previous_owners_cents
            FROM finance.receipts r
            JOIN core.lots l ON l.lot_id = r.lot_id
            LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
            WHERE l.scheme_id = CAST(:sid AS UUID)
              AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
              AND rev.journal_entry_id IS NULL
        """),
        {"sid": scheme_id, "unit_number": unit_number,
         "valid_from": owner_row["valid_from"]},
    )
    split_row = split_result.mappings().first() or {}
    cur = int(split_row.get("current_owner_cents") or 0)
    prev = int(split_row.get("previous_owners_cents") or 0)
    return {
        "current_owner_cents": cur,
        "previous_owners_cents": prev,
        "total_cents": cur + prev,
        "current_owner_since": owner_row["valid_from"],
    }


async def _get_owner_lifetime_split_standalone(
        building_id: str, unit_number: str,
) -> Optional[dict]:
    """Lifetime owner split for Mongo-served callers that don't already hold a PG session
    (GAP-FIN-054). Opens its own tenant-scoped session and delegates to
    ``_compute_owner_lifetime_split``. Fully non-fatal and directional (PG attempt -> None
    on any failure): a Postgres-sourced addition to a Mongo-served response must never break
    it. Returns None (rendered null, card hidden) when PG is unreachable/unpromoted, the
    scheme can't be resolved, or the lot has no ownership_periods row.
    """
    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant

        scheme = await resolve_scheme_context(building_id)
        if not scheme:
            return None
        async with async_session_context() as session:
            await set_tenant(session, str(scheme["tenant_id"]))
            return await _compute_owner_lifetime_split(
                scheme_id=str(scheme["scheme_id"]),
                unit_number=unit_number,
                session=session,
            )
    except Exception as exc:
        logger.warning(
            "owner lifetime paid-split (PG) unavailable for %s/%s: %s",
            building_id, unit_number, exc,
        )
        return None


@router.get("/finance/unit-dashboard-overview/{unit_number}")
async def get_unit_dashboard_overview(
        unit_number: str,
        year: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Owner dashboard unit summary.

    Source is gated by the finance cutover control plane
    (get_finance_route_runtime_state), like every other finance route — this
    route does not read Postgres unconditionally. While finance_ledger has not
    been promoted to postgres_read for this route, MongoDB serves as primary
    and Postgres is a shadow-compare target only. Once promoted, MongoDB is
    used only as a fallback when the PG read itself fails (connection/query
    error) — see _get_unit_dashboard_overview_mongo_fallback. A reachable PG
    with no ledger rows for this unit/year is a valid state (empty contract,
    no fallback).
    """
    # Resolve display variants (87 / U87 / Unit 87 → TH087) to the canonical
    # units.unit_number key before authorisation and before every finance read.
    unit_number = await resolve_canonical_unit_number(
        db, building_id, unit_number, rules=await _unit_display_rules_safe(building_id)
    )
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances and not user_unit_matches(current_user, unit_number):
        raise HTTPException(status_code=403, detail="Not authorized to view other units")

    route_state = await get_finance_route_runtime_state(
        building_id=building_id, route_key="finance.unit_dashboard_overview",
    )
    source = route_state["source"]
    if source != "postgres":
        # Serving Mongo as primary — keep the shadow comparison running alongside.
        asyncio.create_task(_maybe_shadow_unit_dashboard_overview(building_id, unit_number, year))

    async def _pg_read():
        # A reachable PG with no ledger rows yet for this unit/year returns an
        # empty-but-valid PG contract — normal for a brand-new unit, not a failure, and does
        # NOT trigger the MongoDB DR fallback (a non-None PG payload is served as-is).
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant
        from sqlalchemy import text

        pg_balance = await _financial_read_service.get_unit_levy_balance(
            building_id=building_id,
            unit_number=unit_number,
            financial_year=year,
        )
        scheme = await resolve_scheme_context(building_id)
        if not pg_balance or not scheme:
            return _empty_unit_dashboard_overview_pg_response(unit_number, year)

        scheme_id = str(scheme["scheme_id"])
        tenant_id = str(scheme["tenant_id"])
        selected_year = str(pg_balance.get("financial_year") or year or "")

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)

            totals_row = await session.execute(
                text("""
                    SELECT
                        COALESCE(MAX(l.entitlement_units), 0) AS entitlement,
                        COALESCE(SUM(CASE WHEN f.fund_type = 'admin'
                            THEN (li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents)
                            ELSE 0 END), 0) AS admin_levied_cents,
                        COALESCE(SUM(CASE WHEN f.fund_type = 'admin'
                            THEN li.paid_cents ELSE 0 END), 0) AS admin_paid_cents,
                        COALESCE(SUM(CASE WHEN f.fund_type IN ('sinking', 'capital_works')
                            THEN (li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents)
                            ELSE 0 END), 0) AS sinking_levied_cents,
                        COALESCE(SUM(CASE WHEN f.fund_type IN ('sinking', 'capital_works')
                            THEN li.paid_cents ELSE 0 END), 0) AS sinking_paid_cents
                    FROM core.lots l
                    JOIN finance.levy_items li ON li.lot_id = l.lot_id
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    JOIN finance.funds f ON f.fund_id = li.fund_id
                    WHERE l.scheme_id = CAST(:sid AS UUID)
                      AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
                      AND lr.financial_year = :fy
                """),
                {"sid": scheme_id, "unit_number": unit_number, "fy": selected_year},
            )
            totals = totals_row.mappings().first() or {}

            quarters_rows = await session.execute(
                text("""
                    SELECT
                        COALESCE(lr.quarter_no, 0) AS quarter_no,
                        lr.due_date,
                        COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0) AS levied_cents,
                        COALESCE(SUM(li.paid_cents), 0) AS paid_cents
                    FROM core.lots l
                    JOIN finance.levy_items li ON li.lot_id = l.lot_id
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    WHERE l.scheme_id = CAST(:sid AS UUID)
                      AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
                      AND lr.financial_year = :fy
                    GROUP BY lr.quarter_no, lr.due_date
                    ORDER BY lr.due_date
                """),
                {"sid": scheme_id, "unit_number": unit_number, "fy": selected_year},
            )
            quarter_data = quarters_rows.fetchall()

            next_due_row = await session.execute(
                text("""
                    SELECT due_date
                    FROM finance.levy_runs
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND financial_year = :fy
                      AND due_date >= CURRENT_DATE
                    ORDER BY due_date ASC
                    LIMIT 1
                """),
                {"sid": scheme_id, "fy": selected_year},
            )
            next_due = next_due_row.scalar()

            # Current-owner-aware paid split (2026-08-05, additive only -- does not
            # change total_paid above). Sourced from finance.receipts.payer_party_id
            # (resolved at posting time from core.ownership_periods as of each receipt's
            # received_on), so a receipt already records WHICH owner paid it across a
            # mid-year transfer. total_paid stays lot-level cumulative -- the split is
            # additive alongside it. A lot with no ownership_periods row reports null
            # (missing != zero). Shared with the Mongo-served /levy-status route via
            # _compute_owner_paid_split so the two never diverge.
            _owner_split = await _compute_owner_paid_split(
                scheme_id=scheme_id, unit_number=unit_number,
                financial_year=selected_year, session=session,
            )
            paid_by_current_owner_cents = _owner_split["current_owner_cents"] if _owner_split else None
            paid_by_previous_owners_cents = _owner_split["previous_owners_cents"] if _owner_split else None
            current_owner_since = _owner_split["current_owner_since"] if _owner_split else None

            # GAP-FIN-036 (additive, 2026-08-10): a unit that has paid MORE than everything
            # ever levied to it holds unapplied credit that finance.levy_items cannot express
            # (allocate_payment has no open item to absorb the excess). The levy_items-derived
            # closing_balance -> balance_credit therefore reads $0.00 for a genuinely-in-credit
            # owner. Surface the true, receipts-aware balance additively via the ONE canonical
            # per-lot helper (per-lot, never netted against another unit — CLAUDE.md rule 10).
            # Fully defensive: any error leaves both fields null (unknown != $0), never aborting
            # the route to Mongo. Does NOT redefine balance_owing/balance_credit (kept for
            # back-compat); the UI adopts true_balance to show a real credit. See
            # tasks/GAP-FIN-036-*.md and tasks/GAP-ONBOARD-004-*.md (A1).
            unit_unapplied_credit_cents = None
            unit_true_balance_cents = None
            try:
                from services.finance_metrics.lot_true_balance import compute_unit_true_balance
                _tb = await compute_unit_true_balance(
                    session, scheme_id=scheme_id, tenant_id=tenant_id,
                    unit_number=unit_number, financial_year=selected_year,
                )
                if _tb is not None:
                    unit_unapplied_credit_cents = _tb.unapplied_credit_cents
                    unit_true_balance_cents = _tb.true_balance_cents
            except Exception as _tb_exc:  # pragma: no cover - defensive, never breaks the route
                logger.debug(
                    "finance/unit-dashboard-overview: true-balance enrichment skipped for %s/%s: %s",
                    building_id, unit_number, _tb_exc,
                )

        # Same gap as the Mongo fallback path just above: finance.levy_runs only has a row for a
        # quarter that has already been formally run/invoiced, so a not-yet-run upcoming quarter
        # (e.g. Q3 whose finance.levy_runs row doesn't exist yet) shows no next_due at all even
        # though the building's own configured schedule already defines that date. Fall back to
        # computing it from settings, same as the Mongo path.
        if next_due is None:
            settings_doc = await _get_general_settings(building_id, {"_id": 0})
            due_months = (settings_doc or {}).get("levy_due_months") or [3, 6, 9, 12]
            due_day_type = (settings_doc or {}).get("levy_due_day_type") or "first"
            due_day = (settings_doc or {}).get("levy_due_day")
            custom_dates = (settings_doc or {}).get("levy_due_custom_dates") or {}
            fy_start_month = int((settings_doc or {}).get("financial_year_start_month") or 1)
            try:
                fy_year_int = int(str(selected_year).split("-")[0])
            except (TypeError, ValueError):
                fy_year_int = date.today().year
            computed_dates = _compute_period_due_dates(
                fy_year_int, due_months, due_day_type, due_day, len(due_months) or 4,
                custom_dates, fy_start_month=fy_start_month,
            )
            today_iso = date.today().isoformat()
            upcoming = sorted(d for d in computed_dates if d >= today_iso)
            next_due = date.fromisoformat(upcoming[0]) if upcoming else None

        quarters = []
        today = date.today()
        for q in quarter_data:
            q_levied = _aud_from_cents(q.levied_cents)
            q_paid = _aud_from_cents(q.paid_cents)
            outstanding = round(max(q_levied - q_paid, 0), 2)
            due_date = q.due_date
            if outstanding <= 0.01:
                status = "paid"
            elif q_paid > 0:
                status = "partial"
            elif due_date and due_date < today:
                status = "overdue"
            else:
                status = "unpaid"
            quarter_no = int(q.quarter_no) if q.quarter_no is not None else 0
            quarters.append({
                "quarter": f"Q{quarter_no}",
                "due_date": due_date.isoformat() if due_date else None,
                "status": status,
                "amount_due": q_levied,
                "amount_paid": q_paid,
                "outstanding": outstanding,
            })

        closing_balance = round(float(pg_balance.get("closing_balance") or 0), 2)

        # GAP-DASH-001 Bug #1/#3: admin_fund.annual / sinking_fund.annual must be the FULL
        # annual per-unit levy (what the Owner dashboard's "Paid · FY26" ring denominator and the
        # "Where your levy goes / Annual share" donut are written against), NOT the YTD-charged
        # SUM(levy_items) — which is ~half by Q2 and produced TH087's "$3,542.02 annual" / pinned
        # 100% ring. Source it from get_levy_rates × entitlement, exactly like the Mongo branch
        # (whose contract the frontend follows). Fall back to the YTD-levied figure only when the
        # annual rate is unavailable, so a pure-PG building without Mongo annual_levies is
        # unchanged rather than regressed to $0 — never show a YTD number under an "annual" label
        # when the real annual is known.
        _entitlement = float(totals.get("entitlement") or 0)
        _rates = await get_levy_rates(str(selected_year), building_id)
        _admin_annual_full = round(float(_rates.get("admin_annual") or 0) * _entitlement, 2)
        _sinking_annual_full = round(float(_rates.get("sinking_annual") or 0) * _entitlement, 2)
        admin_annual = _admin_annual_full if _admin_annual_full > 0 else _aud_from_cents(totals.get("admin_levied_cents"))
        sinking_annual = _sinking_annual_full if _sinking_annual_full > 0 else _aud_from_cents(totals.get("sinking_levied_cents"))

        # GAP-DASH-001 Bug #2: emit next_payment_amount (the PG branch previously omitted it, so
        # once all ISSUED quarters were paid the frontend's fallback chain bottomed out at $0.00 —
        # TH087 showed "$0.00 due 1 Sept" while genuinely owing $1,772.51 − $254.98 credit =
        # $1,517.53). Mirror the Mongo fallback's credit-aware, integer-cents estimate, and only
        # when the quarters array has no genuinely-upcoming unpaid instalment (same guard as the
        # frontend's nextUnpaidQuarter test) so it never overrides real per-quarter data.
        next_payment_amount = None
        _today_iso = today.isoformat()
        _has_real_upcoming_quarter = any(
            q.get("status") != "paid" and (not q.get("due_date") or str(q["due_date"])[:10] >= _today_iso)
            for q in quarters
        )
        _full_annual = admin_annual + sinking_annual
        if next_due is not None and not _has_real_upcoming_quarter and _full_annual > 0:
            _settings_np = await _get_general_settings(building_id, {"_id": 0})
            _due_months_np = (_settings_np or {}).get("levy_due_months") or [3, 6, 9, 12]
            _num_periods = len(_due_months_np) or 4
            _base_instalment_cents = round(_full_annual * 100 / _num_periods)
            # closing_balance sign convention matches net_balance: > 0 owes, < 0 in credit.
            _net_balance_cents = round(closing_balance * 100)
            _quarters_prepaid = (
                (-_net_balance_cents) // _base_instalment_cents
                if (_net_balance_cents < 0 and _base_instalment_cents > 0) else 0
            )
            _remaining_cents = _net_balance_cents + _quarters_prepaid * _base_instalment_cents
            next_payment_amount = round(max(0, _base_instalment_cents + _remaining_cents) / 100, 2)

        asyncio.create_task(
            _maybe_shadow_unit_dashboard_overview(building_id, unit_number, selected_year)
        )

        return {
            "unit_number": unit_number,
            "financial_year": selected_year,
            "unit_entitlement": int(_entitlement),
            "admin_fund": {
                "annual": admin_annual,
                "paid": _aud_from_cents(totals.get("admin_paid_cents")),
            },
            "sinking_fund": {
                "annual": sinking_annual,
                "paid": _aud_from_cents(totals.get("sinking_paid_cents")),
            },
            "next_payment_amount": next_payment_amount,
            "total_levied": round(float(pg_balance.get("levied_amount") or 0), 2),
            "total_paid": round(float(pg_balance.get("paid_amount") or 0), 2),
            # Same derivation as the Mongo fallback path, for the same reason: a field explicitly
            # scoped to "paid toward this year's own levied charges", not whatever total_paid
            # represents on this path (kept for backward compatibility, not redefined here).
            "paid_this_year": round(float(pg_balance.get("levied_amount") or 0) - closing_balance, 2),
            "balance_owing": round(max(closing_balance, 0), 2),
            "balance_credit": round(abs(min(closing_balance, 0)), 2),
            "next_due_date": next_due.isoformat() if next_due else None,
            "quarters": quarters,
            # Additive (2026-08-05) -- see the query comment above for why these are
            # separate from total_paid rather than a redefinition of it.
            "paid_by_current_owner": _aud_from_cents(paid_by_current_owner_cents) if paid_by_current_owner_cents is not None else None,
            "paid_by_previous_owners": _aud_from_cents(paid_by_previous_owners_cents) if paid_by_previous_owners_cents is not None else None,
            "current_owner_since": current_owner_since.isoformat() if current_owner_since else None,
            # GAP-FIN-036 additive true balance (receipts-aware). unapplied_credit is the
            # overpayment beyond total levied that has no open levy_item to allocate to;
            # true_balance is signed (>0 owes, <0 in credit). Both null == unknown (no PG
            # ledger rows for the year), never a fabricated $0.00.
            "unapplied_credit": _aud_from_cents(unit_unapplied_credit_cents) if unit_unapplied_credit_cents is not None else None,
            "true_balance": _aud_from_cents(unit_true_balance_cents) if unit_true_balance_cents is not None else None,
            "source": "postgres_ledger",
        }
    # (end of _pg_read)

    async def _mongo_read():
        return await _get_unit_dashboard_overview_mongo_fallback(building_id, unit_number, year)

    # PG-first with Mongo as a disaster-recovery fallback (serve PG incl. empty; fall back
    # only on genuine PG failure). reraise=(HTTPException,) so the 403/4xx auth path is never
    # masked by Mongo. A Mongo failure on the DR path surfaces as 503 (both stores down).
    #
    # require_fresh_snapshot=True (2026-08-11, right-sized DR pilot route): a DR fallback that
    # blindly trusted whatever Mongo unit_levy_ledger data happened to exist would be unsafe --
    # this route is the first to require a completed, reconciled backend/scripts/dr_mongo_snapshot.py
    # snapshot within the last 30 minutes before serving Mongo at all; otherwise it returns 503
    # rather than silently serving indeterminately-stale dollar figures. See finance_pg_read_dr.py.
    try:
        payload, served = await read_pg_first_with_mongo_dr(
            route_key="finance.unit_dashboard_overview",
            building_id=building_id,
            source=source,
            pg_read=_pg_read,
            mongo_read=_mongo_read,
            reraise=(HTTPException,),
            require_fresh_snapshot=True,
        )
        if served == SERVED_MONGO_DR_FALLBACK and isinstance(payload, dict):
            snapshot = await get_dr_snapshot("finance.unit_dashboard_overview", building_id)
            payload["served_source"] = served
            payload["as_of"] = (snapshot or {}).get("completed_at")
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "finance/unit-dashboard-overview: read failed (both PostgreSQL and MongoDB) for %s/%s: %s",
            building_id, unit_number, exc,
        )
        raise HTTPException(status_code=503, detail="Finance unit-dashboard-overview read failed (both PostgreSQL and MongoDB).")


# ─────────────────────────────────────────────────────────────────────────────
# Portal Bank Balances (Strata Web scraper snapshot — read-only)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/portal-bank-balances")
async def get_portal_bank_balances(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Return bank account balances and owner arrears/credit summary from the
    Strata Web portal scraper snapshot.

    Data sources (single source of truth — read-only from this router):
      - bank_accounts     : admin + sinking fund balances from portal
      - building_summaries: aggregated owner arrears/credit totals

    These figures are the portal's view and may differ slightly from the
    levy system (unit_levy_ledger) due to timing of bank reconciliation.
    They are surfaced for informational cross-check only — never used in
    any levy calculation or accounting formula.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Parallel fetch — both collections are building-scoped
    bank_task = db.bank_accounts.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(20)
    summary_task = db.building_summaries.find_one(
        {"building_id": building_id}, {"_id": 0}
    )
    bank_docs, summary_doc = await asyncio.gather(bank_task, summary_task)

    # Aggregate totals across all bank accounts for this building
    total_admin = round(sum(float(b.get("admin_balance") or 0) for b in bank_docs), 2)
    total_sinking = round(sum(float(b.get("sinking_balance") or 0) for b in bank_docs), 2)
    total_balance = round(sum(float(b.get("total_balance") or 0) for b in bank_docs), 2)

    # Scrub internal fields from account records returned to the client
    accounts = [
        {
            "bsb": b.get("bsb"),
            "account_number": b.get("account_number"),
            "account_name": b.get("account_name"),
            "admin_balance": round(float(b.get("admin_balance") or 0), 2),
            "sinking_balance": round(float(b.get("sinking_balance") or 0), 2),
            "total_balance": round(float(b.get("total_balance") or 0), 2),
            "updated_at": b.get("updated_at"),
        }
        for b in bank_docs
    ]

    return {
        "accounts": accounts,
        "totals": {
            "admin_balance": total_admin,
            "sinking_balance": total_sinking,
            "total_balance": total_balance,
        },
        "owner_summary": {
            "arrears_total": round(float(summary_doc.get("arrears_total") or 0), 2),
            "credit_total": round(float(summary_doc.get("credit_total") or 0), 2),
            "arrears_count": summary_doc.get("arrears_count", 0),
            "credit_count": summary_doc.get("credit_count", 0),
            "clear_count": summary_doc.get("clear_count", 0),
            "total_lots": summary_doc.get("total_lots", 0),
            "collection_rate": summary_doc.get("collection_rate"),
            "risk_level": summary_doc.get("risk_level"),
            "updated_at": summary_doc.get("updated_at"),
        } if summary_doc else None,
        "synced_at": (
                (summary_doc.get("updated_at") if summary_doc else None) or
                (bank_docs[0].get("updated_at") if bank_docs else None)
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Quarter KPI Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/levy-kpi")
async def get_levy_kpi(
        year: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Compute quarter-level KPI metrics per the Collection Rate & Fund Health spec.

    All metrics are DERIVED from existing unit_levy_ledger + annual_levies data.
    Nothing new is stored. Suitable for the Collection Rate popup and Fund Health
    popup on the Management Cockpit and Owner dashboards.

    Returns building-level summary plus per-lot detail array.
    Per-lot detail (lots, top_true_arrears) is only returned for users with
    can_view_finances permission to avoid exposing other owners' levy balances.

    Multi-tenant: scoped by building_id from auth context.
    """
    permissions = get_user_permissions(current_user)
    can_view_per_lot = permissions.can_view_finances
    route_state = await get_finance_route_runtime_state(
        building_id=building_id,
        route_key="finance.levy_kpi",
    )
    # GAP-FIN-063: only current_balance (net_balance) is re-sourced from Postgres
    # below -- uoe, lot_number, and every rate/budget/fund-balance/fund-health figure
    # in this response still come from Mongo, because no Postgres source for the
    # staff-entered rate/budget config exists yet (GAP-ONBOARD-004 item B3). This is
    # the same hybrid pattern finance.arrears_detail's own PG branch already uses --
    # a full swap isn't achievable until B3 lands.

    async def _return_kpi(payload: dict) -> dict:
        """Generated function header.

        Function: _return_kpi
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if route_state.get("run_shadow"):
            asyncio.create_task(maybe_run_finance_shadow(
                building_id=building_id,
                route_key="finance.levy_kpi",
                mongo_payload=payload,
            ))
        return payload
    # ── 1. Resolve year and fetch config ─────────────────────────────────────
    # Performance Optimization⚡: Parallelize initial annual levy, settings, and building config fetches.
    # Reduces up to 3 sequential database round-trips into 1.
    settings_task = _get_general_settings(building_id, {"_id": 0})
    building_task = db.buildings.find_one({"id": building_id}, {"_id": 0, "trust_config": 1})

    if year:
        annual_levy_task = db.annual_levies.find_one({"building_id": building_id, "year": year})
        annual_levy, settings_doc, building_doc = await asyncio.gather(
            annual_levy_task, settings_task, building_task
        )
    else:
        # No year specified — LevyKpiDialog.tsx omits it whenever selectedYear hasn't
        # loaded yet. Must resolve via _resolve_default_levy_year (never a not-yet-started
        # year), not a raw sort=[("year", -1)] query — see module note above get_available_years.
        settings_doc, building_doc = await asyncio.gather(settings_task, building_task)
        resolved_year = await _resolve_default_levy_year(building_id, settings_doc=settings_doc)
        annual_levy = (
            await db.annual_levies.find_one({"building_id": building_id, "year": resolved_year})
            if resolved_year else None
        )

    if not annual_levy:
        # Return zeroed structure for buildings with no levy data
        return await _return_kpi(_empty_kpi_response(building_id, year or str(date.today().year)))

    year = annual_levy["year"]
    total_uoe = int(annual_levy.get("total_uoe") or TOTAL_UOE)
    levy_rate_breakdown = get_levy_rate_breakdown(
        annual_levy,
        settings_doc=settings_doc,
        trust_config=(building_doc or {}).get("trust_config"),
        total_uoe=total_uoe,
    )

    admin_q_rate = float(levy_rate_breakdown.get("admin_payable_quarterly") or 0)
    sinking_q_rate = float(levy_rate_breakdown.get("sinking_payable_quarterly") or 0)
    total_q_rate = admin_q_rate + sinking_q_rate

    # Use the canonical helper — prefers proposed_* fields, falls back to per-UOE rate × UOE,
    # then to levy_income only for fully-completed historical years.  This avoids the _is_partial
    # divergence where manual logic and the helper would give different results for years that have
    # proposed_* values regardless of their status field.
    from utils.finance_helpers import get_levy_proposed_amounts
    admin_a_gross, sinking_a_gross = get_levy_proposed_amounts(annual_levy)
    total_a_gross = admin_a_gross + sinking_a_gross

    # Imported/current fund balances. Older annual_levies documents may only have closing_balance.
    admin_fund_balance, admin_fund_balance_source = get_annual_fund_balance(annual_levy.get("admin_fund"))
    sinking_fund_balance, sinking_fund_balance_source = get_annual_fund_balance(annual_levy.get("sinking_fund"))
    total_cash_balance = round(admin_fund_balance + sinking_fund_balance, 2)

    # Live balance components: opening balance at FY start + YTD payments (same formula as stats endpoint)
    admin_opening_balance = float((annual_levy.get("admin_fund") or {}).get("opening_balance") or 0)
    sinking_opening_balance = float((annual_levy.get("sinking_fund") or {}).get("opening_balance") or 0)

    # Quarter budget = annual / 4
    admin_quarter_budget = round(admin_a_gross / 4, 2) if admin_a_gross else 0
    sinking_quarter_budget = round(sinking_a_gross / 4, 2) if sinking_a_gross else 0

    # ── 3. Parallel fetch ledger and expenses ────────────────────────────────
    # Performance Optimization⚡: Parallelize ledger retrieval and expense aggregations
    # into a single concurrent block, reducing 2 sequential stages into 1.
    ledger_task = db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year},
        {
            "unit_number": 1, "lot_number": 1, "uoe": 1,
            "net_balance": 1, "total_paid": 1,
            "admin_paid": 1, "sinking_paid": 1,
            "admin_opening": 1, "sinking_opening": 1,
            "_id": 0,
        },
    ).to_list(1000)

    admin_exp_task = db.expense_transactions.aggregate([
        {"$match": {"building_id": building_id, "financial_year": year, "fund_type_short": "admin"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)

    sinking_exp_task = db.expense_transactions.aggregate([
        {"$match": {"building_id": building_id, "financial_year": year, "fund_type_short": "sinking"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)

    async def _pg_net_balance_by_unit() -> dict | None:
        """Per-unit net_balance from Postgres, reusing FinancialReadService.
        get_unit_levy_balance_list() unchanged -- the same already-shadow-verified
        per-unit balance finance.arrears_detail's own PG branch already serves live.
        That function's `closing_balance` field (what we key off below) is computed
        from finance.journal_lines AR-account movements (opening + levied - paid,
        expressed as a GL closing balance), not a direct read of finance.levy_items --
        see get_unit_levy_balance()'s own SQL for the exact query. Deliberately does
        NOT hand-roll a new core.lots join for uoe/lot_number (see GAP-FIN-030's
        entitlement-column incident) -- only net_balance is re-sourced; every other
        per-lot field keeps coming from the Mongo ledger_docs above. Returns None
        (caller keeps every lot's Mongo net_balance) when this building isn't
        promoted to serve Postgres for this route, or on any fetch error.
        """
        if route_state["source"] != "postgres":
            return None
        try:
            balances = await _financial_read_service.get_unit_levy_balance_list(
                building_id=building_id, financial_year=year,
            )
        except Exception as exc:
            logger.warning(
                "finance.levy_kpi: PG per-lot balance fetch failed, falling back to "
                "Mongo net_balance: %s", exc,
            )
            return None
        if not balances:
            return None
        return {b["unit_number"]: b["closing_balance"] for b in balances}

    ledger_docs, admin_exp_agg, sinking_exp_agg, pg_net_balance_by_unit = await asyncio.gather(
        ledger_task, admin_exp_task, sinking_exp_task, _pg_net_balance_by_unit()
    )

    if not ledger_docs:
        return await _return_kpi(_empty_kpi_response(building_id, year))

    # YTD totals from ledger — direct per-fund fields, no ratio estimation needed
    ytd_total_paid = round(sum(float(d.get("total_paid") or 0) for d in ledger_docs), 2)
    _ytd_admin = round(sum(float(d.get("admin_paid") or 0) for d in ledger_docs), 2)
    _ytd_sinking = round(sum(float(d.get("sinking_paid") or 0) for d in ledger_docs), 2)
    # admin_ratio for display only (not used in balance calculation)
    _total_annual = admin_a_gross + sinking_a_gross
    _admin_ratio = (admin_a_gross / _total_annual) if _total_annual > 0 else 0.77
    ytd_admin_expenses = round(admin_exp_agg[0]["total"] if admin_exp_agg else 0, 2)
    ytd_sinking_expenses = round(sinking_exp_agg[0]["total"] if sinking_exp_agg else 0, 2)

    # Live balance: computed from database transactions (primary — fully reconcilable)
    # Formula: opening_balance + YTD levy income − YTD expenses
    # Strata Mgmt system current_balance (admin_fund_balance/sinking_fund_balance) kept as cross-check only.
    admin_live_balance = round(admin_opening_balance + _ytd_admin - ytd_admin_expenses, 2)
    sinking_live_balance = round(sinking_opening_balance + _ytd_sinking - ytd_sinking_expenses, 2)
    total_live_balance = round(admin_live_balance + sinking_live_balance, 2)

    # ── 4. Derive per-lot metrics ─────────────────────────────────────────────
    lots = []
    for doc in ledger_docs:
        uoe = int(doc.get("uoe") or 0)
        if uoe <= 0 or total_q_rate <= 0:
            continue  # skip lots with no UOE mapping

        # current_balance: positive = owes money (arrears), negative = credit.
        # GAP-FIN-063: prefer the Postgres-sourced balance for this unit when this
        # building/route is promoted to serve it; fall back to the Mongo ledger doc
        # per-lot when the PG map has no entry for this unit (or the fetch failed).
        unit_number_key = doc.get("unit_number", "")
        if pg_net_balance_by_unit is not None and unit_number_key in pg_net_balance_by_unit:
            current_balance = round(float(pg_net_balance_by_unit[unit_number_key]), 2)
        else:
            current_balance = round(float(doc.get("net_balance") or 0), 2)

        # quarter_levy: derived from per-UOE quarterly rate
        quarter_levy = round(uoe * total_q_rate, 2)

        # 2026-08-03: true_arrears previously subtracted a whole quarter's levy
        # from every lot's balance before counting it (quarter_true_arrears()),
        # while non_compliant_lot_count below counted ANY positive balance —
        # mismatched numerator/denominator, the same symptom as East Gate's
        # "31 units / $1,469.49" bug. Now both derive from the same
        # unit_arrears_and_credit() call, so they agree by construction.
        # No in-grace subtraction here (this endpoint has no due-date/grace
        # metadata loaded) — arrears_balance is the lot's full net_balance.
        arrears_cents, credit_cents = unit_arrears_and_credit(
            net_balance_cents=round(current_balance * 100),
        )
        arrears_balance = round(arrears_cents / 100, 2)
        credit_balance = round(credit_cents / 100, 2)
        true_arrears = arrears_balance

        # current_quarter_unpaid: portion of THIS quarter's levy not yet covered
        current_quarter_unpaid = round(min(arrears_balance, quarter_levy), 2)

        # current_quarter_collected: portion of THIS quarter's levy that IS covered
        current_quarter_collected = round(quarter_levy - current_quarter_unpaid, 2)

        # net_cash_collected: trust-accounting-capped at quarter_levy.
        # Credit overpayments are pre-payments for next quarter — not current-period income.
        net_cash_collected = round(max(0.0, quarter_levy - arrears_balance), 2)

        if current_balance < 0:
            status = "credit"
        elif current_balance > 0:
            status = "arrears"
        else:
            status = "paid_exact"

        is_compliant = current_balance <= 0

        lots.append({
            "lot": doc.get("lot_number", ""),
            "unit": doc.get("unit_number", ""),
            "uoe": uoe,
            "current_balance": current_balance,
            "quarter_levy": quarter_levy,
            "arrears_balance": arrears_balance,
            "credit_balance": credit_balance,
            "true_arrears": true_arrears,
            "current_quarter_unpaid": current_quarter_unpaid,
            "current_quarter_collected": current_quarter_collected,
            "net_cash_collected": net_cash_collected,
            "status": status,
            "is_compliant": is_compliant,
        })

    if not lots:
        return _empty_kpi_response(building_id, year)

    # ── 5. Building-level aggregates ─────────────────────────────────────────
    quarter_billed_lot_sum = round(sum(l["quarter_levy"] for l in lots), 2)

    # Canonical billed total: use the payable quarterly levy target from the annual
    # levy contract so the headline matches what owners are actually billed.
    quarter_billed_display = round(
        (levy_rate_breakdown.get("total_payable_quarterly") or 0) * total_uoe,
        2,
    ) if total_q_rate > 0 else quarter_billed_lot_sum

    arrears_total = round(sum(l["arrears_balance"] for l in lots), 2)
    credit_total = round(sum(l["credit_balance"] for l in lots), 2)
    true_arrears_total = round(sum(l["true_arrears"] for l in lots), 2)
    current_quarter_unpaid_total = round(
        min(sum(l["current_quarter_unpaid"] for l in lots), quarter_billed_display),
        2,
    )
    current_quarter_collected_total = round(
        max(0.0, quarter_billed_display - current_quarter_unpaid_total),
        2,
    )
    # Trust accounting: credit pre-payments are held in trust for the next quarter.
    # They do not count as current-period income, and do not offset other units' arrears.
    # net_arrears_outstanding = gross arrears only (not net of credits).
    net_cash_collected_total = round(max(0.0, quarter_billed_display - arrears_total), 2)
    net_arrears_outstanding = arrears_total

    compliant_lot_count = sum(1 for l in lots if l["is_compliant"])
    non_compliant_lot_count = len(lots) - compliant_lot_count
    total_lot_count = len(lots)
    # Credit lots are tracked as their own, never-netted-against-arrears count
    # (Root Cause #2: a unit's overpayment must be shown, not just zeroed out).
    # credit_total (dollar figure) already exists above.
    credit_lot_count = sum(1 for l in lots if l["credit_balance"] > 0.01)

    # ── 6. KPI ratios ────────────────────────────────────────────────────────
    quarter_billed_cents = round(quarter_billed_display * 100)

    # GAP-FIN-016 B2: these are 0-1 KPI fractions over this route's
    # quarter_billed_display denominator. Do not replace with
    # quarterly_collection_rate(), which is a 0-100 display-percent helper for
    # different endpoint contracts. The helper keeps the route's output shape
    # and 4dp precision, with deterministic Decimal rounding at the domain
    # boundary instead of inline float arithmetic.
    collection_rate = float(quarter_collection_fraction(
        numerator_cents=round(current_quarter_collected_total * 100),
        quarter_billed_cents=quarter_billed_cents,
    ))
    net_cash_realisation_rate = float(quarter_collection_fraction(
        numerator_cents=round(net_cash_collected_total * 100),
        quarter_billed_cents=quarter_billed_cents,
    ))
    lot_compliance_rate = round(compliant_lot_count / total_lot_count, 4) if total_lot_count > 0 else 0
    prior_period_arrears_rate = float(quarter_collection_fraction(
        numerator_cents=round(true_arrears_total * 100),
        quarter_billed_cents=quarter_billed_cents,
    ))

    # ── 7. Fund health ───────────────────────────────────────────────────────
    admin_fund_health = round(admin_fund_balance / admin_quarter_budget, 4) if admin_quarter_budget > 0 else 0

    # Admin health including net receivables (apportioned by admin share of levy)
    admin_share = (admin_a_gross / total_a_gross) if total_a_gross > 0 else 0
    admin_net_receivables = round(net_arrears_outstanding * admin_share, 2)
    admin_fund_health_incl_receivables = round(
        (admin_fund_balance + admin_net_receivables) / admin_quarter_budget, 4
    ) if admin_quarter_budget > 0 else 0

    sinking_cash_coverage = round(sinking_fund_balance / sinking_quarter_budget, 4) if sinking_quarter_budget > 0 else 0

    # sinking_percent_funded: null until reserve plan adequacy model exists
    sinking_percent_funded = None

    # Overall liquidity
    overall_liquidity_cash_only = round(total_cash_balance / quarter_billed_display,
                                        4) if quarter_billed_display > 0 else 0
    overall_liquidity_incl_receivables = round(
        (total_cash_balance + net_arrears_outstanding) / quarter_billed_display, 4
    ) if quarter_billed_display > 0 else 0

    # ── 8. Status labels ─────────────────────────────────────────────────────
    def _collection_status(rate: float) -> str:
        """Generated function header.

        Function: _collection_status
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pct = rate * 100
        if pct >= 95: return "green"
        if pct >= 85: return "amber"
        return "red"

    def _admin_health_status(rate: float) -> str:
        """Generated function header.

        Function: _admin_health_status
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pct = rate * 100
        if pct >= 100: return "green"
        if pct >= 50: return "amber"
        return "red"

    def _sinking_status(rate: float) -> str:
        """Generated function header.

        Function: _sinking_status
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pct = rate * 100
        if pct >= 200: return "green"
        if pct >= 100: return "amber"
        return "red"

    def _lot_compliance_status(rate: float) -> str:
        """Generated function header.

        Function: _lot_compliance_status
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pct = rate * 100
        if pct >= 90: return "green"
        if pct >= 75: return "amber"
        return "red"

    # ── 9. Sort top true arrears (for table) ─────────────────────────────────
    top_true_arrears = sorted(
        [l for l in lots if l["true_arrears"] > 0],
        key=lambda x: x["true_arrears"],
        reverse=True,
    )[:20]

    return await _return_kpi({
        "building_id": building_id,
        "year": year,
        # Levy budget inputs
        "admin_annual_gross": admin_a_gross,
        "sinking_annual_gross": sinking_a_gross,
        "total_annual_gross": total_a_gross,
        "admin_quarter_budget": admin_quarter_budget,
        "sinking_quarter_budget": sinking_quarter_budget,
        # Billed totals
        "quarter_billed_total_display": quarter_billed_display,
        "quarter_billed_total_lot_sum": quarter_billed_lot_sum,
        # Collection aggregates
        "arrears_total": arrears_total,
        "credit_total": credit_total,
        "net_arrears_outstanding": net_arrears_outstanding,
        "true_arrears_total": true_arrears_total,
        "current_quarter_unpaid_total": current_quarter_unpaid_total,
        "current_quarter_collected_total": current_quarter_collected_total,
        "net_cash_collected_total": net_cash_collected_total,
        # KPI ratios (0-1 range)
        "collection_rate": collection_rate,
        "net_cash_realisation_rate": net_cash_realisation_rate,
        "lot_compliance_rate": lot_compliance_rate,
        "prior_period_arrears_rate": prior_period_arrears_rate,
        # Lot counts
        "compliant_lot_count": compliant_lot_count,
        "non_compliant_lot_count": non_compliant_lot_count,
        "credit_lot_count": credit_lot_count,
        "total_lot_count": total_lot_count,
        # Fund balances — static (Strata Mgmt system import) vs computed live
        "admin_fund_balance": admin_fund_balance,
        "sinking_fund_balance": sinking_fund_balance,
        "admin_fund_balance_source": admin_fund_balance_source,
        "sinking_fund_balance_source": sinking_fund_balance_source,
        "total_cash_balance": total_cash_balance,
        # Live balance breakdown — computed from database transactions
        # opening + YTD levy income (admin_paid/sinking_paid) − YTD expenses
        "admin_opening_balance": admin_opening_balance,
        "sinking_opening_balance": sinking_opening_balance,
        "ytd_total_paid": ytd_total_paid,
        "admin_ratio": round(_admin_ratio, 4),
        "ytd_admin_paid": _ytd_admin,
        "ytd_sinking_paid": _ytd_sinking,
        "ytd_admin_expenses": ytd_admin_expenses,
        "ytd_sinking_expenses": ytd_sinking_expenses,
        "admin_live_balance": admin_live_balance,
        "sinking_live_balance": sinking_live_balance,
        "total_live_balance": total_live_balance,
        # Strata Mgmt system balance (cross-check) — may differ by interest income not yet in transactions
        "strata_mgmt_admin_balance": admin_fund_balance,
        "strata_mgmt_sinking_balance": sinking_fund_balance,
        "strata_mgmt_total_balance": total_cash_balance,
        # Fund health ratios (0-∞, >1 = healthy)
        "admin_fund_health": admin_fund_health,
        "admin_fund_health_incl_receivables": admin_fund_health_incl_receivables,
        "sinking_cash_coverage": sinking_cash_coverage,
        "sinking_percent_funded": sinking_percent_funded,
        "overall_liquidity_cash_only": overall_liquidity_cash_only,
        "overall_liquidity_incl_receivables": overall_liquidity_incl_receivables,
        # Status colours
        "collection_rate_status": _collection_status(collection_rate),
        "admin_health_status": _admin_health_status(admin_fund_health),
        "sinking_status": _sinking_status(sinking_cash_coverage),
        "lot_compliance_status": _lot_compliance_status(lot_compliance_rate),
        # Per-lot detail — redacted for non-finance users to avoid exposing other owners' balances
        "lots": lots if can_view_per_lot else [],
        # Top true arrears lots (for table, pre-sorted) — privileged users only
        "top_true_arrears": top_true_arrears if can_view_per_lot else [],
        # Permission flag so the frontend can conditionally show/hide per-lot sections
        "can_view_top_true_arrears": can_view_per_lot,
        # Metadata
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def _empty_kpi_response(building_id: str, year: str) -> dict:
    """Return a zeroed KPI response when no levy data exists for this building/year."""
    return {
        "building_id": building_id,
        "year": year,
        "admin_annual_gross": 0,
        "sinking_annual_gross": 0,
        "total_annual_gross": 0,
        "admin_quarter_budget": 0,
        "sinking_quarter_budget": 0,
        "quarter_billed_total_display": 0,
        "quarter_billed_total_lot_sum": 0,
        "arrears_total": 0,
        "credit_total": 0,
        "net_arrears_outstanding": 0,
        "true_arrears_total": 0,
        "current_quarter_unpaid_total": 0,
        "current_quarter_collected_total": 0,
        "net_cash_collected_total": 0,
        "collection_rate": 0,
        "net_cash_realisation_rate": 0,
        "lot_compliance_rate": 0,
        "prior_period_arrears_rate": 0,
        "compliant_lot_count": 0,
        "non_compliant_lot_count": 0,
        "credit_lot_count": 0,
        "total_lot_count": 0,
        "admin_fund_balance": 0,
        "sinking_fund_balance": 0,
        "total_cash_balance": 0,
        "admin_opening_balance": 0,
        "sinking_opening_balance": 0,
        "ytd_total_paid": 0,
        "admin_ratio": 0,
        "ytd_admin_paid": 0,
        "ytd_sinking_paid": 0,
        "ytd_admin_expenses": 0,
        "ytd_sinking_expenses": 0,
        "admin_live_balance": 0,
        "sinking_live_balance": 0,
        "total_live_balance": 0,
        "strata_mgmt_admin_balance": 0,
        "strata_mgmt_sinking_balance": 0,
        "strata_mgmt_total_balance": 0,
        "admin_fund_health": 0,
        "admin_fund_health_incl_receivables": 0,
        "sinking_cash_coverage": 0,
        "sinking_percent_funded": None,
        "overall_liquidity_cash_only": 0,
        "overall_liquidity_incl_receivables": 0,
        "collection_rate_status": "red",
        "admin_health_status": "red",
        "sinking_status": "red",
        "lot_compliance_status": "red",
        "lots": [],
        "top_true_arrears": [],
        "can_view_top_true_arrears": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Finance Charts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/charts")
async def get_finance_charts(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get chart data: expense by category, income breakdown, comparison charts.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Determine year and fetch initial levy data - Bolt ⚡
    # Optimized: if year is not provided, fetch full levy doc now to avoid second query later.
    # Resolves via _resolve_default_levy_year (never a not-yet-started year) — see module
    # note above get_available_years.
    if not year:
        resolved_year = await _resolve_default_levy_year(building_id)
        levy = (
            await db.annual_levies.find_one({"building_id": building_id, "year": resolved_year}, {"_id": 0})
            if resolved_year else None
        )
        year = levy["year"] if levy else None
    else:
        # We still need the levy doc for income breakdown later
        levy = None

    if not year:
        return {"expense_by_category": [], "income_by_category": [], "monthly_trend": []}

    # Performance Optimization⚡: Parallelize independent DB queries
    # 1. Categories for expenses
    cats_task = db.levy_categories.find(
        {"year": year, "building_id": building_id}, {"_id": 0}
    ).to_list(100)

    # 2. Quarterly payments trend
    payments_task = db.levy_payments.aggregate([
        {"$match": {"building_id": building_id, "year": year}},
        {"$group": {
            "_id": "$quarter",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1}
        }}
    ]).to_list(10)
    settings_task = _get_general_settings(building_id, {"_id": 0})

    # 3. Annual levy data (only if we don't have it yet)
    if levy is None:
        levy_task = db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})
        cats, quarterly_payments, levy, settings_doc = await asyncio.gather(
            cats_task,
            payments_task,
            levy_task,
            settings_task,
        )
    else:
        cats, quarterly_payments, settings_doc = await asyncio.gather(cats_task, payments_task, settings_task)

    cats = _normalize_cats_fund_type(cats)
    tx_actual_by_id: dict[tuple[str, str], float] = {}
    tx_actual_by_name: dict[tuple[str, str], float] = {}
    if cats:
        tx_actual_by_id, tx_actual_by_name = await _get_actual_overrides_from_financial_transactions(
            year=year,
            building_id=building_id,
        )

    # Expense by category (prefer financial_transactions-derived actuals; fallback to stored actual_amount;
    # then fallback to budgeted_amount for display continuity)
    admin_expenses = [
        {
            "name": c["name"],
            "value": round(
                _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
                or c.get("budgeted_amount", 0),
                2,
            ),
        }
        for c in cats if c.get("fund_type") == "administrative"
    ]
    sinking_expenses = [
        {
            "name": c["name"],
            "value": round(
                _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
                or c.get("budgeted_amount", 0),
                2,
            ),
        }
        for c in cats if c.get("fund_type") == "sinking"
    ]

    # Sort by value descending
    admin_expenses.sort(key=lambda x: x["value"], reverse=True)
    sinking_expenses.sort(key=lambda x: x["value"], reverse=True)

    income_by_category = []
    gst_summary = None
    if levy:
        from utils.finance_helpers import get_levy_proposed_amounts
        af_ex_gst, sf_ex_gst = get_levy_proposed_amounts(levy)
        af = levy.get("admin_fund", {})
        sf = levy.get("sinking_fund", {})
        # Use the proposed annual amounts (not YTD levy_income which is partial for the current year).
        # other_income is always actual so we keep it from the fund dict directly.
        af_other = round(af.get("other_income") or 0, 2)
        sf_other = round(sf.get("other_income") or 0, 2)
        income_by_category = [
            {"name": "Administrative Fund (ex-GST)", "value": af_ex_gst},
            {"name": "Sinking Fund (ex-GST)", "value": sf_ex_gst},
        ]
        gst_config = parse_levy_gst_settings(settings_doc)
        gst_summary = {
            "gst_registered": gst_config["gst_registered"],
            "gst_rate": gst_config["effective_gst_rate"],
            "gst_label": gst_config["gst_label"],
            "gst_component": round((af_ex_gst + sf_ex_gst) * gst_config["effective_gst_rate"], 2),
            "classification": "tax_collected_not_income",
        }
        if af_other:
            income_by_category.append({"name": "Admin Other Income", "value": af_other})
        if sf_other:
            income_by_category.append({"name": "Sinking Other Income", "value": sf_other})
        income_by_category = [i for i in income_by_category if i["value"] > 0]

    # Build quarterly trend: 3 distinct series per quarter so bars are visually separate.
    # • levies   = quarterly budgeted levy (inc-GST) — what was planned
    # • income   = actual levy payments collected that quarter (from levy_payments collection)
    # • expenses = budgeted/actual expenses (annual ÷ 4)
    # Shape: {"month": "Q1 2026", "income": N, "levies": N, "expenses": N}
    quarterly_trend = []
    if levy:
        from utils.finance_helpers import get_levy_proposed_amounts
        af_qt, sf_qt = get_levy_proposed_amounts(levy)
        annual_ex_gst = round(af_qt + sf_qt, 2)
        trend_gst_config = parse_levy_gst_settings(settings_doc)
        annual_inc_gst = round(annual_ex_gst * trend_gst_config["gst_multiplier"], 2)
        annual_expenses = round(sum(
            _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
            or c.get("budgeted_amount", 0)
            for c in cats
        ), 2)
        q_levies_budget = round(annual_inc_gst / 4, 2)  # budgeted quarterly levy (inc-GST)
        q_expenses = round(annual_expenses / 4, 2)

        # quarterly_payments is already fetched above from levy_payments grouped by quarter
        payments_by_q = {p["_id"]: round(p["total"], 2) for p in quarterly_payments if p.get("_id")}

        _fy_start = int((settings_doc or {}).get("financial_year_start_month", 1))
        _due_months = (settings_doc or {}).get("levy_due_months") or [3, 6, 9, 12]
        _due_day_type = (settings_doc or {}).get("levy_due_day_type") or "first"
        _due_day = (settings_doc or {}).get("levy_due_day")
        _custom_dates = (settings_doc or {}).get("levy_due_custom_dates") or {}
        try:
            _year_int = int(str(year).split("-")[0])
        except (ValueError, TypeError):
            _year_int = date.today().year
        q_due_dates = _compute_period_due_dates(
            _year_int, _due_months, _due_day_type, _due_day, 4, _custom_dates,
            fy_start_month=_fy_start,
        )
        today_str = date.today().isoformat()

        q_labels = ["Q1", "Q2", "Q3", "Q4"]
        for idx, q_label in enumerate(q_labels):
            key = f"{q_label} {year}"
            due_date = q_due_dates[idx] if idx < len(q_due_dates) else None
            has_started = not due_date or due_date <= today_str
            # Actual collected: from levy_payments if we have data; 0 otherwise (honest for historical years)
            actual_collected = payments_by_q.get(q_label, 0) if has_started else 0
            quarterly_trend.append({
                "month": key,
                "income": actual_collected,  # actual levy payments collected this quarter
                "levies": q_levies_budget if has_started else 0,  # budgeted quarterly levy (inc-GST)
                "expenses": q_expenses if has_started else 0,  # budgeted/actual quarterly expenses
                "due_date": due_date,
                "status": "started" if has_started else "upcoming",
            })

    return {
        "expense_by_category": {
            "administrative": admin_expenses,
            "sinking": sinking_expenses,
        },
        "income_by_category": income_by_category,
        "gst_summary": gst_summary,
        "monthly_trend": quarterly_trend,  # key matches frontend TremorBar index="month"
        "year": year,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Budget vs Actual
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/budget-vs-actual")
async def get_budget_vs_actual(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get budget vs actual comparison for expense categories.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        # Resolves via _resolve_default_levy_year (never a not-yet-started year) — see
        # module note above get_available_years.
        year = await _resolve_default_levy_year(building_id)

    if not year:
        return {"administrative": [], "sinking": []}

    cats = _normalize_cats_fund_type(await db.levy_categories.find(
        {"year": year, "building_id": building_id}, {"_id": 0}
    ).to_list(100))

    tx_actual_by_id, tx_actual_by_name = await _get_actual_overrides_from_financial_transactions(
        year=year,
        building_id=building_id,
    )

    admin_comparison = [
        {
            "category": c["name"],
            "budget": round(c.get("budgeted_amount", 0), 2),
            "actual": _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name),
        }
        for c in cats if c.get("fund_type") == "administrative"
    ]
    sinking_comparison = [
        {
            "category": c["name"],
            "budget": round(c.get("budgeted_amount", 0), 2),
            "actual": _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name),
        }
        for c in cats if c.get("fund_type") == "sinking"
    ]

    return {
        "year": year,
        "administrative": admin_comparison,
        "sinking": sinking_comparison,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Quarterly Budget View
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/quarterly-budget")
async def get_quarterly_budget(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Returns per-quarter budget vs actual vs pending income data.

    Income is derived from unit_levy_ledger (authoritative — includes DEFT/bank imports).
    Expenses are derived from levy_categories budgeted_amount + financial_transactions
    expense actuals (with levy_categories.actual_amount as legacy/manual fallback).
    Quarters are based on the payment_schedule in annual_levies.

    Multi-tenant: all queries are scoped by building_id.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        # Resolves via _resolve_default_levy_year (never a not-yet-started year) — see
        # module note above get_available_years.
        year = await _resolve_default_levy_year(building_id)

    if not year:
        return {"year": None, "quarters": []}

    # Fetch levy config, categories, ledger, building settings, and portal payments in parallel.
    # Settings provide the authoritative FY start month and quarter due dates.
    levy_task = db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})
    cats_task = db.levy_categories.find(
        {"year": year, "building_id": building_id}, {"_id": 0}
    ).to_list(200)
    ledger_task = db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year}, {"_id": 0}
    ).to_list(200)
    settings_task = _get_general_settings(building_id, {"_id": 0})
    payments_task = db.levy_payments.aggregate([
        {"$match": {"building_id": building_id, "year": year}},
        {"$group": {
            "_id": "$quarter",
            "confirmed": {"$sum": {"$cond": [{"$eq": ["$status", "confirmed"]}, "$amount", 0]}},
            "pending": {"$sum": {"$cond": [{"$eq": ["$status", "pending_verification"]}, "$amount", 0]}},
        }}
    ]).to_list(10)

    levy, cats, ledger_entries, settings_doc, payments_agg = await asyncio.gather(
        levy_task, cats_task, ledger_task, settings_task, payments_task
    )
    cats = _normalize_cats_fund_type(cats)

    if not levy:
        return {"year": year, "quarters": []}

    # ── Quarter due dates: always computed from live settings (never from stale stored schedule).
    # Settings hold fy_start_month (1=Jan, 7=Jul, etc.) which controls year-rollover logic.
    _s = settings_doc or {}
    _fy_start = int(_s.get("financial_year_start_month", 1))
    _due_months = _s.get("levy_due_months") or [3, 6, 9, 12]
    _due_day_type = _s.get("levy_due_day_type") or "custom"
    _due_day = _s.get("levy_due_day")
    _custom_dates = _s.get("levy_due_custom_dates") or {}
    _freq = _s.get("levy_collection_frequency", "quarterly")
    _freq_map = {"quarterly": 4, "half_yearly": 2, "monthly": 12, "yearly": 1}
    num_periods = _freq_map.get(_freq, 4)

    # levy_year is the START calendar year of the FY (e.g. "2026" → 2026)
    _cal_year_str = str(year).split("-")[0]
    try:
        _cal_year = int(_cal_year_str)
    except ValueError:
        _cal_year = 2026

    _fy_label = get_fy_label(_cal_year, _fy_start)
    _fy_start_date, _fy_end_date = get_fy_date_range(_cal_year, _fy_start)

    _computed_due_dates = _compute_period_due_dates(
        _cal_year, _due_months, _due_day_type, _due_day,
        num_periods, _custom_dates, fy_start_month=_fy_start,
    )
    quarters_info = [
        {"label": f"Q{i + 1}", "due_date": d}
        for i, d in enumerate(_computed_due_dates)
    ]

    # ── Income budget: use proposed annual amounts (not YTD levy_income for partial/current years).
    # levy_income in annual_levies stores YTD actual collected for the current year —
    # proposed_admin_expenses / proposed_sinking_expenses hold the correct annual budget figure.
    from utils.finance_helpers import get_levy_proposed_amounts
    admin_ex_gst, sinking_ex_gst = get_levy_proposed_amounts(levy)
    total_ex_gst = round(admin_ex_gst + sinking_ex_gst, 2)
    gst_config = parse_levy_gst_settings(settings_doc)
    effective_gst_rate = gst_config["effective_gst_rate"]
    gst_annual = round(total_ex_gst * effective_gst_rate, 2)
    total_inc_gst = round(total_ex_gst + gst_annual, 2)  # = what owners actually pay
    budgeted_income_ex_gst_per_q = round(total_ex_gst / num_periods, 2)
    budgeted_gst_per_q = round(gst_annual / num_periods, 2)
    budgeted_income_inc_gst_per_q = round(total_inc_gst / num_periods, 2)

    # ── Expense budget from levy_categories (these are ex-GST operational costs).
    # Note: Sinking Fund categories are CAPITAL reserves — they accumulate for future
    # large works and are NOT expected to equal the annual sinking fund levy.
    admin_budget_annual = round(
        sum(c.get("budgeted_amount", 0) for c in cats if c.get("fund_type") == "administrative"), 2)
    sinking_budget_annual = round(sum(c.get("budgeted_amount", 0) for c in cats if c.get("fund_type") == "sinking"), 2)
    total_expense_budget_annual = round(admin_budget_annual + sinking_budget_annual, 2)
    tx_actual_by_id: dict[tuple[str, str], float] = {}
    tx_actual_by_name: dict[tuple[str, str], float] = {}
    if cats:
        tx_actual_by_id, tx_actual_by_name = await _get_actual_overrides_from_financial_transactions(
            year=year,
            building_id=building_id,
        )

    admin_actual_annual = round(sum(
        _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
        for c in cats if c.get("fund_type") == "administrative"
    ), 2)
    sinking_actual_annual = round(sum(
        _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
        for c in cats if c.get("fund_type") == "sinking"
    ), 2)
    total_expense_actual_annual = round(admin_actual_annual + sinking_actual_annual, 2)
    budgeted_expenses_per_q = round(total_expense_budget_annual / num_periods, 2)
    actual_expenses_per_q = round(total_expense_actual_annual / num_periods, 2)

    # ── Real collected/levied data from unit_levy_ledger.
    # unit_levy_ledger.quarters_charged does NOT exist in production: confirmed live
    # 2026-08-01 -- 0 of 522 East Gate documents (spanning 6 years) have this field
    # set, and grepping every write path in the entire backend shows nothing in live
    # application code ever sets it (the only two writers were one-off Mongo CLI
    # backfill scripts -- scripts/db/import_q1_2026_payments.py and
    # scripts/db/import_civium_payments.py -- never the payment-posting/ledger-rebuild
    # runtime paths). Grouping by it here always produced an empty bucket, so
    # has_ledger_data was permanently False for every quarter, on every building,
    # regardless of what was actually raised -- this is a generic bug, not East-Gate
    # data corruption: the field was never wired into any live write path anywhere.
    #
    # Mongo's unit_levy_ledger is annual-only (one doc per unit per year) -- there is
    # genuinely no live per-quarter breakdown to read, for any building, until this
    # route is promoted to the Postgres finance.levy_runs/levy_items model (which DOES
    # track per-quarter "raised" state via row presence -- see
    # FinancialReadService.get_oc_levy_summary() and the "finance.quarterly_budget"
    # route policy in finance_route_cutover_service.py). Until that promotion,
    # compute_mongo_quarter_statuses() is this codebase's already-established, tested
    # approximation for exactly this gap (used identically for the per-unit dashboard
    # at get_unit_dashboard_overview) -- it splits the annual total evenly across the
    # schedule and waterfalls the amount actually paid against periods in due-date
    # order, rather than depending on a field nothing populates.
    # Roll up levied/collected/outstanding per-unit WITHOUT netting credits against arrears.
    # The previous `total_levied - Σ signed(net_balance)` collapsed to Σ paid_i the moment any
    # owner paid ahead — netting overpayers against underpayers and understating every quarter's
    # Outstanding (the 2026 Q2 "$2,275.50 Outstanding" bug). Outstanding is a per-unit obligation
    # (CLAUDE.md "Arrears Are a Per-Unit Obligation — Never Netted Across Units"): a credit unit
    # contributes 0 to outstanding and its own levy counts as paid, but its excess never offsets
    # another unit's shortfall. We also derive collected from (levied - outstanding) rather than
    # the raw ledger total_paid, which can be a back-solved cumulative-since-inception figure.
    _ledger_rollup = sum_ledger_collected_outstanding(ledger_entries)
    total_levied = _ledger_rollup["total_levied"]
    total_paid = _ledger_rollup["total_collected"]

    now_str = _now()[:10]  # YYYY-MM-DD
    schedule_for_waterfall = [
        {"quarter": qi["label"], "due_date": qi["due_date"]}
        for qi in quarters_info
        if not qi["due_date"] or qi["due_date"] <= now_str
    ]
    waterfall_by_label = {
        w["quarter"]: w
        for w in compute_mongo_quarter_statuses(schedule_for_waterfall, total_levied, total_paid)
    }

    payments_by_quarter = {p["_id"]: p for p in payments_agg if p.get("_id")}

    # ── Build per-quarter cards.
    quarters_output = []
    for i, q_info in enumerate(quarters_info):
        label = q_info["label"]
        due_date = q_info["due_date"]

        # Real levied/paid data for this period in the Mongo annual-ledger fallback.
        # Mongo has no genuine per-period levy-item rows, so split the YTD levied/paid
        # amount only across periods whose due date has started. Splitting across all
        # configured periods would halve Q1/Q2 during August for a quarterly building.
        real = waterfall_by_label.get(label, {})
        due_date_started = not due_date or due_date <= now_str
        real_levied = round(real.get("amount_due", 0), 2) if due_date_started else 0.0
        real_paid = round(real.get("amount_paid", 0), 2) if due_date_started else 0.0
        outstanding = round(real.get("outstanding", 0), 2) if due_date_started else 0.0

        # Portal-level supplemental data
        portal_data = payments_by_quarter.get(label, {})
        portal_confirmed = round(portal_data.get("confirmed", 0), 2)
        portal_pending = round(portal_data.get("pending", 0), 2)

        # Quarter status — a quarter whose due date has passed AND still carries an
        # unpaid balance is "overdue", not merely "past". "past" alone reads as a
        # neutral historical record and doesn't signal that collection is needed.
        if due_date and due_date < now_str:
            status = "overdue" if outstanding > 0.01 else "past"
        elif due_date and due_date == now_str:
            status = "due_today"
        elif not due_date:
            status = "unknown"
        else:
            status = "upcoming"

        # has_ledger_data: True once this year has any real levied amount at all (a
        # genuine annual_levies/unit_levy_ledger record exists, not just a future
        # budget projection with zero levied data) -- the most honest signal Mongo's
        # annual-only model can give for "has this quarter's levy actually been
        # raised," since it has no true per-quarter granularity to check against.
        has_ledger_data = total_levied > 0 and due_date_started

        quarters_output.append({
            "label": label,
            "due_date": due_date,
            "status": status,
            # Income budget (what should be collected from all owners this quarter)
            "budgeted_income_ex_gst": budgeted_income_ex_gst_per_q,
            "budgeted_gst": budgeted_gst_per_q,
            "budgeted_income_inc_gst": budgeted_income_inc_gst_per_q,
            # Real data from unit_levy_ledger (authoritative; 0 if not yet raised)
            "levied": real_levied,
            "collected": real_paid,
            "outstanding": outstanding,
            "has_ledger_data": has_ledger_data,
            # Portal-imported payments (supplement — partial coverage only)
            "portal_confirmed": portal_confirmed,
            "portal_pending": portal_pending,
            # Expenses
            "budgeted_expenses": budgeted_expenses_per_q,
            "actual_expenses": actual_expenses_per_q,
        })

    _quarterly_budget_response = {
        "year": year,
        "fy_label": _fy_label,  # e.g. "FY 2026" or "FY 2025-26"
        "fy_start_date": _fy_start_date,  # e.g. "2026-01-01"
        "fy_end_date": _fy_end_date,  # e.g. "2026-12-31"
        "fy_start_month": _fy_start,
        "gst_registered": gst_config["gst_registered"],
        "gst_rate": gst_config["levy_gst_rate"],
        "effective_gst_rate": effective_gst_rate,
        "gst_label": gst_config["gst_label"],
        "annual_totals": {
            "admin_ex_gst": admin_ex_gst,
            "sinking_ex_gst": sinking_ex_gst,
            "total_ex_gst": total_ex_gst,
            "gst": gst_annual,
            "total_inc_gst": total_inc_gst,
            "total_levied_ytd": total_levied,
            "total_collected_ytd": total_paid,
            # Sum of per-unit amounts still owed (Σ max(net_balance,0)) — never a netted
            # building-wide figure; credits are surfaced separately, not offset against arrears.
            "total_outstanding_ytd": _ledger_rollup["total_outstanding"],
            "total_expenses_budgeted": total_expense_budget_annual,
            "total_expenses_actual": total_expense_actual_annual,
        },
        "quarters": quarters_output,
    }

    # Dispatch through the same cutover-readiness mechanism every other finance.py
    # route uses (never hardcode "always Mongo" for a domain with a Postgres path —
    # see CLAUDE.md's Data-Source Precedence rule). This route's policy currently has
    # postgres_read_supported=False/shadow_supported=False (no PG query or shadow
    # comparator exists yet), so source always resolves "mongo" and run_shadow is
    # always False today — registering it here still makes it visible in the
    # cutover readiness table for future promotion, instead of this route silently
    # never appearing in that inventory at all.
    route_state = await get_finance_route_runtime_state(
        building_id=building_id,
        route_key="finance.quarterly_budget",
    )
    if route_state.get("run_shadow"):
        asyncio.create_task(maybe_run_finance_shadow(
            building_id=building_id,
            route_key="finance.quarterly_budget",
            mongo_payload=_quarterly_budget_response,
        ))
    return _quarterly_budget_response


# ─────────────────────────────────────────────────────────────────────────────
# Levy Categories
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/levy-categories", response_model=List[LevyCategoryResponse])
async def get_levy_categories(
        year: Optional[str] = None,
        fund_type: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get levy expense categories, optionally filtered by year and fund type.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Exclude soft-archived categories — the canonical-consolidation repair archives
    # duplicate spellings of an economic category (merged_into a keeper doc); without
    # this filter those merged duplicates would still render as rows on the page.
    query: dict = {"building_id": building_id, "is_archived": {"$ne": True}}
    if year:
        query["year"] = year
    if fund_type:
        query["fund_type"] = _levy_category_fund_filter(fund_type)

    cats = _normalize_cats_fund_type(
        await db.levy_categories.find(query, {"_id": 0}).sort("name", 1).to_list(500)
    )
    for cat in cats:
        cat.setdefault("budgeted_amount", 0.0)
        cat.setdefault("actual_amount", 0.0)
        cat.setdefault("description", None)
        cat.setdefault("status", "proposed")
    return [LevyCategoryResponse(**c) for c in cats]


@router.get("/levy-categories/budget-summary")
async def get_levy_categories_budget_summary(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Fund-level proposed budget vs actual for the spending-categories page.

    Why this exists (GAP-FIN-041): for buildings whose finances were imported from an AGM
    PDF/CSV, the proposed budget is stored **only at fund level** in `annual_levies`
    (`proposed_admin_expenses` / `proposed_sinking_expenses`, read via the canonical
    `get_levy_proposed_amounts()`) — there is **no per-category proposed breakdown**. So the
    per-category `budgeted_amount` column is a genuine `$0` for those categories, and a
    per-category variance is not computable from stored data. Rather than fabricate a split,
    this endpoint surfaces the honest figure the data supports: each fund's proposed budget
    total, its `Σ actual`, and the variance — plus `has_itemised_budget` so the UI can say
    "budget not itemised" where per-category budgets were never captured.

    Actuals use the SAME resolution as `/finance/budget-vs-actual`
    (`_resolve_category_actual_amount` over `financial_transactions` + stored `actual_amount`),
    so the two pages never disagree. `missing` (no annual_levies doc for the year) is reported
    as its own state — never silently coerced to `$0`, per the finance-integrity rule.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        year = await _resolve_default_levy_year(building_id)
    if not year:
        return {"year": None, "available": False, "administrative": None, "sinking": None}

    levy_task = db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})
    cats_task = db.levy_categories.find(
        {"year": year, "building_id": building_id, "is_archived": {"$ne": True}}, {"_id": 0}
    ).to_list(500)
    levy, cats = await asyncio.gather(levy_task, cats_task)
    cats = _normalize_cats_fund_type(cats)

    # Prior year is needed so a year's OPENING balance can fall back to the prior year's CLOSING
    # balance (opening[y] == closing[y-1]) when the year's own opening field isn't stored.
    try:
        _prior_year = str(int(str(year)[:4]) - 1)
    except (ValueError, TypeError):
        _prior_year = None
    prior_levy = (
        await db.annual_levies.find_one({"year": _prior_year, "building_id": building_id}, {"_id": 0})
        if _prior_year else None
    )

    tx_actual_by_id, tx_actual_by_name = await _get_actual_overrides_from_financial_transactions(
        year=year, building_id=building_id,
    )

    from utils.finance_helpers import get_levy_proposed_amounts
    # `available` distinguishes "no proposed budget on record" (missing) from a real $0 budget.
    if levy:
        admin_proposed, sinking_proposed = get_levy_proposed_amounts(levy)
        available = True
    else:
        admin_proposed, sinking_proposed = None, None
        available = False

    # annual_levies stores fund position under admin_fund/sinking_fund sub-docs. Balance fields are
    # inconsistent across importers: some write dollar floats (opening_balance/closing_balance),
    # others integer cents (opening_balance_cents/closing_balance_cents) — so read both.
    _fund_doc_key = {"administrative": "admin_fund", "sinking": "sinking_fund"}

    def _read_balance(fund: dict, keys: tuple) -> Optional[float]:
        """First non-empty of `keys`, converting a `*_cents` field to dollars. None if none set."""
        for k in keys:
            v = (fund or {}).get(k)
            if v in (None, ""):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            return round(fv / 100, 2) if k.endswith("_cents") else round(fv, 2)
        return None

    def _fund_summary(fund_key: str, proposed):
        fund_doc = (levy or {}).get(_fund_doc_key[fund_key], {}) or {}
        prior_fund_doc = (prior_levy or {}).get(_fund_doc_key[fund_key], {}) or {}
        # "What remains" = the SAME canonical figure /financials shows (get_annual_fund_balance:
        # current_balance → closing_balance_actual → closing_balance → opening_balance), with a cents
        # fallback. Missing is reported as None, never coerced to $0 (finance-integrity rule).
        _cb, _cb_src = get_annual_fund_balance(fund_doc)
        closing_balance = _cb if _cb_src != "missing" else _read_balance(
            fund_doc, ("current_balance_cents", "closing_balance_cents")
        )
        # Opening reserve carried in. Prefer the year's own opening field (dollars or cents); if it
        # isn't stored, derive it from the PRIOR year's closing balance (opening[y] == closing[y-1]).
        opening_balance = _read_balance(
            fund_doc, ("opening_balance", "opening_balance_actual", "opening_balance_cents")
        )
        if opening_balance is None and prior_fund_doc:
            _pb, _pb_src = get_annual_fund_balance(prior_fund_doc)
            opening_balance = _pb if _pb_src != "missing" else _read_balance(
                prior_fund_doc, ("current_balance_cents", "closing_balance_cents")
            )
        fund_cats = [c for c in cats if c.get("fund_type") == fund_key]
        actual = round(sum(
            _resolve_category_actual_amount(c, tx_actual_by_id, tx_actual_by_name)
            for c in fund_cats
        ), 2)
        # A budget is "itemised" only if at least one category carries a real per-category figure.
        has_itemised = any(float(c.get("budgeted_amount") or 0) > 0 for c in fund_cats)
        itemised_total = round(sum(float(c.get("budgeted_amount") or 0) for c in fund_cats), 2)
        # Authoritative TOTAL = the fund-level proposed from annual_levies (the SAME canonical
        # figure /financials shows via get_levy_proposed_amounts). Per-category budgets are
        # a breakdown that may be INCOMPLETE — e.g. East Gate LY2023 admin itemises only $91,355 of
        # a $221,316 adopted budget. A partial itemisation must NOT override the authoritative total,
        # or this page disagrees with /finance (the exact bug this fixes). Itemised total is used
        # only as a fallback when annual_levies carries no fund-level figure at all.
        if proposed is not None and proposed > 0:
            proposed_budget, budget_source = proposed, "annual_levies"
        elif has_itemised:
            proposed_budget, budget_source = itemised_total, "categories"
        elif proposed is not None:
            proposed_budget, budget_source = proposed, "annual_levies"  # genuine $0 fund budget
        else:
            proposed_budget, budget_source = None, "missing"
        # How much of the authoritative total is itemised per-category (for a "remainder not
        # itemised" note); None when there is no authoritative total or no itemisation to compare.
        unitemised_remainder = (
            round(proposed_budget - itemised_total, 2)
            if (proposed_budget is not None and has_itemised) else None
        )
        variance = round(actual - proposed_budget, 2) if proposed_budget is not None else None
        return {
            "proposed_budget": proposed_budget,
            "actual": actual,
            "variance": variance,
            "has_itemised_budget": has_itemised,
            "itemised_total": itemised_total,
            "unitemised_remainder": unitemised_remainder,
            "budget_source": budget_source,
            "category_count": len(fund_cats),
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
        }

    return {
        "year": year,
        "available": available,
        "administrative": _fund_summary("administrative", admin_proposed),
        "sinking": _fund_summary("sinking", sinking_proposed),
    }


@router.get("/finance/portal-actuals")
async def get_portal_actuals(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Return portal-sourced financial actuals from the Strata Web scraper.

    Data source: strata_financials (written exclusively by the Strata Web scraper).
    These are the portal's planned vs actual category figures. They are surfaced
    alongside levy_categories so the UI can display a side-by-side comparison
    without copying data between collections (single source of truth rule).

    Australian financial year mapping:
      levy year "2026"  →  financial_year "2025-2026"
      levy year "2025"  →  financial_year "2024-2025"
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Resolve year from annual_levies if not provided. Resolves via
    # _resolve_default_levy_year (never a not-yet-started year) — see module note
    # above get_available_years.
    if not year:
        year = await _resolve_default_levy_year(building_id, fallback=str(date.today().year))

    # Normalise: "2026-27" → "2026"
    if year and len(year) > 4:
        year = year[:4]

    # Map levy year to Australian financial year stored by the scraper
    try:
        fy_start = int(year) - 1
    except (ValueError, TypeError):
        fy_start = date.today().year - 1
    financial_year = f"{fy_start}-{year}"

    docs = await db.strata_financials.find(
        {"building_id": building_id, "financial_year": financial_year},
        {"_id": 0, "building_id": 0},
    ).sort("category", 1).to_list(200)

    return {
        "year": year,
        "financial_year": financial_year,
        "categories": docs,
        "totals": {
            "admin": {
                "planned": round(sum(float(d.get("planned") or 0) for d in docs if d.get("fund") == "admin"), 2),
                "actual": round(sum(float(d.get("actual") or 0) for d in docs if d.get("fund") == "admin"), 2),
            },
            "capital_works": {
                "planned": round(sum(float(d.get("planned") or 0) for d in docs if d.get("fund") == "capital_works"),
                                 2),
                "actual": round(sum(float(d.get("actual") or 0) for d in docs if d.get("fund") == "capital_works"), 2),
            },
        },
    }


@router.post("/levy-categories", response_model=LevyCategoryResponse)
async def create_levy_category(
        data: LevyCategoryCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new levy category. Requires can_manage_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **data.model_dump(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.levy_categories.insert_one(doc)
    return LevyCategoryResponse(**doc)


@router.put("/levy-categories/{category_id}", response_model=LevyCategoryResponse)
async def update_levy_category(
        category_id: str,
        data: LevyCategoryUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update a levy category's actual or budgeted amount.
    Requires can_manage_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = _now()

    await db.levy_categories.update_one({"id": category_id, "building_id": building_id}, {"$set": update_data})
    cat = await db.levy_categories.find_one({"id": category_id, "building_id": building_id}, {"_id": 0})
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return LevyCategoryResponse(**cat)


@router.delete("/levy-categories/{category_id}", response_model=MessageAck)
async def delete_levy_category(
        category_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a levy category. Requires can_manage_finances."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.levy_categories.delete_one({"id": category_id, "building_id": building_id})
    return {"message": "Category deleted"}


# ─────────────────────────────────────────────────────────────────────────────
# Budget Proposals — CPI-adjusted draft budget for a new year
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/budget-proposals")
async def get_budget_proposals(
        base_year: str,
        target_year: str,
        inflation_rate: float = 3.0,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Generate CPI-adjusted budget proposals for a target year based on a prior year.

    For each category in base_year:
      - base_amount = actual_amount if actual_amount > 0 else budgeted_amount
      - proposed_amount = base_amount * (1 + inflation_rate / 100)

    If target_year already has saved levy_categories with status="proposed",
    those amounts are returned with approved=True and their saved amounts.

    Requires can_view_finances.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Performance Optimization⚡: Parallelize fetching of base categories and saved proposals
    # to reduce endpoint latency.
    base_cats_task = db.levy_categories.find(
        {"building_id": building_id, "year": base_year}, {"_id": 0}
    ).sort("name", 1).to_list(200)

    # Fetch any already-saved proposals for target_year (status=proposed)
    saved_cats_task = db.levy_categories.find(
        {"building_id": building_id, "year": target_year, "status": "proposed"}, {"_id": 0}
    ).to_list(200)

    base_cats, saved_cats = await asyncio.gather(base_cats_task, saved_cats_task)
    base_cats = _normalize_cats_fund_type(base_cats)
    saved_cats = _normalize_cats_fund_type(saved_cats)

    if not base_cats:
        raise HTTPException(
            status_code=404,
            detail=f"No levy categories found for base year {base_year}"
        )
    saved_map = {(c["fund_type"], c["name"]): c for c in saved_cats}

    items = []
    for cat in base_cats:
        fund_type = cat.get("fund_type", "administrative")
        name = cat["name"]
        actual = cat.get("actual_amount", 0.0) or 0.0
        budgeted = cat.get("budgeted_amount", 0.0) or 0.0
        base_amount = actual if actual > 0 else budgeted
        proposed = round(base_amount * (1 + inflation_rate / 100), 2)

        saved = saved_map.get((fund_type, name))
        item = {
            "fund_type": fund_type,
            "name": name,
            "prior_year_actual": round(actual, 2),
            "prior_year_budgeted": round(budgeted, 2),
            "proposed_amount": proposed,
            "amended_amount": round(saved["budgeted_amount"], 2) if saved else None,
            "approved": saved is not None,
        }
        items.append(item)

    admin_total = round(sum(
        (i["amended_amount"] or i["proposed_amount"])
        for i in items if i["fund_type"] == "administrative" and i["approved"]
    ), 2)
    sinking_total = round(sum(
        (i["amended_amount"] or i["proposed_amount"])
        for i in items if i["fund_type"] == "sinking" and i["approved"]
    ), 2)

    # Levy impact preview — compute proposed per-UOE rates for approved items
    grand_total = round(admin_total + sinking_total, 2)
    existing_levy = await db.annual_levies.find_one(
        {"building_id": building_id, "year": target_year},
        {"_id": 0, "total_uoe": 1},
    )
    total_uoe = (existing_levy or {}).get("total_uoe") or TOTAL_UOE
    total_uoe = total_uoe or TOTAL_UOE
    levy_impact = {
        "total_uoe": total_uoe,
        "proposed_admin_per_uoe_annual": round(admin_total / total_uoe, 6) if total_uoe else 0.0,
        "proposed_sinking_per_uoe_annual": round(sinking_total / total_uoe, 6) if total_uoe else 0.0,
        "proposed_total_per_uoe_annual": round(grand_total / total_uoe, 6) if total_uoe else 0.0,
        "proposed_total_per_uoe_quarterly": round(grand_total / total_uoe / 4, 6) if total_uoe else 0.0,
    }

    return {
        "base_year": base_year,
        "target_year": target_year,
        "inflation_rate": inflation_rate,
        "admin_total": admin_total,
        "sinking_total": sinking_total,
        "grand_total": grand_total,
        "levy_impact": levy_impact,
        "items": items,
    }


@router.post("/budget-proposals", response_model=BudgetProposalResponse)
async def save_budget_proposals(
        data: BudgetProposalCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Save approved budget proposals as levy_categories for the target year.

    Only items with approved=True are saved.
    Each item's final amount = amended_amount ?? proposed_amount.
    Any existing proposed (non-actual) levy_categories for target_year are replaced.

    Requires can_manage_finances.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    approved_items = [i for i in data.items if i.approved]
    if not approved_items:
        raise HTTPException(status_code=400, detail="No items approved — nothing to save")

    now = _now()

    # Delete existing proposed (not actual) categories for target_year
    await db.levy_categories.delete_many({
        "building_id": building_id,
        "year": data.target_year,
        "status": "proposed",
    })

    # Insert approved categories
    docs = []
    for item in approved_items:
        final_amount = item.amended_amount if item.amended_amount is not None else item.proposed_amount
        docs.append({
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "year": data.target_year,
            "status": "proposed",
            "fund_type": item.fund_type,
            "name": item.name,
            "budgeted_amount": round(final_amount, 2),
            "actual_amount": 0.0,
            "description": f"CPI {data.inflation_rate}% from {data.base_year}",
            "created_at": now,
            "updated_at": now,
        })

    if docs:
        await db.levy_categories.insert_many(docs)

    # Compute totals by fund type
    admin_total = round(sum(
        (i.amended_amount or i.proposed_amount)
        for i in approved_items if i.fund_type == "administrative"
    ), 2)
    sinking_total = round(sum(
        (i.amended_amount or i.proposed_amount)
        for i in approved_items if i.fund_type == "sinking"
    ), 2)
    grand_total = round(admin_total + sinking_total, 2)

    # ── Levy impact: compute per-UOE proposed rates and per-unit preview ───────
    # Fetch the existing levy record (if any) for total_uoe; fall back to TOTAL_UOE constant.
    existing_levy = await db.annual_levies.find_one(
        {"building_id": building_id, "year": data.target_year},
        {"_id": 0, "total_uoe": 1},
    )
    total_uoe = (existing_levy or {}).get("total_uoe") or TOTAL_UOE
    total_uoe = total_uoe or TOTAL_UOE  # guard against 0

    proposed_admin_per_uoe_annual = round(admin_total / total_uoe, 6) if total_uoe else 0.0
    proposed_sinking_per_uoe_annual = round(sinking_total / total_uoe, 6) if total_uoe else 0.0
    proposed_total_per_uoe_annual = round(grand_total / total_uoe, 6) if total_uoe else 0.0

    # Optional: compute a quick per-unit levy preview (top units by UOE)
    sample_units = await db.units.find(
        {"building_id": building_id, "entitlement": {"$gt": 0}},
        {"_id": 0, "unit_number": 1, "unit_type": 1, "entitlement": 1},
    ).sort("unit_number", 1).to_list(10)

    levy_preview = [
        {
            "unit_number": u["unit_number"],
            "unit_type": u.get("unit_type", ""),
            "uoe": u["entitlement"],
            "proposed_admin_annual": round(proposed_admin_per_uoe_annual * u["entitlement"], 2),
            "proposed_sinking_annual": round(proposed_sinking_per_uoe_annual * u["entitlement"], 2),
            "proposed_total_annual": round(proposed_total_per_uoe_annual * u["entitlement"], 2),
            "proposed_total_quarterly": round(proposed_total_per_uoe_annual * u["entitlement"] / 4, 2),
        }
        for u in sample_units
    ]

    # Upsert annual_levies for target_year with proposed totals and per-UOE rates
    await db.annual_levies.update_one(
        {"building_id": building_id, "year": data.target_year},
        {"$set": {
            "proposed_admin_expenses": admin_total,
            "proposed_sinking_expenses": sinking_total,
            "proposed_admin_levy_per_uoe_annual": proposed_admin_per_uoe_annual,
            "proposed_sinking_levy_per_uoe_annual": proposed_sinking_per_uoe_annual,
            "proposed_total_levy_per_uoe_annual": proposed_total_per_uoe_annual,
            "updated_at": now,
        }},
        upsert=False,  # only update if exists; don't create a full levy doc here
    )

    return {
        "status": "saved",
        "target_year": data.target_year,
        "base_year": data.base_year,
        "inflation_rate": data.inflation_rate,
        "categories_saved": len(docs),
        "admin_total": admin_total,
        "sinking_total": sinking_total,
        "grand_total": grand_total,
        "levy_impact": {
            "total_uoe": total_uoe,
            "proposed_admin_per_uoe_annual": proposed_admin_per_uoe_annual,
            "proposed_sinking_per_uoe_annual": proposed_sinking_per_uoe_annual,
            "proposed_total_per_uoe_annual": proposed_total_per_uoe_annual,
            "proposed_total_per_uoe_quarterly": round(proposed_total_per_uoe_annual / 4, 6),
        },
        "levy_preview": levy_preview,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Unit Levy Ledger
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/unit-levy-ledger", response_model=List[UnitLevyLedgerResponse])
async def get_unit_levy_ledger(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get per-unit levy ledger for a given year.
    Defaults to most recent year. Returns all units sorted by unit_number.
    Owner names are resolved from user_units/units for the given year.
    Requires can_view_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        # Resolves via _resolve_default_levy_year (never a not-yet-started year) — see
        # module note above get_available_years.
        year = await _resolve_default_levy_year(building_id)

    if not year:
        return []

    # Year-accurate owner resolution via user_units date ranges.
    # For a calendar year "YYYY", an owner was active during that year if:
    #   start_date <= "{year}-12-31"  AND
    #   (is_active=True  OR  actual_end_date >= "{year}-01-01")
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"

    # Fetch all rows for this building/year contract. This endpoint currently has
    # no pagination parameter, so applying a hard to_list(200) cap silently drops
    # units in larger schemes. Keep the three independent reads concurrent to
    # avoid sequential round-trips while preserving the all-unit response shape.
    entries_task = db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year}, {"_id": 0}
    ).sort("unit_number", 1).to_list(None)

    units_task = db.units.find(
        {"building_id": building_id},
        {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1}
    ).to_list(None)

    user_unit_task = db.user_units.find(
        {
            "building_id": building_id,
            "role_at_unit": "owner",
            "start_date": {"$lte": year_end},
        },
        {"_id": 0, "unit_number": 1, "user_id": 1, "is_active": 1,
         "is_primary": 1, "start_date": 1, "actual_end_date": 1}
    ).to_list(1000)
    settings_task = _get_general_settings(building_id, {"_id": 0})

    entries, unit_docs, user_unit_docs, settings_doc = await asyncio.gather(
        entries_task, units_task, user_unit_task, settings_task,
    )

    due_months = (settings_doc or {}).get("levy_due_months") or [3, 6, 9, 12]
    due_day_type = (settings_doc or {}).get("levy_due_day_type") or "first"
    due_day = (settings_doc or {}).get("levy_due_day")
    custom_dates = (settings_doc or {}).get("levy_due_custom_dates") or {}
    fy_start_month = int((settings_doc or {}).get("financial_year_start_month") or 1)
    total_periods = len(due_months) if due_months else 4
    try:
        levy_year_int = int(str(year).split("-")[0])
    except (TypeError, ValueError):
        levy_year_int = date.today().year
    period_due_dates = _compute_period_due_dates(
        levy_year_int,
        due_months,
        due_day_type,
        due_day,
        total_periods,
        custom_dates,
        fy_start_month=fy_start_month,
    )
    period_schedule = [
        {"quarter": f"Q{i + 1}", "due_date": due_date}
        for i, due_date in enumerate(period_due_dates)
    ]
    today = date.today()

    # Build legacy fallback map: unit_number → {owner_name, owner_name_b}
    legacy_map: dict = {u["unit_number"]: u for u in unit_docs}

    # Filter to those whose tenure overlapped this year and build best-match map
    unit_to_user_id: dict = {}
    for doc in user_unit_docs:
        actual_end = doc.get("actual_end_date") or ""
        if not doc.get("is_active") and actual_end and actual_end < year_start:
            continue  # ownership ended before this year — skip
        un = doc["unit_number"]
        existing = unit_to_user_id.get(un)
        # Prefer is_active over inactive; prefer is_primary over non-primary
        if existing is None:
            unit_to_user_id[un] = doc
        elif doc.get("is_primary") and not existing.get("is_primary"):
            unit_to_user_id[un] = doc
        elif doc.get("is_active") and not existing.get("is_active"):
            unit_to_user_id[un] = doc

    # Bulk-fetch resolved users
    user_ids = list({d["user_id"] for d in unit_to_user_id.values()})
    users_by_id: dict = {}
    if user_ids:
        user_docs = await db.users.find(
            {"id": {"$in": user_ids}},
            {"_id": 0, "id": 1, "full_name": 1}
        ).to_list(None)
        users_by_id = {u["id"]: u for u in user_docs}

    # Build final owner map: unit_number → {owner_name, owner_name_b}
    owner_map: dict = {}
    for un, mapping in unit_to_user_id.items():
        user = users_by_id.get(mapping["user_id"], {})
        leg = legacy_map.get(un, {})
        owner_map[un] = {
            "owner_name": user.get("full_name") or leg.get("owner_name") or "Unknown",
            "owner_name_b": leg.get("owner_name_b"),  # co-owner always from units
        }
    # Fill gaps from legacy units for any unit not in user_units
    for un, leg in legacy_map.items():
        if un not in owner_map:
            owner_map[un] = {
                "owner_name": leg.get("owner_name") or "Unknown",
                "owner_name_b": leg.get("owner_name_b"),
            }

    # Merge owner names into ledger entries
    enriched = []
    for entry in entries:
        un = entry.get("unit_number", "")
        info = owner_map.get(un, {})
        entry_dict = dict(entry)
        entry_dict["owner_name"] = info.get("owner_name") or "Unknown"
        entry_dict["owner_name_b"] = info.get("owner_name_b")
        # total_paid on this row is not reliably scoped to this year (see
        # UnitLevyLedgerResponse.paid_this_year's docstring). The Levy Status table also
        # needs the as-of-today view: only levy periods whose configured due date has arrived
        # should count toward "levied" / "paid" status. Otherwise an annual/full-year ledger
        # projection with net_balance=0 makes every unit look paid for all four instalments
        # in August, before Q3/Q4 are due.
        annual_total_levied = round(float(entry_dict.get("total_levied") or 0), 2)
        annual_paid_this_year = round(
            annual_total_levied - float(entry_dict.get("net_balance") or 0), 2,
        )
        period_statuses = compute_mongo_quarter_statuses(
            period_schedule,
            annual_total_levied,
            annual_paid_this_year,
            today=today,
        )
        due_periods = [p for p in period_statuses if p.get("status") != "not_yet_due"]
        levied_due_to_date = round(sum(float(p.get("amount_due") or 0) for p in due_periods), 2)
        paid_due_to_date = round(sum(float(p.get("amount_paid") or 0) for p in due_periods), 2)
        outstanding_due_to_date = round(sum(float(p.get("outstanding") or 0) for p in due_periods), 2)

        entry_dict["annual_total_levied"] = annual_total_levied
        entry_dict["annual_paid_this_year"] = annual_paid_this_year
        entry_dict["levied_due_to_date"] = levied_due_to_date
        entry_dict["paid_due_to_date"] = paid_due_to_date
        entry_dict["outstanding_due_to_date"] = outstanding_due_to_date
        entry_dict["periods_due_to_date"] = len(due_periods)
        entry_dict["total_periods"] = len(period_statuses)
        entry_dict["paid_this_year"] = annual_paid_this_year
        # UnitLevyLedgerResponse declares created_at/updated_at as str, but some
        # documents (confirmed live, older/reconstructed rows) store a real
        # datetime instead of an ISO string -- Pydantic v2 does not auto-coerce
        # datetime -> str for a str-typed field, so those rows 500 the whole
        # endpoint response instead of returning the other units' data.
        for _dt_field in ("created_at", "updated_at"):
            _val = entry_dict.get(_dt_field)
            if isinstance(_val, datetime):
                entry_dict[_dt_field] = _val.isoformat()
        enriched.append(entry_dict)

    asyncio.create_task(_maybe_shadow_unit_levy_ledger(building_id, year, enriched))
    return [UnitLevyLedgerResponse(**e) for e in enriched]


@router.get("/unit-levy-ledger/{unit_number}")
async def get_unit_levy_ledger_by_unit(
        unit_number: str,
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get levy ledger history for a specific unit across all years.
    Non-admin users can only see their own unit.
    """
    unit_number = await resolve_canonical_unit_number(
        db, building_id, unit_number, rules=await _unit_display_rules_safe(building_id)
    )
    permissions = get_user_permissions(current_user)

    # Non-admin: restrict to own unit
    if not permissions.can_view_finances:
        if not user_unit_matches(current_user, unit_number):
            raise HTTPException(status_code=403, detail="Not authorized")

    query: dict = {"building_id": building_id, "unit_number": unit_number}
    if year:
        query["year"] = year

    # Performance Optimization⚡: Parallelize ledger entries and portal snapshot retrieval.
    entries_task = db.unit_levy_ledger.find(query, {"_id": 0}).sort("year", -1).to_list(10)

    # Cross-reference portal snapshot — read-only, additive only.
    # portal_net_balance is the Strata Web portal's current balance for this unit.
    # It does NOT replace net_balance (the authoritative levy accounting figure).
    # Surfaced purely so the UI can show a reconciliation cross-check.
    portal_task = db.strata_owners.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0, "balance": 1, "status": 1, "updated_at": 1},
    )

    # Interest/penalty an owner PAID on late levies is real transaction data — recorded as
    # levy_payments(payment_type="interest") and/or financial_transactions(transaction_type=
    # "interest"), tagged to the unit/year/fund. We derive it from those transactions here
    # (building-agnostic) rather than inferring it from a closing-balance gap. A stored
    # admin_interest/sinking_interest (populated only by the portal sync) still wins when present.
    # NOTE: for buildings whose history was reconstructed from budgets only (e.g. East Gate),
    # no interest transactions exist yet, so this correctly yields 0 until they are ingested.
    interest_lp_task = db.levy_payments.aggregate([
        {"$match": {"unit_number": unit_number, "payment_type": "interest",
                    "status": {"$ne": "rejected"}, **({"year": year} if year else {})}},
        {"$group": {"_id": {"year": "$year", "fund": "$fund_type"}, "total": {"$sum": "$amount"}}},
    ]).to_list(100)
    interest_ft_task = db.financial_transactions.aggregate([
        {"$match": {"transaction_type": "interest",
                    "$or": [{"unit_number": unit_number}, {"lot_number": unit_number}],
                    **({"financial_year": year} if year else {})}},
        {"$group": {"_id": {"year": "$financial_year", "fund": "$fund_type"}, "total": {"$sum": "$amount"}}},
    ]).to_list(100)

    entries, portal_doc, lp_interest, ft_interest = await asyncio.gather(
        entries_task, portal_task, interest_lp_task, interest_ft_task
    )
    portal_balance = portal_doc.get("balance") if portal_doc else None
    portal_synced_at = portal_doc.get("updated_at") if portal_doc else None

    def _norm_fund(f: Optional[str]) -> str:
        f = (f or "").lower()
        return "sinking" if (f.startswith("sink") or f == "capital_works") else "admin"

    # {year: {"admin": cents-agnostic float, "sinking": ...}} from both transaction sources.
    interest_by_year: dict = {}
    for row in (lp_interest or []) + (ft_interest or []):
        yr = str((row.get("_id") or {}).get("year") or "")
        if not yr:
            continue
        fund = _norm_fund((row.get("_id") or {}).get("fund"))
        bucket = interest_by_year.setdefault(yr, {"admin": 0.0, "sinking": 0.0})
        bucket[fund] = round(bucket[fund] + float(row.get("total") or 0.0), 2)

    enriched = []
    for e in entries:
        d = dict(e)
        d["portal_net_balance"] = portal_balance
        d["portal_synced_at"] = portal_synced_at
        derived = interest_by_year.get(str(e.get("year") or ""), {})
        stored_admin = float(e.get("admin_interest") or 0.0)
        stored_sinking = float(e.get("sinking_interest") or 0.0)
        # Stored (portal-sourced) interest wins; otherwise use the transaction-derived figure.
        d["admin_interest"] = stored_admin if abs(stored_admin) > 0.01 else round(derived.get("admin", 0.0), 2)
        d["sinking_interest"] = stored_sinking if abs(stored_sinking) > 0.01 else round(derived.get("sinking", 0.0), 2)
        if abs(stored_admin) > 0.01 or abs(stored_sinking) > 0.01:
            d["interest_source"] = "stored"
        elif abs(d["admin_interest"]) > 0.01 or abs(d["sinking_interest"]) > 0.01:
            d["interest_source"] = "transactions"
        else:
            d["interest_source"] = "none"
        enriched.append(d)

    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Levy Calculator
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/levy-calculator")
async def calculate_levies(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Calculate levy amounts for all units from annual_levies and unit UOE.
    Returns per-unit breakdown with admin/sinking split.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Normalise year format: "2026-27" or "2025-26" → "2026" / "2025"
    if year and len(year) > 4:
        year = year[:4]

    # Performance Optimization⚡: Parallelize independent database queries to reduce endpoint latency.
    units_task = db.units.find({"building_id": building_id},
                               {"_id": 0, "unit_number": 1, "unit_type": 1, "entitlement": 1,
                                "scheme_class": 1}).to_list(200)
    settings_task = _get_general_settings(building_id, {"_id": 0})
    split_task = db.scheme_classes.find({"building_id": building_id}, {"_id": 0}).to_list(10)
    building_task = db.buildings.find_one({"id": building_id}, {"_id": 0, "trust_config": 1})

    if year:
        levy_task = db.annual_levies.find_one({"building_id": building_id, "year": year}, {"_id": 0})
        levy, units, settings_res, split_classes, building_doc = await asyncio.gather(
            levy_task, units_task, settings_task, split_task, building_task
        )
    else:
        # No year specified — must resolve via _resolve_default_levy_year (never a
        # not-yet-started year), not a raw sort=[("year", -1)] query — see module note
        # above get_available_years.
        units, settings_res, split_classes, building_doc = await asyncio.gather(
            units_task, settings_task, split_task, building_task
        )
        resolved_year = await _resolve_default_levy_year(building_id, settings_doc=settings_res)
        levy = (
            await db.annual_levies.find_one({"building_id": building_id, "year": resolved_year}, {"_id": 0})
            if resolved_year else None
        )
    settings_doc = settings_res or {}

    if not levy:
        return {"error": "No levy data found", "levies": []}

    year = levy["year"]
    total_uoe = levy.get("total_uoe", TOTAL_UOE)
    gst_config = parse_levy_gst_settings(settings_doc)
    gst_rate = gst_config["effective_gst_rate"]
    gst_multiplier = gst_config["gst_multiplier"]

    trust_cfg = (building_doc or {}).get("trust_config", {}) if building_doc else {}
    rate_breakdown = get_levy_rate_breakdown(
        levy,
        settings_doc=settings_doc,
        trust_config=trust_cfg,
    )
    admin_per_uoe_annual = rate_breakdown["admin_ex_gst_annual"]
    sinking_per_uoe_annual = rate_breakdown["sinking_ex_gst_annual"]
    admin_per_uoe_payable = rate_breakdown["admin_payable_annual"]
    sinking_per_uoe_payable = rate_breakdown["sinking_payable_annual"]
    admin_budget_cents = round(admin_per_uoe_payable * rate_breakdown["total_uoe"] * 100)
    sinking_budget_cents = round(sinking_per_uoe_payable * rate_breakdown["total_uoe"] * 100)

    # Penny-adjustment allocation:
    # 1. Floor-divide the budget in cents proportional to UOE.
    # 2. Collect fractional remainders per lot.
    # 3. Award 1 extra cent to the lots with the largest fractional parts until
    #    the sum exactly equals the budget — guarantees sum(lot_levies) == budget.
    valid_units = [u for u in units if u.get("entitlement")]

    def _allocate_cents(budget_cents: int, unit_list: list) -> dict:
        """Return {unit_number: cents} where sum == budget_cents exactly.

        Allocation is proportional to each unit's UOE share of the total
        entitlement for the supplied unit_list.  Using the actual sum of
        entitlements (rather than the levy record's total_uoe) guarantees that
        the penny-adjustment loop always has enough slots to distribute every
        remainder cent, regardless of whether some lots were excluded due to
        missing entitlement data.
        """
        if not unit_list:
            return {}
        actual_total_uoe = sum(u["entitlement"] for u in unit_list)
        if actual_total_uoe == 0:
            return {}
        alloc: dict = {}
        remainders: list = []
        assigned = 0
        for u in unit_list:
            uoe = u["entitlement"]
            exact = budget_cents * uoe / actual_total_uoe
            floor_val = math.floor(exact)
            alloc[u["unit_number"]] = floor_val
            assigned += floor_val
            remainders.append((exact - floor_val, u["unit_number"]))
        # Distribute remaining cents to highest-fractional-part lots.
        remainder = budget_cents - assigned
        remainders.sort(key=lambda x: -x[0])
        for i in range(min(remainder, len(remainders))):
            alloc[remainders[i][1]] += 1
        return alloc

    def _allocate_rate_cents(rate_per_uoe_annual: float, budget_cents: int, unit_list: list) -> dict:
        """Return {unit_number: cents} derived from a per-UOE rate with exact total.

        Each lot starts from its exact amount floor(rate * entitlement * 100), and
        the remaining cents are assigned to the lots with the largest fractional
        remainders until the allocation total matches the target budget exactly.
        This guarantees sum(lot_levies) == budget_cents.
        """
        if not unit_list:
            return {}
        alloc: dict = {}
        remainders: list = []
        assigned = 0
        for u in unit_list:
            exact = rate_per_uoe_annual * u["entitlement"] * 100
            floor_val = math.floor(exact)
            alloc[u["unit_number"]] = floor_val
            assigned += floor_val
            remainders.append((exact - floor_val, u["unit_number"]))

        remainder = budget_cents - assigned
        if remainder > 0:
            remainders.sort(key=lambda x: (-x[0], x[1]))
            for i in range(min(remainder, len(remainders))):
                alloc[remainders[i][1]] += 1
        elif remainder < 0:
            remainders.sort(key=lambda x: (x[0], x[1]))
            decremented = 0
            for i in range(len(remainders)):
                if decremented >= -remainder:
                    break
                unit_number = remainders[i][1]
                if alloc[unit_number] > 0:
                    alloc[unit_number] -= 1
                    decremented += 1
        return alloc

    # Allocate using GST-inclusive rates so each unit's levy = what they pay on their notice.
    # Fall back to total-budget allocation when no per-UOE rate is available.
    if admin_per_uoe_payable:
        admin_alloc = _allocate_rate_cents(admin_per_uoe_payable, admin_budget_cents, valid_units)
    else:
        admin_alloc = _allocate_cents(admin_budget_cents, valid_units)

    if sinking_per_uoe_payable:
        sinking_alloc = _allocate_rate_cents(sinking_per_uoe_payable, sinking_budget_cents, valid_units)
    else:
        sinking_alloc = _allocate_cents(sinking_budget_cents, valid_units)

    levies = []
    for unit in valid_units:
        uoe = unit["entitlement"]
        u_num = unit.get("unit_number")

        admin_annual_cents = admin_alloc[u_num]
        sinking_annual_cents = sinking_alloc[u_num]
        total_annual_cents = admin_annual_cents + sinking_annual_cents

        # Express as dollars for backward-compatible API response
        admin_annual = admin_annual_cents / 100
        sinking_annual = sinking_annual_cents / 100
        total_annual = total_annual_cents / 100

        # All amounts are GST-inclusive (what appears on the owner's levy notice).
        # GST breakdown for the levy notice four-line format:
        #   Administrative Fund Levy:  admin_ex_gst
        #   Sinking Fund Levy:         sinking_ex_gst
        #   GST:                       gst_annual
        #   TOTAL DUE:                 total_annual
        admin_ex_gst = round(admin_annual / gst_multiplier, 2)
        sinking_ex_gst = round(sinking_annual / gst_multiplier, 2)
        gst_annual = round(total_annual - admin_ex_gst - sinking_ex_gst, 2)

        levies.append({
            "unit_number": unit.get("unit_number"),
            "unit_type": unit.get("unit_type"),
            "scheme_class": unit.get("scheme_class"),  # None when no split assigned
            "uoe": uoe,
            "entitlement_percentage": round((uoe / total_uoe) * 100, 4),
            # Dollar amounts — GST-inclusive (what owner pays on levy notice)
            "admin_annual": admin_annual,
            "admin_quarterly": round(admin_annual_cents / 4) / 100,
            "sinking_annual": sinking_annual,
            "sinking_quarterly": round(sinking_annual_cents / 4) / 100,
            "total_annual": total_annual,
            "total_quarterly": round(total_annual_cents / 4) / 100,
            # Ex-GST amounts and GST component — for levy notice breakdown display
            "admin_annual_ex_gst": admin_ex_gst,
            "sinking_annual_ex_gst": sinking_ex_gst,
            "gst_annual": gst_annual,
            "gst_quarterly": round(gst_annual / 4, 2),
            "gst_registered": gst_config["gst_registered"],
            "gst_rate": gst_rate,
            # Integer-cent amounts for downstream calculations (no float drift).
            # *_quarterly_cents is a rounded indicative instalment (annual / 4).
            # Billing systems generating 4 actual invoices should compute Q1-Q3
            # as annual_cents // 4 and Q4 as annual_cents - 3 * (annual_cents // 4)
            # so that the four instalments always sum exactly to the annual amount.
            "admin_annual_cents": admin_annual_cents,
            "admin_quarterly_cents": round(admin_annual_cents / 4),
            "sinking_annual_cents": sinking_annual_cents,
            "sinking_quarterly_cents": round(sinking_annual_cents / 4),
            "total_annual_cents": total_annual_cents,
            "total_quarterly_cents": round(total_annual_cents / 4),
        })

    levies.sort(key=lambda x: x["unit_number"])

    # Build per-class summary when a split is configured
    split_summary = None
    if split_classes:
        from datetime import datetime
        from services.scheme_levy_service import is_split_active as _is_split_active

        def _parse_eff(raw) -> Optional[datetime]:
            """Generated function header.

            Function: _parse_eff
            Path: backend/routers/finance.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            if raw is None:
                return None
            if isinstance(raw, datetime):
                return raw
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None

        active_classes = [
            sc for sc in split_classes
            if _is_split_active(_parse_eff(sc.get("effective_from")))
        ]

        if active_classes:
            per_class: dict = {}
            for levy_row in levies:
                cls = levy_row.get("scheme_class") or "unassigned"
                if cls not in per_class:
                    per_class[cls] = {"unit_count": 0, "total_uoe": 0.0, "admin_annual": 0.0, "sinking_annual": 0.0,
                                      "total_annual": 0.0}
                per_class[cls]["unit_count"] += 1
                per_class[cls]["total_uoe"] = round(per_class[cls]["total_uoe"] + levy_row["uoe"], 4)
                per_class[cls]["admin_annual"] = round(per_class[cls]["admin_annual"] + levy_row["admin_annual"], 2)
                per_class[cls]["sinking_annual"] = round(per_class[cls]["sinking_annual"] + levy_row["sinking_annual"],
                                                         2)
                per_class[cls]["total_annual"] = round(per_class[cls]["total_annual"] + levy_row["total_annual"], 2)
            split_summary = {"split_active": True, "classes": per_class}
        else:
            split_summary = {"split_active": False}

    # Compute payment schedule from site settings (already fetched in parallel above).
    # Falls back to the annual_levies document schedule if settings are unavailable.
    levy_due_months = settings_doc.get("levy_due_months") or [3, 6, 9, 12]
    levy_due_day_type = settings_doc.get("levy_due_day_type") or "first"
    levy_due_day = settings_doc.get("levy_due_day")
    levy_due_custom_dates = settings_doc.get("levy_due_custom_dates")
    levy_collection_frequency = settings_doc.get("levy_collection_frequency", "quarterly")
    fy_start_month_setting = int(settings_doc.get("financial_year_start_month", 1))
    freq_to_periods = {"quarterly": 4, "half_yearly": 2, "monthly": 12, "yearly": 1}
    num_periods = freq_to_periods.get(levy_collection_frequency, 4)
    # levy_year is always the calendar year the FY STARTS in.
    # "2026-2027" → 2026; "2026" → 2026
    cal_year_str = str(year).split("-")[0]
    try:
        cal_year = int(cal_year_str)
    except ValueError:
        cal_year = 2026
    _fy_label_calc = get_fy_label(cal_year, fy_start_month_setting)
    quarter_labels = [f"Q{i + 1}" for i in range(num_periods)]
    try:
        computed_dates = _compute_period_due_dates(
            cal_year, levy_due_months, levy_due_day_type, levy_due_day,
            num_periods, levy_due_custom_dates,
            fy_start_month=fy_start_month_setting,
        )
        payment_schedule = [
            {"quarter": quarter_labels[i], "due_date": d}
            for i, d in enumerate(computed_dates)
        ]
    except Exception:
        payment_schedule = levy.get("payment_schedule", [])

    return {
        "year": year,
        "status": levy.get("status"),
        "admin_fund_total": admin_budget_cents / 100,
        "sinking_fund_total": sinking_budget_cents / 100,
        "total_budget": (admin_budget_cents + sinking_budget_cents) / 100,
        # Integer-cent totals for downstream use (no float drift)
        "admin_fund_total_cents": admin_budget_cents,
        "sinking_fund_total_cents": sinking_budget_cents,
        "total_budget_cents": admin_budget_cents + sinking_budget_cents,
        "total_uoe": total_uoe,
        "admin_per_uoe_annual": admin_per_uoe_annual,
        "sinking_per_uoe_annual": sinking_per_uoe_annual,
        "admin_per_uoe_quarterly": rate_breakdown["admin_ex_gst_quarterly"],
        "sinking_per_uoe_quarterly": rate_breakdown["sinking_ex_gst_quarterly"],
        "admin_per_uoe_payable_annual": admin_per_uoe_payable,
        "admin_per_uoe_payable_quarterly": rate_breakdown["admin_payable_quarterly"],
        "sinking_per_uoe_payable_annual": sinking_per_uoe_payable,
        "sinking_per_uoe_payable_quarterly": rate_breakdown["sinking_payable_quarterly"],
        "total_per_uoe_payable_annual": rate_breakdown["total_payable_annual"],
        "total_per_uoe_payable_quarterly": rate_breakdown["total_payable_quarterly"],
        "fy_label": _fy_label_calc,
        "payment_schedule": payment_schedule,
        "split_summary": split_summary,
        # None when no split configured; dict with split_active + classes when configured
        "payment_methods": [
            {"name": "Online Payment (Stripe)", "id": "stripe", "enabled": True, "surcharge": "1.75% + 30c",
             "is_online": True},
            {"name": "DEFT", "id": "deft", "enabled": True, "url": "https://deft.com.au",
             "deft_ref": settings_doc.get("deft_ref", "")},
            {"name": "BPAY", "id": "bpay", "enabled": True,
             "biller_code": settings_doc.get("bpay_biller_code", ""),
             "bpay_ref": settings_doc.get("bpay_ref", "")},
            {"name": "Credit Card", "id": "credit_card", "enabled": True, "surcharge": "2.0%"},
            {"name": "Direct Bank Transfer", "id": "bank_transfer", "enabled": True,
             "bank_name": settings_doc.get("bank_name", ""),
             "bsb": settings_doc.get("bank_bsb", ""),
             "account_number": settings_doc.get("bank_account_number", ""),
             "account_name": settings_doc.get("bank_account_name", settings_doc.get("building_name", ""))},
            {"name": "Australia Post / Post Billpay", "id": "australia_post", "enabled": True,
             "billpay_code": settings_doc.get("aus_post_code", ""),
             "aus_post_ref": settings_doc.get("aus_post_ref", "")},
        ],
        "gst_registered": gst_config["gst_registered"],
        "gst_rate": gst_rate,
        "gst_label": gst_config["gst_label"],
        "levies": levies,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Levy Payments
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/levy-payments", response_model=LevyPaymentResponse)
async def record_levy_payment(
        data: LevyPaymentCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Record a levy payment with route-level write-source resolution."""
    state = await get_finance_write_route_runtime_state(
        building_id=building_id,
        route_key="finance.levy_payment_create",
        idempotency_key=idempotency_key,
    )

    if state["source"] == "mongo":
        if state["domain_mode"] == "postgres_write":
            raise HTTPException(
                status_code=409,
                detail="PostgreSQL write cutover blocked for POST /levy-payments: " + str(state.get("blocked_reason")),
            )
        return LevyPaymentResponse(**await _record_levy_payment_mongodb(data, current_user, building_id))

    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="PostgreSQL receipt posting requires finance manager approval")

    result = await finance_postgres_write_service.post_manual_levy_receipt(
        building_id=building_id,
        unit_number=data.unit_number,
        amount=data.amount,
        payment_method=data.payment_method,
        payment_reference=data.payment_reference,
        quarter=data.quarter,
        year=data.year,
        fund_type=data.fund_type,
        payment_type=data.payment_type,
        notes=data.notes,
        actor=current_user,
        idempotency_key=idempotency_key or "",
    )
    return LevyPaymentResponse(**result.response)


# ──────────────────────────────────────────────────────────────────────────────
# Component 4: Strangler pattern helpers for record_levy_payment
# ──────────────────────────────────────────────────────────────────────────────

async def _record_levy_payment_mongodb(
        data: LevyPaymentCreate,
        current_user: dict,
        building_id: str
) -> dict:
    """MongoDB implementation of record_levy_payment."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        own_unit = current_user.get("unit_number")
        if effective_role(current_user) != "owner" or not own_unit or \
                not user_unit_matches(current_user, data.unit_number):
            raise HTTPException(status_code=403, detail="Not authorized")

    payment_id = str(uuid.uuid4())
    now = _now()
    payment_type = data.payment_type or "standard"

    # Performance Optimization⚡: Parallelize independent database operations to reduce latency.
    tasks = [db.levy_payments.count_documents({})]
    if payment_type == "advance":
        from utils.finance_helpers import get_levy_rates
        tasks.append(db.units.find_one(
            {"building_id": building_id, "unit_number": data.unit_number},
            {"_id": 0, "entitlement": 1}
        ))
        tasks.append(get_levy_rates(data.year, building_id))

    results = await asyncio.gather(*tasks)
    count = results[0]

    receipt_number = f"EG-{datetime.now().year}-{str(count + 1).zfill(5)}"
    is_self_report = not permissions.can_manage_finances

    # For advance payments: compute the credit portion using pre-fetched data
    credit_amount: Optional[float] = None
    if payment_type == "advance":
        try:
            unit_doc = results[1]
            rates = results[2]

            if unit_doc and rates:
                uoe = unit_doc.get("entitlement", 0)
                admin_annual = round(rates.get("admin_annual", 0) * uoe, 2)
                sinking_annual = round(rates.get("sinking_annual", 0) * uoe, 2)
                total_annual = round(admin_annual + sinking_annual, 2)
                period_levy = round(total_annual / 4, 2)  # quarterly default
                excess = round(data.amount - period_levy, 2)
                if excess > 0:
                    credit_amount = excess
        except Exception as exc:
            logger.warning(f"Failed to compute credit_amount for advance payment: {exc}")
            pass  # non-fatal — payment still records fine

    payment_doc = {
        "id": payment_id,
        "building_id": building_id,
        **data.model_dump(),
        "payment_type": payment_type,
        "credit_amount": credit_amount,
        "status": "pending_verification" if is_self_report else "confirmed",
        "receipt_number": receipt_number,
        "paid_by": current_user["id"],
        "confirmed_by": None if is_self_report else current_user["id"],
        "confirmed_at": None if is_self_report else now,
        "created_at": now,
    }

    await db.levy_payments.insert_one(payment_doc)

    asyncio.create_task(create_audit_log(
        action="payment_recorded",
        resource_type="levy_payment",
        resource_id=payment_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "unit_number": data.unit_number,
            "amount": data.amount,
            "year": data.year,
            "payment_type": payment_type,
            "credit_amount": credit_amount,
        }
    ))

    asyncio.create_task(
        _notify_levy_payment(data.unit_number, data.amount, data.quarter, data.year, building_id)
    )

    # H-3: recompute true-cost-of-ownership summary for this unit so the
    # lot_financial_summary collection stays current after every payment.
    # Phase G: when levy data lives in Postgres, gate behind
    #   if not _postgres_enabled_for(building_id)
    from services.lot_finance_service import compute_true_cost as _compute_true_cost
    asyncio.create_task(_compute_true_cost(data.unit_number, str(data.year), building_id))

    return LevyPaymentResponse(**payment_doc).model_dump()


async def _record_levy_payment_postgres(
        data: LevyPaymentCreate,
        current_user: dict,
        building_id: str
) -> dict:
    """Postgres implementation of record_levy_payment (TODO: full implementation).
    
    For now, delegates to MongoDB until Postgres payment recording is complete.
    """
    try:
        # TODO: Phase G - Full implementation with proper data mapping
        # Current limitations:
        #   1. Need to lookup lot_id from unit_number
        #   2. Need to resolve payer_party_id from current_user
        #   3. Need to handle amount currency conversion (dollars -> cents)
        #   4. Need to handle payment_type metadata (advance, partial, etc.)
        # For now, use MongoDB path as safe fallback during Phase F development
        logger.info(
            f"record_levy_payment: TODO Postgres implementation, using MongoDB fallback for unit {data.unit_number}")
        return await _record_levy_payment_mongodb(data, current_user, building_id)
    except Exception as e:
        logger.warning(f"record_levy_payment Postgres fallback: {e}, using MongoDB")
        return await _record_levy_payment_mongodb(data, current_user, building_id)


@router.get("/levy-payments", response_model=List[LevyPaymentResponse])
async def get_levy_payments(
        unit_number: Optional[str] = None,
        year: Optional[str] = None,
        quarter: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get levy payments filtered by unit, year, and quarter. Scoped to building.
    Non-admin users can only see their own payments.
    """
    permissions = get_user_permissions(current_user)

    query: dict = {"building_id": building_id}
    if unit_number:
        query["unit_number"] = unit_number
    if year:
        query["year"] = year
    if quarter:
        query["quarter"] = quarter

    if not permissions.can_view_finances:
        query["unit_number"] = current_user.get("unit_number")

    payments = await db.levy_payments.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [LevyPaymentResponse(**p) for p in payments]


# ──────────────────────────────────────────────────────────────────────────────
# Component 4: Strangler pattern helpers for verify_levy_payment
# ──────────────────────────────────────────────────────────────────────────────

async def _verify_levy_payment_mongodb(
        payment_id: str,
        data: LevyPaymentVerify,
        current_user: dict,
        building_id: str
) -> dict:
    """MongoDB implementation of verify_levy_payment."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to verify payments")

    payment = await db.levy_payments.find_one({"id": payment_id, "building_id": building_id}, {"_id": 0})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment["status"] != "pending_verification":
        raise HTTPException(status_code=400, detail="Payment is not pending verification")

    now = _now()
    update: dict = {
        "status": "confirmed",
        "confirmed_by": current_user["id"],
        "confirmed_at": now,
    }
    if data.notes:
        existing_notes = payment.get("notes") or ""
        update[
            "notes"] = f"{existing_notes}\n[Manager note: {data.notes}]".strip() if existing_notes else f"[Manager note: {data.notes}]"

    await db.levy_payments.update_one({"id": payment_id, "building_id": building_id}, {"$set": update})
    updated = {**payment, **update}

    asyncio.create_task(create_audit_log(
        action="verified",
        resource_type="levy_payment",
        resource_id=payment_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"unit_number": payment["unit_number"], "amount": payment["amount"],
                 "receipt": payment["receipt_number"]}
    ))

    asyncio.create_task(_notify_payment_verified(
        unit_number=payment["unit_number"],
        amount=payment["amount"],
        quarter=payment["quarter"],
        year=payment["year"],
        receipt=payment["receipt_number"],
        building_id=building_id,
    ))

    asyncio.create_task(_upsert_ledger_for_payment(
        unit_number=payment["unit_number"],
        year=payment["year"],
        amount=payment["amount"],
        payment_id=payment_id,
        building_id=building_id,
        fund_type=payment.get("fund_type"),
    ))

    # H-3: recompute true-cost-of-ownership after payment verification.
    from services.lot_finance_service import compute_true_cost as _compute_true_cost
    asyncio.create_task(_compute_true_cost(payment["unit_number"], str(payment["year"]), building_id))

    return LevyPaymentResponse(**updated).model_dump()


async def _verify_levy_payment_postgres(
        payment_id: str,
        data: LevyPaymentVerify,
        current_user: dict,
        building_id: str
) -> dict:
    """PostgreSQL implementation of verify_levy_payment (Prompt 8).

    Looks up the receipt by payment_id in the PG finance schema, validates
    building scope, performs idempotent status confirmation, and writes audit.
    Does NOT fall back to MongoDB — callers in postgres_write mode must not
    silently degrade to Mongo.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to verify payments")

    return await finance_postgres_write_service.verify_receipt(
        building_id=building_id,
        payment_id=payment_id,
        notes=data.notes,
        actor=current_user,
    )


@router.patch("/levy-payments/{payment_id}/verify", response_model=LevyPaymentResponse)
async def verify_levy_payment(
        payment_id: str,
        data: LevyPaymentVerify,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Confirm a pending_verification levy payment. Scoped to building.
    Requires can_manage_finances permission (super_admin, chairman, strata_manager).
    
    Routes to Postgres when financial_core.read_from_postgres toggle is enabled,
    otherwise uses MongoDB (strangler pattern).
    """
    result_dict = await route_financial_write(
        operation_name="verify_levy_payment",
        building_id=building_id,
        postgres_handler=lambda: _verify_levy_payment_postgres(payment_id, data, current_user, building_id),
        mongodb_handler=lambda: _verify_levy_payment_mongodb(payment_id, data, current_user, building_id),
    )
    return LevyPaymentResponse(**result_dict)


# ──────────────────────────────────────────────────────────────────────────────
# Component 4: Strangler pattern helpers for reject_levy_payment
# ──────────────────────────────────────────────────────────────────────────────

async def _reject_levy_payment_mongodb(
        payment_id: str,
        data: LevyPaymentReject,
        current_user: dict,
        building_id: str
) -> dict:
    """MongoDB implementation of reject_levy_payment."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to reject payments")

    if not data.rejection_reason or not data.rejection_reason.strip():
        raise HTTPException(status_code=400, detail="rejection_reason is required")

    payment = await db.levy_payments.find_one({"id": payment_id, "building_id": building_id}, {"_id": 0})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment["status"] != "pending_verification":
        raise HTTPException(status_code=400, detail="Payment is not pending verification")

    now = _now()
    update: dict = {
        "status": "rejected",
        "rejected_by": current_user["id"],
        "rejected_at": now,
        "rejection_reason": data.rejection_reason.strip(),
    }
    if data.notes:
        existing_notes = payment.get("notes") or ""
        update[
            "notes"] = f"{existing_notes}\n[Manager note: {data.notes}]".strip() if existing_notes else f"[Manager note: {data.notes}]"

    await db.levy_payments.update_one({"id": payment_id, "building_id": building_id}, {"$set": update})
    updated = {**payment, **update}

    asyncio.create_task(create_audit_log(
        action="rejected",
        resource_type="levy_payment",
        resource_id=payment_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"unit_number": payment["unit_number"], "amount": payment["amount"], "reason": data.rejection_reason}
    ))

    asyncio.create_task(_notify_payment_rejected(
        unit_number=payment["unit_number"],
        amount=payment["amount"],
        quarter=payment["quarter"],
        year=payment["year"],
        receipt=payment["receipt_number"],
        reason=data.rejection_reason,
        building_id=building_id,
    ))

    # H-3: recompute true-cost-of-ownership after payment rejection.
    from services.lot_finance_service import compute_true_cost as _compute_true_cost
    asyncio.create_task(_compute_true_cost(payment["unit_number"], str(payment["year"]), building_id))

    return LevyPaymentResponse(**updated).model_dump()


async def _reject_levy_payment_postgres(
        payment_id: str,
        data: LevyPaymentReject,
        current_user: dict,
        building_id: str
) -> dict:
    """PostgreSQL implementation of reject_levy_payment (Prompt 8).

    For posted PG receipts creates a reversal journal entry (original is IMMUTABLE).
    For unposted drafts transitions status only.  Returns 409 if already rejected.
    Does NOT fall back to MongoDB — callers in postgres_write mode must not
    silently degrade to Mongo.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to reject payments")

    return await finance_postgres_write_service.reject_receipt(
        building_id=building_id,
        payment_id=payment_id,
        rejection_reason=data.rejection_reason,
        notes=data.notes,
        actor=current_user,
    )


@router.patch("/levy-payments/{payment_id}/reject", response_model=LevyPaymentResponse)
async def reject_levy_payment(
        payment_id: str,
        data: LevyPaymentReject,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Reject a pending_verification levy payment. Scoped to building.
    Requires can_manage_finances permission.
    
    Routes to Postgres when financial_core.read_from_postgres toggle is enabled,
    otherwise uses MongoDB (strangler pattern).
    """
    result_dict = await route_financial_write(
        operation_name="reject_levy_payment",
        building_id=building_id,
        postgres_handler=lambda: _reject_levy_payment_postgres(payment_id, data, current_user, building_id),
        mongodb_handler=lambda: _reject_levy_payment_mongodb(payment_id, data, current_user, building_id),
    )
    return LevyPaymentResponse(**result_dict)


# ──────────────────────────────────────────────────────────────────────────────
# Component 4: Strangler pattern helpers for delete_levy_payment
# ──────────────────────────────────────────────────────────────────────────────

async def _delete_levy_payment_mongodb(
        payment_id: str,
        current_user: dict,
        building_id: str
) -> dict:
    """MongoDB implementation of delete_levy_payment."""
    permissions = get_user_permissions(current_user)
    is_super_admin = effective_role(current_user) == "super_admin"
    is_owner = effective_role(current_user) == "owner"

    payment = await db.levy_payments.find_one({"id": payment_id, "building_id": building_id}, {"_id": 0})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    status = payment["status"]

    if is_owner:
        if payment.get("paid_by") != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to delete this payment")
        if status != "pending_verification":
            raise HTTPException(status_code=403, detail="Owners can only cancel pending_verification records")
    elif permissions.can_manage_finances:
        if status not in ("pending_verification", "confirmed", "rejected") and not is_super_admin:
            raise HTTPException(status_code=403, detail="Cannot delete this payment")
        if status in ("confirmed", "rejected") and not is_super_admin:
            raise HTTPException(status_code=403, detail="Only super_admin can delete confirmed/rejected records")
    else:
        raise HTTPException(status_code=403, detail="Not authorized to delete payments")

    await db.levy_payments.delete_one({"id": payment_id, "building_id": building_id})

    asyncio.create_task(create_audit_log(
        action="deleted",
        resource_type="levy_payment",
        resource_id=payment_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"unit_number": payment["unit_number"], "amount": payment["amount"], "prior_status": status}
    ))

    return {"message": "Payment record deleted", "id": payment_id}


async def _delete_levy_payment_postgres(
        payment_id: str,
        current_user: dict,
        building_id: str
) -> dict:
    """PostgreSQL implementation of delete_levy_payment (Prompt 8).

    Posted PG records → HTTP 409 POSTED_RECORD_IMMUTABLE (use reject/reversal instead).
    Unposted drafts → voided status-only transition (no physical delete).
    Does NOT fall back to MongoDB — callers in postgres_write mode must not
    silently degrade to Mongo.
    """
    permissions = get_user_permissions(current_user)
    _role = effective_role(current_user)
    if not permissions.can_manage_finances and _role != "owner":
        raise HTTPException(status_code=403, detail="Not authorized to delete payments")

    return await finance_postgres_write_service.delete_receipt(
        building_id=building_id,
        payment_id=payment_id,
        actor=current_user,
    )


@router.delete("/levy-payments/{payment_id}", response_model=DeleteWithIdAck)
async def delete_levy_payment(
        payment_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a levy payment record. Scoped to building.
    - Owner: can delete own pending_verification records only.
    - can_manage_finances: can delete any pending_verification record.
    - super_admin: can also delete confirmed or rejected records.
    
    Routes to Postgres when financial_core.read_from_postgres toggle is enabled,
    otherwise uses MongoDB (strangler pattern).
    """
    result_dict = await route_financial_write(
        operation_name="delete_levy_payment",
        building_id=building_id,
        postgres_handler=lambda: _delete_levy_payment_postgres(payment_id, current_user, building_id),
        mongodb_handler=lambda: _delete_levy_payment_mongodb(payment_id, current_user, building_id),
    )
    return result_dict


# ──────────────────────────────────────────────────────────────────────────────
# POST /finance/adjustment-journals
# PostgreSQL-only: post a manual double-entry adjustment journal.
# Requires postgres_write mode + can_manage_finances.
# Not available in mongo_primary mode (no MongoDB equivalent).
# ──────────────────────────────────────────────────────────────────────────────

class AdjustmentJournalLineRequest(BaseModel):
    gl_account_id: str
    direction: str  # "debit" | "credit"
    amount_cents: int
    gst_cents: int = 0
    narration: Optional[str] = None


class AdjustmentJournalRequest(BaseModel):
    narration: str
    reason: str
    lines: List[AdjustmentJournalLineRequest]


@router.post("/finance/adjustment-journals")
async def create_adjustment_journal(
        data: AdjustmentJournalRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """
    Post a manual double-entry adjustment journal entry in PostgreSQL.

    Rules:
    - Only available when financial_pg_writes_enabled is enabled for this building.
    - Requires can_manage_finances permission.
    - All amount_cents must be positive integers (no floats).
    - Journal must be balanced (total debits == total credits).
    - narration and reason are required.
    - Idempotency-Key header is required.

    Returns structured 409 if postgres_write mode is not active.
    """
    state = await get_finance_write_route_runtime_state(
        building_id=building_id,
        route_key="finance.adjustment_journal",
        idempotency_key=idempotency_key,
    )

    if state["source"] == "mongo":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ROUTE_NOT_AVAILABLE_IN_MONGO_PRIMARY",
                "message": (
                    "POST /finance/adjustment-journals is only available when "
                    "PostgreSQL write mode is active for this building. "
                    "This route has no MongoDB equivalent."
                ),
                "status": 409,
                "retryable": False,
                "blocked_reason": state.get("blocked_reason"),
                "suggested_action": "Enable financial_pg_writes_enabled for this building to use this route.",
            },
        )

    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Adjustment journals require finance manager approval")

    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for adjustment journals")

    result = await finance_postgres_write_service.create_adjustment_journal(
        building_id=building_id,
        narration=data.narration,
        reason=data.reason,
        lines=[line.model_dump() for line in data.lines],
        idempotency_key=idempotency_key,
        actor=current_user,
    )
    return result


@router.get("/levy-status/{unit_number}")
async def get_levy_status(
        unit_number: str,
        year: Optional[str] = None,
        current_user: dict = Depends(require_feature("finance")),
        building_id: str = Depends(get_current_building)
):
    """
    Get levy payment status and ledger for a unit.

    Per-period logic:
    - Each period shows standard_amount + prev_year_balance (Q1 only) unless overridden by roll-over.
    - Carry-forward to future periods only activates AFTER a period's due_date + grace_period_days
      has passed AND the period has an unpaid/partial balance.
    - Before the grace deadline: future periods always show the standard per-period amount.
    - After the grace deadline: any unpaid remainder rolls immediately into the next period.
    """
    unit_number = await resolve_canonical_unit_number(
        db, building_id, unit_number, rules=await _unit_display_rules_safe(building_id)
    )
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances and not user_unit_matches(current_user, unit_number):
        raise HTTPException(status_code=403, detail="Not authorized to view other units' levy status")

    # Performance Optimization⚡: Parallelize independent DB queries in two phases
    # Phase 1: Unit info, site settings, and annual levy (to determine year)
    unit_task = db.units.find_one({"building_id": building_id, "unit_number": unit_number},
                                  {"_id": 0, "entitlement": 1})
    settings_task = _get_general_settings(building_id, {"_id": 0})

    if year:
        # Common case: caller specified a year — fully parallel, no extra round-trip.
        levy_task = db.annual_levies.find_one({"building_id": building_id, "year": year}, {"_id": 0})
        unit, settings_doc, levy = await asyncio.gather(unit_task, settings_task, levy_task)
    else:
        # No year specified — LevyPaymentPage.tsx is a confirmed live caller of this path.
        # Must resolve via _resolve_default_levy_year (never a not-yet-started year), not a
        # raw sort=[("year", -1)] query — see module note above get_available_years.
        unit, settings_doc = await asyncio.gather(unit_task, settings_task)
        resolved_year = await _resolve_default_levy_year(building_id, settings_doc=settings_doc)
        levy = (
            await db.annual_levies.find_one({"building_id": building_id, "year": resolved_year}, {"_id": 0})
            if resolved_year else None
        )

    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    if not levy:
        return {"error": "No levy data found"}

    grace_period_days = int(settings_doc.get("grace_period_days", 14)) if settings_doc else 14
    levy_collection_frequency = (
        settings_doc.get("levy_collection_frequency", "quarterly") if settings_doc else "quarterly"
    )
    frequency_map = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}
    num_periods = frequency_map.get(levy_collection_frequency, 4)

    # Extract due-day settings (used to compute due dates dynamically)
    levy_due_months: List[int] = (settings_doc.get("levy_due_months") or []) if settings_doc else []
    levy_due_day_type: str = (settings_doc.get("levy_due_day_type") or "first") if settings_doc else "first"
    levy_due_day: Optional[int] = settings_doc.get("levy_due_day") if settings_doc else None
    fy_start_month_ls: int = int(settings_doc.get("financial_year_start_month", 1)) if settings_doc else 1

    year = levy["year"]
    uoe = unit.get("entitlement", 0)

    # Phase 2: Ledger entries, payments, and rates (dependent on determined year)
    # Performance Optimization⚡: Parallelize rate lookup with ledger and payment fetches.
    prev_year = str(int(year) - 1)

    rates_task = get_levy_rates(year, building_id)
    ledger_task = db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "year": year}, {"_id": 0}
    )
    prev_ledger_task = db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "year": prev_year}, {"_id": 0}
    )
    payments_task = db.levy_payments.find({
        "building_id": building_id,
        "unit_number": unit_number,
        "year": year,
        "status": {"$in": ["confirmed", "pending_verification"]}
    }, {"_id": 0}).to_list(20)

    # Lifetime (all-years) total paid for this unit — the sum of every ledger year's
    # total_paid. The year-scoped `ledger.total_paid` below is the CURRENT year only;
    # the Overview "Total Paid" tile is meant to show the cumulative amount the unit has
    # ever paid across its whole levy history, distinct from "Paid This Year". Aggregated
    # (not .to_list()-capped) so it stays correct for units with many years of history.
    lifetime_paid_task = db.unit_levy_ledger.aggregate([
        {"$match": {"building_id": building_id, "unit_number": unit_number}},
        {"$group": {"_id": None, "total": {"$sum": "$total_paid"}}},
    ]).to_list(1)

    # Portal cross-check — read-only, additive only. Fetched in parallel with levy data.
    portal_task = db.strata_owners.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0, "balance": 1, "status": 1, "updated_at": 1},
    )

    rates, ledger, prev_ledger, payments, lifetime_paid_docs, portal_owner = await asyncio.gather(
        rates_task, ledger_task, prev_ledger_task, payments_task, lifetime_paid_task, portal_task
    )

    admin_annual = round(rates.get("admin_annual", 0) * uoe, 2)
    sinking_annual = round(rates.get("sinking_annual", 0) * uoe, 2)
    total_annual = round(admin_annual + sinking_annual, 2)
    period_amounts = compute_period_installment_amounts(total_annual, num_periods)
    period_amount = period_amounts[0] if period_amounts else round(total_annual / num_periods, 2)

    # Use current year's opening_arrears as the carry-forward (post-reconciliation).
    # prev_ledger.net_balance is a DB snapshot that may not reflect payments settled in
    # Jan/Feb before the year starts (e.g. owners paying off FY2025 in the transition period).
    # opening_arrears = Civium_balance − Q1_levy; accurately represents the actual carry-forward.
    if ledger and ledger.get("opening_arrears", 0.0) > 0:
        prev_year_balance = round(ledger.get("opening_arrears", 0.0), 2)
    elif prev_ledger:
        prev_year_balance = round(prev_ledger.get("net_balance", 0.0), 2)
    else:
        prev_year_balance = 0.0

    # Ordered period labels from payment_schedule (fallback to generated list)
    # Guard: payment_schedule may be a string (e.g. "quarterly") in demo/seed data — treat as empty
    _raw_schedule = levy.get("payment_schedule", [])
    payment_schedule = _raw_schedule if isinstance(_raw_schedule, list) else []
    periods_list = [ps.get("quarter") for ps in payment_schedule if isinstance(ps, dict) and ps.get("quarter")]
    if not periods_list:
        prefix_map = {"quarterly": "Q", "monthly": "M", "half_yearly": "H", "yearly": "Y"}
        prefix = prefix_map.get(levy_collection_frequency, "Q")
        periods_list = [f"{prefix}{i}" for i in range(1, num_periods + 1)]

    # Pre-compute due dates from site settings when levy_due_months is configured.
    # This overrides the hardcoded payment_schedule dates so the levy-status tab
    # always reflects the admin-configured levy schedule.
    computed_due_dates: List[str] = []
    if levy_due_months:
        try:
            computed_due_dates = _compute_period_due_dates(
                int(year),
                levy_due_months,
                levy_due_day_type,
                levy_due_day,
                num_periods,
                settings_doc.get("levy_due_custom_dates"),
                fy_start_month=fy_start_month_ls,
            )
        except (ValueError, TypeError):
            computed_due_dates = []

    today = date.today()
    running_carry = prev_year_balance  # starts with previous year's net balance
    period_status = []
    # Sum of period levy amounts whose due date has actually passed — used below to
    # compute paid_this_year as "billed-and-due so far this year", not the full annual
    # levy (GAP-FIN-033 Part A2).
    levied_due_to_date = 0.0

    for i, period_label in enumerate(periods_list):
        standard_period_amount = (
            period_amounts[i]
            if i < len(period_amounts)
            else period_amount
        )
        # Aggregate all payments for this quarter (there may be multiple partial/advance records)
        quarter_payments = [p for p in payments if p.get("quarter") == period_label]
        confirmed_amount = round(sum(
            p.get("amount", 0) for p in quarter_payments if p.get("status") == "confirmed"
        ), 2)
        pending_amount_q = round(sum(
            p.get("amount", 0) for p in quarter_payments if p.get("status") == "pending_verification"
        ), 2)
        amount_paid = round(confirmed_amount + pending_amount_q, 2)

        # For receipt: prefer confirmed payment receipt, fall back to first pending
        receipt_number = None
        confirmed_rec = next((p for p in quarter_payments if p.get("status") == "confirmed"), None)
        if confirmed_rec:
            receipt_number = confirmed_rec.get("receipt_number")
        elif quarter_payments:
            receipt_number = quarter_payments[0].get("receipt_number")

        # Use settings-computed date if available, else fall back to payment_schedule
        if computed_due_dates and i < len(computed_due_dates):
            due_date_str = computed_due_dates[i]
        else:
            due_date_str = next(
                (ps.get("due_date") for ps in payment_schedule if
                 isinstance(ps, dict) and ps.get("quarter") == period_label),
                None
            )

        # Parse due date and compute the grace deadline
        due_date_obj = None
        grace_deadline = None
        if due_date_str:
            try:
                due_date_obj = date.fromisoformat(str(due_date_str)[:10])
                grace_deadline = due_date_obj + timedelta(days=grace_period_days)
            except (ValueError, TypeError):
                pass

        if due_date_obj is not None and due_date_obj <= today:
            levied_due_to_date += standard_period_amount

        # Gross amount = standard period + any accumulated carry-forward
        # Q1 always includes prev_year_balance; later periods only include it after rollover
        gross_due = standard_period_amount + running_carry

        past_deadline = grace_deadline is not None and today > grace_deadline

        # For 'upcoming' periods (before grace deadline), we display ONLY the standard
        # period amount. Arrears carry-forwards from prior unpaid periods only 'roll' into
        # the amount_due after they become overdue. A CREDIT carry-forward (running_carry < 0,
        # from an overpayment/advance in a prior period) is different: it's already-received
        # money, not a pending expectation, so it reduces amount_due immediately regardless of
        # this period's own deadline — mirrors gross_due, which already includes it.
        if running_carry < 0:
            amount_due = round(max(0.0, gross_due), 2)
        elif not past_deadline and i > 0:
            amount_due = round(max(0.0, standard_period_amount), 2)
        else:
            amount_due = round(max(0.0, gross_due), 2)

        remaining = round(gross_due - amount_paid, 2)

        if amount_paid >= gross_due:
            if confirmed_amount >= gross_due:
                # Fully confirmed by strata manager
                status = "paid"
            else:
                # Covered by self-reported payment awaiting strata verification
                status = "pending"
            # Surplus/advance payment: carry the excess FORWARD as a credit (negative
            # carry) into the next period, symmetric to the arrears carry below but not
            # gated on past_deadline — a payment is a real credit the instant it's
            # recorded. Previously this was unconditionally 0.0, so an advance payment
            # tagged to one quarter never reduced later quarters' amount_due, making the
            # sum of all quarters' amount_due exceed the true annual levy by the
            # unrolled surplus (GAP-FIN-033 Part B2).
            surplus = round(amount_paid - gross_due, 2)
            next_carry = -surplus if surplus > 0.01 else 0.0
        elif amount_paid > 0:
            # Partial payment exists (confirmed or pending) — balance still outstanding
            # Roll carry only after grace deadline; before deadline future periods see standard
            status = "partial"
            next_carry = remaining if past_deadline else 0.0
        elif past_deadline:
            # No payment at all and grace deadline passed
            status = "overdue"
            next_carry = remaining
        else:
            # No payment, before or within grace period
            status = "upcoming"
            next_carry = 0.0

        period_status.append({
            "quarter": period_label,
            "due_date": due_date_str,
            # `amount_due` is the CUMULATIVE gross owing at this period (standard + carried-forward
            # arrears). `standard_amount` is THIS period's own levy in isolation — the UI shows the
            # latter as "Levy this quarter" so a unit that has paid prior periods doesn't see a
            # confusing cumulative figure as "due". `actual_paid` is the real confirmed+pending
            # receipts tagged to THIS period, captured before the credit-rollforward reconciliation
            # passes below may overwrite `amount_paid` with credit carried in from another period.
            "amount_due": amount_due,
            "standard_amount": round(standard_period_amount, 2),
            "amount_paid": amount_paid,
            "actual_paid": amount_paid,
            "confirmed_amount": confirmed_amount,
            "pending_amount": pending_amount_q,
            "status": status,
            "past_deadline": past_deadline,
            "receipt_number": receipt_number,
            # carry_forward annotates Q1 so the UI can display the prev-year component
            "carry_forward": round(prev_year_balance, 2) if i == 0 else 0.0,
        })

        running_carry = next_carry

    # Authoritative total_paid: unit_levy_ledger.total_paid includes DEFT/bank-imported payments.
    # levy_payments only tracks portal/Stripe payments — using it alone causes units that paid
    # via DEFT to show $0 Total Paid even when fully settled.
    # Use ledger.total_paid when available; fall back to summing levy_payments (portal-only).
    portal_paid = round(sum(p.get("amount", 0) for p in payments), 2)
    total_paid = normalize_effective_total_paid(
        ledger_total_paid=(ledger or {}).get("total_paid", 0.0),
        live_payments_total=portal_paid,
        carry_forward_balance=prev_year_balance,
    )
    pending_total = round(sum(
        p.get("amount", 0) for p in payments if p.get("status") == "pending_verification"
    ), 2)
    # Total owing = annual levy + positive carry-forward arrears − paid.
    # Prior-year credits are already reflected in normalized total_paid.
    balance_due = compute_remaining_payment_obligation(
        total_annual=total_annual,
        total_paid=total_paid,
        prev_year_balance=prev_year_balance,
    )
    # Credit balance: how much the owner has paid ABOVE the current year's total obligation
    credit_balance = round(max(0.0, -balance_due), 2)
    # ledger_net_balance feeds apply_net_credit_override() below (interest/late-fee
    # override) independently of the paid_this_year fix that follows — kept computed
    # unconditionally, with its original balance_due fallback, since that consumer wants
    # "best available net-balance estimate" even with no ledger doc, not a due-to-date-
    # scoped figure.
    ledger_net_balance = float((ledger or {}).get("net_balance", balance_due) or 0.0)
    # paid_this_year = what's actually been billed-and-due so far this year, minus the
    # ledger's net balance (positive = still owing, reduces paid; negative = credit,
    # increases paid). Previously clamped against the FULL annual levy
    # (min(total_annual, total_annual - ledger_net_balance)): for a paid-up-or-ahead
    # unit (negative net_balance), that expression exceeds total_annual and the min()
    # silently capped it back down to exactly the full annual levy — making
    # "Paid this Year" always equal "Amount Levy" for any unit in credit, regardless of
    # how much of the year had actually come due. See GAP-FIN-033 Part A2 (and, for
    # why the prior "credit cap" existed at all, GAP-FIN-032's own "Reopened
    # unresolved items" note — that decision's *intent* is preserved here, only its
    # baseline is corrected from full-annual to due-to-date).
    #
    # paid_this_year is CONFIRMED-money only: pending_verification payments never post
    # to unit_levy_ledger.net_balance until a strata manager confirms them, so this
    # figure never reflects a still-unverified self-report — by design.
    # The authoritative branch requires an actual net_balance VALUE, not merely the
    # presence of a ledger document. Testing `ledger is not None` and then defaulting a
    # missing key to 0.0 conflates "this unit owes nothing" with "this row does not tell
    # us what it owes": paid_this_year would become the full levied-to-date figure, i.e.
    # a unit that has paid nothing is reported as paid up to date, for every period that
    # has come due. Verified 2026-08-20 that all 546 live unit_levy_ledger rows across
    # buildings 13195 / 16244 / 18932 carry net_balance, so this path is unreachable
    # today — it is guarded because the failure mode is a silent financial misstatement,
    # not because it is currently firing.
    #
    # A net_balance of exactly 0 (or 0.0) is a real, authoritative "paid up" and must
    # still take the authoritative branch — hence an explicit `is not None` test on the
    # value rather than a truthiness check.
    _ledger_net_raw = ledger.get("net_balance") if ledger is not None else None
    if _ledger_net_raw is not None:
        ledger_net_balance = float(_ledger_net_raw)
        paid_this_year = round(max(0.0, round(levied_due_to_date, 2) - ledger_net_balance), 2)
    else:
        # No unit_levy_ledger doc for this unit/year (not yet posted/rebuilt), or a row
        # that carries no net_balance. Previously the no-doc case fell back to
        # `ledger_net_balance = balance_due`, which is a FULL-ANNUAL-levy-basis figure
        # (balance_due nets against total_annual), not a due-to-date basis consistent with
        # levied_due_to_date — that mismatch collapsed paid_this_year to 0 (or worse) any
        # time no ledger doc existed, discarding every already-confirmed payment for the
        # whole year (GAP-FIN-067). Without an authoritative net balance, levy_payments IS
        # the only source of truth we have, so use its confirmed sum directly — uncapped,
        # matching the authoritative branch's own uncapped (credit-ahead-friendly)
        # semantics above.
        #
        # `ledger_net_balance` is deliberately NOT reassigned here: its other consumers
        # (apply_net_credit_override, balance_owing/balance_credit, the response field)
        # want the best-available estimate computed above, which is what they received
        # before this branch existed.
        paid_this_year = round(sum(
            p.get("amount", 0) for p in payments if p.get("status") == "confirmed"
        ), 2)
    opening_arrears = round(max(0.0, prev_year_balance), 2)

    # Final period classification must use the authoritative year-scoped ledger
    # payment amount, not only levy_payments rows tagged to one quarter. Those rows
    # are receipt detail; DEFT/imported receipts and overpayments often exist only
    # in unit_levy_ledger, so quarter-by-quarter statuses are recomputed as a
    # chronological waterfall here.
    #
    # Two independent money pools feed the waterfall — CONFIRMED (paid_this_year, ledger-
    # authoritative or levy_payments-confirmed) and PENDING (pending_total, self-reported
    # levy_payments awaiting strata verification). They are kept separate, confirmed
    # applied first, so a quarter fully covered ONLY by a pending_verification payment is
    # classified "pending" here too — not silently reclassified "overdue" (no confirmed
    # money to apply, so the pre-fix single-pool version saw it as unpaid) nor promoted
    # to "paid" (which would misrepresent an unverified self-report as bank fact).
    # GAP-FIN-067.
    _remaining_confirmed = paid_this_year
    _remaining_pending = pending_total
    _running_carry = max(0.0, prev_year_balance)
    for _idx, _p in enumerate(period_status):
        _standard = round(float(_p.get("standard_amount") or 0.0), 2)
        _gross = round(max(0.0, _standard + _running_carry), 2)

        _applied_confirmed = round(min(max(_remaining_confirmed, 0.0), _gross), 2)
        _remaining_confirmed = round(max(0.0, _remaining_confirmed - _applied_confirmed), 2)
        _remaining_after_confirmed = round(max(0.0, _gross - _applied_confirmed), 2)
        _applied_pending = round(min(max(_remaining_pending, 0.0), _remaining_after_confirmed), 2)
        _remaining_pending = round(max(0.0, _remaining_pending - _applied_pending), 2)

        _applied = round(_applied_confirmed + _applied_pending, 2)
        _remaining_due = round(max(0.0, _gross - _applied), 2)
        _past_deadline = bool(_p.get("past_deadline"))

        if _remaining_due <= 0.01 and _applied_pending <= 0.01:
            _status = "paid"
            _next_carry = 0.0
        elif _remaining_due <= 0.01:
            _status = "pending"
            _next_carry = 0.0
        elif _applied > 0.01:
            _status = "partial"
            _next_carry = _remaining_due if _past_deadline else 0.0
        elif _past_deadline:
            _status = "overdue"
            _next_carry = _remaining_due
        else:
            _status = "upcoming"
            _next_carry = 0.0

        # Future periods with current-year credit should show the credit applied
        # to that unit's own next levy, but this must not affect collection-rate
        # metrics or any other unit's arrears.
        if not _past_deadline and _remaining_due > 0.01 and (_remaining_confirmed > 0.01 or _remaining_pending > 0.01):
            _future_applied_confirmed = round(min(_remaining_confirmed, _remaining_due), 2)
            _remaining_confirmed = round(max(0.0, _remaining_confirmed - _future_applied_confirmed), 2)
            _remaining_due = round(max(0.0, _remaining_due - _future_applied_confirmed), 2)
            _future_applied_pending = round(min(_remaining_pending, _remaining_due), 2)
            _remaining_pending = round(max(0.0, _remaining_pending - _future_applied_pending), 2)
            _remaining_due = round(max(0.0, _remaining_due - _future_applied_pending), 2)
            _applied_pending = round(_applied_pending + _future_applied_pending, 2)
            _applied = round(_applied + _future_applied_confirmed + _future_applied_pending, 2)
            if _remaining_due <= 0.01 and _applied_pending <= 0.01:
                _status = "paid"
            elif _remaining_due <= 0.01:
                _status = "pending"
            elif _applied > 0.01:
                _status = "partial"

        _p["amount_due"] = _remaining_due
        _p["amount_paid"] = _applied
        _p["status"] = _status
        _p["outstanding"] = _remaining_due
        _p["rolled_forward"] = round(_running_carry, 2) if _idx > 0 and _running_carry > 0.01 else 0.0
        _running_carry = _next_carry

    # Fetch credit reward history (volunteer credits, rebates) - Step 4
    credit_history_cursor = db._db.journal_entries.find({
        "building_id": building_id,
        "lot_id": unit_number,  # unit_number used as lot_id in this context
        "entry_type": "volunteer_credit"
    }, {"_id": 0}).sort("created_at", -1)

    # Performance Optimization⚡: Using to_list() for faster data retrieval than async iteration.
    credit_history_docs = await credit_history_cursor.to_list(1000)

    credit_history = []
    volunteer_credits_ytd = 0
    for entry in credit_history_docs:
        credit_history.append({
            "date": entry["created_at"],
            "description": entry["description"],
            "amount_cents": entry["amount_cents"]
        })
        # Check if within current financial year (simplified)
        if entry["created_at"].startswith(year):
            volunteer_credits_ytd += entry["amount_cents"]

    # Payment history for the ledger
    payment_history = []
    for p in payments:
        # Disclosure surfacing (2026-07-17): a payment mirrored from a
        # historical reconstruction (docs/migration/
        # historical_ledger_reconciliation_plan01.md §9) must never be shown
        # indistinguishably from an owner's actual observed bank payment —
        # the exact date/channel is modelled, not observed. is_reconstructed
        # is additive (existing consumers of payment_history ignore unknown
        # keys); nothing here can be True until Phase 6 (still deferred) ever
        # promotes a reconstructed transaction through _post_payment_to_ledger.
        is_reconstructed = p.get("transaction_origin") == "reconstructed_historical"
        entry = {
            "date": p.get("created_at") or p.get("payment_date", ""),
            "description": f"Levy Payment - {p.get('quarter', '')} {p.get('year', '')}",
            "amount": p.get("amount", 0),
            "status": p.get("status", "unknown"),
            "is_reconstructed": is_reconstructed,
        }
        if is_reconstructed:
            entry["disclosure"] = (
                "Historical transaction reconstructed during onboarding — the amount is "
                "sourced from approved records, but the exact date and payment channel are "
                "modelled, not observed from a bank statement."
            )
        payment_history.append(entry)

    # Computed arrears interest + late-fee penalty (used when no actual per-unit interest
    # transactions exist — per the "use the formula" direction). Percentage interest uses the
    # building's effective rate (nil for buildings like East Gate that adopted a nil rate); the
    # flat late fee ($amount per period_days a levy stays overdue) uses the building's late-fee
    # policy. Both are computed per overdue period from period_status; nothing is persisted.
    from services.interest_penalty_service import apply_net_credit_override, zero_charges

    computed_charges = zero_charges()
    _credit_interest_rate_pct = 0.0
    _year_int = int(str(year).split("-")[0])
    _as_of = min(date.today(), date(_year_int, 12, 31))
    try:
        from services.arrears_interest_service import (
            get_effective_credit_interest_rate,
            get_effective_interest_rate,
        )
        from services.interest_penalty_service import (
            compute_unit_interest_and_penalty,
            get_late_fee_policy,
        )
        _rate_info, _late_fee_policy, _credit_interest_rate_pct = await asyncio.gather(
            get_effective_interest_rate(building_id, db),
            get_late_fee_policy(db, building_id),
            get_effective_credit_interest_rate(building_id, db),
        )
        computed_charges = compute_unit_interest_and_penalty(
            period_status=period_status,
            grace_period_days=grace_period_days,
            as_of=_as_of,
            annual_rate_pct=float(_rate_info.get("rate_pct", 0.0)),
            max_rate_pct=float(_rate_info.get("max_rate_pct", 20.0)),
            late_fee_policy=_late_fee_policy,
            levy_year=_year_int,
        )
    except Exception as _ip_exc:
        logger.warning(f"computed interest/penalty unavailable for {building_id}/{year}: {_ip_exc}")

    # A unit in net credit can never be shown owing arrears interest (MANDATORY arrears
    # rule, GAP-FIN-047 §1) — see apply_net_credit_override()'s docstring in
    # interest_penalty_service.py for the full root-cause (per-period residual vs. the
    # authoritative year-scoped net_balance). GAP-FIN-047 §2 (approved 2026-08-19): the same
    # call optionally attaches an owner-earned credit_interest_estimate, off by default for
    # every building, using a simple Jan-1-of-levy-year-to-as_of accrual window.
    computed_charges = apply_net_credit_override(
        computed_charges, ledger_net_balance,
        credit_interest_rate_pct=_credit_interest_rate_pct,
        credit_days=(_as_of - date(_year_int, 1, 1)).days,
    )

    # Owner-transfer paid split (FY-scoped finance.receipts, Postgres-sourced) -- lets the
    # unit page show, after a mid-year ownership transfer, how much of THIS year's levy
    # receipts were paid by the current owner vs a previous owner. Non-fatal: None when PG
    # is unavailable/unpromoted or the lot has no ownership_periods row (missing != zero),
    # rendered as null and simply not shown. This is a SEPARATE, FY-scoped figure -- NOT a
    # breakdown of the cumulative total_paid_all_years tile (which is lifetime, all owners).
    _owner_split = await _get_owner_paid_split_standalone(building_id, unit_number, year)

    # GAP-FIN-054: LIFETIME (all-years) per-owner split for the cumulative "Total Paid".
    # `total_paid_all_years` (the Mongo unit_levy_ledger sum shown in the "Total Paid" tile)
    # is the authoritative displayed lifetime magnitude; PG provides only the attribution
    # RATIO (who paid what fraction across all years, partitioned by ownership tenure). We
    # scale the tile total by that ratio so `paid_by_current_owner_lifetime +
    # paid_by_previous_owners_lifetime == total_paid_all_years` to the cent — one lifetime
    # total, never a second conflicting PG figure. null (card hidden) when PG is
    # unavailable/unpromoted, the lot has no ownership_periods row, or PG has no non-reversed
    # receipts to form a ratio (missing != zero). Distinct from the FY `_owner_split` above
    # (this year's receipts by owner) — this is cumulative across every levy year.
    _tile_total_paid = round(
        float((lifetime_paid_docs[0] or {}).get("total", 0.0)) if lifetime_paid_docs else 0.0,
        2,
    )
    _owner_lifetime = await _get_owner_lifetime_split_standalone(building_id, unit_number)
    paid_by_current_owner_lifetime = None
    paid_by_previous_owners_lifetime = None
    lifetime_split_since = None
    if _owner_lifetime:
        paid_by_current_owner_lifetime, paid_by_previous_owners_lifetime = _scale_lifetime_owner_split(
            _owner_lifetime.get("current_owner_cents", 0),
            _owner_lifetime.get("previous_owners_cents", 0),
            _tile_total_paid,
        )
        if paid_by_current_owner_lifetime is not None:
            lifetime_split_since = _owner_lifetime.get("current_owner_since")

    # GAP-FIN-047 item (a): the legacy `balance_due`/`credit_balance` fields below are a
    # FULL-ANNUAL remaining figure (compute_remaining_payment_obligation = total_annual +
    # prior_arrears - total_paid). Mid-year that overstates what a unit owes and shows a
    # credit-ahead unit as "owing" (the reported TH087 bug: $3,290.04 "Outstanding" for a
    # unit that is actually $254.98 in credit). Surface, additively, the authoritative
    # net_balance-based owing/credit — identical to the formula the canonical
    # /finance/unit-dashboard-overview already uses (balance_owing = max(net_balance, 0);
    # balance_credit = max(-net_balance, 0)) and to the MANDATORY arrears rule (net_balance
    # is the trusted per-unit figure; a credit is never zeroed, never netted across units).
    # `ledger_net_balance` is year-scoped unit_levy_ledger.net_balance (matches the portal's
    # per-unit CR/DR exactly for TH087). balance_due / net_balance / credit_balance are left
    # unchanged for back-compat — test_owner_dashboard_balance.py locks their behaviour; the
    # Unit Finance Detail tile now reads balance_owing/balance_credit instead.
    balance_owing = round(max(0.0, ledger_net_balance), 2)
    balance_credit = round(max(0.0, -ledger_net_balance), 2)

    return {
        "unit_number": unit_number,
        "year": year,
        # Computed (estimated) arrears interest + late-fee penalty for this unit/year. Only
        # meaningful when no actual interest transactions exist; the UI must label it "estimated".
        "computed_interest": computed_charges.get("interest", 0.0),
        "computed_penalty": computed_charges.get("penalty", 0.0),
        "computed_charges_total": computed_charges.get("total", 0.0),
        "computed_late_fee_applied": computed_charges.get("late_fee_applied", False),
        "uoe": uoe,
        "entitlement_units": uoe,
        "admin_annual": admin_annual,
        "sinking_annual": sinking_annual,
        "total_annual": total_annual,
        "annual_levy": total_annual,
        "period_levy": period_amount,
        "quarterly_levy": period_amount,  # alias for backward compatibility
        "levy_frequency": levy_collection_frequency,
        "grace_period_days": grace_period_days,
        "total_paid": total_paid,
        # Lifetime cumulative paid across ALL levy years for this unit (Overview "Total Paid"
        # tile). Distinct from year-scoped `total_paid` (current year) and `paid_this_year`.
        # `_tile_total_paid` is the same value (computed once above for the GAP-FIN-054
        # lifetime split, which scales to it).
        "total_paid_all_years": _tile_total_paid,
        # Owner-transfer paid split (FY-scoped, Postgres-sourced -- see
        # _get_owner_paid_split_standalone). null (not 0.0) when the split is unknown:
        # PG unavailable/unpromoted, or the lot has no ownership_periods row. current +
        # previous = this FY's receipts total, NOT total_paid_all_years -- the UI labels
        # it as its own FY-scoped by-owner figure.
        "paid_by_current_owner": _aud_from_cents(_owner_split["current_owner_cents"]) if _owner_split else None,
        "paid_by_previous_owners": _aud_from_cents(_owner_split["previous_owners_cents"]) if _owner_split else None,
        "current_owner_since": (
            _owner_split["current_owner_since"].isoformat()
            if _owner_split and _owner_split.get("current_owner_since") else None
        ),
        # GAP-FIN-054: LIFETIME per-owner split of the cumulative total_paid_all_years above
        # (current + previous == total_paid_all_years to the cent). null when unknown (PG
        # unavailable/unpromoted, no ownership_periods row, or no PG receipts to ratio).
        # Distinct from the FY-scoped paid_by_current_owner/paid_by_previous_owners above.
        "paid_by_current_owner_lifetime": paid_by_current_owner_lifetime,
        "paid_by_previous_owners_lifetime": paid_by_previous_owners_lifetime,
        "lifetime_split_since": lifetime_split_since.isoformat() if lifetime_split_since else None,
        "lifetime_split_basis": (
            "ownership_tenure_ratio_of_total_paid_all_years"
            if paid_by_current_owner_lifetime is not None else None
        ),
        "paid_this_year": paid_this_year,
        "opening_arrears": opening_arrears,
        "pending_total": pending_total,
        "balance_due": round(max(0.0, balance_due), 2),
        "net_balance": balance_due,
        "credit_balance": credit_balance,
        "in_credit": credit_balance > 0,
        # GAP-FIN-047 item (a): authoritative net_balance-based owing/credit — what the
        # Unit Finance Detail "Balance Due" tile reads (see comment above). balance_owing +
        # balance_credit are mutually exclusive: at most one is > 0 for a given unit.
        "balance_owing": balance_owing,
        "balance_credit": balance_credit,
        "ledger_net_balance": round(ledger_net_balance, 2),
        "prev_year": prev_year,
        "prev_year_balance": prev_year_balance,
        "volunteer_credits_ytd": volunteer_credits_ytd,
        "credit_history": credit_history,
        "payment_history": payment_history,
        "entitlement": uoe,
        "quarters": period_status,
        "ledger": {
            "admin_opening": ledger.get("admin_opening") if ledger else None,
            "admin_levied": ledger.get("admin_levied") if ledger else None,
            "admin_paid": ledger.get("admin_paid") if ledger else None,
            "admin_closing": ledger.get("admin_closing") if ledger else None,
            "sinking_opening": ledger.get("sinking_opening") if ledger else None,
            "sinking_levied": ledger.get("sinking_levied") if ledger else None,
            "sinking_paid": ledger.get("sinking_paid") if ledger else None,
            "sinking_closing": ledger.get("sinking_closing") if ledger else None,
            "net_balance": ledger.get("net_balance") if ledger else None,
        } if ledger else None,
        # Portal cross-check — read-only snapshot from Strata Web scraper (strata_owners).
        # portal_balance is the portal's current outstanding amount for this unit.
        # It does NOT replace net_balance (the authoritative levy accounting balance).
        "portal_balance": portal_owner.get("balance") if portal_owner else None,
        "portal_status": portal_owner.get("status") if portal_owner else None,
        "portal_synced_at": portal_owner.get("updated_at") if portal_owner else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Finance Export
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/export")
async def export_finance(
        year: Optional[str] = None,
        format: str = "csv",
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # Bulk financial export.
        # NOTE: can_view_finances below is False for strata_manager — a
        # pre-existing bug in DEFAULT_PERMISSIONS, not introduced here, and not
        # fixable by this additive guard. Retiring that boolean is the deferred
        # GAP-SEC-006 work; recorded so the exclusion is not mistaken for intent.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """
    Export unit levy ledger data for a year as CSV.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        # Resolves via _resolve_default_levy_year (never a not-yet-started year) — see
        # module note above get_available_years.
        year = await _resolve_default_levy_year(building_id)

    entries = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year}, {"_id": 0}
    ).sort("unit_number", 1).to_list(200)

    if format == "csv":
        output = io.StringIO()
        output.write(
            "Unit Number,Lot Number,Property Type,UOE,"
            "Admin Opening,Admin Levied,Admin Paid,Admin Closing,"
            "Sinking Opening,Sinking Levied,Sinking Paid,Sinking Closing,"
            "Total Levied,Total Paid,Net Balance\n"
        )
        for e in entries:
            output.write(
                f"{e.get('unit_number', '')},{e.get('lot_number', '')},{e.get('property_type', '')},"
                f"{e.get('uoe', 0)},"
                f"{e.get('admin_opening', 0)},{e.get('admin_levied', 0)},{e.get('admin_paid', 0)},{e.get('admin_closing', 0)},"
                f"{e.get('sinking_opening', 0)},{e.get('sinking_levied', 0)},{e.get('sinking_paid', 0)},{e.get('sinking_closing', 0)},"
                f"{e.get('total_levied', 0)},{e.get('total_paid', 0)},{e.get('net_balance', 0)}\n"
            )
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=levy_ledger_{year}.csv"}
        )

    return {"error": "Unsupported format"}


# ─────────────────────────────────────────────────────────────────────────────
# CSV Upload for Data Management
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/finance/upload-budget-categories", response_model=BulkUploadResult)
async def upload_budget_categories(
        year: Optional[str] = Form(None),
        fund_type: Optional[str] = Form(None),
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Upload proposed budget categories from CSV.

    CSV format (no header required, or with header):
        category_name,budgeted_amount,description(optional)

    Example:
        Cleaning,27500,Annual cleaning contract
        Management Fee,27682,Strata management fee

    Requires can_manage_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    content = await file.read()
    await scan_upload(content, context="csv", filename=file.filename or "")
    text = _decode_upload_text(content)
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or []) if h]
    default_fund_type = _normalize_upload_fund_type(fund_type)

    inserted = 0
    errors = []

    if {"year", "fund_type"} & set(headers):
        rows = list(reader)
        for idx, row in enumerate(rows, start=2):
            row_year = _csv_field(row.get("year") or row.get("financial_year") or year)
            row_fund_type = _normalize_upload_fund_type(row.get("fund_type") or default_fund_type)
            name = _csv_field(row.get("name") or row.get("category_name"))
            if not row_year or not row_fund_type or not name:
                errors.append(f"Row {idx}: missing year, fund_type, or category name")
                continue

            budgeted_amount = _parse_upload_float(row.get("budgeted_amount") or row.get("planned"))
            actual_raw = _csv_field(row.get("actual_amount") or row.get("actual"))
            actual_amount = _parse_upload_float(actual_raw) if actual_raw else 0.0
            description = _csv_field(row.get("description"))
            status = "actual" if actual_raw else "proposed"
            now = _now()
            existing = await db.levy_categories.find_one(
                {"building_id": building_id, "year": row_year, "fund_type": row_fund_type, "name": name}
            )
            update_doc = {
                "budgeted_amount": budgeted_amount,
                "actual_amount": actual_amount,
                "description": description,
                "status": status,
                "updated_at": now,
            }
            if existing:
                await db.levy_categories.update_one(
                    {"id": existing["id"], "building_id": building_id},
                    {"$set": update_doc}
                )
            else:
                await db.levy_categories.insert_one({
                    "id": str(uuid.uuid4()),
                    "building_id": building_id,
                    "year": row_year,
                    "status": status,
                    "fund_type": row_fund_type,
                    "name": name,
                    "budgeted_amount": budgeted_amount,
                    "actual_amount": actual_amount,
                    "description": description,
                    "created_at": now,
                    "updated_at": now,
                })
            inserted += 1
        return {
            "message": f"Uploaded {inserted} categories from CSV rows",
            "inserted": inserted,
            "errors": errors,
        }

    if default_fund_type not in ("administrative", "sinking"):
        raise HTTPException(status_code=400, detail="fund_type must be 'administrative' or 'sinking'")
    if not year:
        raise HTTPException(status_code=400, detail="year is required when CSV rows do not include a year column")

    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.lower().startswith("category"):
            continue

        parts = [p.strip() for p in line.split(",", 2)]
        if len(parts) < 2:
            errors.append(f"Line {i + 1}: insufficient columns — '{line}'")
            continue

        name = parts[0]
        try:
            budgeted_amount = float(parts[1].replace("$", "").replace(",", ""))
        except ValueError:
            errors.append(f"Line {i + 1}: invalid amount '{parts[1]}'")
            continue

        description = parts[2] if len(parts) > 2 else ""
        now = _now()
        existing = await db.levy_categories.find_one(
            {"building_id": building_id, "year": year, "fund_type": default_fund_type, "name": name}
        )
        if existing:
            await db.levy_categories.update_one(
                {"id": existing["id"], "building_id": building_id},
                {"$set": {"budgeted_amount": budgeted_amount, "description": description, "updated_at": now}}
            )
        else:
            await db.levy_categories.insert_one({
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "year": year,
                "status": "proposed",
                "fund_type": default_fund_type,
                "name": name,
                "budgeted_amount": budgeted_amount,
                "actual_amount": 0.0,
                "description": description,
                "created_at": now,
                "updated_at": now,
            })
        inserted += 1

    return {
        "message": f"Uploaded {inserted} categories for {year} {default_fund_type}",
        "inserted": inserted,
        "errors": errors,
    }


@router.post("/finance/upload-actuals", response_model=BulkUploadResult)
async def upload_actual_expenses(
        year: Optional[str] = Form(None),
        fund_type: Optional[str] = Form(None),
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Upload year-end actual expenses from CSV to update levy_categories.actual_amount.

    CSV format:
        category_name,actual_amount

    Requires can_manage_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    content = await file.read()
    await scan_upload(content, context="csv", filename=file.filename or "")
    text = _decode_upload_text(content)
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or []) if h]
    default_fund_type = _normalize_upload_fund_type(fund_type)

    updated = 0
    not_found = []
    errors = []

    if {"year", "fund_type"} & set(headers):
        rows = list(reader)
        for idx, row in enumerate(rows, start=2):
            row_year = _csv_field(row.get("year") or row.get("financial_year") or year)
            row_fund_type = _normalize_upload_fund_type(row.get("fund_type") or default_fund_type)
            name = _csv_field(row.get("name") or row.get("category_name"))
            actual_raw = row.get("actual_amount") or row.get("actual")
            if not row_year or not row_fund_type or not name:
                errors.append(f"Row {idx}: missing year, fund_type, or category name")
                continue
            actual_amount = _parse_upload_float(actual_raw)
            result = await db.levy_categories.update_one(
                {"building_id": building_id, "year": row_year, "fund_type": row_fund_type, "name": name},
                {"$set": {"actual_amount": actual_amount, "status": "actual", "updated_at": _now()}}
            )
            if result.matched_count == 0:
                not_found.append(f"{row_year}/{row_fund_type}/{name}")
            else:
                updated += 1
        return {
            "message": f"Updated {updated} actual amounts from CSV rows",
            "updated": updated,
            "not_found": not_found,
            "errors": errors,
        }

    if default_fund_type not in ("administrative", "sinking"):
        raise HTTPException(status_code=400, detail="fund_type must be 'administrative' or 'sinking'")
    if not year:
        raise HTTPException(status_code=400, detail="year is required when CSV rows do not include a year column")

    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.lower().startswith("category"):
            continue

        parts = [p.strip() for p in line.split(",", 1)]
        if len(parts) < 2:
            errors.append(f"Line {i + 1}: insufficient columns")
            continue

        name = parts[0]
        try:
            actual_amount = float(parts[1].replace("$", "").replace(",", ""))
        except ValueError:
            errors.append(f"Line {i + 1}: invalid amount '{parts[1]}'")
            continue

        result = await db.levy_categories.update_one(
            {"building_id": building_id, "year": year, "fund_type": default_fund_type, "name": name},
            {"$set": {"actual_amount": actual_amount, "status": "actual", "updated_at": _now()}}
        )
        if result.matched_count == 0:
            not_found.append(name)
        else:
            updated += 1

    return {
        "message": f"Updated {updated} actual amounts for {year} {default_fund_type}",
        "updated": updated,
        "not_found": not_found,
        "errors": errors,
    }


@router.post("/finance/upload-unit-ledger", response_model=BulkUploadResult)
async def upload_unit_ledger(
        year: Optional[str] = Form(None),
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Upload per-unit levy ledger from CSV.

    CSV format (header required):
        lot_number,admin_opening,admin_levied,admin_paid,admin_closing,
        sinking_opening,sinking_levied,sinking_paid,sinking_closing,net_balance

    Requires can_manage_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    content = await file.read()
    await scan_upload(content, context="csv", filename=file.filename or "")
    text = _decode_upload_text(content)

    # Get unit mapping
    units_in_db = await db.units.find({"building_id": building_id},
                                      {"_id": 0, "lot_number": 1, "unit_number": 1, "entitlement": 1,
                                       "unit_entitlement": 1, "unit_type": 1}).to_list(200)
    lot_to_unit = {str(u.get("lot_number", "")).upper(): u for u in units_in_db if u.get("lot_number")}
    unit_to_info = {str(u.get("unit_number", "")).upper(): u for u in units_in_db if u.get("unit_number")}

    inserted = 0
    errors = []
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or []) if h]

    if "lot_number" in headers and ("admin_opening" in headers or "admin_opening_balance" in headers):
        rows = list(reader)
        for idx, row in enumerate(rows, start=2):
            lot = _csv_field(row.get("lot_number")).upper()
            unit_number = _csv_field(row.get("unit_number")).upper()
            if lot and not lot.startswith("LOT"):
                lot = f"LOT{lot}"

            unit_info = lot_to_unit.get(lot) or unit_to_info.get(unit_number)
            if not unit_info:
                errors.append(f"Row {idx}: unit mapping not found for lot '{lot}' / unit '{unit_number}'")
                continue

            row_year = _csv_field(row.get("year") or row.get("financial_year") or year)
            if not row_year:
                errors.append(f"Row {idx}: missing year or financial_year")
                continue

            resolved_unit_number = _csv_field(row.get("unit_number") or unit_info.get("unit_number"))
            if not resolved_unit_number:
                errors.append(f"Row {idx}: missing unit_number")
                continue

            uoe = _parse_upload_float(
                row.get("uoe") or row.get("unit_entitlement") or unit_info.get("unit_entitlement") or unit_info.get(
                    "entitlement")
            )
            admin_opening = _parse_upload_float(row.get("admin_opening") or row.get("admin_opening_balance"))
            admin_levied = _parse_upload_float(row.get("admin_levied"))
            admin_paid = _parse_upload_float(row.get("admin_paid"))
            admin_closing = _parse_upload_float(row.get("admin_closing") or row.get("admin_closing_balance"))
            sinking_opening = _parse_upload_float(row.get("sinking_opening") or row.get("sinking_opening_balance"))
            sinking_levied = _parse_upload_float(row.get("sinking_levied"))
            sinking_paid = _parse_upload_float(row.get("sinking_paid"))
            sinking_closing = _parse_upload_float(row.get("sinking_closing") or row.get("sinking_closing_balance"))
            total_levied = _parse_upload_float(row.get("total_levied"), admin_levied + sinking_levied)
            total_paid = _parse_upload_float(row.get("total_paid"), admin_paid + sinking_paid)
            net_balance = _parse_upload_float(row.get("net_balance"), admin_closing + sinking_closing)
            now = _now()
            doc = {
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "year": row_year,
                "unit_number": resolved_unit_number,
                "lot_number": lot or unit_info.get("lot_number", ""),
                "uoe": uoe,
                "property_type": _csv_field(row.get("property_type") or unit_info.get("unit_type")),
                "admin_opening": admin_opening,
                "admin_levied": admin_levied,
                "admin_paid": admin_paid,
                "admin_closing": admin_closing,
                "sinking_opening": sinking_opening,
                "sinking_levied": sinking_levied,
                "sinking_paid": sinking_paid,
                "sinking_closing": sinking_closing,
                "total_levied": total_levied,
                "total_paid": total_paid,
                "net_balance": net_balance,
                "created_at": now,
                "updated_at": now,
            }
            await db.unit_levy_ledger.replace_one(
                {"building_id": building_id, "unit_number": resolved_unit_number, "year": row_year},
                doc,
                upsert=True
            )
            inserted += 1

        return {
            "message": f"Uploaded {inserted} unit ledger records from CSV rows",
            "inserted": inserted,
            "errors": errors,
        }

    if not year:
        raise HTTPException(status_code=400, detail="year is required when CSV rows do not include a year column")

    lines = text.splitlines()
    header_skipped = False
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if not header_skipped and "lot" in line.lower():
            header_skipped = True
            continue

        parts = [p.strip().replace("$", "").replace(",", "") for p in line.split(",")]
        if len(parts) < 10:
            errors.append(f"Line {i + 1}: insufficient columns (need 10)")
            continue

        lot = parts[0].upper()
        if not lot.startswith("LOT"):
            lot = f"LOT{lot}"

        unit_info = lot_to_unit.get(lot)
        if not unit_info:
            errors.append(f"Line {i + 1}: lot {lot} not found in units collection")
            continue

        try:
            now = _now()
            doc = {
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "year": year,
                "unit_number": unit_info["unit_number"],
                "lot_number": lot,
                "uoe": unit_info.get("unit_entitlement") or unit_info.get("entitlement", 0),
                "property_type": unit_info.get("unit_type", ""),
                "admin_opening": float(parts[1] or 0),
                "admin_levied": float(parts[2] or 0),
                "admin_paid": float(parts[3] or 0),
                "admin_closing": float(parts[4] or 0),
                "sinking_opening": float(parts[5] or 0),
                "sinking_levied": float(parts[6] or 0),
                "sinking_paid": float(parts[7] or 0),
                "sinking_closing": float(parts[8] or 0),
                "total_levied": float(parts[2] or 0) + float(parts[6] or 0),
                "total_paid": float(parts[3] or 0) + float(parts[7] or 0),
                "net_balance": float(parts[9] or 0),
                "created_at": now,
                "updated_at": now,
            }
        except (ValueError, IndexError) as e:
            errors.append(f"Line {i + 1}: parse error — {e}")
            continue

        await db.unit_levy_ledger.replace_one(
            {"building_id": building_id, "unit_number": unit_info["unit_number"], "year": year},
            doc,
            upsert=True
        )
        inserted += 1

    return {
        "message": f"Uploaded {inserted} unit ledger records for {year}",
        "inserted": inserted,
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Combined Budget+Actuals Upload (rich CSV format)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/finance/upload-budget-actuals", response_model=BulkUploadResult)
async def upload_budget_actuals(
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Upload combined budget + actuals CSV in the rich format produced by strata systems.

    CSV columns (header required):
        year, category_id, category_name, planned, actual, variance, previous_actual

    All 36 admin-fund categories (or any mix of admin/sinking) are upserted in one pass.
    The fund_type is inferred from category_id: 101-199 → administrative, 200-299 → sinking.
    If a category_id outside those ranges is provided, fund_type defaults to administrative.

    Requires can_manage_finances permission.
    Allowed roles: super_admin, strata_manager, chairman.
    """
    allowed_roles = {"super_admin", "strata_manager", "ec_member", "strata_admin"}
    if effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Access restricted to Super Admin, Strata Manager, or Chairman")

    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    content = await file.read()
    await scan_upload(content, context="csv", filename=file.filename or "")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="Empty file")

    # Parse header
    header = [h.strip().lower() for h in lines[0].split(",")]
    required_cols = {"year", "category_name", "planned"}
    if not required_cols.issubset(set(header)):
        raise HTTPException(
            status_code=400,
            detail=f"Missing required columns. Expected at minimum: year, category_name, planned. Got: {', '.join(header)}"
        )

    def _col(row_parts, col_name, default=None):
        """Generated function header.

        Function: _col
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if col_name in header:
            val = row_parts[header.index(col_name)].strip().replace("$", "").replace(",", "")
            return val if val else default
        return default

    def _float(val, default=0.0):
        """Generated function header.

        Function: _float
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            return round(float(val), 2) if val is not None else default
        except (ValueError, TypeError):
            return default

    def _infer_fund_type(category_id_str):
        """Generated function header.

        Function: _infer_fund_type
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            cid = int(category_id_str)
            if 200 <= cid <= 299:
                return "sinking"
        except (ValueError, TypeError):
            pass
        return "administrative"

    def _status(budgeted, actual):
        """Generated function header.

        Function: _status
        Path: backend/routers/finance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if budgeted > 0 and actual > budgeted:
            return "over_budget"
        if actual < budgeted:
            return "under_budget"
        return "on_track"

    now = _now()
    upserted = 0
    skipped = 0
    errors = []
    # Track per-year fund totals so we can update annual_levies after the loop.
    levy_totals: dict = {}  # {year: {admin_planned, admin_actual, sinking_planned, sinking_actual}}
    current_levy_year = await _resolve_current_levy_year(building_id)

    for i, line in enumerate(lines[1:], start=2):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(header):
            # pad short rows
            parts += [""] * (len(header) - len(parts))

        year_val = _col(parts, "year")
        name = _col(parts, "category_name", "")
        if not year_val or not name:
            errors.append(f"Row {i}: missing year or category_name — skipped")
            skipped += 1
            continue
        year_int = _year_int(year_val)
        if year_int is not None and year_int > current_levy_year:
            errors.append(
                f"Row {i}: levy year {year_val} has not started for this building — skipped"
            )
            skipped += 1
            continue

        cat_id_str = _col(parts, "category_id", "")
        fund_type = _infer_fund_type(cat_id_str)
        budgeted = _float(_col(parts, "planned", "0"))
        actual = _float(_col(parts, "actual", "0"))
        prev_actual = _float(_col(parts, "previous_actual", "0"))
        variance = _float(_col(parts, "variance", None))
        if variance == 0.0 and _col(parts, "variance") is None:
            variance = round(budgeted - actual, 2)

        status = _status(budgeted, actual)

        update_doc = {
            "building_id": building_id,
            "plan_id": building_id,  # alias for building_id; never hardcode
            "year": year_val,
            "fund_type": fund_type,
            "name": name,
            "budgeted_amount": budgeted,
            "actual_amount": actual,
            "previous_actual": prev_actual,
            "variance": variance,
            "status": status,
            "updated_at": now,
        }
        if cat_id_str:
            try:
                update_doc["category_id"] = int(cat_id_str)
            except ValueError:
                pass

        existing = await db.levy_categories.find_one(
            {"building_id": building_id, "year": year_val, "name": name}
        )
        if existing:
            await db.levy_categories.update_one(
                {"id": existing["id"], "building_id": building_id},
                {"$set": update_doc}
            )
        else:
            update_doc["id"] = str(uuid.uuid4())
            update_doc["created_at"] = now
            await db.levy_categories.insert_one(update_doc)

        # Accumulate totals for annual_levies update
        if year_val not in levy_totals:
            levy_totals[year_val] = {
                "admin_planned": 0.0, "admin_actual": 0.0,
                "sinking_planned": 0.0, "sinking_actual": 0.0,
            }
        if fund_type == "administrative":
            levy_totals[year_val]["admin_planned"] += budgeted
            levy_totals[year_val]["admin_actual"] += actual
        else:
            levy_totals[year_val]["sinking_planned"] += budgeted
            levy_totals[year_val]["sinking_actual"] += actual

        upserted += 1

    # Update annual_levies for each year found in the upload.
    # Creates a synthetic record if none exists; only overwrites income on synthetic records
    # so AGM-ratified data is never clobbered by a budget upload.
    for yr, totals in levy_totals.items():
        existing_levy = await db.annual_levies.find_one({"building_id": building_id, "year": yr})
        ap = round(totals["admin_planned"], 2)
        aa = round(totals["admin_actual"], 2)
        sp = round(totals["sinking_planned"], 2)
        sa = round(totals["sinking_actual"], 2)
        if not existing_levy:
            await db.annual_levies.insert_one({
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "plan_id": building_id,
                "year": yr,
                "status": "partial_actual",
                "total_uoe": TOTAL_UOE,
                "admin_fund": {
                    "levy_income": ap, "total_income": ap, "total_expenses": aa,
                    "opening_balance": 0.0, "closing_balance": 0.0,
                    "surplus_deficit": round(ap - aa, 2),
                },
                "sinking_fund": {
                    "levy_income": sp, "total_income": sp, "total_expenses": sa,
                    "opening_balance": 0.0, "closing_balance": 0.0,
                    "surplus_deficit": round(sp - sa, 2),
                },
                "payment_schedule": [],
                "admin_levy_per_uoe_annual": 0.0,
                "admin_levy_per_uoe_quarterly": 0.0,
                "sinking_levy_per_uoe_annual": 0.0,
                "sinking_levy_per_uoe_quarterly": 0.0,
                "data_source": "upload_budget_actuals",
                "is_synthetic": True,
                "created_at": now,
                "updated_at": now,
            })
        else:
            upd: dict = {
                "admin_fund.total_expenses": aa,
                "sinking_fund.total_expenses": sa,
                "updated_at": now,
            }
            if existing_levy.get("is_synthetic") or existing_levy.get("data_source") in (
                    "scraper_import", "upload_budget_actuals"
            ):
                upd.update({
                    "admin_fund.levy_income": ap,
                    "admin_fund.total_income": ap,
                    "sinking_fund.levy_income": sp,
                    "sinking_fund.total_income": sp,
                })
            await db.annual_levies.update_one(
                {"building_id": building_id, "year": yr},
                {"$set": upd},
            )

    return {
        "message": f"Processed {upserted} categories ({skipped} skipped, {len(errors)} errors)",
        "imported": upserted,
        "skipped": skipped,
        "errors": errors,
    }


@router.post("/finance/fund-balances", response_model=FundBalancesResponse)
async def update_fund_balances(
        admin_balance: float = Form(...),
        sinking_balance: float = Form(...),
        as_of_date: str = Form(...),
        financial_year: str = Form(...),
        notes: Optional[str] = Form(None),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Record the current bank fund balances for admin and sinking funds.

    Creates two bank_reconciliation snapshot records (one per fund) and updates
    annual_levies.admin_fund.current_balance / sinking_fund.current_balance.

    Allowed roles: super_admin, strata_manager, chairman.
    """
    allowed_roles = {"super_admin", "strata_manager", "ec_member", "strata_admin"}
    if effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Access restricted to Super Admin, Strata Manager, or Chairman")

    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = _now()

    # Upsert bank_reconciliations snapshot for admin fund
    for fund_type, balance in [("administrative", admin_balance), ("sinking", sinking_balance)]:
        snap = {
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "plan_id": building_id,  # alias for building_id; never hardcode
            "financial_year": financial_year,
            "fund_type": fund_type,
            "statement_date": as_of_date,
            "as_of_date": as_of_date,
            "opening_balance": balance,
            "closing_balance": balance,
            "total_receipts": 0.0,
            "total_payments": 0.0,
            "admin_balance": admin_balance if fund_type == "administrative" else None,
            "sinking_balance": sinking_balance if fund_type == "sinking" else None,
            "notes": notes or "",
            "recorded_by": current_user.get("email", ""),
            "variance_amount": 0.0,
            "is_reconciled": False,
            "created_at": now,
            "updated_at": now,
        }
        # Remove None values
        snap = {k: v for k, v in snap.items() if v is not None}

        # Replace existing snapshot for same date+fund_type or insert
        await db.bank_reconciliations.replace_one(
            {"building_id": building_id, "financial_year": financial_year,
             "fund_type": fund_type, "as_of_date": as_of_date},
            snap,
            upsert=True
        )

    # Update annual_levies current_balance fields
    await db.annual_levies.update_one(
        {"building_id": building_id, "year": financial_year.split("-")[0]},
        {"$set": {
            "admin_fund.current_balance": admin_balance,
            "sinking_fund.current_balance": sinking_balance,
            "updated_at": now,
        }},
        upsert=False
    )

    total = round(admin_balance + sinking_balance, 2)
    return {
        "message": f"Fund balances recorded for {as_of_date}",
        "admin_balance": admin_balance,
        "sinking_balance": sinking_balance,
        "total": total,
        "financial_year": financial_year,
        "as_of_date": as_of_date,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Payment Methods
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/payment-methods")
async def get_payment_methods(building_id: str = Depends(get_current_building)):
    """Get configured payment methods for levy payments — sourced from building settings."""
    s = await _get_general_settings(building_id, {"_id": 0}) or {}
    bank_name = s.get("bank_name", "")
    bank_bsb = s.get("bank_bsb", "")
    bank_account_number = s.get("bank_account_number", "")
    bank_account_name = s.get("bank_account_name", s.get("building_name", ""))
    deft_ref = s.get("deft_ref", "")
    bpay_biller_code = s.get("bpay_biller_code", "")
    bpay_ref = s.get("bpay_ref", "")
    aus_post_code = s.get("aus_post_code", "")
    aus_post_ref = s.get("aus_post_ref", "")
    return {
        "methods": [
            {
                "id": "stripe", "name": "Online Payment (Stripe)", "enabled": True,
                "description": "Pay securely online using credit or debit card.",
                "surcharge": "1.75% + 30c", "is_online": True,
            },
            {
                "id": "deft", "name": "DEFT", "enabled": True,
                "description": "Direct Entry Funds Transfer",
                "instructions": "Pay online at deft.com.au",
                "url": "https://deft.com.au",
                "deft_ref": deft_ref,
            },
            {
                "id": "bpay", "name": "BPAY", "enabled": True,
                "description": "Pay via your bank's BPAY service",
                "biller_code": bpay_biller_code,
                "bpay_ref": bpay_ref,
            },
            {
                "id": "credit_card", "name": "Credit Card", "enabled": True,
                "description": "Visa, Mastercard, Amex",
                "surcharge": "2.0%",
            },
            {
                "id": "bank_transfer", "name": "Direct Bank Transfer", "enabled": True,
                "description": "Transfer directly to our bank account",
                "bank_name": bank_name,
                "bsb": bank_bsb,
                "account_number": bank_account_number,
                "account_name": bank_account_name,
                "reference_format": "Unit number + Quarter (e.g., UA001 Q1)",
            },
            {
                "id": "australia_post", "name": "Australia Post / Post Billpay", "enabled": True,
                "description": "Pay at any Australia Post outlet",
                "billpay_code": aus_post_code,
                "aus_post_ref": aus_post_ref,
            },
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Levy Reminders
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/levy-reminder-settings")
async def get_levy_reminder_settings(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_levy_reminder_settings
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications:
        raise HTTPException(status_code=403, detail="Not authorized")
    settings = await get_pg_levy_reminder_settings(building_id, settings_db=db)
    if not settings:
        return {"building_id": building_id, "enabled": False, "reminder_days": [14, 7], "last_sent": None}
    return {
        "building_id": building_id,
        "enabled": settings.get("enabled", False),
        "reminder_days": settings.get("pre_due_days", [14, 7]),
        "last_sent": settings.get("last_sent"),
    }


@router.put("/levy-reminder-settings", response_model=LevyReminderSettingsAck)
async def update_levy_reminder_settings(
        data: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_levy_reminder_settings
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications:
        raise HTTPException(status_code=403, detail="Not authorized")
    update_payload = {
        **data,
        "enabled": data.get("enabled", False),
        "pre_due_days": data.get("pre_due_days", data.get("reminder_days", [14, 7])),
        "updated_by": current_user["id"],
        "updated_at": get_current_timestamp(),
    }
    await upsert_pg_levy_reminder_settings(
        building_id,
        update_payload,
        updated_by=current_user["id"],
        settings_db=db,
    )
    return {"message": "Levy reminder settings updated"}


@router.get("/levy-reminder-log")
async def get_levy_reminder_log(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_levy_reminder_log
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications:
        raise HTTPException(status_code=403, detail="Not authorized")
    logs = await db.auto_reminders_log.find({"building_id": building_id}, {"_id": 0}).sort("created_at", -1).to_list(20)
    return logs


# ─────────────────────────────────────────────────────────────────────────────
# Financial Projections
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/projections", response_model=FinancialProjectionResponse)
async def create_projection(
        data: FinancialProjectionCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_projection
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    base_levy = await db.annual_levies.find_one({"building_id": building_id, "year": data.base_year}, {"_id": 0})
    if not base_levy:
        raise HTTPException(status_code=404, detail=f"No levy data for year {data.base_year}")

    assumptions = data.assumptions
    projections = []

    af = base_levy.get("admin_fund", {})
    sf = base_levy.get("sinking_fund", {})

    admin_income = af.get("total_income", 0)
    admin_expenses = af.get("total_expenses", 0)
    admin_closing = af.get("closing_balance", 0)
    sinking_closing = sf.get("closing_balance", 0)

    base_year_int = int(data.base_year)

    for i in range(data.projection_years):
        year = base_year_int + i + 1
        inflation = 1 + (assumptions.inflation_rate / 100)

        projected_expenses = admin_expenses * (inflation ** (i + 1))
        projected_income = admin_income * (inflation ** (i + 1))
        sinking_contribution = assumptions.sinking_fund_contribution * (inflation ** (i + 1))
        major_work_cost = sum(
            mw.get("amount", 0) for mw in assumptions.major_works
            if mw.get("year") == year
        )

        admin_closing = admin_closing + projected_income - projected_expenses
        sinking_closing = sinking_closing + sinking_contribution - major_work_cost

        total_budget = projected_expenses + sinking_contribution
        levy_per_uoe = total_budget / TOTAL_UOE

        projections.append({
            "year": str(year),
            "admin_income": round(projected_income, 2),
            "admin_expenses": round(projected_expenses, 2),
            "admin_closing": round(admin_closing, 2),
            "sinking_income": round(sinking_contribution, 2),
            "sinking_expenses": round(major_work_cost, 2),
            "sinking_closing": round(sinking_closing, 2),
            "total_budget": round(total_budget, 2),
            "levy_per_uoe": round(levy_per_uoe, 4),
            "major_works": [mw for mw in assumptions.major_works if mw.get("year") == year],
        })

    projection_id = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": projection_id,
        "building_id": building_id,
        "projection_name": data.projection_name,
        "base_year": data.base_year,
        "projection_years": data.projection_years,
        "assumptions": assumptions.model_dump(),
        "projections": projections,
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    }
    await db.projections.insert_one(doc)
    return FinancialProjectionResponse(**doc)


@router.get("/projections", response_model=List[FinancialProjectionResponse])
async def get_projections(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_projections
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")
    projections = await db.projections.find({"building_id": building_id}, {"_id": 0}).sort("created_at", -1).to_list(20)
    return [FinancialProjectionResponse(**p) for p in projections]


@router.delete("/projections/{projection_id}", response_model=MessageAck)
async def delete_projection(
        projection_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_projection
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")
    await db.projections.delete_one({"id": projection_id, "building_id": building_id})
    return {"message": "Projection deleted"}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: notify on levy payment
# ─────────────────────────────────────────────────────────────────────────────

async def _notify_levy_payment(unit_number: str, amount: float, quarter: str, year: str, building_id: str):
    """Generated function header.

    Function: _notify_levy_payment
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        users = await db.user_units.find(
            {"building_id": building_id, "unit_number": unit_number, "is_active": True}).to_list(10)
        for uu in users:
            await create_user_notification(
                user_id=uu["user_id"],
                title="Levy Payment Recorded",
                message=f"A payment of ${amount:,.2f} has been recorded for Unit {unit_number} ({quarter} {year})",
                notification_type="levy",
                link="/financials/levy-payments"
            )
    except Exception as e:
        print(f"Error notifying levy payment: {e}")


async def _notify_payment_verified(unit_number: str, amount: float, quarter: str, year: str, receipt: str,
                                   building_id: str):
    """Generated function header.

    Function: _notify_payment_verified
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        users = await db.user_units.find(
            {"building_id": building_id, "unit_number": unit_number, "is_active": True}).to_list(10)
        for uu in users:
            await create_user_notification(
                user_id=uu["user_id"],
                title="Levy Payment Confirmed",
                message=f"Your payment of ${amount:,.2f} ({receipt}) for {quarter} {year} has been confirmed.",
                notification_type="levy",
                link="/financials/levy-payments?tab=history"
            )
    except Exception as e:
        print(f"Error notifying payment verification: {e}")


async def _notify_payment_rejected(unit_number: str, amount: float, quarter: str, year: str, receipt: str, reason: str,
                                   building_id: str):
    """Generated function header.

    Function: _notify_payment_rejected
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        users = await db.user_units.find(
            {"building_id": building_id, "unit_number": unit_number, "is_active": True}).to_list(10)
        for uu in users:
            await create_user_notification(
                user_id=uu["user_id"],
                title="Levy Payment Not Verified",
                message=f"Your payment of ${amount:,.2f} ({receipt}) for {quarter} {year} could not be verified. Reason: {reason}",
                notification_type="levy",
                link="/financials/levy-payments?tab=history"
            )
    except Exception as e:
        print(f"Error notifying payment rejection: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Transactions
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/expense-transactions", response_model=ExpenseTransactionResponse)
async def record_expense(
        data: ExpenseTransactionCreate,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Record a manual supplier invoice. Scoped to building."""
    now_ts = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **data.model_dump(),
        "created_by": current_user["id"],
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    # Ensure date is always set (ISO date string for display)
    if not doc.get("date"):
        doc["date"] = now_ts[:10]
    # Ensure description falls back to supplier_name if not provided
    if not doc.get("description") and doc.get("supplier_name"):
        doc["description"] = doc["supplier_name"]
    await db.expense_transactions.insert_one(doc)

    asyncio.create_task(create_audit_log(
        action="created",
        resource_type="expense_transaction",
        resource_id=doc["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"amount": data.amount, "supplier": data.supplier_name, "category": data.category_id}
    ))

    # H-4: trigger async building summary recompute so the health score and aggregate
    # fund balance reflect the new expense without waiting for the next scheduled run.
    # Phase G: when expense data lives in Postgres, gate behind
    #   if not _postgres_enabled_for(building_id)
    from workers.analytics_worker import recompute_building_summary as _recompute_bldg
    asyncio.create_task(_recompute_bldg(building_id))

    return ExpenseTransactionResponse(**doc)


@router.get("/expense-transactions", response_model=List[ExpenseTransactionResponse])
async def get_expenses(
        year: Optional[str] = None,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_expenses
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}
    if year:
        query.update(_year_match_filter(year))

    # Memoised for the life of this request. The Mongo aggregate is needed twice — once
    # as the possible response, once as the shadow comparison's Mongo side — and calling
    # the reader twice would issue every one of those queries twice on every request,
    # including the `_fallback_financial_transactions` chain behind it. It also breaks
    # any caller whose cursor is single-use, which is how the legacy-empty fallback test
    # started returning an empty income list.
    _mongo_expense_cache: list[dict] | None = None

    async def _mongo_expenses() -> list[dict]:
        nonlocal _mongo_expense_cache
        if _mongo_expense_cache is not None:
            return _mongo_expense_cache
        rows = await db.expense_transactions.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        if not rows:
            rows = await _fallback_financial_transactions(
                building_id=building_id, year=year, transaction_type="expense",
            )
        _mongo_expense_cache = rows
        return rows

    # Store-agnostic read. finance_ledger is postgres_write/promoted for East Gate, so
    # this now serves Postgres where the control plane says so — and falls back to
    # MongoDB when Postgres is empty for the year (the coexistence window) or
    # unavailable. Those two are reported distinctly by read_through; collapsing them
    # is how an empty Postgres read gets mistaken for "this year had no expenses".
    _read = await read_through(
        domain="finance_ledger",
        building_id=building_id,
        route="finance.expense_transactions",
        postgres=lambda: _financial_read_service.get_expense_transactions(
            building_id=building_id, financial_year=year,
        ),
        mongo=_mongo_expenses,
    )
    expenses = _read.items

    # The shadow comparison keeps running against the MONGO aggregate specifically.
    # Comparing whatever this request happened to serve against Postgres would compare
    # Postgres with itself the moment the route is promoted, and report a permanent
    # clean pass that means nothing.
    asyncio.create_task(_maybe_shadow_transactions(building_id, year, await _mongo_expenses(), [], "expense"))
    return [ExpenseTransactionResponse(**e) for e in expenses]


@router.post("/expense-transactions/reconcile", response_model=ReconcileResponse)
async def reconcile_expenses_to_category(
        financial_year: str,
        category_id: str,
        fund_type: str,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """
    Sum all expense_transactions for a category and push to levy_categories.actual_amount.
    Verified manual reconciliation action. Scoped to building.
    """
    pipeline = [
        {"$match": {
            "building_id": building_id,
            "financial_year": financial_year,
            "category_id": category_id,
            "fund_type": fund_type
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    res = await db.expense_transactions.aggregate(pipeline).to_list(1)
    total_actual = round(res[0]["total"], 2) if res else 0.0

    result = await db.levy_categories.update_one(
        {"building_id": building_id, "year": financial_year, "name": category_id, "fund_type": fund_type},
        {"$set": {"actual_amount": total_actual, "status": "actual", "updated_at": _now()}}
    )

    if result.matched_count == 0:
        result = await db.levy_categories.update_one(
            {"id": category_id},
            {"$set": {"actual_amount": total_actual, "status": "actual", "updated_at": _now()}}
        )

    await create_audit_log(
        action="reconciled",
        resource_type="levy_category",
        resource_id=category_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"actual_amount": total_actual, "year": financial_year}
    )

    return {"success": True, "actual_amount": total_actual}


@router.get("/income-transactions", response_model=List[IncomeTransactionResponse])
async def get_income_transactions(
        year: Optional[str] = None,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """List non-levy income transactions for the building. Mirrors GET /expense-transactions."""
    query: dict = {"building_id": building_id}
    if year:
        query.update(_year_match_filter(year))

    # Memoised — see GET /expense-transactions above for why.
    _mongo_income_cache: list[dict] | None = None

    async def _mongo_income() -> list[dict]:
        nonlocal _mongo_income_cache
        if _mongo_income_cache is not None:
            return _mongo_income_cache
        rows = await db.income_transactions.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        if not rows:
            rows = await _fallback_financial_transactions(
                building_id=building_id, year=year, transaction_type="income",
            )
        _mongo_income_cache = rows
        return rows

    # Same dispatch as GET /expense-transactions. The Postgres reader excludes retired
    # receipts (`retired_at IS NULL`), which the shadow-comparator reader deliberately
    # does not — a reversed receipt must never be shown to an owner as income.
    _read = await read_through(
        domain="finance_ledger",
        building_id=building_id,
        route="finance.income_transactions",
        postgres=lambda: _financial_read_service.get_income_transactions(
            building_id=building_id, financial_year=year,
        ),
        mongo=_mongo_income,
    )
    items = _read.items

    asyncio.create_task(_maybe_shadow_transactions(building_id, year, [], await _mongo_income(), "income"))
    return [IncomeTransactionResponse(**i) for i in items]


@router.post("/income-transactions", response_model=IncomeTransactionResponse)
async def record_income(
        data: IncomeTransactionCreate,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Record manual non-levy income (interest, rebates, etc). Scoped to building."""
    now_ts = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **data.model_dump(),
        "created_by": current_user["id"],
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    if not doc.get("date"):
        doc["date"] = now_ts[:10]
    if not doc.get("description"):
        doc["description"] = doc.get("source", "")
    await db.income_transactions.insert_one(doc)
    return IncomeTransactionResponse(**doc)


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Special Levies
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/special-levies", response_model=SpecialLevyResponse)
async def create_special_levy(
        data: SpecialLevyCreate,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """
    Create a building-wide special levy. Scoped to building.
    Atomically creates per-unit payment records and links to unit ledger.
    """
    special_id = str(uuid.uuid4())
    now = _now()

    levy_doc = {
        "id": special_id,
        **data.model_dump(),
        "building_id": building_id,  # auth context always wins over model default
        "created_at": now,
        "updated_at": now,
    }

    await db.special_levies.insert_one(levy_doc)

    units = await db.units.find({"building_id": building_id}, {"unit_number": 1, "entitlement": 1}).to_list(200)

    payment_docs = []
    for u in units:
        unit_num = u["unit_number"]
        uoe = u.get("entitlement", 0)
        if TOTAL_UOE == 0:
            raise HTTPException(status_code=500, detail="Invalid configuration: TOTAL_UOE cannot be zero")
        share = round((uoe / TOTAL_UOE) * data.total_amount, 2)

        payment_docs.append({
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "special_levy_id": special_id,
            "unit_number": unit_num,
            "amount_levied": share,
            "amount_paid": 0.0,
            "status": "unpaid",
            "created_at": now,
            "updated_at": now,
        })

    if payment_docs:
        await db.special_levy_payments.insert_many(payment_docs)
        await db.unit_levy_ledger.update_many(
            {"building_id": building_id, "year": data.year},
            {"$push": {"special_levy_ids": special_id}}
        )

    await create_audit_log(
        action="created",
        resource_type="special_levy",
        resource_id=special_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"title": data.title, "total_amount": data.total_amount}
    )

    return SpecialLevyResponse(**levy_doc)


@router.get("/special-levies", response_model=List[SpecialLevyResponse])
async def get_special_levies(
        year: Optional[str] = None,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_special_levies
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}
    if year:
        query["year"] = year
    levies = await db.special_levies.find(query, {"_id": 0}).sort("created_at", -1).to_list(50)
    return [SpecialLevyResponse(**l) for l in levies]


@router.get("/special-levies/{special_levy_id}/payments", response_model=List[SpecialLevyPaymentResponse])
async def get_special_levy_payments(
        special_levy_id: str,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_special_levy_payments
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payments = await db.special_levy_payments.find({"building_id": building_id, "special_levy_id": special_levy_id},
                                                   {"_id": 0}).to_list(200)
    return [SpecialLevyPaymentResponse(**p) for p in payments]


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Bank Reconciliation
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/bank-reconciliations", response_model=List[BankReconciliationResponse])
async def get_bank_reconciliations(
        year: Optional[str] = None,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_bank_reconciliations
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}
    if year:
        query["financial_year"] = year
    recons = await db.bank_reconciliations.find(query, {"_id": 0}).sort("statement_date", -1).to_list(100)
    return [BankReconciliationResponse(**r) for r in recons]


@router.post("/bank-reconciliations", response_model=BankReconciliationResponse)
async def create_bank_reconciliation(
        data: BankReconciliationCreate,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """
    Perform a manual bank statement reconciliation.
    Strictly validates variance < $50 if marked reconciled.
    """
    variance = round(data.closing_balance - (data.opening_balance + data.total_receipts - data.total_payments), 2)
    is_reconciled = abs(variance) < 50.0

    recon_id = str(uuid.uuid4())
    now = _now()

    doc = {
        "id": recon_id,
        "building_id": building_id,
        **data.model_dump(),
        "variance_amount": variance,
        "is_reconciled": is_reconciled,
        "reconciled_at": now if is_reconciled else None,
        "reconciled_by": current_user["id"] if is_reconciled else None,
        "created_at": now,
        "updated_at": now,
    }

    await db.bank_reconciliations.insert_one(doc)
    return BankReconciliationResponse(**doc)


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Payment Plans — REMOVED, now owned by routers/payment_plans.py
#
# `POST /payment-plans` and `GET /payment-plans` were declared here AND in
# routers/payment_plans.py. finance_router is included first (server.py), so
# these shadowed the NSW Form 1 s.83A owner-initiated request flow entirely:
# an owner calling POST /payment-plans hit the manager-only guard here and got
# 403 instead of submitting a hardship request. Folded per FIN-PG-CUTOVER-13.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Phase P1: Insurance Policies
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/insurance-policies", response_model=InsurancePolicyResponse)
async def create_insurance_policy(
        data: InsurancePolicyCreate,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Record a new insurance policy. Scoped to building."""
    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **data.model_dump(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.insurance_policies.insert_one(doc)
    return InsurancePolicyResponse(**doc)


@router.get("/insurance-policies", response_model=List[InsurancePolicyResponse])
async def get_insurance_policies(
        active_only: bool = False,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_insurance_policies
    Path: backend/routers/finance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}
    if active_only:
        query["is_active"] = True
    policies = await db.insurance_policies.find(query, {"_id": 0}).sort("end_date", -1).to_list(50)
    return [InsurancePolicyResponse(**p) for p in policies]


# ─────────────────────────────────────────────────────────────────────────────
# Layer: router
# Data flow: ArrearsRecoveryPage → GET /arrears/detail → unit_levy_ledger,
#            units (arrears_metadata.inherited_arrears), levy_payments (building-scoped).
# Related: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
#          backend/server.py (_cascade_owner_change — writes arrears_metadata)
# Phase P1: Arrears Recovery Board
# ─────────────────────────────────────────────────────────────────────────────

# ─── ACT Statutory Interest — UTMA 2011 s.96 ─────────────────────────────────

@router.get("/arrears/interest-rates", response_model=InterestRateResponse)
async def get_arrears_interest_rate(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return the effective levy-arrears interest rate for a building (UTMA 2011 s.96).

    Resolution order: active special resolution → building config → ACT default 10% p.a.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}:
        raise HTTPException(status_code=403, detail="Finance access required.")
    rate_info = await get_effective_interest_rate(building_id, db)
    return InterestRateResponse(building_id=building_id, **rate_info)


@router.post("/arrears/special-resolution-rate", response_model=SpecialResolutionRateResponse, status_code=201)
async def set_special_resolution_interest_rate(
        payload: SpecialResolutionRateCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Record a special resolution raising the arrears interest rate (UTMA 2011 s.96).

    Only super_admin, strata_manager, and strata_admin may record a special resolution.
    Rate must be between 10% (statutory default) and 20% (statutory maximum).
    Any prior active override for this building is superseded automatically.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "strata_admin"}:
        raise HTTPException(status_code=403, detail="Only super_admin, strata_manager, or strata_admin may record a special resolution.")
    try:
        doc = await record_special_resolution_rate(
            building_id=building_id,
            interest_rate_pct=payload.interest_rate_pct,
            passed_date=payload.passed_date,
            passed_by=current_user["id"],
            expires_at=payload.expires_at,
            notes=payload.notes,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    doc["id"] = str(doc.get("id", ""))
    return SpecialResolutionRateResponse(**doc)


@router.get("/arrears/detail")
async def get_arrears_board(
        year: Optional[str] = None,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """
    Main driver for the Arrears Recovery Board. Scoped to building.
    Joins units with ledger and payments to provide actionable arrears data.
    Includes accrued statutory interest per UTM Act 2011 (ACT) § 96.
    """
    route_state = await get_finance_route_runtime_state(
        building_id=building_id,
        route_key="finance.arrears_detail",
    )
    # Performance Optimization⚡: Hoist levy year lookup to enable full parallelization of subsequent data fetches.
    # Parallelizing independent round-trips reduces latency from O(2) to O(1) concurrent sets.
    # When the UI selects a year, use that ledger year explicitly. Without a
    # selected year, resolve through _resolve_default_levy_year (never a
    # not-yet-started year). A raw sort=[("year", -1)] here previously meant a
    # premature next-year annual_levies import could silently point the entire
    # Arrears Recovery Board at an empty/wrong year — see module note above
    # get_available_years.
    year = year or await _resolve_default_levy_year(building_id, fallback=str(date.today().year))

    # 2. Parallel fetch units, ledger, settings, user_units (with owners), and payment stats
    # Performance Optimization⚡: Consolidated 4 database round-trips into 1 concurrent block.
    #
    # Ledger source is the ONLY thing gated on route_state["source"] -- units,
    # owner/portal-account info, and arrears_metadata (DCA/legal/payment-plan/notice
    # history/ownership-transfer provenance) have no Postgres equivalent at all and
    # always come from Mongo regardless. This is a hybrid read, not a full-response
    # swap: only the financial ledger figures (net_balance, opening_arrears,
    # total_levied) that feed the per-unit arrears/credit/interest computation below
    # change source. That computation itself (unit_arrears_and_credit, severity,
    # interest accrual) is completely unchanged -- see GAP-FIN-057/046's own repeated
    # lesson (CLAUDE.md's mandatory arrears rule) about never re-deriving this logic
    # in a second code path; only the ledger INPUT is re-sourced here.
    async def _pg_ledger_entries() -> list[dict] | None:
        """Reshape FinancialReadService.get_unit_levy_balance_list()'s already
        shadow-comparison-verified per-unit output into the same dict shape
        db.unit_levy_ledger.find() would have returned, so the per-unit loop below
        needs zero changes. quarters_charged is deliberately never set -- confirmed
        live (see the comment above num_periods in get_building_finance_overview)
        that unit_levy_ledger.quarters_charged is unset on 0/522 real documents in
        production, so the loop's `l_entry.get("quarters_charged") or 0` already
        always falls through to the total-levied-as-one-period branch today; this
        matches that real behaviour rather than fixing it as a side effect here."""
        balances = await _financial_read_service.get_unit_levy_balance_list(
            building_id=building_id, financial_year=year,
        )
        if balances is None:
            return None
        return [
            {
                "unit_number": b["unit_number"],
                "net_balance": b["closing_balance"],
                "opening_arrears": b["opening_balance"],
                "total_levied": b["levied_amount"],
            }
            for b in balances
        ]

    async def _arrears_ledger_task() -> list[dict]:
        if route_state["source"] != "postgres":
            return await db.unit_levy_ledger.find(
                {"building_id": building_id, "year": year}, {"_id": 0},
            ).to_list(100)
        try:
            pg_entries = await _pg_ledger_entries()
        except Exception as exc:
            logger.warning("finance.arrears_detail: PG ledger fetch failed, falling back to Mongo: %s", exc)
            pg_entries = None
        if pg_entries is not None:
            return pg_entries
        return await db.unit_levy_ledger.find(
            {"building_id": building_id, "year": year}, {"_id": 0},
        ).to_list(100)

    units_task = db.units.find({"building_id": building_id}, {"_id": 0}).to_list(100)
    ledger_task = _arrears_ledger_task()
    settings_task = _get_general_settings(building_id, {"_id": 0})
    building_task = db.buildings.find_one({"id": building_id}, {"_id": 0})

    # Join user_units with users to get owner details in one operation
    user_units_pipeline = [
        {"$match": {"building_id": building_id, "is_active": True}},
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "owner_info"
        }},
        {"$unwind": "$owner_info"},
        {"$project": {
            "_id": 0,
            "unit_number": 1,
            "id": "$user_id",
            "full_name": "$owner_info.full_name",
            "email": "$owner_info.email"
        }}
    ]
    user_units_task = db.user_units.aggregate(user_units_pipeline).to_list(500)

    # Aggregate confirmed payment stats
    payments_pipeline = [
        {"$match": {"building_id": building_id, "year": year, "status": "confirmed"}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$unit_number",
            "total_paid": {"$sum": "$amount"},
            "last_date": {"$first": "$created_at"},
            "method": {"$first": "$payment_method"}
        }}
    ]
    payments_task = db.levy_payments.aggregate(payments_pipeline).to_list(100)

    units, ledger_entries, user_units_with_owners, settings_doc, payment_stats, building_config = await asyncio.gather(
        units_task, ledger_task, user_units_task, settings_task, payments_task, building_task
    )

    # Effective interest rate: special resolution > building config > jurisdiction default.
    # Ceiling (rate_info["max_rate_pct"]) is jurisdiction-specific — e.g. 20% for ACT,
    # 10% for NSW/VIC (no special-resolution increase permitted), 30% for QLD.
    rate_info = await get_effective_interest_rate(building_id, db)
    interest_rate = rate_info["rate_pct"]
    interest_rate_ceiling = rate_info["max_rate_pct"]

    ledger_map = {e["unit_number"]: e for e in ledger_entries}
    # portal_payment_map: payments recorded in levy_payments (real-time Stripe/DEFT in-portal)
    portal_payment_map = {p["_id"]: p for p in payment_stats}

    # Map unit_number to its owner details for O(1) lookup
    owner_map = {uu["unit_number"]: uu for uu in user_units_with_owners}

    # Compute due dates for severity calculation.
    # Calendar-year model: levy_year=2026 means all due months are in 2026.
    # For opening_arrears (prior-year carry-forward), days_overdue counts from the
    # last grace deadline of the PRIOR levy year — e.g. for FY2026 ledger entries,
    # prior year = 2025, last grace = Dec 15 2025 → 91 days on March 16 2026.
    today = date.today()
    due_months = settings_doc.get("levy_due_months", [3, 6, 9, 12]) if settings_doc else [3, 6, 9, 12]
    due_day_type = settings_doc.get("levy_due_day_type", "first") if settings_doc else "first"
    due_day = settings_doc.get("levy_due_day") if settings_doc else None
    custom_dates = settings_doc.get("levy_due_custom_dates", {}) if settings_doc else {}
    fy_start_month_ar = int(settings_doc.get("financial_year_start_month", 1)) if settings_doc else 1

    try:
        y_int = int(year)
    except Exception:
        y_int = today.year

    grace_days = int(settings_doc.get("grace_period_days", 14)) if settings_doc else 14

    # Compute PRIOR year due dates — used for days_overdue/interest accrual only
    # (see below); no longer used to scope which amounts count as arrears.
    prior_year_dates_str = _compute_period_due_dates(
        y_int - 1, due_months, due_day_type, due_day, len(due_months), custom_dates,
        fy_start_month=fy_start_month_ar,
    )
    prior_year_due_dates = sorted([date.fromisoformat(d) for d in prior_year_dates_str])
    prior_grace_deadlines = sorted([d + timedelta(days=grace_days) for d in prior_year_due_dates])
    prior_past_grace = [g for g in prior_grace_deadlines if today > g]

    # Current year due dates — needed to exclude the still-in-grace portion of
    # the currently-charged period from arrears (same grace-aware model as
    # /finance/summary and /finance/kpi-contract).
    current_year_dates_str = _compute_period_due_dates(
        y_int, due_months, due_day_type, due_day, len(due_months), custom_dates,
        fy_start_month=fy_start_month_ar,
    )
    current_year_due_dates = [date.fromisoformat(d) for d in current_year_dates_str]
    in_grace_count = sum(
        1 for d in current_year_due_dates if d < today <= d + timedelta(days=grace_days)
    )

    # Results collection
    results = []
    for unit in units:
        unit_num = unit["unit_number"]
        l_entry = ledger_map.get(unit_num, {})

        # pmt is display metadata only (last_payment_date / method) — the arrears
        # calculation below reads unit_levy_ledger.net_balance directly and does
        # not separately re-consult total_paid/levy_payments.
        pmt = portal_payment_map.get(unit_num, {})

        admin_opening = l_entry.get("admin_opening", 0.0)
        sinking_opening = l_entry.get("sinking_opening", 0.0)

        # Informational only — no longer the arrears headline (see below). Kept
        # for the "opening_arrears" column so recovery staff can still see how
        # much of a unit's position is long-standing carry-forward.
        opening_debt = l_entry.get("opening_arrears") or round(admin_opening + sinking_opening, 2)

        # 2026-08-03: true_arrears previously showed ONLY prior-year carry-forward
        # (recoverable_arrears()/opening_debt), deliberately excluding all
        # current-year past-grace unpaid levy — the comment this replaced warned
        # against "adding periods_past_grace * period_levy" because an earlier
        # attempt inflated UA042 from $963.31 to $2,768. That earlier attempt
        # RECONSTRUCTED an obligation on top of opening_debt; this does not — it
        # trusts unit_levy_ledger.net_balance (the authoritative balance) directly
        # and only ever subtracts the still-in-grace portion of the
        # currently-charged period, via the same canonical
        # unit_arrears_and_credit() used by /finance/summary and
        # /finance/kpi-contract, so all three agree by construction. Confirmed
        # live for East Gate this does not reproduce the earlier blowup.
        net_balance = round(float(l_entry.get("net_balance", 0.0)), 2)
        net_balance_cents = round(net_balance * 100)
        quarters_charged = l_entry.get("quarters_charged") or 0
        total_levied_cents = round((l_entry.get("total_levied") or 0) * 100)
        per_period_cents = (
            round(total_levied_cents / quarters_charged) if quarters_charged > 0 else total_levied_cents
        )
        in_grace_portion_cents = min(max(net_balance_cents, 0), per_period_cents * in_grace_count)
        arrears_cents, credit_cents = unit_arrears_and_credit(
            net_balance_cents=net_balance_cents,
            in_grace_portion_cents=in_grace_portion_cents,
        )
        true_arrears = round(arrears_cents / 100, 2)

        # Current running balance as of today, before the in-grace exclusion —
        # kept alongside true_arrears for transparency (e.g. to show "$X of this
        # is still within its grace window"), never used to reduce another
        # unit's arrears.
        current_year_outstanding = round(max(0.0, net_balance), 2)
        current_year_credit = round(credit_cents / 100, 2)

        # Skip units with no actual arrears (neither prior-year carry-forward debt
        # nor current-year outstanding). A unit sitting on a CREDIT ONLY
        # (current_year_credit > 0, true_arrears == 0, current_year_outstanding == 0)
        # is not in arrears and must not appear on the Arrears Recovery Board --
        # deliberately excluded here, not just filtered client-side, so the count
        # of returned rows matches "units in arrears" everywhere this endpoint feeds.
        if true_arrears < 0.01 and current_year_outstanding < 0.01:
            continue

        # days_overdue for opening_arrears (prior-year carry-forward):
        # Count from the LAST grace deadline of the prior levy year.
        # For FY2026 with months=[3,6,9,12] and day=1:
        #   prior year = 2025, last due = Dec 1 2025, last grace = Dec 15 2025
        #   On March 16 2026: (Mar 16 - Dec 15 2025) = 91 days overdue.
        days_overdue = 0
        severity = "current"

        if prior_past_grace:
            # Count from the LAST past-grace deadline of the prior levy year
            days_overdue = (today - prior_past_grace[-1]).days

        if days_overdue > 90:
            severity = "critical"
        elif days_overdue > 60:
            severity = "serious"
        elif days_overdue > 14:
            severity = "overdue"
        else:
            severity = "current"

        owner = owner_map.get(unit_num, {})
        meta = unit.get("arrears_metadata", {})

        # has_portal_account: True if the owner is registered in the portal (user_units).
        # Units from the strata roll that haven't registered still appear on the board.
        has_portal_account = bool(owner)

        # Fallback: use owner_name from the units collection (imported from Excel) when
        # user_units has no active entry (e.g. unit not yet linked to a portal account).
        owner_name = owner.get("full_name") or unit.get("owner_name") or "Unknown"
        owner_email = owner.get("email") or unit.get("owner_email")

        # Statutory interest per UTM Act 2011 (ACT) § 96
        # Accrues from the last past-grace deadline of the prior levy year.
        overdue_since_dt = None
        if prior_past_grace:
            overdue_since_dt = datetime.combine(prior_past_grace[-1], datetime.min.time())
        accrued_interest = 0.0
        if overdue_since_dt and true_arrears > 0:
            accrued_interest = compute_accrued_interest(
                principal=true_arrears,
                overdue_since=overdue_since_dt,
                as_at=datetime.combine(today, datetime.min.time()),
                annual_rate_pct=interest_rate,
                max_rate_pct=interest_rate_ceiling,
            )

        # Inherited arrears provenance (set by _cascade_owner_change on owner transfer)
        inherited_arrears = meta.get("inherited_arrears")
        previous_owner = meta.get("previous_owner")
        transferred_at = meta.get("transferred_at")

        results.append({
            "unit_number": unit_num,
            "lot_number": unit.get("lot_number"),
            "owner_name": owner_name,
            "owner_email": owner_email,
            "has_portal_account": has_portal_account,
            "total_arrears": true_arrears,
            "opening_arrears": round(opening_debt, 2),
            "accrued_interest": accrued_interest,
            "total_owing": round(true_arrears + accrued_interest, 2),
            # Current running balance as of today (opening + this year's levied,
            # less ALL payments) -- distinct from total_arrears/total_owing above,
            # which are prior-year carry-forward only. See comment at this loop's
            # net_balance computation.
            "current_year_outstanding": current_year_outstanding,
            "current_year_credit": current_year_credit,
            "interest_rate_pct": interest_rate,
            "days_overdue": days_overdue,
            "severity": severity,
            "last_payment_date": pmt.get("last_date"),
            "payment_method_detected": pmt.get("method"),
            "dca_status": meta.get("dca_status", "none"),
            "legal_referral_status": meta.get("legal_referral_status", "none"),
            "active_payment_plan": meta.get("has_active_payment_plan", False),
            "first_notice_sent_at": meta.get("first_notice_sent_at"),
            # Ownership-transfer provenance fields
            "inherited_arrears": round(float(inherited_arrears), 2) if inherited_arrears else None,
            "previous_owner": previous_owner,
            "transferred_at": transferred_at,
        })

    results.sort(key=lambda x: x["total_arrears"], reverse=True)
    if route_state.get("run_shadow"):
        asyncio.create_task(maybe_run_finance_shadow(
            building_id=building_id,
            route_key="finance.arrears_detail",
            mongo_payload={
                # `year` is REQUIRED, not decorative: without it the PG side of the
                # comparison resolves its own default financial year while this side
                # stays on the year the caller asked for, and the comparator reports the
                # resulting cross-year gap as a critical mismatch. `year` is always
                # resolved by this point (param, else _resolve_default_levy_year).
                "year": year,
                "total_arrears": round(sum(float(r.get("total_arrears") or 0) for r in results), 2),
                "units_in_arrears": len(results),
            },
        ))
    return results


@router.post("/arrears/{unit_number}/send-notice")
async def send_arrears_notice_route(
        unit_number: str,
        year: str,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Generate and return the Arrears Notice PDF. Scoped to building."""
    from services.notice_service import generate_arrears_notice

    try:
        pdf_bytes = await generate_arrears_notice(
            unit_number, year, current_user["id"], current_user["full_name"], building_id
        )
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=Arrears_Notice_{unit_number}_{year}.pdf"}
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/arrears/{unit_number}/refer-dca", response_model=DcaReferResponse)
async def refer_to_dca(
        unit_number: str,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Mark a unit as referred to Debt Collection Agency. Scoped to building."""
    now = _now()
    dca_ref = f"DCA-{unit_number}-{datetime.now().strftime('%Y%m%d')}"

    await db.units.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {
            "$set": {
                "arrears_metadata.dca_status": "referred",
                "arrears_metadata.dca_reference": dca_ref,
                # Also set legal_referral_status so the "Legal/Plan" column on the
                # Arrears Recovery Board updates from "NO PLAN" → "LEGAL ACTION"
                "arrears_metadata.legal_referral_status": "referred",
                "updated_at": now
            },
            "$push": {
                "arrears_metadata.contact_log": {
                    "date": now,
                    "method": "dca_referral",
                    "description": f"Referred to Debt Collection Agency. Ref: {dca_ref}",
                    "performed_by": current_user["id"],
                    "performed_by_name": current_user["full_name"]
                }
            }
        }
    )

    await create_audit_log(
        action="dca_referred",
        resource_type="unit",
        resource_id=unit_number,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"dca_reference": dca_ref}
    )

    return {"success": True, "dca_reference": dca_ref}


@router.patch("/arrears/{unit_number}/dca-status", response_model=SuccessAck)
async def update_dca_status(
        unit_number: str,
        status: str,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Manual update of DCA status. Scoped to building."""
    if status not in ["none", "eligible", "referred", "recovering", "resolved", "recalled"]:
        raise HTTPException(status_code=400, detail="Invalid DCA status")

    now = _now()
    await db.units.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {
            "$set": {"arrears_metadata.dca_status": status, "updated_at": now},
            "$push": {
                "arrears_metadata.contact_log": {
                    "date": now,
                    "method": "system",
                    "description": f"DCA Status manually updated to: {status}",
                    "performed_by": current_user["id"],
                    "performed_by_name": current_user["full_name"]
                }
            }
        }
    )
    # Audit trail: manual DCA status changes must be traceable to the user who made them
    await create_audit_log(
        action="dca_status_manual_update",
        resource_type="unit",
        resource_id=unit_number,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"new_dca_status": status, "building_id": building_id}
    )
    return {"success": True}


@router.get("/arrears/{unit_number}/contact-log")
async def get_unit_contact_log(
        unit_number: str,
        current_user: dict = Depends(require_permission("can_view_finances")),
        building_id: str = Depends(get_current_building)
):
    """Get unit contact log. Scoped to building."""
    unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number},
                                   {"arrears_metadata.contact_log": 1})
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    return unit.get("arrears_metadata", {}).get("contact_log", [])


class ContactLogCreate(BaseModel):
    method: str = "email"
    description: str


@router.post("/arrears/{unit_number}/contact-log", response_model=SuccessAck)
async def add_unit_contact_log(
        unit_number: str,
        body: ContactLogCreate,
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """Add entry to unit contact log. Scoped to building."""
    now = _now()
    log_entry = {
        "date": now,
        "method": body.method,
        "description": body.description,
        "performed_by": current_user["id"],
        "performed_by_name": current_user["full_name"]
    }

    await db.units.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"$push": {"arrears_metadata.contact_log": log_entry}, "$set": {"updated_at": now}}
    )
    return {"success": True}
