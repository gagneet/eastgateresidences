# @featuretrace:financial_core — Feature-flag-gated route adapter. Intercepts financial writes.
# Layer: service
# Data flow: FastAPI routes → FinancialCoreAdapter → FinancialCoreService + core.outbox (building-scoped).
# Related: backend/services/financial_core/service.py
#           backend/workers/outbox_relay.py
"""FinancialCoreAdapter — intercepts financial writes and routes them through
the financial_core service when the feature flag is enabled.

Usage in existing FastAPI routes:
    from services.financial_core.adapter import FinancialCoreAdapter, get_adapter

    adapter = Depends(get_adapter)
    await adapter.record_payment(building_id, payment_data)

The adapter:
- Reads the canonical `financial_pg_writes_enabled` feature flag
- When OFF: delegates directly to existing MongoDB service (zero change)
- When ON:  routes writes through financial_core → Postgres + outbox

This is the ONLY place the feature flag is checked. Routes should not check it.

ADR-003: When ON, writes go to Postgres. MongoDB is updated asynchronously via
the outbox relay worker (backend/workers/outbox_relay.py).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from services.cutover_config_service import (
    FINANCIAL_PG_WRITES_ENABLED,
    TRUST_PG_LEDGER_ENABLED,
    is_cutover_feature_enabled,
)


async def _is_financial_core_enabled(building_id: str) -> bool:
    """Check the canonical Postgres financial write toggle for a building."""
    try:
        return await is_cutover_feature_enabled(building_id, FINANCIAL_PG_WRITES_ENABLED)
    except Exception as exc:
        logger.warning("Feature flag check failed, defaulting OFF: %s", exc)
        return False


class FinancialCoreAdapter:
    """
    Adapter layer that intercepts financial writes.

    All existing routes call methods on this adapter instead of writing to
    MongoDB directly. The adapter resolves the feature flag and routes
    accordingly.

    Parameters
    ----------
    building_id : str
        The MongoDB building_id (e.g. "13195"). Used for flag resolution
        and as the Mongo partition key on the legacy path.
    """

    def __init__(self, building_id: str) -> None:
        """Generated function header.

        Function: FinancialCoreAdapter.__init__
        Path: backend/services/financial_core/adapter.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._building_id = building_id
        self._enabled: Optional[bool] = None

    @property
    def is_financial_core_enabled(self) -> bool:
        """Generated function header.

        Function: FinancialCoreAdapter.is_financial_core_enabled
        Path: backend/services/financial_core/adapter.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return bool(self._enabled)

    async def record_payment(
            self,
            *,
            lot_id: str,
            payer_party_id: str,
            amount_dollars: float,
            channel: str,
            received_on: date,
            external_reference: Optional[str] = None,
            trust_account_id: Optional[str] = None,
            bank_transaction_id: Optional[str] = None,
            idempotency_key: Optional[str] = None,
            is_test_data: bool = False,
    ) -> dict:
        """Record an inbound levy payment.

        Returns a dict with `receipt_id` and `amount_cents` regardless of path.
        Shape is identical on both paths — no API contract change.
        """
        self._enabled = await _is_financial_core_enabled(self._building_id)
        if self._enabled:
            return await self._record_payment_postgres(
                lot_id=lot_id,
                payer_party_id=payer_party_id,
                amount_dollars=amount_dollars,
                channel=channel,
                received_on=received_on,
                external_reference=external_reference,
                trust_account_id=trust_account_id,
                bank_transaction_id=bank_transaction_id,
                idempotency_key=idempotency_key,
                is_test_data=is_test_data,
            )
        return await self._record_payment_mongo(
            lot_id=lot_id,
            amount_dollars=amount_dollars,
            channel=channel,
            received_on=received_on,
            external_reference=external_reference,
        )

    async def _record_payment_postgres(self, **kwargs) -> dict:
        """Route payment through financial_core → Postgres."""
        from services.financial_core.domain.entities import (
            PaymentChannel,
            RecordPaymentCommand,
            SchemeRef,
        )
        from services.financial_core.adapters.db_postgres.ledger_repo import (
            PostgresLedgerRepository,
        )
        from services.financial_core.adapters.db_postgres.outbox_repo import (
            PostgresOutboxRepository,
        )
        from services.financial_core.service import FinancialCoreService
        from db_postgres.session import async_session_context, set_tenant
        from db_postgres.engine import get_engine

        scheme_ref = await self._resolve_scheme_ref()
        amount_cents = int(round(float(kwargs["amount_dollars"]) * 100))

        async with async_session_context() as session:
            await set_tenant(session, scheme_ref.tenant_id)
            ledger_repo = PostgresLedgerRepository(session)
            outbox_repo = PostgresOutboxRepository(session)
            svc = FinancialCoreService(ledger_repo, outbox_repo)

            cmd = RecordPaymentCommand(
                scheme_ref=scheme_ref,
                payer_party_id=UUID(kwargs["payer_party_id"]) if kwargs.get("payer_party_id") else UUID(int=0),
                lot_id=UUID(kwargs["lot_id"]) if kwargs.get("lot_id") else UUID(int=0),
                channel=PaymentChannel(kwargs.get("channel", "manual_adjustment")),
                received_on=kwargs["received_on"],
                amount_cents=amount_cents,
                external_reference=kwargs.get("external_reference"),
                idempotency_key=kwargs.get("idempotency_key"),
                is_test_data=kwargs.get("is_test_data", False),
            )
            receipt = await svc.record_payment(cmd)

        return {
            "receipt_id": str(receipt.receipt_id),
            "amount_cents": receipt.amount_cents,
            "source": "postgres",
        }

    async def generate_levy(
            self,
            *,
            financial_year: str,
            quarter_no: Optional[int],
            issue_date: date,
            due_date: date,
            lot_levies: list[dict],
            idempotency_key: Optional[str] = None,
            is_test_data: bool = False,
    ) -> dict:
        """Dual-write: issue a levy run to Postgres when FINANCIAL_PG_WRITES_ENABLED.

        ``lot_levies`` is a list of dicts with keys:
            lot_id, owner_party_id, fund_id, principal_cents, gst_cents (optional).

        Returns ``{"levy_run_id": str, "item_count": int, "source": "postgres"}`` on
        the Postgres path or ``{"source": "mongo"}`` on the legacy path.
        """
        self._enabled = await _is_financial_core_enabled(self._building_id)
        if not self._enabled:
            logger.debug("Financial core OFF — levy generation via MongoDB legacy path")
            return {"source": "mongo"}
        return await self._generate_levy_postgres(
            financial_year=financial_year,
            quarter_no=quarter_no,
            issue_date=issue_date,
            due_date=due_date,
            lot_levies=lot_levies,
            idempotency_key=idempotency_key,
            is_test_data=is_test_data,
        )

    async def _generate_levy_postgres(self, **kwargs) -> dict:
        """Generated function header.

        Function: FinancialCoreAdapter._generate_levy_postgres
        Path: backend/services/financial_core/adapter.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        from uuid import UUID
        from services.financial_core.domain.entities import (
            CreateLevyCommand,
            LotLevySpec,
            SchemeRef,
        )
        from services.financial_core.adapters.db_postgres.ledger_repo import (
            PostgresLedgerRepository,
        )
        from services.financial_core.adapters.db_postgres.outbox_repo import (
            PostgresOutboxRepository,
        )
        from services.financial_core.service import FinancialCoreService
        from db_postgres.session import async_session_context, set_tenant

        scheme_ref = await self._resolve_scheme_ref()

        lot_levy_specs = [
            LotLevySpec(
                lot_id=UUID(spec["lot_id"]),
                owner_party_id=UUID(spec["owner_party_id"]),
                fund_id=UUID(spec["fund_id"]),
                principal_cents=int(spec["principal_cents"]),
                gst_cents=int(spec.get("gst_cents", 0)),
            )
            for spec in kwargs["lot_levies"]
        ]
        cmd = CreateLevyCommand(
            scheme_ref=scheme_ref,
            financial_year=kwargs["financial_year"],
            quarter_no=kwargs["quarter_no"],
            issue_date=kwargs["issue_date"],
            due_date=kwargs["due_date"],
            lot_levies=lot_levy_specs,
            idempotency_key=kwargs.get("idempotency_key"),
            is_test_data=kwargs.get("is_test_data", False),
        )

        async with async_session_context() as session:
            await set_tenant(session, scheme_ref.tenant_id)
            ledger_repo = PostgresLedgerRepository(session)
            outbox_repo = PostgresOutboxRepository(session)
            svc = FinancialCoreService(ledger_repo, outbox_repo)
            saved_items = await svc.create_levy(cmd)

        return {
            "levy_run_id": str(saved_items[0].levy_run_id) if saved_items else None,
            "item_count": len(saved_items),
            "source": "postgres",
        }

    async def post_trust_ledger(
            self,
            *,
            trust_account_id: str,
            amount_cents: int,
            transaction_date: date,
            description: str,
            source_type: str,
            source_reference: Optional[str] = None,
            idempotency_key: Optional[str] = None,
            is_test_data: bool = False,
    ) -> dict:
        """Dual-write: post a trust transaction to Postgres when TRUST_PG_LEDGER_ENABLED.

        Returns ``{"bank_transaction_id": str, "source": "postgres"}`` on the Postgres
        path or ``{"source": "mongo"}`` on the legacy path.

        The amount_cents sign convention: positive = money in (receipt),
        negative = money out (disbursement).
        """
        trust_enabled = await is_cutover_feature_enabled(
            self._building_id, TRUST_PG_LEDGER_ENABLED
        )
        if not trust_enabled:
            logger.debug("Trust PG ledger OFF — trust posting via MongoDB legacy path")
            return {"source": "mongo"}
        return await self._post_trust_ledger_postgres(
            trust_account_id=trust_account_id,
            amount_cents=amount_cents,
            transaction_date=transaction_date,
            description=description,
            source_type=source_type,
            source_reference=source_reference,
            idempotency_key=idempotency_key,
            is_test_data=is_test_data,
        )

    async def _post_trust_ledger_postgres(self, **kwargs) -> dict:
        """Generated function header.

        Function: FinancialCoreAdapter._post_trust_ledger_postgres
        Path: backend/services/financial_core/adapter.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        import uuid as _uuid
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        scheme_ref = await self._resolve_scheme_ref()

        # Use source_reference as the external_transaction_id for idempotency —
        # the schema has UNIQUE(trust_account_id, external_transaction_id).
        external_tx_id = kwargs.get("source_reference") or kwargs.get("idempotency_key")

        async with async_session_context() as session:
            await set_tenant(session, scheme_ref.tenant_id)

            if external_tx_id:
                existing = await session.execute(
                    text(
                        "SELECT bank_transaction_id::text FROM finance.bank_transactions "
                        "WHERE trust_account_id = CAST(:ta_id AS UUID) "
                        "  AND external_transaction_id = :ext_id LIMIT 1"
                    ),
                    {"ta_id": kwargs["trust_account_id"], "ext_id": external_tx_id},
                )
                row = existing.fetchone()
                if row:
                    return {"bank_transaction_id": row[0], "source": "postgres", "replayed": True}

            bank_tx_id = str(_uuid.uuid4())
            await session.execute(
                text(
                    """
                    INSERT INTO finance.bank_transactions
                        (bank_transaction_id, tenant_id, scheme_id, trust_account_id,
                         transaction_date, amount_cents, description, reference,
                         external_transaction_id, reconciliation_status,
                         source_type, transaction_origin)
                    VALUES
                        (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:sid AS UUID),
                         CAST(:ta_id AS UUID), :tx_date, :amount, :desc, :ref, :ext_id,
                         CAST('unmatched' AS finance.reconciliation_status),
                         :source_type, :transaction_origin)
                    """
                ),
                {
                    "id": bank_tx_id,
                    "tid": str(scheme_ref.tenant_id),
                    "sid": str(scheme_ref.scheme_id),
                    "ta_id": kwargs["trust_account_id"],
                    "tx_date": kwargs["transaction_date"],
                    "amount": int(kwargs["amount_cents"]),
                    "desc": kwargs["description"],
                    "ref": kwargs.get("source_reference"),
                    "ext_id": external_tx_id,
                    # Caller-supplied provenance (e.g. "manual_trust_deposit") — was silently
                    # dropped before this fix (kwargs["source_type"] was accepted by
                    # post_trust_ledger() but never reached the INSERT). transaction_origin is
                    # this module's own honest tag for the dual-write path itself, distinct from
                    # reconstructed_historical/bank_reconciled — see docs/finances/
                    # onboarding-reconstruction-workflow-canonical-spec.md §4 provenance rule.
                    "source_type": kwargs.get("source_type"),
                    "transaction_origin": "trust_ledger_dual_write",
                },
            )

        logger.info(
            "Trust ledger posted to Postgres: tx_id=%s amount_cents=%d",
            bank_tx_id,
            kwargs["amount_cents"],
        )
        return {"bank_transaction_id": bank_tx_id, "source": "postgres"}

    async def _record_payment_mongo(self, **kwargs) -> dict:
        """Legacy MongoDB payment recording (existing path, unchanged)."""
        logger.debug("Financial core OFF — payment via MongoDB legacy path")
        # The existing route handler continues to manage MongoDB writes.
        # This method is a pass-through stub; the route still calls Mongo directly.
        return {"source": "mongo"}

    async def _resolve_scheme_ref(self) -> "SchemeRef":
        """Resolve Postgres (tenant_id, scheme_id) from MongoDB building_id."""
        from sqlalchemy import text
        from db_postgres.engine import get_engine
        from db_postgres.session import make_session_factory
        from services.financial_core.domain.entities import SchemeRef

        engine = get_engine()
        session_factory = make_session_factory(engine)
        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT s.tenant_id, s.scheme_id "
                    "FROM core.schemes s "
                    "WHERE s.scheme_number = :number "
                    "LIMIT 1"
                ),
                {"number": self._building_id},
            )
            row = result.fetchone()
            if row is None:
                raise RuntimeError(
                    f"No Postgres scheme found for building_id={self._building_id!r}. "
                    "Run the data migration first."
                )
            return SchemeRef(tenant_id=row.tenant_id, scheme_id=row.scheme_id)


    async def record_expense_transaction(
            self,
            *,
            amount_dollars: float,
            gst_dollars: float,
            description: str,
            fund_type: str,
            category_name: str,
            financial_year: str,
            vendor_name: str,
            invoice_number: Optional[str] = None,
            transaction_date: Optional[str] = None,
            created_by: Optional[str] = None,
            idempotency_key: Optional[str] = None,
            is_test_data: bool = False,
    ) -> dict:
        """Record a confirmed invoice expense in the financial ledger.

        When ``financial_pg_writes_enabled`` is ON, the expense is written to
        ``finance.journal_entries`` + ``finance.journal_lines`` via
        ``FinancialCoreService.record_expense()``.

        Returns a dict with ``journal_entry_id`` on the Postgres path or
        ``{"source": "mongo"}`` on the legacy path.
        """
        self._enabled = await _is_financial_core_enabled(self._building_id)
        if not self._enabled:
            logger.debug("Financial core OFF — expense transaction via MongoDB legacy path")
            return {"source": "mongo"}
        return await self._record_expense_postgres(
            amount_dollars=amount_dollars,
            gst_dollars=gst_dollars,
            description=description,
            fund_type=fund_type,
            category_name=category_name,
            financial_year=financial_year,
            vendor_name=vendor_name,
            invoice_number=invoice_number,
            transaction_date=transaction_date,
            created_by=created_by,
            idempotency_key=idempotency_key,
            is_test_data=is_test_data,
        )

    async def _record_expense_postgres(self, **kwargs) -> dict:
        """Route an expense through FinancialCoreService.record_expense().

        Replaces this method's former raw-SQL body (which bypassed
        FinancialCoreService — "the ONLY authorised writer to the PostgreSQL
        ledger" — and picked GL accounts by first-match-of-type). Wiring
        mirrors _record_payment_postgres exactly.
        """
        from datetime import date as _dt_date
        from services.financial_core.domain.entities import RecordExpenseCommand
        from services.financial_core.adapters.db_postgres.ledger_repo import (
            PostgresLedgerRepository,
        )
        from services.financial_core.adapters.db_postgres.outbox_repo import (
            PostgresOutboxRepository,
        )
        from services.financial_core.genesis import resolve_scheme_fund_ids
        from services.financial_core.service import FinancialCoreService
        from db_postgres.session import async_session_context, set_tenant

        scheme_ref = await self._resolve_scheme_ref()

        amount_cents = int(round(float(kwargs["amount_dollars"]) * 100))
        gst_cents = int(round(float(kwargs.get("gst_dollars", 0) or 0) * 100))

        tx_date_str = kwargs.get("transaction_date")
        try:
            transaction_date = _dt_date.fromisoformat(str(tx_date_str)[:10]) if tx_date_str else _dt_date.today()
        except (ValueError, TypeError):
            transaction_date = _dt_date.today()

        async with async_session_context() as session:
            await set_tenant(session, scheme_ref.tenant_id)
            ledger_repo = PostgresLedgerRepository(session)
            outbox_repo = PostgresOutboxRepository(session)
            svc = FinancialCoreService(ledger_repo, outbox_repo)
            fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)
            fund_type = str(kwargs.get("fund_type") or "admin").strip().lower()
            if fund_type in ("administrative", "administration", "admin_fund"):
                fund_type = "admin"
            elif fund_type in ("capital_works", "capital works", "capital-works", "reserve"):
                fund_type = "sinking"
            fund_id = fund_ids.get(fund_type)
            if fund_id is None:
                raise ValueError(f"Unknown fund_type for expense transaction: {kwargs.get('fund_type')!r}")

            cmd = RecordExpenseCommand(
                scheme_ref=scheme_ref,
                category_name=kwargs["category_name"],
                amount_cents=amount_cents,
                gst_cents=gst_cents,
                transaction_date=transaction_date,
                fund_id=fund_id,
                vendor_name=kwargs.get("vendor_name"),
                invoice_number=kwargs.get("invoice_number"),
                financial_year=kwargs.get("financial_year"),
                description=kwargs.get("description"),
                source="invoice_confirmed",
                derivation_level="exact",
                idempotency_key=kwargs.get("idempotency_key"),
                is_test_data=kwargs.get("is_test_data", False),
            )
            expense = await svc.record_expense(cmd)

        logger.info(
            "Invoice expense recorded to Postgres: journal_entry_id=%s amount_cents=%d",
            expense.journal_entry_id,
            amount_cents,
        )
        return {"journal_entry_id": str(expense.journal_entry_id), "source": "postgres"}


def get_adapter(building_id: str) -> FinancialCoreAdapter:
    """FastAPI dependency factory. Use via Depends(lambda: get_adapter(building_id))."""
    return FinancialCoreAdapter(building_id)
