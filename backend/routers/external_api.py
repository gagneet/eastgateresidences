"""
External API Router — /api/v1/

Provides a versioned, API-key-authenticated interface for external systems
(banking connectors, strata management platforms, service providers, etc.)
to query and push building data to the multi-tenant strata management platform.

Authentication
--------------
All endpoints require an ``X-API-Key`` header.  Keys are created by
administrators via the internal JWT-authenticated management endpoints
``POST /api/v1/api-keys`` and ``GET /api/v1/api-keys``.

Scope model
-----------
Each API key carries a ``scopes`` list.  The ``admin`` scope grants
unrestricted access.  Fine-grained scopes:

  read:building      — Building info, units, defects
  read:finance       — Financial summaries, budget, transactions
  read:maintenance   — Maintenance requests / defects
  write:maintenance  — Report new defects / maintenance requests
  read:work-orders   — Work orders
  write:work-orders  — Update work-order status
  write:service      — Service provider: submit quotes / invoices
  manage:webhooks    — Register & delete outbound webhooks
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncio
import httpx
import logging
from fastapi import APIRouter, HTTPException, Depends, Query, status, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Optional

from database import db
from db_postgres.repos import config_repo
from domain.finance.formulas.collection import quarterly_collection_rate
from models.external_api_models import (
    APIKeyCreate,
    APIKeyResponse,
    APIKeyCreateResponse,
    WebhookCreate,
    WebhookResponse,
    BuildingInfoResponse,
    UnitDetail,
    UnitLevyBalance,
    DefectCreate,
    OCPerformanceSummary,
    OCLevySummary,
    FinanceSummaryResponse,
    MaintenanceRequestSummary,
    MaintenanceStatusUpdate,
    WorkOrderSummary,
    QuoteSubmit,
    QuoteResponse,
    InvoiceSubmit,
    InvoiceStatusResponse,
)
from models.user import UserRole
from services.settings_service import get_general_settings_or_default
from services.cutover_config_service import (
    EXTERNAL_API_FINANCE_PG_ENABLED,
    FINANCIAL_PG_READS_ENABLED,
    are_cutover_features_enabled,
)
from services.financial_read_service import FinancialReadService
from utils.api_key_auth import (
    generate_api_key,
    get_api_key_auth,
    require_scope,
    resolve_building_id_for_key,
    VALID_SCOPES,
)
from utils.auth import get_current_user
from utils.finance_helpers import (
    get_latest_levy_year,
    get_levy_fund_data,
    get_levy_proposed_amounts,
    compute_combined_fund_totals,
    legacy_fund_type,
)
from utils.helpers import create_audit_log


async def _check_external_api_enabled() -> None:
    """Generated function header.

    Function: _check_external_api_enabled
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    enabled = await config_repo.get_global_feature_toggle_state(
        "external_api",
        default=True,
    )
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External API is currently disabled by administrator",
        )


router = APIRouter(
    prefix="/v1",
    tags=["External API v1"],
    dependencies=[Depends(_check_external_api_enabled)],
)
logger = logging.getLogger(__name__)
_financial_read_service = FinancialReadService()

# Prefix used to identify API-key-originated submissions in audit/notes fields
_API_KEY_SUBMITTER_PREFIX = "api_key:"

_ADMIN_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER,
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _pagination_meta(total: int, page: int, page_size: int) -> dict:
    """Generated function header.

    Function: _pagination_meta
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


async def _dispatch_webhook_event(event_type: str, payload: dict) -> None:
    """Fire all active webhooks subscribed to ``event_type`` asynchronously."""
    try:
        webhooks = await db.api_webhooks.find(
            {"is_active": True, "events": event_type}, {"_id": 0}
        ).to_list(50)

        if not webhooks:
            return

        async with httpx.AsyncClient(timeout=10) as client:
            for wh in webhooks:
                body = {"event": event_type, "timestamp": _now(), "data": payload}
                headers = {"Content-Type": "application/json"}

                if wh.get("secret"):
                    body_bytes = json.dumps(body, default=str).encode()
                    sig = hmac.new(
                        wh["secret"].encode(),
                        body_bytes,
                        hashlib.sha256,
                    ).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={sig}"

                try:
                    await client.post(wh["url"], json=body, headers=headers)
                except Exception as exc:
                    logger.warning("Webhook delivery failed for %s: %s", wh["url"], exc)
    except Exception as exc:
        logger.error("_dispatch_webhook_event error: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# API Key Management  (JWT-authenticated — admin only)
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/api-keys",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description="Admin-only. Creates a new external API key. The raw key is returned once.",
)
async def create_api_key(
        data: APIKeyCreate,
        current_user: dict = Depends(get_current_user),
):
    """Create a new API key (admin-only, JWT authenticated)."""
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required to manage API keys")

    # Validate scopes
    invalid = set(data.scopes) - VALID_SCOPES
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid scopes: {', '.join(sorted(invalid))}. Valid: {', '.join(sorted(VALID_SCOPES))}",
        )

    raw_key, key_hash = generate_api_key()
    key_id = str(uuid.uuid4())
    now = _now()
    building_id = await resolve_building_id_for_key()

    doc = {
        "id": key_id,
        "name": data.name,
        "key_hash": key_hash,
        "scopes": data.scopes,
        "is_active": True,
        "description": data.description,
        "expires_at": data.expires_at,
        "created_at": now,
        "updated_at": now,
        "last_used_at": None,
        "created_by": current_user["id"],
        "building_id": building_id,
    }
    await db.api_keys.insert_one(doc)
    doc.pop("_id", None)

    asyncio.create_task(
        create_audit_log(
            action="created",
            resource_type="api_key",
            resource_id=key_id,
            user_id=current_user["id"],
            user_name=current_user.get("full_name", ""),
            details={"name": data.name, "scopes": data.scopes},
        )
    )

    return {**doc, "raw_key": raw_key}


@router.get(
    "/api-keys",
    response_model=List[APIKeyResponse],
    summary="List API keys",
    description="Admin-only. Lists all API keys (raw keys are never returned).",
)
async def list_api_keys(
        include_inactive: bool = Query(False),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_api_keys
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")

    query: dict = {}
    if not include_inactive:
        query["is_active"] = True

    keys = await db.api_keys.find(query, {"_id": 0, "key_hash": 0}).to_list(200)
    return keys


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
)
async def revoke_api_key(
        key_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: revoke_api_key
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.api_keys.update_one(
        {"id": key_id},
        {"$set": {"is_active": False, "updated_at": _now()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="API key not found")

    asyncio.create_task(
        create_audit_log(
            action="revoked",
            resource_type="api_key",
            resource_id=key_id,
            user_id=current_user["id"],
            user_name=current_user.get("full_name", ""),
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# Webhook Management  (API-key authenticated)
# ──────────────────────────────────────────────────────────────────────────────

SUPPORTED_WEBHOOK_EVENTS = [
    "maintenance.created",
    "maintenance.updated",
    "maintenance.resolved",
    "work_order.created",
    "work_order.updated",
    "work_order.completed",
    "levy.paid",
    "levy.overdue",
    "quote.submitted",
    "invoice.submitted",
    "invoice.approved",
    "invoice.paid",
]


@router.get(
    "/webhooks",
    response_model=List[WebhookResponse],
    summary="List registered webhooks",
)
async def list_webhooks(
        key_doc: dict = Depends(require_scope("manage:webhooks")),
):
    """Generated function header.

    Function: list_webhooks
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    webhooks = await db.api_webhooks.find(
        {"created_by_key": key_doc["id"]}, {"_id": 0, "secret": 0}
    ).to_list(100)
    return webhooks


@router.post(
    "/webhooks",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a webhook",
)
async def register_webhook(
        data: WebhookCreate,
        key_doc: dict = Depends(require_scope("manage:webhooks")),
):
    # Validate URL scheme
    """Generated function header.

    Function: register_webhook
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not data.url.startswith("https://"):
        raise HTTPException(
            status_code=422,
            detail="Webhook URL must use HTTPS.",
        )

    # Validate event types
    invalid_events = set(data.events) - set(SUPPORTED_WEBHOOK_EVENTS)
    if invalid_events:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported event types: {', '.join(sorted(invalid_events))}. "
                   f"Supported: {', '.join(SUPPORTED_WEBHOOK_EVENTS)}",
        )

    wh_id = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": wh_id,
        "url": data.url,
        "events": data.events,
        "secret": data.secret,
        "is_active": True,
        "description": data.description,
        "created_at": now,
        "updated_at": now,
        "created_by_key": key_doc["id"],
    }
    await db.api_webhooks.insert_one(doc)
    doc.pop("secret", None)
    return doc


@router.delete(
    "/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a webhook",
)
async def delete_webhook(
        webhook_id: str,
        key_doc: dict = Depends(require_scope("manage:webhooks")),
):
    """Generated function header.

    Function: delete_webhook
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await db.api_webhooks.delete_one(
        {"id": webhook_id, "created_by_key": key_doc["id"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Webhook not found or not owned by this key")


@router.get(
    "/webhooks/events",
    summary="List supported webhook event types",
)
async def list_webhook_events(
        key_doc: dict = Depends(get_api_key_auth),
):
    """Generated function header.

    Function: list_webhook_events
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {"events": SUPPORTED_WEBHOOK_EVENTS}


# ──────────────────────────────────────────────────────────────────────────────
# Building & Units  (read:building)
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/building",
    response_model=BuildingInfoResponse,
    summary="Get building information",
)
async def get_building_info(
        key_doc: dict = Depends(require_scope("read:building")),
):
    """Return general building details drawn from system settings."""
    building_id = await resolve_building_id_for_key(key_doc)
    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    units_count = await db.units.count_documents({"building_id": building_id})
    return {
        "building_name": settings.get("building_name", ""),
        "address": settings.get("building_address"),
        "total_units": units_count,
        "strata_plan": settings.get("strata_plan"),
        "building_manager": settings.get("building_manager_name"),
        "contact_email": settings.get("contact_email"),
    }


@router.get(
    "/units",
    summary="List all units",
)
async def list_units(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        is_tenanted: Optional[bool] = Query(None),
        key_doc: dict = Depends(require_scope("read:building")),
):
    """Return a paginated list of units with basic details."""
    building_id = await resolve_building_id_for_key(key_doc)
    query: dict = {"building_id": building_id}
    if is_tenanted is not None:
        query["is_tenanted"] = is_tenanted

    total = await db.units.count_documents(query)
    skip = (page - 1) * page_size
    units_cursor = db.units.find(query, {"_id": 0}).skip(skip).limit(page_size)
    units = await units_cursor.to_list(page_size)

    data = [
        {
            "unit_number": u.get("unit_number", u.get("id", "")),
            "unit_type": u.get("unit_type"),
            "floor": u.get("floor"),
            "is_tenanted": u.get("is_tenanted", False),
            "entitlement": u.get("entitlement"),
            "owner_name": u.get("owner_name"),
        }
        for u in units
    ]

    return {
        "data": data,
        "meta": _pagination_meta(total, page, page_size),
    }


@router.get(
    "/units/{unit_number}",
    response_model=UnitDetail,
    summary="Get unit details",
)
async def get_unit(
        unit_number: str,
        key_doc: dict = Depends(require_scope("read:building")),
):
    """Generated function header.

    Function: get_unit
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    unit = await db.units.find_one(
        {"building_id": building_id, "unit_number": unit_number}, {"_id": 0}
    )
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    return {
        "unit_number": unit.get("unit_number", ""),
        "unit_id": unit.get("id"),
        "unit_type": unit.get("unit_type"),
        "floor": unit.get("floor"),
        "is_tenanted": unit.get("is_tenanted", False),
        "entitlement": unit.get("entitlement"),
        "owner_name": unit.get("owner_name"),
        "bedrooms": unit.get("bedrooms"),
        "bathrooms": unit.get("bathrooms"),
        "car_spaces": unit.get("car_spaces"),
        "levy_balance": unit.get("closing_balance"),  # prefer canonical field; closing_balance_YYYY is legacy
    }


@router.get(
    "/units/{unit_number}/levy-balance",
    response_model=UnitLevyBalance,
    summary="Get unit levy balance",
)
async def get_unit_levy_balance(
        unit_number: str,
        financial_year: Optional[str] = Query(None, description="e.g. 2025-2026; defaults to current year"),
        key_doc: dict = Depends(require_scope("read:finance")),
):
    """Generated function header.

    Function: get_unit_levy_balance
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    if await are_cutover_features_enabled(
            building_id,
            FINANCIAL_PG_READS_ENABLED,
            EXTERNAL_API_FINANCE_PG_ENABLED,
    ):
        pg_balance = await _financial_read_service.get_unit_levy_balance(
            building_id=building_id,
            unit_number=unit_number,
            financial_year=financial_year,
        )
        if not pg_balance:
            raise HTTPException(
                status_code=404,
                detail=f"No levy ledger found for unit {unit_number} in {financial_year or 'the current year'}",
            )
        return pg_balance

    # Resolve current financial year if not supplied. unit_levy_ledger's real Mongo
    # field is `year` (not `financial_year`) — see CLAUDE.md. get_latest_levy_year()
    # also correctly excludes not-yet-started future/synthetic budget years, unlike a
    # raw sort-desc on annual_levies would.
    if not financial_year:
        financial_year = await get_latest_levy_year(building_id) or str(datetime.now(timezone.utc).year)

    ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "year": financial_year}, {"_id": 0}
    )
    if not ledger:
        raise HTTPException(
            status_code=404,
            detail=f"No levy ledger found for unit {unit_number} in {financial_year}",
        )

    opening = ledger.get("total_opening", 0.0)
    levied = ledger.get("total_levied", 0.0)
    paid = ledger.get("total_paid", 0.0)
    # net_balance is the authoritative closing balance (never the banned
    # total_closing field) — GAP-FIN-040.
    closing = ledger.get("net_balance", opening + levied - paid)
    arrears = max(0.0, closing)

    return {
        "unit_number": unit_number,
        "financial_year": financial_year,
        "opening_balance": opening,
        "levied_amount": levied,
        "paid_amount": paid,
        "closing_balance": closing,
        "arrears": arrears,
    }


@router.get(
    "/building/defects",
    summary="List building defects",
)
async def list_building_defects(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        status_filter: Optional[str] = Query(None, alias="status"),
        key_doc: dict = Depends(require_scope("read:maintenance")),
):
    """Return maintenance requests categorised as building defects."""
    query: dict = {"$or": [{"category": "building_defect"}, {"category": "defect"}]}
    if status_filter:
        query["status"] = status_filter

    total = await db.maintenance_requests.count_documents(query)
    skip = (page - 1) * page_size
    docs = await db.maintenance_requests.find(query, {"_id": 0}).skip(skip).limit(page_size).to_list(page_size)

    data = [
        {
            "id": d.get("id", ""),
            "title": d.get("title", ""),
            "description": d.get("description"),
            "category": d.get("category"),
            "priority": d.get("priority"),
            "status": d.get("status", ""),
            "location": d.get("location"),
            "reported_at": d.get("created_at", ""),
            "resolved_at": d.get("resolved_at"),
        }
        for d in docs
    ]

    return {"data": data, "meta": _pagination_meta(total, page, page_size)}


@router.post(
    "/building/defects",
    status_code=status.HTTP_201_CREATED,
    summary="Report a building defect",
)
async def report_building_defect(
        data: DefectCreate,
        background_tasks: BackgroundTasks,
        key_doc: dict = Depends(require_scope("write:maintenance")),
):
    """Generated function header.

    Function: report_building_defect
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    req_id = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": req_id,
        "title": data.title,
        "description": data.description,
        "category": "building_defect",
        "location": data.location,
        "priority": data.priority or "medium",
        "status": "open",
        "submitted_by": f"{_API_KEY_SUBMITTER_PREFIX}{key_doc['id']}",
        "submitted_by_name": key_doc.get("name", "External API"),
        "work_order_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    await db.maintenance_requests.insert_one(doc)

    background_tasks.add_task(
        _dispatch_webhook_event,
        "maintenance.created",
        {"id": req_id, "title": data.title, "category": "building_defect"},
    )

    return {"id": req_id, "status": "open", "created_at": now}


# ──────────────────────────────────────────────────────────────────────────────
# Owners Corporation  (read:building)
# @featuretrace:financial-metrics-canonical — partner-API OC/finance summaries.
# Layer: router
# Data flow: partner API caller → GET /oc/summary|/oc/levies|/finance/summary|/finance/budget|
#            /finance/transactions|/units/{unit}/levy-balance → annual_levies/unit_levy_ledger/
#            levy_payments/levy_categories (Mongo fallback) or services/financial_read_service.py
#            (Postgres, when financial_pg_reads_enabled+external_api_finance_pg_enabled) →
#            domain/finance/formulas/collection.py::quarterly_collection_rate() for get_oc_summary's
#            collection_rate_pct (building-scoped).
# Related: backend/routers/trust_phase1.py
#          backend/services/bi_service.py
#          backend/utils/finance_helpers.py (get_levy_fund_data/get_latest_levy_year/
#          get_levy_proposed_amounts/compute_combined_fund_totals/legacy_fund_type)
# Toggle: financial_pg_reads_enabled, external_api_finance_pg_enabled
# Collection: annual_levies, unit_levy_ledger, levy_payments, levy_categories
# Tests: tests/backend/test_external_api_building_scoping.py, tests/backend/test_external_api_finance_cutover.py
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/oc/summary",
    response_model=OCPerformanceSummary,
    summary="Owners Corporation performance summary",
)
async def get_oc_summary(
        key_doc: dict = Depends(require_scope("read:building")),
):
    """Generated function header.

    Function: get_oc_summary
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    total_units = await db.units.count_documents({"building_id": building_id})
    tenanted = await db.units.count_documents({"building_id": building_id, "is_tenanted": True})
    # A unit is counted as occupied when it is tenanted OR has a non-null owner_name.
    # This relies on the assumption that owner_name is only populated for units with
    # an actual known owner — not placeholder values.
    # $or nested inside $and (rather than a top-level $or) so the tenant-scoping
    # wrapper can still verify the explicit building_id when no request context is set.
    occupied = await db.units.count_documents({"$and": [
        {"building_id": building_id},
        {"$or": [{"is_tenanted": True}, {"owner_name": {"$exists": True, "$ne": None}}]},
    ]})

    # Get current levy year. annual_levies' real Mongo field is `year` (not
    # `financial_year`), and fund totals live nested under admin_fund/sinking_fund
    # (not flat admin_fund_income/sinking_fund_income) — see CLAUDE.md. Both the field
    # name and the nested shape were wrong here, so total_budgeted was always 0.0 for
    # every real building on this Mongo-fallback path.
    financial_year = await get_latest_levy_year(building_id) or str(datetime.now(timezone.utc).year)
    levy = await get_levy_fund_data(financial_year, building_id)

    # Total budgeted levy income — canonical full-year proposed amounts (CLAUDE.md:
    # "always use get_levy_proposed_amounts(levy_doc)" for annual levy amounts).
    admin_income, sinking_income = get_levy_proposed_amounts(levy) if levy else (0.0, 0.0)
    total_budgeted = admin_income + sinking_income

    # Total collected
    total_collected = await _sum_levy_payments(building_id, financial_year)

    # Units in arrears — canonical grace-aware count (GAP-FIN-040), never the
    # banned total_closing field; agrees with /finance/summary and the dashboards.
    from utils.finance_helpers import get_building_arrears_summary
    units_in_arrears = (await get_building_arrears_summary(building_id, financial_year))["units_in_arrears"]

    # GAP-FIN-016 Phase 2b: shared with trust_phase1.py/bi_service.py — see
    # domain/finance/formulas/collection.py::quarterly_collection_rate() docstring.
    # total_collected/total_budgeted here are dollar floats — convert to cents at
    # this boundary before the domain call, per the ledger cents-only rule.
    collection_rate = float(quarterly_collection_rate(
        paid_cents=round(total_collected * 100), levied_cents=round(total_budgeted * 100),
        digits=2, zero_denominator_value=Decimal("0.00"),
    ))

    return {
        "total_units": total_units,
        "occupied_units": occupied,
        "tenanted_units": tenanted,
        "owner_occupier_units": max(0, occupied - tenanted),
        "current_financial_year": financial_year,
        "total_levy_income_budgeted": round(total_budgeted, 2),
        "total_levy_collected": round(total_collected, 2),
        "collection_rate_pct": round(collection_rate, 2),
        "units_in_arrears": units_in_arrears,
    }


async def _sum_levy_payments(building_id: str, financial_year: str) -> float:
    """Generated function header.

    Function: _sum_levy_payments
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    pipeline = [
        {"$match": {"building_id": building_id, "year": financial_year, "status": "confirmed"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    res = await db.levy_payments.aggregate(pipeline).to_list(1)
    return res[0]["total"] if res else 0.0


@router.get(
    "/oc/levies",
    response_model=OCLevySummary,
    summary="Owners Corporation levy summary",
)
async def get_oc_levies(
        financial_year: Optional[str] = Query(None),
        key_doc: dict = Depends(require_scope("read:finance")),
):
    """Generated function header.

    Function: get_oc_levies
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    if await are_cutover_features_enabled(
            building_id,
            FINANCIAL_PG_READS_ENABLED,
            EXTERNAL_API_FINANCE_PG_ENABLED,
    ):
        pg_summary = await _financial_read_service.get_oc_levy_summary(
            building_id=building_id,
            financial_year=financial_year,
        )
        if not pg_summary:
            raise HTTPException(status_code=404, detail="No levy data found for the requested year")
        return pg_summary

    levy = await _get_annual_levy(building_id, financial_year)

    if not levy:
        raise HTTPException(status_code=404, detail="No levy data found for the requested year")

    fy = levy.get("financial_year", "")
    total_collected = await _sum_levy_payments(building_id, fy)
    admin_income, sinking_income = get_levy_proposed_amounts(levy)
    total_budgeted = admin_income + sinking_income

    return {
        "financial_year": fy,
        "admin_fund_budgeted": round(admin_income, 2),
        "sinking_fund_budgeted": round(sinking_income, 2),
        "total_budgeted": round(total_budgeted, 2),
        "total_collected": round(total_collected, 2),
        "total_outstanding": round(max(0.0, total_budgeted - total_collected), 2),
        "levy_periods": levy.get("levy_periods", []),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Finance / Accounts  (read:finance)
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/finance/summary",
    response_model=FinanceSummaryResponse,
    summary="Financial summary",
    description=(
            "Returns a high-level financial position for the building — budget vs actuals, "
            "levy income collected, and total levy arrears.  Suitable for banking connectors "
            "and external strata management platforms."
    ),
)
async def get_finance_summary(
        financial_year: Optional[str] = Query(None),
        key_doc: dict = Depends(require_scope("read:finance")),
):
    """Generated function header.

    Function: get_finance_summary
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    if await are_cutover_features_enabled(
            building_id,
            FINANCIAL_PG_READS_ENABLED,
            EXTERNAL_API_FINANCE_PG_ENABLED,
    ):
        pg_summary = await _financial_read_service.get_finance_summary(
            building_id=building_id,
            financial_year=financial_year,
        )
        if not pg_summary:
            raise HTTPException(status_code=404, detail="No financial data found")
        return pg_summary

    levy = await _get_annual_levy(building_id, financial_year)
    if not levy:
        raise HTTPException(status_code=404, detail="No financial data found")

    fy = levy.get("financial_year", "")
    admin_income, sinking_income = get_levy_proposed_amounts(levy)
    total_expenses_budget = compute_combined_fund_totals(levy)["total_expenses"]

    total_collected = await _sum_levy_payments(building_id, fy)

    # Actual expenses from financial transactions
    actual_expense = await _sum_actual_expenses(building_id, fy)

    # Arrears — canonical grace-aware true arrears (GAP-FIN-040), never the banned
    # total_closing field; consistent with /finance/summary and the dashboards.
    from utils.finance_helpers import get_building_arrears_summary
    levy_arrears = (await get_building_arrears_summary(building_id, fy))["true_arrears_amount"]

    total_budget = admin_income + sinking_income

    # NOTE: per-fund actual collection is estimated via proportional split of total payments
    # collected (admin_income / total_budget ratio).  Payments are not tagged to a specific
    # fund in the current levy_payments collection.  If explicit fund allocations are added
    # later, replace this with a direct query on the fund-tagged payment records.
    return {
        "financial_year": fy,
        "admin_fund_budget": round(admin_income, 2),
        "admin_fund_actual": round(total_collected * (admin_income / total_budget) if total_budget else 0.0, 2),
        "sinking_fund_budget": round(sinking_income, 2),
        "sinking_fund_actual": round(total_collected * (sinking_income / total_budget) if total_budget else 0.0, 2),
        "total_income": round(total_budget, 2),
        "total_expenses": round(total_expenses_budget, 2),
        "net_position": round(total_collected - actual_expense, 2),
        "levy_arrears_total": round(levy_arrears, 2),
    }


async def _get_annual_levy(building_id: str, financial_year: Optional[str]) -> Optional[dict]:
    """Generated function header.

    Function: _get_annual_levy
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    # annual_levies' real Mongo field is `year` (not `financial_year`) — see CLAUDE.md.
    # get_levy_fund_data()/get_latest_levy_year() are the canonical lookups (the latter
    # excludes not-yet-started future/synthetic budget years, unlike a raw sort-desc).
    fy = financial_year or await get_latest_levy_year(building_id)
    if not fy:
        return None
    levy = await get_levy_fund_data(fy, building_id)
    if not levy:
        return None
    return {**levy, "financial_year": str(fy)}


async def _sum_actual_expenses(building_id: str, financial_year: str) -> float:
    """Generated function header.

    Function: _sum_actual_expenses
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    pipeline = [
        {"$match": {"building_id": building_id, "financial_year": financial_year, "transaction_type": "expense"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    res = await db.financial_transactions.aggregate(pipeline).to_list(1)
    return res[0]["total"] if res else 0.0


@router.get(
    "/finance/budget",
    summary="Budget categories with actuals",
)
async def get_budget(
        financial_year: Optional[str] = Query(None),
        fund_type: Optional[str] = Query(None, pattern="^(admin|sinking)$"),
        key_doc: dict = Depends(require_scope("read:finance")),
):
    """Generated function header.

    Function: get_budget
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    levy = await _get_annual_levy(building_id, financial_year)
    if not levy:
        raise HTTPException(status_code=404, detail="No financial data found")

    fy = levy.get("financial_year", "")
    query: dict = {"building_id": building_id, "year": fy}
    if fund_type:
        # levy_categories stores the legacy long-form fund_type ("administrative"),
        # while this endpoint's query param accepts the short form ("admin").
        query["fund_type"] = legacy_fund_type(fund_type)

    categories = await db.levy_categories.find(query, {"_id": 0}).to_list(500)

    data = [
        {
            "category_name": c.get("category_name", ""),
            "fund_type": c.get("fund_type", ""),
            "budgeted_amount": c.get("budgeted_amount", 0.0) or 0.0,
            "actual_amount": c.get("actual_amount", 0.0) or 0.0,
            "variance": round(
                (c.get("budgeted_amount") or 0.0) - (c.get("actual_amount") or 0.0), 2
            ),
        }
        for c in categories
    ]

    return {"financial_year": fy, "categories": data}


@router.get(
    "/finance/transactions",
    summary="Recent levy payments / financial transactions",
)
async def get_transactions(
        financial_year: Optional[str] = Query(None),
        unit_number: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        key_doc: dict = Depends(require_scope("read:finance")),
):
    """Generated function header.

    Function: get_transactions
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await resolve_building_id_for_key(key_doc)
    if not financial_year:
        financial_year = await get_latest_levy_year(building_id) or str(datetime.now(timezone.utc).year)

    query: dict = {"building_id": building_id, "year": financial_year}
    if unit_number:
        query["unit_number"] = unit_number

    total = await db.levy_payments.count_documents(query)
    skip = (page - 1) * page_size
    payments = await db.levy_payments.find(query, {"_id": 0}).skip(skip).limit(page_size).to_list(page_size)

    data = [
        {
            "id": p.get("id", ""),
            "transaction_type": "levy_payment",
            "amount": p.get("amount", 0.0),
            "description": p.get("description") or "Levy payment",
            "transaction_date": p.get("payment_date") or p.get("created_at", ""),
            "unit_number": p.get("unit_number"),
            "reference": p.get("reference") or p.get("payment_method"),
        }
        for p in payments
    ]

    return {"data": data, "meta": _pagination_meta(total, page, page_size)}


# ──────────────────────────────────────────────────────────────────────────────
# Maintenance Requests  (read:maintenance / write:maintenance)
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/maintenance",
    summary="List maintenance requests",
)
async def list_maintenance_requests(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        status_filter: Optional[str] = Query(None, alias="status"),
        category: Optional[str] = Query(None),
        key_doc: dict = Depends(require_scope("read:maintenance")),
):
    """Generated function header.

    Function: list_maintenance_requests
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {}
    if status_filter:
        query["status"] = status_filter
    if category:
        query["category"] = category

    total = await db.maintenance_requests.count_documents(query)
    skip = (page - 1) * page_size
    docs = await db.maintenance_requests.find(query, {"_id": 0}).skip(skip).limit(page_size).to_list(page_size)

    data = [
        {
            "id": d.get("id", ""),
            "title": d.get("title", ""),
            "category": d.get("category"),
            "priority": d.get("priority"),
            "status": d.get("status", ""),
            "location": d.get("location"),
            "submitted_at": d.get("created_at", ""),
            "updated_at": d.get("updated_at"),
            "work_order_ids": d.get("work_order_ids", []),
        }
        for d in docs
    ]
    return {"data": data, "meta": _pagination_meta(total, page, page_size)}


@router.get(
    "/maintenance/{request_id}",
    response_model=MaintenanceRequestSummary,
    summary="Get maintenance request details",
)
async def get_maintenance_request(
        request_id: str,
        key_doc: dict = Depends(require_scope("read:maintenance")),
):
    """Generated function header.

    Function: get_maintenance_request
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.maintenance_requests.find_one({"id": request_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Maintenance request not found")
    return {
        "id": doc.get("id", ""),
        "title": doc.get("title", ""),
        "category": doc.get("category"),
        "priority": doc.get("priority"),
        "status": doc.get("status", ""),
        "location": doc.get("location"),
        "submitted_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at"),
        "work_order_ids": doc.get("work_order_ids", []),
    }


@router.put(
    "/maintenance/{request_id}/status",
    summary="Update maintenance request status",
)
async def update_maintenance_status(
        request_id: str,
        data: MaintenanceStatusUpdate,
        background_tasks: BackgroundTasks,
        key_doc: dict = Depends(require_scope("write:maintenance")),
):
    """Generated function header.

    Function: update_maintenance_status
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.maintenance_requests.find_one({"id": request_id}, {"_id": 0, "title": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Maintenance request not found")

    now = _now()
    update: dict = {"$set": {"status": data.status, "updated_at": now}}
    if data.status in {"resolved", "completed", "closed"}:
        update["$set"]["resolved_at"] = now
    if data.notes:
        # notes is an embedded array of {text, added_at, added_by} objects on the
        # maintenance_requests document — consistent with internal note-taking patterns.
        update["$push"] = {
            "notes": {"text": data.notes, "added_at": now, "added_by": f"{_API_KEY_SUBMITTER_PREFIX}{key_doc['id']}"}}

    await db.maintenance_requests.update_one({"id": request_id}, update)

    background_tasks.add_task(
        _dispatch_webhook_event,
        "maintenance.updated",
        {"id": request_id, "status": data.status},
    )

    return {"id": request_id, "status": data.status, "updated_at": now}


# ──────────────────────────────────────────────────────────────────────────────
# Work Orders  (read:work-orders / write:work-orders)
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/work-orders",
    summary="List work orders",
)
async def list_work_orders(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        status_filter: Optional[str] = Query(None, alias="status"),
        vendor_id: Optional[str] = Query(None),
        key_doc: dict = Depends(require_scope("read:work-orders")),
):
    """Generated function header.

    Function: list_work_orders
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {}
    if status_filter:
        query["status"] = status_filter
    if vendor_id:
        query["assigned_vendor"] = vendor_id

    total = await db.work_orders.count_documents(query)
    skip = (page - 1) * page_size
    docs = await db.work_orders.find(query, {"_id": 0}).skip(skip).limit(page_size).to_list(page_size)

    data = [
        {
            "id": d.get("id", ""),
            "title": d.get("title", ""),
            "maintenance_request_id": d.get("maintenance_request_id"),
            "status": d.get("status", ""),
            "priority": d.get("priority"),
            "assigned_vendor": d.get("assigned_vendor"),
            "vendor_name": d.get("vendor_name"),
            "estimated_cost": d.get("estimated_cost"),
            "actual_cost": d.get("actual_cost"),
            "created_at": d.get("created_at", ""),
            "updated_at": d.get("updated_at"),
            "completion_date": d.get("completion_date"),
        }
        for d in docs
    ]
    return {"data": data, "meta": _pagination_meta(total, page, page_size)}


@router.get(
    "/work-orders/{work_order_id}",
    response_model=WorkOrderSummary,
    summary="Get work order details",
)
async def get_work_order(
        work_order_id: str,
        key_doc: dict = Depends(require_scope("read:work-orders")),
):
    """Generated function header.

    Function: get_work_order
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.work_orders.find_one({"id": work_order_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Work order not found")
    return {
        "id": doc.get("id", ""),
        "title": doc.get("title", ""),
        "maintenance_request_id": doc.get("maintenance_request_id"),
        "status": doc.get("status", ""),
        "priority": doc.get("priority"),
        "assigned_vendor": doc.get("assigned_vendor"),
        "vendor_name": doc.get("vendor_name"),
        "estimated_cost": doc.get("estimated_cost"),
        "actual_cost": doc.get("actual_cost"),
        "created_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at"),
        "completion_date": doc.get("completion_date"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Service Provider Integration  (write:service)
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/service/work-orders",
    summary="Work orders available to service providers",
    description="Returns work orders in NEW or QUOTE_REQUESTED status that require vendor attention.",
)
async def get_service_work_orders(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        key_doc: dict = Depends(require_scope("read:work-orders")),
):
    """Generated function header.

    Function: get_service_work_orders
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"status": {"$in": ["new", "quote_requested", "approved", "in_progress"]}}
    total = await db.work_orders.count_documents(query)
    skip = (page - 1) * page_size
    docs = await db.work_orders.find(query, {"_id": 0}).skip(skip).limit(page_size).to_list(page_size)

    data = [
        {
            "id": d.get("id", ""),
            "title": d.get("title", ""),
            "status": d.get("status", ""),
            "priority": d.get("priority"),
            "location": d.get("location"),
            "description": d.get("description"),
            "estimated_cost": d.get("estimated_cost"),
            "created_at": d.get("created_at", ""),
        }
        for d in docs
    ]
    return {"data": data, "meta": _pagination_meta(total, page, page_size)}


@router.post(
    "/service/quotes",
    status_code=status.HTTP_201_CREATED,
    response_model=QuoteResponse,
    summary="Submit a quote for a work order",
)
async def submit_quote(
        data: QuoteSubmit,
        background_tasks: BackgroundTasks,
        key_doc: dict = Depends(require_scope("write:service")),
):
    # Verify work order exists
    """Generated function header.

    Function: submit_quote
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    wo = await db.work_orders.find_one({"id": data.work_order_id}, {"_id": 0, "status": 1, "title": 1})
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    quote_id = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": quote_id,
        "work_order_id": data.work_order_id,
        "amount": data.amount,
        "description": data.description,
        "valid_until": data.valid_until,
        "notes": data.notes,
        "status": "pending",
        "submitted_by_key": key_doc["id"],
        "submitted_by_name": key_doc.get("name", "External API"),
        "submitted_at": now,
        "updated_at": now,
    }
    await db.quotes.insert_one(doc)

    # Mark work order as quote received
    await db.work_orders.update_one(
        {"id": data.work_order_id},
        {"$set": {"status": "quote_received", "updated_at": now}},
    )

    background_tasks.add_task(
        _dispatch_webhook_event,
        "quote.submitted",
        {"quote_id": quote_id, "work_order_id": data.work_order_id, "amount": data.amount},
    )

    return {
        "id": quote_id,
        "work_order_id": data.work_order_id,
        "amount": data.amount,
        "description": data.description,
        "status": "pending",
        "submitted_at": now,
        "valid_until": data.valid_until,
    }


@router.post(
    "/service/invoices",
    status_code=status.HTTP_201_CREATED,
    response_model=InvoiceStatusResponse,
    summary="Submit an invoice for a completed work order",
)
async def submit_invoice(
        data: InvoiceSubmit,
        background_tasks: BackgroundTasks,
        key_doc: dict = Depends(require_scope("write:service")),
):
    # Verify work order exists
    """Generated function header.

    Function: submit_invoice
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    wo = await db.work_orders.find_one({"id": data.work_order_id}, {"_id": 0, "status": 1})
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    invoice_id = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": invoice_id,
        "work_order_id": data.work_order_id,
        "invoice_number": data.invoice_number,
        "amount": data.amount,
        "gst_amount": data.gst_amount,
        "description": data.description,
        "invoice_date": data.invoice_date,
        "notes": data.notes,
        "status": "pending",
        "submitted_by_key": key_doc["id"],
        "submitted_by_name": key_doc.get("name", "External API"),
        "submitted_at": now,
        "updated_at": now,
        "approved_at": None,
        "paid_at": None,
    }
    await db.invoices.insert_one(doc)

    # Link invoice to work order
    await db.work_orders.update_one(
        {"id": data.work_order_id},
        {"$set": {"invoice_id": invoice_id, "updated_at": now}},
    )

    background_tasks.add_task(
        _dispatch_webhook_event,
        "invoice.submitted",
        {"invoice_id": invoice_id, "work_order_id": data.work_order_id, "amount": data.amount},
    )

    return {
        "id": invoice_id,
        "work_order_id": data.work_order_id,
        "invoice_number": data.invoice_number,
        "amount": data.amount,
        "gst_amount": data.gst_amount,
        "status": "pending",
        "submitted_at": now,
        "approved_at": None,
        "paid_at": None,
    }


@router.get(
    "/service/invoices/{invoice_id}",
    response_model=InvoiceStatusResponse,
    summary="Get invoice status",
)
async def get_invoice_status(
        invoice_id: str,
        key_doc: dict = Depends(require_scope("write:service")),
):
    """Generated function header.

    Function: get_invoice_status
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return {
        "id": doc.get("id", ""),
        "work_order_id": doc.get("work_order_id", ""),
        "invoice_number": doc.get("invoice_number", ""),
        "amount": doc.get("amount", 0.0),
        "gst_amount": doc.get("gst_amount"),
        "status": doc.get("status", ""),
        "submitted_at": doc.get("submitted_at", ""),
        "approved_at": doc.get("approved_at"),
        "paid_at": doc.get("paid_at"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Health / Discovery
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/health",
    summary="API health check",
    description="No authentication required. Returns API version and status.",
    include_in_schema=True,
)
async def api_health():
    """Generated function header.

    Function: api_health
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "status": "ok",
        "version": "1.0",
        "api": "Strata Management External API v1",
        "timestamp": _now(),
    }


@router.get(
    "/scopes",
    summary="List available API scopes",
    description="Returns all available permission scopes for API key creation.",
)
async def list_scopes(
        key_doc: dict = Depends(get_api_key_auth),
):
    """Generated function header.

    Function: list_scopes
    Path: backend/routers/external_api.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {"scopes": sorted(VALID_SCOPES)}
