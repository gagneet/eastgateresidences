# @featuretrace:financial_core — PostgreSQL-backed analytics read service (sinking fund, levy benchmarks).
# Layer: service
# Data flow: routers/analytics.py → this service → finance.levy_items/levy_runs/journal_entries (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/levy_generation_service.py
# Table: finance.levy_items, finance.levy_runs, finance.journal_lines, finance.journal_entries
"""PostgreSQL-backed analytics read service.

Provides PG-first implementations for finance analytics endpoints that are
gated behind the ``financial_pg_reads_enabled`` toggle and the
``governance_read_pg_enabled`` toggle.

Each public function:
  - Resolves scheme_id from building_id via identity_repo
  - Executes a Postgres query with RLS-enforced tenant context
  - Returns the same response shape as the existing MongoDB path so the
    calling endpoint needs zero response-model changes
  - Raises RuntimeError when PG data is unavailable (caller falls back to
    Mongo)

Toggle flow (callers handle this):
    financial_pg_reads_enabled   → sinking_fund_forecast, levy_benchmarks,
                                   budget_variance, expense_breakdown
    governance_read_pg_enabled   → compliance_summary, compliance_details
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def _resolve_scheme(building_id: str):
    """Return the core.schemes row for this building, or raise RuntimeError."""
    from db_postgres.repos import identity_repo as _id_repo
    scheme = await _id_repo.get_scheme_by_number(building_id)
    if not scheme:
        raise RuntimeError(f"No Postgres scheme for building_id={building_id!r}")
    return scheme


# ─────────────────────────────────────────────────────────────────────────────
# Sinking-fund forecast (financial_pg_reads_enabled)
# ─────────────────────────────────────────────────────────────────────────────

async def get_sinking_fund_forecast_pg(building_id: str, years: int) -> dict:
    """Read sinking-fund balance from Postgres finance tables.

    Current balance: derived from the net sum of posted ``finance.journal_lines``
    entries whose parent ``finance.journal_entries`` are linked to a sinking or
    capital-works fund (``finance.funds``).

    Per-year projections: built from levy contribution totals in
    ``finance.levy_runs`` + ``finance.levy_items`` grouped by financial year.

    Returns the same dict shape as analytics.py::get_sinking_fund_forecast:
        {
            "current_balance": float,
            "data_available": bool,
            "projection": [{"year": int, "balance": float, "is_actual": bool, "contributions": float}, ...],
            "source": "postgres"
        }
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme = await _resolve_scheme(building_id)
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        # Current balance: sum of all posted journal_lines for sinking/capital fund
        balance_row = await session.execute(
            text("""
                SELECT
                    SUM(CASE WHEN jl.direction = 'credit' THEN jl.amount_cents
                             ELSE -jl.amount_cents END) AS net_cents
                FROM finance.journal_lines jl
                JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
                JOIN finance.funds f ON f.fund_id = je.fund_id
                WHERE je.scheme_id = CAST(:sid AS UUID)
                  AND je.status = 'posted'
                  AND f.fund_type IN ('capital_works', 'sinking')
                  AND COALESCE(je.is_test_data, FALSE) = FALSE
            """),
            {"sid": scheme_id},
        )
        row = balance_row.fetchone()
        net_cents = row[0] if row and row[0] is not None else 0
        current_balance = round(float(net_cents) / 100, 2)

        # Per-year closing balances from levy_runs (as actual rows)
        year_rows = await session.execute(
            text("""
                SELECT
                    EXTRACT(YEAR FROM lr.due_date)::int AS fy,
                    SUM(li.principal_cents + li.gst_cents) AS levy_cents
                FROM finance.levy_runs lr
                JOIN finance.levy_items li ON li.levy_run_id = lr.levy_run_id
                JOIN finance.funds f ON f.fund_id = li.fund_id
                WHERE lr.scheme_id = CAST(:sid AS UUID)
                  AND lr.status = 'issued'
                  AND f.fund_type IN ('capital_works', 'sinking')
                GROUP BY EXTRACT(YEAR FROM lr.due_date)
                ORDER BY fy
            """),
            {"sid": scheme_id},
        )
        yearly = {r[0]: round(float(r[1]) / 100, 2) for r in year_rows.fetchall()}

    if not yearly and current_balance == 0.0:
        raise RuntimeError("No sinking fund data available in Postgres")

    current_year = datetime.now().year
    projection = []
    running_balance = current_balance
    for i in range(1, years + 1):
        y = current_year + i
        contrib = yearly.get(y, 0.0)
        running_balance = round(running_balance + contrib, 2)
        # `contributions` mirrors the field name the MongoDB path's endpoint returns
        # (routers/analytics.py's GET /analytics/sinking-fund-forecast, which reshapes
        # forecast_service.get_sinking_fund_projections()'s singular `contribution` into
        # this plural key before responding) — ReserveRunwayChart plots it as a second
        # area series and previously always saw 0 on this Postgres path, since the value
        # was computed for running_balance but never included in the returned row.
        projection.append({
            "year": y,
            "balance": running_balance,
            "is_actual": y in yearly,
            "contributions": contrib,
        })

    return {
        "current_balance": current_balance,
        "data_available": bool(yearly or current_balance > 0),
        "projection": projection,
        "source": "postgres",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Levy benchmarks (financial_pg_reads_enabled)
# ─────────────────────────────────────────────────────────────────────────────

async def get_levy_benchmarks_pg(building_id: str, unit_number: str | None) -> list:
    """Read levy totals from finance.levy_runs + finance.levy_items.

    Returns the same list shape as analytics.py::get_levy_benchmarks.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme = await _resolve_scheme(building_id)
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        levy_rows = await session.execute(
            text("""
                SELECT
                    lr.financial_year,
                    f.fund_type::text,
                    SUM(li.principal_cents + li.gst_cents) AS total_cents
                FROM finance.levy_runs lr
                JOIN finance.levy_items li ON li.levy_run_id = lr.levy_run_id
                JOIN finance.funds f ON f.fund_id = li.fund_id
                WHERE lr.scheme_id = CAST(:sid AS UUID)
                  AND lr.status = 'issued'
                GROUP BY lr.financial_year, f.fund_type
                ORDER BY lr.financial_year
            """),
            {"sid": scheme_id},
        )
        rows = levy_rows.fetchall()

        # Resolve entitlement for personal share calculation
        entitlement = 0.0
        total_entitlement = 0.0
        if unit_number:
            lot_row = await session.execute(
                text("""
                    SELECT entitlement,
                           (SELECT SUM(entitlement) FROM core.lots WHERE scheme_id = CAST(:sid AS UUID)) AS total_ent
                    FROM core.lots
                    WHERE scheme_id = CAST(:sid AS UUID)
                      AND (lot_number = :un OR unit_number = :un) LIMIT 1
                """),
                {"sid": scheme_id, "un": str(unit_number)},
            )
            lot = lot_row.fetchone()
            if lot:
                entitlement = float(lot[0] or 0)
                total_entitlement = float(lot[1] or 0)

    if not rows:
        raise RuntimeError("No levy run data available in Postgres")

    share_pct = (entitlement / total_entitlement) if total_entitlement > 0 else 0.0

    # Group by financial_year
    by_year: dict = {}
    for row in rows:
        fy = str(row[0])
        fund = str(row[1])
        cents = float(row[2] or 0)
        if fy not in by_year:
            by_year[fy] = {"admin": 0.0, "sinking": 0.0, "capital_works": 0.0}
        if fund in ("admin",):
            by_year[fy]["admin"] += cents / 100
        elif fund in ("sinking", "capital_works"):
            by_year[fy]["sinking"] += cents / 100

    results = []
    for fy in sorted(by_year.keys()):
        admin = round(by_year[fy]["admin"], 2)
        sinking = round(by_year[fy]["sinking"], 2)
        total = round(admin + sinking, 2)
        results.append({
            "year": fy,
            "Building": total,
            "ACTMedian": round(total * 0.94, 2),
            "Admin": admin,
            "Sinking": sinking,
            "Personal": round(total * share_pct, 2),
            "PersonalAdmin": round(admin * share_pct, 2),
            "PersonalSinking": round(sinking * share_pct, 2),
            "source": "postgres",
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Budget variance (financial_pg_reads_enabled)
# ─────────────────────────────────────────────────────────────────────────────

async def get_budget_variance_pg(building_id: str, financial_year: str) -> list:
    """Read budget vs actual from finance.journal_lines grouped by GL account.

    Returns the same list shape as analytics.py::get_budget_variance.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme = await _resolve_scheme(building_id)
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        # Actual spend per GL account for the financial year
        actual_rows = await session.execute(
            text("""
                SELECT
                    ga.account_name AS category,
                    f.fund_type::text AS fund,
                    SUM(CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE 0 END) AS actual_cents
                FROM finance.journal_lines jl
                JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
                JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
                JOIN finance.funds f ON f.fund_id = je.fund_id
                JOIN finance.accounting_periods ap ON ap.period_id = je.period_id
                WHERE je.scheme_id = CAST(:sid AS UUID)
                  AND je.status = 'posted'
                  AND ga.account_type = 'expense'
                  AND EXTRACT(YEAR FROM ap.starts_on)::text = :fy
                  AND COALESCE(je.is_test_data, FALSE) = FALSE
                GROUP BY ga.account_name, f.fund_type
                ORDER BY f.fund_type, ga.account_name
            """),
            {"sid": scheme_id, "fy": str(financial_year)},
        )
        rows = actual_rows.fetchall()

    if not rows:
        raise RuntimeError("No journal data available in Postgres for budget variance")

    # Budget amounts per GL account are not yet stored in the Postgres schema
    # (planned for Phase G).  Returning fabricated budgeted=0 / variance_pct=100
    # data would display every expense as 100% over-budget on the dashboard.
    # Instead we raise so the caller's Mongo fallback runs and real budget data
    # from levy_categories is served.  Remove this raise once Phase G seeds
    # budget_lines into finance.gl_account_budgets.
    raise RuntimeError(
        "Budget amounts per GL account not yet available in Postgres (Phase G). "
        "Using MongoDB fallback."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Expense breakdown (financial_pg_reads_enabled)
# ─────────────────────────────────────────────────────────────────────────────

async def get_expense_breakdown_pg(building_id: str, financial_year: str) -> list:
    """Read expense totals by category from Postgres journal_lines.

    Returns a list of {category, fund, amount} dicts matching the existing
    Mongo-backed response from get_expense_breakdown_by_category().
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme = await _resolve_scheme(building_id)
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        rows = await session.execute(
            text("""
                SELECT
                    ga.account_name AS category,
                    f.fund_type::text AS fund,
                    SUM(CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE 0 END) AS amount_cents,
                    SUM(jl.gst_cents) AS gst_cents
                FROM finance.journal_lines jl
                JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
                JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
                JOIN finance.funds f ON f.fund_id = je.fund_id
                JOIN finance.accounting_periods ap ON ap.period_id = je.period_id
                WHERE je.scheme_id = CAST(:sid AS UUID)
                  AND je.status = 'posted'
                  AND ga.account_type = 'expense'
                  AND EXTRACT(YEAR FROM ap.starts_on)::text = :fy
                  AND COALESCE(je.is_test_data, FALSE) = FALSE
                GROUP BY ga.account_name, f.fund_type
                ORDER BY f.fund_type, ga.account_name
            """),
            {"sid": scheme_id, "fy": str(financial_year)},
        )
        result_rows = rows.fetchall()

    if not result_rows:
        raise RuntimeError("No expense data available in Postgres")

    return [
        {
            "category": r[0],
            "fund": r[1],
            "amount": round(float(r[2] or 0) / 100, 2),
            "gst": round(float(r[3] or 0) / 100, 2),
            "source": "postgres",
        }
        for r in result_rows
        if float(r[2] or 0) > 0
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Compliance summary (governance_read_pg_enabled)
# ─────────────────────────────────────────────────────────────────────────────

async def get_compliance_summary_pg(building_id: str) -> dict:
    """Read compliance item counts from compliance.compliance_items (Postgres).

    Returns the same dict shape as analytics.py::get_compliance_summary.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme = await _resolve_scheme(building_id)
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        rows = await session.execute(
            text("""
                SELECT status, COUNT(*) AS cnt
                FROM compliance.compliance_items
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND COALESCE(is_test_data, FALSE) = FALSE
                GROUP BY status
            """),
            {"sid": scheme_id},
        )
        status_counts = {r[0]: int(r[1]) for r in rows.fetchall()}

    total = sum(status_counts.values())
    if total == 0:
        return {"total": 0, "completed": 0, "pending": 0, "overdue": 0,
                "status": "unknown", "label": "No Data", "percentage": 0,
                "source": "postgres"}

    completed = status_counts.get("completed", 0)
    pending = status_counts.get("pending", 0)
    overdue = status_counts.get("overdue", 0)

    if overdue > 0:
        health_status = "danger"
        label = f"{overdue} Overdue"
    elif pending > 0:
        health_status = "warning"
        label = f"{pending} Pending"
    else:
        health_status = "healthy"
        label = "All Clear"

    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "overdue": overdue,
        "status": health_status,
        "label": label,
        "percentage": round((completed / total) * 100),
        "source": "postgres",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Compliance details (governance_read_pg_enabled)
# ─────────────────────────────────────────────────────────────────────────────

async def get_compliance_details_pg(building_id: str) -> list:
    """Read compliance register details from Postgres compliance tables.

    Returns the same list shape as the Mongo-backed compliance_details endpoint.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    scheme = await _resolve_scheme(building_id)
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        rows = await session.execute(
            text("""
                SELECT
                    cr.register_id::text,
                    cr.register_type,
                    cr.title,
                    cr.status,
                    cr.next_due_date,
                    cr.last_completed_date,
                    cr.notes,
                    COUNT(ci.item_id) AS item_count,
                    COUNT(ci.item_id) FILTER (WHERE ci.status = 'completed') AS completed_count,
                    COUNT(ci.item_id) FILTER (WHERE ci.status = 'overdue') AS overdue_count
                FROM compliance.compliance_registers cr
                LEFT JOIN compliance.compliance_items ci
                    ON ci.register_id = cr.register_id
                    AND COALESCE(ci.is_test_data, FALSE) = FALSE
                WHERE cr.scheme_id = CAST(:sid AS UUID)
                  AND COALESCE(cr.is_test_data, FALSE) = FALSE
                GROUP BY cr.register_id, cr.register_type, cr.title, cr.status,
                         cr.next_due_date, cr.last_completed_date, cr.notes
                ORDER BY cr.next_due_date NULLS LAST, cr.register_type
            """),
            {"sid": scheme_id},
        )
        result_rows = rows.fetchall()

    if not result_rows:
        raise RuntimeError("No compliance register data available in Postgres")

    return [
        {
            "id": r[0],
            "type": r[1],
            "title": r[2],
            "status": r[3],
            "next_due_date": str(r[4]) if r[4] else None,
            "last_completed_date": str(r[5]) if r[5] else None,
            "notes": r[6],
            "item_count": int(r[7] or 0),
            "completed_count": int(r[8] or 0),
            "overdue_count": int(r[9] or 0),
            "source": "postgres",
        }
        for r in result_rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Levy-allocation fund totals (shadow comparison for
# /analytics/levy-allocation-breakdown) — integer CENTS
# ─────────────────────────────────────────────────────────────────────────────

async def get_levy_allocation_totals_pg(
    building_id: str, financial_year: str | None
) -> dict | None:
    """Admin/sinking annual levy totals in integer CENTS for the given FY.

    Feeds the shadow comparator for /analytics/levy-allocation-breakdown — it is
    the PG side of the admin_annual / sinking_annual / total_annual figures the
    Mongo route returns (in AUD). Mirrors the aggregate in
    routers/analytics.py::get_levy_allocation_breakdown's PG branch.

    Returns None (⇒ the caller records pg_unavailable) when there is no PG scheme,
    no resolvable financial year, or no levy_items for it.
    """
    from db_postgres.session import async_session_context, set_tenant
    from sqlalchemy import text

    try:
        scheme = await _resolve_scheme(building_id)
    except RuntimeError:
        return None

    sid = str(scheme["scheme_id"])
    tid = str(scheme["tenant_id"])
    async with async_session_context() as session:
        await set_tenant(session, tid)
        _year = str(financial_year) if financial_year else None
        if not _year:
            year_row = await session.execute(
                text(
                    "SELECT financial_year FROM finance.levy_runs "
                    "WHERE scheme_id = CAST(:sid AS UUID) ORDER BY issue_date DESC LIMIT 1"
                ),
                {"sid": sid},
            )
            _year = year_row.scalar()
        if not _year:
            return None
        rows = await session.execute(
            text(
                """
                SELECT
                    f.fund_type::text AS fund_type,
                    COALESCE(SUM(li.principal_cents + li.gst_cents
                                 + li.interest_cents + li.recovery_costs_cents), 0) AS amount_cents
                FROM finance.levy_items li
                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                JOIN finance.funds f ON f.fund_id = li.fund_id
                WHERE li.scheme_id = CAST(:sid AS UUID)
                  AND lr.financial_year = :fy
                GROUP BY f.fund_type
                """
            ),
            {"sid": sid, "fy": str(_year)},
        )
        fund_rows = rows.fetchall()

    if not fund_rows:
        return None

    admin_cents = 0
    sinking_cents = 0
    for r in fund_rows:
        amt = int(r.amount_cents or 0)
        if r.fund_type == "admin":
            admin_cents += amt
        elif r.fund_type in ("sinking", "capital_works"):
            sinking_cents += amt

    return {
        "admin_annual": admin_cents,
        "sinking_annual": sinking_cents,
        "total_annual": admin_cents + sinking_cents,
    }
