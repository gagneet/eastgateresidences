# @featuretrace:financial_integration_v2 — PostgreSQL current-balance finance reads.
# Layer: service
# Data flow: external_api.py → FinancialReadService → finance.* + core.lots (building-scoped).
# Related: backend/services/cutover_config_service.py
#           backend/services/financial_core/adapter.py
"""Postgres-backed financial read models for cutover-safe current-balance APIs."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant

logger = logging.getLogger(__name__)

_AR_ACCOUNT_CODE = "1100"


def _to_aud(cents: int | None) -> float:
    """Generated function header.

    Function: _to_aud
    Path: backend/services/financial_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return round(((cents or 0) / 100), 2)


@dataclass(frozen=True)
class FinancialYearWindow:
    financial_year: str
    starts_on: date
    ends_on: date


class FinancialReadService:
    """Query current-balance financial summaries from the PostgreSQL ledger."""

    # routers/external_api.py, routers/finance.py, and finance_shadow_read_service.py all hold
    # this class as a module-level singleton (`_financial_read_service = FinancialReadService()`),
    # not one instance per request. get_building_finance_pg_dashboard() alone fans out to 4
    # sub-calls that each independently re-derive the FY window for the same building+year, so
    # _get_fy_start_month() (added 2026-08-01) would otherwise re-run its Postgres settings lookup
    # up to 5x per dashboard load, forever, for a value that changes maybe once a year. A short
    # TTL (not an unbounded cache) bounds staleness after a live settings edit to at most this
    # many seconds instead of requiring a backend restart.
    _FY_START_MONTH_CACHE_TTL_SECONDS = 60

    def __init__(self) -> None:
        # Instance-level, not class-level: external_api.py, routers/finance.py, and
        # finance_shadow_read_service.py each hold their own module-level FinancialReadService()
        # singleton — this must not become accidental shared mutable state across those three.
        self._fy_start_month_cache: dict[str, tuple[int, float]] = {}

    async def _resolve_scheme(self, building_id: str) -> dict:
        """Generated function header.

        Function: FinancialReadService._resolve_scheme
        Path: backend/services/financial_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await config_repo.resolve_scheme_context(building_id)
        if scheme is None:
            raise RuntimeError(
                f"No Postgres scheme found for building_id={building_id!r}."
            )
        return scheme

    async def _get_financial_year_window(
            self,
            building_id: str,
            financial_year: str | None = None,
    ) -> FinancialYearWindow | None:
        """Generated function header.

        Function: FinancialReadService._get_financial_year_window
        Path: backend/services/financial_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        query = """
            SELECT financial_year
            FROM finance.levy_runs
            WHERE scheme_id = :scheme_id
        """
        params: dict[str, Any] = {"scheme_id": str(scheme["scheme_id"])}
        if financial_year:
            query += " AND financial_year = :financial_year"
            params["financial_year"] = financial_year
        query += """
            GROUP BY financial_year
            ORDER BY financial_year DESC
            LIMIT 1
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(text(query), params)
            row = result.fetchone()

            # If an exact financial_year match found nothing, the caller's label
            # format may differ from what's stored (e.g. requested "2026" vs a
            # stored "2026-27", or vice versa). Retry matching on the leading
            # calendar year so the SAME logical year still resolves — this can
            # never match a different year (LEFT(...,4) pins the year). This was
            # the silent root cause of building_overview/summary `pg_unavailable`:
            # the exact match missed and the method returned None with no log.
            if row is None and financial_year:
                fy_prefix = str(financial_year).strip()[:4]
                fallback = await session.execute(
                    text(
                        """
                        SELECT financial_year
                        FROM finance.levy_runs
                        WHERE scheme_id = :scheme_id
                          AND LEFT(financial_year, 4) = :fy_prefix
                        GROUP BY financial_year
                        ORDER BY financial_year DESC
                        LIMIT 1
                        """
                    ),
                    {"scheme_id": str(scheme["scheme_id"]), "fy_prefix": fy_prefix},
                )
                row = fallback.fetchone()
                if row is not None:
                    logger.warning(
                        "FY window: requested financial_year=%r not found exactly for "
                        "building=%s; matched on leading year -> stored=%r",
                        financial_year, building_id, row.financial_year,
                    )
                else:
                    # Genuine miss — log the requested value AND the years that DO
                    # exist so a single log line fully explains the pg_unavailable.
                    avail = await session.execute(
                        text(
                            "SELECT DISTINCT financial_year FROM finance.levy_runs "
                            "WHERE scheme_id = :scheme_id"
                        ),
                        {"scheme_id": str(scheme["scheme_id"])},
                    )
                    years = [r.financial_year for r in avail.fetchall()]
                    logger.warning(
                        "FY window: no finance.levy_runs match financial_year=%r for "
                        "building=%s; available=%r", financial_year, building_id, years,
                    )

        if row is None:
            return None

        # starts_on/ends_on used to be MIN(issue_date)/MAX(due_date) across
        # whatever levy_runs happen to exist for the year — fragile in two
        # ways, both confirmed live for East Gate: (1) every levy_run's
        # issue_date == due_date (no per-quarter issuance schedule in this
        # data), which starved get_oc_levy_summary's date-window receipts
        # query to zero (fixed 2026-07-13, see
        # test_financial_read_service_oc_levy_summary.py); (2) MAX(due_date)
        # only covers quarters that have actually been generated so far — for
        # FY2026 with only Q1/Q2 levy_runs existing, ends_on resolved to
        # 2026-06-30, silently excluding any GL activity dated after that
        # (e.g. a payment posted in August) from get_unit_levy_balance's
        # closing_balance/arrears query. This is the same root cause as (1),
        # not yet fixed here. Deriving the window directly from
        # financial_year removes the levy_runs dependency entirely, matching
        # however many quarters actually exist without truncating.
        #
        # The Levy Year is NOT necessarily the calendar year — a strata
        # building's levy period does not have to start 1 January (the
        # Mongo-side `db.settings.financial_year_start_month` already models
        # this per building, default 7 for the AU tax-year convention; East
        # Gate itself is configured to 1). A prior version of this function
        # hardcoded Jan 1 - Dec 31 unconditionally; that only looked correct
        # because East Gate's own FY happens to be calendar-year. Fetch the
        # building's actual FY start month and derive the window from it so
        # this is correct for any building, not just East Gate.
        fy_int = int(str(row.financial_year).split("-")[0])
        fy_start_month = await self._get_fy_start_month(building_id)
        starts_on = date(fy_int, fy_start_month, 1)
        if fy_start_month == 1:
            ends_on = date(fy_int, 12, 31)
        else:
            ends_on = date(fy_int + 1, fy_start_month, 1) - timedelta(days=1)
        return FinancialYearWindow(
            financial_year=str(row.financial_year),
            starts_on=starts_on,
            ends_on=ends_on,
        )

    async def _get_fy_start_month(self, building_id: str) -> int:
        """Building's configured Levy/FY start month (1-12), cached for
        `_FY_START_MONTH_CACHE_TTL_SECONDS` since this instance is a long-lived, per-process
        singleton (see the class docstring note above `__init__`) and this value changes rarely.
        """
        cached = self._fy_start_month_cache.get(building_id)
        if cached is not None:
            value, expires_at = cached
            if time.monotonic() < expires_at:
                return value

        settings_doc = await config_repo.get_building_setting(
            building_id, "general.settings", default=None,
        )
        raw = (settings_doc or {}).get("financial_year_start_month") if isinstance(settings_doc, dict) else None
        try:
            fy_start_month = int(raw) if raw else 1
        except (TypeError, ValueError):
            fy_start_month = 1
        fy_start_month = fy_start_month if 1 <= fy_start_month <= 12 else 1

        self._fy_start_month_cache[building_id] = (
            fy_start_month, time.monotonic() + self._FY_START_MONTH_CACHE_TTL_SECONDS,
        )
        return fy_start_month

    async def _get_lot_id(self, building_id: str, unit_number: str) -> str | None:
        """Generated function header.

        Function: FinancialReadService._get_lot_id
        Path: backend/services/financial_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT lot_id::text
                    FROM core.lots
                    WHERE scheme_id = :scheme_id
                      AND (unit_number = :unit_number OR lot_number = :unit_number)
                    LIMIT 1
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "unit_number": unit_number,
                },
            )
            return result.scalar()

    async def get_unit_levy_balance(
            self,
            *,
            building_id: str,
            unit_number: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Generated function header.

        Function: FinancialReadService.get_unit_levy_balance
        Path: backend/services/financial_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None

        lot_id = await self._get_lot_id(building_id, unit_number)
        if lot_id is None:
            return None

        # scheme must be resolved in this scope — _get_lot_id resolves its own
        # copy locally. Missing this line made every call raise NameError,
        # which silently disabled the Phase D shadow comparison for
        # finance.unit_dashboard_overview (pg_payload=None on every request).
        scheme = await self._resolve_scheme(building_id)

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            opening_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(
                        CASE
                            WHEN jl.direction = 'debit' THEN jl.amount_cents
                            ELSE -jl.amount_cents
                        END
                    ), 0) AS opening_balance_cents
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga
                      ON ga.gl_account_id = jl.gl_account_id
                    WHERE je.scheme_id = :scheme_id
                      AND jl.lot_id = :lot_id
                      AND ga.account_code = :ar_account_code
                      AND je.status = 'posted'
                      AND je.effective_on < :starts_on
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "lot_id": lot_id,
                    "ar_account_code": _AR_ACCOUNT_CODE,
                    "starts_on": window.starts_on,
                },
            )
            levy_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0)
                        AS levied_cents,
                           COALESCE(SUM(li.paid_cents), 0) AS paid_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr
                      ON lr.levy_run_id = li.levy_run_id
                    WHERE li.scheme_id = :scheme_id
                      AND li.lot_id = :lot_id
                      AND lr.financial_year = :financial_year
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "lot_id": lot_id,
                    "financial_year": window.financial_year,
                },
            )
            closing_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(
                        CASE
                            WHEN jl.direction = 'debit' THEN jl.amount_cents
                            ELSE -jl.amount_cents
                        END
                    ), 0) AS closing_balance_cents
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga
                      ON ga.gl_account_id = jl.gl_account_id
                    WHERE je.scheme_id = :scheme_id
                      AND jl.lot_id = :lot_id
                      AND ga.account_code = :ar_account_code
                      AND je.status = 'posted'
                      AND je.effective_on <= :ends_on
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "lot_id": lot_id,
                    "ar_account_code": _AR_ACCOUNT_CODE,
                    "ends_on": window.ends_on,
                },
            )

            opening_cents = int(opening_result.scalar() or 0)
            levy_row = levy_result.fetchone()
            levied_cents = int(levy_row.levied_cents or 0)
            paid_cents = int(levy_row.paid_cents or 0)
            closing_cents = int(closing_result.scalar() or 0)

        return {
            "unit_number": unit_number,
            "financial_year": window.financial_year,
            "opening_balance": _to_aud(opening_cents),
            "levied_amount": _to_aud(levied_cents),
            "paid_amount": _to_aud(paid_cents),
            "closing_balance": _to_aud(closing_cents),
            "arrears": _to_aud(max(0, closing_cents)),
        }

    async def get_unit_levy_balance_list(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> list[dict] | None:
        """Per-unit levy ledger for every lot in the building/year (GAP-FIN-030
        Fix 4 -- Levy Status tab shadow comparator; now also the live PG-serving
        source for finance.arrears_detail and finance.levy_kpi, GAP-FIN-063).

        GAP-FIN-069 (2026-08-18, bulk-query rewrite -- Option 1, the preferred
        long-term fix): previously fanned out via N calls to
        get_unit_levy_balance() (3 queries each), first unbounded then bounded
        by a semaphore as a tactical stopgap (see git history on this method for
        that intermediate version). Now issues exactly 3 bulk `GROUP BY lot_id`
        queries for the whole building/year -- one connection, not up to N -- the
        same pattern lot_true_balance.py::compute_lot_true_balances() already
        uses. Each bulk query is the SAME WHERE-clause shape as
        get_unit_levy_balance()'s three queries (opening/levy/closing), just
        grouped per lot instead of filtered to one -- get_unit_levy_balance()
        itself is UNCHANGED and still the single-unit path other live callers
        use (routers/finance.py, routers/external_api.py,
        finance_shadow_read_service.py), so this rewrite carries no risk to
        those. Per CLAUDE.md's Financial Formula Verification rule and this
        ticket's own explicit caution (a prior hand-rolled bulk rewrite of a
        per-unit query caused a real incident, GAP-FIN-030's addendum), this was
        verified against East Gate before landing: byte-identical output
        (same lot set, same opening/levied/paid/closing/arrears cents) to the
        prior per-unit version across 2021-2025, plus the live 6-year
        Mongo-vs-Postgres reconciliation GAP-FIN-069's tactical fix already ran
        (0 diffs, unchanged) re-run clean against this version too.
        """
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None

        scheme = await self._resolve_scheme(building_id)
        scheme_id = str(scheme["scheme_id"])
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            lots_result = await session.execute(
                text(
                    """
                    SELECT lot_id::text AS lot_id, unit_number
                    FROM core.lots
                    WHERE scheme_id = :scheme_id
                    ORDER BY unit_number
                    """
                ),
                {"scheme_id": scheme_id},
            )
            lot_rows = lots_result.fetchall()
            if not lot_rows:
                return None
            unit_number_by_lot_id = {row.lot_id: row.unit_number for row in lot_rows}

            opening_result = await session.execute(
                text(
                    """
                    SELECT jl.lot_id::text AS lot_id, COALESCE(SUM(
                        CASE
                            WHEN jl.direction = 'debit' THEN jl.amount_cents
                            ELSE -jl.amount_cents
                        END
                    ), 0) AS opening_balance_cents
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga
                      ON ga.gl_account_id = jl.gl_account_id
                    WHERE je.scheme_id = :scheme_id
                      AND ga.account_code = :ar_account_code
                      AND je.status = 'posted'
                      AND je.effective_on < :starts_on
                    GROUP BY jl.lot_id
                    """
                ),
                {
                    "scheme_id": scheme_id,
                    "ar_account_code": _AR_ACCOUNT_CODE,
                    "starts_on": window.starts_on,
                },
            )
            opening_by_lot_id = {
                row.lot_id: int(row.opening_balance_cents or 0)
                for row in opening_result.fetchall()
            }

            levy_result = await session.execute(
                text(
                    """
                    SELECT li.lot_id::text AS lot_id,
                        COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0)
                            AS levied_cents,
                        COALESCE(SUM(li.paid_cents), 0) AS paid_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr
                      ON lr.levy_run_id = li.levy_run_id
                    WHERE li.scheme_id = :scheme_id
                      AND lr.financial_year = :financial_year
                    GROUP BY li.lot_id
                    """
                ),
                {"scheme_id": scheme_id, "financial_year": window.financial_year},
            )
            levy_by_lot_id = {
                row.lot_id: (int(row.levied_cents or 0), int(row.paid_cents or 0))
                for row in levy_result.fetchall()
            }

            closing_result = await session.execute(
                text(
                    """
                    SELECT jl.lot_id::text AS lot_id, COALESCE(SUM(
                        CASE
                            WHEN jl.direction = 'debit' THEN jl.amount_cents
                            ELSE -jl.amount_cents
                        END
                    ), 0) AS closing_balance_cents
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga
                      ON ga.gl_account_id = jl.gl_account_id
                    WHERE je.scheme_id = :scheme_id
                      AND ga.account_code = :ar_account_code
                      AND je.status = 'posted'
                      AND je.effective_on <= :ends_on
                    GROUP BY jl.lot_id
                    """
                ),
                {
                    "scheme_id": scheme_id,
                    "ar_account_code": _AR_ACCOUNT_CODE,
                    "ends_on": window.ends_on,
                },
            )
            closing_by_lot_id = {
                row.lot_id: int(row.closing_balance_cents or 0)
                for row in closing_result.fetchall()
            }

        balances: list[dict] = []
        for lot_id, unit_number in unit_number_by_lot_id.items():
            opening_cents = opening_by_lot_id.get(lot_id, 0)
            levied_cents, paid_cents = levy_by_lot_id.get(lot_id, (0, 0))
            closing_cents = closing_by_lot_id.get(lot_id, 0)
            balances.append({
                "unit_number": unit_number,
                "financial_year": window.financial_year,
                "opening_balance": _to_aud(opening_cents),
                "levied_amount": _to_aud(levied_cents),
                "paid_amount": _to_aud(paid_cents),
                "closing_balance": _to_aud(closing_cents),
                "arrears": _to_aud(max(0, closing_cents)),
            })
        return balances

    async def get_transactions_for_year(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> list[dict] | None:
        """Income + expense transactions for the building/year (GAP-FIN-030
        Fix 4 -- Transactions tab shadow comparator).

        Expense side: finance.expense_transactions (already scoped by
        financial_year). Income side: finance.receipts has no financial_year
        column -- filtered by received_on within the resolved FY window
        instead (same window-derivation helper get_unit_levy_balance uses).
        """
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None

        scheme = await self._resolve_scheme(building_id)
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            expense_rows = await session.execute(
                text(
                    """
                    SELECT expense_id::text AS id, transaction_date, amount_cents,
                           category_name, vendor_name, description
                    FROM finance.expense_transactions
                    WHERE scheme_id = :scheme_id
                      AND financial_year = :financial_year
                    ORDER BY transaction_date
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "financial_year": window.financial_year},
            )
            expenses = [
                {
                    "id": row.id,
                    "date": row.transaction_date.isoformat() if row.transaction_date else None,
                    "amount": _to_aud(row.amount_cents),
                    "category": row.category_name,
                    "description": row.description or row.vendor_name,
                    "transaction_type": "expense",
                }
                for row in expense_rows.fetchall()
            ]

            receipt_rows = await session.execute(
                text(
                    """
                    SELECT receipt_id::text AS id, received_on, amount_cents, external_reference
                    FROM finance.receipts
                    WHERE scheme_id = :scheme_id
                      AND received_on >= :starts_on
                      AND received_on <= :ends_on
                    ORDER BY received_on
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "starts_on": window.starts_on,
                    "ends_on": window.ends_on,
                },
            )
            income = [
                {
                    "id": row.id,
                    "date": row.received_on.isoformat() if row.received_on else None,
                    "amount": _to_aud(row.amount_cents),
                    "category": "Levy receipt",
                    "description": row.external_reference,
                    "transaction_type": "income",
                }
                for row in receipt_rows.fetchall()
            ]

        return expenses + income

    async def get_oc_levy_summary(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Generated function header.

        Function: FinancialReadService.get_oc_levy_summary
        Path: backend/services/financial_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            budget_result = await session.execute(
                text(
                    """
                    SELECT f.fund_type::text AS fund_type,
                           COALESCE(SUM(li.principal_cents + li.gst_cents), 0) AS budgeted_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr
                      ON lr.levy_run_id = li.levy_run_id
                    JOIN finance.funds f
                      ON f.fund_id = li.fund_id
                    WHERE li.scheme_id = :scheme_id
                      AND lr.financial_year = :financial_year
                    GROUP BY f.fund_type
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                },
            )
            # NOT finance.receipts summed by received_on BETWEEN starts_on/ends_on:
            # finance.levy_runs has exactly one row per financial_year with
            # issue_date == due_date (a nominal "start of year" placeholder, no
            # per-quarter issuance schedule in this data — see finance.levy_kpi's
            # comment above for the same root fact). window.starts_on/ends_on
            # (MIN(issue_date)/MAX(due_date)) therefore collapses to a single
            # day, so a receipts-by-date-range query structurally cannot match
            # receipts dated any other day of the year — confirmed live 2026-07-13:
            # FY2026's window is exactly 2026-07-01..2026-07-01, while all real
            # receipts are dated Feb-Apr 2026, so this always summed to 0 (the
            # building.overview shadow-diff total_paid=0-vs-mongo divergence).
            # finance.levy_items.paid_cents is the authoritative, already fund-
            # allocated (via finance.receipt_allocations) figure the live
            # building-overview/unit-dashboard routes already use — summing it
            # here instead matches that and is immune to the date-window issue.
            # Confirmed live: sums to within 4 cents of Mongo's own total_paid.
            receipts_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(li.paid_cents), 0)
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr
                      ON lr.levy_run_id = li.levy_run_id
                    WHERE li.scheme_id = :scheme_id
                      AND lr.financial_year = :financial_year
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                },
            )
            outstanding_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(
                        CASE
                            WHEN jl.direction = 'debit' THEN jl.amount_cents
                            ELSE -jl.amount_cents
                        END
                    ), 0) AS outstanding_cents
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga
                      ON ga.gl_account_id = jl.gl_account_id
                    WHERE je.scheme_id = :scheme_id
                      AND ga.account_code = :ar_account_code
                      AND je.status = 'posted'
                      AND je.effective_on <= :ends_on
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "ar_account_code": _AR_ACCOUNT_CODE,
                    "ends_on": window.ends_on,
                },
            )
            periods_result = await session.execute(
                text(
                    """
                    SELECT quarter_no, issue_date, due_date, status
                    FROM finance.levy_runs
                    WHERE scheme_id = :scheme_id
                      AND financial_year = :financial_year
                    ORDER BY issue_date
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                },
            )

            budget_rows = {row.fund_type: int(row.budgeted_cents or 0) for row in budget_result.fetchall()}
            total_collected_cents = int(receipts_result.scalar() or 0)
            total_budgeted_cents = sum(budget_rows.values())
            outstanding_cents = int(outstanding_result.scalar() or 0)
            levy_periods = [
                {
                    "quarter_no": row.quarter_no,
                    "issue_date": row.issue_date.isoformat() if row.issue_date else None,
                    "due_date": row.due_date.isoformat() if row.due_date else None,
                    "status": row.status,
                }
                for row in periods_result.fetchall()
            ]

        return {
            "financial_year": window.financial_year,
            "admin_fund_budgeted": _to_aud(budget_rows.get("admin", 0)),
            "sinking_fund_budgeted": _to_aud(budget_rows.get("sinking", 0)),
            "total_budgeted": _to_aud(total_budgeted_cents),
            "total_collected": _to_aud(total_collected_cents),
            "total_outstanding": _to_aud(max(0, outstanding_cents)),
            "levy_periods": levy_periods,
        }

    async def get_finance_summary(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Generated function header.

        Function: FinancialReadService.get_finance_summary
        Path: backend/services/financial_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None

        oc_levy_summary = await self.get_oc_levy_summary(
            building_id=building_id,
            financial_year=window.financial_year,
        )
        if oc_levy_summary is None:
            return None

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            expense_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(jl.amount_cents), 0)
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga
                      ON ga.gl_account_id = jl.gl_account_id
                    WHERE je.scheme_id = :scheme_id
                      AND je.status = 'posted'
                      AND je.effective_on BETWEEN :starts_on AND :ends_on
                      AND ga.account_type = 'expense'
                      AND jl.direction = 'debit'
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "starts_on": window.starts_on,
                    "ends_on": window.ends_on,
                },
            )
            allocation_result = await session.execute(
                text(
                    """
                    SELECT f.fund_type::text AS fund_type,
                           COALESCE(SUM(ra.allocated_cents), 0) AS allocated_cents
                    FROM finance.receipt_allocations ra
                    JOIN finance.levy_items li
                      ON li.levy_item_id = ra.levy_item_id
                    JOIN finance.levy_runs lr
                      ON lr.levy_run_id = li.levy_run_id
                    JOIN finance.funds f
                      ON f.fund_id = li.fund_id
                    WHERE li.scheme_id = :scheme_id
                      AND lr.financial_year = :financial_year
                    GROUP BY f.fund_type
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                },
            )
            allocation_rows = {row.fund_type: int(row.allocated_cents or 0) for row in allocation_result.fetchall()}

        total_income = oc_levy_summary["total_budgeted"]
        total_collected = oc_levy_summary["total_collected"]
        admin_budget = oc_levy_summary["admin_fund_budgeted"]
        sinking_budget = oc_levy_summary["sinking_fund_budgeted"]
        expense_cents = int(expense_result.scalar() or 0)

        admin_actual = _to_aud(allocation_rows.get("admin", 0))
        sinking_actual = _to_aud(allocation_rows.get("sinking", 0))
        if admin_actual == 0 and sinking_actual == 0 and total_income > 0:
            admin_ratio = admin_budget / total_income if total_income else 0
            sinking_ratio = sinking_budget / total_income if total_income else 0
            admin_actual = round(total_collected * admin_ratio, 2)
            sinking_actual = round(total_collected * sinking_ratio, 2)

        return {
            "financial_year": window.financial_year,
            "admin_fund_budget": admin_budget,
            "admin_fund_actual": admin_actual,
            "sinking_fund_budget": sinking_budget,
            "sinking_fund_actual": sinking_actual,
            "total_income": total_income,
            "total_expenses": _to_aud(expense_cents),
            "net_position": round(total_collected - _to_aud(expense_cents), 2),
            "levy_arrears_total": oc_levy_summary["total_outstanding"],
        }

    async def get_fund_balances(
            self,
            *,
            building_id: str,
            as_of_date: date | None = None,
    ) -> dict | None:
        """Return current fund balances in integer cents from the PG ledger.

        Balance = sum of all posted journal lines for the fund's GL accounts up
        to ``as_of_date`` (defaults to today).  Debits add, credits subtract on
        asset/receivable accounts; for liability/equity accounts the sign is
        reversed — but opening-balance genesis entries always credit the fund
        equity account, so a positive balance means the fund has money.

        Returns a dict with keys:
            fund_balances: list of {fund_code, fund_type, balance_cents}
            admin_balance_cents: int
            sinking_balance_cents: int
            special_balance_cents: int
            total_balance_cents: int
        """
        scheme = await self._resolve_scheme(building_id)
        cutoff = as_of_date or date.today()

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            result = await session.execute(
                text(
                    """
                    SELECT
                        f.fund_code,
                        f.fund_type::text AS fund_type,
                        COALESCE(SUM(
                            CASE
                                WHEN je.journal_entry_id IS NULL THEN 0
                                WHEN jl.direction = 'credit' THEN jl.amount_cents
                                ELSE -jl.amount_cents
                            END
                        ), 0) AS balance_cents
                    FROM finance.funds f
                    LEFT JOIN finance.gl_accounts ga ON ga.fund_id = f.fund_id
                    LEFT JOIN finance.journal_lines jl ON jl.gl_account_id = ga.gl_account_id
                    LEFT JOIN finance.journal_entries je
                        ON je.journal_entry_id = jl.journal_entry_id
                        AND je.status = 'posted'
                        AND je.effective_on <= :cutoff
                    WHERE f.scheme_id = :scheme_id
                      AND f.status = 'active'
                    GROUP BY f.fund_code, f.fund_type
                    ORDER BY f.fund_type
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "cutoff": cutoff,
                },
            )
            rows = result.fetchall()

        fund_balances = [
            {
                "fund_code": row.fund_code,
                "fund_type": row.fund_type,
                "balance_cents": int(row.balance_cents or 0),
            }
            for row in rows
        ]

        admin_cents = sum(
            fb["balance_cents"] for fb in fund_balances if fb["fund_type"] == "admin"
        )
        sinking_cents = sum(
            fb["balance_cents"] for fb in fund_balances if fb["fund_type"] in ("sinking", "capital_works")
        )
        special_cents = sum(
            fb["balance_cents"] for fb in fund_balances if fb["fund_type"] == "special"
        )

        return {
            "fund_balances": fund_balances,
            "admin_balance_cents": admin_cents,
            "sinking_balance_cents": sinking_cents,
            "special_balance_cents": special_cents,
            "total_balance_cents": admin_cents + sinking_cents + special_cents,
            "as_of_date": cutoff.isoformat(),
        }

    async def get_consolidated_fund_balances(
            self,
            *,
            building_id: str,
            financial_year: str,
            as_of_date: date | None = None,
    ) -> dict | None:
        """Return dashboard fund balances from analytics.fact_financial_balance.

        The BI fact is the consolidated, performance-oriented read model for
        dashboard charts. Its latest row per fund stores the actual/as-of
        balance in closing_balance_cents; projected levy-year closing values
        must not be used for the management Cash Position card.
        """
        scheme = await self._resolve_scheme(building_id)
        cutoff = as_of_date or date.today()

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (fund_type)
                               fund_type,
                               opening_balance_cents,
                               levy_income_cents,
                               other_income_cents,
                               expenses_cents,
                               closing_balance_cents,
                               bank_balance_cents,
                               reconciliation_gap_cents,
                               period_date,
                               ingested_at
                        FROM analytics.fact_financial_balance
                        WHERE scheme_id = :scheme_id
                          AND financial_year = :financial_year
                          AND period_date <= :cutoff
                          AND COALESCE(is_test_data, FALSE) = FALSE
                        ORDER BY fund_type, period_date DESC, ingested_at DESC
                    )
                    SELECT *
                    FROM latest
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": financial_year,
                    "cutoff": cutoff,
                },
            )
            rows = result.fetchall()

        if not rows:
            return None

        fact_rows = [
            {
                "fund_type": row.fund_type,
                "opening_balance_cents": int(row.opening_balance_cents or 0),
                "levy_income_cents": int(row.levy_income_cents or 0),
                "other_income_cents": int(row.other_income_cents or 0),
                "expenses_cents": int(row.expenses_cents or 0),
                "balance_cents": int(row.closing_balance_cents or 0),
                "bank_balance_cents": int(row.bank_balance_cents) if row.bank_balance_cents is not None else None,
                "reconciliation_gap_cents": int(row.reconciliation_gap_cents) if row.reconciliation_gap_cents is not None else None,
                "period_date": row.period_date.isoformat() if row.period_date else None,
            }
            for row in rows
        ]
        admin_cents = sum(row["balance_cents"] for row in fact_rows if row["fund_type"] == "admin")
        sinking_cents = sum(
            row["balance_cents"]
            for row in fact_rows
            if row["fund_type"] in ("sinking", "capital_works")
        )
        special_cents = sum(row["balance_cents"] for row in fact_rows if row["fund_type"] == "special")

        return {
            "fund_balances": fact_rows,
            "admin_balance_cents": admin_cents,
            "sinking_balance_cents": sinking_cents,
            "special_balance_cents": special_cents,
            "total_balance_cents": admin_cents + sinking_cents + special_cents,
            "as_of_date": cutoff.isoformat(),
            "source": "analytics.fact_financial_balance",
        }

    async def get_arrears_summary(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
            grace_aware: bool = False,
    ) -> dict | None:
        """Return arrears totals from PG levy_items in integer cents.

        Args:
            grace_aware: when True, only levy_items whose grace deadline has already
                passed (``finance.levy_items.grace_deadline_date < CURRENT_DATE``, the
                column added in Alembic 0077) are counted — i.e. "currently overdue"
                arrears. This is the due-date-aware concept that matches the Mongo
                ``GET /arrears/detail`` figure (a per-unit sum of true_arrears that skips
                units not yet past their grace deadline). When False (the default, kept
                for backward compatibility with every existing caller) EVERY unpaid
                levy_item for the financial year is counted regardless of due date — the
                year-scoped raw-ledger aggregate.

        Returns a dict with keys:
            total_arrears_cents: int  — sum of unpaid levy principal
            units_in_arrears: int
            arrears_by_lot: list of {lot_id, unit_number, arrears_cents}
            basis: "due_date_grace_aware" | "year_scoped_all_unpaid"

        NOTE (GAP-FIN-057): the arrears figure derives from levy_items.paid_cents, which
        is overstated building-wide until the orphaned-receipt_allocations reversal is
        applied. Until then this returns an UNDERSTATED arrears total — expected, not a
        bug in this function.
        """
        basis = "due_date_grace_aware" if grace_aware else "year_scoped_all_unpaid"
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return {
                "total_arrears_cents": 0,
                "units_in_arrears": 0,
                "arrears_by_lot": [],
                "financial_year": financial_year,
                "basis": basis,
            }

        # Constant, non-parameterised predicate (no user input) — only appended when the
        # caller asks for the due-date-aware concept.
        grace_predicate = "AND li.grace_deadline_date < CURRENT_DATE" if grace_aware else ""

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            result = await session.execute(
                text(
                    f"""
                    SELECT
                        l.lot_id::text AS lot_id,
                        l.unit_number,
                        COALESCE(SUM(
                            li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents
                            - li.paid_cents
                        ), 0) AS arrears_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    JOIN core.lots l ON l.lot_id = li.lot_id
                    WHERE li.scheme_id = :scheme_id
                      AND lr.financial_year = :financial_year
                      {grace_predicate}
                    GROUP BY l.lot_id, l.unit_number
                    HAVING COALESCE(SUM(
                        li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents
                        - li.paid_cents
                    ), 0) > 0
                    ORDER BY arrears_cents DESC
                    LIMIT 200
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                },
            )
            rows = result.fetchall()

        arrears_by_lot = [
            {
                "lot_id": row.lot_id,
                "unit_number": row.unit_number,
                "arrears_cents": int(row.arrears_cents or 0),
            }
            for row in rows
        ]
        total_cents = sum(a["arrears_cents"] for a in arrears_by_lot)

        return {
            "total_arrears_cents": total_cents,
            "units_in_arrears": len(arrears_by_lot),
            "arrears_by_lot": arrears_by_lot,
            "financial_year": window.financial_year,
            "basis": basis,
        }

    async def get_receipt_totals(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Return receipt/payment totals in integer cents."""
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return {
                "total_receipts_cents": 0,
                "receipt_count": 0,
                "financial_year": financial_year,
            }

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT
                        COALESCE(SUM(r.amount_cents), 0) AS total_cents,
                        COUNT(*) AS receipt_count
                    FROM finance.receipts r
                    WHERE r.scheme_id = :scheme_id
                      AND r.received_on BETWEEN :starts_on AND :ends_on
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "starts_on": window.starts_on,
                    "ends_on": window.ends_on,
                },
            )
            row = result.fetchone()

        return {
            "total_receipts_cents": int(row.total_cents or 0) if row else 0,
            "receipt_count": int(row.receipt_count or 0) if row else 0,
            "financial_year": window.financial_year,
        }

    async def get_invoice_summary(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Return invoice totals from PG expense_invoices in integer cents.

        Returns a dict with keys:
            total_invoices_cents: int
            approved_invoices_cents: int
            pending_invoices_cents: int
            invoice_count: int
        """
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return {
                "total_invoices_cents": 0,
                "approved_invoices_cents": 0,
                "pending_invoices_cents": 0,
                "invoice_count": 0,
                "financial_year": financial_year,
            }

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT
                        COALESCE(SUM(amount_cents), 0) AS total_cents,
                        COALESCE(SUM(CASE WHEN status = 'approved' THEN amount_cents ELSE 0 END), 0) AS approved_cents,
                        COALESCE(SUM(CASE WHEN status NOT IN ('approved','paid') THEN amount_cents ELSE 0 END), 0) AS pending_cents,
                        COUNT(*) AS invoice_count
                    FROM finance.expense_invoices
                    WHERE scheme_id = :scheme_id
                      AND invoice_date BETWEEN :starts_on AND :ends_on
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "starts_on": window.starts_on,
                    "ends_on": window.ends_on,
                },
            )
            row = result.fetchone()

        return {
            "total_invoices_cents": int(row.total_cents or 0) if row else 0,
            "approved_invoices_cents": int(row.approved_cents or 0) if row else 0,
            "pending_invoices_cents": int(row.pending_cents or 0) if row else 0,
            "invoice_count": int(row.invoice_count or 0) if row else 0,
            "financial_year": window.financial_year,
        }

    async def get_fund_expense_totals(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Return fund-split EXPENSE actuals from posted GL activity, in integer cents.

        Not to be confused with get_finance_summary()'s "actual" fields, which are
        INCOME-side (finance.receipt_allocations — money collected) and not fund-split.
        This is the real fund-split expense figure that GET /finance/summary's
        admin_fund/sinking_fund.actual_expenses needs.

        unassigned_expense_cents (expense GL accounts with no fund_id) is reported as its
        own bucket, never silently folded into admin/sinking — a nonzero value here is a
        real chart-of-accounts data-completeness signal, not something to hide.
        """
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return {
                "admin_expense_cents": 0,
                "sinking_expense_cents": 0,
                "unassigned_expense_cents": 0,
                "total_expense_cents": 0,
                "financial_year": financial_year,
            }

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT f.fund_type::text AS fund_type,
                           COALESCE(SUM(jl.amount_cents), 0) AS expense_cents
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
                    JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
                    LEFT JOIN finance.expense_transactions et
                      ON et.journal_entry_id = je.journal_entry_id
                     AND et.scheme_id = je.scheme_id
                    LEFT JOIN finance.funds f ON f.fund_id = COALESCE(et.fund_id, je.fund_id, ga.fund_id)
                    WHERE je.scheme_id = :scheme_id
                      AND je.status = 'posted'
                      AND je.effective_on BETWEEN :starts_on AND :ends_on
                      AND ga.account_type = 'expense'
                      AND jl.direction = 'debit'
                    GROUP BY f.fund_type
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "starts_on": window.starts_on,
                    "ends_on": window.ends_on,
                },
            )
            rows = {row.fund_type: int(row.expense_cents or 0) for row in result.fetchall()}

        admin_cents = rows.get("admin", 0)
        sinking_cents = sum(v for k, v in rows.items() if k in ("sinking", "capital_works"))
        unassigned_cents = rows.get(None, 0)
        if unassigned_cents:
            logger.warning(
                "get_fund_expense_totals: %d cents of posted expense activity has no "
                "fund_id on its GL account for building=%s fy=%s -- chart-of-accounts "
                "data-completeness gap, not folded into admin/sinking.",
                unassigned_cents, building_id, window.financial_year,
            )

        return {
            "admin_expense_cents": admin_cents,
            "sinking_expense_cents": sinking_cents,
            "unassigned_expense_cents": unassigned_cents,
            "total_expense_cents": admin_cents + sinking_cents + unassigned_cents,
            "financial_year": window.financial_year,
        }

    async def get_canonical_ledger_quality(
            self,
            *,
            building_id: str,
            financial_year: str,
    ) -> dict | None:
        """PG equivalent of Mongo's utils.finance_helpers.get_ledger_quality().

        Returns the SAME key shape so routers/finance.py's _build_ledger_quality_warnings
        needs no changes to consume either source. Several Mongo-side fields
        (duplicate_ledger_units, extra_ledger_units, malformed_ledger_row_count) are
        always empty/zero here -- not because they're unimplemented, but because
        finance.levy_items.lot_id is a hard FK into core.lots.lot_id (unlike Mongo's
        free-text unit_number join), which structurally eliminates the
        duplicate/orphan/malformed-row classes of problem get_ledger_quality() exists to
        catch in Mongo.

        A single grouped query, not a per-lot loop -- do NOT reuse
        get_unit_levy_balance_list() here, whose own docstring says its N+1 query shape
        is only acceptable for fire-and-forget shadow comparison, never a live response.
        """
        scheme = await self._resolve_scheme(building_id)

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT l.lot_id::text AS lot_id,
                           l.unit_number,
                           COALESCE(SUM(
                               li.principal_cents + li.gst_cents + li.interest_cents
                               + li.recovery_costs_cents - li.paid_cents
                           ), 0) AS net_cents,
                           COUNT(li.levy_item_id) AS item_count
                    FROM core.lots l
                    LEFT JOIN finance.levy_items li
                        ON li.lot_id = l.lot_id AND li.scheme_id = l.scheme_id
                    LEFT JOIN finance.levy_runs lr
                        ON lr.levy_run_id = li.levy_run_id AND lr.financial_year = :fy
                    WHERE l.scheme_id = :scheme_id
                    GROUP BY l.lot_id, l.unit_number
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "fy": financial_year},
            )
            rows = result.fetchall()

        canonical_unit_count = len(rows)
        missing_ledger_units: list[str] = []
        paid_up = owing = credit = 0
        matched_row_count = 0
        for row in rows:
            if not row.item_count:
                missing_ledger_units.append(row.unit_number)
                continue
            matched_row_count += 1
            net = int(row.net_cents or 0)
            if net > 0:
                owing += 1
            elif net < 0:
                credit += 1
            else:
                paid_up += 1

        return {
            "canonical_unit_count": canonical_unit_count,
            "ledger_row_count": matched_row_count,
            "distinct_ledger_unit_count": matched_row_count,
            # Structurally impossible under the lot_id FK -- see docstring, not unimplemented.
            "duplicate_ledger_units": [],
            "duplicate_ledger_row_count": 0,
            "missing_ledger_units": missing_ledger_units,
            "extra_ledger_units": [],
            "malformed_ledger_row_count": 0,
            "is_unit_count_consistent": not missing_ledger_units,
            "canonical_status_counts": {
                "paid_up": paid_up,
                "owing": owing,
                "credit": credit,
            },
            "financial_year": financial_year,
            "source": "core.lots + finance.levy_items",
        }

    async def get_current_quarter_levy_total(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Return the CURRENT quarter's billed total (all funds) in integer cents.

        GAP-FIN-058 follow-up (2026-08-09) -- root cause of finance.levy_kpi's
        "PG=2.0x Mongo" shadow divergence: Mongo's quarter_billed_total_display
        (routers/finance.py get_finance_levy_kpi) is a FLAT single-quarter target rate
        (total_payable_quarterly * total_uoe) that never grows as more quarters get
        raised through the year. get_oc_levy_summary()'s total_budgeted (used by
        building_overview/summary, correctly for THEIR concept) is a YTD-CUMULATIVE sum
        across every finance.levy_runs row raised so far this financial year -- these
        are genuinely different concepts, not a bug in either. The 2x was real: East
        Gate FY2026 had exactly 1 quarter raised when this comparator was first checked
        (2026-07-12), and has 2 (Q1+Q2, confirmed live) as of this fix -- 2 x one
        quarter's flat rate is exactly the observed divergence. Do NOT "fix" this by
        widening tolerance or excluding the field; the real fix is sourcing a PG figure
        that matches Mongo's actual concept (one quarter only), not the whole year.

        Scopes to the MOST RECENTLY ISSUED levy_run's issue_date across all funds (not
        filtered by status -- Mongo's flat-rate concept has no "draft vs approved"
        distinction to mirror, and East Gate's live levy_runs are all status='draft').
        """
        scheme = await self._resolve_scheme(building_id)
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return {"quarter_billed_cents": 0, "financial_year": financial_year, "issue_date": None}

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            latest_result = await session.execute(
                text(
                    """
                    SELECT MAX(issue_date) AS max_issue_date
                    FROM finance.levy_runs
                    WHERE scheme_id = :scheme_id AND financial_year = :financial_year
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "financial_year": window.financial_year},
            )
            max_issue_date = latest_result.scalar()
            if max_issue_date is None:
                return {"quarter_billed_cents": 0, "financial_year": window.financial_year, "issue_date": None}

            total_result = await session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(li.principal_cents + li.gst_cents), 0) AS quarter_billed_cents
                    FROM finance.levy_items li
                    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                    WHERE li.scheme_id = :scheme_id
                      AND lr.financial_year = :financial_year
                      AND lr.issue_date = :max_issue_date
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                    "max_issue_date": max_issue_date,
                },
            )
            quarter_billed_cents = int(total_result.scalar() or 0)

        return {
            "quarter_billed_cents": quarter_billed_cents,
            "financial_year": window.financial_year,
            "issue_date": max_issue_date.isoformat() if max_issue_date else None,
        }

    async def get_building_finance_pg_dashboard(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
    ) -> dict | None:
        """Combined PG finance dashboard values for shadow comparison.

        All money values are in integer cents. Callers must convert to display
        units (e.g. AUD) before rendering. This method is intentionally parallel-
        safe: individual sub-calls are independent and can be gathered.
        """
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None

        fy = window.financial_year
        # return_exceptions=True so a single failing sub-call does not sink the
        # whole payload silently into "pg_unavailable" with no clue which one
        # threw. Any failure is logged BY NAME and the method returns None —
        # never a partial payload with a silently-zeroed money field (a zeroed
        # arrears/fund figure served as if real would violate the "zero != missing"
        # rule and produce a false shadow diff). This converts an opaque
        # pg_unavailable into an actionable "get_<x>_summary failed: <error>" log
        # line — the diagnostic that gates the building_overview/summary read
        # promotion (see tasks/CUTOVER-COMPLETION-REGISTER.md, P1.1a).
        sub_call_names = (
            "get_oc_levy_summary",
            "get_arrears_summary",
            "get_receipt_totals",
            "get_fund_balances",
        )
        results = await asyncio.gather(
            self.get_oc_levy_summary(building_id=building_id, financial_year=fy),
            self.get_arrears_summary(building_id=building_id, financial_year=fy),
            self.get_receipt_totals(building_id=building_id, financial_year=fy),
            self.get_fund_balances(building_id=building_id),
            return_exceptions=True,
        )
        failed = False
        for name, result in zip(sub_call_names, results):
            if isinstance(result, BaseException):
                failed = True
                logger.warning(
                    "finance PG dashboard: sub-call %s failed for building=%s fy=%s: %r",
                    name, building_id, fy, result,
                )
        if failed:
            # Cannot build a trustworthy payload — behave exactly as before
            # (None => the caller records pg_unavailable), but now the log names
            # the culprit sub-call so the underlying PG query bug is fixable.
            return None
        oc_levy, arrears, receipts, fund_bal = results

        return {
            "financial_year": fy,
            "levy_budgeted_cents": int(round((oc_levy or {}).get("total_budgeted", 0) * 100)),
            "levy_collected_cents": int(round((oc_levy or {}).get("total_collected", 0) * 100)),
            "levy_outstanding_cents": int(round((oc_levy or {}).get("total_outstanding", 0) * 100)),
            "total_arrears_cents": (arrears or {}).get("total_arrears_cents", 0),
            "units_in_arrears": (arrears or {}).get("units_in_arrears", 0),
            "total_receipts_cents": (receipts or {}).get("total_receipts_cents", 0),
            "admin_fund_balance_cents": (fund_bal or {}).get("admin_balance_cents", 0),
            "sinking_fund_balance_cents": (fund_bal or {}).get("sinking_balance_cents", 0),
            "total_fund_balance_cents": (fund_bal or {}).get("total_balance_cents", 0),
            "source": "postgres",
        }

    # ------------------------------------------------------------------
    # Response-shaped readers — for routes served directly, not shadowed
    # ------------------------------------------------------------------
    #
    # get_transactions_for_year() above feeds the shadow COMPARATOR, which only ever
    # sums two totals, so its rows carry {id, date, amount, category, description,
    # transaction_type} and nothing else. Serving those rows to a route would 500:
    # ExpenseTransactionResponse requires financial_year, supplier_name, created_at,
    # updated_at and created_by; IncomeTransactionResponse requires financial_year,
    # source, created_at and updated_at. None of them are optional and none are present.
    #
    # That failure mode only appears once a domain is promoted and Postgres actually
    # serves — in production, on a path no test covers while the route is still
    # Mongo-primary. It is the same defect found in documents_repo on 2026-08-29, so
    # the readers below are written against the RESPONSE MODEL, not against the table.

    async def get_expense_transactions(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
            limit: int = 1000,
    ) -> list[dict] | None:
        """Expense rows shaped for ExpenseTransactionResponse.

        UNAVAILABLE vs EMPTY:
          * `None` is returned when there is no financial-year window — the request
            cannot be scoped, so `read_through` reports `mongo_fallback_pg_unavailable`.
          * An unresolvable building RAISES from `_resolve_scheme` rather than returning
            None; `read_through` catches that and reports the same thing. The
            `if not scheme` branch is defensive only and does not fire today.
          * An empty list is a REAL answer — the year genuinely has no expenses.
        """
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None
        scheme = await self._resolve_scheme(building_id)
        if not scheme:
            return None

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            rows = await session.execute(
                text(
                    """
                    SELECT expense_id::text AS id, transaction_date, amount_cents,
                           category_name, vendor_name, description, financial_year,
                           created_at
                      FROM finance.expense_transactions
                     WHERE scheme_id = :scheme_id
                       AND financial_year = :financial_year
                     ORDER BY transaction_date DESC
                     LIMIT :limit
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "financial_year": window.financial_year,
                    "limit": int(limit),
                },
            )
            fetched = rows.fetchall()

        return [
            {
                "id": row.id,
                "building_id": building_id,
                "plan_id": building_id,
                "financial_year": row.financial_year,
                "date": row.transaction_date.isoformat() if row.transaction_date else None,
                "amount": _to_aud(row.amount_cents),
                "description": row.description,
                "category_name": row.category_name,
                # supplier_name is REQUIRED by the response model and vendor_name is
                # nullable in Postgres. "" rather than None: the field is typed `str`,
                # so None fails validation and would 500 the whole list on one row.
                "supplier_name": row.vendor_name or "",
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "updated_at": row.created_at.isoformat() if row.created_at else "",
                # No updated_by/created_by column exists on this table. "" is honest —
                # inventing a user id would be worse than admitting we do not know.
                "created_by": "",
                "source_store": "postgres",
            }
            for row in fetched
        ]

    async def get_income_transactions(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
            limit: int = 1000,
    ) -> list[dict] | None:
        """Levy receipts shaped for IncomeTransactionResponse.

        RETIRED RECEIPTS ARE EXCLUDED. `finance.receipts.retired_at` marks a receipt
        that has been reversed or withdrawn; get_transactions_for_year does not filter
        it, which is tolerable for a comparator summing both stales but NOT for a route
        that shows an owner their income. Counting reversed receipts as income is the
        exact defect that made $1,769,655.36 of reversed receipts read as owner credit
        (see lot_true_balance, 2026-08-28).
        """
        window = await self._get_financial_year_window(building_id, financial_year)
        if window is None:
            return None
        scheme = await self._resolve_scheme(building_id)
        if not scheme:
            return None

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            rows = await session.execute(
                text(
                    """
                    SELECT receipt_id::text AS id, received_on, amount_cents,
                           external_reference, channel, created_at
                      FROM finance.receipts
                     WHERE scheme_id = :scheme_id
                       AND received_on >= :starts_on
                       AND received_on <= :ends_on
                       AND retired_at IS NULL
                     ORDER BY received_on DESC
                     LIMIT :limit
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "starts_on": window.starts_on,
                    "ends_on": window.ends_on,
                    "limit": int(limit),
                },
            )
            fetched = rows.fetchall()

        return [
            {
                "id": row.id,
                "building_id": building_id,
                "plan_id": building_id,
                "financial_year": window.financial_year,
                "date": row.received_on.isoformat() if row.received_on else None,
                "amount": _to_aud(row.amount_cents),
                "description": row.external_reference,
                "category_name": "Levy receipt",
                # `source` is REQUIRED and is the income TYPE (interest/rebate/grant/
                # other), not the datastore. Every row here is a levy receipt, so the
                # channel is the closest true value and "levy" is the honest default.
                "source": row.channel or "levy",
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "updated_at": row.created_at.isoformat() if row.created_at else "",
                # Required by IncomeTransactionResponse and absent from the table, same
                # as the expense reader. Caught by validating a real row against the
                # real model rather than by reading the model definition — the expense
                # shape passed while this one did not, and nothing but an actual
                # round-trip would have shown that.
                "created_by": "",
                "source_store": "postgres",
            }
            for row in fetched
        ]

    async def get_available_levy_years(self, *, building_id: str) -> list[str] | None:
        """Distinct financial years that have a levy run in PostgreSQL.

        The Mongo equivalent is `db.annual_levies.distinct("year")`. This is one of the
        very few finance reads that maps CLEANLY: the value is a bare year string on
        both sides, with no response model to satisfy beyond `list[str]`, so there is
        nothing to invent.

        UNAVAILABLE vs EMPTY, and how each actually happens:
          * an unresolvable building RAISES from `_resolve_scheme` (it does not return
            None), and `read_through` catches that and reports
            `mongo_fallback_pg_unavailable`. The `if not scheme` branch below is
            defensive only — kept because a future `_resolve_scheme` that returns None
            would otherwise crash on a subscript, not because it fires today.
          * an empty list is a REAL answer — a building with no levy runs yet — and is
            returned as such rather than being turned into a fallback.

        The caller still applies its own not-yet-started filter: which years are
        SELECTABLE is a levy-cycle rule that lives in the router, not a property of the
        rows, and duplicating it here would create the second implementation the
        capability index exists to prevent.
        """
        scheme = await self._resolve_scheme(building_id)
        if not scheme:
            return None

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT financial_year
                      FROM finance.levy_runs
                     WHERE scheme_id = :scheme_id
                       AND financial_year IS NOT NULL
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"])},
            )
            return [row.financial_year for row in result.fetchall()]

    async def get_budget_categories(
            self,
            *,
            building_id: str,
            financial_year: str | None = None,
            fund_type: str | None = None,
            include_archived: bool = False,
            limit: int = 500,
    ) -> list[dict] | None:
        """Budget categories shaped for LevyCategoryResponse, with actuals DERIVED.

        `actual_amount` is computed here, not stored. `finance.budget_categories` has no
        column for it on purpose: the number already exists in
        `finance.expense_transactions` at exactly this grain (scheme + financial_year +
        fund_id + category_name), and a second stored copy drifts the moment an expense
        is posted, reversed or re-categorised. East Gate has already carried two
        disconnected expense totals that diverged 3.6x for precisely that reason.

        The join is on `category_name`, which is a STRING match. That is the grain the
        expense table actually offers — it has no budget_category_id — so a category
        whose expenses were filed under a different spelling derives an actual of 0.00
        rather than the true figure. That is a real limitation and it is why the archived
        duplicate-spelling rows exist in the first place; it is reported as 0.00 rather
        than guessed at, because a fuzzy match here would silently move money between
        budget lines.

        Returns None when the request cannot be scoped. An empty list is a real answer.
        """
        scheme = await self._resolve_scheme(building_id)
        if not scheme:
            return None

        clauses = ["bc.scheme_id = :scheme_id"]
        params: dict[str, Any] = {"scheme_id": str(scheme["scheme_id"]), "limit": int(limit)}
        if not include_archived:
            clauses.append("bc.is_archived = FALSE")
        if financial_year:
            clauses.append("bc.financial_year = :financial_year")
            params["financial_year"] = str(financial_year)
        if fund_type:
            clauses.append("f.fund_type = :fund_type")
            params["fund_type"] = fund_type

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    f"""
                    SELECT bc.budget_category_id::text AS id,
                           bc.financial_year,
                           f.fund_type,
                           bc.name,
                           bc.canonical_name,
                           bc.budgeted_cents,
                           bc.status,
                           bc.created_at,
                           bc.updated_at,
                           COALESCE((
                               SELECT SUM(et.amount_cents)
                                 FROM finance.expense_transactions et
                                WHERE et.scheme_id = bc.scheme_id
                                  AND et.fund_id = bc.fund_id
                                  AND et.financial_year = bc.financial_year
                                  AND et.category_name = bc.name
                           ), 0) AS actual_cents
                      FROM finance.budget_categories bc
                      JOIN finance.funds f ON f.fund_id = bc.fund_id
                     WHERE {' AND '.join(clauses)}
                     ORDER BY bc.name
                     LIMIT :limit
                    """
                ),
                params,
            )
            rows = result.fetchall()

        return [
            {
                "id": row.id,
                "building_id": building_id,
                "plan_id": building_id,
                "year": row.financial_year,
                # LevyCategoryResponse expects the legacy vocabulary ("admin"/"sinking"),
                # which finance.funds.fund_type already uses for those two. Passed
                # through rather than re-mapped so a future fund type surfaces as itself
                # instead of being silently folded into one of the two the UI knows.
                "fund_type": row.fund_type,
                "name": row.name,
                # `budgeted_cents` is NULL when nobody set a budget. The response field
                # is a non-optional float, so it becomes 0.0 here — the same value the
                # Mongo handler's `setdefault("budgeted_amount", 0.0)` produces, so the
                # two stores agree on the rendering of "no budget".
                "budgeted_amount": float(_to_aud(row.budgeted_cents)) if row.budgeted_cents is not None else 0.0,
                # float(), not just _to_aud(): SUM() over a bigint returns a Decimal, and
                # _to_aud passes it through, so actual_amount would arrive as Decimal.
                # Pydantic coerces it on the way out, which hides the problem — but any
                # caller that does arithmetic first (GET /finance/budget-vs-actual sums
                # these) raises `unsupported operand type(s) for +: 'Decimal' and 'float'`.
                # The response field is typed float; make it one here.
                "actual_amount": float(_to_aud(row.actual_cents)),
                "description": row.canonical_name,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "source_store": "postgres",
            }
            for row in rows
        ]
