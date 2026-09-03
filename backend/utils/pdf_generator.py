"""
PDF Generation Utilities

Functions for generating PDF documents (Purchase Orders, Invoices, etc.).
"""
import html as html_lib
import io
from typing import Optional

from config import PDF_AVAILABLE
from domain.jurisdictional_rules import rule_engine
from services.gst_service import parse_levy_gst_settings
from services.document_branding_service import local_brand_asset_path
from utils.name_utils import format_owner_names

if PDF_AVAILABLE:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
        Image as PdfImage,
    )
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT


class BuildingSettingsIncompleteError(ValueError):
    """Required building settings fields are missing or empty.

    Raised when a legal-document PDF function cannot determine plan_number or
    strata_address. HTTP callers should convert this to a 422 response with the
    structured body. Cron callers should log and skip the affected unit.
    """

    def __init__(self, missing_fields: list, building_id: str = ""):
        """Generated function header.

        Function: BuildingSettingsIncompleteError.__init__
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.missing_fields = missing_fields
        self.building_id = building_id
        self.error_code = "BuildingSettingsIncomplete"
        self.fix_action = "Update building settings before generating this document"
        super().__init__(
            f"Building settings incomplete for building_id={building_id!r}: "
            f"missing/empty fields: {missing_fields}"
        )


def generate_purchase_order_pdf(
        po_data: dict,
        contractor_data: dict,
        building_name: str = "East Gate Residences",
        gst_rate: float = 0.10,
        gst_registered: bool = True,
        building_settings: dict = None,
) -> bytes:
    """Generate a Purchase Order PDF"""
    if not PDF_AVAILABLE:
        return None

    s = building_settings or {}
    if s:
        building_name = s.get("building_name") or s.get("name") or building_name
    strata_address = s.get("strata_address") or s.get("building_address") or ""

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=20 * mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=18)

    elements = []

    # Header
    elements.append(Paragraph(html_lib.escape(building_name), title_style))
    elements.append(Paragraph("PURCHASE ORDER", ParagraphStyle('PO', alignment=TA_CENTER, fontSize=14, spaceAfter=20)))
    elements.append(Spacer(1, 10 * mm))

    # PO Details
    po_info = [
        ["PO Number:", html_lib.escape(str(po_data.get("po_number", "")))],
        ["Date:", html_lib.escape(str(po_data.get("created_at", "")[:10]))],
        ["Due Date:", html_lib.escape(str(po_data.get("due_date", "N/A")[:10] if po_data.get("due_date") else "N/A"))],
    ]

    contractor_info = [
        ["Contractor:", html_lib.escape(str(contractor_data.get("name", "")))],
        ["ABN:", html_lib.escape(str(contractor_data.get("abn", "N/A")))],
        ["Phone:", html_lib.escape(str(contractor_data.get("phone", "N/A")))],
        ["Email:", html_lib.escape(str(contractor_data.get("email", "N/A")))],
    ]

    # Create two-column layout
    header_table = Table([
        [Table(po_info), Table(contractor_info)]
    ], colWidths=[90 * mm, 90 * mm])
    elements.append(header_table)
    elements.append(Spacer(1, 10 * mm))

    # Description
    elements.append(Paragraph("<b>Description:</b>", styles['Normal']))
    elements.append(Paragraph(html_lib.escape(str(po_data.get("description", ""))), styles['Normal']))
    elements.append(Spacer(1, 10 * mm))

    # Amount
    effective_gst_rate = gst_rate if gst_registered else 0.0
    gst_label = f"GST ({effective_gst_rate * 100:g}%)"
    total_amount = po_data.get("total_amount", po_data.get("amount", 0) * (1 + effective_gst_rate))
    amount_table = Table([
        ["Amount (excl. GST):", f"${po_data.get('amount', 0):,.2f}"],
        [f"{gst_label}:", f"${po_data.get('amount', 0) * effective_gst_rate:,.2f}"],
        ["Total (incl. GST):", f"${total_amount:,.2f}"],
    ], colWidths=[100 * mm, 50 * mm])
    amount_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
    ]))
    elements.append(amount_table)

    # Footer
    elements.append(Spacer(1, 20 * mm))
    elements.append(Paragraph(html_lib.escape(strata_address),
                              ParagraphStyle('Footer', alignment=TA_CENTER, fontSize=10, textColor=colors.grey)))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_invoice_pdf(invoice_data: dict, po_data: dict, contractor_data: dict,
                         building_name: str = "East Gate Residences",
                         building_settings: dict = None) -> bytes:
    """Generate an Invoice PDF"""
    if not PDF_AVAILABLE:
        return None

    s = building_settings or {}
    if s:
        building_name = s.get("building_name") or s.get("name") or building_name
    strata_address = s.get("strata_address") or s.get("building_address") or ""

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=20 * mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=18)

    elements = []

    # Header
    elements.append(Paragraph(html_lib.escape(building_name), title_style))
    elements.append(Paragraph("INVOICE", ParagraphStyle('Invoice', alignment=TA_CENTER, fontSize=14, spaceAfter=20)))
    elements.append(Spacer(1, 10 * mm))

    # Invoice details
    inv_info = [
        ["Invoice Number:", html_lib.escape(str(invoice_data.get("invoice_number", "")))],
        ["PO Reference:", html_lib.escape(str(po_data.get("po_number", "")))],
        ["Date:", html_lib.escape(str(invoice_data.get("created_at", "")[:10]))],
        ["Status:", html_lib.escape(str(invoice_data.get("status", "").upper()))],
    ]

    contractor_info = [
        ["Contractor:", html_lib.escape(str(contractor_data.get("name", "")))],
        ["ABN:", html_lib.escape(str(contractor_data.get("abn", "N/A")))],
    ]

    header_table = Table([
        [Table(inv_info), Table(contractor_info)]
    ], colWidths=[90 * mm, 90 * mm])
    elements.append(header_table)
    elements.append(Spacer(1, 10 * mm))

    # Description
    elements.append(Paragraph("<b>Description:</b>", styles['Normal']))
    elements.append(Paragraph(po_data.get("description", ""), styles['Normal']))
    elements.append(Spacer(1, 10 * mm))

    # Amount breakdown
    amount_table = Table([
        ["Amount (excl. GST):", f"${invoice_data.get('amount', 0):,.2f}"],
        ["GST:", f"${invoice_data.get('gst_amount', 0):,.2f}"],
        ["Total:", f"${invoice_data.get('total_amount', 0):,.2f}"],
    ], colWidths=[100 * mm, 50 * mm])
    amount_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
    ]))
    elements.append(amount_table)

    # Notes
    if invoice_data.get("notes"):
        elements.append(Spacer(1, 10 * mm))
        elements.append(Paragraph("<b>Notes:</b>", styles['Normal']))
        elements.append(Paragraph(invoice_data.get("notes", ""), styles['Normal']))

    # Footer
    elements.append(Spacer(1, 20 * mm))
    elements.append(Paragraph(html_lib.escape(strata_address),
                              ParagraphStyle('Footer', alignment=TA_CENTER, fontSize=10, textColor=colors.grey)))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


# NOTE: The 2024 Strata Schemes Management Amendment Act inserted payment-plan provisions
# as ss 83A–83C (Part 4 Div 2A). The original s.83 deals with time to pay. Both sections
# are in scope; legal review recommended before any litigation-facing notices.
HARDSHIP_STATEMENT_NSW = """
<b>Financial Hardship Information (NSW Strata Schemes Management Act 2015)</b><br/>
If you are experiencing financial difficulty and are unable to pay this levy by the due date, \
you may request a payment plan from the owners corporation. The owners corporation must \
respond within 28 days. Contact your strata manager or the executive committee as soon \
as possible.<br/>
<b>NSW Debt Helpline:</b> 1800 007 007 (free, independent financial counselling — \
Mon–Fri 9:30am–4:30pm) or visit <u>ndh.org.au</u>. Interest may be charged on overdue \
levies per the owners corporation by-laws or at the rate prescribed under the Strata \
Schemes Management Regulation 2016.
"""


def get_levy_notice_footer(jurisdiction: str) -> str:
    """Generated function header.

    Function: get_levy_notice_footer
    Path: backend/utils/pdf_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        rules = rule_engine.get_effective_rules(jurisdiction)
    except ValueError:
        return ""
    hardship_required = (rules.get("levy_notice_hardship_required") or {}).get("value", False)
    return HARDSHIP_STATEMENT_NSW if hardship_required else ""


def generate_levy_notice_pdf(levy_data: dict, unit_data: dict, owner_data: dict,
                             building_name: str = "East Gate Residences",
                             building_settings: dict = None) -> bytes:
    """Generate a Levy Notice PDF based on the standard template.

    building_settings: dict from db.settings for the building. When provided, bank/payment
    details, ABN, disclaimer, and the plan number are sourced from the database rather than
    from hardcoded defaults.
    """
    if not PDF_AVAILABLE:
        raise RuntimeError("PDF generation unavailable: reportlab library not installed")

    s = building_settings or {}
    gst_config = parse_levy_gst_settings(s)
    plan_number = s.get("plan_number") or ""
    strata_address = s.get("strata_address") or s.get("building_address") or ""

    missing = [f for f, v in [("plan_number", plan_number), ("strata_address", strata_address)] if not v]
    if missing:
        raise BuildingSettingsIncompleteError(
            missing_fields=missing,
            building_id=s.get("building_id", ""),
        )
    building_abn = s.get("building_abn", "")
    deft_ref = s.get("deft_ref", "")
    bpay_biller_code = s.get("bpay_biller_code", "")
    bpay_ref = s.get("bpay_ref", "")
    aus_post_code = s.get("aus_post_code", "")
    aus_post_ref = s.get("aus_post_ref", "")
    bank_name = s.get("bank_name", "")
    bank_bsb = s.get("bank_bsb", "")
    bank_account_number = s.get("bank_account_number", "")
    levy_interest_rate_pa = s.get("levy_interest_rate_pa", 10.0)
    disclaimer = s.get("levy_notice_disclaimer", "")

    buffer = io.BytesIO()
    # Tight-but-legible margins so the enhanced notice (levy split + arrears/credit +
    # interest/late fees + legal paragraph) reliably fits on a single A4 page.
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=16)
    sub_title_style = ParagraphStyle('SubTitle', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10,
                                     spaceAfter=4)
    bold_style = styles['Normal'].clone('Bold')
    bold_style.fontName = 'Helvetica-Bold'
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8, leading=10)
    disclaimer_style = ParagraphStyle('Disclaimer', parent=styles['Normal'], fontSize=8, leading=10,
                                      textColor=colors.HexColor("#555555"))

    # Managing-agent branding: the notice is issued in the strata management
    # company's name (substituting any hardcoded platform brand). Resolved from
    # the same building_settings the caller already loaded.
    from services.levy_notice_email_service import resolve_managing_agent_branding
    branding = resolve_managing_agent_branding(s.get("building_id", ""), s)
    company_name = branding["company_name"]
    accent = colors.HexColor(branding.get("document_accent_color") or "#B8823D")
    branding_mode = branding.get("document_branding_mode") or "dual"
    agency_logo_path = local_brand_asset_path(branding.get("strata_management_logo_url"))
    building_logo_path = local_brand_asset_path(branding.get("building_logo_url"))

    elements = []

    # ── Shared building + managing-agent branding header ──────────────────────
    agent_name_style = ParagraphStyle(
        'AgentName', parent=styles['Heading1'], fontSize=17,
        textColor=accent,
    )
    building_name_style = ParagraphStyle(
        'BuildingName', parent=styles['Normal'], fontSize=9, alignment=TA_CENTER,
        fontName='Helvetica-Bold', textColor=colors.HexColor("#4B5563"),
    )
    agent_contact_style = ParagraphStyle(
        'AgentContact', parent=styles['Normal'], fontSize=8, alignment=TA_RIGHT,
        textColor=colors.HexColor("#4B5563"), leading=10,
    )
    agent_contact_bits = []
    if branding.get("strata_management_abn"):
        agent_contact_bits.append(f"ABN: {html_lib.escape(str(branding['strata_management_abn']))}")
    if branding.get("strata_management_licence"):
        agent_contact_bits.append(f"Licence: {html_lib.escape(str(branding['strata_management_licence']))}")
    if branding.get("company_address"):
        agent_contact_bits.append(html_lib.escape(str(branding["company_address"])))
    if branding.get("company_phone"):
        agent_contact_bits.append(f"Ph: {html_lib.escape(str(branding['company_phone']))}")
    if branding.get("company_email"):
        agent_contact_bits.append(html_lib.escape(str(branding["company_email"])))
    if branding.get("strata_management_website"):
        agent_contact_bits.append(html_lib.escape(str(branding["strata_management_website"])))

    if branding_mode == "building":
        primary_identity = (
            PdfImage(building_logo_path, width=38 * mm, height=14 * mm)
            if building_logo_path
            else Paragraph(html_lib.escape(str(building_name)), agent_name_style)
        )
        secondary_identity = ""
    else:
        primary_identity = (
            PdfImage(agency_logo_path, width=38 * mm, height=14 * mm)
            if agency_logo_path
            else Paragraph(html_lib.escape(str(company_name)), agent_name_style)
        )
        if branding_mode == "dual":
            secondary_identity = (
                PdfImage(building_logo_path, width=24 * mm, height=12 * mm)
                if building_logo_path
                else Paragraph(html_lib.escape(str(building_name)), building_name_style)
            )
        else:
            secondary_identity = ""

    agent_header = Table(
        [[
            primary_identity,
            secondary_identity,
            Paragraph("<br/>".join(agent_contact_bits), agent_contact_style),
        ]],
        colWidths=[58 * mm, 38 * mm, 74 * mm],
    )
    agent_header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(agent_header)
    elements.append(HRFlowable(width="100%", thickness=1.2, color=accent, spaceBefore=4, spaceAfter=8))

    # ── Levy Notice Header ────────────────────────────────────────────────────
    elements.append(Paragraph("LEVY NOTICE", title_style))
    elements.append(Paragraph("Unit Titles (Management) Act 2011", sub_title_style))
    lot_number = unit_data.get("lot_number", "")
    re_line = f"RE: {html_lib.escape(building_name)}, Units Plan {html_lib.escape(str(plan_number))}"
    if lot_number:
        re_line += f", Lot {html_lib.escape(str(lot_number))}"
    if strata_address:
        re_line += f", {html_lib.escape(str(strata_address))}"
    elements.append(Paragraph(re_line, sub_title_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2F4F4F"), spaceAfter=6))

    # ABN line
    if building_abn:
        elements.append(Paragraph(
            f"Building ABN: {html_lib.escape(str(building_abn))}",
            ParagraphStyle('ABN', parent=styles['Normal'], fontSize=8, alignment=TA_RIGHT, textColor=colors.grey)
        ))

    elements.append(Spacer(1, 4 * mm))

    # Owner and Unit Info (Left Side) and Notice Details (Right Side).
    # Escaped content must live inside a Paragraph so ReportLab decodes XML
    # entities (a bare table-cell string renders "&amp;" literally).
    owner_info = [
        [Paragraph("<b>To the Owner(s):</b>", styles['Normal'])],
        [Paragraph(html_lib.escape(str(owner_data.get("full_name", "The Owner"))), styles['Normal'])],
        [Paragraph(html_lib.escape(str(owner_data.get("address", strata_address))), styles['Normal'])],
    ]

    notice_info = [
        ["Notice Date:", Paragraph(html_lib.escape(str(levy_data.get("notice_date", ""))), styles['Normal'])],
        ["Due Date:", Paragraph(html_lib.escape(str(levy_data.get("due_date", ""))), styles['Normal'])],
        ["Unit Number:", Paragraph(html_lib.escape(str(unit_data.get("unit_number", ""))), styles['Normal'])],
        ["Lot Number:", Paragraph(html_lib.escape(str(unit_data.get("lot_number", ""))), styles['Normal'])],
        ["Entitlement:", Paragraph(html_lib.escape(str(unit_data.get("entitlement", ""))), styles['Normal'])],
    ]

    header_table = Table([
        [Table(owner_info), Table(notice_info)]
    ], colWidths=[100 * mm, 70 * mm])
    elements.append(header_table)
    elements.append(Spacer(1, 5 * mm))

    # Levy Details Table — the current period's Admin/Sinking split plus the account's
    # brought-forward position (arrears owed OR credit paid in advance) and any estimated
    # interest / late-payment fee on overdue amounts. Every figure is supplied by the caller
    # from a canonical finance helper (compute_unit_levy for the split;
    # services.levy_notice_financials for arrears/credit/interest/penalty) — this function
    # never reconstructs an obligation, it only itemises and totals what it is given.
    admin_amount = levy_data.get("admin_amount", 0) or 0
    sinking_amount = levy_data.get("sinking_amount", 0) or 0
    levy_subtotal = admin_amount + sinking_amount
    gst_multiplier = gst_config["gst_multiplier"]
    gst_total = round(levy_subtotal - (levy_subtotal / gst_multiplier), 2)

    # Account position (all non-negative magnitudes; arrears and credit are mutually exclusive).
    arrears_amount = round(float(levy_data.get("arrears_amount", 0) or 0), 2)
    credit_amount = round(float(levy_data.get("credit_amount", 0) or 0), 2)
    interest_amount = round(float(levy_data.get("interest_amount", 0) or 0), 2)
    penalty_amount = round(float(levy_data.get("penalty_amount", 0) or 0), 2)

    period_label = levy_data.get("period", "")
    levy_details = [
        [Paragraph("<b>Description</b>", styles['Normal']), Paragraph("<b>Period</b>", styles['Normal']),
         Paragraph("<b>Amount</b>", styles['Normal'])],
        ["Administrative Fund", period_label, f"${admin_amount:,.2f}"],
        ["Sinking Fund", period_label, f"${sinking_amount:,.2f}"],
    ]

    # Brought-forward account position. A credit reduces the payable total and is shown as a
    # parenthesised (negative) figure; arrears adds to it. Never netted across units.
    if arrears_amount > 0:
        levy_details.append(["Arrears Brought Forward (overdue balance)", "", f"${arrears_amount:,.2f}"])
    elif credit_amount > 0:
        levy_details.append(["Less: Credit in Advance (paid ahead)", "", f"(${credit_amount:,.2f})"])

    if interest_amount > 0:
        levy_details.append(["Interest on Overdue Levy (estimated)", "", f"${interest_amount:,.2f}"])
    if penalty_amount > 0:
        levy_details.append(["Late Payment Fee (estimated)", "", f"${penalty_amount:,.2f}"])

    # Grand total = this period's levy + arrears + interest + late fee − any advance credit,
    # clamped at $0. Falls back to the current-period levy when no account position is supplied
    # (non-cron callers), preserving the previous single-period behaviour.
    total_amount = round(
        max(0.0, levy_subtotal + arrears_amount + interest_amount + penalty_amount - credit_amount),
        2,
    )
    levy_details.append([Paragraph("<b>Total Amount Payable (incl. GST)</b>", bold_style), "",
                         Paragraph(f"<b>${total_amount:,.2f}</b>", bold_style)])
    levy_details.append([Paragraph(f"{gst_config['gst_label']} included in current levy", small_style), "",
                         Paragraph(f"${gst_total:,.2f}", small_style)])

    levy_table = Table(levy_details, colWidths=[88 * mm, 42 * mm, 40 * mm])
    levy_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(levy_table)

    # Plain-language account-status note directly under the table (owner-facing).
    if arrears_amount > 0:
        status_note = (
            f"<b>Account status:</b> Your account is currently <b>${arrears_amount:,.2f}</b> in arrears. "
            f"Please pay the total amount payable above to bring your account up to date."
        )
    elif credit_amount > 0:
        status_note = (
            f"<b>Account status:</b> Your account is <b>${credit_amount:,.2f}</b> in credit (paid in advance). "
            f"This credit has been applied to the amount payable above."
        )
    else:
        status_note = "<b>Account status:</b> Your account is up to date. Thank you."
    elements.append(Spacer(1, 3 * mm))
    elements.append(Paragraph(status_note, small_style))
    # Payment Methods
    elements.append(Paragraph("<b>HOW TO PAY:</b>", bold_style))
    elements.append(Spacer(1, 2 * mm))

    deft_text = "Visit <b>deft.com.au</b>"
    if deft_ref:
        deft_text += f"<br/>Ref: {html_lib.escape(str(deft_ref))}"

    bpay_text = "<b>BPAY</b>"
    if bpay_biller_code:
        bpay_text = f"Biller Code: {html_lib.escape(str(bpay_biller_code))}"
    if bpay_ref:
        bpay_text += f"<br/>Ref: {html_lib.escape(str(bpay_ref))}"

    aus_post_text = "<b>Post Billpay</b>"
    if aus_post_code:
        aus_post_text = f"Billpay Code: {html_lib.escape(str(aus_post_code))}"
    if aus_post_ref:
        aus_post_text += f"<br/>Ref: {html_lib.escape(str(aus_post_ref))}"

    payment_data = [
        [Paragraph("<b>DEFT (Card/Direct Debit)</b>", styles['Normal']),
         Paragraph("<b>BPAY</b>", styles['Normal']),
         Paragraph("<b>Post Billpay</b>", styles['Normal'])],
        [
            Paragraph(deft_text, styles['Normal']),
            Paragraph(bpay_text, styles['Normal']),
            Paragraph(aus_post_text, styles['Normal']),
        ]
    ]

    payment_table = Table(payment_data, colWidths=[56 * mm, 56 * mm, 56 * mm])
    payment_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
    ]))
    elements.append(payment_table)

    # EFT / Direct Bank Transfer row
    if bank_bsb and bank_account_number:
        elements.append(Spacer(1, 2 * mm))
        eft_text = (
            f"<b>EFT/Direct Bank Transfer:</b> {html_lib.escape(str(bank_name))} "
            f"BSB: {html_lib.escape(str(bank_bsb))} — Acc: {html_lib.escape(str(bank_account_number))} "
            f"— Ref: Unit {html_lib.escape(str(unit_data.get('unit_number', '')))}"
        )
        elements.append(Paragraph(eft_text, small_style))

    # Interest / arrears legal paragraph (mirrors the standard levy-notice wording).
    try:
        interest_rate_display = f"{float(levy_interest_rate_pa):.2f}"
    except (TypeError, ValueError):
        interest_rate_display = str(levy_interest_rate_pa)
    legal_text = (
        f"Please note that the interest rate applying to overdue levies for this Units Plan is "
        f"{interest_rate_display}% per annum. Arrears fees are applicable once an account has passed "
        f"the grace period applicable to the building. Legal costs may also be incurred for arrears "
        f"as directed by the Owners Corporation. All interest, fees and legal costs are due and "
        f"payable immediately and, if not paid, will attract interest at the same rate as overdue "
        f"levies. You will also be ineligible to vote at general meetings of the Owners Corporation "
        f"until all levies, costs, fees and interest are paid in full. Accordingly, you should "
        f"contact us if you anticipate any difficulty attending to payment on time."
    )
    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph(legal_text, small_style))

    # Jurisdiction-aware footer (NSW hardship statement)
    jurisdiction = levy_data.get("jurisdiction", "ACT")
    footer_text = get_levy_notice_footer(jurisdiction)
    if footer_text:
        elements.append(Spacer(1, 4 * mm))
        elements.append(
            Paragraph(footer_text, ParagraphStyle('Hardship', fontSize=9, textColor=colors.black, leading=11)))

    # Configurable legal/disclaimer copy plus the shared document footer.
    shared_footer = branding.get("document_footer_text") or ""
    if shared_footer:
        disclaimer = f"{disclaimer}\n{shared_footer}".strip()

    # Disclaimer
    if disclaimer:
        elements.append(Spacer(1, 4 * mm))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(html_lib.escape(str(disclaimer)), disclaimer_style))

    # Footer
    elements.append(Spacer(1, 5 * mm))
    footer_plan = f"Units Plan {html_lib.escape(str(plan_number))} — {html_lib.escape(building_name)}"
    elements.append(Paragraph(footer_plan,
                              ParagraphStyle('Footer', alignment=TA_CENTER, fontSize=9, textColor=colors.grey)))
    if strata_address:
        elements.append(Paragraph(html_lib.escape(str(strata_address)),
                                  ParagraphStyle('Footer2', alignment=TA_CENTER, fontSize=9, textColor=colors.grey)))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_rental_certificate_pdf(cert_data: dict, building_name: str = "East Gate Residences",
                                    building_settings: dict = None) -> bytes:
    """
    Generate an ACT Section 119A Unit Title Rental Certificate PDF.

    Sections:
      1. Header
      2. Certificate reference box
      3. Property & Owner details
      4. Tenant details
      5. Financial summary
      6. EC Members
      7. Strata Manager
      8. Insurance
      9. By-Laws note
      10. Known Defects / Special Levies / Common Property
      11. Footer + signature block
    """
    if not PDF_AVAILABLE:
        return None

    s = building_settings or {}
    plan_number = s.get("plan_number") or ""
    strata_address = s.get("strata_address") or s.get("building_address") or ""

    missing = [f for f, v in [("plan_number", plan_number), ("strata_address", strata_address)] if not v]
    if missing:
        raise BuildingSettingsIncompleteError(
            missing_fields=missing,
            building_id=s.get("building_id", ""),
        )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'RCTitle', parent=styles['Heading1'],
        alignment=TA_CENTER, fontSize=16, spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        'RCSub', parent=styles['Normal'],
        alignment=TA_CENTER, fontSize=10, spaceAfter=2, textColor=colors.grey,
    )
    section_style = ParagraphStyle(
        'RCSection', parent=styles['Normal'],
        fontSize=9, fontName='Helvetica-Bold',
        backColor=colors.HexColor('#2F4F4F'), textColor=colors.white,
        leftIndent=4, spaceBefore=6, spaceAfter=2,
    )
    normal = styles['Normal'].clone('RCNormal')
    normal.fontSize = 9
    bold = normal.clone('RCBold')
    bold.fontName = 'Helvetica-Bold'
    small_grey = ParagraphStyle(
        'RCGrey', parent=styles['Normal'],
        fontSize=8, textColor=colors.grey, alignment=TA_CENTER,
    )

    COL_W = [50 * mm, 110 * mm]  # label / value
    FULL_W = 160 * mm

    def section_header(text):
        """Generated function header.

        Function: section_header
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return Paragraph(f"  {html_lib.escape(text)}", section_style)

    def kv_row(label, value):
        """Generated function header.

        Function: kv_row
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return [Paragraph(f"<b>{html_lib.escape(label)}</b>", normal),
                Paragraph(html_lib.escape(str(value or "—")), normal)]

    def kv_table(rows):
        """Generated function header.

        Function: kv_table
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        t = Table(rows, colWidths=COL_W)
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ]))
        return t

    elements = []

    # ── 1. Header ─────────────────────────────────────────────────────────────
    # Certificate type label in header (reflects July 2024 amendment)
    cert_type = cert_data.get("certificate_type", "sale")
    type_label_map = {
        "sale": "SALE CERTIFICATE",
        "rental": "RENTAL CERTIFICATE",
        "update": "UPDATE CERTIFICATE",
    }
    type_label = type_label_map.get(cert_type, "RENTAL CERTIFICATE")

    elements.append(Paragraph(html_lib.escape(building_name), title_style))
    elements.append(Paragraph(f"UNIT TITLE {type_label}", title_style))
    elements.append(Paragraph(
        "Section 119A — Unit Titles (Management) Act 2011 (ACT)",
        sub_style,
    ))
    elements.append(Spacer(1, 4 * mm))

    # ── 2. Certificate reference box ──────────────────────────────────────────
    ref_data = [
        [
            Paragraph("<b>Certificate No.</b>", bold),
            Paragraph(cert_data.get("cert_number", ""), normal),
            Paragraph("<b>Issue Date</b>", bold),
            Paragraph((cert_data.get("issued_at") or "")[:10] or "Pending", normal),
        ],
        [
            Paragraph("<b>Valid Until</b>", bold),
            Paragraph((cert_data.get("expiry_date") or "")[:10] or "—", normal),
            Paragraph("<b>Status</b>", bold),
            Paragraph((cert_data.get("status") or "").upper(), normal),
        ],
    ]
    ref_table = Table(ref_data, colWidths=[35 * mm, 45 * mm, 35 * mm, 45 * mm])
    ref_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#2F4F4F')),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0f4f4')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(ref_table)
    elements.append(Spacer(1, 4 * mm))

    # ── 3. Property Details ───────────────────────────────────────────────────
    elements.append(section_header("PROPERTY DETAILS"))
    prop_rows = [
        kv_row("Unit Number", cert_data.get("unit_number")),
        kv_row("Lot Number", cert_data.get("lot_number")),
        kv_row("Property Type", cert_data.get("property_type", "").replace("_", " ").title()),
        kv_row("Unit of Entitlement (UOE)", cert_data.get("unit_entitlement")),
        kv_row("Address", strata_address),
    ]
    elements.append(kv_table(prop_rows))

    # ── 4. Owner Details ──────────────────────────────────────────────────────
    elements.append(section_header("OWNER DETAILS"))
    owner_name = format_owner_names(
        cert_data.get("owner_name") or "—",
        cert_data.get("owner_name_b") or "",
    )
    elements.append(kv_table([kv_row("Owner(s)", owner_name)]))

    # ── 5. Tenant Details ─────────────────────────────────────────────────────
    elements.append(section_header("INCOMING TENANT DETAILS"))
    tenant_rows = [
        kv_row("Tenant Name", cert_data.get("tenant_name")),
        kv_row("Lease Start Date", (cert_data.get("lease_start_date") or "")[:10]),
        kv_row("Lease End Date", (cert_data.get("lease_end_date") or "")[:10] or "Periodic / Not specified"),
    ]
    elements.append(kv_table(tenant_rows))

    # ── 6. Financial Summary ──────────────────────────────────────────────────
    elements.append(section_header("FINANCIAL SUMMARY"))
    levy_q = cert_data.get("levy_quarterly")
    levy_a = cert_data.get("levy_annual")
    arrears = cert_data.get("arrears_amount", 0) or 0
    admin_budget = cert_data.get("admin_budget_current")
    sink_bal = cert_data.get("sinking_fund_balance")

    fin_rows = [
        kv_row("Quarterly Levy (Admin + Sinking)", f"${levy_q:,.2f}" if levy_q is not None else "—"),
        kv_row("Annual Levy Total", f"${levy_a:,.2f}" if levy_a is not None else "—"),
        kv_row("Outstanding Arrears", f"${arrears:,.2f}" if arrears > 0 else "NIL"),
        kv_row("Admin Fund Budget (Current Year)", f"${admin_budget:,.2f}" if admin_budget is not None else "—"),
        kv_row("Sinking Fund Balance", f"${sink_bal:,.2f}" if sink_bal is not None else "—"),
    ]
    elements.append(kv_table(fin_rows))

    # ── 7. Executive Committee Members ────────────────────────────────────────
    elements.append(section_header("EXECUTIVE COMMITTEE"))
    ec_members = cert_data.get("ec_members_snapshot") or []
    if ec_members:
        ec_rows = [[Paragraph("<b>Name</b>", bold), Paragraph("<b>Role</b>", bold)]]
        for m in ec_members:
            ec_rows.append([
                Paragraph(m.get("name", ""), normal),
                Paragraph(m.get("role", "").replace("_", " ").title(), normal),
            ])
        ec_table = Table(ec_rows, colWidths=[80 * mm, 80 * mm])
        ec_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(ec_table)
    else:
        elements.append(kv_table([kv_row("Members", "See management office for current EC details")]))

    # ── 8. Strata Manager ─────────────────────────────────────────────────────
    elements.append(section_header("STRATA MANAGER"))
    sm_rows = [
        kv_row("Name", cert_data.get("strata_manager_name")),
        kv_row("Contact", cert_data.get("strata_manager_contact")),
        kv_row("Licence Number", cert_data.get("strata_manager_licence")),
    ]
    elements.append(kv_table(sm_rows))

    # ── 9. Insurance ──────────────────────────────────────────────────────────
    elements.append(section_header("INSURANCE"))
    ins_rows = [
        kv_row("Insurer", cert_data.get("insurance_insurer")),
        kv_row("Building Coverage Amount", f"${cert_data.get('insurance_coverage_amount'):,.2f}" if cert_data.get(
            "insurance_coverage_amount") else "—"),
        kv_row("Public Liability", cert_data.get("insurance_public_liability")),
        kv_row("Policy Expiry Date", (cert_data.get("insurance_expiry") or "")[:10] or "—"),
    ]
    elements.append(kv_table(ins_rows))

    # ── 10. By-Laws Note ──────────────────────────────────────────────────────
    elements.append(section_header("BY-LAWS"))
    elements.append(kv_table([kv_row(
        "By-Laws",
        "A copy of the Units Plan by-laws is available for inspection via the East Gate Residences "
        "resident portal (eastgateresidences.com.au) under Documents. Tenants are required to comply "
        "with all applicable by-laws.",
    )]))

    # ── 11. Common Property / Defects / Special Levies ────────────────────────
    elements.append(section_header("ADDITIONAL INFORMATION"))
    add_rows = [
        kv_row("Common Property Notes", cert_data.get("common_property_notes") or "No special notes at time of issue"),
        kv_row("Known Defects", cert_data.get("known_defects") or "None at time of issue"),
        kv_row("Special Levies Pending", cert_data.get("special_levies_pending") or "None pending at time of issue"),
    ]
    if cert_data.get("additional_notes"):
        add_rows.append(kv_row("Additional Notes", cert_data["additional_notes"]))
    elements.append(kv_table(add_rows))

    # ── 12. Footer / Signature Block ──────────────────────────────────────────
    elements.append(Spacer(1, 6 * mm))
    sig_data = [
        [
            Paragraph("<b>Issued by:</b> ____________________________", normal),
            Paragraph("<b>Date:</b> ________________________", normal),
        ],
        [
            Paragraph(f"Name: {cert_data.get('issued_by_name') or 'Authorised Officer'}", normal),
            Paragraph(f"On behalf of the Owners Corporation — {building_name}", normal),
        ],
    ]
    sig_table = Table(sig_data, colWidths=[80 * mm, 80 * mm])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(sig_table)

    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph(
        "This certificate is valid for <b>5 years</b> from the issue date (Unit Titles (Management) Act 2011 s.119A). "
        "Reissue is required if material information changes.",
        small_grey,
    ))
    elements.append(Paragraph(
        f"Units Plan {html_lib.escape(str(plan_number))} — {html_lib.escape(building_name)} — {html_lib.escape(strata_address)}",
        small_grey,
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_invoice_receipt_pdf(invoice_data: dict, building_name: str = "Your Building") -> Optional[bytes]:
    """Generate a payment receipt PDF for a confirmed invoice/quote."""
    if not PDF_AVAILABLE:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    center = ParagraphStyle("Center", alignment=TA_CENTER, fontSize=11)
    right = ParagraphStyle("Right", alignment=TA_RIGHT, fontSize=10)
    bold_center = ParagraphStyle("BoldCenter", alignment=TA_CENTER, fontSize=14, fontName="Helvetica-Bold")
    elements = []

    elements.append(Paragraph(html_lib.escape(building_name), bold_center))
    elements.append(Paragraph("PAYMENT RECEIPT", ParagraphStyle("Sub", alignment=TA_CENTER, fontSize=12, spaceAfter=6)))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2F4F4F")))
    elements.append(Spacer(1, 8 * mm))

    vendor = html_lib.escape(str(invoice_data.get("vendor_name", "")))
    inv_no = html_lib.escape(str(invoice_data.get("invoice_number", "N/A")))
    inv_date = html_lib.escape(str(invoice_data.get("invoice_date", "")))
    abn = html_lib.escape(str(invoice_data.get("abn") or "N/A"))
    fund = html_lib.escape(str(invoice_data.get("fund", "admin")).capitalize())

    meta_table = Table([
        ["Vendor:", vendor, "Invoice #:", inv_no],
        ["ABN:", abn, "Date:", inv_date],
        ["Fund:", fund, "", ""],
    ], colWidths=[30 * mm, 65 * mm, 30 * mm, 55 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 8 * mm))

    # Line items
    line_items = invoice_data.get("line_items") or []
    if line_items:
        rows = [["Description", "Qty", "Unit Price", "Total"]]
        for item in line_items:
            rows.append([
                html_lib.escape(str(item.get("description", ""))),
                str(item.get("qty", 1)),
                f"${float(item.get('unit_price', 0)):,.2f}",
                f"${float(item.get('total', 0)):,.2f}",
            ])
        item_table = Table(rows, colWidths=[95 * mm, 20 * mm, 30 * mm, 35 * mm])
        item_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(item_table)
        elements.append(Spacer(1, 6 * mm))

    subtotal = float(invoice_data.get("subtotal", 0))
    gst = float(invoice_data.get("gst", 0))
    total = float(invoice_data.get("total", 0))

    totals_table = Table([
        ["Subtotal (excl. GST):", f"${subtotal:,.2f}"],
        ["GST (10%):", f"${gst:,.2f}"],
        ["Total (incl. GST):", f"${total:,.2f}"],
    ], colWidths=[120 * mm, 40 * mm])
    totals_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 12 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elements.append(Spacer(1, 4 * mm))
    elements.append(
        Paragraph("This receipt confirms the expense has been recorded in the strata management ledger.", center))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_nsw_cwf_schedule_pdf(
        building_settings: dict,
        sinking_fund_years: list,
        building_name: str = "Our Building",
) -> bytes:
    """GAP-JUR-NSW-004: NSW 10-year Capital Works Fund schedule in prescribed form.

    sinking_fund_years: list of dicts from the sinking_fund_plan collection,
    each with keys: year (int), opening_balance (float), expenditure (float),
    levy_income (float), interest (float), closing_balance (float), description (str).
    Sorted ascending by year before rendering.
    """
    if not PDF_AVAILABLE:
        raise RuntimeError("PDF generation unavailable: reportlab library not installed")

    s = building_settings or {}
    plan_number = s.get("plan_number", "")
    strata_address = s.get("strata_address") or s.get("building_address", "")
    building_abn = s.get("building_abn", "")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CWFTitle', parent=styles['Heading1'],
                                 alignment=TA_CENTER, fontSize=14, spaceAfter=4)
    sub_style = ParagraphStyle('CWFSub', parent=styles['Normal'],
                               alignment=TA_CENTER, fontSize=9, textColor=colors.grey, spaceAfter=2)
    bold_style = styles['Normal'].clone('CWFBold')
    bold_style.fontName = 'Helvetica-Bold'
    small_style = ParagraphStyle('CWFSmall', parent=styles['Normal'], fontSize=8, leading=10)

    elements = []

    # Header
    elements.append(Paragraph("10-YEAR CAPITAL WORKS FUND SCHEDULE", title_style))
    elements.append(Paragraph(
        "Strata Schemes Management Act 2015 (NSW) — s.75 &amp; s.79 Prescribed Form",
        sub_style,
    ))
    if plan_number:
        elements.append(Paragraph(f"Strata Plan No: {html_lib.escape(str(plan_number))}", sub_style))
    elements.append(Paragraph(html_lib.escape(building_name), sub_style))
    if strata_address:
        elements.append(Paragraph(html_lib.escape(strata_address), sub_style))
    if building_abn:
        elements.append(Paragraph(f"ABN: {html_lib.escape(str(building_abn))}", sub_style))
    elements.append(Spacer(1, 6 * mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2F4F4F"), spaceAfter=4))

    # Schedule table
    years = sorted(sinking_fund_years, key=lambda r: r.get("year", 0))

    header_row = [
        Paragraph("<b>Year</b>", bold_style),
        Paragraph("<b>Opening\nBalance ($)</b>", bold_style),
        Paragraph("<b>Levy\nIncome ($)</b>", bold_style),
        Paragraph("<b>Interest\nEarned ($)</b>", bold_style),
        Paragraph("<b>Expenditure ($)</b>", bold_style),
        Paragraph("<b>Closing\nBalance ($)</b>", bold_style),
        Paragraph("<b>Major Works Description</b>", bold_style),
    ]
    col_widths = [14 * mm, 26 * mm, 22 * mm, 22 * mm, 26 * mm, 26 * mm, 28 * mm]

    table_data = [header_row]
    for row in years:
        table_data.append([
            str(row.get("year", "")),
            f"${row.get('opening_balance', 0):,.0f}",
            f"${row.get('levy_income', 0):,.0f}",
            f"${row.get('interest', 0):,.0f}",
            f"${row.get('expenditure', 0):,.0f}",
            f"${row.get('closing_balance', 0):,.0f}",
            Paragraph(html_lib.escape(str(row.get("description", ""))),
                      ParagraphStyle('CWFDesc', parent=styles['Normal'], fontSize=7, leading=9)),
        ])

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (1, 0), (5, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 6 * mm))

    # Statutory note
    note = (
        "<b>Note:</b> This schedule has been prepared in accordance with s.75 of the "
        "Strata Schemes Management Act 2015 (NSW) and the Strata Schemes Management "
        "Regulation 2016. The capital works fund must be maintained in a separate account "
        "and must not be used for administrative fund expenses."
    )
    elements.append(Paragraph(note, small_style))
    elements.append(Spacer(1, 4 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(
        "Generated by Strata Management Platform — for informational purposes only. "
        "Seek independent financial advice for investment decisions.",
        ParagraphStyle('CWFFooter', parent=styles['Normal'], fontSize=7,
                       textColor=colors.grey, alignment=TA_CENTER),
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


# NSW s.184 embedded-network disclosure field names (SSMA 2015 s.184 as amended by
# Strata Schemes Management Amendment (Embedded Networks) Act 2024)
_NSW_S184_NETWORK_TYPES = ["electricity", "gas", "hot_water", "cold_water", "telecommunications", "other"]


def generate_nsw_s184_certificate_pdf(
        building_settings: dict,
        cert_data: dict,
        building_name: str = "Our Building",
) -> bytes:
    """GAP-JUR-NSW-006: NSW s.184 Strata Information Certificate with embedded-network disclosure.

    cert_data keys:
      lot_number, unit_number, owner_name, issued_at, cert_number
      levies_owing_cents (int), special_levy_cents (int, optional)
      has_embedded_network (bool)
      embedded_networks (list of dicts):
        network_type (one of _NSW_S184_NETWORK_TYPES), operator_name (str),
        tariff_description (str), complaint_contact (str)
      strata_manager_name, strata_manager_email, strata_manager_phone
      by_laws_registered (bool), pending_by_laws (str, optional)
      insurance_insurer, insurance_policy_number, insurance_expiry
    """
    if not PDF_AVAILABLE:
        raise RuntimeError("PDF generation unavailable: reportlab library not installed")

    s = building_settings or {}
    plan_number = s.get("plan_number", "")
    strata_address = s.get("strata_address") or s.get("building_address", "")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=20 * mm, leftMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('S184Title', parent=styles['Heading1'],
                                 alignment=TA_CENTER, fontSize=15, spaceAfter=4)
    sub_style = ParagraphStyle('S184Sub', parent=styles['Normal'],
                               alignment=TA_CENTER, fontSize=9, textColor=colors.grey, spaceAfter=2)
    section_style = ParagraphStyle('S184Section', parent=styles['Normal'],
                                   fontSize=9, fontName='Helvetica-Bold',
                                   backColor=colors.HexColor('#2F4F4F'), textColor=colors.white,
                                   leftIndent=4, spaceBefore=6, spaceAfter=2)
    normal = styles['Normal'].clone('S184Normal')
    normal.fontSize = 9
    bold = normal.clone('S184Bold')
    bold.fontName = 'Helvetica-Bold'
    small = ParagraphStyle('S184Small', parent=styles['Normal'], fontSize=8, leading=10,
                           textColor=colors.HexColor("#555555"))

    COL_W = [55 * mm, 105 * mm]

    def sec(text):
        """Generated function header.

        Function: sec
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return Paragraph(f"  {html_lib.escape(text)}", section_style)

    def kv(label, value):
        """Generated function header.

        Function: kv
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return [Paragraph(f"<b>{html_lib.escape(label)}</b>", normal),
                Paragraph(html_lib.escape(str(value or "—")), normal)]

    def kv_tbl(rows):
        """Generated function header.

        Function: kv_tbl
        Path: backend/utils/pdf_generator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        t = Table(rows, colWidths=COL_W)
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ]))
        return t

    elements = []

    # ── Header ────────────────────────────────────────────────────────────────
    elements.append(Paragraph(html_lib.escape(building_name), title_style))
    elements.append(Paragraph("STRATA INFORMATION CERTIFICATE", title_style))
    elements.append(Paragraph(
        "Section 184 — Strata Schemes Management Act 2015 (NSW)",
        sub_style,
    ))
    if plan_number:
        elements.append(Paragraph(f"Strata Plan No: {html_lib.escape(str(plan_number))}", sub_style))
    if strata_address:
        elements.append(Paragraph(html_lib.escape(strata_address), sub_style))
    elements.append(Spacer(1, 4 * mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2F4F4F"), spaceAfter=4))

    # ── 1. Certificate details ────────────────────────────────────────────────
    elements.append(sec("1. Certificate Details"))
    elements.append(kv_tbl([
        kv("Certificate No.", cert_data.get("cert_number", "")),
        kv("Issue Date", (cert_data.get("issued_at") or "")[:10] or ""),
        kv("Lot / Unit", f"Lot {cert_data.get('lot_number', '')} / Unit {cert_data.get('unit_number', '')}"),
        kv("Owner", cert_data.get("owner_name", "")),
    ]))

    # ── 2. Financial information ──────────────────────────────────────────────
    elements.append(sec("2. Financial Information"))
    owing = cert_data.get("levies_owing_cents", 0) or 0
    special = cert_data.get("special_levy_cents", 0) or 0
    elements.append(kv_tbl([
        kv("Levies Owing", f"${owing / 100:,.2f}"),
        kv("Special Levy (if any)", f"${special / 100:,.2f}" if special else "Nil"),
    ]))

    # ── 3. Insurance ─────────────────────────────────────────────────────────
    elements.append(sec("3. Insurance"))
    elements.append(kv_tbl([
        kv("Insurer", cert_data.get("insurance_insurer", "")),
        kv("Policy Number", cert_data.get("insurance_policy_number", "")),
        kv("Expiry Date", cert_data.get("insurance_expiry", "")),
    ]))

    # ── 4. By-laws ────────────────────────────────────────────────────────────
    elements.append(sec("4. By-Laws"))
    by_laws_registered = cert_data.get("by_laws_registered", True)
    elements.append(kv_tbl([
        kv("By-Laws Registered", "Yes" if by_laws_registered else "No"),
        kv("Pending By-Laws", cert_data.get("pending_by_laws") or "None"),
    ]))

    # ── 5. Embedded-Network Disclosure (s.184(1)(h) as amended 2024) ─────────
    elements.append(sec("5. Embedded Network Disclosure — s.184(1)(h) SSMA 2015"))
    has_network = cert_data.get("has_embedded_network", False)
    elements.append(kv_tbl([
        kv("Embedded Network Present", "YES" if has_network else "NO"),
    ]))

    if has_network:
        networks = cert_data.get("embedded_networks") or []
        for i, net in enumerate(networks, 1):
            elements.append(Spacer(1, 2 * mm))
            elements.append(Paragraph(
                f"  <b>Network {i}: {html_lib.escape(net.get('network_type', '').replace('_', ' ').title())}</b>",
                bold,
            ))
            elements.append(kv_tbl([
                kv("Operator Name", net.get("operator_name", "")),
                kv("Tariff / Pricing", net.get("tariff_description", "")),
                kv("Complaints Contact", net.get("complaint_contact", "")),
            ]))
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(
            "Owners and occupants have the right to seek an alternative energy retailer "
            "where permitted under the National Energy Retail Law. Contact the Australian "
            "Energy Regulator (AER) at <u>aer.gov.au</u> or 1300 585 165 for advice.",
            small,
        ))
    else:
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(
            "This strata scheme does not operate an embedded network for any utility service.",
            normal,
        ))

    # ── 6. Strata manager ─────────────────────────────────────────────────────
    elements.append(sec("6. Strata Manager"))
    elements.append(kv_tbl([
        kv("Name", cert_data.get("strata_manager_name", "")),
        kv("Email", cert_data.get("strata_manager_email", "")),
        kv("Phone", cert_data.get("strata_manager_phone", "")),
    ]))

    # ── Footer / signature block ──────────────────────────────────────────────
    elements.append(Spacer(1, 8 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elements.append(Spacer(1, 3 * mm))
    sig_rows = [
        [Paragraph("<b>Authorised by:</b>", bold), Paragraph("_" * 40, normal)],
        [Paragraph("<b>Position:</b>", bold), Paragraph("", normal)],
        [Paragraph("<b>Date:</b>", bold), Paragraph("", normal)],
    ]
    elements.append(Table(sig_rows, colWidths=[50 * mm, 110 * mm]))
    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph(
        "This certificate is issued under s.184 of the Strata Schemes Management Act 2015 (NSW). "
        "A fee may be charged in accordance with the Strata Schemes Management Regulation 2016. "
        "This certificate is valid for 3 months from the date of issue.",
        small,
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


__all__ = [
    'generate_purchase_order_pdf',
    'generate_invoice_pdf',
    'generate_levy_notice_pdf',
    'generate_rental_certificate_pdf',
    'generate_invoice_receipt_pdf',
    'generate_nsw_cwf_schedule_pdf',
    'generate_nsw_s184_certificate_pdf',
]
