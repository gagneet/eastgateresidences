# @featuretrace:demo_bank — Bank feed ingestion, Demo Bank sync, and strata-year summary endpoints.
# Layer: router
# Data flow: ProviderRegistry → GET/POST /bank-feeds/* → demo_bank_transactions (Mongo staging) →
#            POST /bank-feeds/sync → finance.bank_transactions (Postgres) →
#            MatchingEngine.match() → match_review_queue (building-scoped)
# Related: backend/integrations/demo_bank/provider.py
#          backend/integrations/demo_bank/ingestion.py
#          backend/routers/financial_matching.py
#          backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py
#          tests/backend/test_demo_bank_provider.py
# Toggle: demo_bank_feed_enabled, bank_feeds_sync_enabled
# Collection: demo_bank_transactions, demo_bank_accounts, demo_bank_import_batches
# Table: finance.bank_transactions, finance.receipts

"""
Bank Feeds Router — API endpoints for bank feed ingestion and transaction management.

Endpoints:
  POST  /bank-feeds/ingest              — trigger bank feed ingestion (mock or live)
  POST  /bank-feeds/sync                — sync Demo Bank provider transactions to Postgres
  POST  /bank-feeds/rematch             — replay existing Postgres transactions through matching
  GET   /bank-feeds/transactions        — list bank transactions (building-scoped)
  GET   /bank-feeds/transactions/{id}   — single transaction
  PATCH /bank-feeds/transactions/{id}/match  — manually match a transaction
  GET   /bank-feeds/runs                — list ingestion run history
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import asyncio
import json
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from typing import Any, Optional

from services.bank_feed_service import BankFeedService
from services.cutover_config_service import get_banking_mode, get_banking_provider
from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from integrations.envelopes import BankTxObserved
from integrations.demo_bank.ingestion import _extract_signals
from integrations.demo_bank.provider import DemoBankFeed
from integrations.demo_bank.schemas import PROVIDER as DEMO_BANK_PROVIDER
from request_context import set_ctx_building_id
from utils.auth import get_current_user, get_current_building, effective_role
from utils.helpers import create_audit_log
from utils.permissions import require_feature
from services.settings_service import get_general_settings_or_default

logger = logging.getLogger(__name__)
# integrations.matching.engine.match()'s score is always <= 1.0 (see its docstring
# and _DEFAULT_AUTO_MATCH_THRESHOLD=0.90) — any threshold above 1.0 can never be
# exceeded, so passing this as auto_match_threshold makes is_auto_allocated
# permanently False regardless of match confidence. Used by
# disable_auto_allocation=True call sites (historical/backfill sync + rematch)
# instead of inventing a separate "review_only" code path in the engine itself.
_AUTO_ALLOCATION_DISABLED_THRESHOLD = 1.01

# finance.bank_transactions.transaction_origin fallback when a Demo Bank transaction's own
# transaction_origin/reconstruction metadata is unset (migration 0085, GAP-ONBOARD-004 B1b) --
# `demo_bank/ingestion.py` only populates transaction_origin explicitly for
# import_historical_reconstruction() call sites; csv_upload/manual/strata_web_*/seed leave it at
# their None default, so a fallback keyed on the real, always-present `source_type` field is
# required to avoid mislabelling them as 'bank_reconciled' (a false "confirmed against a real
# Bank/Trust feed" claim -- see _tx_from_envelope's docstring). Mapping confidence varies:
#   - csv_upload: HIGH -- matches the vocabulary's own 'external_import' definition exactly
#     ("real observed cash from a DEFT/BPAY/bank CSV import").
#   - seed: HIGH -- matches migration 0085's own backfill choice for this exact seed data.
#   - strata_web_inferred_payment: HIGH -- matches 'consecutive_snapshot_delta's definition
#     exactly ("inferred movement between two portal snapshots" -- literally what
#     strata_web_balance_inference_service.py's own docstring says it does).
#   - strata_web_payment: MEDIUM -- best available existing tag (portal-sourced), but the
#     vocabulary has no tag distinguishing "portal-confirmed payment row" from "portal balance
#     scrape"; may warrant its own vocabulary value in a future migration.
#   - manual: LOW -- 'manual_adjustment' technically means "correction with a known cause," not
#     "first-time manual entry." No better existing tag fits; flagged here rather than assumed
#     correct. A dedicated vocabulary value (e.g. 'demo_bank_manual_entry') may be warranted.
_SOURCE_TYPE_TO_TRANSACTION_ORIGIN: dict[str, str] = {
    "csv_upload": "external_import",
    "seed": "reconstructed_historical",
    "strata_web_inferred_payment": "consecutive_snapshot_delta",
    "strata_web_payment": "strata_web_portal_scrape",
    "manual": "manual_adjustment",
}

# GAP-FT-002: Bank feed ingestion is part of FIL v2 (disabled by default). Gate the
# entire router so it cannot be reached when FIL v2 is off for a building.
router = APIRouter(
    prefix="/bank-feeds",
    tags=["Bank Feeds"],
    dependencies=[Depends(require_feature("financial_integration_layer_v2"))],
)


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_finance(current_user: dict) -> dict:
    """Generated function header.

    Function: _require_finance
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member"}
    if effective_role(current_user) not in allowed:
        raise HTTPException(403, "Finance access required.")
    return current_user


def _require_sync(current_user: dict) -> dict:
    """Generated function header.

    Function: _require_sync
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_admin"}
    if effective_role(current_user) not in allowed:
        raise HTTPException(403, "Bank-feed sync access required.")
    return current_user


class BankFeedSyncRequest(BaseModel):
    """Request body for POST /bank-feeds/sync."""

    account_ref: Optional[str] = Field(default=None, max_length=64)
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    include_test_data: bool = False
    reconstruction_batch_id: Optional[str] = Field(
        default=None,
        description=(
            "When set, only Demo Bank transactions tagged with this "
            "reconstruction_batch_id (see integrations.demo_bank."
            "provider.DemoBankFeed._doc_to_envelope's metadata field) are synced; "
            "every other staged transaction is skipped. Used by financial_onboarding.py's "
            "reconstruction-batch /sync endpoint to scope sync to one batch."
        ),
    )
    disable_auto_allocation: bool = Field(
        default=False,
        description=(
            "When true, every matched transaction from this sync lands in "
            "match_review_queue with status='pending' for human review, "
            "regardless of MatchingEngine confidence score — the score-based "
            "auto-allocate path (routers.financial_matching.auto_allocate_queue_item, "
            "which posts straight to the ledger) can never fire. Set true for any "
            "historical/backfill sync where transaction provenance hasn't been "
            "independently verified against a real bank statement; default False "
            "preserves current behaviour for live, ongoing bank feeds."
        ),
    )


class BankFeedRematchRequest(BaseModel):
    """Request body for POST /bank-feeds/rematch."""

    account_ref: str = Field(..., max_length=64)
    limit: int = Field(default=2000, ge=1, le=10000)
    disable_auto_allocation: bool = Field(
        default=False,
        description="Same contract as BankFeedSyncRequest.disable_auto_allocation — "
                    "set true for historical/backfill rematch runs.",
    )


def _date_to_utc_start(value: date | None) -> datetime | None:
    """Generated function header.

    Function: _date_to_utc_start
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _date_to_utc_end(value: date | None) -> datetime | None:
    """Generated function header.

    Function: _date_to_utc_end
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def _account_query(building_id: str, include_test_data: bool) -> dict[str, Any]:
    """Generated function header.

    Function: _account_query
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict[str, Any] = {
        "building_id": building_id,
        "provider": DEMO_BANK_PROVIDER,
        "status": "active",
    }
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}
    return query


def _normalise_posted_date(value: Any) -> date:
    """Generated function header.

    Function: _normalise_posted_date
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    raise ValueError("Demo Bank transaction is missing a valid posted_date")


def _normalise_pg_tx_datetime(value: Any) -> datetime:
    """Generated function header.

    Function: _normalise_pg_tx_datetime
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError("Postgres bank transaction is missing a valid transaction_date")


def _pg_amount_cents(tx: dict[str, Any]) -> int:
    """Generated function header.

    Function: _pg_amount_cents
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    amount = int(tx.get("amount_cents") or 0)
    if "direction" not in tx:
        if amount == 0:
            raise ValueError("Bank transaction amount_cents must be non-zero")
        return amount

    abs_amount = abs(amount)
    if abs_amount <= 0:
        raise ValueError("Demo Bank transaction amount_cents must be positive")
    return abs_amount if tx.get("direction") == "credit" else -abs_amount


def _tx_from_envelope(event: BankTxObserved, source_doc: dict[str, Any] | None) -> dict[str, Any]:
    """Convert the provider envelope to the minimal Postgres insert payload.

    The signed amount must come from DemoBankFeed, not from the Mongo staging
    document, because the provider is the bank-feed abstraction boundary. The
    optional source document is used only for non-canonical extras such as the
    bank reference and for updating the Demo Bank UI sync cache.

    Provenance/confidence fields (source_type, confidence, provenance_class, etc.)
    are Demo-Bank-specific metadata, not part of the provider-neutral BankTxObserved
    envelope — they are copied from source_doc the same way `reference` already is,
    so future real providers (which have no such document) simply carry no
    provenance and default to NULL/False on the Postgres side.

    Reconstruction metadata (reconstruction_batch_id, reconstruction_version,
    assumption_code) is read from event.metadata first — the provider-neutral
    envelope contract per Phase 0B — falling back to the Mongo side-channel doc
    for providers/call sites that populate source_doc but not the envelope's
    optional metadata field. Real providers that omit both simply carry
    NULL/None for those three fields, same as before this change.

    transaction_origin is the one exception: `finance.bank_transactions.
    transaction_origin` is NOT NULL as of migration 0085 (GAP-ONBOARD-004 B1b).
    CORRECTED 2026-08-10 (self-audit): an earlier version of this fix defaulted
    every unset case straight to 'bank_reconciled'. That is wrong for the
    common case — `run_bank_feed_sync()` is currently the ONLY caller of this
    function, always via `DemoBankFeed()` (never a real external provider), and
    `demo_bank/ingestion.py`'s own docstring confirms `transaction_origin` is
    left at its None default for every CSV-upload/manual/Strata-Web-inferred
    Demo Bank transaction (only `import_historical_reconstruction()` sets it
    explicitly). Those are real Demo Bank data, genuinely NOT confirmed against
    a bank feed — tagging them 'bank_reconciled' would be a false provenance
    claim, not just a missing one. Falls back on `source_type` (present on
    every `demo_bank_transactions` document) instead, via
    `_SOURCE_TYPE_TO_TRANSACTION_ORIGIN`. Only `source_doc is None` (no Mongo
    side-channel document at all — the one scenario that actually indicates a
    real, non-Demo-Bank provider) falls through to `bank_reconciled`.
    """
    src = source_doc or {}
    meta = event.metadata or {}
    if source_doc is None:
        # No Mongo side-channel document at all -- the one case that actually indicates a
        # real, non-Demo-Bank provider observing this transaction directly. That IS
        # genuinely a real, bank-confirmed movement.
        _fallback_origin = "bank_reconciled"
    else:
        # Demo Bank data with no explicit transaction_origin -- derive from source_type
        # (see _SOURCE_TYPE_TO_TRANSACTION_ORIGIN's confidence notes). Unrecognised
        # source_type falls back to 'reconstructed_historical' (the vocabulary's own
        # "default onboarding tag") rather than the false certainty of 'bank_reconciled'.
        _fallback_origin = _SOURCE_TYPE_TO_TRANSACTION_ORIGIN.get(
            src.get("source_type"), "reconstructed_historical"
        )
    return {
        "posted_date": event.occurred_at,
        "description": event.description,
        "reference": src.get("reference"),
        "amount_cents": int(event.amount_cents),
        "running_balance_cents": event.balance_after_cents,
        "external_transaction_id": event.provider_txn_id,
        "provider": DEMO_BANK_PROVIDER,
        "source_type": src.get("source_type"),
        "confidence": src.get("confidence"),
        "provenance_class": src.get("provenance_class"),
        "evidence_type": src.get("evidence_type"),
        "formula_version": src.get("formula_version"),
        "source_snapshot_ids": src.get("source_snapshot_ids") or [],
        "supersedes_event_id": src.get("supersedes_event_id"),
        "requires_review": bool(src.get("requires_review") or False),
        "date_basis": src.get("date_basis"),
        "unit_number": src.get("unit_number"),
        "transaction_origin": (
            meta.get("transaction_origin") or src.get("transaction_origin") or _fallback_origin
        ),
        "reconstruction_batch_id": meta.get("reconstruction_batch_id") or src.get("reconstruction_batch_id"),
        "reconstruction_version": meta.get("reconstruction_version") or src.get("reconstruction_version"),
        "assumption_code": meta.get("assumption_code") or src.get("assumption_code"),
        "reconstruction_metadata": meta,
        "allocations": src.get("allocations") or [],
    }


def _split_allocations_by_fund(allocations: list[dict[str, Any]] | None) -> dict[str, int]:
    """Sum a combined Demo Bank transaction's per-fund allocation lines by fund_type.

    Demo Bank always stores ONE combined amount per owner-levy payment (or
    expense) — admin + sinking + GST paid/spent together, never as separate
    Demo Bank rows. Splitting that single amount into the correct per-fund GL
    postings is this application's job, not Demo Bank's; `allocations` (set by
    integrations.demo_bank.ingestion.import_historical_reconstruction's
    payment_group_id grouping) is where that per-fund breakdown already lives.
    Each line's `amount_cents` is that fund's own inc-GST portion. Returns {}
    when there's nothing to split (single-fund transaction, no allocations
    recorded — e.g. a real bank feed, which has no such concept).
    """
    totals: dict[str, int] = {}
    for line in allocations or []:
        fund_type = line.get("fund_type")
        amount = line.get("amount_cents")
        if not fund_type or amount is None:
            continue
        totals[fund_type] = totals.get(fund_type, 0) + int(amount)
    return totals


def _fund_type_for_account(account: dict[str, Any] | None) -> str | None:
    """Generated function header.

    Function: _fund_type_for_account
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not account:
        return None
    account_type = str(account.get("account_type") or "").lower()
    if "sinking" in account_type or "capital" in account_type:
        return "sinking"
    if "admin" in account_type or "operating" in account_type:
        return "admin"
    return None


async def _account_refs_for_sync(db, *, building_id: str, payload: BankFeedSyncRequest) -> list[str]:
    """Generated function header.

    Function: _account_refs_for_sync
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if payload.account_ref:
        return [payload.account_ref]

    accounts = await (
        db._db.demo_bank_accounts.find(_account_query(building_id, payload.include_test_data))
        .sort("account_ref", 1)
        .to_list(length=None)
    )
    return [str(account["account_ref"]) for account in accounts if account.get("account_ref")]


async def _source_doc_for_event(db, *, building_id: str, event: BankTxObserved) -> dict[str, Any] | None:
    """Generated function header.

    Function: _source_doc_for_event
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db._db.demo_bank_transactions.find_one({
        "building_id": building_id,
        "account_ref": event.account_ref,
        "provider": DEMO_BANK_PROVIDER,
        "external_transaction_id": event.provider_txn_id,
        "is_archived": {"$ne": True},
    })


async def _resolve_trust_account(
    session,
    *,
    scheme_id: str,
    account_ref: str,
    fund_type: str | None,
) -> str | None:
    """Generated function header.

    Function: _resolve_trust_account
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT ta.trust_account_id::text AS trust_account_id
                FROM finance.trust_accounts ta
                WHERE ta.scheme_id = CAST(:scheme_id AS uuid)
                  AND ta.external_uid = :account_ref
                  AND ta.status != CAST('archived' AS core.record_status)
                LIMIT 1
                """
            ),
            {"scheme_id": scheme_id, "account_ref": account_ref},
        )
    ).first()
    if row:
        return str(row.trust_account_id)

    if not fund_type:
        return None

    rows = (
        await session.execute(
            text(
                """
                SELECT ta.trust_account_id::text AS trust_account_id
                FROM finance.trust_accounts ta
                JOIN finance.funds f ON f.fund_id = ta.fund_id
                WHERE ta.scheme_id = CAST(:scheme_id AS uuid)
                  AND f.fund_type::text = :fund_type
                  AND ta.status != CAST('archived' AS core.record_status)
                ORDER BY ta.created_at ASC
                LIMIT 2
                """
            ),
            {"scheme_id": scheme_id, "fund_type": fund_type},
        )
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0].trust_account_id)
    return None


def _event_from_pg_row(row: Any, *, building_id: str, account_ref: str) -> BankTxObserved:
    """Generated function header.

    Function: _event_from_pg_row
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    description = str(row.description or "")
    bpay_crn, osko_e2e_id, lot_ref_raw = _extract_signals(description)
    return BankTxObserved(
        provider_txn_id=str(row.external_transaction_id or row.bank_transaction_id),
        tenant_id=building_id,
        account_ref=account_ref,
        occurred_at=_normalise_pg_tx_datetime(row.transaction_date),
        amount_cents=int(row.amount_cents or 0),
        description=description,
        balance_after_cents=row.balance_after_cents,
        bpay_crn=bpay_crn,
        osko_e2e_id=osko_e2e_id,
        lot_ref_raw=lot_ref_raw,
    )


async def _insert_bank_transaction(
    session,
    *,
    tenant_id: str,
    scheme_id: str,
    trust_account_id: str,
    tx: dict[str, Any],
) -> str | None:
    """Generated function header.

    Function: _insert_bank_transaction
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = (
        await session.execute(
            text(
                """
                INSERT INTO finance.bank_transactions
                    (tenant_id, scheme_id, trust_account_id, transaction_date,
                     description, reference, amount_cents, balance_after_cents,
                     external_transaction_id, provider_name,
                     source_type, confidence, provenance_class, evidence_type,
                     formula_version, source_snapshot_ids, supersedes_event_id,
                     requires_review, date_basis, unit_number,
                     transaction_origin, reconstruction_batch_id,
                     reconstruction_version, assumption_code, reconstruction_metadata)
                VALUES
                    (CAST(:tenant_id AS uuid), CAST(:scheme_id AS uuid), CAST(:trust_account_id AS uuid),
                     :transaction_date, :description, :reference, :amount_cents,
                     :balance_after_cents, :external_transaction_id, :provider_name,
                     :source_type, :confidence, :provenance_class, :evidence_type,
                     :formula_version, CAST(:source_snapshot_ids AS jsonb), :supersedes_event_id,
                     :requires_review, :date_basis, :unit_number,
                     :transaction_origin, CAST(:reconstruction_batch_id AS uuid),
                     :reconstruction_version, :assumption_code, CAST(:reconstruction_metadata AS jsonb))
                ON CONFLICT (trust_account_id, external_transaction_id) DO NOTHING
                RETURNING bank_transaction_id::text
                """
            ),
            {
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "trust_account_id": trust_account_id,
                "transaction_date": _normalise_posted_date(tx.get("posted_date")),
                "description": str(tx.get("description") or ""),
                "reference": tx.get("reference"),
                "amount_cents": _pg_amount_cents(tx),
                "balance_after_cents": tx.get("running_balance_cents"),
                "external_transaction_id": str(tx["external_transaction_id"]),
                "provider_name": str(tx.get("provider") or DEMO_BANK_PROVIDER),
                "source_type": tx.get("source_type"),
                "confidence": tx.get("confidence"),
                "provenance_class": tx.get("provenance_class"),
                "evidence_type": tx.get("evidence_type"),
                "formula_version": tx.get("formula_version"),
                "source_snapshot_ids": json.dumps(tx.get("source_snapshot_ids") or []),
                "supersedes_event_id": tx.get("supersedes_event_id"),
                "requires_review": bool(tx.get("requires_review") or False),
                "date_basis": tx.get("date_basis"),
                "unit_number": tx.get("unit_number"),
                "transaction_origin": tx.get("transaction_origin"),
                "reconstruction_batch_id": tx.get("reconstruction_batch_id"),
                "reconstruction_version": tx.get("reconstruction_version"),
                "assumption_code": tx.get("assumption_code"),
                "reconstruction_metadata": json.dumps(tx.get("reconstruction_metadata") or {}),
            },
        )
    ).first()
    return str(row[0]) if row else None


async def _load_pg_transactions_for_rematch(
    session,
    *,
    scheme_id: str,
    trust_account_id: str,
    limit: int,
) -> list[Any]:
    """Generated function header.

    Function: _load_pg_transactions_for_rematch
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT
                bt.bank_transaction_id::text AS bank_transaction_id,
                bt.transaction_date,
                bt.description,
                bt.amount_cents,
                bt.balance_after_cents,
                bt.external_transaction_id
            FROM finance.bank_transactions bt
            WHERE bt.scheme_id = CAST(:scheme_id AS uuid)
              AND bt.trust_account_id = CAST(:trust_account_id AS uuid)
            ORDER BY bt.transaction_date ASC, bt.bank_transaction_id ASC
            LIMIT :limit
            """
        ),
        {
            "scheme_id": scheme_id,
            "trust_account_id": trust_account_id,
            "limit": limit,
        },
    )
    return list(result.fetchall())


async def _mark_demo_bank_sync(db, tx_id: Any, *, status: str, pg_ref: str | None = None, error: str | None = None) -> None:
    """Generated function header.

    Function: _mark_demo_bank_sync
    Path: backend/routers/bank_feeds.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    update: dict[str, Any] = {
        "sync_status": status,
        "last_sync_attempt_at": datetime.now(timezone.utc),
        "sync_error": error,
    }
    if pg_ref:
        update["finance_bank_transaction_ref"] = pg_ref
    await db._db.demo_bank_transactions.update_one({"_id": tx_id}, {"$set": update})


# ─── Ingest ───────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def trigger_ingest(
        account_id: str = Query(...),
        connection_id: str = Query(default="mock-connection"),
        fund_type: str = Query(default="admin_fund"),
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Trigger bank feed ingestion for a building account.

    Provider selection is resolved from the canonical cutover settings:
      - building setting ``banking.provider`` (default: ``mock``)
      - building setting ``banking.mode``     (default: ``mock``)

    This keeps the route provider-neutral while the platform is still running
    on mock/csv-replay implementations.
    """
    user = _require_finance(current_user)
    db = _get_db()

    provider_name = await get_banking_provider(building_id)
    provider_mode = await get_banking_mode(building_id)
    service = BankFeedService(
        db=db,
        provider_name=provider_name,
        provider_mode=provider_mode,
    )

    result = await service.ingest(
        building_id=building_id,
        account_id=account_id,
        connection_id=connection_id,
        fund_type=fund_type,
        from_date=from_date,
        to_date=to_date,
        initiated_by=user["id"],
    )

    asyncio.create_task(create_audit_log(
        action="bank_feed_ingested",
        resource_type="bank_feed",
        resource_id=result["run_id"],
        user_id=user["id"],
        user_name=user.get("full_name", "Staff"),
        details=result,
        building_id=building_id,
    ))

    return result


# ─── MatchingEngine dispatch (called as background task after each new sync insert) ──

async def _load_lot_candidates(db, building_id: str, as_of_date: date | None = None) -> list:
    """Build LotCandidate list from Mongo units + levy data for the building.

    Uses the same fields the existing financial_matching router uses when assembling
    candidates before calling engine.match(). Keeps the MatchingEngine pure — it never
    touches the DB directly.

    ``as_of_date`` (typically the bank transaction's own date) is passed through to
    receivables_resolver.get_open_receivable_for_unit() so the candidate's
    open_levy_cents reflects the amount owed for the RELEVANT period, not today's
    latest unit_levy_ledger.net_balance — a historical/reconstructed transaction can
    have the correct unit reference and still fail amount matching if compared
    against a current balance that has since been paid down to zero (GAP-FIN-015).
    """
    from integrations.matching.layers.base import LotCandidate
    from services.receivables_resolver import get_open_receivable_for_unit

    units_cursor = db.units.find(
        {"building_id": building_id},
        {"unit_number": 1, "owner_name": 1, "owner_email": 1, "_id": 0},
    )
    units = await units_cursor.to_list(500)

    # Shared across every unit in this call: annual_levies is a building+year
    # document, not per-unit, so without this cache an N-unit building would
    # re-fetch the identical document N times per candidate-list build.
    levy_doc_cache: dict = {}

    candidates = []
    for unit in units:
        un = str(unit.get("unit_number", ""))
        if not un:
            continue
        receivable = await get_open_receivable_for_unit(
            building_id=building_id, unit_number=un, as_of_date=as_of_date,
            _levy_doc_cache=levy_doc_cache,
        )

        candidates.append(LotCandidate(
            lot_id=un,           # Mongo-era lot_id is unit_number until Postgres cutover
            unit_number=un,
            building_id=building_id,
            owner_name=unit.get("owner_name") or "",
            open_levy_cents=receivable["open_receivable_cents"],
            due_date=receivable.get("due_date") or "",
        ))
    return candidates


async def _dispatch_to_matching_engine(
        event: BankTxObserved,
        building_id: str,
        inbox_event_id: str,
        is_test_data: bool,
        disable_auto_allocation: bool = False,
) -> None:
    """Background task: run MatchingEngine for one synced bank transaction.

    Failures are logged but never propagate — sync must not be aborted by a
    matching error.
    """
    try:
        from integrations.matching.engine import match
        from request_context import set_ctx_building_id

        set_ctx_building_id(building_id)
        db = _get_db()
        candidates = await _load_lot_candidates(db, building_id, as_of_date=event.occurred_at.date())

        match_kwargs = {}
        if disable_auto_allocation:
            match_kwargs["auto_match_threshold"] = _AUTO_ALLOCATION_DISABLED_THRESHOLD

        result = await match(
            tx=event,
            candidates=candidates,
            db=db._db,
            inbox_event_id=inbox_event_id,
            is_test_data=is_test_data,
            **match_kwargs,
        )

        # match() only classifies status="auto_allocated" for high-confidence
        # matches — it never itself posts to the ledger. decide_queue_item()
        # (the only other caller of _post_payment_to_ledger()) requires
        # status="pending" and 409s otherwise, so without this call every
        # auto_allocated item would sit inert forever. See
        # financial_matching.auto_allocate_queue_item() for the full rationale.
        #
        # Deliberately retried on is_idempotent_replay too: a crash between
        # match() committing status="auto_allocated" and this call finishing
        # would otherwise leave the item stuck forever, since every future
        # replay of the same inbox_event_id returns is_idempotent_replay=True.
        # auto_allocate_queue_item()'s own update_one(status="auto_allocated")
        # race-guard makes this call itself idempotent, so retrying it here on
        # every replay is safe.
        if result.is_auto_allocated:
            from routers.financial_matching import auto_allocate_queue_item

            await auto_allocate_queue_item(result.queue_id, building_id)
    except Exception as exc:
        logger.warning(
            "MatchingEngine dispatch failed for building=%s inbox_event_id=%s: %s",
            building_id, inbox_event_id, exc,
        )


# ─── Canonical Sync ───────────────────────────────────────────────────────────

async def run_bank_feed_sync(db, *, building_id: str, payload: BankFeedSyncRequest, actor: dict) -> dict:
    """Core Demo Bank -> finance.bank_transactions sync loop.

    Extracted from the POST /sync route body so it can also be called
    in-process (not via a self-HTTP-call) from
    financial_onboarding.py's reconstruction-batch /sync endpoint, scoped
    via payload.reconstruction_batch_id. The HTTP route below is now a thin
    wrapper. Callers other than the route are responsible for their own
    request-level auth/feature-toggle checks before calling this — this
    function itself performs no permission checks.
    """
    set_ctx_building_id(building_id)

    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise HTTPException(status_code=404, detail="Postgres scheme context not found for building")

    processed = 0
    inserted = 0
    duplicates = 0
    failed = 0
    skipped_other_batch = 0
    account_cache: dict[str, tuple[str | None, str | None]] = {}
    split_account_cache: dict[tuple[str, str], str | None] = {}
    provider = DemoBankFeed()
    account_refs = await _account_refs_for_sync(db, building_id=building_id, payload=payload)
    since = _date_to_utc_start(payload.from_date) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    until = _date_to_utc_end(payload.to_date)

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        for account_ref in account_refs:
            iterator = (
                provider.pull_transactions_include_test(account_ref, since, building_id)
                if payload.include_test_data else
                provider.pull_transactions(account_ref, since)
            )
            async for event in iterator:
                if until and event.occurred_at > until:
                    continue

                if payload.reconstruction_batch_id is not None:
                    event_batch_id = (event.metadata or {}).get("reconstruction_batch_id")
                    if event_batch_id != payload.reconstruction_batch_id:
                        skipped_other_batch += 1
                        continue

                processed += 1
                source_doc = await _source_doc_for_event(db, building_id=building_id, event=event)
                tx_id = source_doc.get("_id") if source_doc else None
                try:
                    if event.tenant_id != building_id:
                        raise ValueError("Provider emitted a transaction for the wrong building")

                    if account_ref not in account_cache:
                        account = await db._db.demo_bank_accounts.find_one(
                            {"building_id": building_id, "account_ref": account_ref}
                        )
                        fund_type = _fund_type_for_account(account)
                        # Preferred mapping is trust_accounts.external_uid == Demo Bank account_ref.
                        # The fund-type fallback exists only for current East Gate genesis data,
                        # where one active trust account per fund exists but external_uid may not
                        # yet be populated. Ambiguity fails closed instead of guessing.
                        trust_account_id = await _resolve_trust_account(
                            session,
                            scheme_id=str(scheme["scheme_id"]),
                            account_ref=account_ref,
                            fund_type=fund_type,
                        )
                        account_cache[account_ref] = (trust_account_id, fund_type)
                    else:
                        trust_account_id, fund_type = account_cache[account_ref]

                    tx_payload = _tx_from_envelope(event, source_doc)

                    if trust_account_id:
                        # Single-fund account — the common path (real bank feeds,
                        # and any Demo Bank account whose account_type maps to
                        # exactly one fund). Unchanged from before this split logic.
                        postings = [(trust_account_id, tx_payload)]
                    else:
                        # No single trust account for this account_ref — e.g. a
                        # GENERATION_METHOD reconstruction batch's combined
                        # "OPERATING-*" account (account_type="transaction"),
                        # which intentionally holds ONE owner-levy amount spanning
                        # multiple funds so payment grouping isn't silently dropped
                        # (see budget_levy_generator.py / ingestion.py's
                        # payment_group_id grouping). Demo Bank always stores one
                        # combined amount per payment; splitting it into the
                        # correct per-fund GL postings is this application's job,
                        # not Demo Bank's — read the split from the transaction's
                        # own `allocations` (per-fund amount_cents, already
                        # inc-GST) instead of guessing a single fund_type for the
                        # whole amount.
                        fund_totals = _split_allocations_by_fund(tx_payload.get("allocations"))
                        if not fund_totals:
                            raise ValueError(
                                f"No unique active Postgres trust account found for "
                                f"account_ref={account_ref!r}, and no per-fund allocations "
                                f"recorded to split it by"
                            )
                        sign = 1 if int(event.amount_cents) >= 0 else -1
                        postings = []
                        for split_fund_type, split_amount_cents in fund_totals.items():
                            # Every combined transaction under this account_ref resolves
                            # the same fund->trust_account mapping — cache it instead of
                            # re-querying Postgres per row (was the actual cause of the
                            # 120s proxy timeout on this batch's 2,088-row income side:
                            # ~4,176 identical, uncached SELECTs).
                            split_cache_key = (account_ref, split_fund_type)
                            if split_cache_key not in split_account_cache:
                                split_account_cache[split_cache_key] = await _resolve_trust_account(
                                    session, scheme_id=str(scheme["scheme_id"]),
                                    account_ref=account_ref, fund_type=split_fund_type,
                                )
                            split_trust_account_id = split_account_cache[split_cache_key]
                            if not split_trust_account_id:
                                raise ValueError(
                                    f"No unique active Postgres trust account found for "
                                    f"account_ref={account_ref!r} fund_type={split_fund_type!r} "
                                    f"(splitting a combined transaction by allocations)"
                                )
                            split_tx = dict(tx_payload)
                            split_tx["amount_cents"] = sign * abs(split_amount_cents)
                            postings.append((split_trust_account_id, split_tx))

                    pg_refs: list[str] = []
                    for posting_trust_account_id, posting_tx in postings:
                        pg_ref = await _insert_bank_transaction(
                            session,
                            tenant_id=str(scheme["tenant_id"]),
                            scheme_id=str(scheme["scheme_id"]),
                            trust_account_id=posting_trust_account_id,
                            tx=posting_tx,
                        )
                        if not pg_ref:
                            continue
                        pg_refs.append(pg_ref)
                        # One combined Demo Bank event can now yield multiple
                        # Postgres rows (one per fund) — dispatch matching once
                        # per row, scoped to that fund's own portion of the
                        # amount (not the full combined amount), so scoring
                        # compares like with like. Feed each through the
                        # MatchingEngine so candidates are scored and the result
                        # lands in match_review_queue. Fire-and-forget: a
                        # matching failure must never abort sync.
                        split_event = (
                            event if len(postings) == 1
                            else event.model_copy(update={"amount_cents": posting_tx["amount_cents"]})
                        )
                        asyncio.create_task(
                            _dispatch_to_matching_engine(
                                event=split_event,
                                building_id=building_id,
                                inbox_event_id=pg_ref,
                                is_test_data=payload.include_test_data,
                                disable_auto_allocation=payload.disable_auto_allocation,
                            )
                        )

                    if pg_refs:
                        inserted += 1
                        if tx_id is not None:
                            await _mark_demo_bank_sync(db, tx_id, status="synced", pg_ref=",".join(pg_refs))
                    else:
                        duplicates += 1
                        if tx_id is not None:
                            await _mark_demo_bank_sync(db, tx_id, status="synced")
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "Demo Bank sync failed for building=%s tx=%s account_ref=%s: %s",
                        building_id,
                        event.provider_txn_id,
                        account_ref,
                        exc,
                    )
                    if tx_id is not None:
                        await _mark_demo_bank_sync(db, tx_id, status="failed", error=str(exc))

    asyncio.create_task(create_audit_log(
        action="bank_feed_synced",
        resource_type="bank_feed",
        resource_id=building_id,
        user_id=actor["id"],
        user_name=actor.get("full_name", "Staff"),
        details={
            "provider_name": DEMO_BANK_PROVIDER,
            "account_ref": payload.account_ref,
            "reconstruction_batch_id": payload.reconstruction_batch_id,
            "processed": processed,
            "inserted": inserted,
            "duplicates": duplicates,
            "failed": failed,
            "skipped_other_batch": skipped_other_batch,
        },
        building_id=building_id,
    ))

    return {
        "provider_name": DEMO_BANK_PROVIDER,
        "building_id": building_id,
        "processed": processed,
        "inserted": inserted,
        "duplicates": duplicates,
        "failed": failed,
        "skipped_other_batch": skipped_other_batch,
    }


@router.post("/sync")
async def sync_demo_bank_transactions(
        payload: BankFeedSyncRequest,
        current_user: dict = Depends(require_feature("bank_feeds_sync_enabled")),
        building_id: str = Depends(get_current_building),
):
    """Promote Demo Bank staged transactions into finance.bank_transactions.

    The Postgres insert is idempotent via UNIQUE (trust_account_id,
    external_transaction_id). Demo Bank sync_status is only a UI-facing
    operational cache and is not used to suppress re-sync attempts.
    """
    user = _require_sync(current_user)
    db = _get_db()

    # Per-building auto-approve policy (tasks/GAP-ONBOARD-001). Explicit
    # False (an admin turned the toggle off via Settings → Bank Feed
    # Transaction Matching) forces manual review regardless of the request
    # default. Unset (never configured) and explicit True both preserve the
    # existing confidence-based auto-allocate behaviour — this only ever
    # tightens the gate, never loosens what the caller already requested.
    # The settings lookup is best-effort: this endpoint had no dependency on
    # settings_service before this change, so a Postgres/Mongo hiccup in the
    # settings read path must not break bank-feed sync entirely — fail open
    # (no override, i.e. preserve pre-existing behaviour) and log a warning.
    if not payload.disable_auto_allocation:
        try:
            settings_doc = await get_general_settings_or_default(building_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "bank-feeds/sync: bank_feed_auto_approve lookup failed for building=%s, "
                "preserving default sync behaviour: %s", building_id, e,
            )
            settings_doc = {}
        if settings_doc.get("bank_feed_auto_approve") is False:
            payload.disable_auto_allocation = True

    return await run_bank_feed_sync(db, building_id=building_id, payload=payload, actor=user)


# ─── Matching Backfill ────────────────────────────────────────────────────────

@router.post("/rematch")
async def rematch_bank_transactions(
        payload: BankFeedRematchRequest,
        current_user: dict = Depends(require_feature("bank_feeds_sync_enabled")),
        building_id: str = Depends(get_current_building),
):
    """Replay already-synced Postgres bank transactions through MatchingEngine.

    This backfills match_review_queue for transactions that were promoted before
    /bank-feeds/sync started dispatching matching tasks. MatchingEngine remains
    the idempotency boundary via inbox_event_id == finance.bank_transactions ID.
    The response reports created documents separately from idempotent replays.
    """
    user = _require_sync(current_user)
    db = _get_db()
    set_ctx_building_id(building_id)

    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise HTTPException(status_code=404, detail="Postgres scheme context not found for building")

    account = await db._db.demo_bank_accounts.find_one({
        "building_id": building_id,
        "account_ref": payload.account_ref,
        "provider": DEMO_BANK_PROVIDER,
    })
    fund_type = _fund_type_for_account(account)

    processed = 0
    created = 0
    skipped_existing = 0
    failed = 0
    # Candidates are resolved per transaction date, not loaded once for the whole
    # batch: a batch commonly spans many years of historical/reconstructed rows,
    # and open_levy_cents must reflect what was owed AT THAT DATE (GAP-FIN-015),
    # not a single value reused across every row. Cached per date so rows sharing
    # a transaction date don't re-resolve the same receivables repeatedly.
    #
    # Known trade-off: a batch of thousands of rows spanning thousands of DISTINCT
    # calendar dates (as opposed to a handful of distinct years) still triggers one
    # full units+ledger scan per unique date, since each _load_lot_candidates()
    # call gets its own fresh levy_doc_cache. This endpoint is admin-only
    # (_require_sync), bounded by BankFeedRematchRequest.limit (<=10000), and run
    # infrequently for backfill — correctness of the matched amount is prioritised
    # over raw throughput here. Revisit with a shared per-(unit, year) cache across
    # the whole batch if this endpoint's usage pattern changes.
    candidates_by_date: dict[Any, list] = {}

    async def _candidates_for(tx_date) -> list:
        """Generated function header.

        Function: _candidates_for
        Path: backend/routers/bank_feeds.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if tx_date not in candidates_by_date:
            candidates_by_date[tx_date] = await _load_lot_candidates(
                db, building_id, as_of_date=tx_date
            )
        return candidates_by_date[tx_date]

    from integrations.matching.engine import match

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        trust_account_id = await _resolve_trust_account(
            session,
            scheme_id=str(scheme["scheme_id"]),
            account_ref=payload.account_ref,
            fund_type=fund_type,
        )
        if not trust_account_id:
            raise HTTPException(
                status_code=404,
                detail=f"No unique active Postgres trust account found for account_ref={payload.account_ref!r}",
            )

        rows = await _load_pg_transactions_for_rematch(
            session,
            scheme_id=str(scheme["scheme_id"]),
            trust_account_id=trust_account_id,
            limit=payload.limit,
        )

        for row in rows:
            processed += 1
            inbox_event_id = str(row.bank_transaction_id)
            try:
                row_candidates = await _candidates_for(row.transaction_date)
                rematch_kwargs = {}
                if payload.disable_auto_allocation:
                    rematch_kwargs["auto_match_threshold"] = _AUTO_ALLOCATION_DISABLED_THRESHOLD
                result = await match(
                    tx=_event_from_pg_row(row, building_id=building_id, account_ref=payload.account_ref),
                    candidates=row_candidates,
                    db=db._db,
                    inbox_event_id=inbox_event_id,
                    is_test_data=False,
                    **rematch_kwargs,
                )
                if result.is_idempotent_replay:
                    skipped_existing += 1
                else:
                    # The engine always writes a review-queue document, but its
                    # status can be pending or auto_allocated. Count creations,
                    # not only human-review items.
                    created += 1
                    # Same dead-end as _dispatch_to_matching_engine(): match() only
                    # classifies status="auto_allocated", it never posts to the ledger
                    # itself, and decide_queue_item() requires status="pending". Without
                    # this call every freshly auto_allocated rematch result would sit
                    # inert forever, same as the bug fixed for /bank-feeds/sync.
                    if result.is_auto_allocated:
                        from routers.financial_matching import auto_allocate_queue_item

                        await auto_allocate_queue_item(result.queue_id, building_id)
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Demo Bank rematch failed for building=%s account_ref=%s bank_transaction_id=%s: %s",
                    building_id,
                    payload.account_ref,
                    inbox_event_id,
                    exc,
                )

    asyncio.create_task(create_audit_log(
        action="bank_feed_rematched",
        resource_type="bank_feed",
        resource_id=building_id,
        user_id=user["id"],
        user_name=user.get("full_name", "Staff"),
        details={
            "provider_name": DEMO_BANK_PROVIDER,
            "account_ref": payload.account_ref,
            "processed": processed,
            "created": created,
            "queued": created,  # Backward-compatible alias for the original rematch response.
            "skipped_existing": skipped_existing,
            "failed": failed,
        },
        building_id=building_id,
    ))

    return {
        "provider_name": DEMO_BANK_PROVIDER,
        "building_id": building_id,
        "account_ref": payload.account_ref,
        "processed": processed,
        "created": created,
        "queued": created,  # Backward-compatible alias for the original rematch response.
        "skipped_existing": skipped_existing,
        "failed": failed,
    }


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.get("/transactions")
async def list_transactions(
        fund_type: Optional[str] = None,
        matched: Optional[bool] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List bank transactions for a building."""
    _require_finance(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if fund_type:
        query["fund_type"] = fund_type
    if matched is not None:
        query["matched"] = matched
    if from_date or to_date:
        query["date"] = {}
        if from_date:
            query["date"]["$gte"] = from_date
        if to_date:
            query["date"]["$lte"] = to_date

    skip = (page - 1) * page_size
    # Performance Optimization⚡: Parallelize count and data retrieval.
    # Using to_list() is faster than async iteration for paginated results.
    count_task = db.bank_transactions.count_documents(query)
    txs_task = db.bank_transactions.find(query).sort("date", -1).skip(skip).limit(page_size).to_list(page_size)

    total, docs = await asyncio.gather(count_task, txs_task)

    txs = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        txs.append(doc)

    return {"data": txs, "total": total, "page": page, "page_size": page_size}


@router.get("/transactions/{tx_id}")
async def get_transaction(
        tx_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Get a single bank transaction."""
    _require_finance(current_user)
    db = _get_db()

    doc = await db.bank_transactions.find_one({
        "_id": ObjectId(tx_id),
        "building_id": building_id,
    })
    if not doc:
        raise HTTPException(404, "Transaction not found.")
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.patch("/transactions/{tx_id}/match")
async def match_transaction(
        tx_id: str,
        matched_to_id: str = Query(..., description="Invoice or ledger entry ID"),
        match_type: str = Query(default="manual", description="manual | auto"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Manually match a bank transaction to an invoice or journal entry."""
    user = _require_finance(current_user)
    db = _get_db()

    doc = await db.bank_transactions.find_one({
        "_id": ObjectId(tx_id),
        "building_id": building_id,
    })
    if not doc:
        raise HTTPException(404, "Transaction not found.")

    await db.bank_transactions.update_one(
        {"_id": ObjectId(tx_id)},
        {"$set": {
            "matched": True,
            "matched_to_id": matched_to_id,
            "match_confidence": 100 if match_type == "manual" else 80,
            "match_type": match_type,
            "matched_by": user["id"],
            "matched_at": _now_iso(),
        }},
    )

    asyncio.create_task(create_audit_log(
        action="bank_transaction_matched",
        resource_type="bank_transaction",
        resource_id=tx_id,
        user_id=user["id"],
        user_name=user.get("full_name", "Staff"),
        details={"matched_to_id": matched_to_id, "match_type": match_type},
        building_id=building_id,
    ))

    return {"message": "Transaction matched.", "tx_id": tx_id, "matched_to_id": matched_to_id}


# ─── Run History ──────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_feed_runs(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List bank feed ingestion runs for a building."""
    _require_finance(current_user)
    db = _get_db()

    skip = (page - 1) * page_size
    query = {"building_id": building_id}

    # Performance Optimization⚡: Parallelize count and data retrieval.
    # Using to_list() is faster than async iteration for paginated results.
    count_task = db.bank_feed_runs.count_documents(query)
    runs_task = (
        db.bank_feed_runs.find(query)
        .sort("started_at", -1)
        .skip(skip)
        .limit(page_size)
        .to_list(page_size)
    )

    total, docs = await asyncio.gather(count_task, runs_task)

    runs = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        runs.append(doc)

    return {"data": runs, "total": total, "page": page, "page_size": page_size}


# ─── Strata-year summary ──────────────────────────────────────────────────────

@router.get("/strata-years/summary")
async def strata_year_summary(
        from_date: Optional[str] = Query(None, description="Override earliest date YYYY-MM-DD"),
        to_date: Optional[str] = Query(None, description="Override latest date YYYY-MM-DD"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return per-strata-year receipt totals sourced from Demo Bank staging.

    Groups demo_bank_transactions by their strata_year tag (set at import time).
    strata_year tags are building-agnostic — the historical import script tags each
    transaction when it extracts from the source collection.

    from_date / to_date optionally narrow the date range.  Without them the full
    staging history is returned.

    The Postgres authoritative view (post-approval) is at /strata-years/summary/pg.
    """
    _require_finance(current_user)
    db = _get_db()
    set_ctx_building_id(building_id)

    match_filter: dict = {
        "building_id": building_id,
        "is_test_data": {"$ne": True},
        "sync_status": {"$ne": "reversed"},
        "is_archived": {"$ne": True},
    }
    if from_date or to_date:
        date_filter: dict = {}
        if from_date:
            date_filter["$gte"] = from_date
        if to_date:
            date_filter["$lte"] = to_date
        match_filter["posted_date"] = date_filter

    pipeline = [
        {"$match": match_filter},
        {"$group": {
            "_id": "$strata_year",
            "credit_cents": {"$sum": {
                "$cond": [{"$eq": ["$direction", "credit"]}, "$amount_cents", 0]
            }},
            "debit_cents": {"$sum": {
                "$cond": [{"$eq": ["$direction", "debit"]}, "$amount_cents", 0]
            }},
            "tx_count": {"$sum": 1},
            "unit_numbers": {"$addToSet": "$unit_number"},
            "min_date": {"$min": "$posted_date"},
            "max_date": {"$max": "$posted_date"},
        }},
        {"$sort": {"_id": 1}},
    ]

    rows = await db.demo_bank_transactions.aggregate(pipeline).to_list(50)

    result = []
    for row in rows:
        year_key = row.get("_id") or "unknown"
        unit_set = [u for u in row.get("unit_numbers", []) if u]
        result.append({
            "strata_year": year_key,
            "period_start": row.get("min_date"),
            "period_end": row.get("max_date"),
            "staged_credits_aud": round(row.get("credit_cents", 0) / 100, 2),
            "staged_debits_aud": round(row.get("debit_cents", 0) / 100, 2),
            "staged_net_aud": round(
                (row.get("credit_cents", 0) - row.get("debit_cents", 0)) / 100, 2
            ),
            "staged_tx_count": row.get("tx_count", 0),
            "staged_unit_count": len(unit_set),
            "source": "demo_bank_staging",
        })

    return {"building_id": building_id, "strata_years": result}


@router.get("/strata-years/summary/pg")
async def strata_year_summary_postgres(
        from_date: Optional[str] = Query(None, description="Override earliest date YYYY-MM-DD"),
        to_date: Optional[str] = Query(None, description="Override latest date YYYY-MM-DD"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return per-strata-year receipt totals from the Postgres accounting ledger.

    Groups finance.receipts by calendar year of received_on. Only contains data
    for transactions that have completed the full pipeline:
      Demo Bank → sync → MatchingEngine → approve → FinancialCoreService.record_payment().

    from_date / to_date default to the full history range.
    Returns empty rows until the matching/approval pipeline has run.
    """
    _require_finance(current_user)

    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        return {"building_id": building_id, "strata_years": [], "note": "No Postgres scheme found"}

    start_bound = from_date or "2000-01-01"
    end_bound = to_date or "2099-12-31"

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        rows = await session.execute(
            text("""
                SELECT
                    EXTRACT(year FROM r.received_on)::text       AS strata_year,
                    MIN(r.received_on)::text                     AS period_start,
                    MAX(r.received_on)::text                     AS period_end,
                    COUNT(r.receipt_id)                          AS receipt_count,
                    COALESCE(SUM(r.amount_cents), 0)             AS receipts_cents,
                    COUNT(DISTINCT r.lot_id)                     AS lot_count
                FROM finance.receipts r
                JOIN core.lots l ON l.lot_id = r.lot_id
                WHERE l.scheme_id = CAST(:scheme_id AS uuid)
                  AND r.received_on BETWEEN CAST(:start AS date) AND CAST(:end AS date)
                GROUP BY EXTRACT(year FROM r.received_on)
                ORDER BY 1
            """),
            {
                "scheme_id": str(scheme["scheme_id"]),
                "start": start_bound,
                "end": end_bound,
            },
        )
        pg_rows = rows.fetchall()

    result = [
        {
            "strata_year": r.strata_year,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "posted_receipts_aud": round((r.receipts_cents or 0) / 100, 2),
            "posted_receipt_count": r.receipt_count or 0,
            "posted_lot_count": r.lot_count or 0,
            "source": "finance.receipts",
        }
        for r in pg_rows
    ]

    return {"building_id": building_id, "strata_years": result}
