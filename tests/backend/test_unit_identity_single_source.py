# @featuretrace:multi-unit-ownership — one source of truth for lot -> unit resolution.
# Layer: test
# Data flow: static scan of backend/**.py -> fails if a module re-implements the mapping.
# Scope: repo-wide (building-agnostic)
# Related: backend/utils/unit_number.py       (THE canonical resolver)
#          backend/services/settings_service.py::get_unit_display_rules
"""Lot -> unit resolution lives in ONE module. This test keeps it that way.

The rule
--------
A plan LOT number ("71") and an addressable UNIT number ("TH071") are different
identifiers. The mapping between them is per-building CONFIGURATION, not code —
``db.settings`` type ``unit_display``::

    [{"prefix": "UA", "min": 1, "max": 70, "pad": 3},
     {"prefix": "TH", "min": 71, "max": 87, "pad": 3}]

``backend/utils/unit_number.py`` owns that mapping:

  * ``format_unit_display(lot, rules)``        — lot int -> display unit
  * ``resolve_canonical_unit_number(db, ...)`` — any reference -> canonical units row
  * ``unit_number_candidates(value, rules)``   — variant expansion
  * ``normalise_unit_token(value)``            — strip "UNIT ", case, spacing

Nothing else may derive a unit number from a lot number.

Why this test exists
--------------------
This has now been re-introduced more than once, each time by someone (including an
assistant) building a local lot->unit map instead of calling the helper that already
existed. The most recent instance, 2026-08-28 in
``strata_web_balance_inference_service``, built its map from ``units.lot_number`` —
which stores "LOT71", not "71" — so every lookup missed, the balance-delta inference
emitted bare lot numbers as unit references, and the matching engine scored 32 of 39
real payments 0.0 because it was comparing "3" against "UA003".

A local map is wrong even when its lookups succeed, because it cannot know the
building's rules and will silently mis-resolve any building whose prefixes differ.

If this test fails
------------------
Do not add your module to the allowlist. Call ``utils.unit_number`` instead.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"

# The one module allowed to know how a prefix attaches to a lot number, plus the places
# that legitimately DECLARE the rule shape (a Pydantic model, the settings accessor and
# its docstrings) rather than apply it.
_ALLOWED = {
    "utils/unit_number.py",
    "models/settings.py",
    "services/settings_service.py",
}

# Building a unit number by gluing a prefix to a lot: f"TH{n:03d}", "UA" + str(lot),
# "TH%03d" % lot. Deliberately narrow — it targets CONSTRUCTION, not the many
# legitimate mentions of a prefix as a literal value in config, seeds or comparisons.
_CONSTRUCTION = re.compile(
    r"""(
        f["'][^"']*\b(?:TH|UA)\{          # f"TH{lot...}"
      | ["'](?:TH|UA)["']\s*\+            # "TH" + ...
      | ["'](?:TH|UA)%0?\d*d["']          # "TH%03d"
      | ["'](?:TH|UA)["']\s*,\s*\w*lot    # ("TH", lot...)
    )""",
    re.VERBOSE,
)


def _python_files() -> list[Path]:
    skip = ("venv", "__pycache__", "/alembic/versions/", "/scripts/migrations/")
    return [
        p for p in _BACKEND.rglob("*.py")
        if not any(s in str(p) for s in skip)
    ]


def _rel(path: Path) -> str:
    return str(path.relative_to(_BACKEND))


class TestUnitIdentityHasOneOwner:
    def test_no_module_constructs_a_unit_number_from_a_prefix(self):
        offenders = []
        for path in _python_files():
            rel = _rel(path)
            if rel in _ALLOWED:
                continue
            # Scan CODE only. A comment that quotes the wrong pattern in order to warn
            # against it is not an instance of it — and this test's own fix comments
            # would otherwise trip it, which is a fine way to teach people to delete
            # the warning rather than the code.
            for lineno, raw in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                if raw.lstrip().startswith("#"):
                    continue
                for match in _CONSTRUCTION.finditer(raw):
                    offenders.append(f"{rel}:{lineno}  {match.group(0)!r}")
        assert not offenders, (
            "These build a unit number from a prefix instead of using "
            "utils.unit_number.format_unit_display(lot, rules), which reads the "
            "building's own unit_display rules:\n  " + "\n  ".join(offenders)
        )

    def test_the_canonical_helper_exposes_what_callers_need(self):
        """If any of these disappear or are renamed, callers will start rolling their
        own again — which is precisely how this keeps coming back."""
        from utils import unit_number

        for name in (
            "normalise_unit_token",
            "extract_lot_int",
            "format_unit_display",
            "unit_number_candidates",
            "resolve_canonical_unit_number",
        ):
            assert hasattr(unit_number, name), f"utils.unit_number.{name} is missing"

    def test_format_unit_display_uses_the_building_rules(self):
        """The mapping is configuration. Same lot, different rules, different answer —
        which is exactly why a hardcoded local map is wrong even when it 'works'."""
        from utils.unit_number import format_unit_display

        east_gate = [
            {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
            {"prefix": "TH", "min": 71, "max": 87, "pad": 3},
        ]
        assert format_unit_display(3, east_gate) == "UA003"
        assert format_unit_display(71, east_gate) == "TH071"
        assert format_unit_display(87, east_gate) == "TH087"

        other = [{"prefix": "APT", "min": 1, "max": 999, "pad": 2}]
        assert format_unit_display(3, other) == "APT03"

        # No rule covering the lot means "no prefix configured", not an error.
        assert format_unit_display(500, east_gate) == "500"
        assert format_unit_display(3, None) == "3"

    @pytest.mark.parametrize("given,expected", [
        ("UNIT 3", "3"), ("unit3", "3"), (" TH071 ", "TH071"), ("th071", "TH071"),
    ])
    def test_token_normalisation_is_shared(self, given, expected):
        from utils.unit_number import normalise_unit_token
        assert normalise_unit_token(given) == expected


class TestInferenceServiceUsesTheHelper:
    """The 2026-08-28 regression, pinned so it cannot recur in this specific module."""

    def test_it_imports_the_canonical_resolver(self):
        src = (_BACKEND / "services" / "strata_web_balance_inference_service.py").read_text()
        assert "resolve_canonical_unit_number" in src, (
            "the balance-delta inference must resolve lot -> unit through "
            "utils.unit_number, not a local map"
        )

    def test_it_does_not_build_its_own_map(self):
        src = (_BACKEND / "services" / "strata_web_balance_inference_service.py").read_text()
        for banned in ("_resolve_unit_numbers", "_bare_digits"):
            assert banned not in src, (
                f"{banned} is a re-implementation of lot -> unit resolution; "
                "call utils.unit_number instead"
            )
