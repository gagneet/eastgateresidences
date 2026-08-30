# @featuretrace:gst-bas — GST rate/registration resolution helper (settings-only, no ledger math).
# Layer: service
# Data flow: routers/gst_bas.py → get_building_levy_gst_settings() → db.settings (building-scoped).
# Related: backend/routers/gst_bas.py
#           frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
# Collection: settings
from __future__ import annotations

from typing import Any, Dict, Optional

from services.settings_service import get_general_settings_or_default

DEFAULT_LEVY_GST_RATE = 0.10
DEFAULT_GST_REGISTERED = True

# ATO non-profit compulsory-GST-registration threshold. An Owners Corporation is a non-profit
# body, so GST registration is COMPULSORY when annual (ex-GST) turnover is A$150,000 or more, and
# voluntary below it. Stored in integer cents (CLAUDE.md ledger-money rule): $150,000 = 15,000,000c.
# Source: docs/architecture/onboarding/04_onboarding_financial_data_flow.md §6.
GST_REGISTRATION_THRESHOLD_CENTS = 15_000_000


def _normalize_gst_rate(raw_rate: Any, default: float = DEFAULT_LEVY_GST_RATE) -> float:
    """Generated function header.

    Function: _normalize_gst_rate
    Path: backend/services/gst_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        rate = float(raw_rate)
    except (TypeError, ValueError):
        return default

    if rate > 1:
        rate = rate / 100.0
    if rate < 0:
        return 0.0
    return rate


def format_gst_percent(rate: float) -> str:
    """Generated function header.

    Function: format_gst_percent
    Path: backend/services/gst_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    percent = round(rate * 100, 4)
    if float(percent).is_integer():
        return f"{int(percent)}%"
    return f"{percent:g}%"


def parse_levy_gst_settings(
    settings_doc: Optional[Dict[str, Any]] = None,
    *,
    annual_ex_gst_income_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve a building's effective GST registration + rate for levy/expense math.

    Registration is the OR of two independent facts (per the ATO non-profit rule codified in
    ``docs/architecture/onboarding/04_onboarding_financial_data_flow.md`` §6):

    ``gst_registered = manual_gst_registered_flag OR (annual_ex_gst_income >= $150,000)``

    Parameters
    ----------
    settings_doc:
        The building's general-settings document. ``gst_registered`` is the manual flag
        (defaults to ``True``); ``levy_gst_rate`` is the configured rate (defaults to 10%).
    annual_ex_gst_income_cents:
        The approved annual ex-GST levy income (admin + sinking), in integer cents. When
        supplied, the ATO $150k compulsory-registration threshold is applied. When ``None``
        (the default — preserves the historical behaviour for every existing caller), only the
        manual flag is used and ``gst_registration_compulsory`` is returned as ``None`` (unknown).

    Returns
    -------
    dict with:
        ``gst_registered`` (effective), ``manual_gst_registered`` (the raw flag),
        ``gst_registration_compulsory`` (True/False when income supplied, else None),
        ``gst_registration_basis`` (``manual_flag`` | ``ato_threshold`` | ``manual_and_threshold``),
        ``levy_gst_rate`` (configured), ``effective_gst_rate`` (0.0 when not registered),
        ``gst_multiplier``, and ``gst_label``.

    Note
    ----
    A threshold-driven registration is SURFACED via ``gst_registration_compulsory`` /
    ``gst_registration_basis`` — never a silent flip. A building manually marked unregistered
    whose income crosses the threshold is reported as ``compulsory=True`` so the effect on owner
    due amounts can be shown, per the contract's "surface, not repair" rule.
    """
    settings_doc = settings_doc or {}
    manual_registered = bool(settings_doc.get("gst_registered", DEFAULT_GST_REGISTERED))
    configured_rate = _normalize_gst_rate(settings_doc.get("levy_gst_rate"), DEFAULT_LEVY_GST_RATE)

    compulsory: Optional[bool] = None
    basis = "manual_flag"
    if annual_ex_gst_income_cents is not None:
        compulsory = int(annual_ex_gst_income_cents) >= GST_REGISTRATION_THRESHOLD_CENTS
        if compulsory and manual_registered:
            basis = "manual_and_threshold"
        elif compulsory:
            basis = "ato_threshold"
        else:
            basis = "manual_flag"

    gst_registered = manual_registered or bool(compulsory)
    effective_rate = configured_rate if gst_registered else 0.0

    return {
        "gst_registered": gst_registered,
        "manual_gst_registered": manual_registered,
        "gst_registration_compulsory": compulsory,
        "gst_registration_basis": basis,
        "levy_gst_rate": configured_rate,
        "effective_gst_rate": effective_rate,
        "gst_multiplier": 1 + effective_rate,
        "gst_label": f"GST ({format_gst_percent(effective_rate)})",
    }


async def get_building_levy_gst_settings(
        building_id: str,
        settings_doc: Optional[Dict[str, Any]] = None,
        settings_db=None,
) -> Dict[str, Any]:
    """Generated function header.

    Function: get_building_levy_gst_settings
    Path: backend/services/gst_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if settings_doc is None:
        settings_doc = await get_general_settings_or_default(
            building_id,
            {"_id": 0},
            settings_db=settings_db,
        )
    return parse_levy_gst_settings(settings_doc)
