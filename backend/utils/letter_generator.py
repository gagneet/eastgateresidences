# @featuretrace:letters — WeasyPrint HTML/CSS → PDF letter renderer
# Layer: service
# Data flow: routers/letters.py → generate_letter_pdf() → WeasyPrint → bytes
# Related: backend/routers/letters.py
#           backend/models/letter.py
# Scope: (building-scoped)

import asyncio
import html as html_lib
from typing import Optional

from config import WEASYPRINT_AVAILABLE

if WEASYPRINT_AVAILABLE:
    from weasyprint import HTML, CSS  # type: ignore

# ── Design-system colours (matches design_guidelines.json) ───────────────────

_LETTER_CSS = """
@page { size: A4; margin: 25mm 20mm 20mm 20mm; }
body {
    font-family: 'Charter', Georgia, 'Times New Roman', serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #2F4F4F;
}
.header {
    border-bottom: 2px solid #2F4F4F;
    padding-bottom: 8px;
    margin-bottom: 24px;
}
.header h1 {
    margin: 0 0 2px 0;
    font-size: 16pt;
    color: #E07A5F;
    font-family: 'Manrope', Arial, sans-serif;
}
.header .subtitle {
    font-size: 9pt;
    color: #6B7280;
}
.body-section { margin-bottom: 20px; }
.highlight-box {
    background: #FEF3EE;
    border-left: 4px solid #E07A5F;
    padding: 12px 16px;
    margin: 16px 0;
    border-radius: 0 4px 4px 0;
}
.amount { font-size: 18pt; font-weight: bold; color: #E07A5F; }
.footer {
    border-top: 1px solid #D1D5DB;
    margin-top: 32px;
    padding-top: 8px;
    font-size: 8pt;
    color: #9CA3AF;
}
table { width: 100%; border-collapse: collapse; margin: 12px 0; }
th { background: #2F4F4F; color: white; padding: 6px 10px; text-align: left; font-size: 9pt; }
td { padding: 6px 10px; border-bottom: 1px solid #E5E7EB; font-size: 10pt; }
ul { padding-left: 20px; }
li { margin-bottom: 4px; }
"""


def _e(value: str) -> str:
    """HTML-escape a user-supplied string to prevent XSS in WeasyPrint output."""
    return html_lib.escape(str(value))


def _building_header(building_name: str, plan_number: str, address: str) -> str:
    """Generated function header.

    Function: _building_header
    Path: backend/utils/letter_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"""
    <div class="header">
        <h1>{_e(building_name)}</h1>
        <div class="subtitle">Unit Plan {_e(plan_number)} &bull; {_e(address)}</div>
    </div>
    """


def _render_levy_reminder(data: dict, building: dict) -> str:
    """Generated function header.

    Function: _render_levy_reminder
    Path: backend/utils/letter_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    owner = _e(data.get("owner_name", "Owner"))
    unit = _e(data.get("unit_number", ""))
    amount = _e(str(data.get("amount_due", "0.00")))
    due_date = _e(data.get("due_date", ""))
    quarter = _e(data.get("quarter", ""))
    ref = _e(data.get("payment_ref", ""))
    bname = _e(building.get("name", "Your Building"))
    plan_no = _e(str(building.get("building_id", "")))
    addr = _e(building.get("address", ""))

    quarter_line = f"<tr><td>Quarter</td><td>{quarter}</td></tr>" if quarter else ""
    ref_line = f"<tr><td>Payment Reference</td><td>{ref}</td></tr>" if ref else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {_building_header(bname, plan_no, addr)}
    <p>Dear {owner},</p>
    <p>This notice is to inform you that your strata levy payment for <strong>Unit {unit}</strong>
    is now overdue. Please arrange payment at your earliest convenience to avoid further action.</p>
    <div class="highlight-box">
        <div>Amount Due</div>
        <div class="amount">${amount}</div>
        <div>Due Date: {due_date}</div>
    </div>
    <table>
        <tr><th>Detail</th><th>Value</th></tr>
        <tr><td>Unit Number</td><td>{unit}</td></tr>
        {quarter_line}
        {ref_line}
    </table>
    <div class="body-section">
        <p>To make a payment, please use your online banking (BSB and account number provided
        separately) or contact the strata manager directly.</p>
        <p>If you have recently made payment, please disregard this notice.</p>
    </div>
    <p>Yours sincerely,<br>Strata Manager<br>{bname}</p>
    <div class="footer">This letter was generated automatically. Please retain for your records.</div>
    </body></html>"""


def _render_agm_invitation(data: dict, building: dict) -> str:
    """Generated function header.

    Function: _render_agm_invitation
    Path: backend/utils/letter_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    owner = _e(data.get("owner_name", "Owner"))
    agm_date = _e(data.get("agm_date", ""))
    agm_time = _e(data.get("agm_time", ""))
    location = _e(data.get("agm_location", ""))
    raw_agenda = data.get("agenda_items", "")
    agenda_items = [_e(item.strip()) for item in str(raw_agenda).splitlines() if item.strip()]
    agenda_html = "".join(f"<li>{item}</li>" for item in agenda_items) if agenda_items else "<li>To be advised</li>"
    bname = _e(building.get("name", "Your Building"))
    plan_no = _e(str(building.get("building_id", "")))
    addr = _e(building.get("address", ""))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {_building_header(bname, plan_no, addr)}
    <p>Dear {owner},</p>
    <p>You are cordially invited to attend the <strong>Annual General Meeting (AGM)</strong>
    of the Owners Corporation.</p>
    <div class="highlight-box">
        <table>
            <tr><td><strong>Date</strong></td><td>{agm_date}</td></tr>
            <tr><td><strong>Time</strong></td><td>{agm_time}</td></tr>
            <tr><td><strong>Location</strong></td><td>{location}</td></tr>
        </table>
    </div>
    <div class="body-section">
        <p><strong>Agenda:</strong></p>
        <ul>{agenda_html}</ul>
    </div>
    <p>Your attendance and participation are encouraged. Proxy forms are available from the
    strata manager if you are unable to attend in person.</p>
    <p>Yours sincerely,<br>Strata Manager<br>{bname}</p>
    <div class="footer">This letter was generated automatically. Please retain for your records.</div>
    </body></html>"""


def _render_general_notice(data: dict, building: dict) -> str:
    """Generated function header.

    Function: _render_general_notice
    Path: backend/utils/letter_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    owner = _e(data.get("owner_name", "Owner"))
    subject = _e(data.get("subject", "Notice"))
    # body allows newlines → convert to <br> after escaping
    body_raw = str(data.get("body", ""))
    body_html = "<br>".join(_e(line) for line in body_raw.splitlines())
    closing = _e(data.get("closing", "Yours sincerely"))
    bname = _e(building.get("name", "Your Building"))
    plan_no = _e(str(building.get("building_id", "")))
    addr = _e(building.get("address", ""))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {_building_header(bname, plan_no, addr)}
    <p>Dear {owner},</p>
    <p><strong>Re: {subject}</strong></p>
    <div class="body-section"><p>{body_html}</p></div>
    <p>{closing},<br>Strata Manager<br>{bname}</p>
    <div class="footer">This letter was generated automatically. Please retain for your records.</div>
    </body></html>"""


_RENDERERS = {
    "levy_reminder": _render_levy_reminder,
    "agm_invitation": _render_agm_invitation,
    "general_notice": _render_general_notice,
}


def render_letter_html(template: str, data: dict, building: dict) -> str:
    """Generated function header.

    Function: render_letter_html
    Path: backend/utils/letter_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    renderer = _RENDERERS.get(template)
    if not renderer:
        raise ValueError(f"Unknown template: {template!r}")
    return renderer(data, building)


def _generate_pdf_sync(html_str: str) -> bytes:
    """Generated function header.

    Function: _generate_pdf_sync
    Path: backend/utils/letter_generator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not WEASYPRINT_AVAILABLE:
        raise RuntimeError("WeasyPrint is not installed — cannot generate PDF")
    pdf_bytes = HTML(string=html_str).write_pdf(stylesheets=[CSS(string=_LETTER_CSS)])
    _PDF_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    if len(pdf_bytes) > _PDF_MAX_BYTES:
        raise ValueError(f"Generated PDF exceeds {_PDF_MAX_BYTES // (1024 * 1024)} MB limit")
    return pdf_bytes


async def generate_letter_pdf(template: str, data: dict, building: dict) -> bytes:
    """Render HTML template then convert to PDF via WeasyPrint in a thread executor."""
    html_str = render_letter_html(template, data, building)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _generate_pdf_sync, html_str)
