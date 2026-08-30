# @featuretrace:financial-pdf-report — multi-section executive financial PDF report generator.
# Layer: service
# Data flow: get_levy_fund_data() + compute_financial_health() + forecasts/anomalies → generate_executive_report() → reportlab PDF (building-scoped).
# Related: backend/services/health_service.py (shares get_levy_fund_data/compute_combined_fund_totals, provides compute_financial_health)
#          backend/utils/finance_helpers.py (get_levy_fund_data, compute_combined_fund_totals, get_unit_ledger_stats)
"""
Report Service — generates comprehensive multi-section financial PDF reports.

Sections:
  0. Cover Page (CONFIDENTIAL, strata details, generated metadata)
  1. Executive Summary (narrative paragraphs + key metrics table)
  2. Income & Expense vs Budget by Fund
  3. Financial Anomalies
  4. Monthly Cashflow Projection (with chart)
  5. Health Score Breakdown
  6. Top 10 Lots by Cost
  7. 3-Year Expense Forecast (with grouped bar chart)
  8. 3-Year Levy Rate Projection (with line chart)
  9. Appendix — Methodology, Assumptions, Glossary

Uses reportlab.platypus for layout.
Matplotlib charts embedded as PNG images.
"""
import html as html_lib
import io
from datetime import datetime, timezone

from typing import List, Any, Optional

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak, Image as RLImage, KeepTogether,
    )

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import matplotlib

    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from database import db
from services.settings_service import get_general_settings_or_default
from utils.auth import DEFAULT_BUILDING_ID
from utils.finance_helpers import compute_combined_fund_totals, get_levy_fund_data, get_unit_ledger_stats
from utils.finance_logger import timed
from services.anomaly_service import detect_financial_anomalies
from services.health_service import compute_financial_health
from services.forecast_service import generate_cashflow_projection

_PLAN_NAME = ""  # always overridden by caller before use
_PLAN_NUMBER = DEFAULT_BUILDING_ID  # always overridden by caller before use

# Kept as a module-level alias — this function used to be a private,
# byte-identical copy of health_service.py's own `_get_levy_data`.
# Both now delegate to the single source in finance_helpers.py (GAP-FIN-016 Phase 2).
_get_levy_data = get_levy_fund_data


async def _get_categories(year: str, building_id: str):
    """Return categories with actual_amount computed from financial_transactions."""
    from services.financial_service import get_legacy_levy_categories_shape
    cats = await get_legacy_levy_categories_shape(year, building_id=building_id)
    if cats:
        return cats
    legacy = await db.levy_categories.find(
        {"year": year, "building_id": building_id},
        {"_id": 0, "name": 1, "fund_type": 1, "budgeted_amount": 1, "year": 1}
    ).sort("name", 1).to_list(100)
    for c in legacy:
        c.setdefault("actual_amount", 0.0)
    return legacy


def _now_str() -> str:
    """Generated function header.

    Function: _now_str
    Path: backend/services/report_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).strftime("%d %B %Y %H:%M UTC")


def _date_str() -> str:
    """Generated function header.

    Function: _date_str
    Path: backend/services/report_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).strftime("%d %B %Y")


# ─────────────────────────────────────────────────────────────────────────────
# Chart generators
# ─────────────────────────────────────────────────────────────────────────────

def _make_chart_bar(categories: List[str], budgeted: List[float], actual: List[float], title: str) -> bytes:
    """Create a bar chart comparing budget vs actual. Returns PNG bytes."""
    if not MATPLOTLIB_AVAILABLE:
        return b""
    fig, ax = plt.subplots(figsize=(10, 4))
    x = range(len(categories))
    width = 0.35
    ax.bar([i - width / 2 for i in x], budgeted, width, label="Budgeted", color="#2F4F4F", alpha=0.85)
    ax.bar([i + width / 2 for i in x], actual, width, label="Actual", color="#E07A5F", alpha=0.85)
    ax.set_xticks(list(x))
    ax.set_xticklabels(categories, rotation=45, ha="right", fontsize=7)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.legend()
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _make_chart_cashflow(months_data: List[dict]) -> bytes:
    """Create an area chart of monthly cashflow. Returns PNG bytes."""
    if not MATPLOTLIB_AVAILABLE or not months_data:
        return b""
    fig, ax = plt.subplots(figsize=(10, 3))
    labels = [m["month"] for m in months_data]
    balances = [m["cumulative_balance"] for m in months_data]
    ax.fill_between(range(len(labels)), balances, alpha=0.3, color="#2F4F4F")
    ax.plot(range(len(labels)), balances, color="#2F4F4F", linewidth=2)
    ax.axhline(y=0, color="#E07A5F", linestyle="--", linewidth=1, label="Zero balance")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax.set_title("Cumulative Cashflow Balance", fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _make_chart_forecast_3yr(forecasts: List[dict]) -> bytes:
    """
    Create a grouped bar chart for Y+1/Y+2/Y+3 forecasts across top 10 categories.
    Returns PNG bytes.
    """
    if not MATPLOTLIB_AVAILABLE or not forecasts:
        return b""

    # Top 10 by Y+1 projection
    top = sorted(forecasts, key=lambda f: f.get("projection_year_1", 0), reverse=True)[:10]
    categories = [f.get("category", "")[:18] for f in top]
    y1 = [f.get("projection_year_1", 0) for f in top]
    y2 = [f.get("projection_year_2", 0) for f in top]
    y3 = [f.get("projection_year_3", 0) for f in top]

    x = range(len(categories))
    width = 0.25
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.bar([i - width for i in x], y1, width, label="Y+1", color="#2F4F4F", alpha=0.9)
    ax.bar([i for i in x], y2, width, label="Y+2", color="#E07A5F", alpha=0.9)
    ax.bar([i + width for i in x], y3, width, label="Y+3", color="#F2CC8F", alpha=0.9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(categories, rotation=45, ha="right", fontsize=7)
    ax.set_title("3-Year Expense Forecast — Top 10 Categories", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _make_chart_levy_projection(levy_projection: dict) -> bytes:
    """
    Create a line chart for 3-year levy rate projection.
    Returns PNG bytes.
    """
    if not MATPLOTLIB_AVAILABLE or not levy_projection:
        return b""

    combined = levy_projection.get("combined", {})
    if not combined:
        return b""

    years = sorted(combined.keys())
    admin_rates = [combined[y].get("admin_rate_per_uoe", 0) for y in years]
    sinking_rates = [combined[y].get("sinking_rate_per_uoe", 0) for y in years]
    total_rates = [a + s for a, s in zip(admin_rates, sinking_rates)]

    x_pos = list(range(len(years)))
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.plot(x_pos, admin_rates, "o-", color="#2F4F4F", linewidth=2, label="Admin Fund ($/UOE)")
    ax.plot(x_pos, sinking_rates, "s--", color="#E07A5F", linewidth=2, label="Sinking Fund ($/UOE)")
    ax.plot(x_pos, total_rates, "^:", color="#F2CC8F", linewidth=2, label="Total ($/UOE)")
    for i, rate in enumerate(total_rates):
        ax.annotate(f"${rate:,.0f}", (i, rate), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=7)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(years)
    ax.set_title("3-Year Levy Rate Projection (per UOE)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Annual Rate per UOE ($)")
    ax.legend(fontsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _embed_png(png_bytes: bytes, width_cm: float = 16) -> Optional[Any]:
    """Wrap PNG bytes in a ReportLab Image flowable."""
    if not png_bytes or not REPORTLAB_AVAILABLE:
        return None
    buf = io.BytesIO(png_bytes)
    img = RLImage(buf)
    # Scale to specified width, preserve aspect ratio
    ratio = img.drawHeight / img.drawWidth
    img.drawWidth = width_cm * cm
    img.drawHeight = img.drawWidth * ratio
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Narrative generator
# ─────────────────────────────────────────────────────────────────────────────

def _build_executive_narrative(
        health: dict,
        anomaly_count: int,
        cashflow: dict,
        ledger_stats: dict,
        levy: dict,
        year: str,
) -> List[str]:
    """Return list of narrative paragraph strings for the executive summary."""
    score = health.get("score", 0)
    risk_level = health.get("risk_level", "unknown")
    risk_labels = {
        "excellent": "excellent financial health",
        "good": "good financial health",
        "moderate": "moderate financial health with some areas requiring attention",
        "at_risk": "financial risk that requires management attention",
        "critical": "critical financial risk requiring immediate action",
    }
    risk_desc = risk_labels.get(risk_level, risk_level)

    fund_totals = compute_combined_fund_totals(levy)
    total_income = fund_totals["total_income"]
    total_expenses = fund_totals["total_expenses"]
    closing_balance = fund_totals["closing_balance"]
    surplus = fund_totals["surplus"]

    units_owing = ledger_stats.get("units_owing", 0)
    risk_months = cashflow.get("risk_months", [])
    min_balance = cashflow.get("min_balance", 0)

    paras = [
        (
            f"The {html_lib.escape(_PLAN_NAME)} strata corporation (Plan {html_lib.escape(_PLAN_NUMBER)}) reported <b>{html_lib.escape(risk_desc)}</b> "
            f"with a financial health score of <b>{score:.1f}/100</b> for the {html_lib.escape(str(year))} financial year. "
            f"Total levied income across both funds was <b>${total_income:,.2f}</b>, "
            f"against total expenditure of <b>${total_expenses:,.2f}</b>, "
            f"resulting in a {'surplus' if surplus >= 0 else 'deficit'} of "
            f"<b>${abs(surplus):,.2f}</b>."
        ),
        (
            "Key highlights for this reporting period:"
        ),
    ]

    bullets = []
    bullets.append(
        f"<b>Anomalies</b>: {anomaly_count} active anomal{'y' if anomaly_count == 1 else 'ies'} detected "
        f"({'none requiring immediate action' if anomaly_count == 0 else 'see Section 3 for details'})."
    )
    bullets.append(
        f"<b>Cashflow</b>: Minimum monthly cumulative balance was <b>${min_balance:,.2f}</b>. "
        + (
            f"Risk months identified: {html_lib.escape(', '.join(risk_months))}." if risk_months else "No negative cashflow months projected.")
    )
    bullets.append(
        f"<b>Arrears</b>: {units_owing} unit{'s' if units_owing != 1 else ''} "
        f"{'are' if units_owing != 1 else 'is'} currently outstanding on levy payments."
    )
    bullets.append(
        f"<b>Closing Balance</b>: Combined fund closing balance is <b>${closing_balance:,.2f}</b>."
    )

    return paras, bullets


# ─────────────────────────────────────────────────────────────────────────────
# Main report generator
# ─────────────────────────────────────────────────────────────────────────────

async def generate_full_report(year: str, building_id: str, generated_by_role: str = "unknown") -> bytes:
    """
    Generate a complete financial report PDF for the given year. Scoped to building.
    Returns PDF bytes for StreamingResponse.
    """
    if not REPORTLAB_AVAILABLE:
        return b"ReportLab not available"

    with timed("report_generated", year=year, building_id=building_id, generated_by_role=generated_by_role):
        return await _generate_full_report_impl(year, building_id, generated_by_role)


async def _generate_full_report_impl(year: str, building_id: str, generated_by_role: str) -> bytes:
    """Generated function header.

    Function: _generate_full_report_impl
    Path: backend/services/report_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Financial Intelligence Report {year}",
        author="Eastgate Intelligence Engine",
        subject=f"Financial Intelligence Report — {_PLAN_NAME} {year}",
        creator="Eastgate Platform v1.0",
        keywords="strata,finance,report,intelligence",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=20, spaceAfter=16,
                                 textColor=colors.HexColor("#2F4F4F"))
    h1_style = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=14, spaceAfter=8,
                              textColor=colors.HexColor("#2F4F4F"))
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11, spaceAfter=6,
                              textColor=colors.HexColor("#2F4F4F"))
    body_style = styles["BodyText"]
    footer_style = ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7,
                                  textColor=colors.grey)
    confidential_style = ParagraphStyle("Confidential", parent=styles["Normal"], fontSize=9,
                                        textColor=colors.HexColor("#ef4444"), fontName="Helvetica-Bold")
    bullet_style = ParagraphStyle("Bullet", parent=styles["BodyText"], leftIndent=16, spaceAfter=4)
    story = []

    # ── Fetch building settings for branding ──
    b_settings = await get_general_settings_or_default(building_id, {"_id": 0})
    building_name = b_settings.get("building_name", "East Gate Residences")
    plan_number = b_settings.get("plan_number", "13195")
    strata_address = b_settings.get("address", "Section 75, Block 4, Denman Prospect ACT 2611")

    # ── Gather all data ───────────────────────────────────────────────────────
    levy = await _get_levy_data(year, building_id)
    health = await compute_financial_health(year, building_id)
    ledger_stats = await get_unit_ledger_stats(year, building_id)
    cashflow = await generate_cashflow_projection(year, building_id)
    anomalies = await db.financial_anomalies.find(
        {"building_id": building_id, "financial_year": year, "resolved": False}, {"_id": 0}
    ).sort("severity", 1).to_list(50)
    forecasts = await db.financial_forecasts.find(
        {"building_id": building_id, "financial_year": year}, {"_id": 0}
    ).to_list(100)
    cats = await _get_categories(year, building_id)
    lot_summaries = await db.lot_financial_summary.find(
        {"building_id": building_id, "financial_year": year}, {"_id": 0}
    ).sort("total_cost", -1).to_list(200)

    # Levy projection for chart
    try:
        from services.forecast_service import generate_levy_projection
        levy_projection = await generate_levy_projection(year, building_id)
    except Exception:
        levy_projection = {}

    # ── Maintenance Intelligence ──
    try:
        from services.maintenance_intelligence_service import (
            update_building_intelligence_summary,
            generate_maintenance_forecast
        )
        maint_summary = await update_building_intelligence_summary(building_id)
        maint_forecast = await generate_maintenance_forecast(building_id)
        cap_works = await db.capital_replacement_schedule.find({"building_id": building_id}).sort("replacement_year",
                                                                                                  1).to_list(100)
    except Exception:
        maint_summary = {}
        maint_forecast = {}
        cap_works = []

    # ── Council rate settings for assumptions section ────────────────────────
    # council_rate_settings uses "YYYY-YY" keys (e.g. "2025-26"), not the plain
    # year string used elsewhere in the report (e.g. "2026").
    _fy_key = f"{int(year) - 1}-{str(year)[-2:]}"
    _rate_settings = await db["council_rate_settings"].find_one(
        {"financial_year": _fy_key, "building_id": building_id}, {"_id": 0}
    ) or {}
    _raw_auv = _rate_settings.get("block_auv")
    _block_auv_str = f"${float(_raw_auv):,.0f}" if _raw_auv else "not configured"
    _total_ue = int(_rate_settings.get("total_block_entitlement") or 10_000)

    admin = (levy or {}).get("admin_fund", {})
    sinking = (levy or {}).get("sinking_fund", {})

    # ── Section 0: Cover Page ─────────────────────────────────────────────────
    story.append(Spacer(1, 2 * cm))

    # Dark header bar
    cover_header_data = [[""]]
    cover_header = Table(cover_header_data, colWidths=[17 * cm], rowHeights=[0.8 * cm])
    cover_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#2F4F4F")),
    ]))
    story.append(cover_header)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph(html_lib.escape(building_name).upper(), title_style))
    story.append(Paragraph(f"Strata Plan {html_lib.escape(plan_number)}", h2_style))
    story.append(Paragraph(html_lib.escape(strata_address), body_style))
    story.append(Spacer(1, 0.8 * cm))

    story.append(HRFlowable(width="100%", thickness=3, color=colors.HexColor("#E07A5F")))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("FINANCIAL INTELLIGENCE REPORT", title_style))
    story.append(
        Paragraph(f"Financial Year: {html_lib.escape(str(year))}–{html_lib.escape(str(int(year) + 1))}", h2_style))
    story.append(Spacer(1, 1.5 * cm))

    meta_data = [
        ["Generated:", _now_str()],
        ["Report Date:", _date_str()],
        ["Prepared for:", generated_by_role.replace("_", " ").title()],
        ["Classification:", "CONFIDENTIAL"],
    ]
    meta_table = Table(meta_data, colWidths=[5 * cm, 12 * cm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#374151")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 1.5 * cm))

    story.append(Paragraph("⚠  CONFIDENTIAL — NOT FOR PUBLIC DISTRIBUTION", confidential_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "This document contains confidential financial information for East Gate Residences "
        "Owners Corporation. It is intended solely for authorised committee members and "
        "management. Unauthorised distribution is prohibited.",
        body_style,
    ))
    story.append(PageBreak())

    # ── Section 1: Executive Summary ─────────────────────────────────────────
    story.append(Paragraph("1. Executive Summary", h1_style))
    # Pass branding to narrative builder
    orig_plan_name = _PLAN_NAME
    orig_plan_num = _PLAN_NUMBER
    globals()['_PLAN_NAME'] = building_name
    globals()['_PLAN_NUMBER'] = plan_number

    paras, bullets = _build_executive_narrative(
        health, len(anomalies), cashflow, ledger_stats, levy, year
    )

    globals()['_PLAN_NAME'] = orig_plan_name
    globals()['_PLAN_NUMBER'] = orig_plan_num
    for p in paras:
        story.append(Paragraph(p, body_style))
        story.append(Spacer(1, 0.2 * cm))
    for b in bullets:
        story.append(Paragraph(f"• {b}", bullet_style))
    story.append(Spacer(1, 0.4 * cm))

    risk_color_map = {
        "excellent": "#22c55e", "good": "#86efac",
        "moderate": "#f59e0b", "at_risk": "#f97316", "critical": "#ef4444"
    }
    risk_color = risk_color_map.get(health["risk_level"], "#6b7280")
    total_exp = (admin.get("total_expenses", 0) or 0) + (sinking.get("total_expenses", 0) or 0)
    summary_data = [
        ["Metric", "Value"],
        ["Health Score", f"{health['score']:.1f}/100 — {health['risk_level'].upper().replace('_', ' ')}"],
        ["Total Income", f"${health['details'].get('total_income', 0):,.2f}"],
        ["Total Expenses", f"${total_exp:,.2f}"],
        ["Closing Balance", f"${health['details'].get('closing_balance', 0):,.2f}"],
        ["Units Owing", f"{ledger_stats.get('units_owing', 0)} units"],
        ["Arrears %", f"{health['details'].get('arrears_pct_raw', 0):.1f}%"],
        ["Active Anomalies", f"{len(anomalies)}"],
        ["Min Cashflow Balance", f"${cashflow.get('min_balance', 0):,.2f}"],
    ]
    t = Table(summary_data, colWidths=[8 * cm, 9 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("TEXTCOLOR", (1, 1), (1, 1), colors.HexColor(risk_color)),
        ("FONTNAME", (1, 1), (1, 1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))

    # ── Section 2: Income & Expense vs Budget ─────────────────────────────────
    story.append(Paragraph("2. Income vs Budget by Fund", h1_style))
    for fund_label, fund_key in [("Administrative Fund", "administrative"), ("Sinking Fund", "sinking")]:
        story.append(Paragraph(fund_label, h2_style))
        fund_cats = [c for c in cats if c.get("fund_type") == fund_key]
        if fund_cats:
            var_data = [["Category", "Budgeted", "Actual", "Variance", "Var %"]]
            bar_cats, bar_budgeted, bar_actual = [], [], []
            for c in fund_cats:
                budgeted = c.get("budgeted_amount", 0) or 0
                actual = c.get("actual_amount", 0) or 0
                variance = actual - budgeted
                var_pct = (variance / budgeted * 100) if budgeted > 0 else 0
                var_data.append([
                    c["name"],
                    f"${budgeted:,.2f}",
                    f"${actual:,.2f}",
                    f"${variance:+,.2f}",
                    f"{var_pct:+.1f}%",
                ])
                bar_cats.append(c["name"][:12])
                bar_budgeted.append(budgeted)
                bar_actual.append(actual)
            vt = Table(var_data, colWidths=[6 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 2 * cm])
            ts = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
            for i, c in enumerate(fund_cats):
                actual_v = c.get("actual_amount", 0) or 0
                budgeted_v = c.get("budgeted_amount", 0) or 0
                if actual_v > budgeted_v * 1.1 and budgeted_v > 0:
                    ts.append(("TEXTCOLOR", (3, i + 1), (4, i + 1), colors.HexColor("#ef4444")))
                elif actual_v < budgeted_v:
                    ts.append(("TEXTCOLOR", (3, i + 1), (4, i + 1), colors.HexColor("#22c55e")))
            vt.setStyle(TableStyle(ts))
            story.append(vt)
            # Embed bar chart
            chart_png = _make_chart_bar(bar_cats, bar_budgeted, bar_actual, f"{fund_label} — Budget vs Actual")
            img = _embed_png(chart_png, width_cm=15)
            if img:
                story.append(Spacer(1, 0.3 * cm))
                story.append(img)
        story.append(Spacer(1, 0.4 * cm))

    story.append(PageBreak())

    # ── Section 3: Anomalies ──────────────────────────────────────────────────
    story.append(Paragraph("3. Financial Anomalies", h1_style))
    if anomalies:
        story.append(Paragraph(
            f"{len(anomalies)} active anomal{'y' if len(anomalies) == 1 else 'ies'} detected. "
            "Items are sorted by severity (critical → low). Resolved anomalies are excluded.",
            body_style,
        ))
        story.append(Spacer(1, 0.2 * cm))
        anom_data = [["Severity", "Type", "Category/Unit", "Description"]]
        sev_colors_map = {"critical": "#fecaca", "high": "#fed7aa", "medium": "#fef3c7", "low": "#dbeafe"}
        for a in anomalies[:20]:
            anom_data.append([
                html_lib.escape(a.get("severity", "").upper()),
                html_lib.escape(a.get("anomaly_type", "").replace("_", " ").title()),
                html_lib.escape(str(a.get("category") or a.get("lot_number") or "—")),
                html_lib.escape(a.get("description", "")[:80]),
            ])
        at = Table(anom_data, colWidths=[2 * cm, 3.5 * cm, 3.5 * cm, 7.5 * cm])
        ats = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("WORDWRAP", (3, 1), (3, -1), True),
        ]
        for i, a in enumerate(anomalies[:20]):
            sev = a.get("severity", "low")
            bg = sev_colors_map.get(sev, "#ffffff")
            ats.append(("BACKGROUND", (0, i + 1), (0, i + 1), colors.HexColor(bg)))
        at.setStyle(TableStyle(ats))
        story.append(at)
    else:
        story.append(Paragraph("No active anomalies detected for this period.", body_style))

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 4: Cashflow ───────────────────────────────────────────────────
    story.append(Paragraph("4. Monthly Cashflow Projection", h1_style))
    story.append(Paragraph(
        f"Annual Income: <b>${cashflow.get('annual_income', 0):,.2f}</b> | "
        f"Annual Expenses: <b>${cashflow.get('annual_expenses', 0):,.2f}</b> | "
        f"Min Balance: <b>${cashflow.get('min_balance', 0):,.2f}</b>",
        body_style,
    ))
    risk_months = cashflow.get("risk_months", [])
    if risk_months:
        story.append(Paragraph(
            f"⚠ Risk months (negative cumulative balance): <b>{html_lib.escape(', '.join(risk_months))}</b>",
            body_style,
        ))
    else:
        story.append(Paragraph("✓ No negative cashflow months projected.", body_style))
    story.append(Spacer(1, 0.3 * cm))
    cashflow_png = _make_chart_cashflow(cashflow.get("months", []))
    cashflow_img = _embed_png(cashflow_png, width_cm=15)
    if cashflow_img:
        story.append(cashflow_img)

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 5: Health Score breakdown ────────────────────────────────────
    story.append(Paragraph("5. Health Score Breakdown", h1_style))
    bd = health.get("breakdown", {})
    health_data = [
        ["Component", "Score", "Max", "Notes"],
        ["Surplus Ratio", f"{bd.get('surplus_ratio', 0):.1f}", "20",
         "Closing balance ÷ income (target ≥ 10%)"],
        ["Arrears %", f"{bd.get('arrears_pct', 0):.1f}", "20",
         "Units owing ÷ total units (target < 5%)"],
        ["Cashflow Buffer", f"{bd.get('cashflow_buffer', 0):.1f}", "15",
         "Min balance ÷ monthly expenses (target ≥ 2 months)"],
        ["Forecast Stability", f"{bd.get('forecast_stability', 0):.1f}", "15",
         "Forecast coefficient of variation (lower = better)"],
        ["Budget Discipline", f"{bd.get('budget_discipline', 0):.1f}", "15",
         "Avg |actual − budget| ÷ budget (target < 10%)"],
        ["Expense Volatility", f"{bd.get('expense_volatility', 0):.1f}", "15",
         "Year-on-year expense variance (target < 15%)"],
        ["TOTAL", f"{health['score']:.1f}", "100", ""],
    ]
    ht = Table(health_data, colWidths=[5 * cm, 2 * cm, 1.5 * cm, 8.5 * cm])
    ht.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.whitesmoke, colors.white]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0fdf4")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
    ]))
    story.append(ht)
    story.append(Spacer(1, 0.5 * cm))

    story.append(PageBreak())

    # ── Section 6: Top 10 Lots by Cost ───────────────────────────────────────
    story.append(Paragraph("6. Top 10 Lots by Total Cost", h1_style))
    if lot_summaries:
        lot_data = [["Unit", "Admin Paid", "Sinking Paid", "Council/Tax", "Water", "Total", "Arrears"]]
        for s in lot_summaries[:10]:
            lot_data.append([
                html_lib.escape(str(s.get("lot_number", ""))),
                f"${s.get('total_admin_paid', 0):,.0f}",
                f"${s.get('total_sinking_paid', 0):,.0f}",
                f"${s.get('total_council', 0) + s.get('total_land_tax', 0):,.0f}",
                f"${s.get('total_water', 0):,.0f}",
                f"${s.get('total_cost', 0):,.0f}",
                "YES" if s.get("arrears_flag") else "no",
            ])
        lt = Table(lot_data, colWidths=[2.2 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2 * cm, 2.3 * cm, 2 * cm])
        lts = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]
        for i, s in enumerate(lot_summaries[:10]):
            if s.get("arrears_flag"):
                lts.append(("TEXTCOLOR", (6, i + 1), (6, i + 1), colors.HexColor("#ef4444")))
                lts.append(("FONTNAME", (6, i + 1), (6, i + 1), "Helvetica-Bold"))
        lt.setStyle(TableStyle(lts))
        story.append(lt)
    else:
        story.append(Paragraph("No lot summary data available for this year.", body_style))

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 7: Forecast Summary ───────────────────────────────────────────
    story.append(Paragraph("7. 3-Year Expense Forecast", h1_style))
    if forecasts:
        # Chart first
        forecast_png = _make_chart_forecast_3yr(forecasts)
        forecast_img = _embed_png(forecast_png, width_cm=16)
        if forecast_img:
            story.append(forecast_img)
            story.append(Spacer(1, 0.3 * cm))

        fc_data = [["Fund", "Category", "Method", "Y+1", "Y+2", "Y+3", "Confidence"]]
        for f in forecasts[:20]:
            fc_data.append([
                f.get("fund_type", "").title()[:5],
                f.get("category", "")[:20],
                f.get("method", ""),
                f"${f.get('projection_year_1', 0):,.0f}",
                f"${f.get('projection_year_2', 0):,.0f}",
                f"${f.get('projection_year_3', 0):,.0f}",
                f"{f.get('confidence_score', 0):.0%}",
            ])
        ft = Table(fc_data, colWidths=[1.5 * cm, 5 * cm, 2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.5 * cm])
        ft.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(ft)
    else:
        story.append(Paragraph(
            "No forecast data available. Run forecast generation to populate this section.",
            body_style,
        ))

    story.append(Spacer(1, 0.5 * cm))

    # ── Section 8: Levy Rate Projection ──────────────────────────────────────
    story.append(Paragraph("8. 3-Year Levy Rate Projection", h1_style))
    if levy_projection and levy_projection.get("combined"):
        levy_png = _make_chart_levy_projection(levy_projection)
        levy_img = _embed_png(levy_png, width_cm=14)
        if levy_img:
            story.append(levy_img)
            story.append(Spacer(1, 0.3 * cm))
        combined = levy_projection.get("combined", {})
        proj_data = [["Year", "Admin Rate/UOE", "Sinking Rate/UOE", "Total Rate/UOE", "Admin Total", "Sinking Total"]]
        for yr in sorted(combined.keys()):
            yd = combined[yr]
            proj_data.append([
                yr,
                f"${yd.get('admin_rate_per_uoe', 0):,.2f}",
                f"${yd.get('sinking_rate_per_uoe', 0):,.2f}",
                f"${yd.get('admin_rate_per_uoe', 0) + yd.get('sinking_rate_per_uoe', 0):,.2f}",
                f"${yd.get('total_admin_levy', 0):,.2f}",
                f"${yd.get('total_sinking_levy', 0):,.2f}",
            ])
        pt = Table(proj_data, colWidths=[2 * cm, 3 * cm, 3 * cm, 3 * cm, 3 * cm, 3 * cm])
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(pt)
    else:
        story.append(Paragraph(
            "No levy projection data available. Run forecast generation to populate.",
            body_style,
        ))

    story.append(PageBreak())

    # ── Section 9: Maintenance & Infrastructure Intelligence ─────────────────
    story.append(Paragraph("9. Maintenance & Infrastructure Intelligence", h1_style))

    if maint_summary:
        story.append(Paragraph(
            f"Building Asset Health Score: <b>{maint_summary.get('asset_health_score', 0)}/100</b>. "
            f"Estimated maintenance cost for next 12 months: <b>${maint_summary.get('expected_maintenance_12m', 0):,.2f}</b>. "
            f"Identified High Risk Assets: {maint_summary.get('high_risk_assets_count', 0)}.",
            body_style
        ))
        story.append(Spacer(1, 0.4 * cm))

        # Capital Works Table
        if cap_works:
            story.append(Paragraph("10-Year Capital Works Outlook", h2_style))
            cw_data = [["Year", "Asset", "Estimated Cost (CPI Adj)"]]
            for cw in cap_works[:15]:
                cw_data.append([
                    str(cw["replacement_year"]),
                    cw["asset_name"],
                    f"${cw['estimated_cost']:,.2f}"
                ])
            cwt = Table(cw_data, colWidths=[3 * cm, 8 * cm, 6 * cm])
            cwt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(cwt)
    else:
        story.append(Paragraph("Maintenance intelligence data not available for this period.", body_style))

    story.append(PageBreak())

    # ── Section 10: Appendix — Methodology ────────────────────────────────────
    story.append(Paragraph("10. Appendix — Methodology & Assumptions", h1_style))

    story.append(Paragraph("10.1 Forecast Models", h2_style))
    forecast_method_data = [
        ["Model", "When Used", "Description"],
        ["Linear Regression", "3+ years of historical data",
         "NumPy polyfit on actuals. Confidence = R² value."],
        ["Inflation Model", "Fewer than 3 years of data",
         f"Compound inflation at {3.5:.1f}% CPI (Australian RBA benchmark). Confidence = 0.50."],
        ["Capital Works", "User-flagged capital items",
         "Spreads budgeted capital expenditure over 3 years equally."],
    ]
    fmt = Table(forecast_method_data, colWidths=[3.5 * cm, 4.5 * cm, 9 * cm])
    fmt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("WORDWRAP", (2, 1), (2, -1), True),
    ]))
    story.append(fmt)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("10.2 Anomaly Detection Thresholds", h2_style))
    anomaly_thresh_data = [
        ["Anomaly Type", "Threshold", "Severity Logic"],
        ["Budget Overrun", "> 20% over budgeted", "Deviation ≥100%=critical, ≥50%=high, ≥20%=medium"],
        ["Unbudgeted Category", "Actual > 0, Budgeted = 0", "Always high severity"],
        ["Expense Spike", "> 30% above 3-yr rolling average", "Scales with deviation magnitude"],
        ["Owner Arrears", "2+ unpaid quarterly levies", "Scales with arrears duration"],
        ["Interest Spike", "> 50% year-on-year increase", "high severity"],
        ["Forecast Deficit", "Closing balance < 0", "critical severity"],
        ["Cashflow Risk", "Min monthly balance < 0", "medium severity"],
    ]
    at_thresh = Table(anomaly_thresh_data, colWidths=[4 * cm, 4.5 * cm, 8.5 * cm])
    at_thresh.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(at_thresh)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("10.3 Health Score Weights", h2_style))
    story.append(Paragraph(
        "Total score is 100 points across 6 components: Surplus Ratio (20), Arrears % (20), "
        "Cashflow Buffer (15), Forecast Stability (15), Budget Discipline (15), "
        "Expense Volatility (15).",
        body_style,
    ))
    score_tiers_data = [
        ["Score Range", "Risk Level", "Interpretation"],
        ["80 – 100", "Excellent", "Strong financial position. All indicators healthy."],
        ["65 – 79", "Good", "Generally healthy. Minor areas to monitor."],
        ["50 – 64", "Moderate", "Some risk factors present. Action recommended."],
        ["35 – 49", "At Risk", "Multiple risk factors. Management intervention required."],
        ["0 – 34", "Critical", "Serious financial concerns. Immediate action needed."],
    ]
    st = Table(score_tiers_data, colWidths=[3 * cm, 3 * cm, 11 * cm])
    st.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(st)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("10.4 Key Assumptions", h2_style))
    assumptions = [
        "CPI inflation rate: 3.5% (Australian RBA benchmark, FY2026)",
        "Budget overrun threshold: 20% above budgeted amount",
        "Arrears threshold: 2 or more unpaid quarterly levy instalments",
        "Cashflow risk: any month where cumulative balance falls below $0",
        "Cashflow buffer target: 2 months of operating expenses",
        "Surplus ratio target: ≥ 10% (closing balance as % of total income)",
        "Levy simulator bounds: -5% (decrease scenario) to +50% (capital-heavy year)",
        f"Building AUV for rates: {_block_auv_str} ({strata_address})",
        f"Total Unit of Entitlement (UOE): {_total_ue:,} across all lots",
    ]
    for assumption in assumptions:
        story.append(Paragraph(f"• {html_lib.escape(str(assumption))}", bullet_style))

    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"CONFIDENTIAL — {building_name} Strata Plan {plan_number} — {_now_str()} — "
        "Generated by StrataOS Intelligence Engine v1.0",
        footer_style,
    ))

    doc.build(story)
    return buf.getvalue()


async def generate_special_levy_report(building_id: str) -> bytes:
    """Generated function header.

    Function: generate_special_levy_report
    Path: backend/services/report_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not REPORTLAB_AVAILABLE:
        return b"ReportLab not available"

    from services.special_levy_service import compute_special_levy_forecast

    forecast = await compute_special_levy_forecast(building_id)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title="Special Levy Risk Report",
        author="Eastgate Intelligence Engine",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleSL", parent=styles["Title"], fontSize=18, spaceAfter=14,
                                 textColor=colors.HexColor("#2F4F4F"))
    h1_style = ParagraphStyle("H1SL", parent=styles["Heading1"], fontSize=12, spaceAfter=8,
                              textColor=colors.HexColor("#2F4F4F"))
    body_style = styles["BodyText"]

    story = []
    # Branding for special levy report
    b_settings = await get_general_settings_or_default(building_id, {"_id": 0})
    building_name = b_settings.get("building_name", "East Gate Residences")
    plan_number = b_settings.get("plan_number", "13195")

    story.append(Paragraph("SPECIAL LEVY RISK REPORT", title_style))
    story.append(
        Paragraph(f"{html_lib.escape(building_name)} — Strata Plan {html_lib.escape(plan_number)}", body_style))
    story.append(Spacer(1, 0.4 * cm))

    summary = [
        ["Metric", "Value"],
        ["Projection Horizon", f"{forecast.get('horizon_years', 10)} years"],
        ["Probability of Special Levy", f"{forecast.get('probability', 0)}%"],
        ["Median Levy per Unit", f"${forecast.get('median_amount', 0):,.0f}"],
        ["P90 Levy per Unit", f"${forecast.get('p90_amount', 0):,.0f}"],
        ["Worst Case Levy", f"${forecast.get('worst_case', 0):,.0f}"],
        ["Simulation Runs", str(forecast.get("simulation_runs", 0))],
    ]
    t = Table(summary, colWidths=[7 * cm, 9 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Shock Timing (Probability by Year)", h1_style))
    timeline = forecast.get("shock_year_distribution", {})
    timeline_rows = [["Year", "Probability"]]
    for year in sorted(timeline.keys()):
        timeline_rows.append([str(year), f"{timeline[year]}%"])
    tt = Table(timeline_rows, colWidths=[4 * cm, 6 * cm])
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tt)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Benefit Group Impact Summary", h1_style))
    groups = forecast.get("group_summary", [])
    group_rows = [["Group", "Probability", "Median Levy", "Worst Case"]]
    for g in groups:
        group_rows.append([
            g.get("group", "Group"),
            f"{g.get('probability', 0)}%",
            f"${g.get('median_amount', 0):,.0f}",
            f"${g.get('worst_case', 0):,.0f}",
        ])
    gt = Table(group_rows, colWidths=[6 * cm, 3 * cm, 4 * cm, 4 * cm])
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(gt)
    story.append(Spacer(1, 0.4 * cm))

    if forecast.get("explanation"):
        story.append(Paragraph("Owner Summary", h1_style))
        story.append(Paragraph(html_lib.escape(forecast["explanation"]), body_style))
        story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    return buf.getvalue()
