# @featuretrace:scoped-capability-access — Finance-intelligence routes enforce building-scoped capabilities.
# Layer: router
# Data flow: finance dashboard → /finance/* → capability_registry + permission overrides → finance services/collections.
# Related: backend/services/capability_registry.py
#          tests/backend/test_finance_intelligence.py
"""
Finance Intelligence Router — Forecasting, Anomaly Detection, Health Scoring

Endpoints:
  POST /finance/forecast/generate?year=      super_admin, strata_admin
  GET  /finance/forecast?year=               all finance roles
  GET  /finance/anomalies?year=              all finance roles
  POST /finance/anomalies/scan?year=         super_admin, strata_admin, strata_manager
  PUT  /finance/anomalies/{id}/resolve       super_admin, strata_admin
  GET  /finance/health?year=                 all finance roles
  GET  /finance/lot-summary?year=            management roles only
  GET  /finance/lot-summary/{lot}?year=      management roles + owner (own lot only)
  POST /finance/lot-summary/compute?year=    super_admin, strata_admin, strata_manager
  GET  /finance/report/{year}                all finance roles → PDF StreamingResponse
  POST /finance/documents/upload             super_admin only
  GET  /finance/levy-simulator               super_admin, strata_admin (rate-limited 10/min)
  GET  /finance/cashflow?year=               all finance roles
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo import ReplaceOne
from typing import List, Optional, Dict, Any

from database import db
from models.user import UserRole
from utils.file_scan import scan_upload
from services.anomaly_service import detect_financial_anomalies
# Services — imported lazily to allow graceful degradation if a dep is missing
from services.forecast_service import (
    generate_category_forecast,
    simulate_levy_adjustment,
    generate_cashflow_projection,
    generate_levy_projection,
    generate_sinking_fund_projection,
)
from services.health_service import compute_financial_health
from services.capability_registry import assert_capability_hydrated
from services.lot_finance_service import compute_building_cost_distribution
from services.pdf_ingestion_service import ingest_financial_document
from services.report_service import generate_full_report
from utils.auth import get_current_building
from utils.finance_logger import log_event, timed
from utils.permissions import get_user_permissions, require_feature

# Rate limiter (optional — falls back gracefully if slowapi not installed)
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    _limiter = Limiter(key_func=get_remote_address)
    _RATE_LIMIT = "10/minute"
    _SLOWAPI_OK = True
except ImportError:
    _SLOWAPI_OK = False

router = APIRouter(prefix="")
logger = logging.getLogger(__name__)

# Roles that can VIEW finance intelligence data
_VIEW_ROLES = {UserRole.SUPER_ADMIN, "strata_admin", "strata_manager", UserRole.EC_MEMBER}
# Roles that can MANAGE (generate forecasts, scan anomalies, resolve)
_MANAGE_ROLES = {UserRole.SUPER_ADMIN, "strata_admin", "strata_manager"}
class SinkingFundPlanRowUpdate(BaseModel):
    year: int
    contribution: Optional[float] = Field(None, ge=0)
    expenditure: Optional[float] = Field(None, ge=0)
    categories: Optional[Dict[str, float]] = None
    category_labels: Optional[Dict[str, str]] = None
    is_actual: Optional[bool] = None
    is_major_capital_year: Optional[bool] = None
    opening_balance: Optional[float] = None


class SinkingFundPlanUpdateRequest(BaseModel):
    plan: List[SinkingFundPlanRowUpdate]
    base_opening_balance: Optional[float] = None


class SinkingFundCapitalEvent(BaseModel):
    year: int
    label: str
    description: Optional[str] = ""
    amount: float = Field(..., ge=0)
    severity: str


class SinkingFundCapitalEventsUpdateRequest(BaseModel):
    events: List[SinkingFundCapitalEvent]


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _finance_scope(current_user: dict, building_id: str) -> dict[str, str]:
    # Do not treat the caller's organisation as verified ownership of an
    # arbitrary target building. Organisation inheritance requires a resolver
    # that supplies the target building's independently verified organisation.
    """Generated function header.

    Function: _finance_scope
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {"building_id": building_id}


# Async because it hydrates verified building claims before deciding; the
# synchronous assert_capability() hydrated nothing, so the decision rested on
# the caller's INHERITED building_id claim (their stored default_scheme_id when
# the JWT names no building) rather than their live role assignments. GAP-SEC-014.
async def _require_finance_view(current_user: dict, building_id: str | None = None) -> None:
    """Generated function header.

    Function: _require_finance_view
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")
    target = building_id or current_user.get("building_id") or ""
    await assert_capability_hydrated(current_user, "building.finance.view", _finance_scope(current_user, target))


async def _require_finance_manage(current_user: dict, building_id: str | None = None) -> None:
    """Generated function header.

    Function: _require_finance_manage
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to manage finances")
    target = building_id or current_user.get("building_id") or ""
    await assert_capability_hydrated(current_user, "building.finance.manage", _finance_scope(current_user, target))


async def _require_generate(current_user: dict, building_id: str | None = None) -> None:
    """Generated function header.

    Function: _require_generate
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to generate forecasts")
    target = building_id or current_user.get("building_id") or ""
    await assert_capability_hydrated(current_user, "building.finance.generate", _finance_scope(current_user, target))


async def _require_super_admin(current_user: dict) -> None:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await assert_capability_hydrated(current_user, "platform.security.manage", {"platform_id": "platform"})


async def _require_plan_edit(current_user: dict, building_id: str | None = None) -> None:
    """Generated function header.

    Function: _require_plan_edit
    Path: backend/routers/finance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    target = building_id or current_user.get("building_id") or ""
    await assert_capability_hydrated(current_user, "building.finance.plan.manage", _finance_scope(current_user, target))


# Replaced legacy _check_finance_intelligence_enabled with require_feature("finance_intelligence")


# ─────────────────────────────────────────────────────────────────────────────
# Forecasts
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/finance/forecast/generate")
async def generate_forecast(
        year: str = Query(..., description="Financial year e.g. '2026'"),
        fund_type: Optional[str] = Query(None, description="'administrative' | 'sinking' | null for all"),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Generate expense forecasts for a year.
    Runs all 3 models (linear, inflation, capital_works) per category.
    Super admin and strata admin only.
    """
    await _require_generate(current_user, building_id)
    with timed("forecast_generation", year=year, user_id=current_user.get("id", ""), fund_type=fund_type or "all") as t:
        forecasts = await generate_category_forecast(year, fund_type, building_id)
        t.extra["count"] = len(forecasts)
    return {
        "message": f"Generated {len(forecasts)} forecast(s) for {year}",
        "count": len(forecasts),
        "year": year,
    }


@router.get("/finance/forecast")
async def get_forecasts(
        year: str = Query(...),
        fund_type: Optional[str] = Query(None),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Fetch stored forecasts for a year. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    query: dict = {"financial_year": year, "building_id": building_id}
    if fund_type:
        query["fund_type"] = fund_type
    # No fixed cap — financial_forecasts is a shared collection that can exceed 200 docs
    # across all buildings/years/fund_types (CLAUDE.md footgun #2); this query is already
    # scoped to one building+year[+fund_type], so fetch every matching doc, not a slice.
    forecasts = await db.financial_forecasts.find(query, {"_id": 0}).sort("category", 1).to_list(length=None)
    return {"year": year, "forecasts": forecasts, "count": len(forecasts)}


# ─────────────────────────────────────────────────────────────────────────────
# Anomalies
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/anomalies")
async def get_anomalies(
        year: str = Query(...),
        severity: Optional[str] = Query(None),
        resolved: Optional[bool] = Query(None),
        fund_type: Optional[str] = Query(None),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Fetch anomalies for a year with optional filters. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    query: dict = {"financial_year": year, "building_id": building_id}
    if severity:
        query["severity"] = severity
    if resolved is not None:
        query["resolved"] = resolved
    if fund_type:
        query["fund_type"] = fund_type
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    anomalies = await db.financial_anomalies.find(query, {"_id": 0}).to_list(500)
    anomalies.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 4))
    return {"year": year, "anomalies": anomalies, "count": len(anomalies)}


@router.post("/finance/anomalies/scan")
async def scan_anomalies(
        year: str = Query(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Re-scan for anomalies in the given year.
    Clears unresolved anomalies and re-detects. Scoped to building.
    """
    await _require_finance_manage(current_user, building_id)
    with timed("anomaly_scan", year=year, user_id=current_user.get("id", "")) as t:
        anomalies = await detect_financial_anomalies(year, building_id)
        t.extra["count"] = len(anomalies)
    return {
        "message": f"Scanned and found {len(anomalies)} anomalie(s) for {year}",
        "count": len(anomalies),
        "year": year,
    }


@router.put("/finance/anomalies/{anomaly_id}/resolve")
async def resolve_anomaly(
        anomaly_id: str,
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Mark an anomaly as resolved. Scoped to building.
    Only super_admin and strata_admin can resolve anomalies.
    """
    await _require_generate(current_user, building_id)
    result = await db.financial_anomalies.update_one(
        {"id": anomaly_id, "building_id": building_id},
        {"$set": {"resolved": True, "resolved_at": _now(),
                  "resolved_by": current_user.get("id", "")}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    log_event("anomaly_resolved", anomaly_id=anomaly_id, user_id=current_user.get("id", ""))
    return {"message": "Anomaly resolved", "id": anomaly_id}


# ─────────────────────────────────────────────────────────────────────────────
# Financial Health
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/health")
async def get_financial_health(
        year: str = Query(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Compute and return the financial health score for the given year. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    with timed("health_compute", year=year) as t:
        health = await compute_financial_health(year, building_id)
        t.extra["score"] = health.get("score", 0)
    return health


# ─────────────────────────────────────────────────────────────────────────────
# Lot Financial Summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/lot-summary")
async def get_lot_summaries(
        year: str = Query(...),
        arrears_only: bool = Query(False),
        risk_only: bool = Query(False),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Fetch per-lot financial summaries for a year. Scoped to building.
    Management roles only (super_admin, strata_admin, strata_manager, ec_member).
    """
    await _require_finance_view(current_user, building_id)
    query: dict = {"financial_year": year, "building_id": building_id}
    if arrears_only:
        query["arrears_flag"] = True
    if risk_only:
        query["risk_flag"] = True
    summaries = await db.lot_financial_summary.find(
        query, {"_id": 0}
    ).sort("total_cost", -1).to_list(200)

    if summaries and all(
        not (
            (s.get("total_council") or 0)
            or (s.get("total_water") or 0)
            or (s.get("total_land_tax") or 0)
        )
        for s in summaries
    ):
        year_match = {"$or": [{"year": year}, {"financial_year": year}]}
        rates_count = await db.council_rates.count_documents({"building_id": building_id, **year_match})
        water_count = await db.water_bills.count_documents({"building_id": building_id, **year_match})
        if rates_count or water_count:
            summaries = await compute_building_cost_distribution(year, building_id)

    # ── Fallback: if no cached summaries (or all total_cost=0), derive from
    # unit_levy_ledger (levy assessment amounts, not payment-recorded amounts).
    # This handles the common case where owners pay via DEFT/BPAY externally.
    if not summaries or all(s.get("total_cost", 0) == 0 for s in summaries):
        ledger_entries = await db.unit_levy_ledger.find(
            {"year": year, "building_id": building_id}, {"_id": 0}
        ).to_list(200)
        if ledger_entries:
            # Grace-aware per-unit arrears map (GAP-FIN-040) — canonical, matches
            # /arrears/detail and the building aggregate; never a raw net_balance>0 flag.
            from utils.finance_helpers import get_unit_arrears_map
            arrears_map = await get_unit_arrears_map(building_id, year)
            fallback = []
            for e in ledger_entries:
                admin_levied = e.get("admin_levied", 0) or 0
                sinking_levied = e.get("sinking_levied", 0) or 0
                total_cost = round(admin_levied + sinking_levied, 2)
                uoe = e.get("uoe", 1) or 1
                fallback.append({
                    "lot_number": e.get("lot_number") or e.get("unit_number", ""),
                    "financial_year": year,
                    "total_admin_paid": round(admin_levied, 2),
                    "total_sinking_paid": round(sinking_levied, 2),
                    "special_levies": 0.0,
                    "total_council": 0.0,
                    "total_water": 0.0,
                    "total_land_tax": 0.0,
                    "total_interest": 0.0,
                    "total_cost": total_cost,
                    "cost_per_uoe": round(total_cost / uoe, 2),
                    "arrears_flag": bool(
                        (arrears_map.get(e.get("lot_number") or e.get("unit_number", "")) or {}).get("in_arrears")
                    ),
                    "risk_flag": False,
                    "_source": "levy_ledger_fallback",
                })
            fallback.sort(key=lambda s: s["total_cost"], reverse=True)
            # Apply view-mode filters if requested
            if arrears_only:
                fallback = [s for s in fallback if s["arrears_flag"]]
            if risk_only:
                fallback = [s for s in fallback if s["risk_flag"]]
            summaries = fallback

    return {"year": year, "summaries": summaries, "count": len(summaries)}


@router.get("/finance/lot-summary/{lot_number}")
async def get_lot_summary(
        lot_number: str,
        year: str = Query(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Fetch per-lot summary for a specific unit.
    Management roles see any lot; owners/tenants can only see their own lot.
    """
    user_role = current_user.get("role", "")
    if user_role not in _VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")

    # Ownership restriction: non-management roles can only view their own lot
    if user_role not in _MANAGE_ROLES:
        user_unit = current_user.get("unit_number", "")
        if user_unit != lot_number:
            raise HTTPException(
                status_code=403,
                detail="You can only view your own lot summary"
            )

    summary = await db.lot_financial_summary.find_one(
        {"lot_number": lot_number, "financial_year": year, "building_id": building_id}, {"_id": 0}
    )
    if not summary:
        raise HTTPException(status_code=404, detail=f"No summary for {lot_number} in {year}")
    return summary


@router.post("/finance/lot-summary/compute")
async def compute_lot_summaries(
        year: str = Query(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Recompute true cost of ownership for ALL units in a year.
    Stores results in lot_financial_summary collection. Scoped to building.
    """
    await _require_finance_manage(current_user, building_id)
    summaries = await compute_building_cost_distribution(year, building_id)
    return {
        "message": f"Computed cost distribution for {len(summaries)} unit(s) in {year}",
        "count": len(summaries),
        "year": year,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Report Generation
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/report/{year}")
async def download_financial_report(
        year: str,
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Generate and download the full financial intelligence report as PDF. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    with timed("report_generated", year=year, user_id=current_user.get("id", ""),
               role=current_user.get("role", "")):
        pdf_bytes = await generate_full_report(
            year,
            building_id,
            generated_by_role=current_user.get("role", "unknown"),
        )
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="Failed to generate report")

    filename = f"eastgate_financial_report_{year}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PDF Document Ingestion
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/finance/documents/upload")
async def upload_financial_document(
        year: str = Form(...),
        document_type: str = Form(..., description="'budget' | 'actual' | 'audit' | 'statement'"),
        file: UploadFile = File(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Upload and ingest a financial PDF document.
    Super admin only.
    """
    await _require_super_admin(current_user)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    file_bytes = await file.read()
    await scan_upload(file_bytes, context="pdf", filename=file.filename or "")
    result = await ingest_financial_document(
        file_bytes=file_bytes,
        file_name=file.filename,
        year=year,
        doc_type=document_type,
        uploaded_by=current_user.get("id", ""),
        building_id=building_id
    )
    return result


@router.get("/finance/documents")
async def list_financial_documents(
        year: Optional[str] = Query(None),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    List all uploaded financial documents. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    query: dict = {"building_id": building_id}
    if year:
        query["financial_year"] = year
    docs = await db.financial_documents.find(query, {"_id": 0}).sort("uploaded_at", -1).to_list(100)
    return {"documents": docs, "count": len(docs)}


# ─────────────────────────────────────────────────────────────────────────────
# Levy Simulator — rate-limited to 10/minute, generate roles only
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/levy-simulator")
async def levy_simulator(
        request: Request,
        year: str = Query(...),
        increase_pct: float = Query(
            0.0,
            ge=-5.0,
            le=50.0,
            description="Levy adjustment % (-5.0 to +50.0). Negative = decrease scenario.",
        ),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Simulate the impact of a levy adjustment across all units.
    Only super_admin and strata_admin can run simulations.
    Rate-limited to 10 requests per minute per IP.
    Returns current vs new collection, surplus/deficit, per-unit impacts.
    """
    await _require_generate(current_user, building_id)

    # Apply rate limit if slowapi is available
    if _SLOWAPI_OK:
        from slowapi import Limiter
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded
        try:
            # Use the app-level limiter stored in request.app.state
            app_limiter = request.app.state.limiter
            await app_limiter._check_request_limit(request, endpoint=levy_simulator, limit="10/minute")
        except RateLimitExceeded:
            raise
        except Exception as exc:
            logger.warning("finance_intelligence: rate-limit check failed, allowing request: %s", exc)

    log_event(
        "simulation_attempt",
        user_id=current_user.get("id", ""),
        year=year,
        increase_pct=increase_pct,
        role=current_user.get("role", ""),
    )

    result = await simulate_levy_adjustment(year, increase_pct, building_id)
    return result


@router.get("/finance/levy-projection")
async def get_levy_projection(
        year: str = Query(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Get 3-year levy rate projection for the given year.
    Projects required levy rates per UOE based on inflation model. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    result = await generate_levy_projection(year, building_id)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Cashflow Projection
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/cashflow")
async def get_cashflow_projection(
        year: str = Query(...),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Get monthly cashflow projection for the given year.
    Includes risk month detection (months with negative cumulative balance). Scoped to building.
    """
    await _require_finance_view(current_user, building_id)
    result = await generate_cashflow_projection(year, building_id)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Sinking Fund Inflation-Adjusted Projection
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/sinking-fund-projection")
async def get_sinking_fund_projection(
        inflation_rate: float = Query(0.035, ge=0.01, le=0.10,
                                      description="Annual CPI rate 0.01–0.10 (default 3.5%)"),
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Return the inflation-adjusted 15-year sinking fund projection.

    Applies CPI compounding to each year's base capital works expenditure,
    computes the smoothed flat annual collection needed, and returns the
    year-by-year projected fund reserve.

    Result mirrors the strata manager's model:
      - Annual collection ≈ $106,763 (at 3.5% CPI)
      - Reserve reaches ~$0 at end of 2035 (fully expended)
      - 2029 lowest point ≈ $242,018 (building repaint year)

    Accessible to all finance view roles.
    """
    await _require_finance_view(current_user, building_id)
    result = await generate_sinking_fund_projection(building_id, inflation_rate=inflation_rate)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 15-Year Sinking Fund Capital Works Plan
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/finance/sinking-fund-plan")
async def get_sinking_fund_plan(
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Return the 15-year sinking fund capital works plan (FY 2021-2035).
    Includes annual contributions, expenditures, closing balances,
    per-category breakdown, and capital event milestones.
    All finance view roles can access this endpoint. Scoped to building.
    """
    await _require_finance_view(current_user, building_id)

    # Fetch annual plan rows sorted by year
    rows = await db.sinking_fund_plan.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", 1).to_list(20)

    # Fetch capital events
    events = await db.sinking_fund_capital_events.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", 1).to_list(10)

    if not rows:
        return {
            "plan": [],
            "capital_events": [],
            "summary": {},
            "seeded": False,
        }

    # Compute summary stats
    total_contributions = sum(r.get("contribution", 0) for r in rows)
    total_expenditures = sum(r.get("expenditure", 0) for r in rows)
    closing_2035 = next((r.get("closing_balance", 0) for r in rows if r["year"] == 2035), 0)
    major_years = [r["year"] for r in rows if r.get("is_major_capital_year")]

    # Build category totals across all years
    cat_totals: dict = {}
    for row in rows:
        for cat, amt in row.get("categories", {}).items():
            cat_totals[cat] = round(cat_totals.get(cat, 0) + amt, 2)

    # Category labels (from first row that has them, or rebuild)
    cat_labels: dict = {}
    for row in rows:
        cat_labels.update(row.get("category_labels", {}))

    return {
        "plan": rows,
        "capital_events": events,
        "category_totals": cat_totals,
        "category_labels": cat_labels,
        "summary": {
            "total_contributions": round(total_contributions, 2),
            "total_expenditures": round(total_expenditures, 2),
            "closing_balance_2035": round(closing_2035, 2),
            "major_capital_years": major_years,
            "plan_start": 2021,
            "plan_end": 2035,
            "years_remaining_to_2035": max(0, 2035 - 2026),
        },
        "seeded": True,
    }


@router.put("/finance/sinking-fund-plan")
async def update_sinking_fund_plan(
        payload: SinkingFundPlanUpdateRequest,
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Update the 15-year sinking fund plan (Strata Admin, EC Member, Strata Manager, Super Admin).
    Recomputes opening/closing balances after applying edits. Scoped to building.
    """
    await _require_plan_edit(current_user, building_id)
    if not payload.plan:
        raise HTTPException(status_code=400, detail="Plan data cannot be empty")

    existing_rows = await db.sinking_fund_plan.find({"building_id": building_id}, {"_id": 0}).sort("year", 1).to_list(
        50)
    plan_map: Dict[int, Dict[str, Any]] = {int(r.get("year")): r for r in existing_rows if r.get("year")}

    for row in payload.plan:
        update = row.dict(exclude_unset=True)
        year = int(update["year"])
        base = plan_map.get(year, {"year": year, "categories": {}, "category_labels": {}})
        merged = {**base, **update}
        plan_map[year] = merged

    if not plan_map:
        raise HTTPException(status_code=400, detail="No valid plan rows provided")

    years = sorted(plan_map.keys())
    base_opening = payload.base_opening_balance
    if base_opening is None:
        base_opening = float(plan_map[years[0]].get("opening_balance", 0) or 0)

    now = _now()
    current_year = datetime.now(timezone.utc).year
    running_balance = float(base_opening)
    updated_rows = []

    for year in years:
        row = plan_map[year]
        contribution = float(row.get("contribution", 0) or 0)
        expenditure = float(row.get("expenditure", 0) or 0)
        row["year"] = year
        row["opening_balance"] = round(running_balance, 2)
        closing = running_balance + contribution - expenditure
        row["closing_balance"] = round(closing, 2)
        row["net_position"] = round(row["closing_balance"] - row["opening_balance"], 2)
        row["updated_at"] = now
        if row.get("categories") is None:
            row["categories"] = {}
        if row.get("category_labels") is None:
            row["category_labels"] = {}
        if row.get("plan_id") is None:
            row["plan_id"] = building_id
        if row.get("building_id") is None:
            row["building_id"] = building_id
        if row.get("is_actual") is None:
            row["is_actual"] = year <= current_year
        if row.get("is_major_capital_year") is None:
            row["is_major_capital_year"] = False
        updated_rows.append(row)
        running_balance = closing

    bulk_ops = [
        ReplaceOne({"year": row["year"], "building_id": building_id}, row, upsert=True)
        for row in updated_rows
    ]
    if bulk_ops:
        await db.sinking_fund_plan.bulk_write(bulk_ops)

    await db.capital_shock_risks.delete_many({"building_id": building_id})
    log_event("sinking_fund_plan_updated", user_id=current_user.get("id", ""), count=len(updated_rows))

    return {"message": "Sinking fund plan updated", "count": len(updated_rows)}


@router.put("/finance/sinking-fund-capital-events")
async def update_sinking_fund_capital_events(
        payload: SinkingFundCapitalEventsUpdateRequest,
        current_user: dict = Depends(require_feature("finance_intelligence")),
        building_id: str = Depends(get_current_building)
):
    """
    Replace sinking fund capital event milestones (Strata Admin, EC Member, Strata Manager, Super Admin).
    Scoped to building.
    """
    await _require_plan_edit(current_user, building_id)

    events = payload.events or []
    now = _now()

    # Delete all existing events for this building then insert fresh
    await db.sinking_fund_capital_events.delete_many({"building_id": building_id})

    if events:
        docs = []
        for event in events:
            doc = event.dict()
            doc["building_id"] = building_id
            doc["plan_id"] = building_id
            doc["updated_at"] = now
            docs.append(doc)
        await db.sinking_fund_capital_events.insert_many(docs)

    await db.capital_shock_risks.delete_many({"building_id": building_id})
    log_event("sinking_fund_events_updated", user_id=current_user.get("id", ""), count=len(events))

    return {"message": "Sinking fund capital events updated", "count": len(events)}
