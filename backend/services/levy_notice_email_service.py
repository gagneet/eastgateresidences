# @featuretrace:levy-notice-delivery
# Layer: service
# Data flow: cron_payment_reminders.send_levy_reminders → resolve_managing_agent_branding(building_id)
#            → db.settings (type="general") + db.buildings branding → build_levy_notice_email_kwargs
#            → utils.email.get_email_template("levy_notice", ...) → utils.email.send_email_async
# Related: backend/utils/email.py (levy_notice template)
#          backend/cron/cron_payment_reminders.py (send_levy_reminders)
#          backend/utils/pdf_generator.py (generate_levy_notice_pdf branding header)
# Toggle: levy_reminders (gated by the cron caller)
"""Managing-agent branding + email-context resolution for automated Levy Notices.

The reference levy notices (Civium ACT/NSW) carry the *managing strata company's*
identity — name, ABN, address, phone, levies-department contacts — not the building's.
StrataOS is multi-tenant, so this must be resolved per building rather than hardcoded.

`resolve_managing_agent_branding` returns a single consolidated dict so the email
template and the PDF header render from one source of truth. Every field has a
platform-default fallback (the "StrataOS" brand values from the requested template)
so a building with no configured managing-agent block still produces a valid notice.

The literal string "StrataOS" is only ever a *default*: when a building configures
`strata_management_company` (or the legacy `strata_manager_name`/`manager_name`),
that company name is substituted everywhere the template would otherwise say StrataOS.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Platform brand defaults ──────────────────────────────────────────────────
# These mirror the requested template verbatim and are used only when a building
# has no managing-agent branding configured in its general settings.
DEFAULT_COMPANY_NAME = "StrataOS"
DEFAULT_COMPANY_PHONE = "02 2222 3333"
DEFAULT_COMPANY_EMAIL = "info@strataos.live"
DEFAULT_COMPANY_ADDRESS = "PO BOX 919 Canberra, ACT, 2600"
DEFAULT_LEVIES_PHONE = "1300 888 999"
DEFAULT_LEVIES_EMAIL = "levies@strataos.live"
DEFAULT_SUPPORT_DOMAIN = "strataos.live"

# Which of the two requested email formats a building uses by default.
STANDARD_FORMAT = "standard"
LEVIES_TEAM_FORMAT = "levies_team"
DEFAULT_EMAIL_FORMAT = STANDARD_FORMAT
_VALID_FORMATS = {STANDARD_FORMAT, LEVIES_TEAM_FORMAT}

# Grace period (no interest/arrears) applying per state, surfaced in the
# "levies_team" format. Sourced from the requested template + confirmed against
# ACT Unit Titles (Management) Act 2011 / NSW Strata Schemes Management Act 2015.
# The building's own state row can be overridden by its configured grace_period_days.
GRACE_PERIODS_BY_STATE: Dict[str, str] = {
    "ACT": "30 days",
    "VIC": "28 days",
    "NSW": "30 days",
    "QLD": (
        "We can confirm a grace period is applicable to QLD levies, but it is "
        "recommended payment is made by the due date to ensure, for those "
        "buildings with a discount, the discount is applicable and owners are "
        "financial at the Annual General Meeting in order to vote."
    ),
}


def _first_nonempty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def resolve_email_format(settings: Optional[Dict[str, Any]]) -> str:
    """Return the configured levy-notice email format, validated to a known value."""
    raw = (settings or {}).get("levy_notice_email_format")
    fmt = str(raw).strip().lower() if raw else DEFAULT_EMAIL_FORMAT
    return fmt if fmt in _VALID_FORMATS else DEFAULT_EMAIL_FORMAT


def _support_email(settings: Dict[str, Any]) -> str:
    """Build the per-plan support address, e.g. ``UP13195@strataos.live``.

    An explicit ``levy_notice_support_email`` in settings wins; otherwise it is
    derived from the plan number and the (configurable) support domain.
    """
    explicit = _first_nonempty(settings.get("levy_notice_support_email"))
    if explicit:
        return explicit
    domain = _first_nonempty(settings.get("levy_notice_support_domain")) or DEFAULT_SUPPORT_DOMAIN
    plan = _first_nonempty(settings.get("plan_number"), settings.get("strata_plan_number"))
    local = f"UP{plan}" if plan else "levies"
    return f"{local}@{domain}"


def resolve_managing_agent_branding(
    building_id: str,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve the managing strata company's branding block for a building.

    Args:
        building_id: tenant/building identifier (for logging + support-address fallback).
        settings: the building's general-settings document (``type="general"``).
            Callers that already loaded it (the cron does) pass it in to avoid a
            second DB round-trip. When ``None`` an empty dict is assumed and the
            platform defaults are returned.

    Returns:
        A dict with keys consumed by the email template and PDF header:
        ``company_name``, ``company_phone``, ``company_email``, ``company_address``,
        ``levies_phone``, ``levies_email``, ``support_email``, ``logo_url``,
        ``building_abn``, ``email_format``.
    """
    s = settings or {}

    company_name = _first_nonempty(
        s.get("strata_management_company"),
        s.get("strata_manager_name"),
        s.get("manager_name"),
    ) or DEFAULT_COMPANY_NAME

    branding = {
        "building_id": building_id,
        "company_name": company_name,
        "company_phone": _first_nonempty(s.get("strata_manager_phone"), s.get("manager_phone"))
        or DEFAULT_COMPANY_PHONE,
        "company_email": _first_nonempty(s.get("strata_manager_email"), s.get("manager_email"))
        or DEFAULT_COMPANY_EMAIL,
        "company_address": _first_nonempty(
            s.get("strata_manager_address"),
            s.get("manager_address"),
        )
        or DEFAULT_COMPANY_ADDRESS,
        "levies_phone": _first_nonempty(s.get("levies_department_phone")) or DEFAULT_LEVIES_PHONE,
        "levies_email": _first_nonempty(s.get("levies_department_email")) or DEFAULT_LEVIES_EMAIL,
        "support_email": _support_email(s),
        "logo_url": _first_nonempty(s.get("logo_url"), s.get("building_logo_url")),
        "building_abn": _first_nonempty(s.get("building_abn")) or "",
        "email_format": resolve_email_format(s),
    }
    return branding


def build_grace_period_lines(
    state: Optional[str] = None,
    grace_period_days: Optional[int] = None,
) -> list[str]:
    """Return the per-state grace-period lines for the ``levies_team`` format.

    If the building's own ``state`` + configured ``grace_period_days`` are known,
    that state's line reflects the configured value rather than the statutory
    default, so the notice never contradicts the building's own settings.
    """
    lines: Dict[str, str] = dict(GRACE_PERIODS_BY_STATE)
    state_code = (state or "").strip().upper()
    if state_code in lines and grace_period_days:
        try:
            days = int(grace_period_days)
            if days > 0:
                lines[state_code] = f"{days} days"
        except (TypeError, ValueError):
            pass
    return [f"{code}: {text}" for code, text in lines.items()]


__all__ = [
    "STANDARD_FORMAT",
    "LEVIES_TEAM_FORMAT",
    "DEFAULT_EMAIL_FORMAT",
    "GRACE_PERIODS_BY_STATE",
    "resolve_managing_agent_branding",
    "resolve_email_format",
    "build_grace_period_lines",
]
