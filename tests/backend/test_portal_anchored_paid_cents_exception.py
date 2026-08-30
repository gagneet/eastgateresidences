# @featuretrace:portal-anchored-paid-cents — Guards the signed-off allocation exception.
# Layer: test
# Data flow: static scan of backend/scripts/data_repair/* -> fails any blanket
#            paid_cents := SUM(allocations) repair (building-scoped).
# Related: backend/scripts/data_repair/gap_fin_046_phantom_removal_portal_anchored_20260809.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
"""
Guards the portal-anchored `paid_cents` reconciliation exception.

THE TRAP THIS EXISTS TO STOP
-----------------------------
An integrity check over East Gate reports 521 of 3,480 `finance.levy_items` where
`paid_cents != SUM(receipt_allocations.allocated_cents)` — 505 claiming $228,873.24 paid
with nothing allocated behind it. It looks exactly like corruption, and the obvious repair
is already built and sitting there: `recompute_paid_cents()` sets
`paid_cents := Σ surviving allocations`.

Running it over those items would be a financial disaster.

The gap is DELIBERATE and was signed off on 2026-08-09. For thirteen units the portal
balance is ground truth and the reconstruction-generated receipts that would otherwise
back `paid_cents` were proven fabricated, so they were excluded from allocation on
purpose. `gap_fin_046_phantom_removal_portal_anchored_20260809.py` says so in terms:

    "2. Does NOT decrement finance.levy_items.paid_cents."
    "4. ... NOT SUM(receipt_allocations) (short by design for these units -- that gap is
        the documented, permanent reconciliation exception, never force-matched)."

Verified live 2026-08-27: the thirteen units carrying the drift are EXACTLY the thirteen
in that script's `UNITS` list, and they are EXACTLY the thirteen units `/arrears/detail`
reports as in arrears ($7,851.30 total). Arrears is computed from `paid_cents`, so
force-matching it to allocations would not "correct" anything — it would collapse
`paid_cents` for these units and inflate their arrears by roughly $224,733, billing
thirteen real owners for debt they do not owe.

The fourteenth drifted unit, TH075, is a single item at -$190.00 — the separately
documented ~$190 portal reconciliation drift, also an exception, also not to be forced.

WHAT THESE TESTS CHECK
----------------------
Not the live data (that moves). They check that the DECISION stays discoverable in the
code that encodes it, and that no new repair script force-matches `paid_cents` to
allocations without acknowledging the exception.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "backend/scripts/data_repair"
          / "gap_fin_046_phantom_removal_portal_anchored_20260809.py")

# The thirteen units the 2026-08-09 sign-off covers. Kept here as the assertion's expected
# value only — the script remains the source of truth and is parsed, never bypassed.
EXPECTED_UNITS = {
    "UA001", "UA009", "UA013", "UA016", "UA028", "UA040", "UA042",
    "UA058", "UA067", "UA070", "TH074", "TH077", "UA050",
}


def _script_text() -> str:
    assert SCRIPT.exists(), (
        f"{SCRIPT.relative_to(REPO)} is missing. It is the only record of why 521 levy "
        "items legitimately disagree with their allocations. Do not delete it without "
        "moving the rationale somewhere a future integrity audit will find it."
    )
    return SCRIPT.read_text()


def test_portal_anchored_unit_list_is_unchanged():
    """The exception covers a closed, hand-verified set — it must not silently grow."""
    text = _script_text()
    block = re.search(r"UNITS\s*=\s*\[(.*?)\]", text, re.S)
    assert block, "UNITS list not found — the exception's scope is no longer declarable."
    found = set(re.findall(r'"([A-Z]{2}\d{3})"', block.group(1)))
    assert found == EXPECTED_UNITS, (
        "The portal-anchored unit list changed. Each unit in it was individually verified "
        "against a portal balance before being added; adding one without that evidence "
        "exempts it from an integrity check it may genuinely be failing.\n"
        f"  added:   {sorted(found - EXPECTED_UNITS)}\n"
        f"  removed: {sorted(EXPECTED_UNITS - found)}"
    )


def test_script_still_declares_it_does_not_touch_paid_cents():
    """The 'why' must survive edits to the script, not just the 'what'."""
    text = _script_text().lower()
    assert "does not decrement" in text and "paid_cents" in text, (
        "The script no longer states that it leaves paid_cents alone. That sentence is "
        "what tells a future reader the allocation gap is intentional."
    )
    assert "never force-matched" in text, (
        "The 'never force-matched' declaration is gone. It is the explicit instruction "
        "not to run recompute_paid_cents over these items."
    )


def _repair_scripts() -> list[pathlib.Path]:
    d = REPO / "backend/scripts/data_repair"
    return sorted(p for p in d.glob("*.py") if p.name != "__init__.py")


# A blanket force-match: assigning paid_cents from a SUM over receipt_allocations.
_FORCE_MATCH = re.compile(
    r"paid_cents\s*=\s*[^;\n]*(SUM|sum)\s*\(\s*[^)]*alloc", re.I)


@pytest.mark.parametrize("script", _repair_scripts(), ids=lambda p: p.name)
def test_no_repair_script_blanket_force_matches_paid_cents(script):
    """
    A new script may legitimately recompute paid_cents from allocations — but only if it
    knows about the thirteen units for which that is wrong. Requiring the acknowledgement
    is what turns a silent, plausible-looking repair into a deliberate decision.
    """
    text = script.read_text()
    if not _FORCE_MATCH.search(text):
        return
    acknowledged = (
        "portal" in text.lower()
        or "gap_fin_046" in text.lower()
        or any(u in text for u in EXPECTED_UNITS)
    )
    assert acknowledged, (
        f"{script.name} sets levy_items.paid_cents from SUM(receipt_allocations) without "
        "mentioning the portal-anchored exception. Thirteen East Gate units hold a "
        "deliberate, signed-off gap between the two (2026-08-09); force-matching them "
        "inflates their arrears by ~$224,733 and bills real owners for it. Either exclude "
        "those units or state why they are safe to include."
    )
