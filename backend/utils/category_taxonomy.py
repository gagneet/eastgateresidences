# @featuretrace:levy — Canonical strata expense-category taxonomy (dedup + budget/actual reconciliation).
# Layer: service
"""Canonical strata expense-category taxonomy.

WHY THIS EXISTS
═══════════════
`levy_categories` uses the free-text `name` as its de-facto identity key (importers
upsert on ``(building_id, year, name, fund_type)``). But a building's financial
history is loaded from several sources — AGM minutes, audited statements, the
Civium portal scrape, and the canonical rebuild CSV — and each spells the *same
economic category* differently:

    "Caretaker"                      vs "Caretaker (Building Manager)"
    "Gardens" / "Garden & Grounds"   vs "Gardening"
    "Management Fees"                vs "Management fees - LJ Hooker" / "- CSMS" / ...
    "Utilities - Water & Sewerage"   vs "Water consumption" / "Water - Utility"
    "Insurance - Premium"            vs "Insurance premium (pre-Civium)"

Keyed on the raw name, every spelling becomes a *separate document*. Two failures
follow directly:

  1. **Duplicate rows** for one economic category on the Spending Categories page.
  2. **Empty "Budgeted" column** — an AGM proposed budget filed under one spelling
     ("Management Fees" $34,800) never lines up with the actuals filed under
     another ("Management fees - LJ Hooker" …), so `budgeted_amount` stays $0 and
     the UI renders "—".

This module maps any free-text category name to a **stable canonical key +
display name** so that:

  * importers can upsert on the canonical key instead of raw text (dedup at the
    source — the fix that *prevents* the problem recurring), and
  * a proposed budget and the itemised actuals for the same economic category
    land on **one** row (so "Budgeted" is populated).

DESIGN
══════
Building-agnostic, mirroring ``services/levy_fairness_service.py::_categorise_facility``:
an *ordered* list of keyword families (first match wins), applied to a
noise-stripped form of the name. It is deliberately **conservative** — only
families with clear real-world equivalence are rolled up; anything unmatched
falls back to a cleaned slug of its own name, so unknown categories still dedupe
on trivial variants (case / whitespace / punctuation / the "(pre-Civium)"
suffix) but are **never** force-merged into an unrelated bucket.

Ordering matters where one keyword is a substring of a distinct concept, e.g.
"waterproofing" must be classified *before* the "water" utility family, and
"electrical repairs" (R&M) must stay distinct from "electricity" (utility).

To extend: add an entry to ``_FAMILIES`` (or ``_ALIASES`` for an exact one-off).
Keep the most specific pattern earliest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = ["CanonicalCategory", "canonical_category", "canonical_key"]


@dataclass(frozen=True)
class CanonicalCategory:
    """Result of normalising a free-text category name.

    key          — stable identity for upsert/grouping (lowercase slug).
    display_name — human-facing name for the consolidated category.
    matched_rule — which family/alias matched, or "fallback" (for diagnostics).
    """

    key: str
    display_name: str
    matched_rule: str


# ── Noise stripping ──────────────────────────────────────────────────────────
# Trailing qualifiers that never change *which* category a line belongs to.
_NOISE_PARENS = re.compile(
    r"\s*\((?:"
    r"pre-civium"
    r"|as printed[^)]*"
    r"|expense line[^)]*"
    r"|combined[^)]*"
    r"|carry-over"
    r"|nearest balance date[^)]*"
    r")\)\s*",
    re.IGNORECASE,
)


def _strip_noise(name: str) -> str:
    """Lowercase + collapse whitespace + drop known non-identifying qualifiers.

    Normalises separator/punctuation noise so "BMC - Keys\\Swipe Cards\\Fobs" and
    "BMC - Keys/Swipe Cards/Fobs" compare equal, and a trailing "." or " (pre-Civium)"
    does not fork a category.
    """
    text = (name or "").strip().lower()
    text = _NOISE_PARENS.sub(" ", text)
    text = text.replace("\\", "/")
    text = re.sub(r"[.’']", "", text)          # drop periods and apostrophes
    text = re.sub(r"[^a-z0-9/&+\- ]", " ", text)     # keep meaningful separators
    # Normalise the "repairs & maintenance" abbreviations so "R & M - Lifts",
    # "Rep & Maint - Roof" etc. reach the same family as their qualifier.
    text = re.sub(r"\br ?& ?m\b|\brep ?& ?maint\b|\brep and maint\b|\br and m\b",
                  "repairs and maintenance", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


# ── Keyword families (ORDER MATTERS — first match wins) ──────────────────────
# (regex tested against the noise-stripped name, canonical_key, display_name)
_FAMILIES: list[tuple[re.Pattern, str, str]] = [
    # -- specific concepts that share a substring with a broader family, FIRST --
    (re.compile(r"waterproof|water ingress|planter box.*(waterproof|removal)"),
     "waterproofing", "Waterproofing"),
    (re.compile(r"\belectric(al)?\b.*(repair|replac|maint|upgrade)|electrical repairs"),
     "electrical_repairs", "Electrical Repairs & Maintenance"),
    (re.compile(r"garage.*door|door.*garage|\br ?& ?m garage"),
     "garage_doors", "Garage Doors"),
    (re.compile(r"roof"),
     "roof", "Roof Repairs & Maintenance"),

    # -- utilities --
    (re.compile(r"\bwater\b|sewer"),
     "water_sewerage", "Water & Sewerage"),
    (re.compile(r"electric|\bpower\b"),
     "electricity", "Electricity"),
    (re.compile(r"waste|rubbish|garbage|bin collection"),
     "waste", "Waste Management"),

    # -- services / contracts --
    (re.compile(r"caretak|building manager"),
     "caretaker", "Caretaker / Building Manager"),
    (re.compile(r"garden|ground|landscap|lawn|mowing"),
     "gardening", "Gardening & Grounds"),
    (re.compile(r"manage(ment)? fee|managing agent fee"),
     "management_fees", "Management Fees"),
    (re.compile(r"\blift|elevator"),
     "lift", "Lift Maintenance"),
    (re.compile(r"\bfire\b"),
     "fire_protection", "Fire Protection"),
    (re.compile(r"clean"),
     "cleaning", "Cleaning"),
    (re.compile(r"pest"),
     "pest_control", "Pest Control"),
    (re.compile(r"plumb|drainage"),
     "plumbing", "Plumbing & Drainage"),
    (re.compile(r"cctv|intercom|security|access control"),
     "security", "Security & Access"),

    # -- money / admin --
    (re.compile(r"insurance"),
     "insurance", "Insurance"),
    (re.compile(r"audit"),
     "audit_fees", "Audit Fees"),
    (re.compile(r"account(ant|ing)|bookkeep"),
     "accounting", "Accounting"),
    (re.compile(r"bank"),
     "bank_charges", "Bank Charges"),
    (re.compile(r"legal"),
     "legal", "Legal Expenses"),
    (re.compile(r"arrears|debt recover|late fee"),
     "arrears_recovery", "Arrears & Late Fees"),
    (re.compile(r"\bgst\b|income tax|\btax\b|\bbas\b"),
     "taxation", "Taxation"),
    (re.compile(r"consult|engineer"),
     "consultant_fees", "Consultant Fees"),
    (re.compile(r"postage|stationery|printing"),
     "postage", "Postage & Stationery"),
    (re.compile(r"key|fob|swipe|\block\b|locks"),
     "keys_locks", "Keys, Fobs & Locks"),

    # -- generic R&M catch-all, LAST: only unqualified repairs/maintenance lines
    #    reach here (roof/lift/garage/plumbing/electrical/fire ran earlier) --
    (re.compile(r"general repair|building repair|repairs? (and|&) maintenance"),
     "general_repairs", "General Repairs & Maintenance"),
]

# Exact-name aliases for one-offs a family can't safely capture. Applied to the
# noise-stripped name AFTER families miss but BEFORE the slug fallback.
_ALIASES: dict[str, tuple[str, str]] = {
    "sundry expenses": ("sundry", "Sundry Expenses"),
    "essential services": ("essential_services", "Essential Services"),
}


def canonical_category(name: str, fund_type: Optional[str] = None) -> CanonicalCategory:
    """Map a free-text category name to its canonical key + display name.

    ``fund_type`` is accepted for future fund-aware disambiguation but is not
    required today (no current family depends on it). The key is fund-agnostic;
    callers that need per-fund identity should compose ``f"{fund_type}:{key}"``.
    """
    stripped = _strip_noise(name)
    if not stripped:
        return CanonicalCategory(key="uncategorised", display_name="Uncategorised", matched_rule="empty")

    for pattern, key, display in _FAMILIES:
        if pattern.search(stripped):
            return CanonicalCategory(key=key, display_name=display, matched_rule=key)

    if stripped in _ALIASES:
        key, display = _ALIASES[stripped]
        return CanonicalCategory(key=key, display_name=display, matched_rule=f"alias:{key}")

    # Fallback: dedupe on the cleaned name only (case/whitespace/punct/pre-Civium),
    # never force-merged into an unrelated family. Display preserves the original.
    return CanonicalCategory(
        key=_slug(stripped) or "uncategorised",
        display_name=(name or "").strip() or "Uncategorised",
        matched_rule="fallback",
    )


def canonical_key(name: str, fund_type: Optional[str] = None) -> str:
    """Convenience: just the canonical key (fund-agnostic)."""
    return canonical_category(name, fund_type).key
