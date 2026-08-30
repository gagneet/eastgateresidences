# @featuretrace:levy-financial-year-reports — Report endpoints for Aging and General Ledger.
# Layer: router
# Data flow: ReportsPage -> /finance/reports/* -> finance_reporting_service -> finance.* PostgreSQL tables (building-scoped).
# Related: backend/services/finance_reporting_service.py
"""Finance reporting routes for Levy Financial Year reports."""
from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from services.finance_reporting_service import (
    REPORT_CATALOG,
    get_report_catalog,
    get_report_payload,
    get_aged_receivables_report,
    get_general_ledger_report,
    report_to_csv,
    report_to_docx,
    report_to_pdf,
    report_to_xlsx,
)
from services.capability_registry import can
from utils.auth import effective_role, get_current_building, get_current_user
from utils.permissions import get_effective_feature_access, get_user_permissions

router = APIRouter(prefix="/finance/reports")

REPORT_ROLES = {"super_admin", "strata_admin", "strata_manager", "admin_staff", "ec_member"}
GST_REPORT_ROLES = {"super_admin", "strata_admin", "strata_manager"}
EXPORT_MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _require_report_access(current_user: dict) -> None:
    role = effective_role(current_user)
    if role not in REPORT_ROLES:
        raise HTTPException(status_code=403, detail="Financial report access is restricted for this role.")


async def _can_access_report_type(report_type: str, current_user: dict, building_id: str) -> bool:
    role = effective_role(current_user)
    if report_type == "financial-statement":
        # Mirrors the linked `/finance/report/{year}` route: feature enabled,
        # finance-view permission granted, and building.finance.view capability
        # present for the resolved tenant scope. This prevents the workbench
        # catalog from advertising a direct link that the target route will 403.
        return (
            await get_effective_feature_access(current_user, "finance_intelligence")
            and get_user_permissions(current_user).can_view_finances
            and can(current_user, "building.finance.view", {"building_id": building_id})
        )
    if report_type == "gst-bas-statement":
        # Mirrors gst_bas.py::_ALLOWED_ROLES. Deliberately not broadened to EC
        # or admin_staff here because GST/BAS filing visibility is a separate
        # management-only access decision.
        return role in GST_REPORT_ROLES
    return role in REPORT_ROLES


async def _require_report_type_access(report_type: str, current_user: dict, building_id: str) -> None:
    _require_report_access(current_user)
    if not await _can_access_report_type(report_type, current_user, building_id):
        raise HTTPException(
            status_code=403,
            detail=f"{REPORT_CATALOG[report_type]['title']} is not available for this role.",
        )


@router.get("/catalog")
async def finance_report_catalog(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
):
    _require_report_access(current_user)
    reports = []
    for report in get_report_catalog():
        if await _can_access_report_type(report["report_type"], current_user, building_id):
            reports.append(report)
    return {"reports": reports}


@router.get("/aged-receivables")
async def aged_receivables_report(
    financial_year: str = Query(..., min_length=4, max_length=9),
    as_of: date | None = Query(None),
    fund_id: str | None = Query(None),
    lot_number: str | None = Query(None),
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
):
    _require_report_access(current_user)
    return await get_aged_receivables_report(
        building_id=building_id,
        financial_year=financial_year,
        as_of=as_of,
        fund_id=fund_id,
        lot_number=lot_number,
    )


@router.get("/general-ledger")
async def general_ledger_report(
    financial_year: str = Query(..., min_length=4, max_length=9),
    fund_id: str | None = Query(None),
    account_code: str | None = Query(None),
    lot_number: str | None = Query(None),
    posted_only: bool = Query(True),
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
):
    _require_report_access(current_user)
    return await get_general_ledger_report(
        building_id=building_id,
        financial_year=financial_year,
        fund_id=fund_id,
        account_code=account_code,
        lot_number=lot_number,
        posted_only=posted_only,
    )


@router.get("/payload/{report_type}")
async def finance_report_payload(
    report_type: str,
    financial_year: str = Query(..., min_length=4, max_length=9),
    as_of: date | None = Query(None),
    fund_id: str | None = Query(None),
    account_code: str | None = Query(None),
    lot_number: str | None = Query(None),
    posted_only: bool = Query(True),
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
):
    if report_type not in REPORT_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown report type: {report_type}")
    await _require_report_type_access(report_type, current_user, building_id)
    return await get_report_payload(
        report_type=report_type,
        building_id=building_id,
        financial_year=financial_year,
        as_of=as_of,
        fund_id=fund_id,
        account_code=account_code,
        lot_number=lot_number,
        posted_only=posted_only,
    )


@router.get("/{report_type}.{export_format}")
async def finance_report_export(
    report_type: str,
    export_format: str,
    financial_year: str = Query(..., min_length=4, max_length=9),
    as_of: date | None = Query(None),
    fund_id: str | None = Query(None),
    account_code: str | None = Query(None),
    lot_number: str | None = Query(None),
    posted_only: bool = Query(True),
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(get_current_user),
):
    if report_type not in REPORT_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown report type: {report_type}")
    await _require_report_type_access(report_type, current_user, building_id)
    if export_format not in EXPORT_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {export_format}")
    if export_format not in REPORT_CATALOG[report_type].get("formats", []):
        raise HTTPException(
            status_code=400,
            detail=f"{export_format.upper()} export is not enabled for {REPORT_CATALOG[report_type]['title']}.",
        )
    if REPORT_CATALOG[report_type].get("external_link"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{REPORT_CATALOG[report_type]['title']} is generated by an existing StrataOS module. "
                f"Use its external_link from /finance/reports/payload/{report_type} directly rather than this "
                "generic export pipeline, which only applies to the canonical Postgres report contracts."
            ),
        )

    report = await get_report_payload(
        report_type=report_type,
        building_id=building_id,
        financial_year=financial_year,
        as_of=as_of,
        fund_id=fund_id,
        account_code=account_code,
        lot_number=lot_number,
        posted_only=posted_only,
    )

    try:
        if export_format == "csv":
            content = report_to_csv(report).encode("utf-8")
        elif export_format == "xlsx":
            content = report_to_xlsx(report)
        elif export_format == "pdf":
            content = report_to_pdf(report)
        else:
            content = report_to_docx(report)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename = f"{building_id}_{report_type}_{financial_year}.{export_format}"
    return Response(
        content=content,
        media_type=EXPORT_MEDIA_TYPES[export_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
