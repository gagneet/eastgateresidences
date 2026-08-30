"""PostgreSQL implementation of OutboxPort.

Inserts rows into core.outbox within the current session/transaction.
The outbox relay worker (backend/workers/outbox_relay.py) picks them up asynchronously.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from services.financial_core.adapters.db_postgres.models import PgOutbox
from services.financial_core.domain.entities import SchemeRef


class PostgresOutboxRepository:
    """Implements OutboxPort using the active SQLAlchemy async session."""

    def __init__(self, session) -> None:
        """Generated function header.

        Function: PostgresOutboxRepository.__init__
        Path: backend/services/financial_core/adapters/db_postgres/outbox_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._session = session

    async def publish(
            self,
            scheme_ref: SchemeRef,
            event_type: str,
            payload: dict,
    ) -> None:
        """Generated function header.

        Function: PostgresOutboxRepository.publish
        Path: backend/services/financial_core/adapters/db_postgres/outbox_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        row = PgOutbox(
            id=uuid.uuid4(),
            tenant_id=scheme_ref.tenant_id,
            scheme_id=scheme_ref.scheme_id,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(tz=timezone.utc),
            published_at=None,
        )
        self._session.add(row)
        await self._session.flush()
