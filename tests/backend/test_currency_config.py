"""Currency configuration — the building-scoped display currency.

Amounts are stored in minor units and are currency-agnostic. The CURRENCY is a
display concern resolved per building, and it must be resolved in exactly one place
or two pages will disagree about what "$" means.

A code is stored rather than a symbol because "$" is ambiguous across AUD, NZD, USD,
SGD and HKD; the ISO-4217 code plus a BCP-47 locale lets the client derive the
symbol AND its placement, grouping and decimal separators.
"""
import pytest

from utils.currency import (
    DEFAULT_CURRENCY_CODE,
    DEFAULT_CURRENCY_LOCALE,
    SUPPORTED_CURRENCY_CODES,
    currency_config,
    locale_for_currency,
    normalise_currency_code,
)


class TestNormaliseCurrencyCode:
    def test_defaults_to_aud_when_unset(self):
        """The platform is Australian strata: a building with no setting is
        Australian, not currency-less."""
        for empty in (None, "", "   "):
            assert normalise_currency_code(empty) == DEFAULT_CURRENCY_CODE

    def test_uppercases_and_trims(self):
        assert normalise_currency_code(" nzd ") == "NZD"

    def test_unsupported_code_degrades_to_default_rather_than_raising(self):
        """An unknown code must never reach Intl.NumberFormat, which throws a
        RangeError on an invalid currency — that would blank every money figure on
        the page rather than showing the wrong symbol."""
        for bad in ("XYZ", "DOLLARS", "$", "123"):
            assert normalise_currency_code(bad) == DEFAULT_CURRENCY_CODE

    def test_every_supported_code_has_a_locale(self):
        for code in SUPPORTED_CURRENCY_CODES:
            assert locale_for_currency(code), code


class TestCurrencyConfig:
    def test_missing_settings_yield_the_documented_default(self):
        for settings in (None, {}):
            assert currency_config(settings) == {
                "currency_code": DEFAULT_CURRENCY_CODE,
                "currency_locale": DEFAULT_CURRENCY_LOCALE,
            }

    def test_code_selects_its_paired_locale(self):
        assert currency_config({"currency_code": "NZD"}) == {
            "currency_code": "NZD",
            "currency_locale": "en-NZ",
        }
        assert currency_config({"currency_code": "GBP"})["currency_locale"] == "en-GB"

    def test_explicit_locale_overrides_the_per_code_default(self):
        """A building may legitimately run AUD with another region's number
        formatting; the pair is configurable, not derived-only."""
        assert currency_config({"currency_code": "AUD", "currency_locale": "en-NZ"}) == {
            "currency_code": "AUD",
            "currency_locale": "en-NZ",
        }

    def test_bad_code_with_good_locale_still_falls_back_on_the_code_only(self):
        cfg = currency_config({"currency_code": "XYZ", "currency_locale": "en-GB"})
        assert cfg["currency_code"] == DEFAULT_CURRENCY_CODE
        assert cfg["currency_locale"] == "en-GB"

    @pytest.mark.parametrize("code", SUPPORTED_CURRENCY_CODES)
    def test_every_supported_code_round_trips(self, code):
        cfg = currency_config({"currency_code": code})
        assert cfg["currency_code"] == code
        assert cfg["currency_locale"]
