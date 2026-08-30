"""
MRI Migration Router — import pipeline for MRI → platform migration.

Endpoints:
  POST   /migration/mri/batches                   — create migration batch
  GET    /migration/mri/batches                   — list batches
  GET    /migration/mri/batches/{batch_id}        — get batch details
  POST   /migration/mri/batches/{batch_id}/validate    — run validation
  GET    /migration/mri/batches/{batch_id}/report      — download validation report
  POST   /migration/mri/batches/{batch_id}/dry-run     — simulate commit (no writes)
  POST   /migration/mri/batches/{batch_id}/commit      — execute commit
  POST   /migration/mri/batches/{batch_id}/rollback    — rollback committed batch

All endpoints require strata_manager or super_admin role.
building_id is always sourced from JWT — never from request payload.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Any, Dict, List

from models.mri_migration import (
    MigrationBatchCreate,
    MigrationBatchStatus,
    MigrationValidationReport,
    ValidationFinding,
    ValidationSeverity,
)
from utils.auth import get_current_user, effective_role
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/migration/mri", tags=["MRI Migration"])

# All temporary staging collections that must be purged on both commit and rollback.
# Kept as a single constant so adding a new staging collection never misses one path.
_STAGING_COLLECTIONS = [
    "staging_owners", "staging_lots", "staging_ledgers",
    "staging_transactions", "staging_bank_balances", "staging_validation_issues",
]


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/mri_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/mri_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_migration_role(user: dict) -> dict:
    """Generated function header.

    Function: _require_migration_role
    Path: backend/routers/mri_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in {"super_admin", "strata_manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MRI migration requires strata_manager or super_admin role.",
        )
    return user


def _get_building_id(user: dict) -> str:
    """Generated function header.

    Function: _get_building_id
    Path: backend/routers/mri_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bid = user.get("building_id")
    if not bid:
        raise HTTPException(status_code=400, detail="No building_id in token.")
    return str(bid)


# State machine: valid transitions from each status
_VALID_TRANSITIONS: Dict[str, set] = {
    MigrationBatchStatus.PENDING.value: {
        MigrationBatchStatus.VALIDATING.value,
    },
    MigrationBatchStatus.VALIDATING.value: {
        MigrationBatchStatus.VALIDATED.value,
        MigrationBatchStatus.VALIDATION_FAILED.value,
        MigrationBatchStatus.FAILED.value,
    },
    MigrationBatchStatus.VALIDATED.value: {
        MigrationBatchStatus.DRY_RUN.value,
        MigrationBatchStatus.COMMITTING.value,
    },
    MigrationBatchStatus.DRY_RUN.value: {
        MigrationBatchStatus.DRY_RUN_COMPLETE.value,
        MigrationBatchStatus.COMMITTING.value,
    },
    MigrationBatchStatus.DRY_RUN_COMPLETE.value: {
        MigrationBatchStatus.READY_TO_COMMIT.value,
    },
    MigrationBatchStatus.READY_TO_COMMIT.value: {
        MigrationBatchStatus.COMMITTING.value,
    },
    MigrationBatchStatus.COMMITTING.value: {
        MigrationBatchStatus.COMMITTED.value,
        MigrationBatchStatus.FAILED.value,
    },
    MigrationBatchStatus.COMMITTED.value: {
        MigrationBatchStatus.ROLLBACK_AVAILABLE.value,
    },
    MigrationBatchStatus.ROLLBACK_AVAILABLE.value: {
        MigrationBatchStatus.ROLLED_BACK.value,
    },
    # Terminal states — no transitions out
    MigrationBatchStatus.VALIDATION_FAILED.value: set(),
    MigrationBatchStatus.FAILED.value: set(),
    MigrationBatchStatus.ROLLED_BACK.value: set(),
}


def _assert_transition(current_status: str, target_status: str, batch_id: str) -> None:
    """Raise 400 if the state transition is not permitted."""
    allowed = _VALID_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid state transition for batch {batch_id}: "
                f"'{current_status}' → '{target_status}'. "
                f"Allowed: {sorted(allowed) or 'none (terminal state)'}."
            ),
        )


# ─── Batch CRUD ───────────────────────────────────────────────────────────────

@router.post("/batches", status_code=status.HTTP_201_CREATED)
async def create_migration_batch(
        payload: MigrationBatchCreate,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Create a new MRI migration batch for this building."""
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    doc = payload.model_dump()
    doc["building_id"] = building_id
    doc["status"] = MigrationBatchStatus.PENDING.value
    doc["total_records"] = 0
    doc["validated_records"] = 0
    doc["error_count"] = 0
    doc["warning_count"] = 0
    doc["created_by"] = user.get("email", "unknown")
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()

    result = await db.migration_batches.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="mri_migration_batch_created",
        entity_type="migration_batch",
        entity_id=doc["id"],
        user_id=user.get("email"),
        building_id=building_id,
        details={"description": payload.description, "source_system": payload.source_system},
    ))

    return {"id": doc["id"], "status": doc["status"], "building_id": building_id}


@router.get("/batches")
async def list_migration_batches(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List migration batches for this building, newest first."""
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    skip = (page - 1) * limit
    cursor = db.migration_batches.find(
        {"building_id": building_id},
        {"_id": 1, "status": 1, "description": 1, "source_system": 1,
         "total_records": 1, "error_count": 1, "warning_count": 1,
         "created_at": 1, "created_by": 1, "committed_at": 1}
    ).sort("created_at", -1).skip(skip).limit(limit)

    batches = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        batches.append(doc)

    total = await db.migration_batches.count_documents({"building_id": building_id})
    return {"batches": batches, "total": total, "page": page, "limit": limit}


@router.get("/batches/{batch_id}")
async def get_migration_batch(
        batch_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get full details of a migration batch."""
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    doc = await db.migration_batches.find_one({"_id": ObjectId(batch_id), "building_id": building_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Migration batch not found.")
    doc["id"] = str(doc.pop("_id"))
    return doc


# ─── Validation ──────────────────────────────────────────────────────────────

@router.post("/batches/{batch_id}/validate")
async def validate_migration_batch(
        batch_id: str,
        current_user: dict = Depends(get_current_user),
) -> MigrationValidationReport:
    """Run validation against all staged records in this batch."""
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    batch = await db.migration_batches.find_one(
        {"_id": ObjectId(batch_id), "building_id": building_id}
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Migration batch not found.")

    if batch.get("status") in (MigrationBatchStatus.COMMITTED.value, MigrationBatchStatus.ROLLED_BACK.value):
        raise HTTPException(status_code=400, detail="Cannot re-validate a committed or rolled-back batch.")

    _assert_transition(batch.get("status", ""), MigrationBatchStatus.VALIDATING.value, batch_id)

    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {"status": MigrationBatchStatus.VALIDATING.value, "updated_at": _now_iso()}}
    )

    issues: List[Dict[str, Any]] = []
    counts = {"owners": 0, "lots": 0, "ledger": 0, "transactions": 0, "bank_balances": 0}
    blocking = 0
    errors = 0
    warnings = 0
    info_count = 0

    async def _check_collection(collection_name: str, entity_type: str, required_fields: List[str]):
        """Generated function header.

        Function: _check_collection
        Path: backend/routers/mri_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        nonlocal blocking, errors, warnings, info_count
        async for doc in db[collection_name].find({"migration_batch_id": batch_id, "building_id": building_id}):
            counts[entity_type] = counts.get(entity_type, 0) + 1
            for field in required_fields:
                if not doc.get(field):
                    issue = {
                        "entity_type": entity_type,
                        "source_record_id": str(doc.get("_id", "")),
                        "field": field,
                        "severity": ValidationSeverity.ERROR.value,
                        "code": f"MISSING_{field.upper()}",
                        "message": f"Required field '{field}' is missing or empty.",
                    }
                    issues.append(issue)
                    errors += 1
            # Email validation for owners
            if entity_type == "owners" and doc.get("email"):
                if not re.match(r"[^@]+@[^@]+\.[^@]+", doc["email"]):
                    issues.append({
                        "entity_type": entity_type,
                        "source_record_id": str(doc.get("_id", "")),
                        "field": "email",
                        "severity": ValidationSeverity.WARNING.value,
                        "code": "INVALID_EMAIL_FORMAT",
                        "message": f"Email '{doc['email']}' does not appear valid.",
                    })
                    warnings += 1
            # Balance validation for ledger
            if entity_type == "ledger" and doc.get("balance_cents") is not None:
                if not isinstance(doc["balance_cents"], int):
                    issues.append({
                        "entity_type": entity_type,
                        "source_record_id": str(doc.get("_id", "")),
                        "field": "balance_cents",
                        "severity": ValidationSeverity.BLOCKING.value,
                        "code": "BALANCE_NOT_INTEGER",
                        "message": "balance_cents must be an integer (cents). Float values not permitted.",
                    })
                    blocking += 1

    await _check_collection("staging_owners", "owners", ["first_name", "last_name"])
    await _check_collection("staging_lots", "lots", ["lot_number"])
    await _check_collection("staging_ledgers", "ledger", ["source_account_code", "balance_cents", "as_at_date"])
    await _check_collection("staging_transactions", "transactions",
                            ["transaction_date", "amount_cents", "transaction_type"])
    await _check_collection("staging_bank_balances", "bank_balances", ["account_name", "balance_cents", "as_at_date"])

    total = sum(counts.values())
    is_committable = blocking == 0

    # Store issues (up to 1000)
    if issues:
        issue_docs = [
            {**iss, "migration_batch_id": batch_id, "building_id": building_id, "created_at": _now_iso()}
            for iss in issues[:1000]
        ]
        await db.staging_validation_issues.delete_many({"migration_batch_id": batch_id, "building_id": building_id})
        await db.staging_validation_issues.insert_many(issue_docs)

    new_status = MigrationBatchStatus.VALIDATED.value if is_committable else MigrationBatchStatus.VALIDATION_FAILED.value
    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {
            "status": new_status,
            "total_records": total,
            "validated_records": total - errors - blocking,
            "error_count": errors + blocking,
            "warning_count": warnings,
            "updated_at": _now_iso(),
        }}
    )

    # Build ValidationFinding list for new Phase 2 model fields
    findings = [
        ValidationFinding(
            severity=iss.get("severity", "error"),
            field=iss.get("field"),
            code=iss.get("code", "UNKNOWN"),
            message=iss.get("message", ""),
            suggested_fix=iss.get("suggested_fix"),
        )
        for iss in issues[:100]
    ]

    return MigrationValidationReport(
        batch_id=batch_id,
        building_id=building_id,
        validated_at=_now_iso(),
        # Phase 2 fields
        total_rows=total,
        blocking_count=blocking,
        warning_count=warnings,
        info_count=info_count,
        can_proceed=is_committable,
        findings=findings,
        # Legacy compatibility fields
        is_committable=is_committable,
        total_records=total,
        owners=counts["owners"],
        lots=counts["lots"],
        ledger_entries=counts["ledger"],
        transactions=counts["transactions"],
        bank_balances=counts["bank_balances"],
        blocking_errors=blocking,
        errors=errors,
        warnings=warnings,
        issues_sample=issues[:20],
    )


# ─── Dry Run ─────────────────────────────────────────────────────────────────

@router.post("/batches/{batch_id}/dry-run")
async def dry_run_migration(
        batch_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Simulate commit without writing to live collections. Returns preview summary."""
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    batch = await db.migration_batches.find_one({"_id": ObjectId(batch_id), "building_id": building_id})
    if not batch:
        raise HTTPException(status_code=404, detail="Migration batch not found.")
    if batch.get("status") not in (MigrationBatchStatus.VALIDATED.value,):
        raise HTTPException(status_code=400, detail="Batch must be validated before dry-run.")

    _assert_transition(batch.get("status", ""), MigrationBatchStatus.DRY_RUN.value, batch_id)

    owners = await db.staging_owners.count_documents({"migration_batch_id": batch_id, "building_id": building_id})
    lots = await db.staging_lots.count_documents({"migration_batch_id": batch_id, "building_id": building_id})
    ledger_entries = await db.staging_ledgers.count_documents(
        {"migration_batch_id": batch_id, "building_id": building_id})
    transactions = await db.staging_transactions.count_documents(
        {"migration_batch_id": batch_id, "building_id": building_id})
    bank_balances = await db.staging_bank_balances.count_documents(
        {"migration_batch_id": batch_id, "building_id": building_id})

    # Compute opening balance total for preview
    balance_pipeline = [
        {"$match": {"migration_batch_id": batch_id, "building_id": building_id}},
        {"$group": {"_id": "$fund_type", "total_cents": {"$sum": "$balance_cents"}}}
    ]
    ledger_totals = {}
    async for row in db.staging_ledgers.aggregate(balance_pipeline):
        ledger_totals[row["_id"] or "unknown"] = row["total_cents"]

    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {"status": MigrationBatchStatus.READY_TO_COMMIT.value, "updated_at": _now_iso()}}
    )

    return {
        "dry_run": True,
        "batch_id": batch_id,
        "building_id": building_id,
        "status": MigrationBatchStatus.READY_TO_COMMIT.value,
        "would_create": {
            "owners": owners,
            "lots": lots,
            "opening_ledger_entries": ledger_entries,
            "historical_transactions": transactions,
            "trust_bank_balances": bank_balances,
        },
        "opening_balance_by_fund": ledger_totals,
        "warnings": [],
        "message": "Dry run complete. No changes were made to live collections. Call /commit to execute.",
    }


# ─── Commit ──────────────────────────────────────────────────────────────────

@router.post("/batches/{batch_id}/commit")
async def commit_migration(
        batch_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Execute the migration commit. Idempotent — skips already-committed records."""
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    batch = await db.migration_batches.find_one({"_id": ObjectId(batch_id), "building_id": building_id})
    if not batch:
        raise HTTPException(status_code=404, detail="Migration batch not found.")

    COMMITTABLE_STATUSES = (
        MigrationBatchStatus.VALIDATED.value,
        MigrationBatchStatus.DRY_RUN.value,
        MigrationBatchStatus.DRY_RUN_COMPLETE.value,
        MigrationBatchStatus.READY_TO_COMMIT.value,
    )
    if batch.get("status") not in COMMITTABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Cannot commit batch in status '{batch.get('status')}'.")

    # Idempotency: SHA-256(building_id + file_checksums_json + freeze_date)
    idempotency_material = (
            building_id
            + json.dumps(batch.get("file_checksums") or {}, sort_keys=True)
            + (batch.get("freeze_date") or "")
    )
    idempotency_key = hashlib.sha256(idempotency_material.encode()).hexdigest()

    # Check for an already-committed batch with the same key (different batch_id)
    existing_committed = await db.migration_batches.find_one({
        "building_id": building_id,
        "idempotency_key": idempotency_key,
        "status": MigrationBatchStatus.COMMITTED.value,
        "_id": {"$ne": ObjectId(batch_id)},
    })
    if existing_committed:
        return {
            "status": "idempotent_replay",
            "batch_id": str(existing_committed["_id"]),
            "message": "A batch with identical file checksums and freeze date has already been committed.",
        }

    # Store idempotency_key on the current batch for future dedup checks
    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {"idempotency_key": idempotency_key}}
    )

    _assert_transition(batch.get("status", ""), MigrationBatchStatus.COMMITTING.value, batch_id)

    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {"status": MigrationBatchStatus.COMMITTING.value, "updated_at": _now_iso()}}
    )

    committed = {"owners": 0, "lots": 0, "ledger_entries": 0, "transactions": 0, "bank_balances": 0}
    skipped = {"owners": 0, "lots": 0}

    # 1. Commit owners
    async for staged_owner in db.staging_owners.find(
            {"migration_batch_id": batch_id, "building_id": building_id, "platform_user_id": None}
    ):
        existing = await db.users.find_one({
            "building_id": building_id,
            "email": staged_owner.get("email"),
        }) if staged_owner.get("email") else None

        if existing:
            platform_id = str(existing["_id"])
            skipped["owners"] += 1
        else:
            user_doc = {
                "building_id": building_id,
                "first_name": staged_owner.get("first_name"),
                "last_name": staged_owner.get("last_name"),
                "email": staged_owner.get("email"),
                "phone": staged_owner.get("phone"),
                "role": "owner",
                "migration_source": "mri",
                "migration_batch_id": batch_id,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            res = await db.users.insert_one(user_doc)
            platform_id = str(res.inserted_id)
            committed["owners"] += 1

        await db.staging_owners.update_one(
            {"_id": staged_owner["_id"]},
            {"$set": {"platform_user_id": platform_id, "validation_status": "committed"}}
        )

    # 2. Commit lots
    async for staged_lot in db.staging_lots.find(
            {"migration_batch_id": batch_id, "building_id": building_id, "platform_unit_id": None}
    ):
        existing = await db.units.find_one({
            "building_id": building_id,
            "unit_number": staged_lot.get("lot_number"),
        })
        if existing:
            skipped["lots"] += 1
            platform_id = str(existing["_id"])
        else:
            unit_doc = {
                "building_id": building_id,
                "unit_number": staged_lot.get("lot_number"),
                "lot_number": staged_lot.get("lot_number"),
                "entitlement_units": staged_lot.get("entitlement_units", 1),
                "property_type": staged_lot.get("property_type"),
                "migration_source": "mri",
                "migration_batch_id": batch_id,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            res = await db.units.insert_one(unit_doc)
            platform_id = str(res.inserted_id)
            committed["lots"] += 1

        await db.staging_lots.update_one(
            {"_id": staged_lot["_id"]},
            {"$set": {"platform_unit_id": platform_id, "validation_status": "committed"}}
        )

    # 3. Commit opening ledger balances as migration journal entries
    async for staged_ledger in db.staging_ledgers.find(
            {"migration_batch_id": batch_id, "building_id": building_id}
    ):
        journal_doc = {
            "building_id": building_id,
            "fund_type": staged_ledger.get("fund_type", "admin_fund"),
            "date": staged_ledger.get("as_at_date"),
            "reference": f"MIG-{batch_id[:8].upper()}-OB",
            "narration": f"Migration opening balance: {staged_ledger.get('source_account_name')}",
            "status": "posted",
            "source_type": "mri_migration",
            "source_id": batch_id,
            "migration_batch_id": batch_id,
            "lines": [
                {
                    "account_code": staged_ledger.get("platform_account_code") or staged_ledger.get(
                        "source_account_code"),
                    "entry_type": staged_ledger.get("direction", "credit"),
                    "amount_cents": abs(staged_ledger.get("balance_cents", 0)),
                    "description": "Opening balance from MRI migration",
                }
            ],
            "is_balanced": True,
            "created_by": user.get("email"),
            "created_at": _now_iso(),
        }
        await db.trust_ledger_entries.insert_one(journal_doc)
        committed["ledger_entries"] += 1

    # 4. Commit trust bank opening balances
    async for staged_bank in db.staging_bank_balances.find(
            {"migration_batch_id": batch_id, "building_id": building_id}
    ):
        bank_doc = {
            "building_id": building_id,
            "account_name": staged_bank.get("account_name"),
            "bank_name": staged_bank.get("bank_name"),
            "bsb": staged_bank.get("bsb"),
            "fund_type": staged_bank.get("fund_type"),
            "current_balance_cents": staged_bank.get("balance_cents", 0),
            "balance_snapshot_at": _now_iso(),
            "opening_balance_cents": staged_bank.get("balance_cents", 0),
            "account_type": "trust",
            "migration_source": "mri",
            "migration_batch_id": batch_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        await db.trust_accounts.insert_one(bank_doc)
        committed["bank_balances"] += 1

    for _col in _STAGING_COLLECTIONS:
        await db[_col].delete_many({"migration_batch_id": batch_id, "building_id": building_id})

    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {
            "status": MigrationBatchStatus.ROLLBACK_AVAILABLE.value,
            "committed_status": MigrationBatchStatus.COMMITTED.value,
            "committed_at": _now_iso(),
            "updated_at": _now_iso(),
        }}
    )

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="mri_migration_committed",
        entity_type="migration_batch",
        entity_id=batch_id,
        user_id=user.get("email"),
        building_id=building_id,
        details={"committed": committed, "skipped": skipped},
    ))

    return {
        "batch_id": batch_id,
        "status": MigrationBatchStatus.ROLLBACK_AVAILABLE.value,
        "committed": committed,
        "skipped": skipped,
        "committed_at": _now_iso(),
    }


# ─── Rollback ────────────────────────────────────────────────────────────────

@router.post("/batches/{batch_id}/rollback")
async def rollback_migration(
        batch_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Rollback a committed migration batch.

    This removes users/units/ledger entries created by this migration batch.
    Posted trust transactions are REVERSED (not deleted) per immutability rules.
    """
    user = _require_migration_role(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    batch = await db.migration_batches.find_one({"_id": ObjectId(batch_id), "building_id": building_id})
    if not batch:
        raise HTTPException(status_code=404, detail="Migration batch not found.")

    ROLLBACKABLE_STATUSES = (
        MigrationBatchStatus.COMMITTED.value,
        MigrationBatchStatus.ROLLBACK_AVAILABLE.value,
    )
    if batch.get("status") not in ROLLBACKABLE_STATUSES:
        raise HTTPException(status_code=400, detail="Only committed batches can be rolled back.")

    _assert_transition(
        batch.get("status", ""),
        MigrationBatchStatus.ROLLED_BACK.value,
        batch_id,
    )

    removed = {}

    # Remove migration-created users (non-destructive: only migration-sourced)
    res = await db.users.delete_many({"building_id": building_id, "migration_batch_id": batch_id})
    removed["users_deleted"] = res.deleted_count

    # Remove migration-created units
    res = await db.units.delete_many({"building_id": building_id, "migration_batch_id": batch_id})
    removed["units_deleted"] = res.deleted_count

    # Post reversal entries for ledger entries (additive reversal — do not delete)
    # Insert in batches of 100 to limit memory usage for large migrations
    BATCH_SIZE = 100
    reversal_batch: List[Dict[str, Any]] = []
    total_reversals = 0

    async for entry in db.trust_ledger_entries.find(
            {"building_id": building_id, "migration_batch_id": batch_id}
    ):
        reversal = {
            "building_id": building_id,
            "fund_type": entry.get("fund_type"),
            "date": _now_iso()[:10],
            "reference": f"ROLLBACK-{batch_id[:8].upper()}",
            "narration": f"Migration rollback reversal of {str(entry['_id'])}",
            "status": "posted",
            "source_type": "mri_migration_rollback",
            "source_id": batch_id,
            "migration_batch_id": batch_id,
            "reversal_of_id": str(entry["_id"]),
            "lines": [
                {
                    **line,
                    "entry_type": "credit" if line.get("entry_type") == "debit" else "debit",
                    "description": f"Rollback: {line.get('description', '')}",
                }
                for line in entry.get("lines", [])
            ],
            "is_balanced": True,
            "created_by": user.get("email"),
            "created_at": _now_iso(),
        }
        reversal_batch.append(reversal)
        if len(reversal_batch) >= BATCH_SIZE:
            await db.trust_ledger_entries.insert_many(reversal_batch)
            total_reversals += len(reversal_batch)
            reversal_batch = []

    if reversal_batch:
        await db.trust_ledger_entries.insert_many(reversal_batch)
        total_reversals += len(reversal_batch)

    removed["reversal_entries_posted"] = total_reversals

    # Mark trust_accounts created by this migration
    res = await db.trust_accounts.delete_many({"building_id": building_id, "migration_batch_id": batch_id})
    removed["trust_accounts_deleted"] = res.deleted_count

    for _col in _STAGING_COLLECTIONS:
        await db[_col].delete_many({"migration_batch_id": batch_id, "building_id": building_id})

    await db.migration_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {
            "status": MigrationBatchStatus.ROLLED_BACK.value,
            "rolled_back_at": _now_iso(),
            "updated_at": _now_iso(),
        }}
    )

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="mri_migration_rolled_back",
        entity_type="migration_batch",
        entity_id=batch_id,
        user_id=user.get("email"),
        building_id=building_id,
        details={"removed": removed},
    ))

    return {
        "batch_id": batch_id,
        "status": "rolled_back",
        "removed": removed,
        "rolled_back_at": _now_iso(),
    }
