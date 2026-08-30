# @featuretrace:financial_integration_v2 — PostgreSQL trust and reconciliation read models.
# Layer: service
# Data flow: trust_accounting.py / trust_reconciliation.py → TrustReadService → finance.* (building-scoped).
# Related: backend/services/cutover_config_service.py
#           backend/services/financial_core/adapters/db_postgres/ledger_repo.py
"""Postgres-backed trust accounting and reconciliation read models."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant


def _to_aud(cents: int | None) -> float:
    """Generated function header.

    Function: _to_aud
    Path: backend/services/trust_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return round(((cents or 0) / 100), 2)


def _map_fund_type(raw: str | None) -> str:
    """Generated function header.

    Function: _map_fund_type
    Path: backend/services/trust_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mapping = {
        "admin": "admin_fund",
        "capital_works": "capital_works_fund",
        "sinking": "capital_works_fund",
        "special_purpose": "special_levy",
        "other": "operating",
    }
    return mapping.get(str(raw or "").strip().lower(), "operating")


def _fund_type_filters(requested_fund_type: str | None) -> tuple[str, ...]:
    """Generated function header.

    Function: _fund_type_filters
    Path: backend/services/trust_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mapping = {
        "admin_fund": ("admin",),
        "capital_works_fund": ("capital_works", "sinking"),
        "special_levy": ("special_purpose",),
        "operating": ("other",),
    }
    return mapping.get(str(requested_fund_type or "").strip().lower(), tuple())


def _append_raw_fund_type_filter(
    query: str,
    params: dict[str, Any],
    filters: tuple[str, ...],
    column_sql: str,
) -> str:
    """Generated function header.

    Function: _append_raw_fund_type_filter
    Path: backend/services/trust_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not filters:
        return query
    clauses: list[str] = []
    for index, value in enumerate(filters):
        key = f"fund_type_{index}"
        params[key] = value
        clauses.append(f"{column_sql} = :{key}")
    return f"{query} AND ({' OR '.join(clauses)})"


def _iso(value: Any) -> str | None:
    """Generated function header.

    Function: _iso
    Path: backend/services/trust_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class TrustReadService:
    """Query trust-accounting and reconciliation summaries from PostgreSQL."""

    async def _resolve_scheme(self, building_id: str) -> dict | None:
        """Generated function header.

        Function: TrustReadService._resolve_scheme
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await config_repo.resolve_scheme_context(building_id)

    async def list_trust_accounts(
        self,
        *,
        building_id: str,
        fund_type: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Generated function header.

        Function: TrustReadService.list_trust_accounts
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None

        filters = _fund_type_filters(fund_type)
        query = """
            SELECT ta.trust_account_id::text AS id,
                   ta.account_name,
                   ta.bank_name,
                   ta.masked_bsb,
                   ta.masked_account_number,
                   ta.status,
                   ta.created_at,
                   f.fund_code,
                   f.fund_type::text AS raw_fund_type,
                   COALESCE(f.opening_balance_cents, 0) AS opening_balance_cents,
                   COALESCE(SUM(bt.amount_cents), 0) AS transaction_balance_cents
            FROM finance.trust_accounts ta
            LEFT JOIN finance.funds f
              ON f.fund_id = ta.fund_id
            LEFT JOIN finance.bank_transactions bt
              ON bt.trust_account_id = ta.trust_account_id
            WHERE ta.scheme_id = :scheme_id
              AND ta.status != CAST('archived' AS core.record_status)
        """
        params: dict[str, Any] = {"scheme_id": str(scheme["scheme_id"])}
        query = _append_raw_fund_type_filter(query, params, filters, "f.fund_type::text")
        query += """
            GROUP BY ta.trust_account_id, ta.account_name, ta.bank_name, ta.masked_bsb,
                     ta.masked_account_number, ta.status, ta.created_at,
                     f.fund_code, f.fund_type, f.opening_balance_cents
            ORDER BY COALESCE(f.fund_code, ta.account_name), ta.account_name
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(text(query), params)
            rows = result.fetchall()

        accounts = [
            {
                "id": row.id,
                "building_id": building_id,
                "fund_type": _map_fund_type(row.raw_fund_type),
                "account_type": "asset",
                "account_code": row.fund_code or "trust-account",
                "account_name": row.account_name,
                "bank_name": row.bank_name,
                "bsb": row.masked_bsb,
                "account_number": row.masked_account_number,
                "opening_balance": _to_aud(int(row.opening_balance_cents or 0)),
                "current_balance": _to_aud(
                    int(row.opening_balance_cents or 0) + int(row.transaction_balance_cents or 0)
                ),
                "currency": "AUD",
                "is_active": str(row.status) == "active",
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.created_at),
            }
            for row in rows
        ]
        return accounts

    async def list_journal_entries(
        self,
        *,
        building_id: str,
        fund_type: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.list_journal_entries
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None

        filters = _fund_type_filters(fund_type)
        base_where = [
            "je.scheme_id = :scheme_id",
        ]
        params: dict[str, Any] = {
            "scheme_id": str(scheme["scheme_id"]),
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        if filters:
            clauses: list[str] = []
            for index, value in enumerate(filters):
                key = f"fund_type_{index}"
                params[key] = value
                clauses.append(f"f.fund_type::text = :{key}")
            base_where.append(f"({' OR '.join(clauses)})")
        if status:
            base_where.append("je.status = :status")
            params["status"] = status
        if from_date:
            base_where.append("je.effective_on >= :from_date")
            params["from_date"] = from_date
        if to_date:
            base_where.append("je.effective_on <= :to_date")
            params["to_date"] = to_date
        where_sql = " AND ".join(base_where)

        count_query = f"""
            SELECT COUNT(*)
            FROM finance.journal_entries je
            LEFT JOIN finance.funds f
              ON f.fund_id = je.fund_id
            WHERE {where_sql}
        """
        data_query = f"""
            SELECT je.journal_entry_id::text AS id,
                   je.effective_on::text AS date,
                   COALESCE(je.source_reference, '') AS reference,
                   je.narration,
                   je.status::text AS status,
                   je.source_type,
                   je.created_at,
                   f.fund_type::text AS raw_fund_type,
                   COALESCE(SUM(CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE 0 END), 0) AS debit_total_cents,
                   COALESCE(SUM(CASE WHEN jl.direction = 'credit' THEN jl.amount_cents ELSE 0 END), 0) AS credit_total_cents
            FROM finance.journal_entries je
            LEFT JOIN finance.funds f
              ON f.fund_id = je.fund_id
            LEFT JOIN finance.journal_lines jl
              ON jl.journal_entry_id = je.journal_entry_id
            WHERE {where_sql}
            GROUP BY je.journal_entry_id, je.effective_on, je.source_reference, je.narration,
                     je.status, je.source_type, je.created_at, f.fund_type
            ORDER BY je.effective_on DESC, je.created_at DESC
            LIMIT :limit OFFSET :offset
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            total = int((await session.execute(text(count_query), params)).scalar() or 0)
            rows = (await session.execute(text(data_query), params)).fetchall()

        data = [
            {
                "id": row.id,
                "building_id": building_id,
                "fund_type": _map_fund_type(row.raw_fund_type),
                "date": row.date,
                "reference": row.reference,
                "narration": row.narration,
                "lines": [],
                "status": row.status,
                "is_balanced": int(row.debit_total_cents or 0) == int(row.credit_total_cents or 0),
                "debit_total": _to_aud(int(row.debit_total_cents or 0)),
                "credit_total": _to_aud(int(row.credit_total_cents or 0)),
                "source_type": row.source_type,
                "source_id": None,
                "batch_id": None,
                "created_by": "postgres-cutover",
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]
        return {
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }

    async def get_journal_entry(
        self,
        *,
        building_id: str,
        entry_id: str,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.get_journal_entry
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None
        try:
            UUID(entry_id)
        except ValueError:
            return None

        entry_query = """
            SELECT je.journal_entry_id::text AS id,
                   je.effective_on::text AS date,
                   COALESCE(je.source_reference, '') AS reference,
                   je.narration,
                   je.status::text AS status,
                   je.source_type,
                   je.created_at,
                   f.fund_type::text AS raw_fund_type
            FROM finance.journal_entries je
            LEFT JOIN finance.funds f
              ON f.fund_id = je.fund_id
            WHERE je.scheme_id = :scheme_id
              AND je.journal_entry_id = CAST(:entry_id AS uuid)
            LIMIT 1
        """
        lines_query = """
            SELECT jl.gl_account_id::text AS account_id,
                   ga.account_code,
                   ga.account_name,
                   jl.direction::text AS entry_type,
                   jl.amount_cents,
                   jl.narration
            FROM finance.journal_lines jl
            JOIN finance.gl_accounts ga
              ON ga.gl_account_id = jl.gl_account_id
            WHERE jl.journal_entry_id = CAST(:entry_id AS uuid)
            ORDER BY ga.account_code, jl.created_at
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            entry_row = (await session.execute(text(entry_query), {
                "scheme_id": str(scheme["scheme_id"]),
                "entry_id": entry_id,
            })).fetchone()
            if entry_row is None:
                return None
            line_rows = (await session.execute(text(lines_query), {"entry_id": entry_id})).fetchall()

        debit_total_cents = sum(
            int(row.amount_cents or 0) for row in line_rows if str(row.entry_type) == "debit"
        )
        credit_total_cents = sum(
            int(row.amount_cents or 0) for row in line_rows if str(row.entry_type) == "credit"
        )
        return {
            "id": entry_row.id,
            "building_id": building_id,
            "fund_type": _map_fund_type(entry_row.raw_fund_type),
            "date": entry_row.date,
            "reference": entry_row.reference,
            "narration": entry_row.narration,
            "lines": [
                {
                    "account_id": row.account_id,
                    "account_code": row.account_code,
                    "account_name": row.account_name,
                    "entry_type": row.entry_type,
                    "amount": _to_aud(int(row.amount_cents or 0)),
                    "description": row.narration,
                }
                for row in line_rows
            ],
            "status": entry_row.status,
            "is_balanced": debit_total_cents == credit_total_cents,
            "debit_total": _to_aud(debit_total_cents),
            "credit_total": _to_aud(credit_total_cents),
            "source_type": entry_row.source_type,
            "source_id": None,
            "batch_id": None,
            "created_by": "postgres-cutover",
            "created_at": _iso(entry_row.created_at),
        }

    async def get_fund_balance(
        self,
        *,
        building_id: str,
        fund_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.get_fund_balance
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None

        filters = _fund_type_filters(fund_type)
        query = """
            SELECT f.fund_type::text AS raw_fund_type,
                   ga.account_type::text AS account_type,
                   COALESCE(SUM(
                       CASE
                           WHEN jl.direction = 'debit' THEN jl.amount_cents
                           ELSE -jl.amount_cents
                       END
                   ), 0) AS total_balance_cents,
                   COUNT(DISTINCT ga.gl_account_id) AS account_count
            FROM finance.journal_entries je
            JOIN finance.journal_lines jl
              ON jl.journal_entry_id = je.journal_entry_id
            JOIN finance.gl_accounts ga
              ON ga.gl_account_id = jl.gl_account_id
            LEFT JOIN finance.funds f
              ON f.fund_id = je.fund_id
            WHERE je.scheme_id = :scheme_id
              AND je.status = 'posted'
        """
        params: dict[str, Any] = {"scheme_id": str(scheme["scheme_id"])}
        query = _append_raw_fund_type_filter(query, params, filters, "f.fund_type::text")
        query += """
            GROUP BY f.fund_type, ga.account_type
            ORDER BY f.fund_type, ga.account_type
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            rows = (await session.execute(text(query), params)).fetchall()

        balances = [
            {
                "fund_type": _map_fund_type(row.raw_fund_type),
                "account_type": row.account_type,
                "total_balance": _to_aud(int(row.total_balance_cents or 0)),
                "account_count": int(row.account_count or 0),
            }
            for row in rows
        ]
        return {
            "building_id": building_id,
            "as_at": _iso(datetime.now(timezone.utc)),
            "balances": balances,
        }

    async def get_trial_balance(self, *, building_id: str) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.get_trial_balance
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None

        query = """
            SELECT ga.account_code,
                   ga.account_name,
                   ga.account_type::text AS account_type,
                   f.fund_type::text AS raw_fund_type,
                   COALESCE(SUM(
                       CASE
                           WHEN jl.direction = 'debit' THEN jl.amount_cents
                           ELSE -jl.amount_cents
                       END
                   ), 0) AS net_balance_cents
            FROM finance.gl_accounts ga
            LEFT JOIN finance.journal_lines jl
              ON jl.gl_account_id = ga.gl_account_id
            LEFT JOIN finance.journal_entries je
              ON je.journal_entry_id = jl.journal_entry_id
             AND je.status = 'posted'
            LEFT JOIN finance.funds f
              ON f.fund_id = je.fund_id
            WHERE ga.scheme_id = :scheme_id
              AND ga.status = CAST('active' AS core.record_status)
            GROUP BY ga.account_code, ga.account_name, ga.account_type, f.fund_type
            ORDER BY ga.account_code
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            rows = (await session.execute(text(query), {"scheme_id": str(scheme["scheme_id"])})).fetchall()

        accounts: list[dict[str, Any]] = []
        total_debits = 0
        total_credits = 0
        for row in rows:
            net_cents = int(row.net_balance_cents or 0)
            debit_cents = net_cents if net_cents > 0 else 0
            credit_cents = abs(net_cents) if net_cents < 0 else 0
            total_debits += debit_cents
            total_credits += credit_cents
            accounts.append(
                {
                    "account_code": row.account_code,
                    "account_name": row.account_name,
                    "fund_type": _map_fund_type(row.raw_fund_type),
                    "account_type": row.account_type,
                    "debit": _to_aud(debit_cents),
                    "credit": _to_aud(credit_cents),
                }
            )

        return {
            "building_id": building_id,
            "as_at": _iso(datetime.now(timezone.utc)),
            "accounts": accounts,
            "total_debits": _to_aud(total_debits),
            "total_credits": _to_aud(total_credits),
            "is_balanced": total_debits == total_credits,
        }

    async def list_reconciliation_runs(
        self,
        *,
        building_id: str,
        bank_account_id: str | None = None,
        status_filter: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.list_reconciliation_runs
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None

        filters = ["rr.scheme_id = :scheme_id"]
        params: dict[str, Any] = {
            "scheme_id": str(scheme["scheme_id"]),
            "limit": limit,
            "offset": (page - 1) * limit,
        }
        if bank_account_id:
            try:
                UUID(bank_account_id)
            except ValueError:
                return None
            filters.append("rr.trust_account_id = CAST(:trust_account_id AS uuid)")
            params["trust_account_id"] = bank_account_id
        if status_filter:
            filters.append("rr.status = :status")
            params["status"] = status_filter
        where_sql = " AND ".join(filters)

        count_query = f"SELECT COUNT(*) FROM finance.reconciliation_runs rr WHERE {where_sql}"
        data_query = f"""
            SELECT rr.reconciliation_run_id::text AS id,
                   rr.trust_account_id::text AS bank_account_id,
                   rr.period_start::text AS statement_start_date,
                   rr.period_end::text AS statement_end_date,
                   rr.cashbook_balance_cents,
                   rr.bank_balance_cents,
                   rr.owner_ledger_balance_cents,
                   rr.difference_cents,
                   rr.status,
                   rr.generated_at,
                   ta.account_name
            FROM finance.reconciliation_runs rr
            LEFT JOIN finance.trust_accounts ta
              ON ta.trust_account_id = rr.trust_account_id
            WHERE {where_sql}
            ORDER BY rr.generated_at DESC
            LIMIT :limit OFFSET :offset
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            total = int((await session.execute(text(count_query), params)).scalar() or 0)
            rows = (await session.execute(text(data_query), params)).fetchall()

        runs = [
            {
                "id": row.id,
                "bank_account_id": row.bank_account_id,
                "bank_account_name": row.account_name,
                "status": row.status,
                "statement_start_date": row.statement_start_date,
                "statement_end_date": row.statement_end_date,
                "discrepancy_cents": int(row.difference_cents or 0),
                "discrepancy_display": _to_aud(int(row.difference_cents or 0)),
                "cashbook_balance_cents": int(row.cashbook_balance_cents or 0),
                "bank_balance_cents": int(row.bank_balance_cents or 0),
                "owner_ledger_balance_cents": int(row.owner_ledger_balance_cents or 0),
                "created_at": _iso(row.generated_at),
                "updated_at": _iso(row.generated_at),
            }
            for row in rows
        ]
        return {"runs": runs, "total": total, "page": page, "limit": limit}

    async def get_reconciliation_run(
        self,
        *,
        building_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.get_reconciliation_run
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None
        try:
            UUID(run_id)
        except ValueError:
            return None

        query = """
            SELECT rr.reconciliation_run_id::text AS id,
                   rr.trust_account_id::text AS bank_account_id,
                   rr.period_start::text AS statement_start_date,
                   rr.period_end::text AS statement_end_date,
                   rr.cashbook_balance_cents,
                   rr.bank_balance_cents,
                   rr.owner_ledger_balance_cents,
                   rr.difference_cents,
                   rr.status,
                   rr.generated_at,
                   ta.account_name
            FROM finance.reconciliation_runs rr
            LEFT JOIN finance.trust_accounts ta
              ON ta.trust_account_id = rr.trust_account_id
            WHERE rr.scheme_id = :scheme_id
              AND rr.reconciliation_run_id = CAST(:run_id AS uuid)
            LIMIT 1
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            row = (await session.execute(text(query), {
                "scheme_id": str(scheme["scheme_id"]),
                "run_id": run_id,
            })).fetchone()
        if row is None:
            return None
        return {
            "id": row.id,
            "building_id": building_id,
            "bank_account_id": row.bank_account_id,
            "bank_account_name": row.account_name,
            "status": row.status,
            "statement_start_date": row.statement_start_date,
            "statement_end_date": row.statement_end_date,
            "cashbook_balance_cents": int(row.cashbook_balance_cents or 0),
            "bank_balance_cents": int(row.bank_balance_cents or 0),
            "owner_ledger_balance_cents": int(row.owner_ledger_balance_cents or 0),
            "discrepancy_cents": int(row.difference_cents or 0),
            "discrepancy_display": _to_aud(int(row.difference_cents or 0)),
            "updated_at": _iso(row.generated_at),
        }

    async def list_statement_lines(
        self,
        *,
        building_id: str,
        run_id: str,
        matched_status: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.list_statement_lines
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None
        try:
            UUID(run_id)
        except ValueError:
            return None

        run = await self.get_reconciliation_run(building_id=building_id, run_id=run_id)
        if run is None:
            return None

        filters = [
            "bt.scheme_id = :scheme_id",
            "bt.trust_account_id = CAST(:trust_account_id AS uuid)",
            "bt.transaction_date >= :period_start",
            "bt.transaction_date <= :period_end",
        ]
        params: dict[str, Any] = {
            "scheme_id": str(scheme["scheme_id"]),
            "trust_account_id": run["bank_account_id"],
            # get_reconciliation_run() returns these ::text-cast for JSON/display
            # callers (routers/trust_reconciliation.py) — asyncpg needs a real
            # date object to bind against bt.transaction_date, a native date
            # column, or it raises DataError ("'str' object has no attribute
            # 'toordinal'") for every real call.
            "period_start": date.fromisoformat(run["statement_start_date"]),
            "period_end": date.fromisoformat(run["statement_end_date"]),
            "limit": limit,
            "offset": (page - 1) * limit,
        }
        if matched_status:
            filters.append("bt.reconciliation_status::text = :matched_status")
            params["matched_status"] = matched_status
        where_sql = " AND ".join(filters)

        count_query = f"SELECT COUNT(*) FROM finance.bank_transactions bt WHERE {where_sql}"
        data_query = f"""
            SELECT bt.bank_transaction_id::text AS id,
                   bt.transaction_date::text AS statement_date,
                   bt.amount_cents,
                   bt.description,
                   bt.reference AS external_reference,
                   bt.reconciliation_status::text AS matched_status,
                   COALESCE(r.journal_entry_id::text, '') AS matched_journal_entry_id
            FROM finance.bank_transactions bt
            LEFT JOIN finance.receipts r
              ON r.bank_transaction_id = bt.bank_transaction_id
            WHERE {where_sql}
            ORDER BY bt.transaction_date ASC, bt.created_at ASC
            LIMIT :limit OFFSET :offset
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            total = int((await session.execute(text(count_query), params)).scalar() or 0)
            rows = (await session.execute(text(data_query), params)).fetchall()

        return {
            "lines": [
                {
                    "id": row.id,
                    "statement_date": row.statement_date,
                    "amount_cents": int(row.amount_cents or 0),
                    "amount_display": _to_aud(int(row.amount_cents or 0)),
                    "description": row.description,
                    "external_reference": row.external_reference,
                    "matched_status": row.matched_status,
                    "matched_ledger_entry_ids": [row.matched_journal_entry_id] if row.matched_journal_entry_id else [],
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "limit": limit,
        }

    async def get_reconciliation_summary(
        self,
        *,
        building_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustReadService.get_reconciliation_summary
        Path: backend/services/trust_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        if scheme is None:
            return None

        run = await self.get_reconciliation_run(building_id=building_id, run_id=run_id)
        if run is None:
            return None

        query = """
            SELECT bt.reconciliation_status::text AS reconciliation_status,
                   COUNT(*) AS line_count,
                   COALESCE(SUM(bt.amount_cents), 0) AS net_total_cents,
                   COALESCE(SUM(ABS(bt.amount_cents)), 0) AS abs_total_cents
            FROM finance.bank_transactions bt
            WHERE bt.scheme_id = :scheme_id
              AND bt.trust_account_id = CAST(:trust_account_id AS uuid)
              AND bt.transaction_date >= :period_start
              AND bt.transaction_date <= :period_end
            GROUP BY bt.reconciliation_status
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            rows = (await session.execute(text(query), {
                "scheme_id": str(scheme["scheme_id"]),
                "trust_account_id": run["bank_account_id"],
                # See list_statement_lines() above for why this needs a
                # real date object, not the ::text-cast string get_reconciliation_run()
                # returns for its JSON/display callers.
                "period_start": date.fromisoformat(run["statement_start_date"]),
                "period_end": date.fromisoformat(run["statement_end_date"]),
            })).fetchall()

        matched_count = 0
        unmatched_count = 0
        total_unreconciled = 0
        total_abs = 0
        matched_abs = 0
        for row in rows:
            status = str(row.reconciliation_status or "")
            line_count = int(row.line_count or 0)
            net_total = int(row.net_total_cents or 0)
            abs_total = int(row.abs_total_cents or 0)
            total_abs += abs_total
            if status == "matched":
                matched_count += line_count
                matched_abs += abs_total
            else:
                unmatched_count += line_count
                total_unreconciled += net_total

        return {
            "run_id": run_id,
            "building_id": building_id,
            "status": run["status"],
            "matched_count": matched_count,
            "unmatched_bank_count": unmatched_count,
            "unmatched_internal_count": 0,
            "suggested_count": 0,
            "duplicate_warnings": [],
            "total_unreconciled_amount_cents": total_unreconciled,
            "total_unreconciled_display": _to_aud(total_unreconciled),
            "reconciliation_confidence": round((matched_abs / total_abs), 4) if total_abs else 0.0,
            "exceptions": [],
            "exception_count": 0,
            "discrepancy_cents": run["discrepancy_cents"],
            "discrepancy_display": _to_aud(run["discrepancy_cents"]),
            "last_updated": run["updated_at"],
            "statement_period": {
                "start": run["statement_start_date"],
                "end": run["statement_end_date"],
            },
        }
