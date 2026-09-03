"""
Arrears Notice Service — generates legally compliant arrears notices.
Citation: Section 83, Unit Titles (Management) Act 2011 (ACT).
"""
import html as html_lib
import io
from datetime import datetime, timezone, date, timedelta

import logging
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage
)

from database import db
from services.owner_service import get_owner_info
from services.settings_service import get_general_settings_or_default
from services.document_branding_service import (
    local_brand_asset_path,
    resolve_document_branding,
)
from utils.finance_helpers import compute_period_due_dates
from utils.helpers import get_current_timestamp, create_audit_log

logger = logging.getLogger(__name__)


def _now_str() -> str:
    """Generated function header.

    Function: _now_str
    Path: backend/services/notice_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).strftime("%d %B %Y")


def _coerce_year(year: str):
    """Return both string and int representations for flexible MongoDB queries."""
    try:
        return str(year), int(year)
    except (ValueError, TypeError):
        return str(year), None


async def generate_arrears_notice(unit_number: str, year: str, user_id: str, user_name: str, building_id: str) -> bytes:
    """
    Generate an Arrears Notice PDF for a specific unit and year. Scoped to building.
    Auto-creates a contact log entry and returns the PDF bytes.
    """
    # Fetch building settings for branding
    b_settings = await get_general_settings_or_default(building_id, {"_id": 0})
    profile = resolve_document_branding(b_settings, building_id)
    building_name = profile["building_name"]
    plan_number = profile["plan_number"]
    strata_manager = profile["strata_management_company"]

    year_str, year_int = _coerce_year(year)

    # 1. Gather Data
    unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    if not unit:
        raise ValueError(f"Unit {unit_number} not found")

    # Try both string and int year to handle type variance in the collection
    ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "levy_year": year_int or year_str}, {"_id": 0}
    )
    if not ledger and year_int is not None:
        # Some docs store year as "year" int field
        ledger = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number, "year": year_int}, {"_id": 0}
        )
    if not ledger:
        ledger = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number, "year": year_str}, {"_id": 0}
        )
    if not ledger:
        # Fallback: latest ledger for this unit
        ledger = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number}, {"_id": 0},
            sort=[("levy_year", -1), ("year", -1)]
        )

    # Get primary owner via canonical resolution (user_units → users, then units fallback).
    _owner = await get_owner_info(unit_number, building_id)
    owner_name = _owner["owner_name"] or unit.get("owner_name", "The Owner")
    owner_email = _owner.get("owner_email")

    # Compute grace-period-aware outstanding amount.
    #
    # Design note (mirrors Debt Recovery Board in routers/finance.py):
    #   • opening_arrears in the ledger is the PRIOR-YEAR carry-forward, already
    #     net of any bank/DEFT payments applied to that prior debt.
    #   • levy_payments (portal/Stripe) records are current-year instalment
    #     payments. Do NOT subtract them from opening_arrears — doing so hides
    #     genuine prior-year debt (e.g. TH015 paid $1,567.97 for Q1/Q2 levy
    #     but still owes $20.23 from FY2025).
    #   • confirmed_paid = ledger.total_paid  (DEFT/bank imports only)
    #   • Current-year levy periods use settings-based due dates (same model
    #     as the board), NOT annual_levies.payment_schedule, because the
    #     payment_schedule contains historical dates (Oct, Jan) already absorbed
    #     into opening_arrears for the current cycle.
    try:
        today = date.today()
        grace_days = int(b_settings.get("grace_period_days", 14))

        # Opening arrears: ledger admin_opening + sinking_opening is the primary source.
        # The unit document often does NOT have this field.
        admin_opening = (ledger.get("admin_opening") or 0) if ledger else 0
        sinking_opening = (ledger.get("sinking_opening") or 0) if ledger else 0
        combined_opening = round(admin_opening + sinking_opening, 2)
        if combined_opening == 0 and ledger:
            combined_opening = ledger.get("opening_arrears") or 0
        if combined_opening == 0:
            combined_opening = unit.get("opening_arrears") or 0

        # Period levy: prefer ledger.period_levy (already computed from actual data).
        # Fallback: derive from admin_levied + sinking_levied in ledger.
        if ledger and ledger.get("period_levy"):
            period_levy = float(ledger["period_levy"])
        elif ledger:
            admin_levied = ledger.get("admin_levied") or 0
            sinking_levied = ledger.get("sinking_levied") or 0
            annual_levy_amount = admin_levied + sinking_levied
            period_levy = annual_levy_amount / 4
        else:
            period_levy = 0.0

        # Compute due dates from site settings (same source as Debt Recovery Board).
        # Default levy_due_day_type="last" → Q1=March 31, aligning the first
        # current-cycle obligation with the quarter the user sees as "next due".
        due_months = b_settings.get("levy_due_months", [3, 6, 9, 12])
        due_day_type = b_settings.get("levy_due_day_type", "last")
        due_day = b_settings.get("levy_due_day")
        custom_dates = b_settings.get("levy_due_custom_dates", {})
        try:
            levy_year_int = int(year)
        except (ValueError, TypeError):
            levy_year_int = today.year
        due_date_strs = compute_period_due_dates(
            levy_year_int, due_months, due_day_type, due_day, len(due_months), custom_dates
        )
        due_dates = [date.fromisoformat(d) for d in due_date_strs]
        periods_past_grace = sum(1 for d in due_dates if today > d + timedelta(days=grace_days))

        # Authoritative confirmed payments: ledger.total_paid (DEFT/bank imports).
        # Do NOT use levy_payments (portal records) — those may be current-year
        # instalment payments, not prior-year arrears clearance.
        confirmed_paid = float(ledger.get("total_paid", 0.0)) if ledger else 0.0

        obligations_so_far = combined_opening + (periods_past_grace * period_levy)
        total_outstanding = round(max(0.0, obligations_so_far - confirmed_paid), 2)

    except Exception as exc:
        logger.warning("notice_service: outstanding computation failed for %s/%s: %s", unit_number, year, exc)
        opening_fallback = (
            (ledger.get("opening_arrears") or 0)
            if ledger else (unit.get("opening_arrears") or 0)
        )
        combined_opening = float(opening_fallback or 0)
        period_levy = 0.0
        periods_past_grace = 0
        confirmed_paid = 0.0
        total_outstanding = round(max(0.0, combined_opening), 2)

    # 2. Generate PDF — compact single-page layout
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.8 * cm, bottomMargin=1.5 * cm,
        title=f"Arrears Notice - Unit {unit_number}",
    )
    styles = getSampleStyleSheet()
    accent = colors.HexColor(profile["document_accent_color"])

    # Compact styles to fit on one page
    header_style = ParagraphStyle(
        "NoticeHeader", parent=styles["Heading1"], fontSize=14, spaceAfter=6,
        alignment=1, textColor=accent
    )
    address_style = ParagraphStyle("Address", parent=styles["Normal"], fontSize=9, leading=11)
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold")
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12, spaceBefore=3, spaceAfter=3)
    legislation_style = ParagraphStyle("Legislation", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold",
                                       spaceBefore=4)
    bullet_style = ParagraphStyle("Bullet", parent=styles["Normal"], fontSize=9, leading=11, leftIndent=10,
                                  spaceBefore=2, spaceAfter=2)

    story = []

    # Shared agency/building letterhead.
    agency_logo = local_brand_asset_path(profile.get("strata_management_logo_url"))
    building_logo = local_brand_asset_path(profile.get("building_logo_url"))
    mode = profile.get("document_branding_mode", "dual")
    if mode == "building":
        primary = RLImage(building_logo, width=3.8 * cm, height=1.4 * cm) if building_logo else Paragraph(
            html_lib.escape(building_name), styles["Heading2"])
        secondary = ""
    else:
        primary = RLImage(agency_logo, width=3.8 * cm, height=1.4 * cm) if agency_logo else Paragraph(
            html_lib.escape(strata_manager), styles["Heading2"])
        secondary = (
            RLImage(building_logo, width=2.4 * cm, height=1.2 * cm)
            if mode == "dual" and building_logo
            else (Paragraph(html_lib.escape(building_name), address_style) if mode == "dual" else "")
        )
    contact_lines = []
    if profile.get("strata_management_abn"):
        contact_lines.append(f"ABN: {html_lib.escape(profile['strata_management_abn'])}")
    if profile.get("strata_management_licence"):
        contact_lines.append(f"Licence: {html_lib.escape(profile['strata_management_licence'])}")
    for key in ("strata_manager_address", "strata_manager_phone", "strata_manager_email"):
        if profile.get(key):
            prefix = "Ph: " if key == "strata_manager_phone" else ""
            contact_lines.append(prefix + html_lib.escape(profile[key]))
    brand_header = Table(
        [[primary, secondary, Paragraph("<br/>".join(contact_lines), address_style)]],
        colWidths=[6 * cm, 4 * cm, 7.5 * cm],
    )
    brand_header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(brand_header)
    story.append(HRFlowable(width="100%", thickness=1.2, color=accent))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("ARREARS NOTICE", header_style))

    # Divider
    story.append(HRFlowable(width="100%", thickness=0.5, color=accent))
    story.append(Spacer(1, 0.2 * cm))

    # Two-column header block: Date/Ref on left, Recipient on right
    header_data = [
        [
            Paragraph(f"<b>Date:</b> {_now_str()}", address_style),
            Paragraph(f"<b>To:</b> {html_lib.escape(str(owner_name or 'The Owner'))}", address_style),
        ],
        [
            Paragraph(f"<b>Ref:</b> Unit {html_lib.escape(str(unit_number))} / Plan {plan_number}", address_style),
            Paragraph(f"Unit {html_lib.escape(str(unit_number))}, {html_lib.escape(building_name)}", address_style),
        ],
        [
            Paragraph("", address_style),
            Paragraph(html_lib.escape(profile.get("building_address") or ""), address_style),
        ],
    ]
    header_tbl = Table(header_data, colWidths=[9 * cm, 8.5 * cm])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 0.3 * cm))

    # Subject Line
    safe_unit_num = html_lib.escape(str(unit_number))
    safe_lot_num = html_lib.escape(str(unit.get('lot_number', 'N/A')))
    story.append(
        Paragraph(f"<b>RE: NOTICE OF OUTSTANDING LEVIES – UNIT {safe_unit_num} (LOT {safe_lot_num})</b>", body_style))

    # Legislation Citation
    story.append(Paragraph("NOTICE PURSUANT TO SECTION 83 OF THE UNIT TITLES (MANAGEMENT) ACT 2011", legislation_style))
    story.append(Spacer(1, 0.2 * cm))

    # Body
    story.append(Paragraph(
        f"Records of the Owners Corporation for Plan {plan_number} indicate that as of {_now_str()}, "
        f"there are outstanding levies associated with your unit. Under the Unit Titles (Management) Act 2011, "
        "levies are required to be paid by the due date specified in the levy notice.",
        body_style
    ))

    # Outstanding Amount Table
    story.append(Spacer(1, 0.3 * cm))
    # Use ledger-derived combined_opening (always set in the try block above).
    prior_arrears = combined_opening
    current_due = round(periods_past_grace * period_levy, 2)

    tbl_data = [
        ["Description", "Amount"],
        ["Prior Year Arrears Carried Forward", f"${prior_arrears:,.2f}"],
        ["Current Period Levies Due (past grace)", f"${current_due:,.2f}"],
        ["Less: Confirmed Payments Received", f"(${confirmed_paid:,.2f})" if confirmed_paid > 0 else "$0.00"],
        ["TOTAL OUTSTANDING", f"${total_outstanding:,.2f}"],
    ]
    t = Table(tbl_data, colWidths=[11 * cm, 4.5 * cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), accent),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#fef2f2")),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * cm))

    # Payment Instructions
    story.append(Paragraph("<b>PAYMENT INSTRUCTIONS</b>", label_style))
    story.append(Paragraph(
        "Please settle the outstanding balance immediately using one of the following methods:",
        body_style
    ))

    _configured_deft = b_settings.get("deft_ref", "")
    _fallback_deft = f"99{plan_number}{unit_number.zfill(3)}"
    deft_ref_is_placeholder = not _configured_deft
    deft_ref = _configured_deft or _fallback_deft
    bpay_biller_code = b_settings.get("bpay_biller_code", "")
    bpay_ref = b_settings.get("bpay_ref", "") or deft_ref
    bank_bsb = b_settings.get("bank_bsb", "")
    bank_account_number = b_settings.get("bank_account_number", "")
    bank_name = b_settings.get("bank_name", "")
    disclaimer = b_settings.get("levy_notice_disclaimer", "")

    if deft_ref_is_placeholder:
        # Fallback reference — highlight so admin knows it needs configuring in Settings → Payment & Bank
        placeholder_style = ParagraphStyle(
            "DeftPlaceholder", parent=bullet_style,
            textColor=colors.HexColor("#B45309"),  # amber-700
        )
        story.append(Paragraph(
            f"• <b>DEFT Online:</b> deft.com.au — Reference: {deft_ref}"
            f" <font color='#B45309'>[PLACEHOLDER — configure in Settings → Payment &amp; Bank]</font>",
            placeholder_style,
        ))
    else:
        story.append(Paragraph(f"• <b>DEFT Online:</b> deft.com.au — Reference: {deft_ref}", bullet_style))
    if bpay_biller_code:
        story.append(Paragraph(f"• <b>BPAY:</b> Biller Code: {bpay_biller_code} — Reference: {bpay_ref}", bullet_style))
    if bank_bsb and bank_account_number:
        story.append(
            Paragraph(
                f"• <b>EFT ({bank_name}):</b> BSB: {bank_bsb} — Account: {bank_account_number} — Reference: Unit {unit_number}",
                bullet_style))

    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        "If payment has been made in the last 48 hours, please disregard this notice. "
        "If you are experiencing financial hardship, please contact management to discuss a payment plan.",
        body_style
    ))

    if disclaimer:
        story.append(Spacer(1, 0.3 * cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 0.15 * cm))
        disclaimer_style = ParagraphStyle(
            "Disclaimer", parent=body_style, fontSize=8, leading=10, textColor=colors.HexColor("#555555")
        )
        story.append(Paragraph(html_lib.escape(str(disclaimer)), disclaimer_style))

    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("Yours faithfully,", body_style))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(f"<b>{html_lib.escape(strata_manager)}</b>", body_style))
    story.append(Paragraph(f"On behalf of the Owners Corporation for Plan {plan_number}", address_style))
    if profile.get("document_footer_text"):
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(html_lib.escape(profile["document_footer_text"]), address_style))

    doc.build(story)
    pdf_bytes = buf.getvalue()

    # 3. Post-Generation Side Effects
    log_entry = {
        "date": get_current_timestamp(),
        "method": "email",
        "description": f"Arrears Notice generated via portal (Year: {year}). Outstanding: ${total_outstanding:,.2f}",
        "performed_by": user_id,
        "performed_by_name": user_name,
    }

    await db.units.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {
            "$push": {"arrears_metadata.contact_log": log_entry},
            "$set": {
                "arrears_metadata.first_notice_sent_at": (
                    get_current_timestamp()
                    if not unit.get("arrears_metadata", {}).get("first_notice_sent_at")
                    else unit["arrears_metadata"]["first_notice_sent_at"]
                ),
                "updated_at": get_current_timestamp(),
            },
        },
    )

    await create_audit_log(
        action="arrears_notice_sent",
        resource_type="unit",
        resource_id=unit_number,
        building_id=building_id,
        user_id=user_id,
        user_name=user_name,
        details={"unit_number": unit_number, "total_outstanding": total_outstanding, "year": year},
    )

    return pdf_bytes
