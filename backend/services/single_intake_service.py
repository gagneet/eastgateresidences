# @featuretrace:demo_bank — Single-intake enforcement: stage external cash-receipt imports
#   (DEFT/BPAY settlement CSV, and future bank/CSV/PDF import paths) into Demo Bank as pending
#   evidence, instead of direct-writing confirmed payments to levy_payments/unit_levy_ledger.
#   This is the write-side of the "everything enters through one gateway" contract — when the
#   disable_strata_sync_direct_write toggle is enabled for a building, an import path routes here.
# @featuretrace:levy — replaces the DEFT importer's direct levy_payments write with Demo Bank
#   staging that flows to the match-review queue via the existing bank-feed sync pipeline.
# Layer: service
# Data flow: external settlement CSV row -> demo_bank_transactions (Mongo, pending) ->
#            [existing /bank-feeds/sync] -> finance.bank_transactions -> match_review_queue.
#            (building-scoped)
# Related: backend/routers/reconciliation.py (DEFT/BPAY importer — primary caller)
#          backend/services/historical_levy_reconstruction_service.py (_upsert_transaction — the
#          demo_bank_transactions schema this mirrors, for real imports rather than synthetic rows)
#          backend/core/toggle_classification.py (disable_strata_sync_direct_write = CUTOVER_SENSITIVE)
#          tasks/GAP-FIN-037-single-intake-enforcement.md
# Toggle: disable_strata_sync_direct_write
# Collection: demo_bank_transactions
"""Route external cash-receipt imports through Demo Bank instead of direct ledger writes.

`disable_strata_sync_direct_write` (cutover-sensitive, super-admin-only, default OFF) is the
building-level switch that turns single-intake enforcement on. When it is ON, an import path must
NOT write a `confirmed` payment straight to `levy_payments`/`unit_levy_ledger`; it stages the row
into Demo Bank as pending external-import evidence (`provenance_class="external_import"`), which the
existing bank-feed sync + matching pipeline then carries into the match-review queue for human
allocation — the same gateway every bank-feed transaction goes through.

The Demo Bank row schema here deliberately mirrors
`historical_levy_reconstruction_service._upsert_transaction()` (the repo's own
`demo_bank_transactions` writer) so staged import rows are shaped identically to every other Demo
Bank transaction and flow through sync unchanged — except `is_synthetic=False` and a distinct
`source_type`/`provenance_class`, because these are REAL imported settlement facts, not synthetic
reconstructions. Amounts are integer cents (converted once, at the importer's ingestion boundary,
before calling here).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

_PROVIDER = "external_import"


async def is_single_intake_enforced(building_id: str) -> bool:
    """True when `disable_strata_sync_direct_write` is enabled for this building. Uses the pure
    building-level toggle resolver (default FALSE) — NOT get_effective_feature_access(), which
    defaults to True and grants super-admins access unconditionally (both wrong for a
    default-off enforcement gate)."""
    from db_postgres.repos import config_repo

    return bool(
        await config_repo.resolve_feature_toggle(
            building_id, "disable_strata_sync_direct_write", default=False
        )
    )


def build_import_idempotency_key(
    *, building_id: str, reference: str, posted_date: str, amount_cents: int, unit_number: str,
) -> str:
    """Deterministic key so re-importing the same settlement row is an idempotent no-op (a
    duplicate), never a double-stage. Matches the DEFT importer's own duplicate identity
    (building + reference + date + amount), plus unit_number for safety."""
    raw = f"{building_id}|{reference}|{posted_date}|{int(amount_cents)}|{unit_number or ''}"
    return "import-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


async def stage_cash_receipt_into_demo_bank(
    mongo_db,
    *,
    building_id: str,
    unit_number: str,
    amount_cents: int,
    posted_date: str,
    reference: str,
    description: str,
    source_type: str,
    payment_method: Optional[str] = None,
    year: Optional[str] = None,
    quarter: Optional[str] = None,
    is_test_data: bool = False,
) -> tuple[bool, str]:
    """Upsert one external cash-receipt row into Demo Bank as pending evidence.

    `mongo_db` is the RAW motor handle (`db._db`) — every field filters building_id explicitly, so
    no TenantScopedDatabase context is required (matches financial_onboarding._mongo's convention).
    `amount_cents` is already integer cents (converted at the importer's ingestion boundary).

    Returns (was_inserted, idempotency_key). was_inserted=False means the row was already staged
    (an idempotent duplicate), so callers can count it as a duplicate rather than a new stage.
    """
    idem_key = build_import_idempotency_key(
        building_id=building_id, reference=reference, posted_date=posted_date,
        amount_cents=amount_cents, unit_number=unit_number,
    )
    now = datetime.now(timezone.utc).isoformat()
    doc: dict[str, Any] = {
        "building_id": building_id,
        "provider": _PROVIDER,
        "account_ref": f"OPERATING-{building_id}",
        "external_transaction_id": idem_key,
        "idempotency_key": idem_key,
        "posted_date": posted_date,
        "amount_cents": int(amount_cents),
        "direction": "credit",
        "description": description or "",
        "reference": reference,
        "source_type": source_type,
        "source_id": f"import:{source_type}",
        "provenance_class": "external_import",
        "unit_number": unit_number or "",
        "levy_year": str(year) if year else None,
        "levy_quarter": quarter,
        "payment_method": payment_method,
        "sync_status": "pending",
        "is_synthetic": False,
        "is_test_data": bool(is_test_data),
        "created_at": now,
        "updated_at": now,
    }
    result = await mongo_db.demo_bank_transactions.update_one(
        {"building_id": building_id, "idempotency_key": idem_key},
        {"$setOnInsert": doc},
        upsert=True,
    )
    return (result.upserted_id is not None), idem_key
