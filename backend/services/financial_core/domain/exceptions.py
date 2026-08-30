# @featuretrace:financial_integration_v2 — Accounting-period resolution exceptions.
# Layer: domain
# Data flow: adapters/db_postgres/ledger_repo.py raises these; service.py lets them
#            propagate to callers (scripts / routers) to handle explicitly.
# Related: backend/services/financial_core/domain/ports.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          backend/services/financial_core/service.py
"""Exceptions for date-based accounting-period resolution.

Distinct types so callers can tell "no period covers this date at all" from
"a period exists but its status isn't permitted" from "more than one period
covers this date" (a data-integrity violation that must fail loud, never
silently pick one).
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID


class AccountingPeriodError(Exception):
    """Base for all accounting-period resolution failures."""


class NoAccountingPeriodForDate(AccountingPeriodError):
    """No finance.accounting_periods row (of any status) covers this scheme+date."""

    def __init__(self, scheme_id: UUID, effective_on: date):
        self.scheme_id = scheme_id
        self.effective_on = effective_on
        super().__init__(
            f"No accounting period covers {effective_on} for scheme {scheme_id}"
        )


class AccountingPeriodNotPermitted(AccountingPeriodError):
    """Exactly one period covers the date, but its status isn't in permitted_statuses."""

    def __init__(
        self,
        scheme_id: UUID,
        effective_on: date,
        period_id: UUID,
        status: str,
        permitted_statuses: frozenset,
    ):
        self.scheme_id = scheme_id
        self.effective_on = effective_on
        self.period_id = period_id
        self.status = status
        self.permitted_statuses = permitted_statuses
        super().__init__(
            f"Accounting period {period_id} covering {effective_on} has status={status!r}, "
            f"not in permitted {sorted(permitted_statuses)} — closed-period historical "
            f"authorisation required"
        )


class AmbiguousAccountingPeriod(AccountingPeriodError):
    """More than one period row covers this date — data integrity violation, must fail loud."""

    def __init__(self, scheme_id: UUID, effective_on: date, period_ids: list):
        self.scheme_id = scheme_id
        self.effective_on = effective_on
        self.period_ids = period_ids
        super().__init__(
            f"{len(period_ids)} accounting periods cover {effective_on} for scheme "
            f"{scheme_id}: {period_ids} — data integrity violation"
        )


class BankTransactionClaimConflict(AccountingPeriodError):
    """A bank transaction is already claimed by a different reconstruction batch."""

    def __init__(self, bank_transaction_id, claiming_batch_id: UUID, requested_batch_id: UUID):
        self.bank_transaction_id = bank_transaction_id
        self.claiming_batch_id = claiming_batch_id
        self.requested_batch_id = requested_batch_id
        super().__init__(
            f"bank_transaction_id={bank_transaction_id} is already claimed by batch "
            f"{claiming_batch_id}, cannot claim for batch {requested_batch_id}"
        )


class BankTransactionNotFound(AccountingPeriodError):
    """The bank transaction being claimed doesn't exist for this tenant/scheme."""

    def __init__(self, bank_transaction_id, tenant_id: UUID, scheme_id: UUID):
        self.bank_transaction_id = bank_transaction_id
        self.tenant_id = tenant_id
        self.scheme_id = scheme_id
        super().__init__(
            f"bank_transaction_id={bank_transaction_id} not found for "
            f"tenant={tenant_id} scheme={scheme_id}"
        )


class IdempotencyKeyCollision(AccountingPeriodError):
    """An idempotency key was replayed but the financial content doesn't agree."""

    def __init__(self, idempotency_key: str, existing_journal_entry_id: UUID, detail: str):
        self.idempotency_key = idempotency_key
        self.existing_journal_entry_id = existing_journal_entry_id
        self.detail = detail
        super().__init__(
            f"idempotency_key={idempotency_key!r} already used by journal "
            f"{existing_journal_entry_id}, but replay content disagrees: {detail}"
        )
