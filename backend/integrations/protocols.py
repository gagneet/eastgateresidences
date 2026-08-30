"""
backend/integrations/protocols.py — The five Protocol boundaries.

# @featuretrace:financial_integration_v2 — provider contract definitions.
# Layer: service
# Data flow: real/mock adapter → Protocol method → EventEnvelope → integration_inbox (building-scoped).
# Related: envelopes.py (return types), registry.py (DI), mocks/ (default impls).

These five Protocols define everything the platform needs from external financial
counterparties. No implementation detail from any specific provider (Basiq, DEFT,
Monoova, Xero, Textract) appears here — only the shapes the platform consumes.

Design choices:
  - typing.Protocol with structural subtyping: mocks need no shared ancestor.
    CI runs without any real vendor SDK installed.
  - runtime_checkable is intentionally omitted: we rely on contract tests, not
    isinstance() checks, to verify compliance at the boundary.
  - Every method has full type signatures. No Any. No dict return types.
  - `name: str` class attribute identifies the provider in logs and toggles.
  - All async: the platform is fully async (Motor + FastAPI). Synchronous
    implementations must be wrapped with asyncio.to_thread at the adapter.

Invariants every implementation MUST uphold:
  1. tenant_id is injected by the adapter — never derived from raw provider data.
     A provider should not know which building it belongs to.
  2. idempotency_key on every outbound call carries a platform-generated key
     so that a retry does not produce a duplicate effect at the provider.
  3. Monetary values are always integer cents in returned envelopes.
     Coerce provider floats at the adapter boundary, never in domain code.
  4. pull_* methods return AsyncIterator for backpressure-friendly streaming.
     Implementations may batch internally but must yield one item at a time.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

from integrations.envelopes import (
    AccountingBill,
    BankAccountMetadata,
    BankTxObserved,
    BillerBatchRecord,
    BillerPaymentObserved,
    BillerReference,
    EventEnvelope,
    OCRExtractionResult,
    PaymentRun,
    PaymentRunAccepted,
)


class BankFeedProvider(Protocol):
    """Anything that can deliver observed bank transactions into the platform.

    Default implementation: csv_upload_bank_feed (mock).
    Future implementations: Basiq, Frollo, Illion BankStatements.

    Invariants:
      - pull_transactions must be idempotent: polling the same window twice
        yields the same transactions (same provider_txn_id values).
      - verify_webhook must return False rather than raise on bad signatures.
      - parse_webhook must inject tenant_id from the adapter's configuration,
        NOT from the webhook payload (a provider should not dictate tenancy).
    """

    name: str

    async def pull_transactions(
            self,
            account_ref: str,
            since: datetime,
    ) -> AsyncIterator[BankTxObserved]:
        """Incrementally fetch transactions since a given timestamp.

        Implementations should use the account_ref to look up credentials.
        The caller is responsible for persisting the high-water mark.
        """
        ...

    async def verify_webhook(
            self,
            headers: dict[str, str],
            raw_body: bytes,
    ) -> bool:
        """Return True iff the webhook is cryptographically authentic.

        Never raise on failure — return False so the caller can respond 401.
        """
        ...

    async def parse_webhook(
            self,
            raw_body: bytes,
    ) -> list[EventEnvelope]:
        """Translate a raw webhook body into zero or more EventEnvelopes.

        A single webhook payload may contain multiple transactions.
        tenant_id is injected by the adapter, not parsed from the payload.
        """
        ...

    async def list_accounts(
            self,
            consent_id: str,
    ) -> list[BankAccountMetadata]:
        """Return metadata for all accounts the provider has consent to observe."""
        ...


class BillerProvider(Protocol):
    """The biller side of the payment rail — issues and manages CRNs/DRNs.

    Default implementation: mock_biller (MOD10V05 CRN generator).
    Future implementations: DEFT, StrataPay, direct BPAY biller relationship.

    Invariants:
      - allocate_reference is idempotent: the same (scheme_id, lot_id,
        instalment_seq) always returns the same CRN.
      - pull_payment_feedback must not yield the same feedback_id twice
        across calls for the same window.
      - reconcile_batch total must match what the bank feed sees as the
        single consolidated credit for that day.
    """

    name: str

    async def allocate_reference(
            self,
            scheme_id: str,
            lot_id: str,
            instalment_seq: int,
    ) -> BillerReference:
        """Allocate (or return existing) CRN/DRN for a lot+instalment pair.

        scheme_id is the building_id-derived scheme identifier.
        lot_id is the unit/lot number as a string.
        instalment_seq is the quarter or instalment number (1-based).
        """
        ...

    async def pull_payment_feedback(
            self,
            since: datetime,
    ) -> AsyncIterator[BillerPaymentObserved]:
        """Fetch per-lot payment decompositions since a given timestamp.

        This is the DEFT TXN-file equivalent — one yield per lot payment,
        even though the bank feed sees a single consolidated credit.
        """
        ...

    async def reconcile_batch(
            self,
            batch_date: datetime,
    ) -> BillerBatchRecord:
        """Return the consolidated settlement the biller credited that day.

        Used to verify the bank feed's single deposit matches the biller's
        sum of per-lot receipts.
        """
        ...


class PaymentInitiationProvider(Protocol):
    """Anything that can push money out of the trust account.

    Default implementation: mock_aba_writer (Cemtex ABA file generator).
    Future implementations: Monoova (PayTo), CBA CommBiz direct API.

    Invariants:
      - initiate_batch is idempotent per payment_run.run_id: submitting the
        same run_id twice must not produce a duplicate bank transaction.
        Implementations must use run_id as their idempotency key to the
        provider or detect and short-circuit duplicate submissions.
      - cancel_payment returns False (not raise) when cancellation is
        impossible (already settled).
      - The aba_sha256 on PaymentRunAccepted is the authoritative audit proof
        that the file content has not changed since generation.
    """

    name: str

    async def initiate_batch(
            self,
            payment_run: PaymentRun,
    ) -> PaymentRunAccepted:
        """Submit a batch of outbound disbursements.

        payment_run.idempotency_key carries the platform-generated key that
        the provider must honour to prevent double-payment on retry.
        """
        ...

    async def pull_status_updates(
            self,
            since: datetime,
    ) -> AsyncIterator[EventEnvelope]:
        """Fetch settlement or failure updates for in-flight payment runs."""
        ...

    async def cancel_payment(
            self,
            reference: str,
    ) -> bool:
        """Attempt cancellation. Return True if cancelled, False if too late."""
        ...


class AccountingProvider(Protocol):
    """Supplier-side accounting integration (Xero, MYOB, etc.).

    Default implementation: mock_accounting_source.
    Future implementations: Xero (post-March-2026 granular scopes), MYOB.

    Invariants:
      - list_bills returns bills in ascending order of issue date.
      - submit_invoice carries an idempotency_key so a retry does not
        create a duplicate bill in the accounting system.
      - verify_webhook and parse_webhook mirror BankFeedProvider semantics.
    """

    name: str

    async def list_bills(
            self,
            since: datetime,
    ) -> list[AccountingBill]:
        """Fetch bills the provider knows about since a given timestamp."""
        ...

    async def submit_invoice(
            self,
            invoice: dict,
            idempotency_key: str,
    ) -> EventEnvelope:
        """Push a strata-platform invoice into the provider's accounting system.

        idempotency_key is platform-generated and must be forwarded to the
        provider's own deduplication mechanism.
        """
        ...

    async def verify_webhook(
            self,
            headers: dict[str, str],
            raw_body: bytes,
    ) -> bool:
        """Return True iff the webhook is cryptographically authentic."""
        ...

    async def parse_webhook(
            self,
            raw_body: bytes,
    ) -> list[EventEnvelope]:
        """Translate a raw webhook body into EventEnvelopes."""
        ...


class OCRProvider(Protocol):
    """Invoice and bank statement text extraction.

    Default implementation: mock_ocr (regex + heuristics, deterministic).
    Future implementations: AWS Textract AnalyzeExpense, Azure Document Intelligence.

    Invariants:
      - extract_invoice must be deterministic: same bytes always yield the
        same result (important for test reproducibility).
      - All monetary values in OCRExtractionResult are integer cents.
        The adapter coerces provider floats before constructing the result.
      - confidence scores are in [0.0, 1.0]. The approval UI uses them to
        highlight low-confidence fields for human review.
    """

    name: str

    async def extract_invoice(
            self,
            document_bytes: bytes,
            mime_type: str,
    ) -> OCRExtractionResult:
        """Extract structured fields from an invoice PDF or image.

        mime_type is one of: "application/pdf", "image/png", "image/jpeg".
        Returns all fields with per-field confidence scores.
        """
        ...

    async def extract_bank_statement(
            self,
            document_bytes: bytes,
    ) -> list[BankTxObserved]:
        """Extract transactions from a PDF or CSV bank statement.

        Used by the mock CSV upload path and future PDF statement ingestion.
        Returns an empty list on parse failure — never raises.
        """
        ...
