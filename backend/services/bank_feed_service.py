"""Bank feed ingestion service behind the provider-agnostic bank abstraction.

This service persists observed bank transactions into the legacy MongoDB
collections that the trust/reconciliation UI still expects today, but the
runtime provider selection now comes from the cutover configuration contract
instead of a hard-coded "mock Basiq" assumption.

Collections used:
  bank_transactions   : ingested bank transactions (building-scoped)
  bank_feed_runs      : ingestion run history (idempotency + audit)

Idempotency:
  Each transaction is deduplicated by (building_id, account_id, external_tx_id).
  Re-running the same feed will not create duplicates.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/services/bank_feed_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


# ─── Transaction schema (mirrors bank_transactions collection) ────────────────

class BankTransaction:
    """Value object representing a single bank transaction."""

    __slots__ = (
        "external_tx_id", "account_id", "building_id", "date",
        "amount", "description", "type", "balance", "fund_type",
        "matched", "match_confidence", "matched_to_id", "metadata",
    )

    def __init__(self, **kwargs):
        """Generated function header.

        Function: BankTransaction.__init__
        Path: backend/services/bank_feed_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    def to_dict(self) -> Dict[str, Any]:
        """Generated function header.

        Function: BankTransaction.to_dict
        Path: backend/services/bank_feed_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {slot: getattr(self, slot) for slot in self.__slots__}


# ─── Provider-neutral bank feed adapter ──────────────────────────────────────

class _BankFeedAdapter:
    """
    Adapter interface for the current provider-neutral bank feed abstraction.

    Supported in this wave:
    - ``mock``       → deterministic in-memory fixture feed
    - ``csv_replay`` → deterministic fixture replay using the same mock payloads

    Future live providers should plug in by implementing the same normalized
    transaction shape and routing their provider selection through
    ``services.cutover_config_service``.
    """

    def __init__(self, provider_name: str = "mock", provider_mode: str = "mock"):
        """Generated function header.

        Function: _BankFeedAdapter.__init__
        Path: backend/services/bank_feed_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.provider_name = (provider_name or "mock").strip().lower()
        self.provider_mode = (provider_mode or "mock").strip().lower()
        self._is_mock = self.provider_name in {"mock", "csv_replay"}

    async def fetch_transactions(
            self,
            connection_id: str,
            from_date: Optional[str] = None,
            to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return list of raw transaction dicts from the bank."""
        if self._is_mock:
            return self._mock_transactions(connection_id, from_date, to_date)
        raise NotImplementedError(
            f"Bank provider {self.provider_name!r} is not configured in this wave."
        )  # pragma: no cover

    def _mock_transactions(
            self,
            connection_id: str,
            from_date: Optional[str],
            to_date: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Generate deterministic mock transactions for development/testing."""
        seed = hashlib.md5(connection_id.encode()).hexdigest()[:8]
        today = datetime.now(timezone.utc).date()

        return [
            {
                "id": f"mock-{seed}-001",
                "postDate": str(today),
                "description": "LEVY PAYMENT - UNIT 101",
                "amount": "1250.00",
                "type": "credit",
                "balance": "125000.00",
            },
            {
                "id": f"mock-{seed}-002",
                "postDate": str(today),
                "description": "PLUMBING INVOICE INV-2026-0042",
                "amount": "-880.00",
                "type": "debit",
                "balance": "124120.00",
            },
            {
                "id": f"mock-{seed}-003",
                "postDate": str(today),
                "description": "COUNCIL RATES Q1 2026",
                "amount": "-2340.00",
                "type": "debit",
                "balance": "121780.00",
            },
            {
                "id": f"mock-{seed}-004",
                "postDate": str(today),
                "description": "INSURANCE PREMIUM ANNUAL",
                "amount": "-8450.00",
                "type": "debit",
                "balance": "113330.00",
            },
        ]


# ─── Ingestion Service ────────────────────────────────────────────────────────

class BankFeedService:
    """Orchestrates bank feed ingestion for a building's trust accounts."""

    def __init__(self, db, provider_name: str = "mock", provider_mode: str = "mock"):
        """Generated function header.

        Function: BankFeedService.__init__
        Path: backend/services/bank_feed_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.db = db
        self.provider_name = (provider_name or "mock").strip().lower()
        self.provider_mode = (provider_mode or "mock").strip().lower()
        self.adapter = _BankFeedAdapter(
            provider_name=self.provider_name,
            provider_mode=self.provider_mode,
        )

    async def ingest(
            self,
            building_id: str,
            account_id: str,  # trust_ledger_accounts._id (string)
            connection_id: str,  # Basiq connection ID (or mock identifier)
            fund_type: str,
            from_date: Optional[str] = None,
            to_date: Optional[str] = None,
            initiated_by: str = "system",
    ) -> Dict[str, Any]:
        """Fetch transactions from the adapter and ingest into bank_transactions.

        Returns a summary dict with counts: total, inserted, skipped (duplicates).
        """
        run_id = str(uuid.uuid4())
        run_doc = {
            "run_id": run_id,
            "building_id": building_id,
            "account_id": account_id,
            "connection_id": connection_id,
            "fund_type": fund_type,
            "from_date": from_date,
            "to_date": to_date,
            "initiated_by": initiated_by,
            "provider_name": self.provider_name,
            "provider_mode": self.provider_mode,
            "status": "running",
            "started_at": _now_iso(),
            "total": 0,
            "inserted": 0,
            "skipped": 0,
            "error": None,
        }
        await self.db.bank_feed_runs.insert_one(run_doc)

        try:
            raw_txs = await self.adapter.fetch_transactions(
                connection_id=connection_id,
                from_date=from_date,
                to_date=to_date,
            )

            inserted = 0
            skipped = 0

            for raw in raw_txs:
                external_id = raw.get("id") or hashlib.sha256(
                    f"{building_id}{raw.get('postDate')}{raw.get('description')}{raw.get('amount')}".encode()
                ).hexdigest()

                # Idempotency check
                existing = await self.db.bank_transactions.find_one({
                    "building_id": building_id,
                    "account_id": account_id,
                    "external_tx_id": external_id,
                })
                if existing:
                    skipped += 1
                    continue

                amount_raw = float(raw.get("amount", 0))
                tx_type = "credit" if amount_raw > 0 else "debit"

                doc = {
                    "building_id": building_id,
                    "account_id": account_id,
                    "fund_type": fund_type,
                    "external_tx_id": external_id,
                    "date": raw.get("postDate", str(datetime.now(timezone.utc).date())),
                    "amount": abs(amount_raw),
                    "signed_amount": amount_raw,
                    "description": raw.get("description", ""),
                    "type": tx_type,
                    "balance": float(raw.get("balance", 0)),
                    "matched": False,
                    "match_confidence": 0,
                    "matched_to_id": None,
                    "source": "bank_feed",
                    "connection_id": connection_id,
                    "ingested_at": _now_iso(),
                    "metadata": {},
                }
                await self.db.bank_transactions.insert_one(doc)
                inserted += 1

            # Update run record
            await self.db.bank_feed_runs.update_one(
                {"run_id": run_id},
                {"$set": {
                    "status": "completed",
                    "total": len(raw_txs),
                    "inserted": inserted,
                    "skipped": skipped,
                    "completed_at": _now_iso(),
                }},
            )

            return {
                "run_id": run_id,
                "total": len(raw_txs),
                "inserted": inserted,
                "skipped": skipped,
                "provider_name": self.provider_name,
                "provider_mode": self.provider_mode,
            }

        except Exception as exc:
            logger.error("Bank feed ingestion failed for building %s: %s", building_id, exc)
            await self.db.bank_feed_runs.update_one(
                {"run_id": run_id},
                {"$set": {
                    "status": "failed",
                    "error": str(exc),
                    "completed_at": _now_iso(),
                }},
            )
            raise
