# @featuretrace:letters
# Layer: service
# Data flow: routers/letters.py -> generate_letter_pdf() -> WeasyPrint -> bytes
# Related: backend/routers/letters.py, backend/services/document_branding_service.py
# Scope: building-scoped

import asyncio
import html as html_lib
import os

from config import WEASYPRINT_AVAILABLE
from services.document_branding_service import resolve_document_branding

if WEASYPRINT_AVAILABLE:
    from weasyprint import HTML, CSS  # type: ignore


_LETTER_CSS = """
@page { size: A4; margin: 22mm 18mm 20mm 18mm; }
body {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 10.5pt;
    line-height: 1.48;
    color: #20242A;
}
.document-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, .7fr) minmax(190px, 1fr);
    align-items: start;
    gap: 14px;
    border-bottom: 2px solid var(--accent);
    padding-bottom: 10px;
    margin-bottom: 22px;
}
.brand-logo { max-width: 175px; max-height: 68px; object-fit: contain; object-position: left top; }
.building-brand { text-align: center; color: #4B5563; font-size: 9pt; font-weight: 700; }
.building-brand .brand-logo { max-width: 110px; max-height: 54px; object-position: center top; }
.agency-name { color: var(--accent); font-size: 17pt; font-weight: 700; line-height: 1.1; }
.agency-details { text-align: right; color: #4B5563; font-size: 8.5pt; line-height: 1.35; }
.document-title {
    color: var(--accent);
    font-size: 17pt;
    line-height: 1.2;
    font-weight: 700;
    text-align: center;
    margin: 0 0 5px;
}
.document-subtitle { color: #4B5563; text-align: center; font-weight: 700; margin: 0 0 22px; }
.re-line { font-weight: 700; margin: 16px 0; }
.body-section { margin-bottom: 18px; }
.highlight-box {
    background: #F8F7F4;
    border-left: 4px solid var(--accent);
    padding: 11px 14px;
    margin: 15px 0;
}
.amount { font-size: 18pt; font-weight: 700; color: var(--accent); }
.disclosure { border: 1px solid #D6D8DC; padding: 11px 13px; margin: 15px 0; font-size: 9pt; }
.disclosure h3 { margin: 0 0 6px; color: var(--accent); font-size: 10pt; }
.footer {
    border-top: 1px solid #D1D5DB;
    margin-top: 28px;
    padding-top: 8px;
    font-size: 8pt;
    color: #6B7280;
    text-align: center;
}
table { width: 100%; border-collapse: collapse; margin: 10px 0; }
th { background: var(--accent); color: white; padding: 6px 9px; text-align: left; font-size: 9pt; }
td { padding: 6px 9px; border-bottom: 1px solid #E5E7EB; font-size: 9.5pt; vertical-align: top; }
ul, ol { padding-left: 20px; }
li { margin-bottom: 4px; }
a { color: #3157A4; word-break: break-all; }
"""


def _e(value) -> str:
    return html_lib.escape(str(value or ""))


def _public_asset_url(value: str | None) -> str:
    url = str(value or "").strip()
    if url.startswith("/"):
        base = os.getenv("FRONTEND_URL", "").rstrip("/")
        return f"{base}{url}" if base else url
    return url


def _logo(value: str | None, alt: str) -> str:
    url = _public_asset_url(value)
    return f'<img class="brand-logo" src="{_e(url)}" alt="{_e(alt)}">' if url else ""


def _dynamic_style(profile: dict) -> str:
    page_number_rule = (
        '@page { @bottom-right { content: "Page " counter(page) " of " counter(pages); '
        'font: 8pt Arial; color: #6B7280; } }'
        if profile.get("document_show_page_numbers", True)
        else ""
    )
    return f"<style>:root {{ --accent: {_e(profile['document_accent_color'])}; }}{page_number_rule}</style>"


def _document_header(building: dict) -> str:
    profile = resolve_document_branding(building, str(building.get("building_id") or ""))
    mode = profile["document_branding_mode"]
    agency_logo = _logo(profile.get("strata_management_logo_url"), profile["strata_management_company"])
    building_logo = _logo(profile.get("building_logo_url"), profile["building_name"])

    if mode == "building":
        primary = building_logo or f'<div class="agency-name">{_e(profile["building_name"])}</div>'
        building_mark = ""
    else:
        primary = agency_logo or f'<div class="agency-name">{_e(profile["strata_management_company"])}</div>'
        building_mark = ""
        if mode == "dual":
            building_mark = building_logo or f'<div>{_e(profile["building_name"])}</div>'

    detail_lines = []
    if profile.get("strata_management_abn"):
        detail_lines.append(f'ABN: {_e(profile["strata_management_abn"])}')
    if profile.get("strata_management_licence"):
        detail_lines.append(f'Licence: {_e(profile["strata_management_licence"])}')
    for key in ("strata_manager_address", "strata_manager_phone", "strata_manager_email", "strata_management_website"):
        if profile.get(key):
            prefix = "Ph: " if key == "strata_manager_phone" else ""
            detail_lines.append(prefix + _e(profile[key]))

    return f"""
    {_dynamic_style(profile)}
    <div class="document-header">
        <div>{primary}</div>
        <div class="building-brand">{building_mark}</div>
        <div class="agency-details">{"<br>".join(detail_lines)}</div>
    </div>
    """


def _footer(building: dict) -> str:
    profile = resolve_document_branding(building, str(building.get("building_id") or ""))
    scheme = f'Units Plan {_e(profile["plan_number"])}' if profile.get("plan_number") else _e(profile["building_name"])
    parts = [scheme, _e(profile.get("building_address"))]
    if profile.get("document_footer_text"):
        parts.append(_e(profile["document_footer_text"]))
    return f'<div class="footer">{" &bull; ".join(part for part in parts if part)}</div>'


def _render_levy_reminder(data: dict, building: dict) -> str:
    owner = _e(data.get("owner_name", "Owner"))
    unit = _e(data.get("unit_number", ""))
    amount = _e(data.get("amount_due", "0.00"))
    due_date = _e(data.get("due_date", ""))
    quarter = _e(data.get("quarter", ""))
    ref = _e(data.get("payment_ref", ""))
    profile = resolve_document_branding(building, str(building.get("building_id") or ""))

    rows = [
        f"<tr><td>Lot / Unit</td><td>{unit}</td></tr>",
        f"<tr><td>Due date</td><td>{due_date}</td></tr>",
    ]
    if quarter:
        rows.append(f"<tr><td>Contribution period</td><td>{quarter}</td></tr>")
    if ref:
        rows.append(f"<tr><td>Payment reference</td><td>{ref}</td></tr>")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {_document_header(building)}
    <h1 class="document-title">LEVY REMINDER</h1>
    <div class="document-subtitle">{_e(profile["building_name"])} - Units Plan {_e(profile["plan_number"])}</div>
    <p>Dear {owner},</p>
    <p>This notice records the levy amount currently due for <strong>Lot / Unit {unit}</strong>.
    Please arrange payment by the due date or contact the strata manager promptly if the
    account details require review.</p>
    <div class="highlight-box"><div>Amount due</div><div class="amount">${amount}</div></div>
    <table><tr><th>Detail</th><th>Value</th></tr>{"".join(rows)}</table>
    <p>If you have recently paid, please retain the payment confirmation while the receipt is allocated.</p>
    <p>Yours sincerely,<br>{_e(data.get("manager_name") or "Strata Manager")}<br>
    {_e(profile["strata_management_company"])}</p>
    {_footer(building)}
    </body></html>"""


def _render_agm_invitation(data: dict, building: dict) -> str:
    owner = _e(data.get("owner_name", "Owner"))
    agm_date = _e(data.get("agm_date", ""))
    agm_time = _e(data.get("agm_time", ""))
    location = _e(data.get("agm_location", ""))
    meeting_link = _e(data.get("meeting_link", ""))
    meeting_id = _e(data.get("meeting_id", ""))
    meeting_passcode = _e(data.get("meeting_passcode", ""))
    manager_name = _e(data.get("manager_name", "Strata Manager"))
    agenda_items = [_e(item.strip()) for item in str(data.get("agenda_items", "")).splitlines() if item.strip()]
    agenda_html = "".join(f"<li>{item}</li>" for item in agenda_items) if agenda_items else "<li>To be advised</li>"
    profile = resolve_document_branding(building, str(building.get("building_id") or ""))

    online_rows = ""
    if meeting_link:
        online_rows += f'<tr><td><strong>Join link</strong></td><td><a href="{meeting_link}">{meeting_link}</a></td></tr>'
    if meeting_id:
        online_rows += f"<tr><td><strong>Meeting ID</strong></td><td>{meeting_id}</td></tr>"
    if meeting_passcode:
        online_rows += f"<tr><td><strong>Passcode</strong></td><td>{meeting_passcode}</td></tr>"

    quorum = _e(data.get("quorum_text") or (
        "Business may proceed only when the quorum required by the legislation applying "
        "to this owners corporation is present. Owners unable to attend should consider "
        "appointing a valid proxy or submitting an absentee vote where permitted."
    ))
    disclosures = []
    for title, key in (
        ("Recording and transcription", "agm_recording_disclosure"),
        ("Insurance disclosure", "agm_insurance_disclosure"),
    ):
        if profile.get(key):
            disclosures.append(f'<div class="disclosure"><h3>{title}</h3><div>{_e(profile[key])}</div></div>')

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {_document_header(building)}
    <h1 class="document-title">NOTICE OF ANNUAL GENERAL MEETING</h1>
    <div class="document-subtitle">THE OWNERS - UNITS PLAN NO. {_e(profile["plan_number"])}<br>
    {_e(profile["building_name"])}, {_e(profile["building_address"])}</div>
    <p>Dear {owner},</p>
    <p>We write on behalf of the Owners Corporation to give notice of the Annual General Meeting.</p>
    <table>
        <tr><th>Meeting detail</th><th>Information</th></tr>
        <tr><td><strong>Date</strong></td><td>{agm_date}</td></tr>
        <tr><td><strong>Time</strong></td><td>{agm_time}</td></tr>
        <tr><td><strong>Venue</strong></td><td>{location}</td></tr>
        {online_rows}
    </table>
    <div class="body-section"><p><strong>Quorum and voting</strong></p><p>{quorum}</p></div>
    <div class="body-section"><p><strong>Agenda</strong></p><ol>{agenda_html}</ol></div>
    {"".join(disclosures)}
    <p>Please read the agenda and supporting papers before the meeting. Owners should also
    check whether their account must be financially current in order to vote.</p>
    <p>Yours faithfully,<br>{manager_name}<br>Strata Manager<br>
    {_e(profile["strata_management_company"])}</p>
    {_footer(building)}
    </body></html>"""


def _render_general_notice(data: dict, building: dict) -> str:
    owner = _e(data.get("owner_name", "Owner"))
    subject = _e(data.get("subject", "Notice"))
    body_html = "<br>".join(_e(line) for line in str(data.get("body", "")).splitlines())
    closing = _e(data.get("closing", "Yours sincerely"))
    profile = resolve_document_branding(building, str(building.get("building_id") or ""))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {_document_header(building)}
    <h1 class="document-title">NOTICE</h1>
    <p>Dear {owner},</p>
    <p class="re-line">RE: {subject}</p>
    <div class="body-section"><p>{body_html}</p></div>
    <p>{closing},<br>{_e(data.get("manager_name") or "Strata Manager")}<br>
    {_e(profile["strata_management_company"])}</p>
    {_footer(building)}
    </body></html>"""


_RENDERERS = {
    "levy_reminder": _render_levy_reminder,
    "agm_invitation": _render_agm_invitation,
    "general_notice": _render_general_notice,
}


def render_letter_html(template: str, data: dict, building: dict) -> str:
    renderer = _RENDERERS.get(template)
    if not renderer:
        raise ValueError(f"Unknown template: {template!r}")
    return renderer(data, building)


def _generate_pdf_sync(html_str: str) -> bytes:
    if not WEASYPRINT_AVAILABLE:
        raise RuntimeError("WeasyPrint is not installed - cannot generate PDF")
    base_url = os.getenv("FRONTEND_URL") or None
    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf(stylesheets=[CSS(string=_LETTER_CSS)])
    max_bytes = 10 * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        raise ValueError(f"Generated PDF exceeds {max_bytes // (1024 * 1024)} MB limit")
    return pdf_bytes


async def generate_letter_pdf(template: str, data: dict, building: dict) -> bytes:
    html_str = render_letter_html(template, data, building)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _generate_pdf_sync, html_str)
