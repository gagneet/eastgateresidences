"""
Tax Summary Service — generates a consolidated annual tax summary PDF for owners.
Includes levies paid, council rates & land tax, and water/sewerage bills.

Data sources:
  - db.levy_payments         — individual confirmed levy payments (receipts)
  - db.unit_levy_ledger      — annual admin/sinking fund totals per unit
  - db.annual_levies         — levy rates and payment schedule for the year
  - db.council_rates         — ACT council rates and land tax for the unit
  - db.water_bills           — paid water/sewerage bills for the unit
  - db.units                 — unit details (owner name, lot number, UOE)
"""
import html as html_lib
import io
from datetime import datetime, timezone

import logging
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)

from database import db
from services.owner_service import get_owner_info
from services.settings_service import get_general_settings_or_default

logger = logging.getLogger(__name__)


def _now_str() -> str:
    """Generated function header.

    Function: _now_str
    Path: backend/services/tax_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).strftime("%d %B %Y at %H:%M UTC")


def _year_str(financial_year: str) -> str:
    """Extract the 4-digit start year from various year formats.
    '2026', '2026-27', '2026-2027' → '2026'
    """
    return str(financial_year).split("-")[0].strip()


def _council_fy(year: str) -> str:
    """Convert portal year '2026' → ACT council FY '2025-26'."""
    y = int(year)
    return f"{y - 1}-{str(y)[2:]}"


def _fy_dates(year: str):
    """Return (fy_start, fy_end) ISO date strings for water bill filtering.
    ACT FY runs 1 Jul (year-1) → 30 Jun (year). We use a wider window
    (1 Oct year-1 → 30 Sep year) to cover strata levy quarter alignment.
    """
    y = int(year)
    return f"{y - 1}-07-01", f"{y}-06-30"


def _safe(val, fallback="—") -> str:
    """Generated function header.

    Function: _safe
    Path: backend/services/tax_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(val) if val is not None else fallback


def _dollar(val) -> str:
    """Generated function header.

    Function: _dollar
    Path: backend/services/tax_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return f"${float(val):,.2f}"
    except Exception:
        return "—"


def _tbl_style(header_bg="#f1f5f9") -> TableStyle:
    """Generated function header.

    Function: _tbl_style
    Path: backend/services/tax_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ])


def _total_row_style(table_style: TableStyle, row_idx: int) -> TableStyle:
    """Add bold formatting to a totals row."""
    new_commands = list(table_style._cmds) + [
        ("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold"),
        ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#e0f2fe")),
        ("LINEABOVE", (0, row_idx), (-1, row_idx), 0.8, colors.HexColor("#0369a1")),
    ]
    return TableStyle(new_commands)


async def generate_tax_summary(unit_number: str, financial_year: str, building_id: str) -> bytes:
    """
    Generate a consolidated tax summary PDF for a unit for the given financial year. Scoped to building.

    Args:
        unit_number:    e.g. 'TH017'
        financial_year: e.g. '2026' or '2026-27' — only the start year is used
        building_id:    isolation ID
    """
    year = _year_str(financial_year)
    council_fy = _council_fy(year)
    fy_start, fy_end = _fy_dates(year)

    # Fetch building settings for branding
    b_settings = await get_general_settings_or_default(building_id, {"_id": 0})
    building_name = b_settings.get("building_name", "Strata Building")
    plan_number = b_settings.get("plan_number", "")
    plan_address = b_settings.get("address", "")

    # ── 1. Fetch all data in parallel-style ───────────────────────────────────
    unit_doc = await db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "year": year}, {"_id": 0}
    )
    annual_levy = await db.annual_levies.find_one(
        {"building_id": building_id, "year": year}, {"_id": 0}
    )
    levy_payments = await db.levy_payments.find(
        {"building_id": building_id, "unit_number": unit_number, "year": year, "status": "confirmed"},
        {"_id": 0}
    ).sort("confirmed_at", 1).to_list(100)
    council_rates = await db.council_rates.find_one(
        {"building_id": building_id, "unit_number": unit_number, "financial_year": council_fy}, {"_id": 0}
    )
    # Try exact year match first, fall back to any available
    if not council_rates:
        council_rates = await db.council_rates.find_one(
            {"building_id": building_id, "unit_number": unit_number}, {"_id": 0}
        )
    water_bills = await db.water_bills.find(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "status": "paid",
            "$or": [
                {"billing_period_start": {"$gte": fy_start, "$lte": fy_end}},
                {"due_date": {"$gte": fy_start, "$lte": fy_end}},
            ],
        },
        {"_id": 0}
    ).sort("due_date", 1).to_list(20)

    # ── 2. Build derived values ───────────────────────────────────────────────
    _owner = await get_owner_info(unit_number, building_id)
    owner_name = _owner["owner_name"] or "Owner"
    owner_name_b = _owner.get("owner_name_b")
    lot_number = (unit_doc or {}).get("lot_number") or "—"
    uoe = (unit_doc or {}).get("unit_entitlement") or (ledger or {}).get("uoe") or "—"
    unit_type = (unit_doc or {}).get("unit_type") or (ledger or {}).get("property_type") or "—"

    # From ledger
    admin_levied = float((ledger or {}).get("admin_levied", 0))
    admin_paid = float((ledger or {}).get("admin_paid", 0))
    sinking_levied = float((ledger or {}).get("sinking_levied", 0))
    sinking_paid = float((ledger or {}).get("sinking_paid", 0))
    total_levied = float((ledger or {}).get("total_levied", 0))
    total_paid_levy = float((ledger or {}).get("total_paid", 0))
    net_balance = float((ledger or {}).get("net_balance", 0))

    # From annual_levy rates
    admin_q = float((annual_levy or {}).get("admin_levy_per_uoe_quarterly", 0))
    sinking_q = float((annual_levy or {}).get("sinking_levy_per_uoe_quarterly", 0))
    payment_schedule = (annual_levy or {}).get("payment_schedule", [])

    # From council rates
    total_rates = float((council_rates or {}).get("total_rates", 0))
    land_tax_total = float((council_rates or {}).get("land_tax_total", 0))
    land_tax_applicable = bool((council_rates or {}).get("land_tax_total", 0))

    # From water bills
    total_water = sum(float(b.get("amount", 0)) for b in water_bills)

    # Grand total
    grand_total = total_paid_levy + total_rates + (land_tax_total if land_tax_applicable else 0) + total_water

    # ── 3. Build PDF ──────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2.5 * cm,
        title=f"Annual Tax Summary — Unit {unit_number} — FY {year}",
    )
    styles = getSampleStyleSheet()
    DARK = colors.HexColor("#1e293b")
    BRAND = colors.HexColor("#2F4F4F")
    ACCENT = colors.HexColor("#0369a1")

    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=20,
                        textColor=BRAND, spaceAfter=4, leading=24)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12,
                        textColor=BRAND, spaceBefore=14, spaceAfter=6,
                        borderPad=4, leading=16)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9,
                          textColor=DARK, leading=13)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8,
                           textColor=colors.HexColor("#6b7280"), leading=11)
    bold_body = ParagraphStyle("BoldBody", parent=body, fontName="Helvetica-Bold")
    disclaimer = ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=7.5,
                                textColor=colors.HexColor("#9ca3af"), leading=11)

    story = []

    # ── Cover header ─────────────────────────────────────────────────────────
    story.append(Paragraph(html_lib.escape(building_name), h1))
    story.append(Paragraph(
        f"Owners Corporation Plan No. {html_lib.escape(plan_number)}  ·  {html_lib.escape(plan_address)}",
        ParagraphStyle("Sub", parent=small, textColor=colors.HexColor("#475569"))
    ))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=BRAND))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(
        f"ANNUAL TAX SUMMARY — Financial Year {year}",
        ParagraphStyle("Title2", parent=styles["Normal"], fontSize=15,
                       fontName="Helvetica-Bold", textColor=BRAND, spaceAfter=6)
    ))

    # Unit info block
    owner_display = owner_name
    if owner_name_b:
        owner_display += f" &amp; {html_lib.escape(owner_name_b)}"

    info_data = [
        ["Unit Number", html_lib.escape(unit_number),
         "Lot Number", html_lib.escape(lot_number)],
        ["Owner(s)", html_lib.escape(owner_display),
         "Unit Type", html_lib.escape(str(unit_type)).title()],
        ["Unit Entitlement", f"{uoe} UOE",
         "Generated", _now_str()],
    ]
    info_tbl = Table(info_data, colWidths=[3.5 * cm, 6.5 * cm, 3.5 * cm, 4.5 * cm])
    info_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), DARK),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "This document summarises property-related payments recorded in the East Gate Residences "
        "Strata Management Portal for the financial year shown above. Retain for tax records.",
        small
    ))
    story.append(Spacer(1, 0.5 * cm))

    # ── Section 1: Owners Corporation Levy Summary ────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")))
    story.append(Paragraph("1. Owners Corporation Levy Summary", h2))

    if ledger:
        levy_rate_data = [
            ["Fund", "Annual Rate (per UOE)", "Quarterly Rate", "UOE", "Annual Levy"],
        ]
        if admin_q and uoe and uoe != "—":
            levy_rate_data.append([
                "Administrative Fund",
                f"${admin_q * 4:,.4f}",
                f"${admin_q:,.4f}",
                str(uoe),
                _dollar(admin_levied),
            ])
        if sinking_q and uoe and uoe != "—":
            levy_rate_data.append([
                "Sinking Fund (Capital Works)",
                f"${sinking_q * 4:,.4f}",
                f"${sinking_q:,.4f}",
                str(uoe),
                _dollar(sinking_levied),
            ])
        if len(levy_rate_data) > 1:
            levy_rate_data.append(["", "", "", "TOTAL LEVIED", _dollar(total_levied)])
            rate_tbl = Table(levy_rate_data, colWidths=[5.5 * cm, 3.5 * cm, 3 * cm, 2 * cm, 4 * cm])
            ts = _tbl_style()
            ts = _total_row_style(ts, len(levy_rate_data) - 1)
            rate_tbl.setStyle(ts)
            story.append(rate_tbl)
            story.append(Spacer(1, 0.3 * cm))

        # Payment schedule from annual levy
        if payment_schedule:
            story.append(Paragraph("Payment Schedule", bold_body))
            sched_data = [["Quarter", "Due Date", "Admin", "Sinking", "Quarter Total"]]
            uoe_num = uoe if isinstance(uoe, (int, float)) else 0
            for q in payment_schedule:
                qtr = q.get("quarter", "—")
                due = q.get("due_date", "—")
                a_amt = admin_q * uoe_num if admin_q and uoe_num else 0
                s_amt = sinking_q * uoe_num if sinking_q and uoe_num else 0
                sched_data.append([qtr, due, _dollar(a_amt), _dollar(s_amt), _dollar(a_amt + s_amt)])
            sched_tbl = Table(sched_data, colWidths=[2.5 * cm, 3.5 * cm, 3.5 * cm, 3.5 * cm, 5 * cm])
            sched_tbl.setStyle(_tbl_style())
            story.append(sched_tbl)
            story.append(Spacer(1, 0.3 * cm))

        # Fund totals summary
        fund_data = [
            ["", "Levied", "Paid", "Balance"],
            ["Administrative Fund", _dollar(admin_levied), _dollar(admin_paid),
             _dollar(admin_levied - admin_paid)],
            ["Sinking Fund", _dollar(sinking_levied), _dollar(sinking_paid),
             _dollar(sinking_levied - sinking_paid)],
            ["TOTAL", _dollar(total_levied), _dollar(total_paid_levy),
             _dollar(net_balance)],
        ]
        fund_tbl = Table(fund_data, colWidths=[5.5 * cm, 4 * cm, 4 * cm, 4.5 * cm])
        ts2 = _tbl_style()
        ts2 = _total_row_style(ts2, 3)
        fund_tbl.setStyle(ts2)
        story.append(KeepTogether([Paragraph("Fund Balance Summary", bold_body), fund_tbl]))
    else:
        story.append(Paragraph(
            f"No levy ledger data found for Unit {unit_number} for year {year}. "
            "Please contact the strata manager if you believe this is incorrect.",
            body
        ))

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 2: Individual Levy Payment Records ────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")))
    story.append(Paragraph("2. Individual Levy Payment Records", h2))
    if levy_payments:
        pay_data = [["Date", "Quarter", "Receipt #", "Method", "Notes", "Amount"]]
        total_receipts = 0.0
        for p in levy_payments:
            dt = (p.get("confirmed_at") or p.get("created_at") or "")[:10]
            amt = float(p.get("amount", 0))
            total_receipts += amt
            notes = p.get("notes") or ""
            if len(notes) > 40:
                notes = notes[:38] + "…"
            pay_data.append([
                dt,
                _safe(p.get("quarter")),
                _safe(p.get("receipt_number")),
                _safe(p.get("payment_method", "")).upper(),
                notes,
                _dollar(amt),
            ])
        pay_data.append(["", "", "", "", "TOTAL CONFIRMED", _dollar(total_receipts)])
        pay_tbl = Table(pay_data, colWidths=[2.5 * cm, 2 * cm, 3.5 * cm, 2 * cm, 5 * cm, 3 * cm])
        ts3 = _tbl_style()
        ts3 = _total_row_style(ts3, len(pay_data) - 1)
        pay_tbl.setStyle(ts3)
        story.append(pay_tbl)
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            "Note: 'Confirmed' means payment has been verified by the strata manager. "
            "DEFT and BPAY payments may appear after a 1–3 business day processing delay.",
            small
        ))
    else:
        story.append(Paragraph(
            f"No confirmed levy payments found for Unit {unit_number} for year {year}.",
            body
        ))

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 3: Council Rates & Land Tax ──────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")))
    story.append(Paragraph("3. ACT Council Rates & Land Tax", h2))
    if council_rates:
        cr_fy = council_rates.get("financial_year", council_fy)
        story.append(Paragraph(
            f"ACT Rates Financial Year: <b>{html_lib.escape(str(cr_fy))}</b>  ·  "
            f"AUV: <b>${float(council_rates.get('auv', 0)):,.0f}</b>  ·  "
            f"Unit Entitlement: <b>{council_rates.get('unit_entitlement_pct', '—')}%</b>",
            body
        ))
        story.append(Spacer(1, 0.3 * cm))

        # General rates quarterly breakdown
        rates_data = [["Quarter", "Rates Amount", "Notes"]]
        qtrs = [("Q1 (Aug)", "rates_q1"), ("Q2 (Nov)", "rates_q2"),
                ("Q3 (Feb)", "rates_q3"), ("Q4 (May)", "rates_q4")]
        for label, field in qtrs:
            amt = council_rates.get(field)
            if amt is not None:
                rates_data.append([label, _dollar(amt), "General Rates"])
        rates_data.append(["TOTAL", _dollar(total_rates), ""])

        rates_tbl = Table(rates_data, colWidths=[3.5 * cm, 4 * cm, 10.5 * cm])
        ts4 = _tbl_style()
        ts4 = _total_row_style(ts4, len(rates_data) - 1)
        rates_tbl.setStyle(ts4)
        story.append(KeepTogether([Paragraph("General Rates", bold_body), rates_tbl]))
        story.append(Spacer(1, 0.3 * cm))

        # Land tax
        if land_tax_applicable:
            lt_data = [["Quarter", "Land Tax", "Notes"]]
            ltqs = [("Q1", "land_tax_q1"), ("Q2", "land_tax_q2"),
                    ("Q3", "land_tax_q3"), ("Q4", "land_tax_q4")]
            for label, field in ltqs:
                amt = council_rates.get(field)
                if amt is not None:
                    lt_data.append([label, _dollar(amt), "Land Tax (investor/non-owner-occupied)"])
            lt_data.append(["TOTAL", _dollar(land_tax_total), ""])
            lt_tbl = Table(lt_data, colWidths=[3.5 * cm, 4 * cm, 10.5 * cm])
            ts5 = _tbl_style()
            ts5 = _total_row_style(ts5, len(lt_data) - 1)
            lt_tbl.setStyle(ts5)
            story.append(KeepTogether([Paragraph("Land Tax", bold_body), lt_tbl]))
            story.append(Spacer(1, 0.2 * cm))
            story.append(Paragraph(
                "Land Tax applies to investment properties (non-owner-occupied). "
                "Owner-occupied properties may be exempt — confirm with ACT Revenue Office.",
                small
            ))
    else:
        story.append(Paragraph(
            f"No council rates data found for Unit {unit_number} (ACT FY {council_fy}). "
            "Council rates data is sourced from the ACT Revenue Office and may not be "
            "available for all units. Contact the strata manager for assistance.",
            body
        ))

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 4: Water & Sewerage Bills ────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")))
    story.append(Paragraph("4. Water & Sewerage Utility Bills (Paid)", h2))
    if water_bills:
        wb_data = [["Quarter", "Billing Period", "Due Date", "Usage (kL)", "Amount"]]
        for b in water_bills:
            bp_start = (b.get("billing_period_start") or "")[:7]
            bp_end = (b.get("billing_period_end") or "")[:7]
            bp = f"{bp_start} – {bp_end}" if bp_start and bp_end else _safe(b.get("due_date"))
            wb_data.append([
                _safe(b.get("quarter")),
                bp,
                _safe(b.get("due_date")),
                _safe(b.get("water_usage_kl")),
                _dollar(b.get("amount")),
            ])
        wb_data.append(["", "", "", "TOTAL PAID", _dollar(total_water)])
        wb_tbl = Table(wb_data, colWidths=[3 * cm, 4.5 * cm, 3 * cm, 2.5 * cm, 5 * cm])
        ts6 = _tbl_style()
        ts6 = _total_row_style(ts6, len(wb_data) - 1)
        wb_tbl.setStyle(ts6)
        story.append(wb_tbl)
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            "Water and sewerage charges are issued by Icon Water and administered "
            "through the Owners Corporation. Usage charges are based on metered consumption. "
            "<b>Note: Icon Water (ACT) utility charges are GST-free</b> — no GST is included "
            "in the amounts shown above.",
            small
        ))
    else:
        story.append(Paragraph(
            f"No paid water bills found for Unit {unit_number} in the period "
            f"{fy_start} to {fy_end}.",
            body
        ))

    story.append(Spacer(1, 0.6 * cm))

    # ── Section 5: Grand Total Summary ────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1.5, color=BRAND))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("5. Grand Total Summary", h2))

    summary_data = [
        ["Category", "Amount", "Notes"],
        ["Owners Corporation Levies (confirmed paid)",
         _dollar(total_paid_levy),
         f"Admin ${admin_paid:,.2f} + Sinking ${sinking_paid:,.2f}"],
        ["Council Rates (general)",
         _dollar(total_rates) if council_rates else "Not available",
         f"ACT FY {council_fy}"],
        ["Land Tax",
         _dollar(land_tax_total) if land_tax_applicable else "N/A (owner-occupied / not applicable)",
         "Investment properties only"],
        ["Water & Sewerage Bills",
         _dollar(total_water) if water_bills else "Not available",
         "Icon Water — paid bills in period"],
        ["GRAND TOTAL (property-related payments)",
         _dollar(grand_total),
         ""],
    ]
    sum_tbl = Table(summary_data, colWidths=[7 * cm, 4 * cm, 7 * cm])
    ts7 = _tbl_style(header_bg="#dbeafe")
    ts7 = _total_row_style(ts7, len(summary_data) - 1)
    sum_tbl.setStyle(ts7)
    story.append(sum_tbl)

    story.append(Spacer(1, 1.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"<b>Disclaimer:</b> This document summarises payments recorded in the {html_lib.escape(building_name)} Strata "
        "Management Portal and is provided for convenience only. It is NOT an official tax invoice or ATO "
        "document. Always cross-reference with your bank statements, ACT Revenue Office notices, and Icon "
        "Water bills before lodging a tax return. The Owners Corporation and Silverfox Technologies accept "
        "no liability for omissions or inaccuracies. Consult a registered tax agent for tax advice.",
        disclaimer
    ))

    doc.build(story)
    return buf.getvalue()
