# @featuretrace:levy — tests for the canonical expense-category taxonomy.
# Layer: test
"""Unit tests for backend/utils/category_taxonomy.py.

These lock in the specific real-world equivalences that caused East Gate's
duplicate spending-category rows and empty "Budgeted" column, plus the
conservative-fallback contract (unrelated names must NOT collapse together).
"""
import pytest

from utils.category_taxonomy import canonical_category, canonical_key


# The concrete duplicate pairs the user reported — each pair MUST share a key.
@pytest.mark.parametrize(
    "a,b",
    [
        ("Caretaker", "Caretaker (Building Manager)"),
        ("Garden & Grounds", "Gardening"),
        ("Gardens", "Gardening"),
        ("Management Fees", "Management fees - LJ Hooker"),
        ("Management Fees", "Management fees - CSMS"),
        ("Utilities - Water & Sewerage", "Water consumption"),
        ("Water & Sewerage", "Water - Utility"),
        ("Insurance - Premium", "Insurance premium (pre-Civium)"),
        ("Lift Maintenance", "Lift Registration Fee"),
        ("Fire Brigade Monitoring", "Fire Prtcn-Repairs & Servicing"),
        # R&M abbreviations must route to their qualifier's family
        ("R & M - Lifts", "Lift Maintenance"),
        ("Rep & Maint - Roof", "Roof Maintenance"),
        ("R & M Garage Doors", "Garage Door"),
        ("Building Repairs & Maintenance", "General Repairs & Maintenance"),
    ],
)
def test_reported_duplicates_share_a_key(a, b):
    assert canonical_key(a) == canonical_key(b), (
        f"{a!r} and {b!r} should map to the same canonical category"
    )


# Names that are genuinely different concepts MUST stay distinct.
@pytest.mark.parametrize(
    "a,b",
    [
        ("Water consumption", "Waterproofing works"),          # utility vs capital works
        ("Electricity consumption", "Electrical Repairs"),      # utility vs R&M
        ("Cleaning", "Caretaker"),                              # distinct services
        ("Audit Fees", "Management Fees"),                      # distinct fees
        ("Roof Maintenance", "Garage Door"),                   # distinct capital items
    ],
)
def test_distinct_concepts_stay_distinct(a, b):
    assert canonical_key(a) != canonical_key(b), (
        f"{a!r} and {b!r} must NOT be merged"
    )


def test_noise_variants_collapse():
    # case / whitespace / punctuation / pre-Civium / slash-vs-backslash
    assert canonical_key("Consultant Fees") == canonical_key("Consultant fees")
    assert canonical_key("Consultant Fees") == canonical_key("Consultant Fees.")
    assert canonical_key("Cleaning - basement") == canonical_key("Cleaning - basement (pre-Civium)")
    assert canonical_key(r"BMC - Keys\Swipe Cards\Fobs") == canonical_key("BMC - Keys/Swipe Cards/Fobs")


def test_idempotent():
    # Feeding a canonical display name back through must be stable.
    for name in ["Caretaker (Building Manager)", "Management fees - LJ Hooker", "Water consumption"]:
        first = canonical_category(name)
        second = canonical_category(first.display_name)
        assert first.key == second.key


def test_empty_and_none_safe():
    assert canonical_category("").key == "uncategorised"
    assert canonical_category(None).key == "uncategorised"


def test_unknown_name_falls_back_to_own_slug():
    r = canonical_category("Totally Novel Line Item 2099")
    assert r.matched_rule == "fallback"
    assert r.key == "totally_novel_line_item_2099"
    assert r.display_name == "Totally Novel Line Item 2099"
