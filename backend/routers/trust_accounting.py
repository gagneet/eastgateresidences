# @featuretrace:pg-migration-shadow — GET /trust/trial-balance shadow-compares against trust_shadow_read_service.
# Layer: router
# Data flow: GET /trust/trial-balance → _maybe_shadow_trial_balance() → trust_shadow_read_service.compare_trust_trial_balance() → core.shadow_diffs (building-scoped).
# Related: backend/services/trust_shadow_read_service.py
#          backend/services/trust_read_service.py
#          docs/fixes/BUG-TRUST-001-legacy-v1-trust-accounting-empty-mongo-collection.md
# Toggle: N/A — gated by trust_ledger's domain_cutover_status mode via require_domain_source
# Collection: trust_ledger_accounts (legacy V1 collection; see BUG-TRUST-001)

"""
Trust Accounting Router — Double-Entry Ledger API

Endpoints:
  POST   /trust/accounts                          — create trust account
  GET    /trust/accounts                          — list accounts (building-scoped)
  POST   /trust/journal                           — post journal entry
  GET    /trust/journal                           — list journal entries
  GET    /trust/journal/{entry_id}                — get single entry
  POST   /trust/journal/{entry_id}/void           — void a posted entry
  GET    /trust/balance                           — fund balance summary
  GET    /trust/trial-balance                     — trial balance report
  POST   /trust/batches                           — create payment batch
  GET    /trust/batches                           — list batches
  POST   /trust/batches/{batch_id}/approve        — first approval (moves to pending_second_approval if ABA ≥ $5k)
  POST   /trust/batches/{batch_id}/second-approve — second approval for high-value ABA batches (GAP-FIN-011)
  GET    /trust/batches/{batch_id}/aba            — download ABA file
  GET    /trust/audit-export                      — hash-chain audit export CSV (GAP-FIN-010)

All endpoints:
  - Require authentication
  - Enforce building_id isolation (multi-tenancy)
  - Write audit logs for financial mutations
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.routing import APIRoute
from pymongo import UpdateOne
from typing import Optional
from pydantic import ValidationError as PydanticValidationError

from domain.journals import Journal, JournalLine, JournalSide
from domain.period_lifecycle import PeriodState, can_post_to_period

# Entry types that map to JournalSide — triggers exact integer-cent domain validation.
# Semantic types (levy_raised, expense, …) use the legacy float-tolerance path.
_DEBIT_CREDIT_SIDES: frozenset[str] = frozenset({"debit", "credit"})
from models.trust_accounting import (
    BatchApprovalResponse,
    BatchSecondApprovalResponse,
    FundType,
    JournalEntryCreate,
    JournalEntryResponse,
    LedgerEntryCreateResponse,
    LedgerReversalCreateResponse,
    LedgerStatus,
    PaymentBatchCreate,
    PaymentBatchResponse,
    TrustAccountCreate,
    TrustAccountResponse,
    VoidJournalEntryResponse,
)
from services.cutover_config_service import (
    TRUST_PG_LEDGER_ENABLED,
    are_cutover_features_enabled,
)
from services.trust_read_service import TrustReadService
from services.domain_source_guard import DomainSourceAuditContext, require_domain_source
from utils.auth import get_current_user, get_current_building
from utils.permissions import require_feature
from utils.encryption import (
    SENSITIVE_TRUST_FIELDS,
    encrypt_document_fields,
    redact_document_fields,
)
from utils.helpers import create_audit_log
# GAP-SEC-005 group 2, tier A. Added as an ADDITIONAL dependency alongside each
# route's existing check, never as a replacement — the effective rule is the
# intersection, so this can only narrow access. See
# docs/security/group2_financial_route_tiers_2026_08_24.md
from services.capability_registry import require_capability

logger = logging.getLogger(__name__)

_V1_SUCCESSOR = "/api/trust/v2"
_V1_SUNSET = "Tue, 13 Oct 2026 00:00:00 GMT"


def _hash_actor_id(value: object) -> str | None:
    """Generated function header.

    Function: _hash_actor_id
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def _capture_v1_trust_usage(
        request: Request,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
) -> None:
    """Generated function header.

    Function: _capture_v1_trust_usage
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    request.state.legacy_trust_v1_usage = {
        "event": "legacy_trust_v1_usage",
        "route": request.scope.get("route").path if request.scope.get("route") else request.url.path,
        "method": request.method,
        "building_id": building_id,
        "actor_hash": _hash_actor_id(current_user.get("id") or current_user.get("_id")),
        "actor_role": current_user.get("effective_role") or current_user.get("role"),
        "request_id": request.headers.get("x-request-id"),
        "timestamp": _now_iso(),
    }


class DeprecatedTrustV1Route(APIRoute):
    """Attach Stage-1 V1 deprecation headers and telemetry without changing responses."""

    def get_route_handler(self):
        """Generated function header.

        Function: DeprecatedTrustV1Route.get_route_handler
        Path: backend/routers/trust_accounting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request):
            """Generated function header.

            Function: DeprecatedTrustV1Route.custom_route_handler
            Path: backend/routers/trust_accounting.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            try:
                response = await original_route_handler(request)
            except HTTPException as exc:
                _attach_v1_deprecation_headers_to_exception(exc)
                await _emit_v1_usage(request, exc.status_code)
                raise
            _attach_v1_deprecation_headers(response)
            await _emit_v1_usage(request, response.status_code)
            return response

        return custom_route_handler


def _attach_v1_deprecation_headers(response) -> None:
    """Generated function header.

    Function: _attach_v1_deprecation_headers
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for key, value in _v1_deprecation_headers().items():
        response.headers.setdefault(key, value)


def _attach_v1_deprecation_headers_to_exception(exc: HTTPException) -> None:
    """Generated function header.

    Function: _attach_v1_deprecation_headers_to_exception
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    headers = dict(exc.headers or {})
    for key, value in _v1_deprecation_headers().items():
        headers.setdefault(key, value)
    exc.headers = headers


def _v1_deprecation_headers() -> dict[str, str]:
    """Generated function header.

    Function: _v1_deprecation_headers
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "Deprecation": "true",
        "Sunset": _V1_SUNSET,
        "Link": f'<{_V1_SUCCESSOR}>; rel="successor-version"',
        "Warning": '299 - "Legacy /trust API is deprecated; use /trust/v2"',
    }


async def _emit_v1_usage(request: Request, status_code: int) -> None:
    """Generated function header.

    Function: _emit_v1_usage
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payload = getattr(request.state, "legacy_trust_v1_usage", None)
    if not payload:
        payload = {
            "event": "legacy_trust_v1_usage",
            "route": request.scope.get("path"),
            "method": request.method,
            "timestamp": _now_iso(),
        }
    record = {**payload, "response_status": status_code}

    # NOTE: logging.basicConfig's format string (server.py) only interpolates
    # asctime/name/levelname/message — an `extra={...}` dict is attached to the
    # LogRecord in memory but never appears in the rendered text. Embed the key
    # fields in the message itself so a plain journalctl grep is still useful for
    # a quick spot-check, independent of the durable Mongo write below.
    logger.info(
        "legacy_trust_v1_usage route=%s method=%s building_id=%s actor_role=%s "
        "actor_hash=%s request_id=%s response_status=%s",
        record.get("route"), record.get("method"), record.get("building_id"),
        record.get("actor_role"), record.get("actor_hash"), record.get("request_id"),
        status_code,
        extra=record,
    )

    # The Stage 1 retirement plan calls for a 14-30 day telemetry review before
    # Stage 2 disables V1 writes. journald retention on this deployment is far
    # shorter than that window, so the log line alone cannot support the review —
    # write the same payload to a durable, queryable collection. Telemetry must
    # never break the (deprecated but still functioning) V1 response.
    try:
        db = _get_db()
        await db.trust_v1_usage_telemetry.insert_one({**record, "created_at": datetime.now(timezone.utc)})
    except Exception as exc:  # noqa: BLE001 - telemetry is best-effort by design
        logger.warning("Failed to record trust_v1_usage_telemetry: %s", exc)


# GAP-FT-001: Gate the entire trust accounting router behind the trust_accounting
# feature toggle. All endpoints inherit this dependency automatically, so disabling
# the toggle in the Feature Toggles UI immediately blocks API access for any building.
router = APIRouter(
    prefix="/trust",
    tags=["Trust Accounting"],
    dependencies=[Depends(require_feature("trust_accounting")), Depends(_capture_v1_trust_usage)],
    route_class=DeprecatedTrustV1Route,
    deprecated=True,
)
_trust_read_service = TrustReadService()

# GAP-FIN-049 (2026-08-07): the shadow route_keys the trust comparators write to
# core.shadow_diffs, keyed by canonical domain (see trust_shadow_read_service.py).
# Consumed by the critical-diff HARD gate in _trust_pg_read_enabled to fail PG trust
# reads over to Mongo whenever the trust shadow comparators have recorded a critical
# divergence for the domain.
_TRUST_SHADOW_ROUTE_KEYS: dict[str, list[str]] = {
    "trust_ledger": ["trust.summary", "trust_v2.accounts.summary"],
    "trust_reconciliation": ["trust_reconciliation.summary"],
}


async def _trust_pg_read_enabled(
    building_id: str,
    toggle_key: str,
    domain: str,
    *,
    route: str | None = None,
    current_user: dict | None = None,
) -> bool:
    """Generated function header.

    Function: _trust_pg_read_enabled
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await are_cutover_features_enabled(building_id, toggle_key):
        return False
    decision = await require_domain_source(
        domain=domain,
        building_id=building_id,
        operation="read",
        requested_source="postgres",
        audit_context=DomainSourceAuditContext(
            actor_user_id=str(current_user.get("id")) if current_user and current_user.get("id") else None,
            actor_role=(current_user.get("effective_role") or current_user.get("role")) if current_user else None,
            route=route,
            source_service="trust_accounting_router",
            feature_toggle_key=toggle_key,
        ),
    )
    if not decision.postgres_allowed:
        return False
    # GAP-FIN-049 critical-diff HARD gate — mirrors finance_route_cutover_service's
    # never-waivable critical gate, previously absent from the trust read path (the
    # gap this closes: trust reads could flip to PG on domain mode alone while the trust
    # shadow comparators were actively recording critical divergence). If ANY critical
    # trust shadow diff exists for this domain in the recent window, refuse PG and fall
    # back to Mongo (the trusted store). Deliberately non-blocking on no-data / query
    # error: the domain-source decision above stays the primary control and Mongo is
    # always the fallback, so a failed readiness check can never wrongly serve stale PG.
    try:
        from services.cutover_status_service import canonical_domain
        from services.shadow_read_service import get_building_domain_shadow_readiness
        canon = canonical_domain(domain)
        route_keys = _TRUST_SHADOW_ROUTE_KEYS.get(canon, [])
        if route_keys:
            readiness = await get_building_domain_shadow_readiness(
                building_id=building_id, domain=canon, route_keys=route_keys,
            )
            if int(readiness.get("total_critical_count") or 0) > 0:
                logger.warning(
                    "trust PG read refused for %s/%s: %d critical shadow diff(s) — "
                    "falling back to Mongo",
                    building_id, canon, readiness["total_critical_count"],
                )
                return False
    except Exception as exc:
        logger.debug("trust critical-diff gate check failed for %s: %s", building_id, exc)
    return True


async def _block_v1_write_if_pg_primary(
    building_id: str,
    *,
    route: str,
    toggle_key: str = "trust_pg_ledger_enabled",
) -> None:
    """Refuse a legacy V1 trust write when Postgres is the live trust read source.

    Every write endpoint in this (V1) router persists ONLY to Mongo
    (``db.trust_ledger_accounts`` / ``_entries`` / ``_batches``) — there is no
    Postgres write path here and no worker bridges the two stores. Once a
    building's trust ledger reads are served from Postgres (``trust_pg_ledger_enabled``
    resolves True — see :func:`_trust_pg_read_enabled`), a Mongo-only write accepted
    here would be **invisible** to every PG-backed read: a silent read/write store
    split that quietly orphans the document. These endpoints are already deprecated
    in favour of ``/trust/v2`` (see :class:`DeprecatedTrustV1Route`); rather than
    accept a write that vanishes, refuse it with a 409 that names the successor API.

    No-op when the toggle is disabled for the building — the legacy Mongo write path
    is still the live one there, so V1 writes remain valid.
    """
    if not await are_cutover_features_enabled(building_id, toggle_key):
        return
    logger.warning(
        "legacy trust V1 write refused route=%s building_id=%s: %s enabled — "
        "trust reads are PostgreSQL-primary; a Mongo-only V1 write would be "
        "orphaned (invisible to PG-backed reads)",
        route, building_id, toggle_key,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Trust ledger for this building is served from PostgreSQL "
            f"({toggle_key} enabled). The legacy /trust (V1) write API persists "
            "only to MongoDB, so this write would be invisible to PostgreSQL-backed "
            "reads. Use the /trust/v2 API instead."
        ),
        headers=_v1_deprecation_headers(),
    )


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _require_finance_role(current_user: dict) -> dict:
    """Enforce that only strata managers / EC / admin can access trust ledger.

    Uses the effective_role pattern (CLAUDE.md mandate) so temp-elevated owners
    pass the guard correctly instead of getting a silent 403.
    """
    allowed = {"super_admin", "strata_manager", "ec_member", "strata_admin"}
    # effective_role is set by the elevation middleware; fall back to raw role
    effective_role = current_user.get("effective_role") or current_user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trust accounting access requires strata manager or EC role.",
        )
    return current_user


# ─── Dependency injection helper ─────────────────────────────────────────────

def _get_db():
    """Lazy import of db to avoid circular imports at module load time."""
    from database import db
    return db


# ─── Trust Accounts ───────────────────────────────────────────────────────────
# NOTE: this is the legacy V1 API (see _V1_SUCCESSOR above) — new callers should
# use POST /trust/v2/accounts in routers/trust_phase1.py instead.

@router.post("/accounts", response_model=TrustAccountResponse, status_code=201)
async def create_trust_account(
        payload: TrustAccountCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # Writes bank account details.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Create a trust ledger account for a building/fund combination."""
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/accounts")
    db = _get_db()

    doc = payload.model_dump()
    doc["building_id"] = building_id  # Enforce multi-tenancy
    doc = encrypt_document_fields(doc, SENSITIVE_TRUST_FIELDS)
    doc["current_balance"] = doc.get("opening_balance", 0.0)
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = current_user["id"]

    result = await db.trust_ledger_accounts.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="trust_account_created",
        resource_type="trust_account",
        resource_id=doc["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"account_code": doc["account_code"], "fund_type": doc["fund_type"]},
        building_id=building_id,
    ))

    doc = redact_document_fields(doc, SENSITIVE_TRUST_FIELDS)
    return TrustAccountResponse(**doc)


@router.get("/accounts")
async def list_trust_accounts(
        building_id: str = Depends(get_current_building),
        fund_type: Optional[FundType] = None,
        current_user: dict = Depends(get_current_user),
):
    """List trust ledger accounts for a building."""
    _require_finance_role(current_user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_PG_LEDGER_ENABLED,
        "trust",
        route="GET /trust/accounts",
        current_user=current_user,
    ):
        pg_accounts = await _trust_read_service.list_trust_accounts(
            building_id=building_id,
            fund_type=fund_type.value if fund_type else None,
        )
        if pg_accounts is not None:
            return {"data": pg_accounts, "count": len(pg_accounts)}
    db = _get_db()

    query: dict = {"building_id": building_id, "is_active": True}
    if fund_type:
        query["fund_type"] = fund_type.value

    cursor = db.trust_ledger_accounts.find(query).sort("account_code", 1)
    accounts = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        doc = redact_document_fields(doc, SENSITIVE_TRUST_FIELDS)
        accounts.append(doc)
    return {"data": accounts, "count": len(accounts)}


# ─── Journal Entries ──────────────────────────────────────────────────────────

@router.post("/journal", response_model=JournalEntryResponse, status_code=201)
async def post_journal_entry(
        payload: JournalEntryCreate,
        period_id: Optional[str] = Query(None,
                                         description="financial_periods period_id — required for period-guarded posting"),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Post a balanced double-entry journal transaction.

    Phase 2 invariants enforced:
    - Period guard: if period_id is supplied, the period must be OPEN (409 otherwise).
    - Domain balance: exact integer-cent equality for debit/credit entry types.
    - At least 2 lines; all account IDs scoped to this building.
    """
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/journal")
    db = _get_db()

    # ── Phase 2: Period guard ─────────────────────────────────────────────────
    if period_id:
        period_doc = await db.financial_periods.find_one({
            "building_id": building_id,
            "period_id": period_id,
            "fund_type": payload.fund_type.value,
        })
        if not period_doc:
            raise HTTPException(
                status_code=404,
                detail=f"Period {period_id!r} not found for building {building_id!r}.",
            )
        can_post_to_period(PeriodState(period_doc["state"]))

    # ── Phase 2: Domain Journal validation ────────────────────────────────────
    # For lines using the legacy "debit"/"credit" entry_type, run the exact
    # integer-cent domain invariant (no float tolerance). Semantic entry types
    # (levy_raised, expense, etc.) fall through to the existing float check.
    if all(ln.entry_type.value in _DEBIT_CREDIT_SIDES for ln in payload.lines):
        try:
            journal = Journal(
                building_id=building_id,
                period_id=period_id,
                posting_date=payload.date,
                reference=payload.reference,
                narration=payload.narration,
                lines=[
                    JournalLine(
                        account_id=ln.account_id,
                        side=JournalSide(ln.entry_type.value),
                        amount_cents=round(ln.amount * 100),
                    )
                    for ln in payload.lines
                ],
            )
        except PydanticValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

        # Derive totals from integer cents already computed per line — never sum floats
        # then multiply, which can introduce binary rounding drift that breaks the
        # Merkle seal leaf hash and three-way reconciliation invariants.
        debit_total_cents = sum(
            jl.amount_cents for jl in journal.lines if jl.side == JournalSide.DEBIT
        )
        credit_total_cents = sum(
            jl.amount_cents for jl in journal.lines if jl.side == JournalSide.CREDIT
        )
        debit_total = debit_total_cents / 100
        credit_total = credit_total_cents / 100
    else:
        # Existing float-tolerance check for semantic entry types (backward compat).
        if len(payload.lines) < 2:
            raise HTTPException(400, "Journal entry must have at least 2 lines.")

        debit_total = sum(
            ln.amount for ln in payload.lines if ln.entry_type.value == "debit"
        )
        credit_total = sum(
            ln.amount for ln in payload.lines if ln.entry_type.value == "credit"
        )
        if abs(debit_total - credit_total) > 0.001:
            raise HTTPException(
                400,
                f"Journal entry is not balanced: debits={debit_total:.2f}, "
                f"credits={credit_total:.2f}",
            )
        # Convert float totals to cents for storage (legacy semantic-type path).
        debit_total_cents = round(debit_total * 100)
        credit_total_cents = round(credit_total * 100)

    # Validate and collect account IDs in one pass
    account_ids = []
    for ln in payload.lines:
        if not ObjectId.is_valid(ln.account_id):
            raise HTTPException(400, f"Invalid account ID format: {ln.account_id!r}")
        account_ids.append(ln.account_id)
    count = await db.trust_ledger_accounts.count_documents({
        "_id": {"$in": [ObjectId(aid) for aid in account_ids]},
        "building_id": building_id,
    })
    if count != len(set(account_ids)):
        raise HTTPException(400, "One or more account IDs are invalid or belong to a different building.")

    lines_data = [ln.model_dump() for ln in payload.lines]
    doc = {
        "building_id": building_id,
        "fund_type": payload.fund_type.value,
        "date": payload.date,
        "reference": payload.reference,
        "narration": payload.narration,
        "lines": lines_data,
        "status": LedgerStatus.POSTED.value,
        "is_balanced": True,
        "debit_total": round(debit_total, 2),
        "credit_total": round(credit_total, 2),
        # Integer-cent totals for Merkle seal and reconciliation (Phase 2).
        # Both branches above assign these as int — never derived from float arithmetic here.
        "debit_total_cents": debit_total_cents,
        "credit_total_cents": credit_total_cents,
        "period_id": period_id,
        "source_type": payload.source_type,
        "source_id": payload.source_id,
        "batch_id": payload.batch_id,
        "metadata": payload.metadata or {},
        "created_by": current_user["id"],
        "created_at": _now_iso(),
        "voided_at": None,
        "voided_by": None,
        "void_reason": None,
    }

    result = await db.trust_ledger_entries.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    # Update running balances on each account using a single bulk_write
    balance_ops = [
        UpdateOne(
            {"_id": ObjectId(line.account_id)},
            {
                "$inc": {"current_balance": (1.0 if line.entry_type.value == "debit" else -1.0) * line.amount},
                "$set": {"updated_at": _now_iso()},
            },
        )
        for line in payload.lines
    ]
    if balance_ops:
        await db.trust_ledger_accounts.bulk_write(balance_ops, ordered=False)

    asyncio.create_task(create_audit_log(
        action="journal_entry_posted",
        resource_type="journal_entry",
        resource_id=doc["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "reference": payload.reference,
            "amount": debit_total,
            "fund_type": payload.fund_type.value,
        },
        building_id=building_id,
    ))

    return JournalEntryResponse(**doc)


@router.get("/journal")
async def list_journal_entries(
        building_id: str = Depends(get_current_building),
        fund_type: Optional[FundType] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        status: Optional[LedgerStatus] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
):
    """List journal entries for a building with optional filters."""
    _require_finance_role(current_user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_PG_LEDGER_ENABLED,
        "trust",
        route="GET /trust/journal",
        current_user=current_user,
    ):
        pg_entries = await _trust_read_service.list_journal_entries(
            building_id=building_id,
            fund_type=fund_type.value if fund_type else None,
            from_date=from_date,
            to_date=to_date,
            status=status.value if status else None,
            page=page,
            page_size=page_size,
        )
        if pg_entries is not None:
            return pg_entries
    db = _get_db()

    query: dict = {"building_id": building_id}
    if fund_type:
        query["fund_type"] = fund_type.value
    if status:
        query["status"] = status.value
    if from_date or to_date:
        query["date"] = {}
        if from_date:
            query["date"]["$gte"] = from_date
        if to_date:
            query["date"]["$lte"] = to_date

    skip = (page - 1) * page_size
    total = await db.trust_ledger_entries.count_documents(query)
    cursor = (
        db.trust_ledger_entries.find(query)
        .sort("date", -1)
        .skip(skip)
        .limit(page_size)
    )

    entries = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        entries.append(doc)

    return {
        "data": entries,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/journal/{entry_id}")
async def get_journal_entry(
        entry_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Retrieve a single journal entry."""
    _require_finance_role(current_user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_PG_LEDGER_ENABLED,
        "trust",
        route="GET /trust/journal/{entry_id}",
        current_user=current_user,
    ):
        pg_entry = await _trust_read_service.get_journal_entry(
            building_id=building_id,
            entry_id=entry_id,
        )
        if pg_entry is not None:
            return pg_entry
    db = _get_db()

    doc = await db.trust_ledger_entries.find_one({
        "_id": ObjectId(entry_id),
        "building_id": building_id,
    })
    if not doc:
        raise HTTPException(404, "Journal entry not found.")
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.post("/journal/{entry_id}/void", response_model=VoidJournalEntryResponse)
async def void_journal_entry(
        entry_id: str,
        building_id: str = Depends(get_current_building),
        reason: str = Query(..., min_length=5),
        current_user: dict = Depends(get_current_user),
):
    """Void a posted journal entry (creates reversing entry)."""
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/journal/{entry_id}/void")
    db = _get_db()

    entry = await db.trust_ledger_entries.find_one({
        "_id": ObjectId(entry_id),
        "building_id": building_id,
    })
    if not entry:
        raise HTTPException(404, "Journal entry not found.")
    if entry.get("status") == LedgerStatus.VOIDED.value:
        raise HTTPException(400, "Entry is already voided.")

    now = _now_iso()
    await db.trust_ledger_entries.update_one(
        {"_id": ObjectId(entry_id)},
        {"$set": {
            "status": LedgerStatus.VOIDED.value,
            "voided_at": now,
            "voided_by": current_user["id"],
            "void_reason": reason,
        }},
    )

    # Reverse account balances
    for line in entry.get("lines", []):
        sign = -1.0 if line["entry_type"] == "debit" else 1.0
        await db.trust_ledger_accounts.update_one(
            {"_id": ObjectId(line["account_id"])},
            {"$inc": {"current_balance": sign * line["amount"]},
             "$set": {"updated_at": now}},
        )

    asyncio.create_task(create_audit_log(
        action="journal_entry_voided",
        resource_type="journal_entry",
        resource_id=entry_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"reason": reason},
        building_id=building_id,
    ))

    return {"message": "Journal entry voided successfully.", "entry_id": entry_id}


# ─── Fund Balance ─────────────────────────────────────────────────────────────

@router.get("/balance")
async def get_fund_balance(
        building_id: str = Depends(get_current_building),
        fund_type: Optional[FundType] = None,
        current_user: dict = Depends(get_current_user),
):
    """Return current balance per account for a building."""
    _require_finance_role(current_user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_PG_LEDGER_ENABLED,
        "trust",
        route="GET /trust/balance",
        current_user=current_user,
    ):
        pg_balance = await _trust_read_service.get_fund_balance(
            building_id=building_id,
            fund_type=fund_type.value if fund_type else None,
        )
        if pg_balance is not None:
            return pg_balance
    db = _get_db()

    query: dict = {"building_id": building_id, "is_active": True}
    if fund_type:
        query["fund_type"] = fund_type.value

    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": {"fund_type": "$fund_type", "account_type": "$account_type"},
            "total_balance": {"$sum": "$current_balance"},
            "account_count": {"$sum": 1},
        }},
    ]

    rows = []
    async for row in db.trust_ledger_accounts.aggregate(pipeline):
        rows.append({
            "fund_type": row["_id"]["fund_type"],
            "account_type": row["_id"]["account_type"],
            "total_balance": round(row["total_balance"], 2),
            "account_count": row["account_count"],
        })

    return {"building_id": building_id, "as_at": _now_iso(), "balances": rows}


@router.get("/trial-balance")
async def get_trial_balance(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Generate trial balance — lists all accounts with debit/credit totals."""
    _require_finance_role(current_user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_PG_LEDGER_ENABLED,
        "trust",
        route="GET /trust/trial-balance",
        current_user=current_user,
    ):
        pg_trial_balance = await _trust_read_service.get_trial_balance(building_id=building_id)
        if pg_trial_balance is not None:
            _maybe_shadow_trial_balance(building_id, mongo_payload=None, pg_payload_precomputed=pg_trial_balance)
            return pg_trial_balance
    db = _get_db()

    accounts = []
    total_debits = 0.0
    total_credits = 0.0

    cursor = db.trust_ledger_accounts.find(
        {"building_id": building_id, "is_active": True}
    ).sort("account_code", 1)

    async for acc in cursor:
        balance = acc.get("current_balance", 0.0)
        debit = balance if balance >= 0 else 0.0
        credit = abs(balance) if balance < 0 else 0.0
        total_debits += debit
        total_credits += credit
        accounts.append({
            "account_code": acc["account_code"],
            "account_name": acc["account_name"],
            "fund_type": acc["fund_type"],
            "account_type": acc["account_type"],
            "debit": round(debit, 2),
            "credit": round(credit, 2),
        })

    result = {
        "building_id": building_id,
        "as_at": _now_iso(),
        "accounts": accounts,
        "total_debits": round(total_debits, 2),
        "total_credits": round(total_credits, 2),
        "is_balanced": abs(total_debits - total_credits) < 0.01,
    }
    _maybe_shadow_trial_balance(building_id, mongo_payload=result, pg_payload_precomputed=None)
    return result


def _maybe_shadow_trial_balance(
        building_id: str, *, mongo_payload: dict | None, pg_payload_precomputed: dict | None,
) -> None:
    """Fire-and-forget shadow comparison for GET /trust/trial-balance.

    Mirrors finance.py's dual-branch pattern: fires from whichever branch just computed a
    response, regardless of which source was primary. Before trust_ledger is promoted past
    mongo_primary, only the Mongo branch is ever reachable in practice — this still wires
    both for correctness once promotion happens.
    """
    async def _run() -> None:
        """Generated function header.

        Function: _run
        Path: backend/routers/trust_accounting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            from services.domain_source_guard import require_domain_source
            decision = await require_domain_source(
                domain="trust_ledger", building_id=building_id, operation="shadow_read",
            )
            if not decision.shadow_enabled:
                return
            from services.trust_shadow_read_service import compare_trust_trial_balance
            mongo_data = mongo_payload
            pg_data = pg_payload_precomputed
            if mongo_data is None:
                # PG was primary for this request — fetch Mongo independently for comparison.
                mongo_data = await _mongo_trial_balance_for_shadow(building_id)
            if pg_data is None:
                pg_data = await _trust_read_service.get_trial_balance(building_id=building_id)
            await compare_trust_trial_balance(
                building_id=building_id, mongo_payload=mongo_data, pg_payload=pg_data,
            )
        except Exception as exc:
            logger.debug("trust trial-balance shadow compare failed: %s", exc)

    from services.shadow_read_service import schedule_shadow_compare
    schedule_shadow_compare(
        _run(), domain="trust_ledger",
        context={"building_id": building_id, "route": "trust.summary"},
    )


async def _mongo_trial_balance_for_shadow(building_id: str) -> dict:
    """Recompute the Mongo trial balance independently — used only when PG was primary and
    the Mongo value wasn't already computed by the request itself."""
    db = _get_db()
    accounts = []
    total_debits = 0.0
    total_credits = 0.0
    cursor = db.trust_ledger_accounts.find({"building_id": building_id, "is_active": True}).sort("account_code", 1)
    async for acc in cursor:
        balance = acc.get("current_balance", 0.0)
        debit = balance if balance >= 0 else 0.0
        credit = abs(balance) if balance < 0 else 0.0
        total_debits += debit
        total_credits += credit
        accounts.append({"account_code": acc["account_code"], "debit": round(debit, 2), "credit": round(credit, 2)})
    return {
        "building_id": building_id,
        "accounts": accounts,
        "total_debits": round(total_debits, 2),
        "total_credits": round(total_credits, 2),
    }


# ─── Payment Batches ──────────────────────────────────────────────────────────

@router.post("/batches", response_model=PaymentBatchResponse, status_code=201)
async def create_payment_batch(
        payload: PaymentBatchCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # Creates the batch that a later approval releases.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Create a payment batch (pending approval before submission)."""
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/batches")
    db = _get_db()

    total_amount = round(sum(item.amount for item in payload.items), 2)
    items_data = [item.model_dump() for item in payload.items]

    # Encrypt sensitive bank details in each item before persisting
    from utils.encryption import encrypt_field
    for item in items_data:
        if item.get("account_number"):
            item["account_number"] = encrypt_field(item["account_number"])
        if item.get("bsb"):
            item["bsb"] = encrypt_field(item["bsb"])

    doc = {
        "building_id": building_id,
        "fund_type": payload.fund_type.value,
        "batch_type": payload.batch_type.value,
        "description": payload.description,
        "payment_date": payload.payment_date,
        "status": "pending",
        "total_amount": total_amount,
        "item_count": len(payload.items),
        "items": items_data,
        "approved_by": None,
        "approved_at": None,
        "submitted_at": None,
        "aba_file_content": None,
        "created_by": current_user["id"],
        "created_at": _now_iso(),
        "is_test_data": bool(payload.is_test_data),
    }

    result = await db.trust_ledger_batches.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="payment_batch_created",
        resource_type="payment_batch",
        resource_id=doc["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "batch_type": payload.batch_type.value,
            "total_amount": total_amount,
            "item_count": len(payload.items),
        },
        building_id=building_id,
    ))

    return PaymentBatchResponse(**doc)


@router.get("/batches")
async def list_payment_batches(
        building_id: str = Depends(get_current_building),
        batch_status: Optional[str] = None,
        include_test_data: bool = Query(False),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List payment batches for a building."""
    _require_finance_role(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}
    if batch_status:
        query["status"] = batch_status

    skip = (page - 1) * page_size
    total = await db.trust_ledger_batches.count_documents(query)
    cursor = (
        db.trust_ledger_batches.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )

    batches = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        doc.pop("aba_file_content", None)  # don't return file content in list
        batches.append(doc)

    return {
        "data": batches,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/batches/{batch_id}", status_code=204)
async def delete_payment_batch(
        batch_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Delete a test payment batch. Production trust batches remain retained."""
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="DELETE /trust/batches/{batch_id}")
    db = _get_db()

    batch = await db.trust_ledger_batches.find_one({
        "_id": ObjectId(batch_id),
        "building_id": building_id,
    })
    if not batch:
        raise HTTPException(404, "Payment batch not found.")
    if not batch.get("is_test_data"):
        raise HTTPException(400, "Only is_test_data payment batches can be deleted.")

    result = await db.trust_ledger_batches.delete_one({
        "_id": ObjectId(batch_id),
        "building_id": building_id,
        "is_test_data": True,
    })
    if result.deleted_count == 0:
        raise HTTPException(404, "Payment batch not found.")

    asyncio.create_task(create_audit_log(
        action="payment_batch_deleted",
        resource_type="payment_batch",
        resource_id=batch_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"is_test_data": True},
        building_id=building_id,
    ))
    return None


_DUAL_APPROVAL_THRESHOLD = 5_000.00  # GAP-FIN-011: ABA batches ≥ $5,000 require two approvers


def _generate_aba(batch: dict, update: dict) -> None:
    """Attempt ABA file generation; logs warning on failure.

    Deliberately NOT gated on financial_services_mock. generate_aba_file builds a
    string and stores it base64 on the batch — it contacts nothing, so it is not a
    live financial call and blocking it here only breaks the dual-approval workflow
    (it did, on first attempt: four tests in test_trust_dual_approval.py). The live
    boundary for payment initiation is the PaymentInitiationProvider, which
    ProviderRegistry already pins to mock_aba_writer while the toggle is on. Gate the
    TRANSMISSION of the file, never its generation.
    """
    if batch.get("batch_type") == "aba":
        from utils.aba_generator import generate_aba_file
        try:
            aba_content = generate_aba_file(batch)
            update["aba_file_content"] = base64.b64encode(
                aba_content.encode("utf-8")
            ).decode("utf-8")
        except Exception as exc:
            logger.warning("ABA generation failed: %s", exc)


@router.post("/batches/{batch_id}/approve", response_model=BatchApprovalResponse)
async def approve_payment_batch(
        batch_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # Money leaves the trust account. _require_finance_role below still admits
        # ec_member; the capability does not, and the additive guard means the
        # tighter of the two decides. Payment execution is the treasurer's under
        # s 43 and needs a recorded authority (GAP-SEC-007) — until that exists,
        # managers only.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Approve a pending payment batch (first approval).

    ABA batches ≥ $5,000 require a second approver (GAP-FIN-011).
    After first approval the batch moves to 'pending_second_approval'.
    All other batches move directly to 'approved'.
    """
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/batches/{batch_id}/approve")
    db = _get_db()

    batch = await db.trust_ledger_batches.find_one({
        "_id": ObjectId(batch_id),
        "building_id": building_id,
    })
    if not batch:
        raise HTTPException(404, "Payment batch not found.")
    if batch.get("status") != "pending":
        raise HTTPException(400, f"Batch status is '{batch['status']}'; only 'pending' batches can be approved.")

    now = _now_iso()
    total = batch.get("total_amount", 0)
    needs_dual = total >= _DUAL_APPROVAL_THRESHOLD and batch.get("batch_type") == "aba"

    update: dict = {
        "approved_by": current_user["id"],
        "approved_at": now,
    }

    if needs_dual:
        update["status"] = "pending_second_approval"
        msg = f"First approval recorded. A second approver is required (total ${total:,.2f} ≥ ${_DUAL_APPROVAL_THRESHOLD:,.0f})."
    else:
        update["status"] = "approved"
        _generate_aba(batch, update)
        msg = "Batch approved."

    await db.trust_ledger_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": update},
    )

    asyncio.create_task(create_audit_log(
        action="payment_batch_approved",
        resource_type="payment_batch",
        resource_id=batch_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "total_amount": total,
            "item_count": batch.get("item_count"),
            "dual_approval_required": needs_dual,
        },
        building_id=building_id,
    ))

    return {
        "message": msg,
        "batch_id": batch_id,
        "status": update["status"],
        "dual_approval_required": needs_dual,
        "aba_generated": "aba_file_content" in update,
    }


@router.post("/batches/{batch_id}/second-approve", response_model=BatchSecondApprovalResponse)
async def second_approve_payment_batch(
        batch_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # Second approver on a payment batch. Same narrowing as the first.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Second approval for high-value ABA batches (GAP-FIN-011).

    Only allowed when batch is in 'pending_second_approval' status.
    The second approver must be a different person from the first approver.
    """
    current_user = _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/batches/{batch_id}/second-approve")
    db = _get_db()

    batch = await db.trust_ledger_batches.find_one({
        "_id": ObjectId(batch_id),
        "building_id": building_id,
    })
    if not batch:
        raise HTTPException(404, "Payment batch not found.")
    if batch.get("status") != "pending_second_approval":
        raise HTTPException(400,
                            f"Batch status is '{batch['status']}'; second approval only applies to 'pending_second_approval' batches.")
    if batch.get("approved_by") == current_user["id"]:
        raise HTTPException(403, "The second approver must be a different person from the first approver.")

    now = _now_iso()
    update: dict = {
        "status": "approved",
        "second_approved_by": current_user["id"],
        "second_approved_at": now,
    }
    _generate_aba(batch, update)

    await db.trust_ledger_batches.update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": update},
    )

    asyncio.create_task(create_audit_log(
        action="payment_batch_second_approved",
        resource_type="payment_batch",
        resource_id=batch_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "total_amount": batch.get("total_amount"),
            "first_approver": batch.get("approved_by"),
        },
        building_id=building_id,
    ))

    return {
        "message": "Second approval complete. Batch approved.",
        "batch_id": batch_id,
        "status": "approved",
        "aba_generated": "aba_file_content" in update,
    }


@router.get("/batches/{batch_id}/aba")
async def download_aba_file(
        batch_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # An ABA file carries supplier BSB and account numbers in the clear.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Download the ABA file for an approved batch."""
    _require_finance_role(current_user)
    db = _get_db()

    batch = await db.trust_ledger_batches.find_one({
        "_id": ObjectId(batch_id),
        "building_id": building_id,
    })
    if not batch:
        raise HTTPException(404, "Payment batch not found.")

    aba_b64 = batch.get("aba_file_content")
    if not aba_b64:
        raise HTTPException(404, "ABA file not yet generated. Approve the batch first.")

    return {
        "batch_id": batch_id,
        "filename": f"payment_batch_{batch_id}.aba",
        "content_base64": aba_b64,
        "total_amount": batch.get("total_amount"),
        "payment_date": batch.get("payment_date"),
    }


# ─── Immutable Trust Ledger Endpoints (Phase C) ──────────────────────────────
# POST  /trust/ledger           — post a single entry (manager/admin)
# GET   /trust/ledger           — paginated list with optional filters
# GET   /trust/ledger/balance   — current fund balance from ledger
# POST  /trust/ledger/reversal  — correct an entry with a REVERSAL

from models.trust_accounting import (
    TrustLedgerCreateRequest,
    TrustLedgerReversalRequest,
    TrustLedgerEntryResponse,
)
from typing import List


@router.get("/ledger")
async def list_trust_ledger(
        fund_type: Optional[str] = None,
        entry_type: Optional[str] = None,
        lot_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return paginated trust ledger entries (most recent first)."""
    _require_finance_role(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id, "is_reversed": False}
    if fund_type:
        query["fund_type"] = fund_type
    if entry_type:
        query["entry_type"] = entry_type
    if lot_id:
        query["lot_id"] = lot_id
    if from_date or to_date:
        date_filter: dict = {}
        if from_date:
            date_filter["$gte"] = from_date
        if to_date:
            date_filter["$lte"] = to_date
        query["effective_date"] = date_filter

    total = await db.trust_ledger_entries.count_documents(query)
    skip = (page - 1) * page_size
    entries = await db.trust_ledger_entries.find(
        query, {"_id": 1, **{k: 1 for k in TrustLedgerEntryResponse.__fields__}}
    ).sort("effective_date", -1).skip(skip).limit(page_size).to_list(page_size)

    for e in entries:
        e["id"] = str(e.pop("_id"))
        amt = e.get("amount_cents", 0)
        e["amount_display"] = f"${amt / 100:,.2f}" if amt >= 0 else f"-${abs(amt) / 100:,.2f}"

    return {"total": total, "page": page, "page_size": page_size, "entries": entries}


@router.get("/ledger/balance")
async def get_ledger_balance(
        fund_type: Optional[str] = None,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return current fund balance(s) computed from the immutable ledger."""
    _require_finance_role(current_user)
    db = _get_db()

    match: dict = {"building_id": building_id, "is_reversed": False}
    if fund_type:
        match["fund_type"] = fund_type

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$fund_type",
            "balance_cents": {"$sum": "$amount_cents"},
            "entry_count": {"$sum": 1},
            "latest_date": {"$max": "$effective_date"},
        }},
    ]
    balances = await db.trust_ledger_entries.aggregate(pipeline).to_list(10)
    result = []
    for b in balances:
        cents = b["balance_cents"]
        result.append({
            "fund_type": b["_id"],
            "balance_cents": cents,
            "balance_display": f"${cents / 100:,.2f}",
            "entry_count": b["entry_count"],
            "as_at": b["latest_date"],
        })
    return {"building_id": building_id, "balances": result}


@router.post("/ledger", status_code=201, response_model=LedgerEntryCreateResponse)
async def post_trust_ledger_entry(
        payload: TrustLedgerCreateRequest,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Post a single new trust ledger entry. Requires strata_manager or admin role."""
    _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/ledger")
    db = _get_db()

    now = datetime.now(timezone.utc).isoformat()
    entry = payload.model_dump()
    entry.update({
        "building_id": building_id,
        "running_balance_cents": None,
        "reversal_of_entry_id": None,
        "is_reversed": False,
        "posted_by": current_user["id"],
        "created_at": now,
    })
    result = await db.trust_ledger_entries.insert_one(entry)

    asyncio.create_task(create_audit_log(
        action="trust_ledger_entry_posted",
        resource_type="trust_ledger_entry",
        resource_id=str(result.inserted_id),
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"entry_type": payload.entry_type, "amount_cents": payload.amount_cents},
        building_id=building_id,
    ))

    return {"message": "Entry posted.", "entry_id": str(result.inserted_id)}


@router.post("/ledger/reversal", status_code=201, response_model=LedgerReversalCreateResponse)
async def reverse_trust_ledger_entry(
        payload: TrustLedgerReversalRequest,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Reverse (correct) an existing posted entry.

    Creates a new REVERSAL entry with the opposite amount, then marks the
    original entry as is_reversed=True. The original entry is NOT deleted —
    the full history remains intact for audit purposes.
    """
    _require_finance_role(current_user)
    await _block_v1_write_if_pg_primary(building_id, route="POST /trust/ledger/reversal")
    db = _get_db()

    original = await db.trust_ledger_entries.find_one({
        "_id": ObjectId(payload.original_entry_id),
        "building_id": building_id,
    })
    if not original:
        raise HTTPException(404, "Original entry not found or belongs to another building.")
    if original.get("is_reversed"):
        raise HTTPException(409, "Entry has already been reversed.")

    now = datetime.now(timezone.utc).isoformat()
    reversal = {
        "building_id": building_id,
        "fund_type": original["fund_type"],
        "entry_type": "reversal",
        "amount_cents": -original["amount_cents"],  # opposite sign
        "running_balance_cents": None,
        "effective_date": payload.effective_date,
        "description": f"REVERSAL: {original.get('description', '')} — {payload.reason}",
        "reference": original.get("reference"),
        "lot_id": original.get("lot_id"),
        "batch_id": None,
        "source_type": "reversal",
        "source_id": None,
        "reversal_of_entry_id": payload.original_entry_id,
        "is_reversed": False,
        "posted_by": current_user["id"],
        "created_at": now,
        "metadata": {"reason": payload.reason},
    }
    result = await db.trust_ledger_entries.insert_one(reversal)

    # Mark original as reversed
    await db.trust_ledger_entries.update_one(
        {"_id": ObjectId(payload.original_entry_id)},
        {"$set": {"is_reversed": True, "reversed_at": now,
                  "reversed_by": current_user["id"]}},
    )

    asyncio.create_task(create_audit_log(
        action="trust_ledger_entry_reversed",
        resource_type="trust_ledger_entry",
        resource_id=str(result.inserted_id),
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"original_entry_id": payload.original_entry_id, "reason": payload.reason},
        building_id=building_id,
    ))

    return {
        "message": "Entry reversed.",
        "reversal_entry_id": str(result.inserted_id),
        "original_entry_id": payload.original_entry_id,
    }


# ── GAP-FIN-010: Trust audit export (hash-chain attestation CSV) ───────────────

@router.get("/audit-export")
async def export_trust_audit(
        date_from: Optional[str] = Query(None, description="ISO-8601 date — start of export window"),
        date_to: Optional[str] = Query(None, description="ISO-8601 date — end of export window"),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # Bulk export of the trust ledger.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Export trust ledger entries as a hash-chain attested CSV (GAP-FIN-010).

    Each row carries the SHA-256 hash of its own content plus the previous row's hash,
    forming a tamper-evident chain. Any modification to an exported row invalidates
    all subsequent hashes.

    The Content-Disposition header causes browsers to download the file directly.
    """
    import csv
    import hashlib
    import io
    from fastapi.responses import StreamingResponse

    _require_finance_role(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id, "is_reversed": {"$ne": True}}
    if date_from:
        query.setdefault("effective_date", {})["$gte"] = date_from
    if date_to:
        query.setdefault("effective_date", {})["$lte"] = date_to

    cursor = db.trust_ledger_entries.find(query).sort("effective_date", 1)
    entries = await cursor.to_list(length=5000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "seq", "entry_id", "effective_date", "fund_type", "entry_type",
        "amount_cents", "description", "reference", "posted_by",
        "created_at", "row_hash", "chain_hash",
    ])

    prev_chain = "GENESIS"
    for seq, entry in enumerate(entries, start=1):
        entry_id = str(entry.get("_id", ""))
        row_fields = "|".join([
            str(seq),
            entry_id,
            str(entry.get("effective_date", "")),
            str(entry.get("fund_type", "")),
            str(entry.get("entry_type", "")),
            str(entry.get("amount_cents", 0)),
            str(entry.get("description", "")),
            str(entry.get("reference", "")),
            str(entry.get("posted_by", "")),
            str(entry.get("created_at", "")),
        ])
        row_hash = hashlib.sha256(row_fields.encode()).hexdigest()[:16]
        chain_input = f"{prev_chain}|{row_hash}"
        chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()[:16]
        prev_chain = chain_hash

        writer.writerow([
            seq,
            entry_id,
            entry.get("effective_date", ""),
            entry.get("fund_type", ""),
            entry.get("entry_type", ""),
            entry.get("amount_cents", 0),
            entry.get("description", ""),
            entry.get("reference", ""),
            entry.get("posted_by", ""),
            entry.get("created_at", ""),
            row_hash,
            chain_hash,
        ])

    asyncio.create_task(create_audit_log(
        action="trust_audit_exported",
        resource_type="trust_ledger",
        resource_id=building_id,
        user_id=current_user["id"],
        user_name=current_user.get("full_name", ""),
        details={"date_from": date_from, "date_to": date_to, "row_count": len(entries)},
        building_id=building_id,
    ))

    output.seek(0)
    filename = f"trust_audit_{building_id}_{_now_iso()[:10]}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
