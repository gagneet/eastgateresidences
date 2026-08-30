"""financial_core — The ONLY writer to the PostgreSQL financial ledger.

Public API (six methods only):
    create_levy(...)
    record_payment(...)
    allocate_payment(...)
    generate_arrears(...)
    reverse_entry(...)
    reconcile_bank_transaction(...)

No other module may write directly to finance.* or core.outbox.
"""
from typing import TYPE_CHECKING

from services.financial_core.service import FinancialCoreService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def get_financial_core_service(session: "AsyncSession") -> FinancialCoreService:
    """Construct a FinancialCoreService bound to an open SQLAlchemy session.

    The Postgres adapters (`PostgresLedgerRepository`, `PostgresOutboxRepository`)
    require a live AsyncSession — they call `session.execute(...)` and
    `session.add(...)` directly. Callers must therefore open
    ``async_session_context()`` and call ``set_tenant(session, tenant_id)``
    before invoking this factory, mirroring the pattern in
    ``services/financial_core/adapter.py``.

    Single source of truth so router modules don't drift on adapter renames
    (the v1 of this branch had two copies and they desynced — see migration
    history for the LedgerRepository → PostgresLedgerRepository rename).
    """
    from services.financial_core.adapters.db_postgres.ledger_repo import (
        PostgresLedgerRepository,
    )
    from services.financial_core.adapters.db_postgres.outbox_repo import (
        PostgresOutboxRepository,
    )

    return FinancialCoreService(
        ledger_repo=PostgresLedgerRepository(session),
        outbox_port=PostgresOutboxRepository(session),
        plugin_registry=None,  # TODO: wire plugin registry once Phase G lands.
    )


__all__ = ["FinancialCoreService", "get_financial_core_service"]
