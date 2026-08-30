"""
GST & BAS (Business Activity Statement) router.

# @featuretrace:gst-bas — GST/BAS worksheet calc: output tax (levies) + ITCs (expenses) + GST-free items. Does NOT read financial_transactions — OCR-confirmed invoice GST is excluded (see GAP-FIN-007).
# Layer: router
# Data flow: GSTBASLedgerPage.jsx → GET /gst/* → annual_levies + expense_transactions + water_bills + council_rates (building-scoped).
# Related: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
#           backend/services/gst_service.py
#           backend/models/gst_bas.py
# Collection: annual_levies, expense_transactions, water_bills, council_rates

Provides a read-only GST ledger and BAS worksheet for the authorised filing roles
(super_admin, strata_admin, strata_manager).

Australian OC GST rules implemented:
  - Levies (admin + sinking) attract 10% GST when OC is registered
  - Input tax credits claimable on GST-inclusive expenses (electricity, cleaning, etc.)
  - Water (Icon Water ACT) = GST-free supply — no GST, no credits
  - Council rates = GST-free — no GST, no credits
  - Net GST payable = output_tax − input_tax_credits

All data is computed at request time from existing collections. No stored aggregates.
Single source of truth: annual_levies (output tax), expense_transactions (ITCs),
water_bills + council_rates (GST-free informational).

Endpoints:
  GET /gst/settings                                  — building GST config
  GET /gst/output-tax?financial_year=2026            — levy GST breakdown by quarter
  GET /gst/input-tax?financial_year=2026             — claimable ITCs from expense transactions
  GET /gst/gst-free?financial_year=2026              — GST-free items (water + rates)
  GET /gst/summary?financial_year=2026&quarter=Annual — full GST summary
  GET /gst/bas-worksheet?financial_year=2026&quarter=Annual — ATO BAS worksheet
  GET /gst/quarterly-worksheets?financial_year=2026  — all 4 quarterly BAS worksheets
  GET /gst/income-tax-prep?financial_year=2026        — annual income tax preparation
  GET /gst/export/pdf?financial_year=2026            — PDF export for filing

Auth: super_admin, strata_admin, strata_manager only.
"""

from __future__ import annotations

import calendar
import io
import logging
import math
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from database import db
from models.gst_bas import (
    BASWorksheet,
    GSTCategoryBreakdown,
    GSTFreeSection,
    GSTIncomeTaxPrep,
    GSTInputTaxResponse,
    GSTLineItem,
    GSTOutputTaxRow,
    GSTQuarterlySummary,
    GSTQuarterlyWorksheetsResponse,
    GSTSettings,
    GSTSummary,
)
from services.gst_service import get_building_levy_gst_settings
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_building, get_current_user
from utils.finance_helpers import get_levy_proposed_amounts

# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

# ---------------------------------------------------------------------------
# Role guard
# ---------------------------------------------------------------------------

_ALLOWED_ROLES = {"super_admin", "strata_admin", "strata_manager"}


def _effective_role(user: dict) -> str:
    """Generated function header.

    Function: _effective_role
    Path: backend/routers/gst_bas.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return user.get("effective_role") or user.get("role", "guest")


def _require_gst_role(user: dict) -> None:
    """Generated function header.

    Function: _require_gst_role
    Path: backend/routers/gst_bas.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if _effective_role(user) not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail="GST/BAS ledger access requires super_admin, strata_admin, or strata_manager role.",
        )


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _fy_range(year: str, fy_start_month: int = 1) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a full financial year."""
    y = int(year)
    if fy_start_month == 1:
        return f"{y}-01-01", f"{y}-12-31"
    # e.g. July FY: Jul year → Jun (year+1)
    end_month = fy_start_month - 1
    end_year = y + 1
    end_day = calendar.monthrange(end_year, end_month)[1]
    return f"{y}-{fy_start_month:02d}-01", f"{end_year}-{end_month:02d}-{end_day:02d}"


def _quarter_range(year: str, quarter: str, fy_start_month: int = 1) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a quarter within the financial year."""
    y = int(year)
    # Build the 4 quarter start-months from fy_start_month
    starts = [((fy_start_month - 1 + i * 3) % 12) + 1 for i in range(4)]
    idx = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}[quarter]
    sm = starts[idx]
    # Determine the calendar year for this quarter start
    qstart_year = y if sm >= fy_start_month else y + 1
    # End month is start of next quarter minus 1
    next_idx = (idx + 1) % 4
    em = starts[next_idx] - 1 or 12
    qend_year = qstart_year if em >= sm else qstart_year + 1
    end_day = calendar.monthrange(qend_year, em)[1]
    return f"{qstart_year}-{sm:02d}-01", f"{qend_year}-{em:02d}-{end_day:02d}"


def _quarter_label(period_start: str) -> str:
    """Derive Q1–Q4 label from a period start ISO date."""
    month = int(period_start[5:7])
    return {1: "Q1", 4: "Q2", 7: "Q3", 10: "Q4"}.get(month, "Q?")


# ---------------------------------------------------------------------------
# Internal data fetch helpers
# ---------------------------------------------------------------------------

async def _get_settings(building_id: str) -> dict:
    """Generated function header.

    Function: _get_settings
    Path: backend/routers/gst_bas.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await get_general_settings_or_default(building_id, {"_id": 0})


async def _get_unit_numbers(building_id: str) -> list[str]:
    """Return all unit_number strings for the building (for water_bill join)."""
    docs = await db.units.find(
        {"building_id": building_id},
        {"unit_number": 1, "_id": 0},
    ).to_list(500)
    return [d["unit_number"] for d in docs if d.get("unit_number")]


async def _fetch_annual_levy(building_id: str, year: str) -> dict:
    """Fetch the annual_levies document for building + year."""
    doc = await db.annual_levies.find_one(
        {"building_id": building_id, "year": year},
        {"_id": 0},
    )
    return doc or {}


async def _fetch_expense_itcs(
        building_id: str, financial_year: str,
        date_gte: Optional[str] = None, date_lte: Optional[str] = None,
        gst_rate: float = 0.10,
) -> tuple[float, list[GSTLineItem]]:
    """
    Return (itc_total, line_items) from GST-inclusive expense transactions.

    Optionally filter by date range for quarterly sub-totals.

    GST derivation: all expense transactions are marked is_gst_inclusive=True,
    meaning the stored amount already includes GST. When gst_amount is not
    explicitly stored (imported as 0.0), it is derived on the fly as:
        gst_amount = amount × rate / (1 + rate)   e.g. amount / 11 for 10% GST
    This covers both legacy imports (gst_amount=0) and future imports where
    gst_amount is properly stored at import time.

    Returns (0.0, []) immediately if the building is not GST-registered (gst_rate=0).
    """
    if gst_rate == 0.0:
        return 0.0, []

    query: dict = {
        "building_id": building_id,
        "financial_year": financial_year,
        "is_gst_inclusive": True,
    }
    if date_gte or date_lte:
        date_filter: dict = {}
        if date_gte:
            date_filter["$gte"] = date_gte
        if date_lte:
            date_filter["$lte"] = date_lte
        query["date"] = date_filter

    docs = await db.expense_transactions.find(query, {"_id": 0}).sort("date", 1).to_list(2000)
    items: list[GSTLineItem] = []
    itc_total = 0.0
    divisor = 1 + gst_rate  # e.g. 1.10 for 10% GST
    for d in docs:
        amount = float(d.get("amount") or 0)
        stored_gst = float(d.get("gst_amount") or 0)
        # Use stored value when present (covers positive expenses and negative credit notes).
        # Derive from GST-inclusive amount only when no stored value exists.
        if stored_gst != 0:
            gst_amt = stored_gst
        else:
            gst_amt = round(amount * gst_rate / divisor, 2)  # = amount / 11 for 10%
        amount_ex = round(amount - gst_amt, 2)
        amount_inc = amount  # already inclusive
        items.append(GSTLineItem(
            date=d.get("date"),
            description=d.get("description") or d.get("notes"),
            supplier_name=d.get("supplier_name"),
            invoice_number=d.get("invoice_number"),
            category_name=d.get("category_name"),
            fund_type=d.get("fund_type"),
            amount_ex_gst=amount_ex,
            gst_amount=gst_amt,
            amount_inc_gst=amount_inc,
            is_gst_free=False,
        ))
        itc_total += gst_amt
    return round(itc_total, 2), items


async def _fetch_water_total(
        unit_numbers: list[str], fy_start: str, fy_end: str,
) -> tuple[float, list[GSTLineItem]]:
    """Aggregate water bill amounts for the building in the FY (GST-free)."""
    if not unit_numbers:
        return 0.0, []
    docs = await db.water_bills.find(
        {
            "unit_number": {"$in": unit_numbers},
            "billing_period_start": {"$gte": fy_start},
            "billing_period_end": {"$lte": fy_end},
        },
        {"_id": 0},
    ).sort("billing_period_start", 1).to_list(500)
    items = [
        GSTLineItem(
            date=d.get("billing_period_start"),
            description=f"Water bill {d.get('quarter', '')} — Unit {d.get('unit_number', '')}",
            supplier_name="Icon Water (ACT)",
            amount_ex_gst=float(d.get("amount") or 0),
            gst_amount=0.0,
            amount_inc_gst=float(d.get("amount") or 0),
            is_gst_free=True,
        )
        for d in docs
    ]
    total = round(sum(float(d.get("amount") or 0) for d in docs), 2)
    return total, items


async def _fetch_council_rates_total(
        building_id: str, financial_year: str,
) -> tuple[float, list[GSTLineItem]]:
    """Aggregate council rates for all units in building for the FY (GST-free)."""
    y = int(financial_year)
    # Council rates use "YYYY-YY" format (ACT financial year Jul–Jun)
    fy_labels = [f"{y}-{str(y + 1)[2:]}", f"{y - 1}-{str(y)[2:]}"]
    docs = await db.council_rates.find(
        {"building_id": building_id, "financial_year": {"$in": fy_labels}},
        {"_id": 0},
    ).to_list(500)
    items = [
        GSTLineItem(
            date=f"{financial_year}-07-01",
            description=f"Council rates — Unit {d.get('unit_number', '')}",
            supplier_name="ACT Revenue Office",
            amount_ex_gst=float(d.get("total_rates") or 0),
            gst_amount=0.0,
            amount_inc_gst=float(d.get("total_rates") or 0),
            is_gst_free=True,
        )
        for d in docs
    ]
    total = round(sum(float(d.get("total_rates") or 0) for d in docs), 2)
    return total, items


async def _fetch_gst_inclusive_expenses_total(
        building_id: str, financial_year: str,
        date_gte: Optional[str] = None, date_lte: Optional[str] = None,
) -> float:
    """Sum of total amount (inc-GST) for all GST-inclusive expense transactions."""
    query: dict = {
        "building_id": building_id,
        "financial_year": financial_year,
        "is_gst_inclusive": True,
    }
    if date_gte or date_lte:
        date_filter: dict = {}
        if date_gte:
            date_filter["$gte"] = date_gte
        if date_lte:
            date_filter["$lte"] = date_lte
        query["date"] = date_filter
    agg = await db.expense_transactions.aggregate([
        {"$match": query},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)
    return round(float((agg[0]["total"] if agg else 0)), 2)


# ---------------------------------------------------------------------------
# Output tax (levy GST) computation
# ---------------------------------------------------------------------------

def _compute_output_tax_rows(
        levy_doc: dict,
        gst_config: dict,
        year: str,
        fy_start_month: int = 1,
) -> list[GSTOutputTaxRow]:
    """Build quarterly output tax rows from the annual levy document."""
    admin_ex, sinking_ex = get_levy_proposed_amounts(levy_doc)
    total_ex = round(admin_ex + sinking_ex, 2)
    rate = gst_config["effective_gst_rate"]
    total_output_tax = round(total_ex * rate, 2)

    rows: list[GSTOutputTaxRow] = []
    q_output_tax_base = round(total_output_tax / 4, 2)
    q_admin_base = round(admin_ex / 4, 2)
    q_sinking_base = round(sinking_ex / 4, 2)

    for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"]):
        start, end = _quarter_range(year, q, fy_start_month)
        # Q4 absorbs rounding remainders
        if i == 3:
            q_admin = round(admin_ex - q_admin_base * 3, 2)
            q_sinking = round(sinking_ex - q_sinking_base * 3, 2)
            q_gst = round(total_output_tax - q_output_tax_base * 3, 2)
        else:
            q_admin = q_admin_base
            q_sinking = q_sinking_base
            q_gst = q_output_tax_base
        q_total_ex = round(q_admin + q_sinking, 2)
        rows.append(GSTOutputTaxRow(
            quarter=q,
            period_start=start,
            period_end=end,
            admin_levy_ex_gst=q_admin,
            sinking_levy_ex_gst=q_sinking,
            total_levy_ex_gst=q_total_ex,
            gst_rate=rate,
            gst_amount=q_gst,
            total_levy_inc_gst=round(q_total_ex + q_gst, 2),
        ))
    return rows


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/gst/settings", response_model=GSTSettings)
async def get_gst_settings(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> GSTSettings:
    """Return the building's GST registration status and rate."""
    _require_gst_role(current_user)
    cfg = await get_building_levy_gst_settings(building_id)
    return GSTSettings(
        gst_registered=cfg["gst_registered"],
        levy_gst_rate=cfg["levy_gst_rate"],
        effective_gst_rate=cfg["effective_gst_rate"],
        gst_label=cfg["gst_label"],
        gst_multiplier=cfg["gst_multiplier"],
    )


@router.get("/gst/output-tax", response_model=list[GSTOutputTaxRow])
async def get_output_tax(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> list[GSTOutputTaxRow]:
    """
    Return quarterly output tax (GST collected on levies) for the financial year.

    Source: annual_levies collection (ex-GST fund totals).
    GST config: site_settings.gst_registered + levy_gst_rate.
    """
    _require_gst_role(current_user)
    settings_doc = await _get_settings(building_id)
    fy_start_month = int(settings_doc.get("financial_year_start_month") or 1)
    gst_config = await get_building_levy_gst_settings(building_id, settings_doc)
    levy_doc = await _fetch_annual_levy(building_id, financial_year)
    # Annual row
    admin_ex, sinking_ex = get_levy_proposed_amounts(levy_doc)
    total_ex = round(admin_ex + sinking_ex, 2)
    rate = gst_config["effective_gst_rate"]
    total_gst = round(total_ex * rate, 2)
    fy_start, fy_end = _fy_range(financial_year, fy_start_month)
    annual_row = GSTOutputTaxRow(
        quarter="Annual",
        period_start=fy_start,
        period_end=fy_end,
        admin_levy_ex_gst=admin_ex,
        sinking_levy_ex_gst=sinking_ex,
        total_levy_ex_gst=total_ex,
        gst_rate=rate,
        gst_amount=total_gst,
        total_levy_inc_gst=round(total_ex + total_gst, 2),
    )
    quarterly = _compute_output_tax_rows(levy_doc, gst_config, financial_year, fy_start_month)
    return quarterly + [annual_row]


@router.get("/gst/input-tax", response_model=GSTInputTaxResponse)
async def get_input_tax(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> GSTInputTaxResponse:
    """
    Return itemised input tax credits (ITCs) from GST-inclusive expense transactions.

    Source: expense_transactions where is_gst_inclusive=True.
    gst_amount is used when stored; derived as amount/11 when not set at import.
    Items grouped by category_name in the response.
    """
    _require_gst_role(current_user)
    gst_config = await get_building_levy_gst_settings(building_id)
    rate = gst_config["effective_gst_rate"]
    itc_total, items = await _fetch_expense_itcs(building_id, financial_year, gst_rate=rate)
    # Group by category
    groups: dict[str, list] = {}
    for item in items:
        key = item.category_name or "Uncategorised"
        groups.setdefault(key, []).append(item)
    category_totals = {k: round(sum(i.gst_amount for i in v), 2) for k, v in groups.items()}
    return GSTInputTaxResponse(
        financial_year=financial_year,
        itc_total=itc_total,
        line_items=items,
        by_category=[
            GSTCategoryBreakdown(category=k, itc_total=category_totals[k], items=groups[k])
            for k in sorted(groups.keys())
        ],
    )


@router.get("/gst/gst-free", response_model=GSTFreeSection)
async def get_gst_free(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> GSTFreeSection:
    """
    Return GST-free items for the year: Icon Water bills + ACT council rates.

    No input tax credits are available on these items.
    """
    _require_gst_role(current_user)
    settings_doc = await _get_settings(building_id)
    fy_start_month = int(settings_doc.get("financial_year_start_month") or 1)
    fy_start, fy_end = _fy_range(financial_year, fy_start_month)
    unit_numbers = await _get_unit_numbers(building_id)

    water_total, water_items = await _fetch_water_total(unit_numbers, fy_start, fy_end)
    rates_total, rates_items = await _fetch_council_rates_total(building_id, financial_year)

    return GSTFreeSection(
        water_bills=water_items,
        water_total=water_total,
        council_rates=rates_items,
        council_rates_total=rates_total,
        gst_free_total=round(water_total + rates_total, 2),
    )


@router.get("/gst/summary", response_model=GSTSummary)
async def get_gst_summary(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        quarter: str = Query("Annual", description="Annual | Q1 | Q2 | Q3 | Q4"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> GSTSummary:
    """
    Full GST summary for the financial year or a single quarter.

    For quarter=Annual: includes quarterly sub-summaries.
    net_gst_payable = output_tax − itc_total
      positive = OC owes ATO
      negative = ATO owes OC (refund)
    """
    _require_gst_role(current_user)
    settings_doc = await _get_settings(building_id)
    fy_start_month = int(settings_doc.get("financial_year_start_month") or 1)
    gst_config = await get_building_levy_gst_settings(building_id, settings_doc)
    levy_doc = await _fetch_annual_levy(building_id, financial_year)
    unit_numbers = await _get_unit_numbers(building_id)

    if quarter == "Annual":
        fy_start, fy_end = _fy_range(financial_year, fy_start_month)
    else:
        fy_start, fy_end = _quarter_range(financial_year, quarter, fy_start_month)

    # Output tax
    admin_ex, sinking_ex = get_levy_proposed_amounts(levy_doc)
    total_levy_ex = round(admin_ex + sinking_ex, 2)
    rate = gst_config["effective_gst_rate"]

    if quarter == "Annual":
        output_tax = round(total_levy_ex * rate, 2)
        levy_inc = round(total_levy_ex + output_tax, 2)
    else:
        # Quarterly split — equal portions, Q4 absorbs rounding remainder
        q_output_tax_base = round(total_levy_ex * rate / 4, 2)
        q_levy_ex_base = round(total_levy_ex / 4, 2)
        idx = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}[quarter]
        if idx == 3:
            q_levy_ex = round(total_levy_ex - q_levy_ex_base * 3, 2)
            output_tax = round(total_levy_ex * rate - q_output_tax_base * 3, 2)
        else:
            q_levy_ex = q_levy_ex_base
            output_tax = q_output_tax_base
        total_levy_ex = q_levy_ex
        levy_inc = round(q_levy_ex + output_tax, 2)

    # Input tax credits
    itc_total, itc_items = await _fetch_expense_itcs(
        building_id, financial_year,
        date_gte=fy_start, date_lte=fy_end,
        gst_rate=rate,
    )

    # GST-free items
    water_total, _ = await _fetch_water_total(unit_numbers, fy_start, fy_end)
    rates_total, _ = await _fetch_council_rates_total(building_id, financial_year)
    gst_free_total = round(water_total + rates_total, 2)

    net = round(output_tax - itc_total, 2)

    # Quarterly sub-summaries for annual view
    quarterly: list[GSTQuarterlySummary] = []
    if quarter == "Annual":
        output_rows = _compute_output_tax_rows(levy_doc, gst_config, financial_year, fy_start_month)
        for row in output_rows:
            q_itc, _ = await _fetch_expense_itcs(
                building_id, financial_year,
                date_gte=row.period_start, date_lte=row.period_end,
                gst_rate=rate,
            )
            q_water, _ = await _fetch_water_total(unit_numbers, row.period_start, row.period_end)
            quarterly.append(GSTQuarterlySummary(
                quarter=row.quarter,
                period_start=row.period_start,
                period_end=row.period_end,
                levy_ex_gst=row.total_levy_ex_gst,
                levy_inc_gst=row.total_levy_inc_gst,
                output_tax=row.gst_amount,
                itc_total=q_itc,
                water_total=q_water,
                council_rates_total=0.0,
                gst_free_total=q_water,
                net_gst_payable=round(row.gst_amount - q_itc, 2),
            ))

    return GSTSummary(
        financial_year=financial_year,
        quarter=quarter,
        period_start=fy_start,
        period_end=fy_end,
        gst_registered=gst_config["gst_registered"],
        effective_gst_rate=rate,
        levy_ex_gst=total_levy_ex,
        levy_inc_gst=levy_inc,
        output_tax=output_tax,
        itc_total=itc_total,
        itc_line_items=itc_items,
        water_total=water_total,
        council_rates_total=rates_total,
        gst_free_total=gst_free_total,
        net_gst_payable=net,
        quarterly=quarterly,
    )


@router.get("/gst/bas-worksheet", response_model=BASWorksheet)
async def get_bas_worksheet(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        quarter: str = Query("Annual", description="Annual | Q1 | Q2 | Q3 | Q4"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> BASWorksheet:
    """
    ATO BAS worksheet for the period.

    Field mapping (ATO GST return):
      G1  = Total sales including GST (levy income inc-GST)
      G2  = Export sales (always 0.0 for a residential OC)
      G3  = GST-free sales (0.0 — water/rates are GST-free PURCHASES, not sales)
      G10 = Capital purchases inc GST (0.0 — not separately tracked)
      G11 = Non-capital purchases inc GST
      1A  = GST on sales (output tax)
      1B  = GST credits (input tax credits)
      Net = 1A − 1B
    """
    _require_gst_role(current_user)
    summary = await get_gst_summary(
        financial_year=financial_year,
        quarter=quarter,
        current_user=current_user,
        building_id=building_id,
    )
    g11 = await _fetch_gst_inclusive_expenses_total(
        building_id, financial_year,
        date_gte=summary.period_start, date_lte=summary.period_end,
    )
    notes = [
        "Water (Icon Water ACT) and council rates are GST-free PURCHASES — "
        "they are excluded from G11 and no ITCs are claimable.",
        "G3 is 0.0: water/rates are GST-free purchases, not GST-free sales. "
        "For a residential OC there are no GST-free sales.",
        "G10 (capital purchases) is 0.0 unless sinking-fund capital works are "
        "separately tracked as expense transactions.",
    ]
    if not summary.gst_registered:
        notes.insert(
            0,
            "WARNING: OC is NOT registered for GST. All BAS fields are zero. "
            "No GST is collected on levies and no input tax credits are claimable.",
        )

    return BASWorksheet(
        financial_year=financial_year,
        quarter=quarter,
        period_start=summary.period_start,
        period_end=summary.period_end,
        G1=summary.levy_inc_gst,
        G2=0.0,
        G3=0.0,
        G10=0.0,
        G11=g11,
        field_1A=summary.output_tax,
        field_1B=summary.itc_total,
        net_9=summary.net_gst_payable,
        gst_registered=summary.gst_registered,
        notes=notes,
    )


@router.get("/gst/quarterly-worksheets", response_model=GSTQuarterlyWorksheetsResponse)
async def get_quarterly_worksheets(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> GSTQuarterlyWorksheetsResponse:
    """Return all four quarterly BAS worksheets for the financial year."""
    _require_gst_role(current_user)
    worksheets = []
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        ws = await get_bas_worksheet(
            financial_year=financial_year,
            quarter=q,
            current_user=current_user,
            building_id=building_id,
        )
        worksheets.append(ws)
    annual = await get_bas_worksheet(
        financial_year=financial_year,
        quarter="Annual",
        current_user=current_user,
        building_id=building_id,
    )
    return GSTQuarterlyWorksheetsResponse(financial_year=financial_year, quarterly=worksheets, annual=annual)


@router.get("/gst/income-tax-prep", response_model=GSTIncomeTaxPrep)
async def get_income_tax_prep(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> GSTIncomeTaxPrep:
    """
    Annual income tax preparation summary.

    Non-mutual income (bank interest on trust accounts) is assessable for income tax.
    Mutual income (levies) is exempt under the mutuality principle.

    Source: trust_transactions_v2 with transaction_type = 'interest_income'.
    File ATO Company Tax Return (NAT 0656) with CPA if total_assessable_income > 0.
    """
    _require_gst_role(current_user)
    settings_doc = await _get_settings(building_id)
    fy_start_month = int(settings_doc.get("financial_year_start_month") or 1)
    fy_start, fy_end = _fy_range(financial_year, fy_start_month)

    # Fetch levy income for mutual income (exempt) reference
    levy_doc = await _fetch_annual_levy(building_id, financial_year)
    admin_ex, sinking_ex = get_levy_proposed_amounts(levy_doc)
    levy_ex = round(admin_ex + sinking_ex, 2)

    # Query trust interest income — non-mutual, assessable
    trust_docs = await db.trust_transactions_v2.find(
        {
            "building_id": building_id,
            "transaction_type": "interest_income",
            "date": {"$gte": fy_start, "$lte": fy_end},
        },
        {"_id": 0},
    ).sort("date", 1).to_list(500)

    interest_items: list[GSTLineItem] = []
    interest_total = 0.0
    for doc in trust_docs:
        amount = float(doc.get("amount") or doc.get("credit_amount") or 0)
        interest_items.append(GSTLineItem(
            date=doc.get("date"),
            description=doc.get("description") or "Trust account interest",
            supplier_name=doc.get("account_name") or "Trust Account",
            invoice_number=doc.get("reference"),
            category_name="Interest Income",
            fund_type=doc.get("fund_type"),
            amount_ex_gst=amount,
            gst_amount=0.0,
            amount_inc_gst=amount,
            is_gst_free=True,
        ))
        interest_total += amount
    interest_total = round(interest_total, 2)

    total_assessable = round(interest_total, 2)
    net_taxable = round(total_assessable, 2)

    return GSTIncomeTaxPrep(
        financial_year=financial_year,
        levy_income_ex_gst=levy_ex,
        interest_income=interest_total,
        other_non_mutual_income=0.0,
        total_assessable_income=total_assessable,
        management_fees=0.0,
        other_deductible_expenses=0.0,
        total_deductions=0.0,
        net_taxable_income=net_taxable,
        items=interest_items,
    )


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

def _dollar(v) -> str:
    """Generated function header.

    Function: _dollar
    Path: backend/routers/gst_bas.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _generate_bas_pdf(
        summary: GSTSummary,
        worksheet: BASWorksheet,
        building_name: str,
        quarterly_worksheets: list[BASWorksheet],
        building_abn: str = "",
        income_tax_prep: "GSTIncomeTaxPrep | None" = None,
) -> bytes:
    """Generate a BAS summary PDF using reportlab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    BRAND = colors.HexColor("#2F4F4F")
    LIGHT = colors.HexColor("#f8fafc")

    def _tbl_style(header_bg="#2F4F4F"):
        """Generated function header.

        Function: _tbl_style
        Path: backend/routers/gst_bas.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=BRAND, fontSize=16, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=BRAND, fontSize=12, spaceAfter=4)
    body = styles["Normal"]
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#64748b"))

    story = []

    # Cover
    story.append(Paragraph(f"GST & BAS Summary — {summary.financial_year}", h1))
    story.append(Paragraph(building_name, body))
    abn_display = f" | ABN: {building_abn}" if building_abn else ""
    story.append(Paragraph(
        f"Period: {summary.period_start} to {summary.period_end} | "
        f"Generated: {datetime.now(timezone.utc).strftime('%d %b %Y')}{abn_display}",
        small,
    ))
    story.append(Spacer(1, 0.3 * cm))

    reg_text = "REGISTERED" if summary.gst_registered else "NOT REGISTERED"
    reg_color_str = "#16a34a" if summary.gst_registered else "#dc2626"
    story.append(Paragraph(
        f'GST Status: <font color="{reg_color_str}">{reg_text}</font> '
        f"| Rate: {summary.effective_gst_rate * 100:.0f}%",
        body,
    ))

    if not summary.gst_registered:
        story.append(Paragraph(
            "OC is NOT registered for GST. No GST is collected on levies "
            "and no input tax credits are claimable.",
            ParagraphStyle("warn", parent=body, textColor=colors.HexColor("#dc2626")),
        ))

    story.append(Spacer(1, 0.5 * cm))

    # Summary cards table
    story.append(HRFlowable(width="100%", thickness=1.5, color=BRAND))
    story.append(Paragraph("Annual GST Summary", h2))
    summary_data = [
        ["Item", "Amount (AUD)", "Notes"],
        ["Levy Income (ex-GST)", _dollar(summary.levy_ex_gst), "Admin + Sinking fund levies"],
        ["GST on Levies (Output Tax — 1A)", _dollar(summary.output_tax), "Collected from owners"],
        ["Levy Income (inc-GST)", _dollar(summary.levy_inc_gst), "Total owner-payable levies"],
        ["Input Tax Credits (1B)", _dollar(summary.itc_total), "Claimable on GST-inclusive expenses"],
        ["Net GST Payable (Net 9)", _dollar(summary.net_gst_payable),
         "Positive = owes ATO; Negative = refund"],
        ["GST-Free Items (informational)", _dollar(summary.gst_free_total),
         "Water + council rates — no credits"],
    ]
    tbl = Table(summary_data, colWidths=[8 * cm, 4.5 * cm, 5.5 * cm])
    ts = _tbl_style()
    ts.add("BACKGROUND", (0, 5), (-1, 5), colors.HexColor("#dbeafe"))
    ts.add("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold")
    tbl.setStyle(ts)
    story.append(tbl)
    story.append(Spacer(1, 0.5 * cm))

    # BAS Worksheet
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
    story.append(Paragraph("BAS Worksheet — ATO GST Return Fields", h2))
    ws = worksheet
    bas_data = [
        ["ATO Field", "Description", "Amount (AUD)"],
        ["G1", "Total sales including GST", _dollar(ws.G1)],
        ["G2", "Export sales", _dollar(ws.G2)],
        ["G3", "Other GST-free sales", _dollar(ws.G3)],
        ["G10", "Capital purchases (inc GST)", _dollar(ws.G10)],
        ["G11", "Non-capital purchases (inc GST)", _dollar(ws.G11)],
        ["1A", "GST on sales (Output Tax)", _dollar(ws.field_1A)],
        ["1B", "GST credits (Input Tax Credits)", _dollar(ws.field_1B)],
        ["Net 9", "Net GST payable / (refundable)", _dollar(ws.net_9)],
    ]
    bas_tbl = Table(bas_data, colWidths=[2.5 * cm, 9 * cm, 6.5 * cm])
    bts = _tbl_style()
    bts.add("BACKGROUND", (0, 6), (-1, 7), colors.HexColor("#fef9c3"))
    bts.add("BACKGROUND", (0, 8), (-1, 8), colors.HexColor("#dbeafe"))
    bts.add("FONTNAME", (0, 6), (-1, 8), "Helvetica-Bold")
    bas_tbl.setStyle(bts)
    story.append(bas_tbl)
    story.append(Spacer(1, 0.3 * cm))
    for note in ws.notes:
        story.append(Paragraph(f"• {note}", small))
    story.append(Spacer(1, 0.5 * cm))

    # Quarterly summary
    if quarterly_worksheets:
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
        story.append(Paragraph("Quarterly BAS Summary", h2))
        q_data = [["Quarter", "Period", "G1 (Sales incl GST)", "1A (Output Tax)", "1B (ITCs)", "Net 9"]]
        for qws in quarterly_worksheets:
            q_data.append([
                qws.quarter,
                f"{qws.period_start[:7]} – {qws.period_end[:7]}",
                _dollar(qws.G1),
                _dollar(qws.field_1A),
                _dollar(qws.field_1B),
                _dollar(qws.net_9),
            ])
        q_tbl = Table(q_data, colWidths=[1.5 * cm, 4.5 * cm, 3.5 * cm, 3 * cm, 3 * cm, 2.5 * cm])
        q_tbl.setStyle(_tbl_style())
        story.append(q_tbl)
        story.append(Spacer(1, 0.5 * cm))

    # Input tax credits
    if summary.itc_line_items:
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
        story.append(Paragraph("Input Tax Credits — Itemised", h2))
        itc_data = [["Date", "Supplier", "Category", "Amount (ex-GST)", "GST", "Amount (inc-GST)"]]
        for item in summary.itc_line_items:
            itc_data.append([
                (item.date or "")[:10],
                (item.supplier_name or "")[:25],
                (item.category_name or "")[:20],
                _dollar(item.amount_ex_gst),
                _dollar(item.gst_amount),
                _dollar(item.amount_inc_gst),
            ])
        itc_data.append(["", "", "TOTAL ITCs", "", _dollar(summary.itc_total), ""])
        itc_tbl = Table(itc_data, colWidths=[2.5 * cm, 4.5 * cm, 3.5 * cm, 3 * cm, 3 * cm, 3 * cm])
        its = _tbl_style()
        its.add("FONTNAME", (0, len(itc_data) - 1), (-1, len(itc_data) - 1), "Helvetica-Bold")
        its.add("BACKGROUND", (0, len(itc_data) - 1), (-1, len(itc_data) - 1), LIGHT)
        itc_tbl.setStyle(its)
        story.append(itc_tbl)
        story.append(Spacer(1, 0.5 * cm))

    # GST-free note
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
    story.append(Paragraph("GST-Free Items (for reference)", h2))
    story.append(Paragraph(
        f"Water bills (Icon Water ACT): {_dollar(summary.water_total)} — GST-free supply. "
        f"Council rates: {_dollar(summary.council_rates_total)} — GST-free. "
        "No input tax credits available on these items.",
        body,
    ))
    story.append(Spacer(1, 0.5 * cm))

    # Income tax preparation section
    if income_tax_prep and income_tax_prep.total_assessable_income > 0:
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
        story.append(Paragraph("Annual Income Tax Preparation — ATO NAT 0656", h2))
        story.append(Paragraph(
            "Owners Corporations are treated as public companies for income tax. "
            "Mutual income (levies) is EXEMPT. Non-mutual income (bank interest, common area rentals) "
            "is ASSESSABLE. File Company Tax Return (NAT 0656) with your CPA by 31 October "
            "if assessable income exists.",
            small,
        ))
        story.append(Spacer(1, 0.3 * cm))
        it_data = [
            ["Income Type", "Tax Treatment", "Amount (AUD)"],
            ["Strata Levies (Admin + Sinking)", "Mutual — EXEMPT", _dollar(income_tax_prep.levy_income_ex_gst)],
        ]
        if income_tax_prep.interest_income > 0:
            it_data.append([
                "Trust Account Interest Income",
                "Non-Mutual — ASSESSABLE",
                _dollar(income_tax_prep.interest_income),
            ])
        if income_tax_prep.other_non_mutual_income > 0:
            it_data.append([
                "Other Non-Mutual Income",
                "Non-Mutual — ASSESSABLE",
                _dollar(income_tax_prep.other_non_mutual_income),
            ])
        it_data.append(
            ["Total Assessable Income", "File NAT 0656 if > $0", _dollar(income_tax_prep.total_assessable_income)])
        it_tbl = Table(it_data, colWidths=[8 * cm, 5 * cm, 5 * cm])
        its_style = _tbl_style()
        its_style.add("FONTNAME", (0, len(it_data) - 1), (-1, len(it_data) - 1), "Helvetica-Bold")
        its_style.add("BACKGROUND", (0, len(it_data) - 1), (-1, len(it_data) - 1), colors.HexColor("#fef3c7"))
        it_tbl.setStyle(its_style)
        story.append(it_tbl)
        story.append(Spacer(1, 0.5 * cm))

    # Disclaimer
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        "<b>Disclaimer:</b> This document is generated from data recorded in the Strata Management "
        "Portal and is provided as a preparation aid only. It is NOT a lodged BAS or official ATO "
        "document. Always verify figures with your registered tax agent or BAS agent before lodging. "
        "The Owners Corporation and its managing agent accept no liability for omissions or "
        "inaccuracies. Consult a registered tax/BAS agent for advice.",
        small,
    ))

    doc.build(story)
    return buf.getvalue()


@router.get("/gst/export/pdf")
async def export_bas_pdf(
        financial_year: str = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
) -> StreamingResponse:
    """
    Generate and download a GST/BAS summary PDF for the financial year.

    Includes: annual summary, BAS worksheet, quarterly breakdown,
    itemised input tax credits, and GST-free items note.
    """
    _require_gst_role(current_user)
    summary = await get_gst_summary(
        financial_year=financial_year,
        quarter="Annual",
        current_user=current_user,
        building_id=building_id,
    )
    worksheet = await get_bas_worksheet(
        financial_year=financial_year,
        quarter="Annual",
        current_user=current_user,
        building_id=building_id,
    )
    quarterly_worksheets = []
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        qws = await get_bas_worksheet(
            financial_year=financial_year,
            quarter=q,
            current_user=current_user,
            building_id=building_id,
        )
        quarterly_worksheets.append(qws)

    settings_doc = await _get_settings(building_id)
    building_name = settings_doc.get("building_name", "Owners Corporation")
    building_abn = settings_doc.get("building_abn") or ""

    # Income tax prep — best-effort (don't fail PDF if trust data is missing)
    income_tax_prep = None
    try:
        income_tax_prep = await get_income_tax_prep(
            financial_year=financial_year,
            current_user=current_user,
            building_id=building_id,
        )
    except Exception as exc:
        logger.warning("gst_bas: income_tax_prep fetch failed for %s; PDF will omit section: %s", financial_year, exc)

    pdf_bytes = _generate_bas_pdf(
        summary, worksheet, building_name, quarterly_worksheets,
        building_abn=building_abn,
        income_tax_prep=income_tax_prep,
    )
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="GST_BAS_{financial_year}.pdf"'},
    )
