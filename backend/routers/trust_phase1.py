# @featuretrace:trust-accounting — Trust account CRUD, interest posting, levy schedules,
# advance-payment interest, financial summary. The current (non-deprecated) trust-account
# API — see trust_accounting.py's _V1_SUCCESSOR for the legacy V1 system this replaced.
# Layer: router
# Data flow: TrustBankAccountsPage.tsx / onboarding.py finalize → this router →
#            Mongo trust_accounts_v2 / trust_transactions_v2 (building-scoped, via JWT).
# Related: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
#           backend/routers/onboarding.py (create_trust_account_v2_record shared helper)
#           backend/routers/trust_accounting.py (legacy V1, no confirmed frontend caller)
# Toggle: trust_accounting
# Collection: trust_accounts_v2, trust_transactions_v2
"""
Trust Accounting Phase 1 Router — Multi-Tenant Foundation

New endpoints:
  GET  /trust/v2/config/{building_id}                   — get trust config
  PUT  /trust/v2/config/{building_id}                   — update trust config
  GET  /trust/v2/accounts                               — list accounts (phase 1)
  POST /trust/v2/accounts                               — create account
  PATCH /trust/v2/accounts/{account_id}                 — update account bank/interest settings
  GET  /trust/v2/accounts/{account_id}/balance          — balance summary
  GET  /trust/v2/accounts/{account_id}/interest-forecast — preview interest for a period
  POST /trust/v2/accounts/{account_id}/post-interest    — post bank interest income
  GET  /trust/v2/transactions                           — paginated ledger
  POST /trust/v2/transactions                           — create transaction
  POST /trust/v2/transactions/{tx_id}/reverse           — reverse transaction
  POST /trust/v2/levies/generate                        — generate levy schedules
  GET  /trust/v2/levies/{building_id}/schedule          — get levy schedule
  POST /trust/v2/levies/{schedule_id}/pay               — record manual payment
  GET  /trust/v2/levies/advance-payments                — list advance levy payments + interest
  POST /trust/v2/deft/webhook                           — DEFT webhook receiver
  POST /trust/v2/arrears/escalate                       — trigger arrears escalation
  GET  /trust/v2/financial-summary                      — aggregated financial summary

Multi-tenancy rule: building_id extracted from JWT, never from request body.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
import httpx
from datetime import date, datetime, timezone
from decimal import Decimal

import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional

from models.trust_accounting import (
    ArrearsEscalationResponse,
    BankAccountUpdate,
    BankAccountUpdateResponse,
    BuildingTrustConfigUpdate,
    DeftSimulatedPayment,
    DeftWebhookResponse,
    InterestPostingEnvelope,
    InterestPostingRequest,
    LevyGenerateEnvelope,
    LevyGenerateRequest,
    LevyPaymentResponse,
    TrustAccountCreateResponse,
    TrustAccountPhase1Create,
    TrustAuditLogCreate,
    TrustConfigUpdateResponse,
    TrustTransactionCreateResponse,
    TrustTransactionPhase1Create,
    TrustTransactionReversalResponse,
    format_aud,
)
from domain.finance.formulas.collection import quarterly_collection_rate
from services.trust_account_read_service import TrustAccountReadService
from utils.auth import effective_role
from utils.permissions import require_feature
# GAP-SEC-005 group 2, tier A. Added as an ADDITIONAL dependency alongside each
# route's existing check, never as a replacement — the effective rule is the
# intersection, so this can only narrow access. See
# docs/security/group2_financial_route_tiers_2026_08_24.md
from services.capability_registry import require_capability

logger = logging.getLogger(__name__)
# V2 is the live trust-accounting surface, so authenticated V2 routes must
# honor the same `trust_accounting` feature toggle as deprecated V1. The DEFT
# webhook is intentionally excluded because it authenticates via webhook secret,
# not an interactive user's feature access.
router = APIRouter(prefix="/trust/v2", tags=["Trust Accounting Phase 1"])
_trust_account_read_service = TrustAccountReadService()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


TRUST_MANAGE_ROLES = {"super_admin", "strata_manager", "strata_admin", "treasurer"}
TRUST_VIEW_ROLES = {"super_admin", "strata_manager", "strata_admin", "ec_member", "treasurer"}


def _require_trust_manage(user: dict) -> dict:
    """Generated function header.

    Function: _require_trust_manage
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in TRUST_MANAGE_ROLES:
        raise HTTPException(status_code=403, detail="Requires trust.manage permission.")
    return user


def _require_trust_view(user: dict) -> dict:
    """Generated function header.

    Function: _require_trust_view
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in TRUST_VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Requires trust.view permission.")
    return user


async def _get_building_id_from_jwt(user: dict, x_building_id: Optional[str] = None) -> str:
    """RULE 4: building_id from JWT, honouring the X-Building-ID header override
    for super_admin — same resolution as utils.auth.get_current_building's
    Postgres branch.

    The header carries the scheme UUID, not a Mongo-compatible plan number —
    AuthContext.tsx's axios interceptor sends `X-Building-ID: b.id`, and
    `/buildings/me` (routers/auth.py) documents `id` as the scheme UUID versus
    `building_id` as the plan number (e.g. "13195"). Mongo documents in this
    router's collections (trust_accounts_v2 etc.) store building_id as the
    plan number, so the header value MUST be resolved via identity_repo
    before use — passing it straight through silently breaks every Mongo
    query in this router (returns 0 rows, not a 400).
    """
    if x_building_id and effective_role(user) == "super_admin":
        from db_postgres.repos import identity_repo
        scheme = await identity_repo.get_scheme_by_id(x_building_id) or \
                 await identity_repo.get_scheme_by_number(x_building_id)
        if scheme:
            return str(scheme.get("scheme_number") or scheme["scheme_id"])
    bid = user.get("building_id")
    if not bid:
        raise HTTPException(status_code=400, detail="No building_id in token.")
    return str(bid)


def _trust_request_id() -> str:
    """Generated function header.

    Function: _trust_request_id
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid4())


async def _write_trust_audit(db, params: TrustAuditLogCreate) -> None:
    """Write trust audit log entry. Silent on failure."""
    try:
        doc = params.model_dump()
        doc["timestamp"] = _now_iso()
        await db.trust_audit_logs.insert_one(doc)
    except Exception as exc:
        logger.warning("Failed to write trust audit log: %s", exc)


# ─── Trust Config Routes ─────────────────────────────────────────────────────

@router.get("/config/{building_id_param}")
async def get_trust_config(
        building_id_param: str,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Get trust config for a building."""
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    if effective_role(current_user) != "super_admin" and building_id_param != jwt_bid:
        raise HTTPException(status_code=403, detail="Cannot access another building's config.")

    db = _get_db()
    building = await db.buildings.find_one({"id": building_id_param})
    if not building:
        raise HTTPException(status_code=404, detail="Building not found.")

    trust_config = building.get("trust_config", {})

    # Mask biller codes (show only last 4 chars)
    for field in ("deft_biller_code_admin", "deft_biller_code_sinking"):
        val = trust_config.get(field)
        if val and len(val) > 4:
            trust_config[field] = "****" + val[-4:]

    return JSONResponse(
        content={"success": True, "data": trust_config},
        headers={"X-Trust-Request-Id": _trust_request_id()},
    )


@router.put("/config/{building_id_param}", response_model=TrustConfigUpdateResponse)
async def update_trust_config(
        building_id_param: str,
        payload: BuildingTrustConfigUpdate,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Update trust config for a building."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    if effective_role(current_user) != "super_admin" and building_id_param != jwt_bid:
        raise HTTPException(status_code=403, detail="Cannot update another building's config.")

    db = _get_db()
    building = await db.buildings.find_one({"id": building_id_param})
    if not building:
        raise HTTPException(status_code=404, detail="Building not found.")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}

    if "quarterly_due_dates" in update_data:
        dates = update_data["quarterly_due_dates"]
        if len(dates) != 4:
            raise HTTPException(status_code=422, detail="quarterly_due_dates must be exactly 4 dates.")

    if "arrears_interest_rate" in update_data:
        rate = update_data["arrears_interest_rate"]
        if not (0 <= rate <= 1):
            raise HTTPException(status_code=422, detail="arrears_interest_rate must be between 0 and 1.")

    for field in ("admin_fund_annual_budget_cents", "sinking_fund_annual_budget_cents"):
        if field in update_data and update_data[field] < 0:
            raise HTTPException(status_code=422, detail=f"{field} must be non-negative.")

    # Recalculate total_uoe from units if not yet set
    current_config = building.get("trust_config", {})
    if current_config.get("total_uoe", 0) == 0:
        pipeline = [
            {"$match": {"building_id": building_id_param, "is_active": True}},
            {"$group": {"_id": None, "total": {"$sum": "$uoe"}}},
        ]
        result = await db.units.aggregate(pipeline).to_list(1)
        update_data["total_uoe"] = result[0]["total"] if result else 0

    # Mark trust as configured on first valid save
    if not current_config.get("is_trust_configured"):
        has_essentials = (
                update_data.get("admin_fund_annual_budget_cents",
                                current_config.get("admin_fund_annual_budget_cents", 0)) > 0
                and update_data.get("deft_biller_code_admin") is not None
        )
        if has_essentials:
            update_data["is_trust_configured"] = True
            update_data["trust_configured_at"] = _now_iso()
            update_data["trust_configured_by"] = str(current_user.get("_id", ""))

    set_fields = {f"trust_config.{k}": v for k, v in update_data.items()}
    await db.buildings.update_one(
        {"id": building_id_param},
        {"$set": set_fields},
    )

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=building_id_param,
        action="trust_config_updated",
        entity_type="Building",
        entity_id=building_id_param,
        performed_by=str(current_user.get("_id", "")),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        after_snapshot=update_data,
    ))

    return JSONResponse(
        content={"success": True, "message": "Trust config updated."},
        headers={"X-Trust-Request-Id": _trust_request_id()},
    )


# ─── Trust Accounts ───────────────────────────────────────────────────────────

@router.get("/accounts")
async def list_trust_accounts_v2(
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """List trust accounts for the JWT building."""
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    results = await _trust_account_read_service.list_accounts(db, building_id=jwt_bid)
    _maybe_shadow_v2_account_summary(jwt_bid, mongo_payload={"accounts": results})

    return JSONResponse(
        content={"success": True, "data": results},
        headers={"X-Trust-Request-Id": _trust_request_id()},
    )


def _maybe_shadow_v2_account_summary(building_id: str, *, mongo_payload: dict[str, Any]) -> None:
    """Fire-and-forget comparison for the live V2 trust account summary.

    V2 account list is the first trust_ledger comparator aimed at the actual live trust
    system. It compares aggregate account counts and computed balance totals only until
    account-id/fund-id mapping is formally proven.
    """
    async def _run() -> None:
        """Generated function header.

        Function: _run
        Path: backend/routers/trust_phase1.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            from services.domain_source_guard import require_domain_source
            decision = await require_domain_source(
                domain="trust_ledger",
                building_id=building_id,
                operation="shadow_read",
            )
            if not decision.shadow_enabled:
                return

            from services.trust_read_service import TrustReadService
            from services.trust_shadow_read_service import compare_trust_v2_account_summary
            pg_accounts = await TrustReadService().list_trust_accounts(building_id=building_id)
            pg_payload = None if pg_accounts is None else {"accounts": pg_accounts}
            await compare_trust_v2_account_summary(
                building_id=building_id,
                mongo_payload=mongo_payload,
                pg_payload=pg_payload,
            )
        except Exception as exc:
            logger.debug("trust v2 account-summary shadow compare failed: %s", exc)

    from services.shadow_read_service import schedule_shadow_compare
    schedule_shadow_compare(
        _run(),
        domain="trust_ledger",
        context={"building_id": building_id, "route": "trust_v2.accounts.summary"},
    )


async def create_trust_account_v2_record(
        payload: TrustAccountPhase1Create,
        building_id: str,
        current_user: dict,
) -> str:
    """Shared insert logic for a new V2 trust account (``trust_accounts_v2``).

    Used by ``POST /accounts`` below and by onboarding finalize
    (``routers/onboarding.py``) so both paths create identical, audited
    rows. Unlike the route, ``building_id`` is an explicit parameter here
    rather than sourced from the caller's JWT — onboarding finalize creates
    the account for the *new* building, not the operator's own.  Callers are
    responsible for their own role/permission gating before calling this.

    Returns the new account's Mongo ObjectId as a string.
    """
    db = _get_db()
    doc = payload.model_dump()
    doc["building_id"] = building_id
    doc["created_by"] = str(current_user.get("_id") or current_user.get("id") or "")
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()

    result = await db.trust_accounts_v2.insert_one(doc)

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=building_id,
        action="trust_account_created",
        entity_type="TrustAccount",
        entity_id=str(result.inserted_id),
        performed_by=str(current_user.get("_id") or current_user.get("id") or ""),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        after_snapshot={"account_type": payload.account_type},
    ))

    return str(result.inserted_id)


@router.post("/accounts", status_code=201, response_model=TrustAccountCreateResponse)
async def create_trust_account_v2(
        payload: TrustAccountPhase1Create,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        # Writes bank account details (v2 trust API).
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Create a trust account."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    account_id = await create_trust_account_v2_record(payload, jwt_bid, current_user)

    return JSONResponse(
        content={"success": True, "id": account_id},
        headers={"X-Trust-Request-Id": _trust_request_id()},
        status_code=201,
    )


@router.patch("/accounts/{account_id}", response_model=BankAccountUpdateResponse)
async def update_trust_account(
        account_id: str,
        payload: BankAccountUpdate,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        # Changes bank account details. Separation of duty (plan §4.7) makes this a
        # distinct trust boundary from approving the next payment to that payee;
        # enforcing the pair is GAP-SEC-007.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Update bank account settings (interest rate, account category, BSB, etc.)."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    account = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if str(account.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Account belongs to a different building.")

    before_snapshot = {k: account.get(k) for k in (
        "bank_name", "account_name", "bsb", "account_number_masked",
        "account_category", "interest_rate_pa", "interest_calc_method",
        "term_deposit_maturity_date", "deft_biller_code", "is_active", "notes",
    )}

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "account_category" in update_data:
        update_data["account_category"] = update_data["account_category"].value if hasattr(
            update_data["account_category"], "value") else update_data["account_category"]
    if "interest_calc_method" in update_data:
        update_data["interest_calc_method"] = update_data["interest_calc_method"].value if hasattr(
            update_data["interest_calc_method"], "value") else update_data["interest_calc_method"]
    if "interest_rate_pa" in update_data:
        rate = update_data["interest_rate_pa"]
        if not (0.0 <= rate <= 1.0):
            raise HTTPException(status_code=422,
                                detail="interest_rate_pa must be between 0.0 and 1.0 (e.g. 0.045 = 4.5%).")

    update_data["updated_at"] = _now_iso()
    await db.trust_accounts_v2.update_one({"_id": ObjectId(account_id)}, {"$set": update_data})

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=jwt_bid,
        action="trust_account_updated",
        entity_type="TrustAccount",
        entity_id=account_id,
        performed_by=str(current_user.get("_id", "")),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        before_snapshot=before_snapshot,
        after_snapshot=update_data,
    ))

    updated = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
    return JSONResponse(content={"success": True, "data": {
        "id": str(updated["_id"]),
        "account_category": updated.get("account_category", "transaction"),
        "interest_rate_pa": updated.get("interest_rate_pa", 0.0),
        "interest_calc_method": updated.get("interest_calc_method", "daily_balance"),
        "bank_name": updated.get("bank_name"),
        "account_name": updated.get("account_name"),
        "bsb": updated.get("bsb"),
    }})


@router.get("/accounts/{account_id}/interest-forecast")
async def get_interest_forecast(
        account_id: str,
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        period_start: str = Query(..., description="ISO date e.g. 2026-04-01"),
        period_end: str = Query(..., description="ISO date e.g. 2026-04-30"),
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """Preview the bank interest income expected for an account over a given period.
    
    Uses the account's current balance and configured interest_rate_pa.
    Shows how the interest would be split between admin_fund and sinking_fund
    proportionally by their current balances.
    """
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    account = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if str(account.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Account belongs to a different building.")

    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError:
        raise HTTPException(status_code=422, detail="period_start and period_end must be ISO dates (YYYY-MM-DD).")

    days = (end - start).days + 1
    if days <= 0:
        raise HTTPException(status_code=422, detail="period_end must be after period_start.")

    # Use opening_balance as base for interest forecast (computed balance from tx preferred but costly here)
    balance_cents = account.get("opening_balance_cents", account.get("last_reconciled_balance_cents", 0))
    rate_pa = account.get("interest_rate_pa", 0.0)
    forecast_cents = round(balance_cents * rate_pa * days / 365)

    # Split proportionally between admin and sinking fund accounts
    all_accounts = await db.trust_accounts_v2.find(
        {"building_id": jwt_bid, "is_active": True}
    ).to_list(length=20)

    admin_bal = sum(a.get("opening_balance_cents", a.get("last_reconciled_balance_cents", 0)) for a in all_accounts if
                    a.get("account_type") == "admin_fund")
    sinking_bal = sum(
        a.get("opening_balance_cents", a.get("last_reconciled_balance_cents", 0)) for a in all_accounts if
        a.get("account_type") == "sinking_fund")
    total_bal = admin_bal + sinking_bal or 1

    admin_split = round(forecast_cents * admin_bal / total_bal)
    sinking_split = forecast_cents - admin_split

    # Check if interest already posted for this period
    already_posted = await db.trust_interest_postings.count_documents({
        "building_id": jwt_bid,
        "account_id": ObjectId(account_id),
        "period_start": {"$lte": period_end},
        "period_end": {"$gte": period_start},
    })

    return JSONResponse(content={"success": True, "data": {
        "account_id": account_id,
        "account_name": account.get("account_name"),
        "account_type": account.get("account_type"),
        "account_category": account.get("account_category", "transaction"),
        "interest_rate_pa": rate_pa,
        "interest_rate_display": f"{rate_pa * 100:.2f}%",
        "period_start": period_start,
        "period_end": period_end,
        "days_in_period": days,
        "average_balance_cents": balance_cents,
        "average_balance_display": format_aud(balance_cents),
        "forecast_interest_cents": forecast_cents,
        "forecast_interest_display": format_aud(forecast_cents),
        "admin_split_cents": admin_split,
        "admin_split_display": format_aud(admin_split),
        "sinking_split_cents": sinking_split,
        "sinking_split_display": format_aud(sinking_split),
        "already_posted_this_period": already_posted > 0,
    }})


@router.post("/accounts/{account_id}/post-interest", response_model=InterestPostingEnvelope)
async def post_interest_income(
        account_id: str,
        payload: InterestPostingRequest,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Post bank interest income earned on a trust account for a period.

    Interest is split proportionally between admin_fund and sinking_fund based
    on their current balances. Two trust transactions are created (one per fund).
    A posting record is stored so the same period cannot be posted twice.
    """
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    account = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if str(account.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Account belongs to a different building.")

    # Guard against double-posting the same period
    existing = await db.trust_interest_postings.find_one({
        "building_id": jwt_bid,
        "account_id": ObjectId(account_id),
        "period_start": payload.period_start,
        "period_end": payload.period_end,
    })
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Interest already posted for this account and period ({payload.period_start} to {payload.period_end}). Void the existing posting first.",
        )

    try:
        start = date.fromisoformat(payload.period_start)
        end = date.fromisoformat(payload.period_end)
    except ValueError:
        raise HTTPException(status_code=422, detail="period_start and period_end must be ISO dates (YYYY-MM-DD).")

    days = (end - start).days + 1
    balance_cents = account.get("opening_balance_cents", account.get("last_reconciled_balance_cents", 0))
    rate_pa = account.get("interest_rate_pa", 0.0)

    # Use provided amount_cents or auto-calculate
    if payload.amount_cents is not None:
        total_interest = payload.amount_cents
    else:
        total_interest = round(balance_cents * rate_pa * days / 365)

    if total_interest <= 0:
        raise HTTPException(status_code=422,
                            detail="Calculated interest is zero or negative. Check interest_rate_pa on the account.")

    # Determine admin/sinking split
    if payload.admin_amount_cents is not None and payload.sinking_amount_cents is not None:
        admin_interest = payload.admin_amount_cents
        sinking_interest = payload.sinking_amount_cents
    else:
        all_accounts = await db.trust_accounts_v2.find(
            {"building_id": jwt_bid, "is_active": True}
        ).to_list(length=20)
        admin_bal = sum(
            a.get("opening_balance_cents", a.get("last_reconciled_balance_cents", 0)) for a in all_accounts if
            a.get("account_type") == "admin_fund")
        sinking_bal = sum(
            a.get("opening_balance_cents", a.get("last_reconciled_balance_cents", 0)) for a in all_accounts if
            a.get("account_type") == "sinking_fund")
        total_bal = admin_bal + sinking_bal or 1
        admin_interest = round(total_interest * admin_bal / total_bal)
        sinking_interest = total_interest - admin_interest

    now = _now_iso()
    performed_by = str(current_user.get("_id", ""))
    ref = payload.reference or f"INTEREST-{payload.period_start[:7].replace('-', '')}"
    description = f"Bank interest income {payload.period_start} to {payload.period_end}"

    admin_tx_id = None
    sinking_tx_id = None

    # Find admin and sinking accounts
    admin_acc = await db.trust_accounts_v2.find_one(
        {"building_id": jwt_bid, "account_type": "admin_fund", "is_active": True})
    sinking_acc = await db.trust_accounts_v2.find_one(
        {"building_id": jwt_bid, "account_type": "sinking_fund", "is_active": True})

    if admin_interest > 0 and admin_acc:
        tx = {
            "building_id": jwt_bid,
            "trust_account_id": admin_acc["_id"],
            "type": "interest",
            "amount_cents": admin_interest,
            "gst_cents": 0,
            "description": description + " (admin fund share)",
            "reference": ref,
            "payment_method": "bank_credit",
            "is_reconciled": False,
            "requires_approval": False,
            "created_by": performed_by,
            "created_at": now,
            "metadata": {"interest_posting": True, "period_start": payload.period_start,
                         "period_end": payload.period_end},
        }
        res = await db.trust_transactions_v2.insert_one(tx)
        admin_tx_id = str(res.inserted_id)
        await db.trust_accounts_v2.update_one(
            {"_id": admin_acc["_id"]},
            {"$set": {"updated_at": now}},
        )

    if sinking_interest > 0 and sinking_acc:
        tx = {
            "building_id": jwt_bid,
            "trust_account_id": sinking_acc["_id"],
            "type": "interest",
            "amount_cents": sinking_interest,
            "gst_cents": 0,
            "description": description + " (capital works fund share)",
            "reference": ref,
            "payment_method": "bank_credit",
            "is_reconciled": False,
            "requires_approval": False,
            "created_by": performed_by,
            "created_at": now,
            "metadata": {"interest_posting": True, "period_start": payload.period_start,
                         "period_end": payload.period_end},
        }
        res = await db.trust_transactions_v2.insert_one(tx)
        sinking_tx_id = str(res.inserted_id)
        await db.trust_accounts_v2.update_one(
            {"_id": sinking_acc["_id"]},
            {"$set": {"updated_at": now}},
        )

    # Store posting record (idempotency guard + history)
    posting_doc = {
        "building_id": jwt_bid,
        "account_id": ObjectId(account_id),
        "period_start": payload.period_start,
        "period_end": payload.period_end,
        "total_interest_cents": total_interest,
        "admin_interest_cents": admin_interest,
        "sinking_interest_cents": sinking_interest,
        "admin_transaction_id": admin_tx_id,
        "sinking_transaction_id": sinking_tx_id,
        "reference": ref,
        "notes": payload.notes,
        "posted_by": performed_by,
        "posted_at": now,
        "is_auto_posted": False,
    }
    posting_res = await db.trust_interest_postings.insert_one(posting_doc)

    # Update account with last_interest_posted metadata
    await db.trust_accounts_v2.update_one(
        {"_id": ObjectId(account_id)},
        {"$set": {
            "last_interest_posted_at": now,
            "last_interest_period_end": payload.period_end,
            "updated_at": now,
        }, "$inc": {"interest_ytd_cents": total_interest}},
    )

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=jwt_bid,
        action="interest_income_posted",
        entity_type="TrustAccount",
        entity_id=account_id,
        performed_by=performed_by,
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        after_snapshot={
            "period_start": payload.period_start, "period_end": payload.period_end,
            "total_interest_cents": total_interest,
        },
    ))

    return JSONResponse(content={"success": True, "data": {
        "id": str(posting_res.inserted_id),
        "building_id": jwt_bid,
        "account_id": account_id,
        "account_name": account.get("account_name"),
        "period_start": payload.period_start,
        "period_end": payload.period_end,
        "total_interest_cents": total_interest,
        "total_interest_display": format_aud(total_interest),
        "admin_interest_cents": admin_interest,
        "admin_interest_display": format_aud(admin_interest),
        "sinking_interest_cents": sinking_interest,
        "sinking_interest_display": format_aud(sinking_interest),
        "admin_transaction_id": admin_tx_id,
        "sinking_transaction_id": sinking_tx_id,
        "reference": ref,
        "posted_at": now,
    }}, status_code=201)


@router.get("/accounts/{account_id}/balance")
async def get_account_balance(
        account_id: str,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Balance summary for one trust account."""
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    account = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    if str(account.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Account belongs to a different building.")

    account_building_id = str(account.get("building_id") or jwt_bid)
    balance = await _trust_account_read_service.get_account_balance(
        db,
        building_id=account_building_id,
        account_id=account_id,
    )
    if balance is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    if balance.get("not_accessible"):
        raise HTTPException(status_code=403, detail="Account belongs to a different building.")

    return JSONResponse(content={
        "success": True,
        "data": balance,
    })


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.get("/transactions")
async def list_transactions_v2(
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        trust_account_id: str = Query(...),
        page: int = Query(1, ge=1),
        limit: int = Query(25, ge=1, le=100),
        type: Optional[str] = Query(None),
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
        is_reconciled: Optional[bool] = Query(None),
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """Paginated ledger with running balance."""
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()

    account = await db.trust_accounts_v2.find_one({"_id": ObjectId(trust_account_id)})
    if not account or (str(account.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin"):
        raise HTTPException(status_code=403, detail="Account not accessible.")

    account_building_id = str(account.get("building_id") or jwt_bid)
    payload = await _trust_account_read_service.list_transactions(
        db,
        building_id=account_building_id,
        account_id=trust_account_id,
        page=page,
        limit=limit,
        transaction_type=type,
        from_date=from_date,
        to_date=to_date,
        is_reconciled=is_reconciled,
    )
    if payload is None or payload.get("not_accessible"):
        raise HTTPException(status_code=403, detail="Account not accessible.")

    return JSONResponse(content={
        "success": True,
        "data": payload["data"],
        "pagination": payload["pagination"],
    })


@router.post("/transactions", status_code=201, response_model=TrustTransactionCreateResponse)
async def create_transaction_v2(
        payload: TrustTransactionPhase1Create,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Create an immutable trust transaction."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    if not isinstance(payload.amount_cents, int) or payload.amount_cents <= 0:
        raise HTTPException(status_code=422, detail="amount_cents must be a positive integer.")

    db = _get_db()

    account = await db.trust_accounts_v2.find_one({"_id": ObjectId(payload.trust_account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found.")
    if str(account.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Account belongs to a different building.")

    doc = payload.model_dump()
    doc["building_id"] = jwt_bid
    doc["trust_account_id"] = ObjectId(payload.trust_account_id)
    if payload.trust_levy_schedule_id:
        doc["trust_levy_schedule_id"] = ObjectId(payload.trust_levy_schedule_id)
    if payload.unit_id:
        doc["unit_id"] = ObjectId(payload.unit_id)
    doc["created_by"] = str(current_user.get("_id", ""))
    doc["created_at"] = _now_iso()
    doc["is_reversed"] = False

    result = await db.trust_transactions_v2.insert_one(doc)

    # Atomically update account balance
    if payload.type in ("receipt", "interest"):
        await db.trust_accounts_v2.update_one(
            {"_id": ObjectId(payload.trust_account_id)},
            {"$set": {"updated_at": _now_iso()}},
        )
    else:
        await db.trust_accounts_v2.update_one(
            {"_id": ObjectId(payload.trust_account_id)},
            {"$set": {"updated_at": _now_iso()}},
        )

    # If linked to a levy schedule, update paid/outstanding status
    if payload.trust_levy_schedule_id and payload.type == "receipt":
        schedule = await db.trust_levy_schedules_v2.find_one({"_id": ObjectId(payload.trust_levy_schedule_id)})
        if schedule:
            new_paid = schedule.get("paid_cents", 0) + payload.amount_cents
            total = schedule.get("total_cents", 0)
            outstanding = max(0, total - new_paid)
            new_status = "paid" if outstanding == 0 else ("partial" if new_paid > 0 else schedule.get("status"))
            await db.trust_levy_schedules_v2.update_one(
                {"_id": ObjectId(payload.trust_levy_schedule_id)},
                {"$set": {
                    "paid_cents": new_paid,
                    "outstanding_cents": outstanding,
                    "status": new_status,
                    "paid_at": _now_iso() if outstanding == 0 else None,
                    "updated_at": _now_iso(),
                }},
            )

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=jwt_bid,
        action="transaction_created",
        entity_type="TrustTransaction",
        entity_id=str(result.inserted_id),
        performed_by=str(current_user.get("_id", "")),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        after_snapshot={"type": payload.type, "amount_cents": payload.amount_cents},
    ))

    return JSONResponse(
        content={"success": True, "id": str(result.inserted_id)},
        headers={"X-Trust-Request-Id": _trust_request_id()},
        status_code=201,
    )


@router.post("/transactions/{tx_id}/reverse", response_model=TrustTransactionReversalResponse)
async def reverse_transaction_v2(
        tx_id: str,
        body: dict,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Reverse a transaction."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    original = await db.trust_transactions_v2.find_one({"_id": ObjectId(tx_id)})
    if not original:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    if str(original.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Transaction belongs to a different building.")

    if original.get("is_reversed"):
        raise HTTPException(status_code=409, detail="Transaction already reversed.")

    reason = body.get("reason", "")

    reversal_doc = {
        "building_id": jwt_bid,
        "trust_account_id": original.get("trust_account_id"),
        "type": "reversal",
        "amount_cents": original.get("amount_cents", 0),
        "gst_cents": original.get("gst_cents", 0),
        "description": f"REVERSAL: {original.get('description', '')}. Reason: {reason}",
        "reversal_of_id": ObjectId(tx_id),
        "is_reversed": False,
        "is_reconciled": False,
        "requires_approval": False,
        "created_by": str(current_user.get("_id", "")),
        "created_at": _now_iso(),
    }

    rev_result = await db.trust_transactions_v2.insert_one(reversal_doc)

    await db.trust_transactions_v2.update_one(
        {"_id": ObjectId(tx_id)},
        {"$set": {"is_reversed": True}},
    )

    # Reverse the balance effect on the account
    amount = original.get("amount_cents", 0)
    orig_type = original.get("type")
    account_id = original.get("trust_account_id")
    if orig_type in ("receipt", "interest"):
        await db.trust_accounts_v2.update_one(
            {"_id": account_id},
            {"$set": {"updated_at": _now_iso()}},
        )
    else:
        await db.trust_accounts_v2.update_one(
            {"_id": account_id},
            {"$set": {"updated_at": _now_iso()}},
        )

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=jwt_bid,
        action="transaction_reversed",
        entity_type="TrustTransaction",
        entity_id=tx_id,
        performed_by=str(current_user.get("_id", "")),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        metadata={"reason": reason, "reversal_id": str(rev_result.inserted_id)},
    ))

    return JSONResponse(content={"success": True, "reversal_id": str(rev_result.inserted_id)})


# ─── Levy Generation ─────────────────────────────────────────────────────────

def _proportional_split(total_cents: int, weights: list) -> list:
    """Largest-remainder proportional split. Sums EXACTLY to total_cents."""
    if not weights:
        return []
    total_weight = sum(weights)
    if total_weight == 0:
        return [0] * len(weights)

    exact = [(total_cents * w) / total_weight for w in weights]
    floored = [int(x) for x in exact]
    remainders = [e - f for e, f in zip(exact, floored)]

    distributed = sum(floored)
    extra = total_cents - distributed

    indices = sorted(range(len(remainders)), key=lambda i: remainders[i], reverse=True)
    result = list(floored)
    for i in range(extra):
        result[indices[i]] += 1

    return result


def _luhn_check_digit(digits: str) -> int:
    """Generated function header.

    Function: _luhn_check_digit
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    nums = list(reversed([int(c) for c in digits]))
    total = 0
    for idx, d in enumerate(nums):
        if idx % 2 == 1:
            doubled = d * 2
            total += (doubled - 9) if doubled > 9 else doubled
        else:
            total += d
    return (10 - (total % 10)) % 10


def _generate_deft_crn(lot_number: int, quarter: str, biller_code: str) -> str:
    """Generated function header.

    Function: _generate_deft_crn
    Path: backend/routers/trust_phase1.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    lot_padded = str(lot_number).zfill(4)
    quarter_code = quarter.replace("-", "")
    base_for_luhn = "".join(c for c in (biller_code + lot_padded + quarter_code) if c.isdigit())
    check_digit = _luhn_check_digit(base_for_luhn)
    return f"{biller_code}-{lot_padded}-{quarter_code}-{check_digit}"


@router.post("/levies/generate", response_model=LevyGenerateEnvelope)
async def generate_levy_schedules(
        payload: LevyGenerateRequest,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Generate quarterly levy schedules for all active units."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()

    building = await db.buildings.find_one({"id": jwt_bid})
    if not building:
        raise HTTPException(status_code=404, detail="Building not found.")

    trust_config = building.get("trust_config", {})

    if not trust_config.get("is_trust_configured", False):
        raise HTTPException(
            status_code=422,
            detail="Trust accounting is not configured for this building. Please complete trust setup first.",
        )

    units = await db.units.find(
        {"building_id": jwt_bid, "is_active": True}
    ).to_list(500)

    if not units:
        raise HTTPException(status_code=422, detail="No active units found for this building.")

    total_uoe = sum(u.get("uoe", 0) for u in units)
    config_uoe = trust_config.get("total_uoe", 0)
    if config_uoe != total_uoe and config_uoe != 0:
        logger.warning("total_uoe mismatch: config=%d, calculated=%d", config_uoe, total_uoe)

    admin_annual = trust_config.get("admin_fund_annual_budget_cents", 0)
    sinking_annual = trust_config.get("sinking_fund_annual_budget_cents", 0)
    admin_quarterly = admin_annual // 4
    sinking_quarterly = sinking_annual // 4

    biller_code = trust_config.get("deft_biller_code_admin")
    if not biller_code:
        raise HTTPException(
            status_code=422,
            detail="DEFT admin biller code is not configured. Please complete trust setup.",
        )
    grace_period_days = trust_config.get("grace_period_days", 14)

    uoe_list = [u.get("uoe", 0) for u in units]
    admin_splits = _proportional_split(admin_quarterly, uoe_list)
    sinking_splits = _proportional_split(sinking_quarterly, uoe_list)

    created = 0
    skipped = 0

    for i, unit in enumerate(units):
        unit_id = str(unit["_id"])

        # Idempotency: skip if schedule already exists for this unit/quarter
        existing = await db.trust_levy_schedules_v2.find_one({
            "building_id": jwt_bid,
            "unit_id": ObjectId(unit_id),
            "quarter": payload.quarter,
        })
        if existing:
            skipped += 1
            continue

        admin_fund_cents = admin_splits[i]
        sinking_fund_cents = sinking_splits[i]
        total_cents = admin_fund_cents + sinking_fund_cents

        deft_crn = _generate_deft_crn(unit.get("lot_number", i + 1), payload.quarter, biller_code)

        doc = {
            "building_id": jwt_bid,
            "unit_id": ObjectId(unit_id),
            "unit_number": unit.get("unit_number", ""),
            "lot_number": unit.get("lot_number", i + 1),
            "uoe_snapshot": unit.get("uoe", 0),
            "total_uoe_snapshot": total_uoe,
            "admin_budget_cents_snapshot": admin_quarterly,
            "sinking_budget_cents_snapshot": sinking_quarterly,
            "quarter": payload.quarter,
            "financial_year": payload.financial_year,
            "due_date": payload.due_date,
            "grace_period_days": grace_period_days,
            "admin_fund_cents": admin_fund_cents,
            "sinking_fund_cents": sinking_fund_cents,
            "special_levy_cents": 0,
            "total_cents": total_cents,
            "deft_crn": deft_crn,
            "status": "pending",
            "paid_cents": 0,
            "outstanding_cents": total_cents,
            "paid_at": None,
            "overdue_since": None,
            "interest_accrued_cents": 0,
            "drb_stage": "none",
            "notes": None,
            "generated_by": str(current_user.get("_id", "")),
            "generated_at": _now_iso(),
            "updated_at": _now_iso(),
        }

        await db.trust_levy_schedules_v2.insert_one(doc)
        created += 1

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=jwt_bid,
        action="levy_schedule_generated",
        entity_type="Building",
        entity_id=jwt_bid,
        performed_by=str(current_user.get("_id", "")),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        metadata={
            "quarter": payload.quarter,
            "created": created,
            "skipped": skipped,
        },
    ))

    return JSONResponse(content={
        "success": True,
        "data": {
            "created": created,
            "skipped": skipped,
            "total_units": len(units),
            "admin_quarterly_cents": admin_quarterly,
            "admin_quarterly_display": format_aud(admin_quarterly),
            "sinking_quarterly_cents": sinking_quarterly,
            "sinking_quarterly_display": format_aud(sinking_quarterly),
            "rounding_difference_cents": 0,
            "quarter": payload.quarter,
            "financial_year": payload.financial_year,
        }
    })


@router.get("/levies/{building_id_param}/schedule")
async def get_levy_schedule(
        building_id_param: str,
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        quarter: str = Query(...),
        page: int = Query(1, ge=1),
        limit: int = Query(25, ge=1, le=100),
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """Get levy schedule for a building and quarter."""
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)
    role = effective_role(current_user)

    if role not in TRUST_VIEW_ROLES and role not in ("owner", "tenant"):
        raise HTTPException(status_code=403, detail="Access denied.")

    if role != "super_admin" and building_id_param != jwt_bid:
        raise HTTPException(status_code=403, detail="Cannot access another building's levy schedule.")

    db = _get_db()
    match: Dict[str, Any] = {
        "building_id": building_id_param,
        "quarter": quarter,
    }

    # Owners/tenants see only their own unit's schedules
    if role in ("owner", "tenant"):
        unit_id = current_user.get("unit_id")
        if unit_id:
            match["unit_id"] = ObjectId(str(unit_id))

    skip = (page - 1) * limit
    docs = await db.trust_levy_schedules_v2.find(match).skip(skip).limit(limit).to_list(limit)
    total = await db.trust_levy_schedules_v2.count_documents(match)

    results = []
    for doc in docs:
        results.append({
            "id": str(doc["_id"]),
            "building_id": str(doc.get("building_id", "")),
            "unit_id": str(doc.get("unit_id", "")),
            "unit_number": doc.get("unit_number", ""),
            "lot_number": doc.get("lot_number", 0),
            "quarter": doc.get("quarter", ""),
            "financial_year": doc.get("financial_year", ""),
            "due_date": doc.get("due_date", ""),
            "admin_fund_cents": doc.get("admin_fund_cents", 0),
            "admin_fund_display": format_aud(doc.get("admin_fund_cents", 0)),
            "sinking_fund_cents": doc.get("sinking_fund_cents", 0),
            "sinking_fund_display": format_aud(doc.get("sinking_fund_cents", 0)),
            "total_cents": doc.get("total_cents", 0),
            "total_display": format_aud(doc.get("total_cents", 0)),
            "deft_crn": doc.get("deft_crn", ""),
            "status": doc.get("status", ""),
            "paid_cents": doc.get("paid_cents", 0),
            "paid_display": format_aud(doc.get("paid_cents", 0)),
            "outstanding_cents": doc.get("outstanding_cents", 0),
            "outstanding_display": format_aud(doc.get("outstanding_cents", 0)),
        })

    return JSONResponse(content={
        "success": True,
        "data": results,
        "pagination": {"page": page, "limit": limit, "total": total},
    })


# @featuretrace:levy — CONFIRMED GAP: second, independent levy-payment ledger with no reconciliation
#                      to the primary one.
# Layer: router
# Data flow: TrustBankAccountsPage.tsx / TrustAccountingPage.tsx -> POST /trust/v2/levies/{id}/pay
#            -> trust_transactions_v2 + trust_levy_schedules_v2 (building-scoped).
# Related: backend/routers/finance.py (_upsert_ledger_for_payment — the other levy-payment writer)
#           frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
# Collection: trust_transactions_v2, trust_levy_schedules_v2

# IMPORTANT: this writes a completely separate data model from finance.py's POST /levy-payments
#            (-> levy_payments + unit_levy_ledger). No code path reconciles the two. A payment
#            recorded here does not appear on FinancePage, CollectionRatePage, OwnerDashboard,
#            MyFinancesPage, LevyPaymentPage, UnitFinanceDetailPage, or ArrearsRecoveryPage — all of
#            which read levy_payments/unit_levy_ledger, never trust_transactions_v2. See
#            docs/architecture/mindmap/merge_candidates.md A2 and
#            docs/architecture/mindmap/levy-payment-current-state-journey.md §4.
@router.post("/levies/{schedule_id}/pay", response_model=LevyPaymentResponse)
async def record_levy_payment(
        schedule_id: str,
        body: dict,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Manually record a levy payment.
    
    Detects advance payments (payment_date before due_date) and calculates
    the bank interest earned by the Owners Corporation on the advance amount,
    using the trust account's configured interest_rate_pa.
    """
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    schedule = await db.trust_levy_schedules_v2.find_one({"_id": ObjectId(schedule_id)})
    if not schedule:
        raise HTTPException(status_code=404, detail="Levy schedule not found.")

    if str(schedule.get("building_id")) != jwt_bid and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Schedule belongs to a different building.")

    amount_cents = body.get("amount_cents", 0)
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise HTTPException(status_code=422, detail="amount_cents must be a positive integer.")

    payment_date_str = body.get("payment_date") or _now_iso()[:10]  # default to today

    account = await db.trust_accounts_v2.find_one({
        "building_id": jwt_bid,
        "account_type": "admin_fund",
    })
    account_id = str(account["_id"]) if account else None

    # ── Advance payment detection ──────────────────────────────────────────
    due_date_str = schedule.get("due_date", "")[:10]
    is_advance = False
    advance_days = 0
    advance_interest_cents = 0
    interest_rate_pa = 0.0

    try:
        payment_date_obj = date.fromisoformat(payment_date_str[:10])
        due_date_obj = date.fromisoformat(due_date_str)
        if payment_date_obj < due_date_obj:
            is_advance = True
            advance_days = (due_date_obj - payment_date_obj).days
            # Use the trust account's interest rate for advance interest calc
            if account:
                interest_rate_pa = account.get("interest_rate_pa", 0.0)
            advance_interest_cents = round(amount_cents * interest_rate_pa * advance_days / 365)
    except (ValueError, TypeError):
        pass  # If dates are malformed, treat as non-advance

    now_iso_str = _now_iso()
    tx_doc = {
        "building_id": jwt_bid,
        "trust_account_id": ObjectId(account_id) if account_id else None,
        "trust_levy_schedule_id": ObjectId(schedule_id),
        "unit_id": schedule.get("unit_id"),
        "unit_number": schedule.get("unit_number"),
        "type": "receipt",
        "amount_cents": amount_cents,
        "gst_cents": 0,
        "description": f"Levy payment — {schedule.get('unit_number')} {schedule.get('quarter')}",
        "reference": body.get("reference"),
        "payment_method": body.get("payment_method"),
        "is_reconciled": False,
        "is_reversed": False,
        "requires_approval": False,
        "bank_date": payment_date_str,
        "is_advance_payment": is_advance,
        "advance_days": advance_days,
        "created_by": str(current_user.get("_id", "")),
        "created_at": now_iso_str,
    }

    await db.trust_transactions_v2.insert_one(tx_doc)

    new_paid = schedule.get("paid_cents", 0) + amount_cents
    total = schedule.get("total_cents", 0)
    outstanding = max(0, total - new_paid)
    new_status = "paid" if outstanding == 0 else ("partial" if new_paid > 0 else "pending")

    schedule_update = {
        "paid_cents": new_paid,
        "outstanding_cents": outstanding,
        "status": new_status,
        "paid_at": now_iso_str if outstanding == 0 else None,
        "updated_at": now_iso_str,
    }
    if is_advance:
        schedule_update["is_advance_payment"] = True
        schedule_update["advance_payment_date"] = payment_date_str
        schedule_update["advance_days"] = advance_days
        schedule_update["advance_interest_earned_cents"] = advance_interest_cents
        schedule_update["advance_interest_posted"] = False

    await db.trust_levy_schedules_v2.update_one(
        {"_id": ObjectId(schedule_id)},
        {"$set": schedule_update},
    )

    if account_id:
        await db.trust_accounts_v2.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": {"updated_at": now_iso_str}},
        )

    await _write_trust_audit(db, TrustAuditLogCreate(
        building_id=jwt_bid,
        action="levy_marked_paid",
        entity_type="TrustLevySchedule",
        entity_id=schedule_id,
        performed_by=str(current_user.get("_id", "")),
        performer_role=current_user.get("role", ""),
        performer_email=current_user.get("email", ""),
        metadata={
            "amount_cents": amount_cents,
            "new_status": new_status,
            "is_advance_payment": is_advance,
            "advance_days": advance_days,
            "advance_interest_earned_cents": advance_interest_cents,
        },
    ))

    return JSONResponse(content={
        "success": True,
        "new_status": new_status,
        "outstanding_cents": outstanding,
        "is_advance_payment": is_advance,
        "advance_days": advance_days,
        "advance_interest_earned_cents": advance_interest_cents,
        "advance_interest_earned_display": format_aud(advance_interest_cents) if is_advance else None,
    })


# ─── Advance Payments Report ──────────────────────────────────────────────────

@router.get("/levies/advance-payments")
async def list_advance_payments(
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        quarter: Optional[str] = Query(None, description="Filter by quarter e.g. 2026-Q1"),
        financial_year: Optional[str] = Query(None, description="Filter by financial year e.g. 2026-27"),
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """List advance levy payments and the interest they earned for the Owners Corporation.

    An advance payment is one where the owner paid before the levy due date.
    The building's trust account earns bank interest on that advance amount from
    payment_date to due_date, proportional to the account's interest_rate_pa.
    """
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    query: Dict[str, Any] = {
        "building_id": jwt_bid,
        "is_advance_payment": True,
    }
    if quarter:
        query["quarter"] = quarter
    if financial_year:
        query["financial_year"] = financial_year

    total = await db.trust_levy_schedules_v2.count_documents(query)
    skip = (page - 1) * limit
    schedules = await db.trust_levy_schedules_v2.find(query).skip(skip).limit(limit).to_list(length=limit)

    items = []
    total_interest = 0
    unposted_interest = 0

    for s in schedules:
        interest = s.get("advance_interest_earned_cents", 0)
        total_interest += interest
        if not s.get("advance_interest_posted", False):
            unposted_interest += interest
        items.append({
            "levy_schedule_id": str(s["_id"]),
            "unit_number": s.get("unit_number", ""),
            "lot_number": s.get("lot_number", 0),
            "quarter": s.get("quarter", ""),
            "financial_year": s.get("financial_year", ""),
            "due_date": s.get("due_date", "")[:10],
            "paid_date": (s.get("advance_payment_date") or s.get("paid_at") or "")[:10],
            "advance_days": s.get("advance_days", 0),
            "total_paid_cents": s.get("paid_cents", 0),
            "total_paid_display": format_aud(s.get("paid_cents", 0)),
            "interest_earned_cents": interest,
            "interest_earned_display": format_aud(interest),
            "interest_posted": s.get("advance_interest_posted", False),
            "interest_posted_at": s.get("advance_interest_posted_at"),
        })

    return JSONResponse(content={"success": True, "data": {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "total_interest_earned_cents": total_interest,
        "total_interest_earned_display": format_aud(total_interest),
        "unposted_interest_cents": unposted_interest,
        "unposted_interest_display": format_aud(unposted_interest),
    }})


@router.get("/accounts/interest-history")
async def list_interest_history(
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """List all interest income postings for the building, newest first."""
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    query = {"building_id": jwt_bid}
    total = await db.trust_interest_postings.count_documents(query)
    skip = (page - 1) * limit
    postings = await db.trust_interest_postings.find(query).sort("posted_at", -1).skip(skip).limit(limit).to_list(
        length=limit)

    # Enrich with account name
    account_cache: Dict[str, str] = {}
    results = []
    for p in postings:
        acc_id = str(p.get("account_id", ""))
        if acc_id not in account_cache:
            acc_doc = await db.trust_accounts_v2.find_one({"_id": p["account_id"]})
            account_cache[acc_id] = acc_doc.get("account_name", "Unknown") if acc_doc else "Unknown"
        results.append({
            "id": str(p["_id"]),
            "building_id": str(p.get("building_id", "")),
            "account_id": acc_id,
            "account_name": account_cache[acc_id],
            "period_start": p.get("period_start"),
            "period_end": p.get("period_end"),
            "total_interest_cents": p.get("total_interest_cents", 0),
            "total_interest_display": format_aud(p.get("total_interest_cents", 0)),
            "admin_interest_cents": p.get("admin_interest_cents", 0),
            "admin_interest_display": format_aud(p.get("admin_interest_cents", 0)),
            "sinking_interest_cents": p.get("sinking_interest_cents", 0),
            "sinking_interest_display": format_aud(p.get("sinking_interest_cents", 0)),
            "reference": p.get("reference"),
            "notes": p.get("notes"),
            "posted_by": p.get("posted_by"),
            "posted_at": p.get("posted_at"),
            "is_auto_posted": p.get("is_auto_posted", False),
        })

    return JSONResponse(content={"success": True, "data": results, "total": total})


# ─── DEFT Webhook ─────────────────────────────────────────────────────────────

DEFT_MOCK_SECRET_ENV = "DEFT_WEBHOOK_SECRET"


async def deft_mock_mode_enabled(building_id: str | None = None) -> bool:
    """Whether DEFT runs against the mock implementation rather than a real bank.

    A deliberate product posture, not a misconfiguration: no environment —
    production included — is connected to a live financial institution yet, and
    `docs/architecture/transactions_accounting.md` RULE 2 makes "no real external API
    keys required" an explicit design rule. `APP_ENV` does not override it.

    Resolved PER BUILDING through the `financial_services_mock` toggle, so one
    building can be taken live without taking every building live. The process-wide
    `MOCK_EXTERNAL_SERVICES` env var still forces mock for everyone when set — it can
    only force mock ON, never off.

    What mock mode does NOT mean is "skip authenticating the caller". Mocking is about
    OUTBOUND integration — which implementation we call. The webhook is INBOUND: there
    is no provider to mock, only a request to authenticate, and it writes to the real
    `trust_transactions_v2` / `trust_levy_schedules_v2` collections either way. So mock
    mode changes who is expected to sign — the local emulator instead of the bank —
    never whether a signature is required. Drive mock payments through
    POST /trust/v2/deft/simulate, which is authenticated as a user.
    """
    from services.financial_mock_mode import financial_services_mocked

    return await financial_services_mocked(building_id)


def sign_deft_payload(raw_body: bytes, secret: str | None = None) -> str:
    """Produce the X-DEFT-Signature a caller must send. Used by the simulator."""
    key = secret if secret is not None else os.environ.get(DEFT_MOCK_SECRET_ENV, "")
    return hmac.new(key.encode(), raw_body, hashlib.sha256).hexdigest()


def verify_deft_webhook_signature(raw_body: bytes, signature: str | None) -> None:
    """Authenticate a DEFT payment notification, or raise.

    The endpoint carries no user session — a third party posts to it — so the HMAC
    IS the authentication, in mock mode as much as in real mode. Everything past it
    writes money: a matched notification creates a `trust_transactions_v2` receipt
    and marks a `trust_levy_schedules_v2` row paid. The CRN it matches on is not a
    secret: seeds/trust_accounting.py::_generate_deft_crn derives it deterministically
    as biller_code-lot_padded-quarter_code-luhn_check_digit, so it is COMPUTABLE from a
    lot number and a quarter, and it is displayed in-app on the levy schedule table.
    (An earlier revision of this comment claimed CRNs are "printed on every levy
    notice". That is the intent recorded in docs/features/
    strataos_bank_integration_and_trust_accounting_cutover_strategy.md as the typical
    strata pattern, but no notice or email template in this repo renders one today, so
    it was not a property of this system. Derivability is checkable and sufficient.)

    Fails CLOSED, matching `stripe_webhook` in server.py and
    `services/email_intake_service.verify_inbound_email_signature`. The previous
    `if webhook_secret:` skipped verification entirely when the variable was unset,
    and the whole check sat behind a mock flag that defaulted to true — so the
    endpoint was open in every deployment that had not set either variable.

    In mock mode the secret is simply a locally generated one rather than a
    bank-issued one; `sign_deft_payload` produces matching signatures for it.
    """
    webhook_secret = os.environ.get(DEFT_MOCK_SECRET_ENV, "")
    if not webhook_secret:
        logger.critical(
            "[SECURITY] %s is not configured; rejecting DEFT notification. Set it to a "
            "locally generated value (openssl rand -hex 32) even in mock mode — mock "
            "means the payments are simulated, not that the endpoint is open.",
            DEFT_MOCK_SECRET_ENV,
        )
        raise HTTPException(status_code=503, detail="DEFT webhook secret is not configured.")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing DEFT webhook signature.")
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        logger.warning("DEFT webhook HMAC validation failed")
        raise HTTPException(status_code=401, detail="Invalid DEFT webhook signature.")


async def _process_deft_notification(payload: dict, *, origin: str) -> JSONResponse:
    """Ingest one DEFT payment notification and post it to the trust ledger.

    Shared by the signed public webhook and the authenticated simulator so a mock
    payment exercises exactly the same matching, dedup and posting path as a real
    one — the point of testing against a mock at all. `origin` records which door
    the notification came through.
    """
    try:

        db = _get_db()

        deft_transaction_id = str(payload.get("transaction_id", payload.get("deft_transaction_id", "")))
        deft_crn = str(payload.get("crn", payload.get("deft_crn", "")))
        amount_cents = int(payload.get("amount_cents", 0))
        payment_date = payload.get("payment_date")

        # Step 1: Store raw payload immediately, before any processing
        notif_doc = {
            "raw_payload": payload,
            "deft_transaction_id": deft_transaction_id,
            "deft_crn": deft_crn or None,
            "amount_cents": amount_cents,
            "payment_date": payment_date,
            "building_id": None,
            "status": "received",
            "origin": origin,
            "received_at": _now_iso(),
        }
        notif_result = await db.deft_notifications.insert_one(notif_doc)
        notif_id = notif_result.inserted_id

        # Step 2: Deduplication check
        existing = await db.deft_notifications.count_documents({
            "deft_transaction_id": deft_transaction_id,
            "_id": {"$ne": notif_id},
        })
        if existing > 0:
            await db.deft_notifications.update_one(
                {"_id": notif_id},
                {"$set": {"status": "duplicate"}},
            )
            return JSONResponse(content={"received": True})

        # Step 3: Look up levy schedule by CRN
        schedule = None
        if deft_crn:
            schedule = await db.trust_levy_schedules_v2.find_one({"deft_crn": deft_crn})

        if not schedule:
            await db.deft_notifications.update_one(
                {"_id": notif_id},
                {"$set": {"status": "unmatched", "processed_at": _now_iso()}},
            )
            logger.warning("DEFT webhook: CRN %s not matched to any levy schedule", deft_crn)
            return JSONResponse(content={"received": True})

        building_id = str(schedule.get("building_id", ""))

        await db.deft_notifications.update_one(
            {"_id": notif_id},
            {"$set": {"building_id": building_id, "status": "processing"}},
        )

        # Step 4: Create TrustTransaction for the payment
        account = await db.trust_accounts_v2.find_one({
            "building_id": building_id,
            "account_type": "admin_fund",
        })

        tx_doc = {
            "building_id": building_id,
            "trust_account_id": account["_id"] if account else None,
            "trust_levy_schedule_id": schedule["_id"],
            "unit_id": schedule.get("unit_id"),
            "unit_number": schedule.get("unit_number"),
            "type": "receipt",
            "amount_cents": amount_cents,
            "gst_cents": 0,
            "description": f"DEFT payment — {schedule.get('unit_number')} {schedule.get('quarter')}",
            "deft_transaction_id": deft_transaction_id,
            "deft_crn": deft_crn,
            "is_reconciled": False,
            "is_reversed": False,
            "requires_approval": False,
            "created_at": _now_iso(),
        }
        tx_result = await db.trust_transactions_v2.insert_one(tx_doc)

        new_paid = schedule.get("paid_cents", 0) + amount_cents
        total = schedule.get("total_cents", 0)
        outstanding = max(0, total - new_paid)
        new_status = "paid" if outstanding == 0 else ("partial" if new_paid > 0 else "pending")

        await db.trust_levy_schedules_v2.update_one(
            {"_id": schedule["_id"]},
            {"$set": {
                "paid_cents": new_paid,
                "outstanding_cents": outstanding,
                "status": new_status,
                "paid_at": _now_iso() if outstanding == 0 else None,
                "updated_at": _now_iso(),
            }},
        )

        if account:
            await db.trust_accounts_v2.update_one(
                {"_id": account["_id"]},
                {"$set": {"updated_at": _now_iso()}},
            )

        await db.deft_notifications.update_one(
            {"_id": notif_id},
            {"$set": {
                "status": "matched",
                "matched_levy_schedule_id": schedule["_id"],
                "matched_transaction_id": tx_result.inserted_id,
                "processed_at": _now_iso(),
            }},
        )

        await _write_trust_audit(db, TrustAuditLogCreate(
            building_id=building_id,
            action="transaction_created",
            entity_type="TrustTransaction",
            entity_id=str(tx_result.inserted_id),
            performed_by="deft_webhook",
            performer_role="system",
            performer_email="deft@system",
            metadata={"deft_transaction_id": deft_transaction_id, "deft_crn": deft_crn},
        ))

    except Exception as exc:
        logger.error("DEFT webhook error: %s", exc, exc_info=True)
        # Attempt to record the error for manual review
        try:
            db = _get_db()
            await db.deft_notifications.insert_one({
                "raw_payload": {},
                "deft_transaction_id": "UNKNOWN",
                "status": "error",
                "origin": origin,
                "error_message": str(exc),
                "received_at": _now_iso(),
            })
        except Exception as exc2:
            logger.warning("trust_phase1: failed to store DEFT error notification: %s", exc2)

    # ALWAYS return 200 to prevent DEFT retries
    return JSONResponse(content={"received": True})


@router.post("/deft/webhook", response_model=DeftWebhookResponse)
async def deft_webhook(request: Request):
    """DEFT payment notification receiver (signed, no user session).

    Returns { received: true } with 200 for anything that authenticates — DEFT
    retries on a non-2xx, and an unmatched CRN is a business outcome, not a delivery
    failure. Authentication is deliberately OUTSIDE that contract and outside the
    exception-swallowing block below: an unsigned call gets 401/503, not a 200.
    """
    raw_body = await request.body()
    verify_deft_webhook_signature(raw_body, request.headers.get("x-deft-signature"))

    import json
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed DEFT payload.")
    return await _process_deft_notification(payload, origin="deft_webhook")


@router.post("/deft/simulate", response_model=DeftWebhookResponse)
async def simulate_deft_payment(
        payload: DeftSimulatedPayment,
        current_user: dict = Depends(require_feature("trust_accounting")),
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Post a SIMULATED DEFT payment through the real ingestion path.

    This is how DEFT is exercised while `MOCK_EXTERNAL_SERVICES=true` and no real
    financial institution is connected — the deliberate posture described in
    `deft_mock_mode_enabled`. It runs the same `_process_deft_notification` as the
    signed webhook, so matching, deduplication and ledger posting are genuinely
    under test rather than stubbed.

    It replaces the previous arrangement, where mock mode was implemented by having
    the public webhook skip its signature check. That did not simulate anything: it
    left an unauthenticated endpoint writing real trust receipts, reachable by anyone
    able to produce a CRN — and a CRN is derivable from a lot number and quarter (see
    verify_deft_webhook_signature). Simulation belongs behind a user session; only the
    transport is mock, and the ledger writes are real.

    Rows are tagged `origin="deft_simulator"` so simulated payments stay
    distinguishable from bank-originated ones in `deft_notifications`.
    """
    _require_trust_manage(current_user)
    building_id = await _get_building_id_from_jwt(current_user, None)
    if not await deft_mock_mode_enabled(building_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "DEFT is not in mock mode for this building. Simulated payments are "
                "refused once it transacts with a live provider; send a signed "
                "notification instead."
            ),
        )
    logger.info(
        "DEFT payment simulated by user=%s crn=%s amount_cents=%s",
        current_user.get("id"), payload.crn, payload.amount_cents,
    )
    return await _process_deft_notification(
        payload.model_dump(mode="json"), origin="deft_simulator"
    )


# ─── Financial Summary ────────────────────────────────────────────────────────
# @featuretrace:financial-metrics-canonical — trust-account financial summary card.
# Layer: router
# Data flow: TrustAccountingPage.tsx (frontend) → GET /trust/financial-summary →
#            trust_levy_schedules_v2/trust_transactions_v2 (Mongo) →
#            domain/finance/formulas/collection.py::quarterly_collection_rate() for
#            collection_rate (building-scoped).
# Related: backend/routers/external_api.py (get_oc_summary)
#          backend/services/bi_service.py
# Collection: trust_levy_schedules_v2, trust_transactions_v2, trust_accounts_v2
# Tests: tests/backend/test_domain_finance_formulas.py

@router.get("/financial-summary")
async def get_financial_summary(
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """Aggregated financial summary for a building."""
    _require_trust_view(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)

    db = _get_db()
    building = await db.buildings.find_one({"id": jwt_bid})
    building_name = building.get("name", "") if building else ""

    bid_oid = jwt_bid

    accounts = await db.trust_accounts_v2.find({"building_id": bid_oid}).to_list(10)

    admin_acc = next((a for a in accounts if a.get("account_type") == "admin_fund"), None)
    sinking_acc = next((a for a in accounts if a.get("account_type") == "sinking_fund"), None)

    async def acc_computed_balance(acc) -> int:
        """Compute balance = opening + receipts/interest - disbursements/charges."""
        if not acc:
            return 0
        opening = acc.get("opening_balance_cents", 0)
        pipeline = [
            {"$match": {"building_id": bid_oid, "trust_account_id": acc["_id"], "is_reversed": False}},
            {"$group": {
                "_id": None,
                "receipts": {"$sum": {"$cond": [{"$in": ["$type", ["receipt", "interest"]]}, "$amount_cents", 0]}},
                "disbursements": {"$sum": {
                    "$cond": [{"$in": ["$type", ["disbursement", "bank_charge", "reversal"]]}, "$amount_cents", 0]}},
            }},
        ]
        agg = await db.trust_transactions_v2.aggregate(pipeline).to_list(1)
        if agg:
            return opening + agg[0].get("receipts", 0) - agg[0].get("disbursements", 0)
        return opening

    # Aggregate transactions per account type
    admin_tx: Dict[str, int] = {}
    if admin_acc:
        pipeline = [
            {"$match": {"building_id": bid_oid, "trust_account_id": admin_acc["_id"]}},
            {"$group": {"_id": "$type", "total": {"$sum": "$amount_cents"}}},
        ]
        async for doc in db.trust_transactions_v2.aggregate(pipeline):
            admin_tx[doc["_id"]] = doc["total"]

    sinking_tx: Dict[str, int] = {}
    if sinking_acc:
        s_pipeline = [
            {"$match": {"building_id": bid_oid, "trust_account_id": sinking_acc["_id"]}},
            {"$group": {"_id": "$type", "total": {"$sum": "$amount_cents"}}},
        ]
        async for doc in db.trust_transactions_v2.aggregate(s_pipeline):
            sinking_tx[doc["_id"]] = doc["total"]

    levy_pipeline = [
        {"$match": {"building_id": bid_oid}},
        {"$group": {
            "_id": None,
            "total_raised": {"$sum": "$total_cents"},
            "total_received": {"$sum": "$paid_cents"},
            "overdue_count": {"$sum": {"$cond": [{"$eq": ["$status", "overdue"]}, 1, 0]}},
            "overdue_cents": {"$sum": {"$cond": [{"$eq": ["$status", "overdue"]}, "$outstanding_cents", 0]}},
            "interest_accrued": {"$sum": "$interest_accrued_cents"},
        }},
    ]

    levy_agg: Dict[str, Any] = {}
    async for doc in db.trust_levy_schedules_v2.aggregate(levy_pipeline):
        levy_agg = doc

    raised = levy_agg.get("total_raised", 0)
    received = levy_agg.get("total_received", 0)
    outstanding = raised - received
    # GAP-FIN-016 Phase 2b: shared with bi_service.py/external_api.py — see
    # domain/finance/formulas/collection.py::quarterly_collection_rate() docstring.
    collection_rate = float(quarterly_collection_rate(
        paid_cents=received, levied_cents=raised, digits=1, zero_denominator_value=Decimal("0.0"),
    ))

    drb_pipeline = [
        {"$match": {"building_id": bid_oid, "status": {"$in": ["overdue", "partial"]}}},
        {"$group": {"_id": "$drb_stage", "count": {"$sum": 1}}},
    ]
    drb_counts: Dict[str, int] = {"none": 0, "monitor": 0, "contacted": 0, "dca_eligible": 0, "dca_referred": 0}
    async for doc in db.trust_levy_schedules_v2.aggregate(drb_pipeline):
        stage = doc.get("_id", "none") or "none"
        if stage in drb_counts:
            drb_counts[stage] = doc.get("count", 0)

    admin_balance = await acc_computed_balance(admin_acc)
    sinking_balance = await acc_computed_balance(sinking_acc)

    return JSONResponse(content={
        "success": True,
        "data": {
            "building_id": jwt_bid,
            "building_name": building_name,
            "period": {"from": from_date, "to": to_date},
            "admin_fund": {
                "closing_balance": {"cents": admin_balance, "display": format_aud(admin_balance)},
                "receipts": {"cents": admin_tx.get("receipt", 0), "display": format_aud(admin_tx.get("receipt", 0))},
                "disbursements": {"cents": admin_tx.get("disbursement", 0),
                                  "display": format_aud(admin_tx.get("disbursement", 0))},
                "bank_charges": {"cents": admin_tx.get("bank_charge", 0),
                                 "display": format_aud(admin_tx.get("bank_charge", 0))},
            },
            "sinking_fund": {
                "closing_balance": {"cents": sinking_balance, "display": format_aud(sinking_balance)},
                "receipts": {"cents": sinking_tx.get("receipt", 0),
                             "display": format_aud(sinking_tx.get("receipt", 0))},
                "disbursements": {"cents": sinking_tx.get("disbursement", 0),
                                  "display": format_aud(sinking_tx.get("disbursement", 0))},
                "bank_charges": {"cents": sinking_tx.get("bank_charge", 0),
                                 "display": format_aud(sinking_tx.get("bank_charge", 0))},
            },
            "levies": {
                "raised": {"cents": raised, "display": format_aud(raised)},
                "received": {"cents": received, "display": format_aud(received)},
                "outstanding": {"cents": outstanding, "display": format_aud(outstanding)},
                "collection_rate_percent": collection_rate,
                "overdue_count": levy_agg.get("overdue_count", 0),
                "overdue": {"cents": levy_agg.get("overdue_cents", 0),
                            "display": format_aud(levy_agg.get("overdue_cents", 0))},
            },
            "arrears": {
                "units_overdue": levy_agg.get("overdue_count", 0),
                "total_outstanding": {"cents": outstanding, "display": format_aud(outstanding)},
                "interest_accrued": {"cents": levy_agg.get("interest_accrued", 0),
                                     "display": format_aud(levy_agg.get("interest_accrued", 0))},
                "drb_stage_counts": drb_counts,
            },
        }
    })


# ─── Arrears Escalation ───────────────────────────────────────────────────────

@router.post("/arrears/escalate", response_model=ArrearsEscalationResponse)
async def escalate_arrears(
        body: dict,
        current_user: dict = Depends(require_feature("trust_accounting")),
        x_building_id: Optional[str] = Header(None, alias="X-Building-ID"),
):
    """Trigger or preview arrears escalation for a building."""
    _require_trust_manage(current_user)
    jwt_bid = await _get_building_id_from_jwt(current_user, x_building_id)
    dry_run = body.get("dry_run", False)

    # super_admin may target a specific building_id via body
    if effective_role(current_user) == "super_admin" and body.get("building_id"):
        target_bid = body.get("building_id")
    else:
        target_bid = jwt_bid

    db = _get_db()
    now_iso = _now_iso()

    building = await db.buildings.find_one({"id": target_bid})
    if not building:
        raise HTTPException(status_code=404, detail="Building not found.")

    tc = building.get("trust_config", {})
    grace = tc.get("grace_period_days", 14)
    reminder_days = tc.get("arrears_reminder_days", 14)
    formal_days = tc.get("arrears_formal_notice_days", 21)
    interest_days = tc.get("arrears_interest_charge_days", 30)
    legal_days = tc.get("arrears_legal_flag_days", 60)
    annual_rate = tc.get("arrears_interest_rate", 0.10)

    schedules = await db.trust_levy_schedules_v2.find({
        "building_id": target_bid,
        "status": {"$in": ["pending", "overdue", "partial"]},
    }).to_list(500)

    today = date.today()

    reminders_sent = 0
    formal_notices_sent = 0
    interest_created = 0
    legal_flagged = 0

    for schedule in schedules:
        due_str = schedule.get("due_date", "")
        if not due_str:
            continue

        try:
            due = date.fromisoformat(due_str[:10])
        except Exception:
            logger.warning(
                "Arrears escalation: invalid due_date '%s' on schedule %s — skipping",
                due_str, str(schedule.get("_id", "")),
            )
            continue

        days_past = (today - due).days - grace
        if days_past <= 0:
            continue

        # Determine escalation stage based on days overdue
        stage = None
        if days_past >= legal_days:
            stage = "legal"
        elif days_past >= interest_days:
            stage = "interest_charge"
        elif days_past >= formal_days:
            stage = "formal_notice"
        elif days_past >= reminder_days:
            stage = "reminder"

        if not stage:
            continue

        # Idempotency: skip if already escalated to this stage
        last_audit = await db.trust_audit_logs.find_one(
            {
                "entity_id": str(schedule["_id"]),
                "action": "arrears_escalated",
            },
            sort=[("timestamp", -1)],
        )
        if last_audit and last_audit.get("metadata", {}).get("stage") == stage:
            continue

        if not dry_run:
            if stage == "legal":
                await db.trust_levy_schedules_v2.update_one(
                    {"_id": schedule["_id"]},
                    {"$set": {"status": "legal", "drb_stage": "dca_eligible", "updated_at": now_iso}},
                )
                legal_flagged += 1
            elif stage == "interest_charge":
                outstanding = schedule.get("outstanding_cents", 0)
                overdue_since = schedule.get("overdue_since")
                if outstanding > 0 and overdue_since:
                    try:
                        from_d = datetime.fromisoformat(overdue_since[:10])
                        to_d = datetime.now(timezone.utc)
                        days = (to_d.date() - from_d.date()).days
                        interest = round(outstanding * annual_rate * days / 365)
                        if interest > 0:
                            await db.trust_levy_schedules_v2.update_one(
                                {"_id": schedule["_id"]},
                                {"$inc": {"interest_accrued_cents": interest}, "$set": {"updated_at": now_iso}},
                            )
                            interest_created += 1
                    except Exception as e:
                        logger.warning("Interest calculation error: %s", e)
            elif stage == "formal_notice":
                await db.trust_levy_schedules_v2.update_one(
                    {"_id": schedule["_id"]},
                    {"$set": {"drb_stage": "monitor", "updated_at": now_iso}},
                )
                formal_notices_sent += 1
            elif stage == "reminder":
                reminders_sent += 1

            await _write_trust_audit(db, TrustAuditLogCreate(
                building_id=target_bid,
                action="arrears_escalated",
                entity_type="TrustLevySchedule",
                entity_id=str(schedule["_id"]),
                performed_by=str(current_user.get("_id", "")),
                performer_role=current_user.get("role", ""),
                performer_email=current_user.get("email", ""),
                metadata={"stage": stage, "days_past": days_past},
            ))

    return JSONResponse(content={
        "success": True,
        "data": {
            "buildings_processed": 1,
            "levies_scanned": len(schedules),
            "reminders_sent": reminders_sent,
            "formal_notices_sent": formal_notices_sent,
            "interest_transactions_created": interest_created,
            "legal_flagged": legal_flagged,
            "dry_run": dry_run,
            "ran_at": now_iso,
        }
    })


# ─── BSB Lookup ──────────────────────────────────────────────────────────────

@router.get("/bsb/{bsb}")
async def lookup_bsb(
        bsb: str,
        current_user: dict = Depends(require_feature("trust_accounting")),
):
    """
    Look up an Australian BSB number via findbsb.com.au.
    Returns bank name, branch, suburb, state, and postcode.
    Authenticated endpoint — any logged-in user may look up a BSB.
    """
    # Normalise: accept "182266" or "182-266"
    clean = bsb.replace("-", "").replace(" ", "")
    if len(clean) != 6 or not clean.isdigit():
        raise HTTPException(status_code=422, detail="BSB must be 6 digits, e.g. 182-266.")
    formatted = f"{clean[:3]}-{clean[3:]}"

    try:
        async with httpx.AsyncClient(timeout=8.0, headers={
            "User-Agent": "StrataApp/1.0 (AU)",
            "Accept": "application/json",
        }) as client:
            resp = await client.get(f"https://findbsb.com.au/api/bsb/{formatted}")
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"BSB {formatted} not found.")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="BSB lookup service unavailable.")
        data = resp.json()
        return JSONResponse(content={
            "success": True,
            "data": {
                "bsb": data.get("bsb", formatted),
                "bank": data.get("bank", ""),
                "mnemonic": data.get("mnemonic", ""),
                "branch": data.get("branch", ""),
                "address": data.get("address", ""),
                "suburb": data.get("suburb", ""),
                "state": data.get("state", ""),
                "postcode": data.get("postcode", ""),
                "closed": data.get("closed", False),
            },
        })
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="BSB lookup timed out.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"BSB lookup error for {formatted}: {exc}")
        raise HTTPException(status_code=502, detail="BSB lookup service unavailable.")
