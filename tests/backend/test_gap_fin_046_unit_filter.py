"""GAP-FIN-046 repair-script unit filter — the restrictive _select_units helper.

The filter can only SHRINK the working set (never add a unit). Three exclusion
categories, checked in order:
  1. PORTAL_ANCHORED_EXCLUDED_UNITS — unconditional, no override flag (10 real
     unit numbers, GAP-FIN-057/046 collision, 2026-08-09).
  2. only_units allowlist, if given.
  3. FLAGGED_UNRESOLVED_UNITS (TH074, TH077, UA050) — held back by default,
     opt-in via include_flagged.
  4. exclude_units, an explicit additional skip-list.

Generic-logic tests below use synthetic unit numbers (UB1xx) that don't
collide with the real PORTAL_ANCHORED_EXCLUDED_UNITS constant, so a future
change to that list can't silently break unrelated assertions the way the
original UA001/UA009/UA013/UA070 collision did. Pure logic — no DB.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "backend"))

# Load the repair script as a module (it lives under scripts/, not a package).
_spec = importlib.util.spec_from_file_location(
    "gap_fin_046_repair",
    _REPO / "backend" / "scripts" / "data_repair" / "gap_fin_046_allocation_integrity_repair_20260805.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

ALL = {"UB101", "UB102", "UB103", "TH074", "TH077", "UA050", "UB104"}


def test_flagged_units_excluded_by_default():
    kept, excluded = mod._select_units(ALL)
    assert "TH074" in excluded and "TH077" in excluded and "UA050" in excluded
    assert kept == {"UB101", "UB102", "UB103", "UB104"}
    assert all("flagged unresolved" in r for u, r in excluded.items())


def test_include_flagged_opt_in_keeps_them():
    kept, excluded = mod._select_units(ALL, include_flagged=True)
    assert kept == ALL
    assert excluded == {}


def test_only_units_allowlist_narrows_to_exactly_those():
    kept, excluded = mod._select_units(ALL, only_units={"UB101", "UB102"})
    assert kept == {"UB101", "UB102"}
    # everything else excluded, including the flagged ones
    assert set(excluded) == ALL - {"UB101", "UB102"}


def test_only_units_still_excludes_a_flagged_unit_not_opted_in():
    # asking for a flagged unit via --only-units without --include-flagged still holds it back
    kept, excluded = mod._select_units(ALL, only_units={"TH074", "UB101"})
    assert kept == {"UB101"}
    assert "TH074" in excluded and "flagged unresolved" in excluded["TH074"]


def test_exclude_units_adds_to_the_default_flagged_set():
    kept, excluded = mod._select_units(ALL, exclude_units={"UB102"})
    assert "UB102" in excluded and "in --exclude-units" in excluded["UB102"]
    assert kept == {"UB101", "UB103", "UB104"}


def test_filter_is_purely_restrictive_never_adds():
    kept, _ = mod._select_units(ALL, only_units={"UB101", "NOT_IN_SCOPE"})
    assert kept <= ALL          # never invents a unit outside the raw scope
    assert "NOT_IN_SCOPE" not in kept


# ─────────────────────────────────────────────────────────────────────────────
# Portal-anchored exclusion (GAP-FIN-057/046 collision, 2026-08-09) — a real,
# separate, unconditional exclusion category with no override flag at all,
# checked BEFORE only_units/flagged/exclude_units in the filter chain.
# ─────────────────────────────────────────────────────────────────────────────

def test_portal_anchored_units_excluded_unconditionally():
    """PORTAL_ANCHORED_EXCLUDED_UNITS units are held back even with
    include_flagged=True and even when explicitly named in --only-units --
    there is no override flag for this category (see the module's own
    docstring: "no future evidence that would make backfilling them
    correct")."""
    portal_anchored_sample = {"UA001", "UA009"}
    assert portal_anchored_sample <= mod.PORTAL_ANCHORED_EXCLUDED_UNITS

    kept, excluded = mod._select_units(
        portal_anchored_sample, only_units=portal_anchored_sample, include_flagged=True,
    )
    assert kept == set()
    assert set(excluded) == portal_anchored_sample
    assert all("portal-anchored" in r for r in excluded.values())


def test_portal_anchored_units_never_appear_in_kept_alongside_normal_units():
    kept, excluded = mod._select_units(mod.PORTAL_ANCHORED_EXCLUDED_UNITS | {"UB101"})
    assert kept == {"UB101"}
    assert set(excluded) == mod.PORTAL_ANCHORED_EXCLUDED_UNITS
