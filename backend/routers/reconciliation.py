"""
Reconciliation Router — DEFT/BPAY Import logic.

# @featuretrace:levy — Third levy-payment write path: DEFT/BPAY settlement CSV.
# @featuretrace:demo_bank — under single-intake enforcement this importer stages into Demo Bank.
# Layer: router
# Data flow (default, enforcement OFF): staff CSV upload -> POST /reconciliation/import-deft ->
#            db.levy_payments.insert_one() -> routers.finance._upsert_ledger_for_payment() ->
#            unit_levy_ledger (building-scoped).
# Data flow (enforcement ON, disable_strata_sync_direct_write): staff CSV upload -> stage each row
#            into demo_bank_transactions (pending) via services.single_intake_service ->
#            [existing /bank-feeds/sync] -> finance.bank_transactions -> match_review_queue. No
#            direct levy_payments/unit_levy_ledger write occurs. This replaces the previous dead
#            409-redirect to a non-existent /demo-bank/import/csv endpoint (GAP-FIN-037).
# Related: backend/routers/finance.py (_upsert_ledger_for_payment)
#           backend/services/single_intake_service.py (Demo Bank staging helper)
#           backend/routers/financial_matching.py (the match-review queue staged rows flow to)
# Collection: levy_payments, unit_levy_ledger, demo_bank_transactions
"""
import csv
import io
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import List

from database import db
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_building
from utils.file_scan import scan_upload
from utils.helpers import create_audit_log, get_current_timestamp
from utils.permissions import require_permission

logger = logging.getLogger(__name__)

# When enabled, the direct levy_payments write path is bypassed.
# Payments are staged via Demo Bank and reach the ledger through MatchingEngine instead.
_DIRECT_WRITE_TOGGLE = "disable_strata_sync_direct_write"

router = APIRouter(prefix="/reconciliation")


class ImportSummary(BaseModel):
    total_rows: int
    imported_count: int
    duplicate_count: int
    error_count: int
    total_amount: float
    errors: List[str]
    # Rows staged into Demo Bank for review instead of directly written to the ledger, when
    # single-intake enforcement (disable_strata_sync_direct_write) is ON. 0 when enforcement is
    # OFF (default) — existing consumers are unaffected. When >0, imported_count is 0.
    staged_count: int = 0
    routed_to_demo_bank: bool = False


@router.post("/import-deft", response_model=ImportSummary)
async def import_deft_csv(
        year: str = Form(...),
        file: UploadFile = File(...),
        current_user: dict = Depends(require_permission("can_manage_finances")),
        building_id: str = Depends(get_current_building)
):
    """
    Import DEFT/BPAY settlement report from CSV. Scoped to building.

    Expected CSV columns:
    Date, Reference, Description, Amount, UnitNumber(Optional)
    """
    # Fetch building settings for the specific plan number used in DEFT references.
    # building_id IS the Unit Plan number, so it is the correct per-tenant fallback —
    # never another building's plan number.
    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    plan_num = str((settings or {}).get("plan_number") or building_id or "").strip()

    content = await file.read()
    await scan_upload(content, context="csv", filename=file.filename or "")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(decoded))

    # Single-intake enforcement: when disable_strata_sync_direct_write is ON for this building,
    # DEFT/BPAY rows are STAGED INTO DEMO BANK (pending review) rather than written straight to
    # levy_payments/unit_levy_ledger. They then flow to the match-review queue via the existing
    # /bank-feeds/sync pipeline. Default OFF -> unchanged direct-write behavior. This replaces the
    # previous 409-redirect to a non-existent /demo-bank/import/csv endpoint (GAP-FIN-037).
    from services.single_intake_service import (
        is_single_intake_enforced,
        stage_cash_receipt_into_demo_bank,
    )
    route_to_demo_bank = await is_single_intake_enforced(building_id)
    if route_to_demo_bank:
        logger.info(
            "reconciliation: single-intake enforced (%s) — staging DEFT import into Demo Bank "
            "for building %s instead of direct levy_payments write.",
            _DIRECT_WRITE_TOGGLE, building_id,
        )

    total_rows = 0
    imported_count = 0
    staged_count = 0
    duplicate_count = 0
    error_count = 0
    total_amount = 0.0
    errors = []

    for row in reader:
        total_rows += 1
        try:
            # Normalize fields
            date_str = row.get("Date") or row.get("Transaction Date")
            ref = row.get("Reference") or row.get("DEFT Reference")
            desc = row.get("Description") or ""
            amount_str = row.get("Amount") or "0"
            amount = float(amount_str.replace("$", "").replace(",", ""))

            if not ref or amount <= 0:
                continue

            # Try to infer unit number from reference if not provided
            # DEFT Ref pattern: 99 + PLAN_NUMBER + UNIT_NUMBER (3 digits)
            unit_number = row.get("UnitNumber") or row.get("Unit")
            if not unit_number and plan_num and plan_num in ref:
                # Extract last 3 digits after PLAN_NUMBER
                idx = ref.find(plan_num)
                unit_number = ref[idx + len(plan_num):].strip()
                if unit_number.startswith("UA") or unit_number.startswith("TH"):
                    pass  # keep it
                elif unit_number.isdigit():
                    # Map digit to Unit format if needed, but here we assume it might be raw
                    pass

            # Single-intake enforcement: stage into Demo Bank instead of a direct ledger write.
            # Idempotent (demo_bank_transactions upsert), so a re-import is a duplicate, not a
            # double-stage. No levy_payments/unit_levy_ledger write happens on this path.
            if route_to_demo_bank:
                amount_cents = int(round(amount * 100))  # cents at the ingestion boundary
                inserted, _idem = await stage_cash_receipt_into_demo_bank(
                    db._db,
                    building_id=building_id,
                    unit_number=unit_number or "",
                    amount_cents=amount_cents,
                    posted_date=date_str,
                    reference=ref,
                    description=f"DEFT/BPAY settlement import: {desc}",
                    source_type="deft_settlement_import",
                    payment_method="deft" if "DEFT" in desc.upper() else "bpay",
                    year=year,
                    is_test_data=False,
                )
                if inserted:
                    staged_count += 1
                    total_amount += amount
                else:
                    duplicate_count += 1
                continue

            # Duplication check based on building + ref + date + amount
            existing = await db.levy_payments.find_one({
                "building_id": building_id,
                "payment_reference": ref,
                "payment_date": date_str,
                "amount": amount
            })

            if existing:
                duplicate_count += 1
                continue

            # Create payment record
            payment_id = str(uuid.uuid4())
            now = get_current_timestamp()

            payment_doc = {
                "id": payment_id,
                "building_id": building_id,
                "unit_number": unit_number or "UNKNOWN",
                "amount": amount,
                "payment_method": "deft" if "DEFT" in desc.upper() else "bpay",
                "payment_reference": ref,
                "payment_date": date_str,
                "quarter": "AUTO",  # Inferred later or assigned to current
                "year": year,
                "status": "confirmed",
                "receipt_number": f"IMP-{ref[-6:]}",
                "notes": f"Auto-imported from settlement report: {desc}",
                "confirmed_by": current_user["id"],
                "confirmed_at": now,
                "created_at": now,
            }

            await db.levy_payments.insert_one(payment_doc)

            # Update ledger if unit identified
            if unit_number and unit_number != "UNKNOWN":
                from routers.finance import _upsert_ledger_for_payment
                await _upsert_ledger_for_payment(unit_number, year, amount, payment_id, building_id)

            imported_count += 1
            total_amount += amount

        except Exception as e:
            error_count += 1
            errors.append(f"Row {total_rows}: {str(e)}")

    await create_audit_log(
        action="import_completed",
        resource_type="reconciliation",
        resource_id=str(uuid.uuid4()),
        building_id=building_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "filename": file.filename,
            "imported": imported_count,
            "staged": staged_count,
            "routed_to_demo_bank": route_to_demo_bank,
            "total_amount": total_amount
        }
    )

    return ImportSummary(
        total_rows=total_rows,
        imported_count=imported_count,
        staged_count=staged_count,
        routed_to_demo_bank=route_to_demo_bank,
        duplicate_count=duplicate_count,
        error_count=error_count,
        total_amount=round(total_amount, 2),
        errors=errors[:10]  # limit returned errors
    )
