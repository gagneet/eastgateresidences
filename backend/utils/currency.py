"""Currency configuration for a building.

# @featuretrace:currency-config — Resolves the currency a building's money is displayed in.
# Layer: service
# Data flow: settings_service.get_general_settings -> resolve_building_currency() -> GET /buildings/me + /settings/general (building-scoped).
# Related: frontend/src/lib/currency.ts (the frontend owner), backend/routers/auth.py (buildings/me)

THE SINGLE HOME for "what currency is this building's money in?".

Amounts are stored in minor units (integer cents) and are currency-agnostic — a
`levy_items.paid_cents` of 176150 is 176150 minor units, whatever the currency. The
CURRENCY is a display concern, resolved per building, and it must be resolved in
exactly one place or two pages will disagree about what "$" means.

Why a code and not a symbol: storing "$" would be ambiguous across AUD, NZD, USD,
SGD and HKD, which all use it. An ISO-4217 code plus a locale lets
`Intl.NumberFormat` derive the correct symbol AND the correct placement, grouping
and decimal separator — "1.234,56 €" is not "$1,234.56" with a different glyph.

Default is AUD, which renders as "$". The platform is Australian strata; a building
with no explicit setting is Australian, not currency-less.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ISO-4217 code + BCP-47 locale. The locale drives grouping/decimal separators and
# symbol placement, so the two travel together and must not be set independently.
DEFAULT_CURRENCY_CODE = "AUD"
DEFAULT_CURRENCY_LOCALE = "en-AU"

# Locale defaults per currency, used when a building sets a code but no locale.
# Deliberately small: every entry here is a jurisdiction this platform could
# plausibly operate in. An unknown code falls back to the default locale rather
# than guessing, because a wrong locale silently reformats every number on screen.
_LOCALE_FOR_CURRENCY: Dict[str, str] = {
    "AUD": "en-AU",
    "NZD": "en-NZ",
    "SGD": "en-SG",
    "HKD": "en-HK",
    "GBP": "en-GB",
    "USD": "en-US",
    "CAD": "en-CA",
    "EUR": "en-IE",
    "AED": "en-AE",
    "ZAR": "en-ZA",
    "INR": "en-IN",
}

SUPPORTED_CURRENCY_CODES = tuple(sorted(_LOCALE_FOR_CURRENCY))


def normalise_currency_code(code: Optional[str]) -> str:
    """Return a supported ISO-4217 code, or the default.

    Never raises and never returns an unsupported code: a bad setting must degrade
    to AUD rather than reach `Intl.NumberFormat`, which throws a RangeError on an
    invalid currency and would blank out every money figure on the page.
    """
    if not code:
        return DEFAULT_CURRENCY_CODE
    candidate = str(code).strip().upper()
    return candidate if candidate in _LOCALE_FOR_CURRENCY else DEFAULT_CURRENCY_CODE


def locale_for_currency(code: Optional[str]) -> str:
    """Default display locale for a currency code."""
    return _LOCALE_FOR_CURRENCY.get(normalise_currency_code(code), DEFAULT_CURRENCY_LOCALE)


def currency_config(settings: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Build the `{code, locale}` payload the frontend formatter consumes.

    `settings` is a building's general-settings document (or None). An explicit
    `currency_locale` wins over the per-code default so a building can, say, run
    AUD with `en-NZ` grouping if that is what its owners expect.
    """
    settings = settings or {}
    code = normalise_currency_code(settings.get("currency_code"))
    locale = settings.get("currency_locale") or locale_for_currency(code)
    return {"currency_code": code, "currency_locale": str(locale)}
