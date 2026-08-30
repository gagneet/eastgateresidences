# @featuretrace:bi-analytics — BI ETL service: populates analytics.fact_* from Mongo and operational PG.
# Layer: service
# Data flow: cron/scheduler → bi_etl_service → analytics.fact_* via idempotent upserts.
#            Sources: unit_levy_ledger, annual_levies, levy_payments (Mongo) + finance.levy_items,
#            finance.receipts, compliance.compliance_items, ops.work_orders (PG).
# Related: backend/alembic/versions/0052_canonical_bi_schema.py
#          backend/services/bi_service.py
#          backend/routers/bi.py
# Toggle: bi_analytics_enabled
# Tests: tests/backend/test_bi_etl_service.py
"""BI ETL service — idempotent population of analytics fact/dimension tables.

All ETL functions:
 1. Read from canonical source (MongoDB or operational Postgres).
 2. Upsert into the corresponding analytics.fact_* or analytics.dim_* table.
 3. Log the run to analytics.bi_etl_runs for freshness tracking.
 4. Never truncate — upsert on natural business key.
 5. Set is_test_data from source record.

Scheduling recommendation:
  - Nightly (02:00 AEST): run_nightly_etl(building_id)
  - Event-driven: etl_levy_payment, etl_work_order_event on write-path events

Admin trigger: POST /bi/admin/etl/run triggers run_nightly_etl for a building.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date, timezone, timedelta
from typing import Any

from domain.finance.formulas.collection import levy_collection_fraction, levy_collection_health_score

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ETL run logger
# ─────────────────────────────────────────────────────────────────────────────

async def _log_etl_start(session, target_table: str, scheme_id: str | None = None) -> str:
    """Insert a bi_etl_runs row and return run_id."""
    from sqlalchemy import text
    row = await session.execute(
        text("""
            INSERT INTO analytics.bi_etl_runs (target_table, scheme_id, started_at, status)
            VALUES (:t, CAST(:s AS UUID), now(), 'running')
            RETURNING run_id::text
        """),
        {"t": target_table, "s": scheme_id},
    )
    return row.scalar()


async def _log_etl_done(session, run_id: str, inserted: int, updated: int, skipped: int = 0) -> None:
    """Generated function header.

    Function: _log_etl_done
    Path: backend/services/bi_etl_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    await session.execute(
        text("""
            UPDATE analytics.bi_etl_runs
            SET completed_at = now(), status = 'success',
                rows_inserted = :i, rows_updated = :u, rows_skipped = :s
            WHERE run_id = CAST(:rid AS UUID)
        """),
        {"i": inserted, "u": updated, "s": skipped, "rid": run_id},
    )


async def _log_etl_error(session, run_id: str, error: str) -> None:
    """Generated function header.

    Function: _log_etl_error
    Path: backend/services/bi_etl_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    await session.execute(
        text("""
            UPDATE analytics.bi_etl_runs
            SET completed_at = now(), status = 'error', error_message = :e
            WHERE run_id = CAST(:rid AS UUID)
        """),
        {"e": str(error)[:2000], "rid": run_id},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scheme resolution
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve(building_id: str) -> tuple[str, str]:
    """Return (scheme_id, tenant_id) for building_id."""
    from db_postgres.repos import identity_repo as _id_repo
    scheme = await _id_repo.get_scheme_by_number(building_id)
    if not scheme:
        raise RuntimeError(f"No PG scheme for building_id={building_id!r}")
    return str(scheme["scheme_id"]), str(scheme["tenant_id"])


# ─────────────────────────────────────────────────────────────────────────────
# dim_building — upsert from core.schemes + Mongo buildings
# ─────────────────────────────────────────────────────────────────────────────

async def etl_dim_building(building_id: str) -> int:
    """Upsert analytics.dim_building for one scheme."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    mongo_doc = await db.buildings.find_one({"building_id": building_id}) or {}

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.dim_building", scheme_id)
        try:
            await session.execute(
                text("""
                    UPDATE analytics.dim_building
                    SET is_current = FALSE, valid_to = now()
                    WHERE scheme_id = CAST(:sid AS UUID) AND is_current = TRUE
                """),
                {"sid": scheme_id},
            )
            await session.execute(
                text("""
                    INSERT INTO analytics.dim_building (
                        tenant_id, scheme_id, scheme_number, building_name,
                        suburb, state, postcode, jurisdiction,
                        total_lots, valid_from, is_current,
                        source_system, source_collection, source_id, ingested_at
                    ) VALUES (
                        CAST(:tid AS UUID), CAST(:sid AS UUID), :num, :name,
                        :suburb, :state, :post, :jur,
                        :lots, now(), TRUE,
                        'mongo', 'buildings', :mid, now()
                    )
                """),
                {
                    "tid": tenant_id, "sid": scheme_id,
                    "num": building_id,
                    "name": mongo_doc.get("name") or mongo_doc.get("building_name"),
                    "suburb": mongo_doc.get("suburb") or mongo_doc.get("location", {}).get("suburb"),
                    "state": mongo_doc.get("state"),
                    "post": mongo_doc.get("postcode"),
                    "jur": mongo_doc.get("jurisdiction") or "ACT",
                    "lots": mongo_doc.get("lot_count") or mongo_doc.get("lots"),
                    "mid": str(mongo_doc.get("_id", "")),
                },
            )
            await _log_etl_done(session, run_id, inserted=1, updated=0)
            await session.commit()
            return 1
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_levy_charge — from unit_levy_ledger (Mongo) or finance.levy_items (PG)
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_levy_charge(building_id: str, financial_year: str | None = None) -> int:
    """Upsert analytics.fact_levy_charge from MongoDB unit_levy_ledger.

    Returns count of rows upserted.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    fy = financial_year or str(datetime.now().year)

    # Fetch from MongoDB unit_levy_ledger
    cursor = db.unit_levy_ledger.find(
        {"building_id": building_id, "year": fy, "is_test_data": {"$ne": True}},
        {
            "lot_number": 1, "year": 1, "total_levied": 1, "total_paid": 1,
            "net_balance": 1, "admin_levied": 1, "sinking_levied": 1,
            "due_date": 1, "_id": 1,
        }
    )
    rows = await cursor.to_list(1000)

    if not rows:
        logger.info("etl_fact_levy_charge: no rows for building=%s fy=%s", building_id, fy)
        return 0

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_levy_charge", scheme_id)
        try:
            for row in rows:
                lot = str(row.get("lot_number", ""))
                total_levied_cents = int(round(float(row.get("total_levied") or 0) * 100))
                total_paid_cents = int(round(float(row.get("total_paid") or 0) * 100))
                # Outstanding from the authoritative net_balance (never the banned
                # balance_owing field), per-unit gross (max 0) — GAP-FIN-040.
                outstanding_cents = max(0, int(round(float(row.get("net_balance") or 0) * 100)))
                admin_cents = int(round(float(row.get("admin_levied") or total_levied_cents / 100 * 100) * 100)) if "admin_levied" in row else total_levied_cents
                source_id = str(row.get("_id", ""))

                # Upsert: conflict on (scheme_id, lot_number, financial_year, fund_type)
                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_levy_charge (
                            tenant_id, scheme_id, lot_number, financial_year,
                            fund_type, principal_cents, gst_cents, interest_cents,
                            total_charged_cents, paid_cents, outstanding_cents,
                            source_system, source_collection, source_id, ingested_at, is_current
                        ) VALUES (
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :lot, :fy,
                            'all', :principal, 0, 0,
                            :total, :paid, :outstanding,
                            'mongo', 'unit_levy_ledger', :src_id, now(), TRUE
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id,
                        "lot": lot, "fy": fy,
                        "principal": total_levied_cents,
                        "total": total_levied_cents,
                        "paid": total_paid_cents,
                        "outstanding": outstanding_cents,
                        "src_id": source_id,
                    },
                )
                if result.rowcount:
                    inserted += 1
                else:
                    # Update existing
                    await session.execute(
                        text("""
                            UPDATE analytics.fact_levy_charge
                            SET total_charged_cents = :total, paid_cents = :paid,
                                outstanding_cents = :outstanding, ingested_at = now()
                            WHERE scheme_id = CAST(:sid AS UUID)
                              AND lot_number = :lot AND financial_year = :fy
                              AND fund_type = 'all'
                        """),
                        {
                            "sid": scheme_id, "lot": lot, "fy": fy,
                            "total": total_levied_cents, "paid": total_paid_cents,
                            "outstanding": outstanding_cents,
                        },
                    )
                    updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            logger.info("etl_fact_levy_charge: building=%s fy=%s inserted=%d updated=%d", building_id, fy, inserted, updated)
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_arrears_snapshot — nightly snapshot from unit_levy_ledger
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_arrears_snapshot(building_id: str) -> int:
    """Nightly: snapshot current lot-level arrears into fact_arrears_snapshot."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    snapshot_date = date.today()
    fy = str(datetime.now().year)

    cursor = db.unit_levy_ledger.find(
        {"building_id": building_id, "year": fy, "is_test_data": {"$ne": True}},
        {"lot_number": 1, "net_balance": 1, "total_levied": 1, "total_paid": 1,
         "last_payment_date": 1, "last_payment_amount": 1, "_id": 1}
    )
    rows = await cursor.to_list(1000)

    inserted = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_arrears_snapshot", scheme_id)
        try:
            for row in rows:
                lot = str(row.get("lot_number", ""))
                # Gross outstanding from the authoritative net_balance (never the
                # banned balance_owing field), per-unit non-negative — GAP-FIN-040.
                outstanding = max(0.0, float(row.get("net_balance") or 0))
                outstanding_cents = int(round(outstanding * 100))
                last_payment_date = row.get("last_payment_date")
                last_payment_cents = int(round(float(row.get("last_payment_amount") or 0) * 100))

                # Compute days_overdue: if any balance and last payment > 30 days
                days_overdue = None
                if outstanding > 0 and last_payment_date:
                    try:
                        lp = date.fromisoformat(str(last_payment_date)[:10])
                        days_overdue = (snapshot_date - lp).days
                    except Exception:
                        pass

                arrears_band = (
                    "current" if outstanding <= 0 else
                    "0-30" if (days_overdue or 0) <= 30 else
                    "31-60" if (days_overdue or 0) <= 60 else
                    "61-90" if (days_overdue or 0) <= 90 else
                    "90+"
                )

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_arrears_snapshot (
                            tenant_id, scheme_id, snapshot_date, lot_number,
                            total_outstanding_cents, days_overdue, arrears_band,
                            last_payment_date, last_payment_cents,
                            source_system, source_collection, source_id, ingested_at
                        ) VALUES (
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :snap_date, :lot,
                            :outstanding, :days, :band,
                            :last_pay, :last_amt,
                            'mongo', 'unit_levy_ledger', :src_id, now()
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id,
                        "snap_date": snapshot_date, "lot": lot,
                        "outstanding": outstanding_cents,
                        "days": days_overdue,
                        "band": arrears_band,
                        "last_pay": str(last_payment_date)[:10] if last_payment_date else None,
                        "last_amt": last_payment_cents,
                        "src_id": str(row.get("_id", "")),
                    },
                )
                if result.rowcount:
                    inserted += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=0)
            await session.commit()
            logger.info("etl_fact_arrears_snapshot: building=%s date=%s inserted=%d", building_id, snapshot_date, inserted)
            return inserted
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_financial_balance — from annual_levies (Mongo) or finance.accounting_periods (PG)
# ─────────────────────────────────────────────────────────────────────────────

def _money_to_cents(value: Any) -> int | None:
    """Generated function header.

    Function: _money_to_cents
    Path: backend/services/bi_etl_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _fund_actual_balance_cents(
        fund: dict[str, Any],
        *,
        actual_levy_income_cents: int | None = None,
        fallback_closing_cents: int,
) -> int:
    """
    Materialise the dashboard-safe fund balance for analytics facts.

    `annual_levies.*.closing_balance` can be a levy-year projection. For the
    current/as-of dashboard graph, prefer an explicitly imported actual balance
    from bank/trust reconciliation fields. If no actual balance exists, derive
    it from prior year-end opening plus YTD income less YTD expenses. Only
    historical/legacy rows with no actual signal fall back to stored closing.
    """
    for key in (
        "current_balance_cents",
        "bank_balance_cents",
        "last_reconciled_balance_cents",
        "closing_balance_actual_cents",
    ):
        value = fund.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue

    for key in ("current_balance", "bank_balance", "last_reconciled_balance", "closing_balance_actual"):
        cents = _money_to_cents(fund.get(key))
        if cents is not None:
            return cents

    opening = _money_to_cents(fund.get("opening_balance"))
    income = actual_levy_income_cents
    if income is None:
        income = _money_to_cents(fund.get("levy_income") or fund.get("total_income"))
    expenses = _money_to_cents(fund.get("total_expenses") or fund.get("total_spent"))
    if opening is not None and income is not None and expenses is not None:
        return opening + income - expenses

    return fallback_closing_cents


def _financial_balance_period_date(financial_year: str) -> date:
    """Generated function header.

    Function: _financial_balance_period_date
    Path: backend/services/bi_etl_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        fy_int = int(financial_year[:4])
    except (TypeError, ValueError):
        return date.today()
    return date.today() if fy_int == date.today().year else date(fy_int, 12, 31)


def _year_int(year: Any) -> int | None:
    """Generated function header.

    Function: _year_int
    Path: backend/services/bi_etl_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return int(str(year)[:4])
    except (TypeError, ValueError):
        return None


async def _resolve_current_levy_year(building_id: str) -> int:
    """Resolve the current levy year for ETL future-year filtering.

    This mirrors the finance router's default-year rule without importing the
    router into the service layer. If settings are unavailable, calendar year is
    the conservative cap: it still prevents a not-yet-started next year from
    being materialised into consolidated BI facts.
    """
    try:
        from services.settings_service import get_general_settings
        settings_doc = await get_general_settings(building_id, {"_id": 0, "financial_year_start_month": 1})
        fy_start_month = int((settings_doc or {}).get("financial_year_start_month") or 1)
    except Exception as exc:
        logger.warning(
            "etl_fact_financial_balance: could not resolve financial_year_start_month "
            "for building=%s; using calendar-year cap: %s",
            building_id,
            exc,
        )
        fy_start_month = 1
    now = datetime.now(timezone.utc)
    return now.year if (fy_start_month <= 1 or now.month >= fy_start_month) else now.year - 1


async def etl_fact_financial_balance(building_id: str) -> int:
    """Upsert analytics.fact_financial_balance from annual_levies.

    For current-year rows, closing_balance_cents is the actual/as-of balance
    used by dashboards, not the projected levy-year closing balance.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    current_levy_year = await _resolve_current_levy_year(building_id)

    cursor = db.annual_levies.find(
        {
            "building_id": building_id,
            "year": {"$lte": str(current_levy_year)},
            "is_test_data": {"$ne": True},
        },
        {"year": 1, "admin_fund": 1, "sinking_fund": 1, "_id": 1}
    )
    levy_docs = await cursor.to_list(20)
    paid_rows = await db.unit_levy_ledger.aggregate([
        {"$match": {
            "building_id": building_id,
            "year": {"$lte": str(current_levy_year)},
            "is_test_data": {"$ne": True},
        }},
        {"$group": {
            "_id": "$year",
            "admin_paid": {"$sum": "$admin_paid"},
            "sinking_paid": {"$sum": "$sinking_paid"},
        }},
    ]).to_list(100)
    paid_by_year = {
        str(row.get("_id")): {
            "admin": _money_to_cents(row.get("admin_paid")) or 0,
            "sinking": _money_to_cents(row.get("sinking_paid")) or 0,
        }
        for row in paid_rows
    }

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_financial_balance", scheme_id)
        try:
            for doc in levy_docs:
                fy = str(doc.get("year", ""))
                for fund_key, fund_type in [("admin_fund", "admin"), ("sinking_fund", "sinking")]:
                    fund = doc.get(fund_key) or {}
                    projected_closing = _money_to_cents(fund.get("closing_balance")) or 0
                    opening = _money_to_cents(fund.get("opening_balance")) or 0
                    # Use ledger-paid amounts for actual YTD levy collection.
                    # annual_levies.levy_income has been imported inconsistently
                    # across sources and can contain proposed annual income; the
                    # unit ledger is the consolidated collection record used by
                    # the dashboard collection-rate path.
                    income = paid_by_year.get(fy, {}).get(fund_type)
                    if income is None:
                        income = _money_to_cents(fund.get("levy_income") or fund.get("total_income")) or 0
                    expenses = _money_to_cents(fund.get("total_expenses") or fund.get("total_spent")) or 0
                    closing = _fund_actual_balance_cents(
                        fund,
                        actual_levy_income_cents=income,
                        fallback_closing_cents=projected_closing,
                    )
                    period_date = _financial_balance_period_date(fy)

                    result = await session.execute(
                        text("""
                            INSERT INTO analytics.fact_financial_balance (
                                tenant_id, scheme_id, period_date, financial_year,
                                fund_type, opening_balance_cents, levy_income_cents,
                                expenses_cents, closing_balance_cents,
                                source_system, source_collection, source_id, ingested_at
                            ) VALUES (
                                CAST(:tid AS UUID), CAST(:sid AS UUID),
                                :period_date, :fy,
                                :ft, :open, :income, :expenses, :close,
                                'mongo', 'annual_levies', :src_id, now()
                            )
                            ON CONFLICT DO NOTHING
                            RETURNING fact_id
                        """),
                        {
                            "tid": tenant_id, "sid": scheme_id,
                            "period_date": period_date, "fy": fy, "ft": fund_type,
                            "open": opening, "income": income,
                            "expenses": expenses, "close": closing,
                            "src_id": str(doc.get("_id", "")),
                        },
                    )
                    if result.rowcount:
                        inserted += 1
                    else:
                        await session.execute(
                            text("""
                                UPDATE analytics.fact_financial_balance
                                SET closing_balance_cents = :close,
                                    opening_balance_cents = :open,
                                    levy_income_cents = :income,
                                    expenses_cents = :expenses,
                                    period_date = :period_date,
                                    ingested_at = now()
                                WHERE scheme_id = CAST(:sid AS UUID)
                                  AND financial_year = :fy AND fund_type = :ft
                            """),
                            {"sid": scheme_id, "fy": fy, "ft": fund_type,
                             "period_date": period_date, "open": opening,
                             "close": closing, "income": income, "expenses": expenses},
                        )
                        updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_work_order — from work_orders + maintenance_requests (Mongo)
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_work_order(building_id: str, since_days: int = 30) -> int:
    """Incremental upsert of fact_work_order from MongoDB work_orders."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    fy = str(datetime.now().year)

    cursor = db.work_orders.find(
        {
            "building_id": building_id,
            "updated_at": {"$gte": cutoff},
            "is_test_data": {"$ne": True},
        },
        {
            "category": 1, "subcategory": 1, "priority": 1, "status": 1,
            "created_at": 1, "completed_at": 1, "cost": 1, "budget": 1,
            "lot_number": 1, "is_ppm": 1, "is_emergency": 1, "_id": 1,
        }
    )
    rows = await cursor.to_list(2000)

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_work_order", scheme_id)
        try:
            for row in rows:
                wo_id = str(row.get("_id", ""))
                created = row.get("created_at")
                completed = row.get("completed_at")
                reported_date = str(created)[:10] if created else None
                completed_date = str(completed)[:10] if completed else None
                days_to_complete = None
                if created and completed:
                    try:
                        c1 = datetime.fromisoformat(str(created)[:19])
                        c2 = datetime.fromisoformat(str(completed)[:19])
                        days_to_complete = (c2 - c1).days
                    except Exception:
                        pass

                actual_cost_cents = int(round(float(row.get("cost") or 0) * 100))
                budget_cents = int(round(float(row.get("budget") or 0) * 100))

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_work_order (
                            tenant_id, scheme_id, work_order_id, category, subcategory,
                            priority, status, reported_date, completed_date,
                            days_to_complete, actual_cost_cents, approved_budget_cents,
                            lot_number, is_ppm, is_emergency, financial_year,
                            source_system, source_collection, source_id, ingested_at
                        ) VALUES (
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :wo_id, :cat, :subcat,
                            :priority, :status, CAST(:rep AS DATE), CAST(:comp AS DATE),
                            :days, :actual, :budget,
                            :lot, :ppm, :emerg, :fy,
                            'mongo', 'work_orders', :wo_id, now()
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id,
                        "wo_id": wo_id,
                        "cat": row.get("category") or "other",
                        "subcat": row.get("subcategory"),
                        "priority": row.get("priority") or "normal",
                        "status": row.get("status") or "unknown",
                        "rep": reported_date, "comp": completed_date,
                        "days": days_to_complete,
                        "actual": actual_cost_cents, "budget": budget_cents,
                        "lot": row.get("lot_number"),
                        "ppm": bool(row.get("is_ppm")),
                        "emerg": bool(row.get("is_emergency")),
                        "fy": fy,
                    },
                )
                if result.rowcount:
                    inserted += 1
                else:
                    await session.execute(
                        text("""
                            UPDATE analytics.fact_work_order
                            SET status = :status, completed_date = CAST(:comp AS DATE),
                                days_to_complete = :days, actual_cost_cents = :actual,
                                ingested_at = now()
                            WHERE scheme_id = CAST(:sid AS UUID) AND work_order_id = :wo_id
                        """),
                        {
                            "sid": scheme_id, "wo_id": wo_id,
                            "status": row.get("status") or "unknown",
                            "comp": completed_date, "days": days_to_complete,
                            "actual": actual_cost_cents,
                        },
                    )
                    updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            logger.info("etl_fact_work_order: building=%s inserted=%d updated=%d", building_id, inserted, updated)
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_compliance_event — from compliance_registers + compliance_items (Mongo/PG)
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_compliance_event(building_id: str) -> int:
    """Upsert analytics.fact_compliance_event from compliance schema."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme_id, tenant_id = await _resolve(building_id)

    # Try Postgres compliance schema first
    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_compliance_event", scheme_id)
        try:
            result = await session.execute(
                text("""
                    INSERT INTO analytics.fact_compliance_event (
                        tenant_id, scheme_id, compliance_register_id, compliance_item_id,
                        register_type, item_title, event_type, event_date, due_date,
                        days_overdue, rag_status,
                        source_system, source_table, source_id, ingested_at
                    )
                    SELECT
                        cr.tenant_id,
                        cr.scheme_id,
                        cr.register_id::text,
                        ci.item_id::text,
                        cr.register_type,
                        ci.title,
                        CASE
                            WHEN ci.status = 'completed' THEN 'completed'
                            WHEN ci.status = 'overdue' THEN 'overdue'
                            ELSE 'due'
                        END,
                        COALESCE(ci.updated_at, now())::date,
                        ci.due_date,
                        CASE
                            WHEN ci.due_date < CURRENT_DATE AND ci.status != 'completed'
                            THEN (CURRENT_DATE - ci.due_date)
                            ELSE NULL
                        END,
                        CASE
                            WHEN ci.status = 'completed' THEN 'green'
                            WHEN ci.due_date < CURRENT_DATE THEN 'red'
                            WHEN ci.due_date <= CURRENT_DATE + 30 THEN 'amber'
                            ELSE 'green'
                        END,
                        'postgres', 'compliance.compliance_items', ci.item_id::text, now()
                    FROM compliance.compliance_items ci
                    JOIN compliance.compliance_registers cr ON cr.register_id = ci.register_id
                    WHERE cr.scheme_id = CAST(:sid AS UUID)
                      AND COALESCE(ci.is_test_data, FALSE) = FALSE
                    ON CONFLICT DO NOTHING
                    RETURNING fact_id
                """),
                {"sid": scheme_id},
            )
            inserted = result.rowcount or 0
            await _log_etl_done(session, run_id, inserted=inserted, updated=0)
            await session.commit()
            return inserted
        except Exception as e:
            # Fall back: log as no-op rather than crashing entire ETL chain
            logger.warning("etl_fact_compliance_event: PG compliance unavailable (%s), skipping", e)
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# fact_utility_bill — from water_bills + council_rates (Mongo/PG)
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_utility_bill(building_id: str) -> int:
    """Upsert analytics.fact_utility_bill from finance.utility_bills + water/council Mongo."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    inserted = 0

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_utility_bill", scheme_id)
        try:
            # Mongo water bills
            cursor = db.water_bills.find(
                {"building_id": building_id, "is_test_data": {"$ne": True}},
                {"billing_period_start": 1, "billing_period_end": 1,
                 "total_amount": 1, "usage_kl": 1, "provider_name": 1,
                 "lot_number": 1, "payment_status": 1, "_id": 1}
            )
            water_docs = await cursor.to_list(500)

            for doc in water_docs:
                amount_cents = int(round(float(doc.get("total_amount") or 0) * 100))
                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_utility_bill (
                            tenant_id, scheme_id, utility_type, provider_name,
                            billing_period_start, billing_period_end,
                            total_amount_cents, usage_quantity, usage_unit,
                            payment_status, lot_number,
                            source_system, source_collection, source_id, ingested_at
                        ) VALUES (
                            CAST(:tid AS UUID), CAST(:sid AS UUID), 'water', :prov,
                            CAST(:ps AS DATE), CAST(:pe AS DATE),
                            :amt, :usage, 'kL',
                            :pay_status, :lot,
                            'mongo', 'water_bills', :src_id, now()
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id,
                        "prov": doc.get("provider_name"),
                        "ps": str(doc.get("billing_period_start", ""))[:10],
                        "pe": str(doc.get("billing_period_end", ""))[:10],
                        "amt": amount_cents,
                        "usage": doc.get("usage_kl"),
                        "pay_status": doc.get("payment_status"),
                        "lot": doc.get("lot_number"),
                        "src_id": str(doc.get("_id", "")),
                    },
                )
                if result.rowcount:
                    inserted += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=0)
            await session.commit()
            return inserted
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_building_health_snapshot — nightly computation
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_building_health_snapshot(building_id: str) -> int:
    """Nightly: compute and snapshot building health scores."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    snapshot_date = date.today()

    # Compute health components
    try:
        # Financial score: levy collection rate
        fy = str(datetime.now().year)
        ledger_cursor = db.unit_levy_ledger.find(
            {"building_id": building_id, "year": fy, "is_test_data": {"$ne": True}},
            {"total_levied": 1, "total_paid": 1, "net_balance": 1}
        )
        ledger = await ledger_cursor.to_list(500)
        total_levied = sum(float(r.get("total_levied") or 0) for r in ledger)
        total_paid = sum(float(r.get("total_paid") or 0) for r in ledger)
        total_levied_cents = int(round(total_levied * 100))
        total_paid_cents = int(round(total_paid * 100))
        # Store both shapes deliberately: analytics.levy_collection_rate is a
        # 0-1 fraction, while financial_score is the clamped 0-100 score
        # component. Do not replace this with quarterly_collection_rate(), which
        # is an unclamped display percentage and can legitimately exceed 100.
        collection_rate = float(levy_collection_fraction(
            paid_cents=total_paid_cents,
            levied_cents=total_levied_cents,
        ))
        financial_score = float(levy_collection_health_score(
            paid_cents=total_paid_cents,
            levied_cents=total_levied_cents,
        ))

        # Gross outstanding from the authoritative net_balance (never balance_owing),
        # per-unit non-negative, no netting across units — GAP-FIN-040.
        total_arrears_cents = int(sum(max(0.0, float(r.get("net_balance") or 0)) * 100 for r in ledger))
        lots_in_arrears = sum(1 for r in ledger if float(r.get("net_balance") or 0) > 0)

        # Maintenance score: open work orders vs total
        open_wos = await db.work_orders.count_documents(
            {"building_id": building_id, "status": {"$in": ["open", "in_progress"]}, "is_test_data": {"$ne": True}}
        )
        total_wos = await db.work_orders.count_documents(
            {"building_id": building_id, "is_test_data": {"$ne": True}}
        )
        maintenance_score = max(0, 100 - (open_wos / max(total_wos, 1)) * 100)

        # Compliance score: from compliance_registers
        compliance_cursor = db.compliance_registers.find(
            {"building_id": building_id, "is_test_data": {"$ne": True}},
            {"status": 1}
        )
        compliance_docs = await compliance_cursor.to_list(200)
        total_comp = len(compliance_docs) or 1
        overdue_comp = sum(1 for c in compliance_docs if c.get("status") == "overdue")
        compliance_score = max(0, 100 - (overdue_comp / total_comp) * 100)

        overall_score = (financial_score * 0.4 + maintenance_score * 0.3 + compliance_score * 0.3)
        health_label = (
            "critical" if overall_score < 40 else
            "at_risk" if overall_score < 60 else
            "fair" if overall_score < 75 else
            "good" if overall_score < 90 else
            "excellent"
        )

    except Exception as e:
        logger.warning("etl_fact_building_health_snapshot: score computation failed (%s)", e)
        return 0

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_building_health_snapshot", scheme_id)
        try:
            result = await session.execute(
                text("""
                    INSERT INTO analytics.fact_building_health_snapshot (
                        tenant_id, scheme_id, snapshot_date, overall_score,
                        financial_score, maintenance_score, compliance_score,
                        levy_collection_rate, arrears_count, arrears_total_cents,
                        overdue_compliance_count, open_work_orders, health_label,
                        source_system, source_collection, ingested_at
                    ) VALUES (
                        CAST(:tid AS UUID), CAST(:sid AS UUID), :snap_date, :overall,
                        :financial, :maintenance, :compliance,
                        :coll_rate, :arr_count, :arr_cents,
                        :overdue_comp, :open_wo, :label,
                        'computed', 'unit_levy_ledger+work_orders+compliance_registers', now()
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING fact_id
                """),
                {
                    "tid": tenant_id, "sid": scheme_id,
                    "snap_date": snapshot_date,
                    "overall": round(overall_score, 2),
                    "financial": round(financial_score, 2),
                    "maintenance": round(maintenance_score, 2),
                    "compliance": round(compliance_score, 2),
                    "coll_rate": round(collection_rate, 4),
                    "arr_count": lots_in_arrears,
                    "arr_cents": total_arrears_cents,
                    "overdue_comp": overdue_comp,
                    "open_wo": open_wos,
                    "label": health_label,
                },
            )
            inserted = result.rowcount or 0
            await _log_etl_done(session, run_id, inserted=inserted, updated=0)
            await session.commit()
            logger.info(
                "etl_fact_building_health_snapshot: building=%s date=%s score=%.1f label=%s",
                building_id, snapshot_date, overall_score, health_label,
            )
            return inserted
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_smart_request — from workflow_requests (Mongo)
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_smart_request(building_id: str, since_days: int = 30) -> int:
    """Incremental upsert of fact_smart_request from workflow_requests."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    cursor = db.workflow_requests.find(
        {
            "building_id": building_id,
            "created_at": {"$gte": cutoff},
            "is_test_data": {"$ne": True},
        },
        {"category": 1, "subcategory": 1, "status": 1, "created_at": 1,
         "resolved_at": 1, "lot_number": 1, "_id": 1}
    )
    rows = await cursor.to_list(2000)

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_smart_request", scheme_id)
        try:
            for row in rows:
                req_id = str(row.get("_id", ""))
                created = row.get("created_at")
                resolved = row.get("resolved_at")
                submitted_date = str(created)[:10] if created else str(date.today())
                resolved_date = str(resolved)[:10] if resolved else None
                days_to_resolve = None
                if created and resolved:
                    try:
                        d1 = datetime.fromisoformat(str(created)[:19])
                        d2 = datetime.fromisoformat(str(resolved)[:19])
                        days_to_resolve = (d2 - d1).days
                    except Exception:
                        pass

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_smart_request (
                            tenant_id, scheme_id, request_id, category, subcategory,
                            submitted_date, resolved_date, days_to_resolve, status, lot_number,
                            source_system, source_collection, source_id, ingested_at
                        ) VALUES (
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :req_id, :cat, :subcat,
                            CAST(:sub_date AS DATE), CAST(:res_date AS DATE), :days, :status, :lot,
                            'mongo', 'workflow_requests', :req_id, now()
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id,
                        "req_id": req_id,
                        "cat": row.get("category") or "other",
                        "subcat": row.get("subcategory"),
                        "sub_date": submitted_date,
                        "res_date": resolved_date,
                        "days": days_to_resolve,
                        "status": row.get("status") or "open",
                        "lot": row.get("lot_number"),
                    },
                )
                if result.rowcount:
                    inserted += 1
                else:
                    await session.execute(
                        text("""
                            UPDATE analytics.fact_smart_request
                            SET status = :status, resolved_date = CAST(:res_date AS DATE),
                                days_to_resolve = :days, ingested_at = now()
                            WHERE scheme_id = CAST(:sid AS UUID) AND request_id = :req_id
                        """),
                        {"sid": scheme_id, "req_id": req_id,
                         "status": row.get("status") or "open",
                         "res_date": resolved_date, "days": days_to_resolve},
                    )
                    updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_levy_payment — payment receipts from Mongo levy_payments collection
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_levy_payment(building_id: str, since_days: int = 60) -> int:
    """ETL levy payment receipts into analytics.fact_levy_payment.

    Source (transitional): MongoDB levy_payments collection.
    Grain: one row per payment allocation (payment × lot × quarter).
    Idempotent: skips rows already ingested for the same source_id.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cursor = db.levy_payments.find(
        {"building_id": building_id, "created_at": {"$gte": cutoff}},
        {
            "payment_id": 1, "lot_number": 1, "amount": 1,
            "received_date": 1, "financial_year": 1, "quarter": 1,
            "payment_method": 1, "is_test_data": 1,
        },
    )
    rows = await cursor.to_list(2000)

    if not rows:
        return 0

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_levy_payment", scheme_id)
        try:
            for row in rows:
                payment_id = str(row.get("payment_id") or row.get("_id", ""))
                lot_number = str(row.get("lot_number", ""))
                amount_cents = int(round(float(row.get("amount") or 0) * 100))
                received_raw = row.get("received_date")
                received_on = received_raw.date() if hasattr(received_raw, "date") else None
                fy = str(row.get("financial_year") or "")
                method = row.get("payment_method") or "unknown"
                is_test = bool(row.get("is_test_data", False))

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_levy_payment (
                            tenant_id, scheme_id, receipt_id, allocation_id, lot_number,
                            allocated_cents, received_on, financial_year,
                            channel, fund_type, is_test_data,
                            source_system, source_collection, source_id, ingested_at
                        )
                        SELECT
                            CAST(:tid AS UUID), CAST(:sid AS UUID), NULL, NULL, :lot,
                            :cents, CAST(:recv AS DATE), :fy,
                            :method, :fund_type, :test,
                            'mongo', 'levy_payments', :pid, now()
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM analytics.fact_levy_payment fp
                            WHERE fp.scheme_id = CAST(:sid AS UUID)
                              AND fp.source_collection = 'levy_payments'
                              AND fp.source_id = :pid
                        )
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id, "pid": payment_id, "lot": lot_number,
                        "cents": amount_cents, "recv": received_on,
                        "fy": fy, "method": method, "fund_type": "admin", "test": is_test,
                    },
                )
                if result.rowcount:
                    inserted += 1
                else:
                    updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_occupancy_snapshot — lot occupancy type from Mongo units collection
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_occupancy_snapshot(building_id: str) -> int:
    """ETL occupancy snapshot from units collection.

    Source (transitional): MongoDB units collection (occupancy_type field).
    Grain: one row per lot per snapshot date (today).
    Idempotent: skips rows already ingested for same lot and snapshot date.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)
    snapshot_date = date.today()

    cursor = db.units.find(
        {"building_id": building_id},
        {
            "lot_number": 1, "unit_number": 1,
            "occupancy_type": 1, "owner_occupied": 1,
            "tenanted": 1, "is_vacant": 1, "is_test_data": 1,
        },
    )
    rows = await cursor.to_list(1000)

    if not rows:
        return 0

    def _classify(row: dict) -> str:
        """Generated function header.

        Function: _classify
        Path: backend/services/bi_etl_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if row.get("is_vacant"):
            return "vacant"
        if row.get("tenanted"):
            return "tenanted"
        ot = str(row.get("occupancy_type") or "").lower()
        if ot in ("owner_occupied", "owner-occupied"):
            return "owner_occupied"
        if ot in ("tenanted", "rented"):
            return "tenanted"
        if ot == "investor":
            return "investor"
        if row.get("owner_occupied"):
            return "owner_occupied"
        return "unknown"

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_occupancy_snapshot", scheme_id)
        try:
            for row in rows:
                lot_number = str(row.get("lot_number") or row.get("unit_number") or "")
                occupancy_type = _classify(row)
                is_test = bool(row.get("is_test_data", False))

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_occupancy_snapshot (
                            tenant_id, scheme_id, lot_number, snapshot_date,
                            occupancy_type, has_active_tenancy, is_test_data,
                            source_system, source_collection, ingested_at
                        )
                        SELECT
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :lot, CAST(:snap AS DATE),
                            :occ, :has_tenancy, :test,
                            'mongo', 'units', now()
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM analytics.fact_occupancy_snapshot fos
                            WHERE fos.scheme_id = CAST(:sid AS UUID)
                              AND fos.lot_number = :lot
                              AND fos.snapshot_date = CAST(:snap AS DATE)
                        )
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id, "lot": lot_number,
                        "snap": snapshot_date, "occ": occupancy_type,
                        "has_tenancy": occupancy_type == "tenanted",
                        "test": is_test,
                    },
                )
                if result.rowcount:
                    inserted += 1
                else:
                    updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_capex_plan — capital works plan from sinking_fund_plan collection
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_capex_plan(building_id: str) -> int:
    """ETL capital works plan from sinking_fund_plan collection.

    Source (transitional): MongoDB sinking_fund_plan → capital_schedule items.
    Grain: one row per planned capital item per year.
    Idempotent: skips rows already ingested for same plan_item/year.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)

    plan_doc = await db.sinking_fund_plan.find_one({"building_id": building_id})
    if not plan_doc:
        return 0

    schedule = plan_doc.get("capital_schedule") or plan_doc.get("capital_replacement_schedule") or []
    if not schedule:
        return 0

    inserted = updated = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_capex_plan", scheme_id)
        try:
            for item in schedule:
                plan_year = str(item.get("year") or "")
                item_ref = str(item.get("item_id") or item.get("asset_id") or item.get("description", ""))[:100]
                description = str(item.get("description") or item.get("asset_name") or "")
                planned_cents = int(round(float(item.get("planned_cost") or item.get("amount") or 0) * 100))
                category = str(item.get("category") or "other")
                fund_type = str(item.get("fund_type") or "capital_works")
                useful_life_years = item.get("useful_life_years")
                priority = item.get("priority")
                status = item.get("status")
                is_test = bool(plan_doc.get("is_test_data", False))

                if not plan_year or not item_ref:
                    continue
                try:
                    planned_year = int(plan_year)
                except (TypeError, ValueError):
                    continue

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_capex_plan (
                            tenant_id, scheme_id, plan_item_id, asset_name,
                            asset_category, planned_year, planned_cost_cents,
                            fund_type, useful_life_years, priority, status, is_test_data,
                            source_system, source_collection, ingested_at
                        )
                        SELECT
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :ref, :asset_name,
                            :cat, :planned_year, :plan,
                            :fund_type, :useful_life_years, :priority, :status, :test,
                            'mongo', 'sinking_fund_plan', now()
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM analytics.fact_capex_plan fcp
                            WHERE fcp.scheme_id = CAST(:sid AS UUID)
                              AND fcp.plan_item_id = :ref
                              AND fcp.planned_year = :planned_year
                        )
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id, "ref": item_ref,
                        "asset_name": description[:500] or item_ref, "cat": category,
                        "planned_year": planned_year, "plan": planned_cents,
                        "fund_type": fund_type, "useful_life_years": useful_life_years,
                        "priority": priority, "status": status, "test": is_test,
                    },
                )
                if result.rowcount:
                    inserted += 1
                else:
                    updated += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=updated)
            await session.commit()
            return inserted + updated
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# fact_ownership_transfer — ownership changes from ownership_periods collection
# ─────────────────────────────────────────────────────────────────────────────

async def etl_fact_ownership_transfer(building_id: str, since_days: int = 180) -> int:
    """ETL ownership transfer events from ownership_periods collection.

    Source (transitional): MongoDB ownership_periods — records lot ownership history.
    Source (preferred): core.ownership_periods (Postgres) when cutover complete.
    Grain: one row per transfer event (period start = transfer date).
    Idempotent: skips rows already ingested for same lot and transfer date.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from database import db

    scheme_id, tenant_id = await _resolve(building_id)

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cursor = db.ownership_periods.find(
        {"building_id": building_id, "start_date": {"$gte": cutoff}},
        {
            "lot_number": 1, "owner_name": 1, "start_date": 1,
            "end_date": 1, "transfer_type": 1, "is_test_data": 1,
        },
    )
    rows = await cursor.to_list(500)

    if not rows:
        return 0

    inserted = 0
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        run_id = await _log_etl_start(session, "analytics.fact_ownership_transfer", scheme_id)
        try:
            for row in rows:
                lot_number = str(row.get("lot_number") or "")
                transfer_date_raw = row.get("start_date")
                transfer_date = (
                    transfer_date_raw.date()
                    if hasattr(transfer_date_raw, "date")
                    else None
                )
                is_test = bool(row.get("is_test_data", False))

                if not lot_number or not transfer_date:
                    continue

                result = await session.execute(
                    text("""
                        INSERT INTO analytics.fact_ownership_transfer (
                            tenant_id, scheme_id, lot_number, transfer_date,
                            is_test_data, source_system, source_collection, ingested_at
                        )
                        SELECT
                            CAST(:tid AS UUID), CAST(:sid AS UUID), :lot, CAST(:dt AS DATE),
                            :test, 'mongo', 'ownership_periods', now()
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM analytics.fact_ownership_transfer fot
                            WHERE fot.scheme_id = CAST(:sid AS UUID)
                              AND fot.lot_number = :lot
                              AND fot.transfer_date = CAST(:dt AS DATE)
                        )
                        RETURNING fact_id
                    """),
                    {
                        "tid": tenant_id, "sid": scheme_id, "lot": lot_number,
                        "dt": transfer_date, "test": is_test,
                    },
                )
                if result.rowcount:
                    inserted += 1

            await _log_etl_done(session, run_id, inserted=inserted, updated=0)
            await session.commit()
            return inserted
        except Exception as e:
            await _log_etl_error(session, run_id, str(e))
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Nightly full ETL run
# ─────────────────────────────────────────────────────────────────────────────

async def run_nightly_etl(building_id: str) -> dict[str, Any]:
    """Run all nightly ETL jobs for one building.

    Called by cron scheduler at 02:00 AEST or via admin endpoint.
    Returns a summary dict: {job: rows_affected}.
    """
    logger.info("run_nightly_etl: starting building=%s", building_id)
    results: dict[str, Any] = {}

    jobs = [
        ("dim_building", lambda: etl_dim_building(building_id)),
        ("fact_financial_balance", lambda: etl_fact_financial_balance(building_id)),
        ("fact_levy_charge", lambda: etl_fact_levy_charge(building_id)),
        ("fact_levy_payment", lambda: etl_fact_levy_payment(building_id)),
        ("fact_arrears_snapshot", lambda: etl_fact_arrears_snapshot(building_id)),
        ("fact_work_order", lambda: etl_fact_work_order(building_id, since_days=2)),
        ("fact_compliance_event", lambda: etl_fact_compliance_event(building_id)),
        ("fact_utility_bill", lambda: etl_fact_utility_bill(building_id)),
        ("fact_smart_request", lambda: etl_fact_smart_request(building_id, since_days=2)),
        ("fact_building_health_snapshot", lambda: etl_fact_building_health_snapshot(building_id)),
        ("fact_occupancy_snapshot", lambda: etl_fact_occupancy_snapshot(building_id)),
        ("fact_capex_plan", lambda: etl_fact_capex_plan(building_id)),
        ("fact_ownership_transfer", lambda: etl_fact_ownership_transfer(building_id)),
    ]

    for name, job in jobs:
        try:
            count = await job()
            results[name] = {"status": "ok", "rows": count}
            logger.info("run_nightly_etl: %s completed rows=%d", name, count)
        except Exception as e:
            results[name] = {"status": "error", "error": str(e)}
            logger.error("run_nightly_etl: %s failed: %s", name, e)

    logger.info("run_nightly_etl: completed building=%s results=%s", building_id, results)
    return results


async def run_nightly_etl_all_buildings() -> dict[str, Any]:
    """Run nightly ETL for all active non-demo buildings.

    Called by the scheduler at 02:00 AEST.
    """
    from database import db
    cursor = db.buildings.find(
        {"is_archived": {"$ne": True}},
        {"building_id": 1, "plan_id": 1}
    )
    buildings = await cursor.to_list(100)
    all_results: dict[str, Any] = {}
    for b in buildings:
        bid = b.get("building_id") or b.get("plan_id")
        if not bid:
            continue
        try:
            all_results[bid] = await run_nightly_etl(bid)
        except Exception as e:
            all_results[bid] = {"status": "error", "error": str(e)}
    return all_results
