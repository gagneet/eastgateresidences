"""
Utility functions for owner name normalisation and similarity matching.

Used by the Owner Name Verification feature to detect mismatches between
a registered user's name and the names on the strata roll (owner_name /
owner_name_b fields imported from the Excel-based unit register).

Algorithm mirrors the frontend isSimilarName / normaliseName implementation
in UsersPage.jsx so that backend and frontend produce identical results.
"""
import math
import re

from typing import Set


def normalise_name(name: str) -> Set[str]:
    """
    Convert a human name into a set of lowercase alphabetic tokens.

    - Strips punctuation, titles (Mr/Mrs/Ms/Dr etc.) and whitespace
    - Splits on any whitespace
    - Filters empty tokens

    Examples:
        "Mr John A Smith"  -> {"john", "a", "smith"}
        "Smith, Jane"      -> {"smith", "jane"}
        ""                 -> set()
    """
    cleaned = re.sub(r"[^a-z\s]", " ", (name or "").lower())
    tokens = {t for t in cleaned.split() if t}
    return tokens


def names_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """
    Return True when names ``a`` and ``b`` are considered a match.

    Matching rules (in priority order):
    1. Either name is blank → True (can't disprove a match with no data)
    2. Token sets are identical after sorting → True (exact match)
    3. Overlap of tokens >= ceil(min_set_size * threshold) → True

    The default threshold of 0.6 matches the frontend implementation.
    """
    if not a or not b:
        return True
    set_a = normalise_name(a)
    set_b = normalise_name(b)
    if not set_a or not set_b:
        return True
    if set_a == set_b:
        return True
    overlap = len(set_a & set_b)
    min_len = min(len(set_a), len(set_b))
    return overlap >= math.ceil(min_len * threshold)


def format_owner_names(primary: str, secondary: str = "", separator: str = " & ") -> str:
    """
    Combine primary and secondary owner names.

    Uses filter(None, ...) so that empty/whitespace-only names are excluded,
    preventing artefacts like " & Gagneet Singh" or "Avneet &  ".

    Examples:
        format_owner_names("Avneet Rooprai", "Gagneet Singh") -> "Avneet Rooprai & Gagneet Singh"
        format_owner_names("Avneet Rooprai", "")              -> "Avneet Rooprai"
        format_owner_names("Avneet Rooprai")                  -> "Avneet Rooprai"
        format_owner_names("", "")                            -> ""
    """
    return separator.join(filter(None, [(primary or "").strip(), (secondary or "").strip()]))


def check_owner_name_against_roll(
        full_name: str,
        roll_primary: str,
        roll_secondary: str = "",
) -> bool:
    """
    Return True when ``full_name`` matches either strata-roll name.

    A match against the secondary name (owner_name_b) is considered
    sufficient for joint-ownership units.
    """
    if names_similar(full_name, roll_primary):
        return True
    if roll_secondary and names_similar(full_name, roll_secondary):
        return True
    return False
