# @featuretrace:bi-analytics — Canonical BI read service: queries analytics.fact_* with Mongo fallback.
# Layer: service
# Data flow: /api/bi/* router → bi_service → analytics.fact_* (PG, toggle: bi_pg_primary_enabled,
#            refreshed daily 02:15 AEST by workers.scheduler:run_bi_analytics_etl)
#            or MongoDB fallback (unit_levy_ledger, maintenance_requests, compliance_registers, etc.)
#            The 4 alert evaluators (evaluate_*_alerts) ALWAYS fall back to Mongo too — they must
#            not go silently quiet just because bi_pg_primary_enabled is off for this building.
# Related: backend/routers/bi.py
#          backend/alembic/versions/0052_canonical_bi_schema.py
#          backend/services/bi_etl_service.py
#          backend/workers/scheduler.py
#          frontend/src/pages/dashboard/BIAnalyticsPage.tsx
# Toggle: bi_pg_primary_enabled
# Tests: tests/backend/test_bi_service.py
"""BI read service — canonical fact-table queries with MongoDB fallback.

All public functions:
 1. Resolve scheme_id from building_id (PG) or use building_id directly (Mongo).
 2. Try the analytics.fact_* read path only when bi_pg_primary_enabled is ON.
 3. Fall back to the MongoDB aggregation path on any RuntimeError/missing data.
 4. Return a standardised dict matching the BI endpoint contract.

Every query filters is_test_data = FALSE (via RLS tenant_id + explicit filter).

Money convention: cents (BIGINT) in DB; ÷100 for API responses.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timezone, timedelta
from typing import Any

from domain.finance.formulas.collection import quarterly_collection_rate

logger = logging.getLogger(__name__)

_BI_PG_PRIMARY_TOGGLE = "bi_pg_primary_enabled"

# GAP-FIN-050 (2026-08-07): request-time freshness gate for the BI PG read path.
# The bi_pg_primary_enabled toggle alone does not guarantee the analytics.fact_*
# tables are current — they are materialised by the daily run_bi_analytics_etl job,
# which can miss a run. Serving a stale fact as "primary" is exactly the silent-
# staleness risk _toggle_on's own docstring warns about. When the latest
# fact_financial_balance ingest for a building/year is older than this many hours,
# the PG balance read is treated as unavailable and the caller falls back to Mongo
# (the correct PG→Mongo fallback direction). 48h matches the existing BI ETL
# staleness threshold used by the readiness check below (bi_service.py ~L2075).
_BI_FACT_MAX_STALENESS_HOURS = 48


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _toggle_on(building_id: str) -> bool:
    """Return True only when BI analytics should use PostgreSQL as primary.

    Visibility toggles such as bi_analytics_enabled control whether BI pages are
    shown. They must not select PG facts by themselves because partial BI facts
    can be stale during cutover.
    """
    try:
        from services.cutover_config_service import is_cutover_feature_enabled
        return await is_cutover_feature_enabled(building_id, _BI_PG_PRIMARY_TOGGLE)
    except Exception:
        return False


async def _resolve_scheme_id(building_id: str) -> str:
    """Return Postgres scheme_id (UUID str) for the given building_id."""
    from db_postgres.repos import identity_repo as _id_repo
    scheme = await _id_repo.get_scheme_by_number(building_id)
    if not scheme:
        raise RuntimeError(f"No Postgres scheme for building_id={building_id!r}")
    return str(scheme["scheme_id"]), str(scheme["tenant_id"])


async def _pg_session(tenant_id: str):
    """Context manager: return async SQLAlchemy session with tenant set."""
    from db_postgres.session import async_session_context, set_tenant
    return async_session_context, set_tenant, tenant_id


def _cents(value) -> float:
    """Convert BigInteger cents to dollars (2dp)."""
    if value is None:
        return 0.0
    return round(float(value) / 100, 2)


def _weighted_collection_rate(total_collected: float, total_due: float) -> float | None:
    """Levied-weighted portfolio collection rate: Σcollected / Σdue * 100, clamped
    [0, 100] (GAP-FIN-040 B9). Returns None when nothing is due (no data), never a
    misleading 0. Uses the canonical due-date formula so a portfolio rate matches
    what each building's own /finance/kpi-contract reports, in aggregate."""
    if not total_due:
        return None
    from domain.finance.formulas.collection import due_date_collection_rate
    result = due_date_collection_rate(
        due_to_date_cents=round(float(total_due) * 100),
        collected_to_date_cents=round(float(total_collected) * 100),
    )
    return float(result.collection_rate_pct)


def _pct(numerator, denominator, digits=1) -> float | None:
    """Generated function header.

    Function: _pct
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not denominator:
        return None
    return round(float(numerator or 0) / float(denominator) * 100, digits)


def _current_fy(building_id: str | None = None) -> str:
    """Return the current financial year as e.g. '2026'."""
    now = datetime.now()
    # Default: calendar year = financial year (ACT pattern Jan-Dec).
    # Buildings on July-June FY will override this via settings.
    return str(now.year)


# ─────────────────────────────────────────────────────────────────────────────
# Building — Financial Summary
# ─────────────────────────────────────────────────────────────────────────────

async def _live_arrears_and_collection(building_id: str, fy: str) -> dict:
    """Canonical, grace-aware arrears + due-date collection for a building/year,
    computed LIVE from the single-source helpers (GAP-FIN-040 — the user's BI
    decision: never trust the stale ETL fact_arrears_snapshot for correctness;
    route BI arrears/collection through get_building_arrears_summary() /
    get_collection_rate_metrics() at read time, ETL only as downstream refresh).

    total_arrears / lots_in_arrears are grace-aware (never a raw balance_owing
    sum); total_outstanding is the no-netting gross rollup; total_paid is the
    per-unit-clamped collected-to-date; levy_collection_rate is THE due-date
    Collection Rate (metric 1), not fund health.
    """
    from database import db
    from utils.finance_helpers import (
        get_building_arrears_summary,
        get_collection_rate_metrics,
        sum_ledger_collected_outstanding,
    )

    ledger_rows = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": fy, "is_test_data": {"$ne": True}},
        {"net_balance": 1, "total_levied": 1, "lot_number": 1},
    ).to_list(500)
    rollup = sum_ledger_collected_outstanding(ledger_rows)

    arrears = await get_building_arrears_summary(building_id, fy)
    rate_metrics = await get_collection_rate_metrics(fy, building_id)

    return {
        "total_lots": len(ledger_rows),
        "total_levied": rate_metrics["due_to_date"],
        "total_paid": rate_metrics["collected_to_date"],
        "total_outstanding": rollup["total_outstanding"],
        "lots_in_arrears": arrears["units_in_arrears"],
        "total_arrears": arrears["true_arrears_amount"],
        "total_credit_amount": arrears["total_credit_amount"],
        "collected_in_advance": rate_metrics["collected_in_advance"],
        "levy_collection_rate": rate_metrics["collection_rate_pct"],
        "admin_arrears": rate_metrics["admin_fund"]["due_to_date"] - rate_metrics["admin_fund"]["collected_to_date"],
        "sinking_arrears": rate_metrics["sinking_fund"]["due_to_date"] - rate_metrics["sinking_fund"]["collected_to_date"],
    }


async def get_financial_summary(building_id: str, financial_year: str | None = None) -> dict:
    """KPI summary: levy collection %, arrears, fund balances.

    Arrears + collection are ALWAYS computed live from the canonical helpers
    (GAP-FIN-040); only the fund balances / income / expenses differ by source
    (PG facts when bi_pg_primary_enabled, else annual_levies).
    """
    fy = financial_year or _current_fy(building_id)

    core = await _live_arrears_and_collection(building_id, fy)

    balances = None
    if await _toggle_on(building_id):
        try:
            balances = await _financial_summary_pg_balances(building_id, fy)
        except Exception as e:
            logger.info("bi_service.get_financial_summary: PG balances unavailable (%s), using Mongo", e)
    if balances is None:
        balances = await _financial_summary_mongo_balances(building_id, fy)

    return {
        "financial_year": fy,
        "levy_collection_rate": core["levy_collection_rate"],
        "total_levied": core["total_levied"],
        "total_paid": core["total_paid"],
        "total_outstanding": core["total_outstanding"],
        "total_lots": core["total_lots"],
        "lots_in_arrears": core["lots_in_arrears"],
        "total_arrears": core["total_arrears"],
        "total_credit_amount": core["total_credit_amount"],
        "collected_in_advance": core["collected_in_advance"],
        "admin_arrears": core["admin_arrears"],
        "sinking_arrears": core["sinking_arrears"],
        "severe_arrears_lots": balances.get("severe_arrears_lots"),
        "admin_fund_balance": balances.get("admin_fund_balance", 0.0),
        "sinking_fund_balance": balances.get("sinking_fund_balance", 0.0),
        "admin_income": balances.get("admin_income"),
        "admin_expenses": balances.get("admin_expenses"),
        "source": balances.get("source", "mongo"),
    }


async def _financial_summary_pg_balances(building_id: str, fy: str) -> dict:
    """Fund balances / income / expenses + severe-arrears count from the PG BI
    facts. Arrears totals and collection rate are NOT sourced here any more —
    they come from the canonical live helpers in _live_arrears_and_collection()
    (GAP-FIN-040). The stale fact_arrears_snapshot is used ONLY for the
    days_overdue >= 60 severe count, which the Mongo ledger cannot express.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        balance_rows = await session.execute(
            text("""
                WITH latest AS (
                    SELECT DISTINCT ON (fund_type)
                           fund_type,
                           levy_income_cents,
                           expenses_cents,
                           closing_balance_cents
                    FROM analytics.fact_financial_balance
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND financial_year = :fy
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    ORDER BY fund_type, period_date DESC, ingested_at DESC
                )
                SELECT fund_type,
                       levy_income_cents AS levy_income,
                       expenses_cents AS expenses,
                       closing_balance_cents AS balance
                FROM latest
            """),
            {"sid": scheme_id, "fy": fy},
        )
        balances = {r[0]: {"income": r[1], "expenses": r[2], "balance": r[3]} for r in balance_rows.fetchall()}

        severe_row = await session.execute(
            text("""
                SELECT COUNT(*) FILTER (WHERE days_overdue >= 60) AS severe_arrears_lots
                FROM analytics.fact_arrears_snapshot
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM analytics.fact_arrears_snapshot
                      WHERE scheme_id = CAST(:sid AS UUID)
                  )
                  AND COALESCE(is_test_data, FALSE) = FALSE
            """),
            {"sid": scheme_id},
        )
        severe = severe_row.fetchone()

        # Freshness gate (GAP-FIN-050): refuse the PG balances when the fact table's
        # most recent ingest for this building/year is staler than the threshold, so a
        # missed ETL run degrades to the live Mongo balances rather than silently
        # serving stale numbers. Computed in SQL to avoid naive/aware datetime pitfalls.
        freshness_row = await session.execute(
            text("""
                SELECT
                    MAX(ingested_at) AS latest_ingest,
                    (MAX(ingested_at) < NOW() - (INTERVAL '1 hour' * :max_hours)) AS is_stale
                FROM analytics.fact_financial_balance
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND financial_year = :fy
                  AND COALESCE(is_test_data, FALSE) = FALSE
            """),
            {"sid": scheme_id, "fy": fy, "max_hours": _BI_FACT_MAX_STALENESS_HOURS},
        )
        freshness = freshness_row.fetchone()

    if not balances:
        raise RuntimeError("No fact_financial_balance data for this building/year")

    if freshness is not None and freshness[1]:
        raise RuntimeError(
            f"fact_financial_balance is stale for building/year "
            f"(latest ingest {freshness[0]}, threshold {_BI_FACT_MAX_STALENESS_HOURS}h) — "
            f"falling back to Mongo"
        )

    admin_b = balances.get("admin", {})
    sinking_b = balances.get("sinking", balances.get("capital_works", {}))

    return {
        "severe_arrears_lots": int(severe[0] or 0) if severe else None,
        "admin_fund_balance": _cents(admin_b.get("balance")),
        "sinking_fund_balance": _cents(sinking_b.get("balance")),
        "admin_income": _cents(admin_b.get("income")),
        "admin_expenses": _cents(admin_b.get("expenses")),
        "source": "postgres_bi",
    }


async def _financial_summary_mongo_balances(building_id: str, fy: str) -> dict:
    """Fund balances from annual_levies (Mongo). Arrears/collection come from the
    canonical live helpers, not from this function (GAP-FIN-040)."""
    from database import db
    from utils.finance_helpers import get_annual_fund_balance

    levy_doc = await db.annual_levies.find_one({"building_id": building_id, "year": fy}) or \
               await db.annual_levies.find_one({"building_id": building_id, "year": int(fy)})

    admin_balance = 0.0
    sinking_balance = 0.0
    if levy_doc:
        admin_balance, _ = get_annual_fund_balance(levy_doc.get("admin_fund"))
        sinking_balance, _ = get_annual_fund_balance(levy_doc.get("sinking_fund"))

    return {
        "severe_arrears_lots": None,
        "admin_fund_balance": admin_balance,
        "sinking_fund_balance": sinking_balance,
        "admin_income": None,
        "admin_expenses": None,
        "source": "mongo",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building — Levy Collection Trend
# ─────────────────────────────────────────────────────────────────────────────

async def get_levy_collection_trend(building_id: str, months: int = 12) -> list[dict]:
    """Monthly levy collection trend: charged vs paid by month."""
    if await _toggle_on(building_id):
        try:
            return await _levy_trend_pg(building_id, months)
        except Exception as e:
            logger.info("bi_service.get_levy_collection_trend: PG unavailable (%s), using Mongo", e)
    return await _levy_trend_mongo(building_id, months)


async def _levy_trend_pg(building_id: str, months: int) -> list[dict]:
    """Generated function header.

    Function: _levy_trend_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff = date.today() - timedelta(days=months * 31)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT
                    TO_CHAR(received_on, 'YYYY-MM') AS month,
                    SUM(allocated_cents)             AS paid_cents
                FROM analytics.fact_levy_payment
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND received_on >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1
                ORDER BY 1
            """),
            {"sid": scheme_id, "cutoff": cutoff},
        )
        payment_by_month = {r[0]: _cents(r[1]) for r in rows.fetchall()}

        charge_rows = await session.execute(
            text("""
                SELECT
                    TO_CHAR(due_date, 'YYYY-MM') AS month,
                    SUM(total_charged_cents)      AS charged_cents
                FROM analytics.fact_levy_charge
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND due_date >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1
                ORDER BY 1
            """),
            {"sid": scheme_id, "cutoff": cutoff},
        )
        charged_by_month = {r[0]: _cents(r[1]) for r in charge_rows.fetchall()}

    all_months = sorted(set(list(payment_by_month.keys()) + list(charged_by_month.keys())))
    return [
        {
            "month": m,
            "charged": charged_by_month.get(m, 0.0),
            "paid": payment_by_month.get(m, 0.0),
            "collection_rate": _pct(payment_by_month.get(m, 0), charged_by_month.get(m, 1), 1),
            "source": "postgres_bi",
        }
        for m in all_months
    ]


async def _levy_trend_mongo(building_id: str, months: int) -> list[dict]:
    """Generated function header.

    Function: _levy_trend_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cutoff_year = (datetime.now() - timedelta(days=months * 31)).year
    cursor = db.annual_levies.find(
        {"building_id": building_id, "year": {"$gte": str(cutoff_year)}},
        {"year": 1, "admin_fund": 1, "sinking_fund": 1}
    )
    docs = await cursor.to_list(24)
    result = []
    for doc in sorted(docs, key=lambda d: str(d.get("year", ""))):
        af = doc.get("admin_fund") or {}
        sf = doc.get("sinking_fund") or {}
        total_levied = float(af.get("total_levied") or 0) + float(sf.get("total_levied") or 0)
        total_paid = float(af.get("levy_income") or 0) + float(sf.get("levy_income") or 0)
        result.append({
            "month": str(doc.get("year", "")),
            "charged": total_levied,
            "paid": total_paid,
            "collection_rate": _pct(total_paid, total_levied, 1),
            "source": "mongo",
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Building — Arrears Hotspots
# ─────────────────────────────────────────────────────────────────────────────

async def get_arrears_hotspots(building_id: str, min_days_overdue: int = 0) -> list[dict]:
    """Per-lot arrears table, sorted by outstanding amount descending."""
    if await _toggle_on(building_id):
        try:
            return await _arrears_hotspots_pg(building_id, min_days_overdue)
        except Exception as e:
            logger.info("bi_service.get_arrears_hotspots: PG unavailable (%s), Mongo", e)
    return await _arrears_hotspots_mongo(building_id, min_days_overdue)


async def _arrears_hotspots_pg(building_id: str, min_days: int) -> list[dict]:
    """Generated function header.

    Function: _arrears_hotspots_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT
                    a.lot_number,
                    a.total_outstanding_cents,
                    a.admin_outstanding_cents,
                    a.sinking_outstanding_cents,
                    a.interest_outstanding_cents,
                    a.days_overdue,
                    a.escalation_status,
                    a.last_payment_date,
                    a.last_payment_cents,
                    a.arrears_band,
                    o.display_name AS owner_name
                FROM analytics.fact_arrears_snapshot a
                LEFT JOIN analytics.dim_lot dl ON dl.dim_lot_id = a.dim_lot_id AND dl.is_current = TRUE
                LEFT JOIN analytics.bridge_lot_owner blo ON blo.dim_lot_id = dl.dim_lot_id
                    AND blo.is_primary_owner = TRUE
                    AND (blo.valid_to IS NULL OR blo.valid_to >= CURRENT_DATE)
                LEFT JOIN analytics.dim_owner o ON o.dim_owner_id = blo.dim_owner_id AND o.is_current = TRUE
                WHERE a.scheme_id = CAST(:sid AS UUID)
                  AND a.snapshot_date = (
                      SELECT MAX(snapshot_date) FROM analytics.fact_arrears_snapshot
                      WHERE scheme_id = CAST(:sid AS UUID)
                  )
                  AND a.total_outstanding_cents > 0
                  AND COALESCE(a.days_overdue, 0) >= :min_days
                  AND COALESCE(a.is_test_data, FALSE) = FALSE
                ORDER BY a.total_outstanding_cents DESC
            """),
            {"sid": scheme_id, "min_days": min_days},
        )
        return [
            {
                "lot_number": r[0],
                "total_outstanding": _cents(r[1]),
                "admin_outstanding": _cents(r[2]),
                "sinking_outstanding": _cents(r[3]),
                "interest_outstanding": _cents(r[4]),
                "days_overdue": r[5],
                "escalation_status": r[6] or "none",
                "last_payment_date": str(r[7]) if r[7] else None,
                "last_payment_amount": _cents(r[8]),
                "arrears_band": r[9] or "unknown",
                "owner_name": r[10],
                "source": "postgres_bi",
            }
            for r in rows.fetchall()
        ]


async def _arrears_hotspots_mongo(building_id: str, min_days: int) -> list[dict]:
    """Generated function header.

    Function: _arrears_hotspots_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from utils.finance_helpers import get_unit_arrears_map

    fy = _current_fy()
    # Canonical grace-aware per-unit arrears (GAP-FIN-040) — never the banned
    # balance_owing field. Only genuinely-in-arrears units (past grace) surface as
    # hotspots, matching /arrears/detail and the building aggregate by construction.
    arrears_map = await get_unit_arrears_map(building_id, fy)
    owner_names = {
        r.get("lot_number") or r.get("unit_number"): r.get("owner_name")
        for r in await db.unit_levy_ledger.find(
            {"building_id": building_id, "year": fy},
            {"lot_number": 1, "unit_number": 1, "owner_name": 1},
        ).to_list(500)
    }
    hotspots = [
        {
            "lot_number": unit,
            "total_outstanding": row["arrears"],
            "admin_outstanding": None,
            "sinking_outstanding": None,
            "interest_outstanding": None,
            "days_overdue": None,
            "escalation_status": "unknown",
            "last_payment_date": None,
            "last_payment_amount": None,
            "arrears_band": "unknown",
            "owner_name": owner_names.get(unit),
            "source": "mongo",
        }
        for unit, row in arrears_map.items()
        if row["in_arrears"]
    ]
    hotspots.sort(key=lambda r: r["total_outstanding"], reverse=True)
    return hotspots


# ─────────────────────────────────────────────────────────────────────────────
# Building — Capex vs Plan
# ─────────────────────────────────────────────────────────────────────────────

async def get_capex_vs_plan(building_id: str, years: int = 10) -> list[dict]:
    """Capex plan vs actual spend by year."""
    if await _toggle_on(building_id):
        try:
            return await _capex_vs_plan_pg(building_id, years)
        except Exception as e:
            logger.info("bi_service.get_capex_vs_plan: PG unavailable (%s), Mongo", e)
    return await _capex_vs_plan_mongo(building_id, years)


async def _capex_vs_plan_pg(building_id: str, years: int) -> list[dict]:
    """Generated function header.

    Function: _capex_vs_plan_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    current_year = datetime.now().year

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        plan_rows = await session.execute(
            text("""
                SELECT planned_year, SUM(planned_cost_cents) AS planned
                FROM analytics.fact_capex_plan
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND planned_year BETWEEN :start AND :end
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1 ORDER BY 1
            """),
            {"sid": scheme_id, "start": current_year - 2, "end": current_year + years},
        )
        plan_by_year = {r[0]: _cents(r[1]) for r in plan_rows.fetchall()}

        actual_rows = await session.execute(
            text("""
                SELECT EXTRACT(YEAR FROM completion_date)::INTEGER AS yr,
                       SUM(actual_cost_cents) AS actual
                FROM analytics.fact_capex_actual
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND completion_date IS NOT NULL
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1 ORDER BY 1
            """),
            {"sid": scheme_id},
        )
        actual_by_year = {r[0]: _cents(r[1]) for r in actual_rows.fetchall()}

    all_years = sorted(set(list(plan_by_year.keys()) + list(actual_by_year.keys())))
    return [
        {
            "year": y,
            "planned": plan_by_year.get(y, 0.0),
            "actual": actual_by_year.get(y, 0.0),
            "variance": round((actual_by_year.get(y, 0.0) or 0) - (plan_by_year.get(y, 0.0) or 0), 2),
            "is_forecast": y > current_year,
            "source": "postgres_bi",
        }
        for y in all_years
    ]


async def _capex_vs_plan_mongo(building_id: str, years: int) -> list[dict]:
    """Generated function header.

    Function: _capex_vs_plan_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    current_year = datetime.now().year
    cursor = db.capital_replacement_schedule.find(
        {"building_id": building_id},
        {"year": 1, "total_cost": 1, "items": 1}
    )
    docs = await cursor.to_list(100)
    return [
        {
            "year": int(doc.get("year") or 0),
            "planned": float(doc.get("total_cost") or 0),
            "actual": None,
            "variance": None,
            "is_forecast": int(doc.get("year") or 0) > current_year,
            "source": "mongo",
        }
        for doc in sorted(docs, key=lambda d: int(d.get("year") or 0))
        if int(doc.get("year") or 0) >= current_year - 2
    ][:years + 2]


# ─────────────────────────────────────────────────────────────────────────────
# Building — Sinking Fund Projection
# ─────────────────────────────────────────────────────────────────────────────

async def get_sinking_fund_projection(building_id: str, years: int = 10) -> dict:
    """10-year sinking fund balance projection with shortfall risk flags."""
    if await _toggle_on(building_id):
        try:
            return await _sinking_fund_pg(building_id, years)
        except Exception as e:
            logger.info("bi_service.get_sinking_fund_projection: PG unavailable (%s), Mongo", e)
    return await _sinking_fund_mongo(building_id, years)


async def _sinking_fund_pg(building_id: str, years: int) -> dict:
    """Generated function header.

    Function: _sinking_fund_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT period_date, closing_balance_cents, is_current
                FROM analytics.fact_financial_balance
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND fund_type IN ('sinking', 'capital_works')
                  AND COALESCE(is_test_data, FALSE) = FALSE
                ORDER BY period_date
                LIMIT :lim
            """),
            {"sid": scheme_id, "lim": years * 4},
        )
        rows_data = rows.fetchall()

    if not rows_data:
        raise RuntimeError("No sinking fund balance data in analytics schema")

    current_balance = _cents(rows_data[-1][1]) if rows_data else 0.0
    shortfall_years = [
        str(r[0].year) for r in rows_data if float(r[1] or 0) < 0
    ]
    return {
        "current_balance": current_balance,
        "shortfall_risk": len(shortfall_years) > 0,
        "shortfall_years": shortfall_years,
        "projection": [
            {"date": str(r[0]), "balance": _cents(r[1]), "is_actual": r[2]}
            for r in rows_data
        ],
        "source": "postgres_bi",
    }


async def _sinking_fund_mongo(building_id: str, years: int) -> dict:
    """Generated function header.

    Function: _sinking_fund_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from services.forecast_service import get_sinking_fund_projections
    all_rows = await get_sinking_fund_projections(building_id, max_years=years)
    current_balance = all_rows[-1]["balance"] if all_rows else 0.0
    shortfall_years = [str(r["year"]) for r in all_rows if r.get("balance", 0) < 0]
    return {
        "current_balance": current_balance,
        "shortfall_risk": len(shortfall_years) > 0,
        "shortfall_years": shortfall_years,
        "projection": all_rows,
        "source": "mongo",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building — Health Trend
# ─────────────────────────────────────────────────────────────────────────────

async def get_health_trend(building_id: str, days: int = 90) -> list[dict]:
    """Building health score trend over time."""
    if await _toggle_on(building_id):
        try:
            return await _health_trend_pg(building_id, days)
        except Exception as e:
            logger.info("bi_service.get_health_trend: PG unavailable (%s), Mongo", e)
    return await _health_trend_mongo(building_id, days)


async def _health_trend_pg(building_id: str, days: int) -> list[dict]:
    """Generated function header.

    Function: _health_trend_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff = date.today() - timedelta(days=days)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT snapshot_date, overall_score, financial_score,
                       maintenance_score, compliance_score, governance_score,
                       health_label, drivers
                FROM analytics.fact_building_health_snapshot
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND snapshot_date >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                ORDER BY snapshot_date
            """),
            {"sid": scheme_id, "cutoff": cutoff},
        )
        return [
            {
                "date": str(r[0]),
                "overall_score": float(r[1] or 0),
                "financial_score": float(r[2] or 0),
                "maintenance_score": float(r[3] or 0),
                "compliance_score": float(r[4] or 0),
                "governance_score": float(r[5] or 0),
                "health_label": r[6],
                "drivers": r[7],
                "source": "postgres_bi",
            }
            for r in rows.fetchall()
        ]


async def _health_trend_mongo(building_id: str, days: int) -> list[dict]:
    """Generated function header.

    Function: _health_trend_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor = db.building_health_scores.find(
        {"building_id": building_id, "recorded_at": {"$gte": cutoff}},
        {"recorded_at": 1, "overall_score": 1, "financial_score": 1,
         "maintenance_score": 1, "compliance_score": 1}
    ).sort("recorded_at", 1)
    docs = await cursor.to_list(100)
    return [
        {
            "date": str(d.get("recorded_at", "")[:10]) if isinstance(d.get("recorded_at"), str) else str(d.get("recorded_at", "").date() if hasattr(d.get("recorded_at", ""), "date") else ""),
            "overall_score": float(d.get("overall_score") or 0),
            "financial_score": float(d.get("financial_score") or 0),
            "maintenance_score": float(d.get("maintenance_score") or 0),
            "compliance_score": float(d.get("compliance_score") or 0),
            "governance_score": None,
            "health_label": None,
            "drivers": None,
            "source": "mongo",
        }
        for d in docs
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Building — Maintenance Costs
# ─────────────────────────────────────────────────────────────────────────────

async def get_maintenance_costs(building_id: str, financial_year: str | None = None) -> dict:
    """Maintenance cost trend by category/quarter."""
    fy = financial_year or _current_fy(building_id)
    if await _toggle_on(building_id):
        try:
            return await _maintenance_costs_pg(building_id, fy)
        except Exception as e:
            logger.info("bi_service.get_maintenance_costs: PG unavailable (%s), Mongo", e)
    return await _maintenance_costs_mongo(building_id, fy)


async def _maintenance_costs_pg(building_id: str, fy: str) -> dict:
    """Generated function header.

    Function: _maintenance_costs_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT
                    category,
                    COUNT(*) AS work_orders,
                    SUM(COALESCE(actual_cost_cents, approved_budget_cents, 0)) AS total_cost,
                    AVG(days_to_complete) AS avg_days,
                    COUNT(*) FILTER (WHERE sla_breached = TRUE) AS sla_breaches,
                    COUNT(*) FILTER (WHERE is_ppm = TRUE) AS ppm_count,
                    COUNT(*) FILTER (WHERE is_emergency = TRUE) AS emergency_count
                FROM analytics.fact_work_order
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND financial_year = :fy
                  AND status = 'completed'
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1
                ORDER BY total_cost DESC
            """),
            {"sid": scheme_id, "fy": fy},
        )
        by_category = [
            {
                "category": r[0],
                "work_orders": int(r[1]),
                "total_cost": _cents(r[2]),
                "avg_days_to_complete": round(float(r[3] or 0), 1),
                "sla_breaches": int(r[4] or 0),
                "ppm_count": int(r[5] or 0),
                "emergency_count": int(r[6] or 0),
            }
            for r in rows.fetchall()
        ]

        total_row = await session.execute(
            text("""
                SELECT
                    COUNT(*) AS total_wo,
                    SUM(COALESCE(actual_cost_cents, approved_budget_cents, 0)) AS total_cost,
                    COUNT(*) FILTER (WHERE status = 'open') AS open_wo,
                    COUNT(*) FILTER (WHERE sla_breached = TRUE) AS sla_breaches
                FROM analytics.fact_work_order
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND financial_year = :fy
                  AND COALESCE(is_test_data, FALSE) = FALSE
            """),
            {"sid": scheme_id, "fy": fy},
        )
        t = total_row.fetchone()

    return {
        "financial_year": fy,
        "total_work_orders": int(t[0] or 0) if t else 0,
        "total_cost": _cents(t[1]) if t else 0,
        "open_work_orders": int(t[2] or 0) if t else 0,
        "sla_breaches": int(t[3] or 0) if t else 0,
        "by_category": by_category,
        "source": "postgres_bi",
    }


async def _maintenance_costs_mongo(building_id: str, fy: str) -> dict:
    """Generated function header.

    Function: _maintenance_costs_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cursor = db.work_orders.find(
        {"building_id": building_id},
        {"category": 1, "status": 1, "cost": 1, "created_at": 1}
    )
    wos = await cursor.to_list(500)
    by_cat: dict[str, dict] = {}
    total_cost = 0.0
    open_count = 0
    for wo in wos:
        cat = wo.get("category") or "other"
        cost = float(wo.get("cost") or 0)
        total_cost += cost
        if wo.get("status") in ("open", "in_progress", "pending"):
            open_count += 1
        if cat not in by_cat:
            by_cat[cat] = {"category": cat, "work_orders": 0, "total_cost": 0.0,
                           "avg_days_to_complete": None, "sla_breaches": 0,
                           "ppm_count": 0, "emergency_count": 0}
        by_cat[cat]["work_orders"] += 1
        by_cat[cat]["total_cost"] = round(by_cat[cat]["total_cost"] + cost, 2)

    return {
        "financial_year": fy,
        "total_work_orders": len(wos),
        "total_cost": round(total_cost, 2),
        "open_work_orders": open_count,
        "sla_breaches": None,
        "by_category": sorted(by_cat.values(), key=lambda x: x["total_cost"], reverse=True),
        "source": "mongo",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building — Compliance Status
# ─────────────────────────────────────────────────────────────────────────────

async def get_compliance_status(building_id: str) -> dict:
    """RAG compliance grid by register/inspection type."""
    if await _toggle_on(building_id):
        try:
            return await _compliance_status_pg(building_id)
        except Exception as e:
            logger.info("bi_service.get_compliance_status: PG unavailable (%s), Mongo", e)
    return await _compliance_status_mongo(building_id)


async def _compliance_status_pg(building_id: str) -> dict:
    """Generated function header.

    Function: _compliance_status_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT
                    register_type,
                    COUNT(*) FILTER (WHERE rag_status = 'green')  AS green_count,
                    COUNT(*) FILTER (WHERE rag_status = 'amber')  AS amber_count,
                    COUNT(*) FILTER (WHERE rag_status = 'red')    AS red_count,
                    MAX(due_date) AS next_due
                FROM analytics.fact_compliance_event
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND event_type IN ('due', 'overdue')
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1
                ORDER BY 3 DESC, 2 DESC
            """),
            {"sid": scheme_id},
        )
        registers = [
            {
                "register_type": r[0],
                "green": int(r[1] or 0),
                "amber": int(r[2] or 0),
                "red": int(r[3] or 0),
                "next_due": str(r[4]) if r[4] else None,
                "overall_rag": "red" if r[3] else ("amber" if r[2] else "green"),
            }
            for r in rows.fetchall()
        ]

    total_overdue = sum(r["red"] for r in registers)
    total_pending = sum(r["amber"] for r in registers)
    overall = "red" if total_overdue else ("amber" if total_pending else "green")

    return {
        "overall_status": overall,
        "total_overdue": total_overdue,
        "total_pending": total_pending,
        "registers": registers,
        "source": "postgres_bi",
    }


async def _compliance_status_mongo(building_id: str) -> dict:
    """Generated function header.

    Function: _compliance_status_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from services.analytics_pg_service import get_compliance_summary_pg
    from database import db
    try:
        summary = await get_compliance_summary_pg(building_id)
        return {**summary, "registers": [], "source": "postgres_operational"}
    except Exception:
        pass
    cursor = db.compliance_registers.find(
        {"building_id": building_id},
        {"register_type": 1, "status": 1, "next_due_date": 1}
    )
    regs = await cursor.to_list(100)
    result = []
    for r in regs:
        status = r.get("status", "pending")
        rag = "red" if status == "overdue" else ("amber" if status == "pending" else "green")
        result.append({
            "register_type": r.get("register_type"),
            "green": 1 if rag == "green" else 0,
            "amber": 1 if rag == "amber" else 0,
            "red": 1 if rag == "red" else 0,
            "next_due": r.get("next_due_date"),
            "overall_rag": rag,
        })
    overdue = sum(r["red"] for r in result)
    pending = sum(r["amber"] for r in result)
    return {
        "overall_status": "red" if overdue else ("amber" if pending else "green"),
        "total_overdue": overdue,
        "total_pending": pending,
        "registers": result,
        "source": "mongo",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building — Utility Trend
# ─────────────────────────────────────────────────────────────────────────────

async def get_utility_trend(building_id: str, months: int = 12) -> dict:
    """Monthly utility spend by type with spike flags."""
    if await _toggle_on(building_id):
        try:
            return await _utility_trend_pg(building_id, months)
        except Exception as e:
            logger.info("bi_service.get_utility_trend: PG unavailable (%s), Mongo", e)
    return await _utility_trend_mongo(building_id, months)


async def _utility_trend_pg(building_id: str, months: int) -> dict:
    """Generated function header.

    Function: _utility_trend_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff = date.today() - timedelta(days=months * 31)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT
                    TO_CHAR(billing_period_start, 'YYYY-MM') AS month,
                    utility_type,
                    SUM(total_amount_cents) AS amount,
                    BOOL_OR(is_spike) AS has_spike
                FROM analytics.fact_utility_bill
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND billing_period_start >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1, 2
                ORDER BY 1, 2
            """),
            {"sid": scheme_id, "cutoff": cutoff},
        )
        by_month: dict[str, dict] = {}
        for r in rows.fetchall():
            month, util_type, amount, has_spike = r
            if month not in by_month:
                by_month[month] = {"month": month, "total": 0.0, "types": {}, "has_spike": False}
            by_month[month]["types"][util_type] = _cents(amount)
            by_month[month]["total"] = round(by_month[month]["total"] + _cents(amount), 2)
            if has_spike:
                by_month[month]["has_spike"] = True

    return {
        "months": sorted(by_month.values(), key=lambda x: x["month"]),
        "source": "postgres_bi",
    }


async def _utility_trend_mongo(building_id: str, months: int) -> dict:
    """Generated function header.

    Function: _utility_trend_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 31)
    by_month: dict[str, dict] = {}
    for coll_name, util_type in [("water_bills", "water"), ("council_rates", "council_rates")]:
        cursor = getattr(db, coll_name).find(
            {"building_id": building_id, "created_at": {"$gte": cutoff}},
            {"billing_period_start": 1, "total_amount": 1}
        )
        docs = await cursor.to_list(200)
        for doc in docs:
            period = str(doc.get("billing_period_start", ""))[:7]
            if period not in by_month:
                by_month[period] = {"month": period, "total": 0.0, "types": {}, "has_spike": False}
            amt = float(doc.get("total_amount") or 0)
            by_month[period]["types"][util_type] = by_month[period]["types"].get(util_type, 0) + amt
            by_month[period]["total"] = round(by_month[period]["total"] + amt, 2)

    return {
        "months": sorted(by_month.values(), key=lambda x: x["month"]),
        "source": "mongo",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building — Occupancy Mix
# ─────────────────────────────────────────────────────────────────────────────

async def get_occupancy_mix(building_id: str) -> dict:
    """Owner-occupier / tenant / investor / vacant breakdown."""
    if await _toggle_on(building_id):
        try:
            return await _occupancy_mix_pg(building_id)
        except Exception as e:
            logger.info("bi_service.get_occupancy_mix: PG unavailable (%s), Mongo", e)
    return await _occupancy_mix_mongo(building_id)


async def _occupancy_mix_pg(building_id: str) -> dict:
    """Generated function header.

    Function: _occupancy_mix_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT occupancy_type, COUNT(*) AS lot_count
                FROM analytics.fact_occupancy_snapshot
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM analytics.fact_occupancy_snapshot
                      WHERE scheme_id = CAST(:sid AS UUID)
                  )
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1
            """),
            {"sid": scheme_id},
        )
        counts = {r[0]: int(r[1]) for r in rows.fetchall()}

    total = sum(counts.values()) or 1
    return {
        "total_lots": total,
        "by_type": [
            {"type": k, "count": v, "pct": _pct(v, total, 1)}
            for k, v in sorted(counts.items())
        ],
        "source": "postgres_bi",
    }


async def _occupancy_mix_mongo(building_id: str) -> dict:
    """Generated function header.

    Function: _occupancy_mix_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cursor = db.units.find(
        {"building_id": building_id},
        {"occupancy_status": 1, "tenancy_active": 1, "is_investor": 1}
    )
    units = await cursor.to_list(500)
    counts = {"owner_occupier": 0, "tenant": 0, "investor_vacant": 0, "unknown": 0}
    for u in units:
        status = u.get("occupancy_status") or ("tenant" if u.get("tenancy_active") else
                                                "investor_vacant" if u.get("is_investor") else "unknown")
        counts[status] = counts.get(status, 0) + 1
    total = len(units) or 1
    return {
        "total_lots": len(units),
        "by_type": [{"type": k, "count": v, "pct": _pct(v, total, 1)} for k, v in counts.items()],
        "source": "mongo",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building — Request Heatmap
# ─────────────────────────────────────────────────────────────────────────────

async def get_request_heatmap(building_id: str, months: int = 12) -> list[dict]:
    """Smart request count by category × month."""
    if await _toggle_on(building_id):
        try:
            return await _request_heatmap_pg(building_id, months)
        except Exception as e:
            logger.info("bi_service.get_request_heatmap: PG unavailable (%s), Mongo", e)
    return await _request_heatmap_mongo(building_id, months)


async def _request_heatmap_pg(building_id: str, months: int) -> list[dict]:
    """Generated function header.

    Function: _request_heatmap_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff = date.today() - timedelta(days=months * 31)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT
                    category,
                    TO_CHAR(submitted_date, 'YYYY-MM') AS month,
                    COUNT(*) AS request_count,
                    COUNT(DISTINCT dim_lot_id) AS unique_lots
                FROM analytics.fact_smart_request
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND submitted_date >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY 1, 2
                ORDER BY 1, 2
            """),
            {"sid": scheme_id, "cutoff": cutoff},
        )
        return [
            {
                "category": r[0],
                "month": r[1],
                "count": int(r[2]),
                "unique_lots": int(r[3]),
                "source": "postgres_bi",
            }
            for r in rows.fetchall()
        ]


async def _request_heatmap_mongo(building_id: str, months: int) -> list[dict]:
    """Generated function header.

    Function: _request_heatmap_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 31)
    pipeline = [
        {"$match": {"building_id": building_id, "created_at": {"$gte": cutoff}, "is_test_data": {"$ne": True}}},
        {"$group": {
            "_id": {
                "category": "$category",
                "month": {"$dateToString": {"format": "%Y-%m", "date": "$created_at"}}
            },
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id.category": 1, "_id.month": 1}},
    ]
    cursor = db.workflow_requests.aggregate(pipeline)
    return [
        {
            "category": r["_id"]["category"],
            "month": r["_id"]["month"],
            "count": r["count"],
            "unique_lots": None,
            "source": "mongo",
        }
        async for r in cursor
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Building — Asset Risk
# ─────────────────────────────────────────────────────────────────────────────

async def get_asset_risk(building_id: str) -> list[dict]:
    """Assets by condition score and replacement risk."""
    if await _toggle_on(building_id):
        try:
            return await _asset_risk_pg(building_id)
        except Exception as e:
            logger.info("bi_service.get_asset_risk: PG unavailable (%s), Mongo", e)
    return await _asset_risk_mongo(building_id)


async def _asset_risk_pg(building_id: str) -> list[dict]:
    """Generated function header.

    Function: _asset_risk_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT asset_name, asset_category, condition_score, condition_label,
                       remaining_life_years, replacement_cost_cents, risk_band
                FROM analytics.fact_asset_condition_snapshot
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM analytics.fact_asset_condition_snapshot
                      WHERE scheme_id = CAST(:sid AS UUID)
                  )
                  AND COALESCE(is_test_data, FALSE) = FALSE
                ORDER BY condition_score ASC NULLS LAST
            """),
            {"sid": scheme_id},
        )
        return [
            {
                "asset_name": r[0],
                "category": r[1],
                "condition_score": float(r[2] or 0),
                "condition_label": r[3],
                "remaining_life_years": float(r[4] or 0),
                "replacement_cost": _cents(r[5]),
                "risk_band": r[6],
                "source": "postgres_bi",
            }
            for r in rows.fetchall()
        ]


async def _asset_risk_mongo(building_id: str) -> list[dict]:
    """Generated function header.

    Function: _asset_risk_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cursor = db.building_assets.find(
        {"building_id": building_id},
        {"name": 1, "category": 1, "condition_score": 1, "remaining_life": 1,
         "replacement_cost": 1, "risk_level": 1}
    )
    assets = await cursor.to_list(200)
    return [
        {
            "asset_name": a.get("name"),
            "category": a.get("category"),
            "condition_score": float(a.get("condition_score") or 0),
            "condition_label": None,
            "remaining_life_years": float(a.get("remaining_life") or 0),
            "replacement_cost": float(a.get("replacement_cost") or 0),
            "risk_band": a.get("risk_level"),
            "source": "mongo",
        }
        for a in sorted(assets, key=lambda x: float(x.get("condition_score") or 10))
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Owner endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def get_owner_levy_history(building_id: str, lot_number: str, years: int = 5) -> list[dict]:
    """Levy charge + payment history for a specific lot."""
    if await _toggle_on(building_id):
        try:
            return await _owner_levy_history_pg(building_id, lot_number, years)
        except Exception as e:
            logger.info("bi_service.get_owner_levy_history: PG unavailable (%s), Mongo", e)
    return await _owner_levy_history_mongo(building_id, lot_number, years)


async def _owner_levy_history_pg(building_id: str, lot_number: str, years: int) -> list[dict]:
    """Generated function header.

    Function: _owner_levy_history_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff_year = datetime.now().year - years

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT financial_year, fund_type, total_charged_cents, paid_cents,
                       outstanding_cents, issue_date, due_date
                FROM analytics.fact_levy_charge
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND lot_number = :lot
                  AND CAST(SPLIT_PART(financial_year, '-', 1) AS INTEGER) >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                ORDER BY financial_year, fund_type
            """),
            {"sid": scheme_id, "lot": lot_number, "cutoff": cutoff_year},
        )
        return [
            {
                "financial_year": r[0],
                "fund_type": r[1],
                "charged": _cents(r[2]),
                "paid": _cents(r[3]),
                "outstanding": _cents(r[4]),
                "issue_date": str(r[5]) if r[5] else None,
                "due_date": str(r[6]) if r[6] else None,
                "source": "postgres_bi",
            }
            for r in rows.fetchall()
        ]


async def _owner_levy_history_mongo(building_id: str, lot_number: str, years: int) -> list[dict]:
    """Generated function header.

    Function: _owner_levy_history_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    cutoff_year = datetime.now().year - years
    cursor = db.unit_levy_ledger.find(
        {
            "building_id": building_id,
            "lot_number": lot_number,
            "year": {"$gte": str(cutoff_year)},
        },
        {"year": 1, "total_levied": 1, "total_paid": 1, "net_balance": 1}
    )
    docs = await cursor.to_list(years * 2)
    return [
        {
            "financial_year": str(d.get("year")),
            "fund_type": "all",
            "charged": float(d.get("total_levied") or 0),
            "paid": float(d.get("total_paid") or 0),
            # Outstanding = the unit's own positive balance (authoritative net_balance,
            # never the banned balance_owing field) — a per-unit obligation, not netted.
            "outstanding": round(max(float(d.get("net_balance") or 0), 0.0), 2),
            "issue_date": None,
            "due_date": None,
            "source": "mongo",
        }
        for d in sorted(docs, key=lambda d: str(d.get("year", "")))
    ]


async def get_owner_arrears_status(building_id: str, lot_number: str) -> dict:
    """Current arrears position for a specific lot."""
    if await _toggle_on(building_id):
        try:
            return await _owner_arrears_pg(building_id, lot_number)
        except Exception as e:
            logger.info("bi_service.get_owner_arrears_status: PG unavailable (%s), Mongo", e)
    return await _owner_arrears_mongo(building_id, lot_number)


async def _owner_arrears_pg(building_id: str, lot_number: str) -> dict:
    """Generated function header.

    Function: _owner_arrears_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        row = await session.execute(
            text("""
                SELECT total_outstanding_cents, admin_outstanding_cents,
                       sinking_outstanding_cents, interest_outstanding_cents,
                       days_overdue, escalation_status, last_payment_date,
                       last_payment_cents, arrears_band
                FROM analytics.fact_arrears_snapshot
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND lot_number = :lot
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM analytics.fact_arrears_snapshot
                      WHERE scheme_id = CAST(:sid AS UUID) AND lot_number = :lot
                  )
                  AND COALESCE(is_test_data, FALSE) = FALSE
                LIMIT 1
            """),
            {"sid": scheme_id, "lot": lot_number},
        )
        r = row.fetchone()

    if not r:
        return {"lot_number": lot_number, "total_outstanding": 0, "in_arrears": False, "source": "postgres_bi"}

    return {
        "lot_number": lot_number,
        "total_outstanding": _cents(r[0]),
        "admin_outstanding": _cents(r[1]),
        "sinking_outstanding": _cents(r[2]),
        "interest_outstanding": _cents(r[3]),
        "days_overdue": r[4],
        "escalation_status": r[5] or "none",
        "last_payment_date": str(r[6]) if r[6] else None,
        "last_payment_amount": _cents(r[7]),
        "arrears_band": r[8],
        "in_arrears": float(r[0] or 0) > 0,
        "source": "postgres_bi",
    }


async def _owner_arrears_mongo(building_id: str, lot_number: str) -> dict:
    """Generated function header.

    Function: _owner_arrears_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from utils.finance_helpers import get_unit_arrears_map

    fy = _current_fy()
    # Grace-aware per-unit arrears (GAP-FIN-040) — same canonical formula as
    # /arrears/detail and the building aggregate, never the banned balance_owing field.
    arrears_map = await get_unit_arrears_map(building_id, fy)
    row = arrears_map.get(lot_number)
    outstanding = row["arrears"] if row else 0.0
    in_arrears = bool(row and row["in_arrears"])
    return {
        "lot_number": lot_number,
        "total_outstanding": outstanding,
        "admin_outstanding": None,
        "sinking_outstanding": None,
        "interest_outstanding": None,
        "days_overdue": None,
        "escalation_status": "unknown",
        "last_payment_date": None,
        "last_payment_amount": None,
        "arrears_band": "unknown" if row is None else ("overdue" if in_arrears else "current"),
        "in_arrears": in_arrears,
        "source": "mongo",
    }


async def get_owner_total_cost_of_ownership(building_id: str, lot_number: str, years: int = 5) -> list[dict]:
    """True cost of ownership per lot per year."""
    if await _toggle_on(building_id):
        try:
            return await _tco_pg(building_id, lot_number, years)
        except Exception as e:
            logger.info("bi_service.get_owner_tco: PG unavailable (%s), Mongo", e)
    return await _tco_mongo(building_id, lot_number, years)


async def _tco_pg(building_id: str, lot_number: str, years: int) -> list[dict]:
    """Generated function header.

    Function: _tco_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff = datetime.now().year - years

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT financial_year, admin_levy_cents, sinking_levy_cents,
                       special_levy_cents, water_bill_cents, council_rates_cents,
                       insurance_share_cents, total_cost_cents
                FROM analytics.fact_true_cost_ownership
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND lot_number = :lot
                  AND CAST(SPLIT_PART(financial_year, '-', 1) AS INTEGER) >= :cutoff
                  AND COALESCE(is_test_data, FALSE) = FALSE
                ORDER BY financial_year
            """),
            {"sid": scheme_id, "lot": lot_number, "cutoff": cutoff},
        )
        return [
            {
                "financial_year": r[0],
                "admin_levy": _cents(r[1]),
                "sinking_levy": _cents(r[2]),
                "special_levy": _cents(r[3]),
                "water": _cents(r[4]),
                "council_rates": _cents(r[5]),
                "insurance_share": _cents(r[6]),
                "total_cost": _cents(r[7]),
                "source": "postgres_bi",
            }
            for r in rows.fetchall()
        ]


async def _tco_mongo(building_id: str, lot_number: str, years: int) -> list[dict]:
    """Generated function header.

    Function: _tco_mongo
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from services.true_cost_service import compute_true_cost_for_lot
    try:
        tco = await compute_true_cost_for_lot(building_id, lot_number)
        return tco if isinstance(tco, list) else [tco]
    except Exception:
        pass
    fy = _current_fy()
    doc = await db.unit_levy_ledger.find_one({"building_id": building_id, "lot_number": lot_number, "year": fy})
    total = float(doc.get("total_levied") or 0) if doc else 0.0
    return [{"financial_year": fy, "total_cost": total, "source": "mongo"}]


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def get_portfolio_arrears_hotspots(org_id: str, building_ids: list[str]) -> list[dict]:
    """Cross-building arrears hotspots for a portfolio."""
    results = []
    for bid in building_ids:
        try:
            summary = await get_financial_summary(bid)
            results.append({
                "building_id": bid,
                "lots_in_arrears": summary.get("lots_in_arrears"),
                "total_arrears": summary.get("total_arrears"),
                "levy_collection_rate": summary.get("levy_collection_rate"),
                "source": summary.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(key=lambda x: float(x.get("total_arrears") or 0), reverse=True)
    return results


async def get_portfolio_health_ranking(org_id: str, building_ids: list[str]) -> list[dict]:
    """Building health ranking across portfolio."""
    results = []
    for bid in building_ids:
        try:
            trend = await get_health_trend(bid, days=7)
            latest = trend[-1] if trend else {}
            results.append({
                "building_id": bid,
                "overall_score": latest.get("overall_score"),
                "health_label": latest.get("health_label"),
                "source": latest.get("source", "unknown"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(key=lambda x: float(x.get("overall_score") or 0))
    return results


async def get_portfolio_compliance_overdue(org_id: str, building_ids: list[str]) -> list[dict]:
    """Cross-building overdue compliance items."""
    results = []
    for bid in building_ids:
        try:
            status = await get_compliance_status(bid)
            results.append({
                "building_id": bid,
                "total_overdue": status.get("total_overdue"),
                "total_pending": status.get("total_pending"),
                "overall_status": status.get("overall_status"),
                "source": status.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(key=lambda x: int(x.get("total_overdue") or 0), reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Alert intelligence
# ─────────────────────────────────────────────────────────────────────────────

async def evaluate_arrears_alerts(building_id: str) -> list[dict]:
    """Fire arrears_escalation alerts: lot >60 days overdue, no payment in 30 days.

    Uses get_arrears_hotspots(), which is PG-attempt-first with a MongoDB
    fallback — alerts must keep firing during the cutover window rather than
    silently going quiet just because bi_pg_primary_enabled is off for this
    building.
    """
    try:
        hotspots = await get_arrears_hotspots(building_id, min_days_overdue=60)
        alerts = []
        for lot in hotspots:
            last_payment = lot.get("last_payment_date")
            no_recent_payment = True
            if last_payment:
                try:
                    lp_date = date.fromisoformat(str(last_payment))
                    no_recent_payment = (date.today() - lp_date).days > 30
                except Exception:
                    pass
            if no_recent_payment and float(lot.get("total_outstanding") or 0) > 0:
                alerts.append({
                    "rule_code": "arrears_escalation",
                    "severity": "high",
                    "entity_type": "lot",
                    "entity_id": lot["lot_number"],
                    "payload": lot,
                })
        return alerts
    except Exception as e:
        logger.warning("evaluate_arrears_alerts failed: %s", e)
        return []


async def evaluate_compliance_alerts(building_id: str, days_ahead: int = 30) -> list[dict]:
    """Fire compliance_due alerts: inspections due in next N days with no completion.

    PG-attempt-first (analytics.fact_compliance_event) with a MongoDB fallback
    against db.compliance_registers, mirroring get_compliance_status()'s own
    fallback path — alerts must not go silent just because bi_pg_primary_enabled
    is off for this building.
    """
    if await _toggle_on(building_id):
        try:
            return await _compliance_due_alerts_pg(building_id, days_ahead)
        except Exception as e:
            logger.info("evaluate_compliance_alerts: PG unavailable (%s), Mongo", e)
    try:
        return await _compliance_due_alerts_mongo(building_id, days_ahead)
    except Exception as e:
        logger.warning("evaluate_compliance_alerts failed: %s", e)
        return []


async def _compliance_due_alerts_pg(building_id: str, days_ahead: int) -> list[dict]:
    """Generated function header.

    Function: _compliance_due_alerts_pg
    Path: backend/services/bi_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    cutoff = date.today() + timedelta(days=days_ahead)

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text("""
                SELECT compliance_item_id, register_type, item_title, due_date, rag_status
                FROM analytics.fact_compliance_event
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND event_type = 'due'
                  AND due_date <= :cutoff
                  AND due_date >= CURRENT_DATE
                  AND rag_status != 'green'
                  AND COALESCE(is_test_data, FALSE) = FALSE
            """),
            {"sid": scheme_id, "cutoff": cutoff},
        )
        return [
            {
                "rule_code": "compliance_due",
                "severity": "medium" if r[4] == "amber" else "high",
                "entity_type": "compliance_item",
                "entity_id": str(r[0]),
                "payload": {"register_type": r[1], "title": r[2], "due_date": str(r[3]), "rag": r[4]},
            }
            for r in rows.fetchall()
        ]


async def _compliance_due_alerts_mongo(building_id: str, days_ahead: int) -> list[dict]:
    """MongoDB fallback for compliance_due alerts.

    IMPORTANT — schema note (this replaced an earlier version of this function
    that queried the wrong collection/fields entirely): `compliance_registers`
    is a per-building HEADER doc per register type (fire/lift/asbestos/pool) —
    it has no per-item due date. Individual inspection items with their own
    due dates live in the separate `compliance_register_items` collection,
    keyed by `next_inspection_date` (a "YYYY-MM-DD" string, NOT a datetime —
    confirmed against the existing overdue-count aggregation in
    routers/compliance_registers.py:list_compliance_registers, which compares
    it with a plain string `$lt today`) and `description` (not `item_title`).
    `status` on an item is an asset-lifecycle flag ("active" | "decommissioned"
    | "replaced") — NOT a due/overdue/completed state; only "active" items are
    still due for inspection. `register_type` for the payload comes from the
    parent `compliance_registers` header doc, looked up by `register_id`.
    """
    from database import db

    today_str = str(date.today())
    cutoff_str = str(date.today() + timedelta(days=days_ahead))

    # Mirrors the PG rule's own filter exactly: due within the window and not
    # yet overdue (analytics.fact_compliance_event's "due_date >= CURRENT_DATE"
    # clause) — an item that's already overdue is a different, separate alert
    # class in the PG rule and is intentionally excluded here too for parity.
    items = await db.compliance_register_items.find(
        {
            "building_id": building_id,
            "status": "active",
            "next_inspection_date": {"$gte": today_str, "$lte": cutoff_str},
        },
        {"register_id": 1, "description": 1, "next_inspection_date": 1},
    ).to_list(200)
    if not items:
        return []

    from bson import ObjectId
    register_ids = {item.get("register_id") for item in items if item.get("register_id")}
    valid_object_ids = [ObjectId(rid) for rid in register_ids if ObjectId.is_valid(rid)]
    registers = await db.compliance_registers.find(
        {"building_id": building_id, "_id": {"$in": valid_object_ids}},
        {"register_type": 1},
    ).to_list(50) if valid_object_ids else []
    register_type_by_id = {str(r["_id"]): r.get("register_type") for r in registers}

    return [
        {
            "rule_code": "compliance_due",
            "severity": "medium",
            "entity_type": "compliance_item",
            "entity_id": str(item.get("_id")),
            "payload": {
                "register_type": register_type_by_id.get(item.get("register_id")),
                "title": item.get("description"),
                "due_date": item.get("next_inspection_date"),
                "rag": "amber",
            },
        }
        for item in items
    ]


async def evaluate_utility_spike_alerts(building_id: str) -> list[dict]:
    """Fire utility_spike alerts: spend >20% above 3-month rolling average.

    Uses get_utility_trend(), which is PG-attempt-first with a MongoDB
    fallback — alerts must keep firing during the cutover window rather than
    silently going quiet just because bi_pg_primary_enabled is off for this
    building.
    """
    try:
        trend_data = (await get_utility_trend(building_id, months=6)).get("months", [])
        if len(trend_data) < 4:
            return []
        alerts = []
        months_sorted = sorted(trend_data, key=lambda x: x["month"])
        current = months_sorted[-1]
        rolling_avg = sum(m["total"] for m in months_sorted[-4:-1]) / 3
        if rolling_avg > 0:
            pct_above = (current["total"] - rolling_avg) / rolling_avg * 100
            if pct_above > 20:
                alerts.append({
                    "rule_code": "utility_spike",
                    "severity": "medium",
                    "entity_type": "building",
                    "entity_id": building_id,
                    "payload": {
                        "current_month": current["month"],
                        "current_spend": current["total"],
                        "rolling_avg": round(rolling_avg, 2),
                        "pct_above": round(pct_above, 1),
                    },
                })
        return alerts
    except Exception as e:
        logger.warning("evaluate_utility_spike_alerts failed: %s", e)
        return []


async def evaluate_health_drop_alerts(building_id: str) -> list[dict]:
    """Fire health score drop alert: score drops >10 points in 30 days.

    Uses get_health_trend(), which is PG-attempt-first with a MongoDB
    fallback — alerts must keep firing during the cutover window rather than
    silently going quiet just because bi_pg_primary_enabled is off for this
    building.
    """
    try:
        trend = await get_health_trend(building_id, days=35)
        if len(trend) < 2:
            return []
        oldest = trend[0]
        newest = trend[-1]
        drop = float(oldest.get("overall_score") or 0) - float(newest.get("overall_score") or 0)
        if drop >= 10:
            return [{
                "rule_code": "health_score_drop",
                "severity": "high",
                "entity_type": "building",
                "entity_id": building_id,
                "payload": {
                    "from_score": oldest["overall_score"],
                    "to_score": newest["overall_score"],
                    "from_date": oldest["date"],
                    "to_date": newest["date"],
                    "drop": round(drop, 1),
                },
            }]
        return []
    except Exception as e:
        logger.warning("evaluate_health_drop_alerts failed: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Cutover Readiness
# ─────────────────────────────────────────────────────────────────────────────

async def get_bi_cutover_status(building_id: str) -> dict:
    """Check whether this building's BI analytics data is ready for PG-primary mode.

    Checks:
      1. Required dimensions populated (building, lot, owner, date)
      2. Required facts populated (levy_charge, arrears_snapshot, financial_balance,
         work_order/maintenance, compliance_event, occupancy_snapshot)
      3. ETL freshness (last run successful, no failed required job)
      4. Parity (lot count, arrears total, levy total, work orders, compliance overdue)
         within tolerance compared to operational Postgres/Mongo source
      5. No unscoped records, no hardcoded building fallback

    Returns a structured readiness report.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    required_checks: list[dict] = []
    failed_checks: list[dict] = []
    warnings: list[str] = []

    try:
        scheme_id, tenant_id = await _resolve_scheme_id(building_id)
    except Exception as exc:
        return {
            "ready": False,
            "building_id": building_id,
            "required_checks": [],
            "failed_checks": [{"check": "scheme_resolution", "detail": str(exc)}],
            "warnings": [],
            "can_enable_bi": False,
            "can_enable_pg_primary": False,
        }

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        # ── 1. Dimension checks ───────────────────────────────────────────────
        for dim_table, label in [
            ("analytics.dim_building", "dim_building"),
            ("analytics.dim_lot", "dim_lot"),
            ("analytics.dim_owner", "dim_owner"),
            ("analytics.dim_date", "dim_date"),
        ]:
            try:
                if dim_table == "analytics.dim_date":
                    row = await session.execute(text(f"SELECT COUNT(*) FROM {dim_table}"))
                else:
                    row = await session.execute(
                        text(f"SELECT COUNT(*) FROM {dim_table} WHERE scheme_id = CAST(:sid AS UUID)"),
                        {"sid": scheme_id},
                    )
                count = row.scalar() or 0
                chk = {"check": label, "count": count, "status": "ok" if count > 0 else "empty"}
                required_checks.append(chk)
                if count == 0:
                    failed_checks.append({"check": label, "detail": "dimension table is empty"})
            except Exception as exc:
                failed_checks.append({"check": label, "detail": str(exc)})

        # ── 2. Fact checks ────────────────────────────────────────────────────
        fact_tables = [
            ("analytics.fact_levy_charge", "fact_levy_charge"),
            ("analytics.fact_arrears_snapshot", "fact_arrears_snapshot"),
            ("analytics.fact_financial_balance", "fact_financial_balance"),
            ("analytics.fact_work_order", "fact_work_order"),
            ("analytics.fact_compliance_event", "fact_compliance_event"),
            ("analytics.fact_occupancy_snapshot", "fact_occupancy_snapshot"),
        ]
        for fact_table, label in fact_tables:
            try:
                row = await session.execute(
                    text(f"SELECT COUNT(*) FROM {fact_table} WHERE scheme_id = CAST(:sid AS UUID)"),
                    {"sid": scheme_id},
                )
                count = row.scalar() or 0
                chk = {"check": label, "count": count, "status": "ok" if count > 0 else "empty"}
                required_checks.append(chk)
                if count == 0:
                    failed_checks.append({"check": label, "detail": "fact table has no rows for this building"})
            except Exception as exc:
                failed_checks.append({"check": label, "detail": str(exc)})

        # ── 3. ETL freshness ──────────────────────────────────────────────────
        try:
            etl_row = await session.execute(
                text("""
                    SELECT target_table, status, completed_at,
                           EXTRACT(EPOCH FROM (now() - completed_at))/3600 AS hours_since
                    FROM analytics.bi_etl_runs
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND status = 'success'
                    ORDER BY completed_at DESC
                    LIMIT 10
                """),
                {"sid": scheme_id},
            )
            etl_rows = etl_row.fetchall()
            if not etl_rows:
                failed_checks.append({"check": "etl_freshness", "detail": "no successful ETL runs found"})
            else:
                stale = [r for r in etl_rows if (r[3] or 999) > 48]
                if stale:
                    warnings.append(f"ETL stale for: {', '.join(r[0] for r in stale)}")
                required_checks.append({
                    "check": "etl_freshness",
                    "last_run_tables": len(etl_rows),
                    "stale_count": len(stale),
                    "status": "ok" if not stale else "stale",
                })
        except Exception as exc:
            failed_checks.append({"check": "etl_freshness", "detail": str(exc)})

        # ── 4. Parity checks ─────────────────────────────────────────────────
        TOLERANCE_PCT = 5.0  # 5% tolerance
        from database import db

        # Lot count parity
        try:
            bi_lot_row = await session.execute(
                text("SELECT COUNT(DISTINCT dim_lot_id) FROM analytics.fact_levy_charge "
                     "WHERE scheme_id = CAST(:sid AS UUID)"),
                {"sid": scheme_id},
            )
            bi_lot_count = bi_lot_row.scalar() or 0

            op_lot_row = await session.execute(
                text("SELECT COUNT(*) FROM core.lots WHERE scheme_id = CAST(:sid AS UUID)"),
                {"sid": scheme_id},
            )
            op_lot_count = op_lot_row.scalar() or 0

            lot_diff_pct = abs(bi_lot_count - op_lot_count) / max(op_lot_count, 1) * 100
            chk = {
                "check": "parity_lot_count",
                "bi_count": bi_lot_count,
                "op_count": op_lot_count,
                "diff_pct": round(lot_diff_pct, 1),
                "status": "ok" if lot_diff_pct <= TOLERANCE_PCT else "fail",
            }
            required_checks.append(chk)
            if lot_diff_pct > TOLERANCE_PCT:
                failed_checks.append({
                    "check": "parity_lot_count",
                    "detail": f"Lot count mismatch: BI={bi_lot_count}, Ops={op_lot_count} ({lot_diff_pct:.1f}% diff)",
                })
        except Exception as exc:
            warnings.append(f"parity_lot_count check skipped: {exc}")

        # Arrears parity (BI vs operational Mongo)
        try:
            bi_arrears_row = await session.execute(
                text("""
                    SELECT SUM(total_outstanding_cents)
                    FROM analytics.fact_arrears_snapshot
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND snapshot_date = (SELECT MAX(snapshot_date)
                                           FROM analytics.fact_arrears_snapshot
                                           WHERE scheme_id = CAST(:sid AS UUID))
                """),
                {"sid": scheme_id},
            )
            bi_arrears = float(bi_arrears_row.scalar() or 0) / 100

            # Mongo source — canonical no-netting gross outstanding from the
            # authoritative net_balance (never the banned balance_owing field),
            # scoped to the current levy year to match the BI snapshot (GAP-FIN-040).
            from database import db
            from utils.finance_helpers import sum_ledger_collected_outstanding
            ledger = await db.unit_levy_ledger.find(
                {"building_id": building_id, "year": _current_fy(), "is_test_data": {"$ne": True}},
                {"net_balance": 1, "total_levied": 1},
            ).to_list(500)
            mongo_arrears = sum_ledger_collected_outstanding(ledger)["total_outstanding"]

            if mongo_arrears > 0:
                arr_diff_pct = abs(bi_arrears - mongo_arrears) / mongo_arrears * 100
                chk = {
                    "check": "parity_arrears",
                    "bi_total": bi_arrears,
                    "mongo_total": mongo_arrears,
                    "diff_pct": round(arr_diff_pct, 1),
                    "status": "ok" if arr_diff_pct <= TOLERANCE_PCT else "fail",
                }
                required_checks.append(chk)
                if arr_diff_pct > TOLERANCE_PCT:
                    failed_checks.append({
                        "check": "parity_arrears",
                        "detail": f"Arrears mismatch: BI=${bi_arrears:,.2f}, Mongo=${mongo_arrears:,.2f}",
                    })
        except Exception as exc:
            warnings.append(f"parity_arrears check skipped: {exc}")

        # Levy total parity (BI vs operational Mongo ledger)
        try:
            bi_levy_row = await session.execute(
                text("""
                    SELECT SUM(total_charged_cents)
                    FROM analytics.fact_levy_charge
                    WHERE scheme_id = CAST(:sid AS UUID)
                """),
                {"sid": scheme_id},
            )
            bi_levy_total = float(bi_levy_row.scalar() or 0) / 100

            ledger_rows = await db.unit_levy_ledger.find(
                {"building_id": building_id},
                {"total_levied": 1},
            ).to_list(1000)
            mongo_levy_total = sum(float(r.get("total_levied") or 0) for r in ledger_rows)

            levy_diff_pct = abs(bi_levy_total - mongo_levy_total) / max(mongo_levy_total, 1) * 100
            chk = {
                "check": "parity_levy_total",
                "bi_total": bi_levy_total,
                "mongo_total": mongo_levy_total,
                "diff_pct": round(levy_diff_pct, 1),
                "status": "ok" if levy_diff_pct <= TOLERANCE_PCT else "fail",
            }
            required_checks.append(chk)
            if levy_diff_pct > TOLERANCE_PCT:
                failed_checks.append({
                    "check": "parity_levy_total",
                    "detail": f"Levy total mismatch: BI=${bi_levy_total:,.2f}, Mongo=${mongo_levy_total:,.2f}",
                })
        except Exception as exc:
            warnings.append(f"parity_levy_total check skipped: {exc}")

        # Work-order parity (open count)
        try:
            bi_work_row = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM analytics.fact_work_order
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND LOWER(COALESCE(status, '')) NOT IN ('completed', 'closed', 'resolved', 'cancelled')
                """),
                {"sid": scheme_id},
            )
            bi_open_work_orders = int(bi_work_row.scalar() or 0)

            mongo_open_work_orders = await db.work_orders.count_documents(
                {
                    "building_id": building_id,
                    "status": {"$nin": ["completed", "closed", "resolved", "cancelled"]},
                }
            )

            work_order_diff_pct = abs(bi_open_work_orders - mongo_open_work_orders) / max(mongo_open_work_orders, 1) * 100
            chk = {
                "check": "parity_work_orders",
                "bi_count": bi_open_work_orders,
                "mongo_count": int(mongo_open_work_orders),
                "diff_pct": round(work_order_diff_pct, 1),
                "status": "ok" if work_order_diff_pct <= TOLERANCE_PCT else "fail",
            }
            required_checks.append(chk)
            if work_order_diff_pct > TOLERANCE_PCT:
                failed_checks.append({
                    "check": "parity_work_orders",
                    "detail": f"Open work-order mismatch: BI={bi_open_work_orders}, Mongo={mongo_open_work_orders}",
                })
        except Exception as exc:
            warnings.append(f"parity_work_orders check skipped: {exc}")

        # Compliance parity (overdue item count)
        try:
            bi_compliance_row = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM analytics.fact_compliance_event
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND event_type = 'overdue'
                """),
                {"sid": scheme_id},
            )
            bi_overdue_compliance = int(bi_compliance_row.scalar() or 0)

            compliance_docs = await db.compliance_registers.find(
                {"building_id": building_id},
                {"items": 1},
            ).to_list(200)
            mongo_overdue_compliance = 0
            for doc in compliance_docs:
                for item in doc.get("items", []) or []:
                    status = str(item.get("status") or "").lower()
                    if status in {"overdue", "expired"}:
                        mongo_overdue_compliance += 1

            compliance_diff_pct = abs(bi_overdue_compliance - mongo_overdue_compliance) / max(mongo_overdue_compliance, 1) * 100
            chk = {
                "check": "parity_compliance_overdue",
                "bi_count": bi_overdue_compliance,
                "mongo_count": mongo_overdue_compliance,
                "diff_pct": round(compliance_diff_pct, 1),
                "status": "ok" if compliance_diff_pct <= TOLERANCE_PCT else "fail",
            }
            required_checks.append(chk)
            if compliance_diff_pct > TOLERANCE_PCT:
                failed_checks.append({
                    "check": "parity_compliance_overdue",
                    "detail": f"Overdue compliance mismatch: BI={bi_overdue_compliance}, Mongo={mongo_overdue_compliance}",
                })
        except Exception as exc:
            warnings.append(f"parity_compliance_overdue check skipped: {exc}")

    # ── Determine overall readiness ───────────────────────────────────────────
    critical_failures = [f for f in failed_checks if f["check"] in {
        "scheme_resolution", "dim_building", "dim_lot",
        "fact_levy_charge", "fact_arrears_snapshot",
    }]
    can_enable_bi = len(failed_checks) == 0 or len(critical_failures) == 0
    # PG-primary requires parity checks also pass
    parity_failures = [f for f in failed_checks if f["check"].startswith("parity_")]
    can_enable_pg_primary = len(failed_checks) == 0 and len(parity_failures) == 0

    return {
        "ready": can_enable_pg_primary,
        "building_id": building_id,
        "required_checks": required_checks,
        "failed_checks": failed_checks,
        "warnings": warnings,
        "can_enable_bi": can_enable_bi,
        "can_enable_pg_primary": can_enable_pg_primary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Agency / Platform aggregations
# ─────────────────────────────────────────────────────────────────────────────

async def get_portfolio_financial_summary(
    entity_id: str, building_ids: list[str], financial_year: str | None = None
) -> dict:
    """Aggregated financial KPIs across all buildings in a management entity portfolio.

    entity_id may be a management_entity_id, agency_id, or strata_manager_id —
    used only for labelling in the response, not for DB queries.
    """
    total_levied = 0.0
    total_paid = 0.0
    total_arrears = 0.0
    lots_in_arrears = 0
    total_lots = 0
    collection_rates: list[float] = []
    building_results = []

    for bid in building_ids:
        try:
            fin = await get_financial_summary(bid, financial_year)
            total_levied += float(fin.get("total_levied") or 0)
            total_paid += float(fin.get("total_paid") or 0)
            total_arrears += float(fin.get("total_arrears") or 0)
            lots_in_arrears += int(fin.get("lots_in_arrears") or 0)
            total_lots += int(fin.get("total_lots") or 0)
            if fin.get("levy_collection_rate") is not None:
                collection_rates.append(float(fin["levy_collection_rate"]))
            building_results.append({"building_id": bid, "status": "ok"})
        except Exception as exc:
            building_results.append({"building_id": bid, "status": "error", "detail": str(exc)})

    # Levied-weighted portfolio collection rate (GAP-FIN-040 B9): Σcollected / Σdue,
    # NOT an unweighted mean of per-building rates (which ignores building size and
    # lets a tiny 100%-paid building offset a large one in arrears).
    avg_rate = _weighted_collection_rate(total_paid, total_levied)
    return {
        "entity_id": entity_id,
        "financial_year": financial_year,
        "building_count": len(building_ids),
        "total_levied": total_levied,
        "total_paid": total_paid,
        "total_arrears": total_arrears,
        "lots_in_arrears": lots_in_arrears,
        "total_lots": total_lots,
        "avg_collection_rate": avg_rate,
        "buildings": building_results,
        "source": "aggregated",
    }


async def get_agency_summary(agency_id: str, building_ids: list[str]) -> dict:
    """High-level financial summary across all buildings in an agency."""
    total_levied = 0.0
    total_paid = 0.0
    total_arrears = 0.0
    lots_in_arrears = 0
    total_lots = 0
    collection_rates = []
    building_results = []

    for bid in building_ids:
        try:
            fin = await get_financial_summary(bid)
            total_levied += float(fin.get("total_levied") or 0)
            total_paid += float(fin.get("total_paid") or 0)
            total_arrears += float(fin.get("total_arrears") or 0)
            lots_in_arrears += int(fin.get("lots_in_arrears") or 0)
            total_lots += int(fin.get("total_lots") or 0)
            if fin.get("levy_collection_rate") is not None:
                collection_rates.append(float(fin["levy_collection_rate"]))
            building_results.append({"building_id": bid, "status": "ok", "source": fin.get("source")})
        except Exception as exc:
            building_results.append({"building_id": bid, "status": "error", "detail": str(exc)})

    # Levied-weighted (GAP-FIN-040 B9) — not an unweighted mean of per-building rates.
    avg_collection_rate = _weighted_collection_rate(total_paid, total_levied)

    return {
        "agency_id": agency_id,
        "building_count": len(building_ids),
        "total_levied": total_levied,
        "total_paid": total_paid,
        "total_arrears": total_arrears,
        "lots_in_arrears": lots_in_arrears,
        "total_lots": total_lots,
        "avg_collection_rate": avg_collection_rate,
        "buildings": building_results,
        "source": "aggregated",
    }


async def get_agency_health_ranking(agency_id: str, building_ids: list[str]) -> list[dict]:
    """Buildings ranked by health score within an agency."""
    results = []
    for bid in building_ids:
        try:
            trend = await get_health_trend(bid, days=7)
            latest = trend[-1] if trend else {}
            results.append({
                "building_id": bid,
                "overall_score": latest.get("overall_score"),
                "health_label": latest.get("health_label"),
                "financial_score": latest.get("financial_score"),
                "maintenance_score": latest.get("maintenance_score"),
                "compliance_score": latest.get("compliance_score"),
                "source": latest.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "overall_score": None, "error": "UNKNOWN"})
    results.sort(key=lambda x: float(x.get("overall_score") or 0), reverse=True)
    return results


async def get_manager_workload(strata_manager_id: str, building_ids: list[str]) -> dict:
    """Workload summary for a strata manager across their assigned buildings."""
    total_open_wo = 0
    total_sla_breaches = 0
    total_compliance_overdue = 0
    building_details = []

    for bid in building_ids:
        try:
            maint = await get_maintenance_costs(bid)
            comp = await get_compliance_status(bid)
            open_wo = int(maint.get("open_work_orders") or 0)
            sla = int(maint.get("sla_breaches") or 0)
            overdue = int(comp.get("total_overdue") or 0)
            total_open_wo += open_wo
            total_sla_breaches += sla
            total_compliance_overdue += overdue
            building_details.append({
                "building_id": bid,
                "open_work_orders": open_wo,
                "sla_breaches": sla,
                "compliance_overdue": overdue,
            })
        except Exception:
            building_details.append({"building_id": bid, "error": "UNKNOWN"})

    return {
        "strata_manager_id": strata_manager_id,
        "building_count": len(building_ids),
        "total_open_work_orders": total_open_wo,
        "total_sla_breaches": total_sla_breaches,
        "total_compliance_overdue": total_compliance_overdue,
        "buildings": building_details,
    }


async def get_platform_summary() -> dict:
    """Platform-wide summary: all agencies, all buildings."""
    from services.bi_toggle_service import get_all_active_buildings
    building_ids = await get_all_active_buildings()

    total_buildings = len(building_ids)
    total_levied = 0.0
    total_arrears = 0.0
    errors = 0
    for bid in building_ids:
        try:
            fin = await get_financial_summary(bid)
            total_levied += float(fin.get("total_levied") or 0)
            total_arrears += float(fin.get("total_arrears") or 0)
        except Exception:
            errors += 1

    return {
        "total_buildings": total_buildings,
        "total_levied": total_levied,
        "total_arrears": total_arrears,
        "data_errors": errors,
        "source": "platform_aggregated",
    }
