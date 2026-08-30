# @featuretrace:levy-notice-delivery
# Layer: test
# Data flow: unit tests for the automated Levy Notice email + PDF branding pipeline.
# Related: backend/services/levy_notice_email_service.py
#          backend/utils/email.py (levy_notice template)
#          backend/utils/pdf_generator.py (generate_levy_notice_pdf branding header)
"""Unit tests for the automated Levy Notice email + PDF branding.

Covers:
  * Managing-agent branding resolution — platform defaults vs. per-building
    override (the "StrataOS" → strata company name substitution).
  * The ``levy_notice`` email template — both selectable formats, company-name
    substitution, per-state grace periods, per-plan support address, and the
    plain-text alternative.
  * The levy-notice PDF — branding header + owner-name rendering + NSW hardship
    statement + BuildingSettingsIncompleteError guard.

These are pure/unit tests: no DB, no network. The branding resolver and email
template are building-agnostic and receive already-resolved kwargs.
"""
import pytest

from services.levy_notice_email_service import (
    DEFAULT_COMPANY_NAME,
    LEVIES_TEAM_FORMAT,
    STANDARD_FORMAT,
    build_grace_period_lines,
    resolve_email_format,
    resolve_managing_agent_branding,
)


# ── Branding resolution ───────────────────────────────────────────────────────

def test_branding_defaults_to_platform_values_when_unconfigured():
    b = resolve_managing_agent_branding("13195", {})
    assert b["company_name"] == DEFAULT_COMPANY_NAME == "StrataOS"
    assert b["company_phone"] == "02 2222 3333"
    assert b["company_email"] == "info@strataos.live"
    assert b["company_address"] == "PO BOX 919 Canberra, ACT, 2600"
    assert b["levies_phone"] == "1300 888 999"
    assert b["levies_email"] == "levies@strataos.live"
    # No plan number configured → generic levies@ support address.
    assert b["support_email"] == "levies@strataos.live"
    assert b["email_format"] == STANDARD_FORMAT


def test_branding_substitutes_configured_strata_company():
    settings = {
        "building_id": "13195",
        "plan_number": "13195",
        "strata_management_company": "Civium Property Group",
        "strata_manager_phone": "1300 724 256",
        "strata_manager_email": "levies@civium.com.au",
        "strata_manager_address": "Locked Bag 8300, Canberra ACT 2601",
        "levy_notice_email_format": "levies_team",
    }
    b = resolve_managing_agent_branding("13195", settings)
    assert b["company_name"] == "Civium Property Group"
    assert b["company_phone"] == "1300 724 256"
    assert b["company_email"] == "levies@civium.com.au"
    assert b["email_format"] == LEVIES_TEAM_FORMAT
    # Per-plan support address is derived from the plan number.
    assert b["support_email"] == "UP13195@strataos.live"


def test_branding_legacy_manager_name_fallback():
    # Older buildings only have strata_manager_name / manager_name set.
    b = resolve_managing_agent_branding("X", {"strata_manager_name": "Acme Strata"})
    assert b["company_name"] == "Acme Strata"
    b2 = resolve_managing_agent_branding("X", {"manager_name": "Legacy Co"})
    assert b2["company_name"] == "Legacy Co"


def test_branding_explicit_support_email_wins():
    b = resolve_managing_agent_branding(
        "X", {"plan_number": "9999", "levy_notice_support_email": "help@example.com"}
    )
    assert b["support_email"] == "help@example.com"


def test_branding_custom_support_domain():
    b = resolve_managing_agent_branding(
        "X", {"plan_number": "13195", "levy_notice_support_domain": "civium.com.au"}
    )
    assert b["support_email"] == "UP13195@civium.com.au"


def test_resolve_email_format_rejects_unknown_values():
    assert resolve_email_format({"levy_notice_email_format": "bogus"}) == STANDARD_FORMAT
    assert resolve_email_format({"levy_notice_email_format": "LEVIES_TEAM"}) == LEVIES_TEAM_FORMAT
    assert resolve_email_format(None) == STANDARD_FORMAT


# ── Grace-period lines ────────────────────────────────────────────────────────

def test_grace_period_lines_defaults():
    lines = build_grace_period_lines()
    assert "ACT: 30 days" in lines
    assert "VIC: 28 days" in lines
    assert "NSW: 30 days" in lines
    assert any(line.startswith("QLD:") for line in lines)


def test_grace_period_lines_building_state_override():
    # A building whose configured grace period differs from the statutory default
    # reflects its own value for its own state, leaving other states untouched.
    lines = build_grace_period_lines("ACT", 21)
    assert "ACT: 21 days" in lines
    assert "VIC: 28 days" in lines


def test_grace_period_lines_ignore_bad_override():
    lines = build_grace_period_lines("ACT", 0)
    assert "ACT: 30 days" in lines  # 0 is not a valid override


# ── Email template ────────────────────────────────────────────────────────────

_BRANDING_KWARGS = {
    "company_name": "Civium Property Group",
    "company_phone": "1300 724 256",
    "company_email": "levies@civium.com.au",
    "company_address": "Locked Bag 8300, Canberra ACT 2601",
    "levies_phone": "1300 888 999",
    "levies_email": "levies@strataos.live",
    "support_email": "UP13195@strataos.live",
}


def test_email_standard_variant_substitutes_company_and_details():
    from utils.email import get_email_template

    html, text = get_email_template(
        "levy_notice",
        format_variant="standard",
        owner_name="Avneet Rooprai & Gagneet Singh",
        unit_plan="13195",
        lot_number="87",
        due_date="01/09/2026",
        amount_due="1,517.53",
        **_BRANDING_KWARGS,
    )
    # Company name substituted; no leftover platform brand in the standard body/footer.
    assert "Civium Property Group" in html
    assert "StrataOS" not in html
    assert "U/Plan 13195 Lot 87" in html
    assert "UP13195@strataos.live" in html
    assert "Best regards" in html
    # Owner name is HTML-escaped (ampersand) but present.
    assert "Avneet Rooprai" in html
    # Plain-text alternative mirrors the content.
    assert "Best regards" in text
    assert "Civium Property Group" in text
    assert "U/Plan 13195 Lot 87" in text


def test_email_levies_team_variant_lists_grace_periods():
    from utils.email import get_email_template

    html, text = get_email_template(
        "levy_notice",
        format_variant="levies_team",
        owner_name="Owner",
        unit_plan="13195",
        lot_number="87",
        due_date="01/09/2026",
        grace_period_lines=build_grace_period_lines("ACT", None),
        **_BRANDING_KWARGS,
    )
    assert "Levies and Arrears Team" in html
    assert "ACT: 30 days" in html
    assert "VIC: 28 days" in html
    assert "Levies Department" in html
    assert "grace period" in text
    assert "Levies and Arrears Team" in text


def test_email_escapes_owner_supplied_html():
    from utils.email import get_email_template

    html, _text = get_email_template(
        "levy_notice",
        format_variant="standard",
        owner_name="<script>alert(1)</script>",
        unit_plan="1",
        lot_number="1",
        **_BRANDING_KWARGS,
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_email_unknown_template_returns_empty():
    from utils.email import get_email_template

    assert get_email_template("does_not_exist") == ("", "")


# ── PDF ───────────────────────────────────────────────────────────────────────

def _pdf_building_settings(**overrides):
    base = {
        "building_id": "13195",
        "plan_number": "13195",
        "strata_address": "14 Hoolihan Street, DENMAN PROSPECT ACT 2611",
        "building_abn": "98 212 234 337",
        "deft_ref": "26061110862701425048",
        "bpay_biller_code": "96503",
        "levy_interest_rate_pa": 10.0,
    }
    base.update(overrides)
    return base


_LEVY_DATA = {
    "notice_date": "03/08/2026",
    "due_date": "01/09/2026",
    "period": "01/07/26 - 30/09/26",
    "admin_amount": 1117.02,
    "sinking_amount": 400.51,
    "total_amount": 1517.53,
}
_UNIT_DATA = {"unit_number": "87", "lot_number": "87", "entitlement": 161.0}
_OWNER_DATA = {
    "full_name": "Avneet Rooprai & Gagneet Singh",
    "address": "87/14 Hoolihan Street, DENMAN PROSPECT ACT 2611",
}


def test_pdf_renders_with_managing_agent_branding():
    from utils.pdf_generator import PDF_AVAILABLE, generate_levy_notice_pdf

    if not PDF_AVAILABLE:
        pytest.skip("reportlab not installed")

    settings = _pdf_building_settings(strata_management_company="Civium Property Group")
    pdf = generate_levy_notice_pdf(
        {**_LEVY_DATA, "jurisdiction": "ACT"},
        _UNIT_DATA,
        _OWNER_DATA,
        building_name="East Gate Residences",
        building_settings=settings,
    )
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_pdf_missing_plan_number_raises():
    from utils.pdf_generator import (
        PDF_AVAILABLE,
        BuildingSettingsIncompleteError,
        generate_levy_notice_pdf,
    )

    if not PDF_AVAILABLE:
        pytest.skip("reportlab not installed")

    settings = _pdf_building_settings()
    settings.pop("plan_number")
    with pytest.raises(BuildingSettingsIncompleteError):
        generate_levy_notice_pdf(_LEVY_DATA, _UNIT_DATA, _OWNER_DATA, building_settings=settings)


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Count page objects in a reportlab PDF without a PDF library (streams are
    zlib-compressed, so text can't be grepped — but the ``/Type /Page`` object
    markers are in the clear). Excludes the single ``/Type /Pages`` catalog node."""
    import re

    return len(re.findall(rb"/Type\s*/Page(?![s])", pdf_bytes))


def test_pdf_enhanced_arrears_notice_is_single_page():
    """Arrears + interest + late fee + NSW hardship must all fit on one A4 page."""
    from utils.pdf_generator import PDF_AVAILABLE, generate_levy_notice_pdf

    if not PDF_AVAILABLE:
        pytest.skip("reportlab not installed")

    settings = _pdf_building_settings(
        strata_management_company="Civium Property Group",
        strata_manager_address="Locked Bag 8300, Canberra ACT 2601",
        bank_name="ANZ",
        bank_bsb="012-345",
        bank_account_number="1234 5678",
        levy_notice_disclaimer="Issued under the Unit Titles (Management) Act 2011. Retain for your records.",
    )
    levy_data = {
        **_LEVY_DATA,
        "jurisdiction": "NSW",  # adds the mandatory hardship statement
        "arrears_amount": 2450.00,
        "interest_amount": 36.75,
        "penalty_amount": 110.00,
    }
    pdf = generate_levy_notice_pdf(
        levy_data, _UNIT_DATA, _OWNER_DATA,
        building_name="East Gate Residences", building_settings=settings,
    )
    assert pdf[:4] == b"%PDF"
    assert _pdf_page_count(pdf) == 1


def test_pdf_credit_and_uptodate_notices_render_single_page():
    """Both the in-credit and up-to-date variants render and stay single-page."""
    from utils.pdf_generator import PDF_AVAILABLE, generate_levy_notice_pdf

    if not PDF_AVAILABLE:
        pytest.skip("reportlab not installed")

    settings = _pdf_building_settings(strata_management_company="Civium Property Group")
    credit = generate_levy_notice_pdf(
        {**_LEVY_DATA, "jurisdiction": "ACT", "credit_amount": 800.00},
        _UNIT_DATA, _OWNER_DATA, building_settings=settings,
    )
    uptodate = generate_levy_notice_pdf(
        {**_LEVY_DATA, "jurisdiction": "ACT"},
        _UNIT_DATA, _OWNER_DATA, building_settings=settings,
    )
    assert _pdf_page_count(credit) == 1
    assert _pdf_page_count(uptodate) == 1


# ── Per-unit financial position resolver ──────────────────────────────────────

def _financial_context(**overrides):
    """A hand-built context (no DB) mirroring prepare_levy_notice_financial_context's
    output, for exercising resolve_unit_levy_position purely."""
    ctx = {
        "arrears_map": {
            "42": {"net_balance": 2450.0, "arrears": 2450.0, "credit": 0.0,
                   "in_arrears": True, "in_credit": False},
            "07": {"net_balance": -800.0, "arrears": 0.0, "credit": 800.0,
                   "in_arrears": False, "in_credit": True},
        },
        "ledger_map": {
            "42": {"unit_number": "42", "total_levied": 6000.0, "total_paid": 3550.0},
            "07": {"unit_number": "07", "total_levied": 6000.0, "total_paid": 6800.0},
        },
        "rate_info": {"rate_pct": 10.0, "max_rate_pct": 20.0},
        "late_fee_policy": {"enabled": True, "amount": 55.0, "period_days": 14,
                            "gst_inclusive": True, "start_year": None},
        "payment_schedule": [
            {"quarter": "Q1", "due_date": "2026-03-31"},
            {"quarter": "Q2", "due_date": "2026-06-30"},
            {"quarter": "Q3", "due_date": "2026-09-30"},
            {"quarter": "Q4", "due_date": "2026-12-31"},
        ],
        "grace_period_days": 14,
        "levy_year_int": 2026,
    }
    ctx.update(overrides)
    return ctx


def test_build_payment_schedule_from_settings():
    from services.levy_notice_financials import _build_payment_schedule

    sched = _build_payment_schedule(
        {"levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "last",
         "levy_collection_frequency": "quarterly", "financial_year_start_month": 1},
        2026,
    )
    assert [s["due_date"] for s in sched] == ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]
    # No configured months → empty schedule (caller reports $0 computed charges).
    assert _build_payment_schedule({}, 2026) == []


def test_resolve_position_arrears_unit_includes_interest_and_penalty():
    from datetime import date

    from services.levy_notice_financials import resolve_unit_levy_position

    pos = resolve_unit_levy_position("42", _financial_context(), today=date(2026, 12, 31))
    assert pos["in_arrears"] is True
    assert pos["arrears"] == 2450.0
    assert pos["credit"] == 0.0
    # Overdue periods accrue both statutory interest and the flat late fee.
    assert pos["interest"] > 0
    assert pos["penalty"] > 0
    assert pos["late_fee_applied"] is True
    assert pos["charges_total"] == round(pos["interest"] + pos["penalty"], 2)


def test_resolve_position_credit_unit_has_no_charges():
    from datetime import date

    from services.levy_notice_financials import resolve_unit_levy_position

    pos = resolve_unit_levy_position("07", _financial_context(), today=date(2026, 12, 31))
    assert pos["in_credit"] is True
    assert pos["credit"] == 800.0
    assert pos["arrears"] == 0.0
    # A unit paid in advance has no overdue principal → no interest, no late fee.
    assert pos["interest"] == 0.0
    assert pos["penalty"] == 0.0


def test_resolve_position_unknown_unit_is_empty():
    from services.levy_notice_financials import resolve_unit_levy_position

    pos = resolve_unit_levy_position("does-not-exist", _financial_context())
    assert pos == {
        "net_balance": 0.0, "arrears": 0.0, "credit": 0.0,
        "in_arrears": False, "in_credit": False,
        "interest": 0.0, "penalty": 0.0, "charges_total": 0.0, "late_fee_applied": False,
    }


def test_resolve_position_no_schedule_skips_charges_but_keeps_arrears():
    from services.levy_notice_financials import resolve_unit_levy_position

    # A building with no due-date schedule can still report arrears/credit; the
    # interest/penalty estimate simply degrades to $0 rather than guessing dates.
    pos = resolve_unit_levy_position("42", _financial_context(payment_schedule=[]))
    assert pos["arrears"] == 2450.0
    assert pos["interest"] == 0.0
    assert pos["penalty"] == 0.0


def test_nil_interest_rate_and_disabled_late_fee_yield_zero_charges():
    from datetime import date

    from services.levy_notice_financials import resolve_unit_levy_position

    ctx = _financial_context(
        rate_info={"rate_pct": 0.0, "max_rate_pct": 20.0},
        late_fee_policy={"enabled": False, "amount": 0.0, "period_days": 14, "start_year": None},
    )
    pos = resolve_unit_levy_position("42", ctx, today=date(2026, 12, 31))
    assert pos["arrears"] == 2450.0  # still in arrears
    assert pos["interest"] == 0.0    # nil rate
    assert pos["penalty"] == 0.0     # policy disabled
    assert pos["late_fee_applied"] is False


# ── Settings model persistence ────────────────────────────────────────────────

def test_settings_model_accepts_branding_and_format_fields():
    """The branding + email-format keys must survive PUT /settings (they were
    previously dropped by Pydantic because they weren't on SiteSettingsUpdate)."""
    from models.settings import SiteSettingsUpdate

    payload = SiteSettingsUpdate(
        strata_management_company="Civium Property Group",
        strata_manager_phone="1300 724 256",
        strata_manager_email="levies@civium.com.au",
        strata_manager_address="Locked Bag 8300, Canberra ACT 2601",
        levies_department_phone="1300 888 999",
        levies_department_email="levies@civium.com.au",
        levy_notice_email_format="levies_team",
        levy_notice_support_domain="civium.com.au",
        levy_notice_disclaimer="Retain for your records.",
    )
    dumped = payload.model_dump(exclude_unset=True)
    assert dumped["strata_management_company"] == "Civium Property Group"
    assert dumped["levy_notice_email_format"] == "levies_team"
    assert dumped["levy_notice_support_domain"] == "civium.com.au"
