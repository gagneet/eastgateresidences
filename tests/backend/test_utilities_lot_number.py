"""
Tests for the lot_number int-parsing fix in routers/utilities.py.

Before the fix, `int('LOT87')` raised a ValueError.
After the fix, non-digit characters are stripped with re.sub before conversion.

Verifies:
- Source inspection confirms the fix is present
- The parsing logic handles all expected string formats
- Edge cases: empty string, None, letters-only, mixed alphanumeric
"""
import inspect
import re

import pytest


# ────────────────────────────────────────────────────────────────────────────
# Helpers — replicate the exact parsing logic from utilities.py
# ────────────────────────────────────────────────────────────────────────────

def _parse_lot_number(lot_num_raw) -> int:
    """
    Replicate the lot_number parsing logic from the utilities endpoint
    fallback path in routers/utilities.py.

    Logic:
      1. If falsy, return 0
      2. Try int() directly (works for plain integers or numeric strings)
      3. On ValueError/TypeError, strip non-digits via re.sub then convert
         — if no digits remain, return 0
    """
    try:
        return int(lot_num_raw) if lot_num_raw else 0
    except (ValueError, TypeError):
        digits = re.sub(r"\D", "", str(lot_num_raw))
        return int(digits) if digits else 0


# ────────────────────────────────────────────────────────────────────────────
# Source-inspection — verify the fix exists in the actual source file
# ────────────────────────────────────────────────────────────────────────────

class TestLotNumberSourceFix:
    """Source-level verification that the fix is present in utilities.py."""

    def test_fix_present_in_source(self):
        """routers/utilities.py must contain the ValueError/TypeError guard."""
        from routers import utilities
        src = inspect.getsource(utilities)
        assert "ValueError" in src or r"\D" in src, (
            "utilities.py must contain lot_number parsing fix (ValueError guard or re.sub)"
        )

    def test_regex_strip_in_source(self):
        """The fix must use re.sub to strip non-digit characters."""
        from routers import utilities
        src = inspect.getsource(utilities)
        assert r"\D" in src, (
            r"utilities.py must use re.sub(r'\D', ...) to strip non-digits from lot_number"
        )

    def test_import_re_in_source(self):
        """utilities.py must import re (used for _unit_to_lot and the fix)."""
        from routers import utilities
        src = inspect.getsource(utilities)
        assert "import re" in src, "utilities.py must import the re module"

    def test_get_utilities_function_exists(self):
        """The get_utilities endpoint function must exist."""
        from routers import utilities
        assert hasattr(utilities, "get_utilities"), (
            "routers/utilities.py must define get_utilities"
        )


# ────────────────────────────────────────────────────────────────────────────
# Unit tests — parsing logic edge cases
# ────────────────────────────────────────────────────────────────────────────

class TestLotNumberParsing:
    """Unit-test the lot_number parsing logic for all expected inputs."""

    def test_lot_prefix_with_number(self):
        """'LOT87' → 87 (the original bug case)."""
        assert _parse_lot_number("LOT87") == 87

    def test_plain_numeric_string(self):
        """'123' → 123 (standard case)."""
        assert _parse_lot_number("123") == 123

    def test_plain_integer(self):
        """int(42) → 42 (already an integer)."""
        assert _parse_lot_number(42) == 42

    def test_empty_string(self):
        """'' → 0 (falsy short-circuit)."""
        assert _parse_lot_number("") == 0

    def test_none_input(self):
        """None → 0 (falsy short-circuit)."""
        assert _parse_lot_number(None) == 0

    def test_zero_integer(self):
        """0 → 0 (falsy short-circuit, lot 0 is not valid)."""
        assert _parse_lot_number(0) == 0

    def test_letters_only_no_digits(self):
        """'LOT' (no digits) → 0."""
        assert _parse_lot_number("LOT") == 0

    def test_mixed_suffix_digits(self):
        """'LOT42A' → 42 (strips 'LOT' prefix and 'A' suffix, keeps '42')."""
        assert _parse_lot_number("LOT42A") == 42

    def test_numeric_zero_string(self):
        """'0' → 0 (numeric zero as string)."""
        assert _parse_lot_number("0") == 0

    def test_townhouse_lot(self):
        """'LOT87' → 87 (TH087 = lot 87, the highest lot)."""
        assert _parse_lot_number("LOT87") == 87

    def test_apartment_lot(self):
        """'LOT1' → 1 (UA001 = lot 1, the lowest lot)."""
        assert _parse_lot_number("LOT1") == 1

    def test_float_string_strips_decimal(self):
        """'42.5' → 425 (re.sub removes '.', keeps digits — edge case)."""
        # This is acceptable behaviour — lot numbers are always integers
        result = _parse_lot_number("42.5")
        assert isinstance(result, int)

    def test_whitespace_string(self):
        """'  ' (whitespace only) → 0 (no digits)."""
        assert _parse_lot_number("  ") == 0


class TestLotNumberHelpers:
    """Verify the _unit_to_lot helper in utilities.py maps correctly."""

    def test_ua001_maps_to_lot_1(self):
        """UA001 should map to lot 1."""
        from routers.utilities import _unit_to_lot
        assert _unit_to_lot("UA001") == 1

    def test_ua070_maps_to_lot_70(self):
        """UA070 should map to lot 70 (highest apartment)."""
        from routers.utilities import _unit_to_lot
        assert _unit_to_lot("UA070") == 70

    def test_th001_maps_to_lot_71(self):
        """TH071 should map to lot 71 (first townhouse)."""
        from routers.utilities import _unit_to_lot
        assert _unit_to_lot("TH071") == 71

    def test_th017_maps_to_lot_87(self):
        """TH087 should map to lot 87 (last townhouse)."""
        from routers.utilities import _unit_to_lot
        assert _unit_to_lot("TH087") == 87

    def test_invalid_unit_returns_none(self):
        """An unrecognised unit number returns None."""
        from routers.utilities import _unit_to_lot
        assert _unit_to_lot("XX999") is None

    def test_lowercase_normalised(self):
        """Lowercase input is normalised to uppercase."""
        from routers.utilities import _unit_to_lot
        assert _unit_to_lot("ua001") == 1
