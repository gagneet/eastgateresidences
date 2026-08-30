# @featuretrace:demo_bank — Reconstruction batch/manifest workflow orchestration.
# Layer: service
# Data flow: extracted facts / existing demo_bank_transactions rows →
#            demo_bank_reconstruction_batches + demo_bank_reconstruction_manifests (Mongo) →
#            [human approval] → import_historical_reconstruction(). (building-scoped)
# Related: integrations.demo_bank.reconstruction_batch_schemas
#          backend/services/levy_generation_service.py
#          docs/migration/historical_ledger_reconciliation_plan01.md
#          docs/migration/historical_ledger_reconciliation_plan02.md
# Toggle: historical_financial_reconstruction, historical_reconstruction_posting
# Collection: demo_bank_reconstruction_batches, demo_bank_reconstruction_manifests, demo_bank_transactions
"""Reconstruction batch/manifest status-machine orchestration.

This module is pure workflow-state orchestration against Mongo — it never
touches finance.* Postgres tables and never calls /bank-feeds/sync itself.
Status transitions only. Ledger posting happens later (Phase 6, explicitly
deferred) via the existing bank-feed sync + matching + FinancialCoreService
pipeline, once a batch reaches "approved".
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from integrations.demo_bank.reconstruction_batch_schemas import (
    MANIFEST_LOCKED_STATUSES,
    ReconstructionBatch,
    ReconstructionBatchStatus,
    ReconstructionManifest,
)

logger = logging.getLogger(__name__)


class ReconstructionWorkflowError(ValueError):
    """Raised on an invalid status transition or a governance precondition failure."""


_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"extracted", "failed"},
    "extracted": {"needs_review", "validation_failed", "failed"},
    "validation_failed": {"extracted", "failed"},
    "needs_review": {"approved", "extracted", "failed"},  # re-extract on rejection
    "approved": {"generation_ready", "superseded"},
    "generation_ready": {"generated", "failed"},
    "generated": {"syncing", "superseded"},
    # "synced" -> "syncing" and "failed" -> "syncing" both allow a sync retry
    # (including a plain re-verification call) without forcing a full
    # re-extract/re-generate cycle. Safe because the underlying Postgres
    # insert is idempotent (UNIQUE trust_account_id/external_transaction_id) —
    # a repeat sync reports prior rows as duplicates, never double-inserts.
    "syncing": {"synced", "failed"},
    "synced": {"matching", "syncing", "failed"},
    "matching": {"posted", "partially_posted", "failed"},
    "partially_posted": {"posted", "matching", "failed"},
    "posted": {"reversed"},
    "failed": {"extracted", "syncing", "superseded"},
    "superseded": set(),
    "reversed": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_batch_doc(db, building_id: str, batch_id: str) -> dict:
    doc = await db.demo_bank_reconstruction_batches.find_one(
        {"building_id": building_id, "batch_id": batch_id}
    )
    if doc is None:
        raise ReconstructionWorkflowError(f"No reconstruction batch {batch_id!r} found for building {building_id!r}")
    return doc


def _doc_to_batch(doc: dict) -> ReconstructionBatch:
    doc = {k: v for k, v in doc.items() if k != "_id"}
    return ReconstructionBatch(**doc)


async def _transition(db, building_id: str, batch_id: str, *, to_status: str, extra: Optional[dict] = None) -> ReconstructionBatch:
    doc = await _get_batch_doc(db, building_id, batch_id)
    current = doc["status"]
    allowed = _VALID_TRANSITIONS.get(current, set())
    if to_status not in allowed:
        raise ReconstructionWorkflowError(
            f"Invalid transition {current!r} -> {to_status!r} for batch {batch_id!r} "
            f"(allowed: {sorted(allowed)})"
        )
    update: dict[str, Any] = {"status": to_status}
    if extra:
        update.update(extra)
    await db.demo_bank_reconstruction_batches.update_one(
        {"building_id": building_id, "batch_id": batch_id},
        {"$set": update},
    )
    updated = await _get_batch_doc(db, building_id, batch_id)
    return _doc_to_batch(updated)


# ── Batch lifecycle ──────────────────────────────────────────────────────────

async def create_batch(
    db, *, building_id: str, financial_year_start: int, financial_year_end: int,
    reconstruction_method: str, created_by: str, source_document_ids: Optional[list[str]] = None,
    onboarding_session_id: Optional[str] = None, is_test_data: bool = False,
) -> ReconstructionBatch:
    """Create a new batch in status=draft."""
    batch_id = str(uuid4())
    batch = ReconstructionBatch(
        batch_id=batch_id,
        building_id=building_id,
        onboarding_session_id=onboarding_session_id,
        financial_year_start=financial_year_start,
        financial_year_end=financial_year_end,
        source_document_ids=source_document_ids or [],
        reconstruction_method=reconstruction_method,
        status="draft",
        created_by=created_by,
        created_at=_now(),
        is_test_data=is_test_data,
    )
    await db.demo_bank_reconstruction_batches.insert_one(batch.model_dump())
    logger.info("Reconstruction batch created: %s (building=%s)", batch_id, building_id)
    return batch


async def mark_extracted(db, *, building_id: str, batch_id: str) -> ReconstructionBatch:
    """Move a batch from draft to extracted — the required intermediate status before
    submit_for_review() can attach a manifest and move it to needs_review. Every other
    status transition in this module has a named wrapper around _transition(); this one
    was missing (draft->extracted has no governance-significant side effects of its own,
    unlike e.g. approve_batch()'s reviewed_by precondition), added here for consistency
    rather than having callers reach into the private _transition() function directly."""
    return await _transition(db, building_id, batch_id, to_status="extracted")


async def submit_for_review(
    db, *, building_id: str, batch_id: str, manifest: ReconstructionManifest, warnings: Optional[list[str]] = None,
) -> ReconstructionBatch:
    """Attach a manifest and move the batch to needs_review.

    This is the mandatory stopping point for an automated implementation pass —
    approval is a distinct, later, human governance action (see plan02
    amendment 2: "Stop at needs_review, with a complete immutable reconciliation
    manifest. No approval, ledger mutation or bank-feed sync occurs
    automatically.").
    """
    # mode="json" so `date` fields (posted_date on each ReconstructedTransactionRow)
    # serialise to ISO strings — pymongo can encode datetime but not bare date.
    await db.demo_bank_reconstruction_manifests.insert_one(manifest.model_dump(mode="json"))

    # expected_transaction_count covers BOTH credit (income) and debit (expense) rows once a
    # manifest merges the two generators — count each direction separately rather than
    # attributing every row to "credit" as this field did before the expense side existed.
    credit_count = sum(1 for t in manifest.transactions if t.direction == "credit")
    debit_count = sum(1 for t in manifest.transactions if t.direction == "debit")
    reconciliation_variance_cents = max(
        (abs(line.closing_balance_cents - (
            line.opening_balance_cents + line.reconstructed_credits_cents - line.reconstructed_debits_cents
        )) for line in manifest.fund_balance_reconciliation),
        default=0,
    )

    return await _transition(
        db, building_id, batch_id, to_status="needs_review",
        extra={
            "manifest_hash": manifest.manifest_hash,
            "warnings": warnings or [],
            "generated_credit_count": credit_count,
            "generated_debit_count": debit_count,
            "generated_credit_cents": manifest.expected_credit_cents,
            "generated_debit_cents": manifest.expected_debit_cents,
            "reconciliation_variance_cents": reconciliation_variance_cents,
            "reviewed_at": None,
            "reviewed_by": None,
        },
    )


async def record_review(
    db, *, building_id: str, batch_id: str, reviewed_by: str, notes: Optional[str] = None,
) -> ReconstructionBatch:
    """Record that a human reviewed the needs_review manifest. Does NOT approve —
    approve_batch is a separate action, per plan02 amendment 2, and (as of the
    router-level dual-control enforcement in financial_onboarding.py) requires
    a DIFFERENT authenticated user than whoever calls this function.

    `notes` is stored on the raw Mongo document (`review_notes`) but is not a
    field on the sibling package's ReconstructionBatch model, so it is not
    currently surfaced through `_doc_to_batch()`/the typed API response —
    only in the raw document and in the audit log. Extending the shared
    Pydantic schema to add a typed field is out of scope for this change.
    """
    doc = await _get_batch_doc(db, building_id, batch_id)
    if doc["status"] != "needs_review":
        raise ReconstructionWorkflowError(
            f"record_review requires status=needs_review, got {doc['status']!r}"
        )
    update: dict[str, Any] = {"reviewed_by": reviewed_by, "reviewed_at": _now()}
    if notes:
        update["review_notes"] = notes
    await db.demo_bank_reconstruction_batches.update_one(
        {"building_id": building_id, "batch_id": batch_id},
        {"$set": update},
    )
    updated = await _get_batch_doc(db, building_id, batch_id)
    return _doc_to_batch(updated)


async def reject_batch(
    db, *, building_id: str, batch_id: str, rejected_by: str, reason: str,
) -> ReconstructionBatch:
    """Human governance action — the negative outcome of the needs_review step. Moves the batch
    back to `extracted` (already a valid transition — 're-extract on rejection') so a corrected
    manifest can be generated fresh, rather than leaving bad data stuck in needs_review with no
    way out other than approving it. `rejected_by`/`rejected_at`/`rejection_reason` are stored as
    raw Mongo fields on the batch doc, the same convention record_review() already uses for
    `notes` -> `review_notes` — not added to the shared ReconstructionBatch Pydantic schema.

    Explicitly requires status=needs_review (mirrors record_review()'s own check) — `extracted`
    is technically also a valid _VALID_TRANSITIONS target from `draft`/`validation_failed`/
    `failed`, but "reject" is specifically the needs_review outcome; without this check, reject
    would be silently callable from those other statuses too, which isn't the intended action."""
    doc = await _get_batch_doc(db, building_id, batch_id)
    if doc["status"] != "needs_review":
        raise ReconstructionWorkflowError(
            f"reject_batch requires status=needs_review, got {doc['status']!r}"
        )
    return await _transition(
        db, building_id, batch_id, to_status="extracted",
        extra={
            "rejected_by": rejected_by,
            "rejected_at": _now(),
            "rejection_reason": reason,
            # Clear the manifest-review state so a stale reviewed_by/manifest_hash from the
            # rejected attempt can't be mistaken for having applied to whatever gets submitted
            # next — mirrors submit_for_review()'s own reviewed_by/reviewed_at reset on resubmit.
            "reviewed_by": None,
            "reviewed_at": None,
        },
    )


async def approve_batch(db, *, building_id: str, batch_id: str, approved_by: str) -> ReconstructionBatch:
    """Human governance action — requires reviewed_by already set. Locks the
    current manifest as immutable (any later recalculation must create a new
    manifest version and moves the batch back out of approved)."""
    doc = await _get_batch_doc(db, building_id, batch_id)
    if not doc.get("reviewed_by"):
        raise ReconstructionWorkflowError(
            f"approve_batch requires record_review() to have set reviewed_by first (batch={batch_id!r})"
        )
    return await _transition(
        db, building_id, batch_id, to_status="approved",
        extra={"approved_by": approved_by, "approved_at": _now()},
    )


async def mark_generation_ready(db, *, building_id: str, batch_id: str) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="generation_ready")


async def mark_generated(db, *, building_id: str, batch_id: str) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="generated")


async def mark_syncing(db, *, building_id: str, batch_id: str) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="syncing")


async def mark_synced(db, *, building_id: str, batch_id: str) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="synced")


async def mark_posted(db, *, building_id: str, batch_id: str, partial: bool = False) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="partially_posted" if partial else "posted")


async def supersede(db, *, building_id: str, batch_id: str, reason: str) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="superseded", extra={"warnings": [reason]})


async def reverse(db, *, building_id: str, batch_id: str, reason: str) -> ReconstructionBatch:
    return await _transition(db, building_id, batch_id, to_status="reversed", extra={"warnings": [reason]})


def is_manifest_locked(batch: ReconstructionBatch) -> bool:
    return batch.status in MANIFEST_LOCKED_STATUSES


# ── East Gate retroactive linking (Phase 5) ──────────────────────────────────

async def link_existing_rows_as_manifest(
    db, *, building_id: str, batch: ReconstructionBatch, source_type: str = "synthetic_from_budget",
) -> ReconstructionManifest:
    """Build a manifest FROM already-existing demo_bank_transactions rows,
    without changing any of their amounts or dates — the retroactive linking
    path for East Gate's pre-existing 3,828 synthetic rows (plan02 amendment 7).

    Caller must run the eligibility checklist BEFORE calling this (count,
    sync_status=pending for every row, no finance_bank_transaction_ref, unique
    external_transaction_id/idempotency_key, posted_date range, no special-levy
    component present) — this function does not re-validate those, it only
    builds the manifest and computes its hash from the rows as they already are.
    """
    from integrations.demo_bank.reconstruction_batch_schemas import ReconstructedTransactionRow

    rows_cursor = db.demo_bank_transactions.find(
        {"building_id": building_id, "source_type": source_type, "is_archived": {"$ne": True}}
    ).sort([("levy_year", 1), ("levy_quarter", 1), ("unit_number", 1)])

    transactions: list[ReconstructedTransactionRow] = []
    total_credit_cents = 0
    input_hashes: list[str] = []
    async for doc in rows_cursor:
        fund_type = "sinking" if "SINKING" in str(doc.get("account_ref") or "") else "admin"
        quarter_raw = doc.get("levy_quarter")
        quarter = int(str(quarter_raw).lstrip("Q")) if quarter_raw else None
        posted_date = doc["posted_date"]
        if hasattr(posted_date, "date"):
            posted_date = posted_date.date()
        transactions.append(ReconstructedTransactionRow(
            account_ref=doc["account_ref"],
            unit_number=str(doc.get("unit_number") or ""),
            financial_year=str(doc.get("levy_year") or ""),
            quarter=quarter,
            fund_type=fund_type,
            levy_component="ordinary",
            posted_date=posted_date,
            amount_cents=int(doc["amount_cents"]),
            amount_ex_gst_cents=int(doc.get("amount_ex_gst_cents") or doc["amount_cents"]),
            gst_cents=int(doc.get("gst_cents") or 0),
            direction=doc.get("direction", "credit"),
            assumption_code=str(doc.get("payment_pattern") or "unknown"),
            description=str(doc.get("description") or ""),
            transaction_sequence=1,
        ))
        total_credit_cents += int(doc["amount_cents"])
        input_hashes.append(str(doc.get("idempotency_key") or doc.get("external_transaction_id") or ""))

    if not transactions:
        raise ReconstructionWorkflowError(
            f"No demo_bank_transactions rows found for building={building_id!r} "
            f"source_type={source_type!r} — nothing to link."
        )

    input_fact_hash = hashlib.sha256("|".join(sorted(input_hashes)).encode()).hexdigest()
    manifest_payload_for_hash = f"{building_id}:{source_type}:{len(transactions)}:{total_credit_cents}:{input_fact_hash}"
    manifest_hash = hashlib.sha256(manifest_payload_for_hash.encode()).hexdigest()

    manifest = ReconstructionManifest(
        manifest_id=str(uuid4()),
        batch_id=batch.batch_id,
        building_id=building_id,
        version=1,
        input_document_hashes=[],
        input_fact_hash=input_fact_hash,
        calculation_configuration={"retroactive_link": True, "source_type": source_type},
        generator_version="retroactive-link-v1",
        expected_transaction_count=len(transactions),
        expected_credit_cents=total_credit_cents,
        expected_debit_cents=0,
        transactions=transactions,
        manifest_hash=manifest_hash,
        generated_at=_now(),
        generated_by=batch.created_by,
        is_test_data=batch.is_test_data,
    )
    return manifest
