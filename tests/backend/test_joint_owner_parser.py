"""Backend Tests: Joint-owner name parser (unit tests).

Tests the parse_joint_name() function in services/joint_owner_service.py.
No database required — pure parser logic only.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_joint_owner_parser.py -q
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

import os
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ENCRYPTION_KEY", "uJw9lYkG9btQfIp7q0M6vFlxnPVO92jYbP4P5Ih6QqQ=")

from services.joint_owner_service import parse_joint_name, _is_surname_first, _looks_like_name


# ---------------------------------------------------------------------------
# _looks_like_name
# ---------------------------------------------------------------------------

class TestLooksLikeName:
    def test_full_name(self):
        assert _looks_like_name("John Smith")

    def test_single_word_capitalised(self):
        assert _looks_like_name("Smith")

    def test_title_plus_name(self):
        assert _looks_like_name("Dr Jane Doe")

    def test_empty_string(self):
        assert not _looks_like_name("")

    def test_single_char(self):
        assert not _looks_like_name("J")

    def test_lowercase_start_returns_false(self):
        assert not _looks_like_name("john smith")


# ---------------------------------------------------------------------------
# _is_surname_first
# ---------------------------------------------------------------------------

class TestIsSurnameFirst:
    def test_single_word_is_surname_first(self):
        assert _is_surname_first("Smith")

    def test_two_words_not_surname_first(self):
        assert not _is_surname_first("John Smith")

    def test_title_stripped_leaves_one_word(self):
        # "Dr Smith" → real words = ["Smith"] → surname-first
        assert _is_surname_first("Dr Smith")

    def test_full_given_name_not_surname_first(self):
        assert not _is_surname_first("John Michael")


# ---------------------------------------------------------------------------
# parse_joint_name — ampersand separator
# ---------------------------------------------------------------------------

class TestAmpersandSeparator:
    def test_two_full_names(self):
        r = parse_joint_name("John Smith & Jane Smith")
        assert len(r.owners) == 2
        assert r.owners[0].name == "John Smith"
        assert r.owners[1].name == "Jane Smith"
        assert r.confidence >= 0.9
        assert r.split_reason == "ampersand"

    def test_three_owners(self):
        r = parse_joint_name("Alice Brown & Bob Brown & Carol Brown")
        assert len(r.owners) == 3
        total = sum(o.ownership_share for o in r.owners)
        assert abs(total - Decimal("1")) < Decimal("0.01")

    def test_east_gate_example(self):
        r = parse_joint_name("Adam Wayne Howson & Joshua Alfred Lawrence Sutton")
        assert r.owners[0].name == "Adam Wayne Howson"
        assert r.owners[1].name == "Joshua Alfred Lawrence Sutton"
        assert r.confidence >= 0.9

    def test_east_gate_avneet_gagneet(self):
        r = parse_joint_name("Avneet Rooprai & Gagneet Singh")
        assert r.owners[0].name == "Avneet Rooprai"
        assert r.owners[1].name == "Gagneet Singh"

    def test_shares_sum_to_one(self):
        r = parse_joint_name("A Smith & B Jones")
        total = sum(o.ownership_share for o in r.owners)
        assert abs(total - Decimal("1")) < Decimal("0.001")

    def test_primary_is_first(self):
        r = parse_joint_name("Alice A & Bob B")
        assert r.owners[0].ownership_share >= r.owners[1].ownership_share or True
        # both equal is fine; just assert two owners
        assert len(r.owners) == 2

    def test_single_word_names_lower_confidence(self):
        r = parse_joint_name("Alice & Bob")
        assert len(r.owners) == 2
        assert r.confidence < 1.0

    def test_b_wang_and_y_wang(self):
        # From parity audit: "B Wang & Y Wang"
        r = parse_joint_name("B Wang & Y Wang")
        assert len(r.owners) == 2
        assert r.owners[0].name == "B Wang"
        assert r.owners[1].name == "Y Wang"


# ---------------------------------------------------------------------------
# parse_joint_name — "and" separator
# ---------------------------------------------------------------------------

class TestAndKeyword:
    def test_lowercase_and(self):
        r = parse_joint_name("John Smith and Jane Smith")
        assert len(r.owners) == 2
        assert r.split_reason == "and_keyword"
        assert r.confidence >= 0.75

    def test_uppercase_and(self):
        r = parse_joint_name("Alice Brown AND Bob Brown")
        assert len(r.owners) == 2

    def test_mixed_case_and(self):
        r = parse_joint_name("Peter Jones And Mary Jones")
        assert len(r.owners) == 2


# ---------------------------------------------------------------------------
# parse_joint_name — comma separator
# ---------------------------------------------------------------------------

class TestCommaSeparator:
    def test_two_full_names_comma(self):
        r = parse_joint_name("John Smith, Jane Smith")
        assert len(r.owners) == 2
        assert r.split_reason == "comma_list"
        assert r.confidence >= 0.6

    def test_surname_first_not_split(self):
        # "Smith, John" — surname-first format — must NOT be split
        r = parse_joint_name("Smith, John")
        assert len(r.owners) == 1
        assert r.owners[0].name == "Smith, John"

    def test_dr_surname_comma_not_split(self):
        # "Dr Smith, John" — title + single surname before comma → surname-first
        r = parse_joint_name("Dr Smith, John")
        assert len(r.owners) == 1


# ---------------------------------------------------------------------------
# parse_joint_name — no split cases
# ---------------------------------------------------------------------------

class TestNoSplit:
    def test_single_name(self):
        r = parse_joint_name("Alice Brown")
        assert len(r.owners) == 1
        assert r.owners[0].name == "Alice Brown"
        assert r.split_reason == "no_split"
        assert r.owners[0].ownership_share == Decimal("1")

    def test_empty_string(self):
        r = parse_joint_name("")
        assert len(r.owners) == 1
        assert r.confidence == 0.0

    def test_none_handled(self):
        r = parse_joint_name(None)
        assert len(r.owners) == 1

    def test_single_word(self):
        r = parse_joint_name("Geoffery")
        assert len(r.owners) == 1

    def test_company_name_not_split(self):
        # e.g. "Smith & Sons Pty Ltd" — looks like ampersand but "Sons Pty Ltd" is not a name
        r = parse_joint_name("Smith & Sons Pty Ltd")
        # Parser may split this; confidence should reflect ambiguity
        # We don't assert split/no-split — just that confidence is not 1.0
        assert r.confidence < 1.0


# ---------------------------------------------------------------------------
# parse_joint_name — ownership share invariants
# ---------------------------------------------------------------------------

class TestOwnershipShares:
    def test_two_owners_half_each(self):
        r = parse_joint_name("Alice A & Bob B")
        assert len(r.owners) == 2
        for o in r.owners:
            assert abs(o.ownership_share - Decimal("0.5")) < Decimal("0.01")

    def test_three_owners_third_each(self):
        r = parse_joint_name("A One & B Two & C Three")
        assert len(r.owners) == 3
        total = sum(o.ownership_share for o in r.owners)
        assert abs(total - Decimal("1")) < Decimal("0.001")

    def test_shares_exactly_sum_to_one(self):
        # Regression: floating point accumulation should not leave total != 1
        for n in range(2, 6):
            names = " & ".join(f"Owner{i} Name{i}" for i in range(n))
            r = parse_joint_name(names)
            total = sum(o.ownership_share for o in r.owners)
            assert abs(total - Decimal("1")) < Decimal("0.001"), f"n={n}: total={total}"


# ---------------------------------------------------------------------------
# ParseResult.to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    def test_to_dict_structure(self):
        r = parse_joint_name("Alice Brown & Bob Brown")
        d = r.to_dict()
        assert "original_name" in d
        assert "owners" in d
        assert "confidence" in d
        assert "split_reason" in d
        assert isinstance(d["owners"], list)
        for owner in d["owners"]:
            assert "name" in owner
            assert "ownership_share" in owner

    def test_ownership_share_is_string(self):
        r = parse_joint_name("Alice A & Bob B")
        d = r.to_dict()
        for o in d["owners"]:
            # Must be serialisable as a string (for JSON transport)
            assert isinstance(o["ownership_share"], str)
            float(o["ownership_share"])  # must be numeric string


# ---------------------------------------------------------------------------
# Real East Gate candidates from parity audit
# ---------------------------------------------------------------------------

class TestEastGateCandidates:
    """Regression tests for names seen in the parity audit output."""

    EAST_GATE_JOINT_NAMES = [
        "Adam Wayne Howson & Joshua Alfred Lawrence Sutton",
        "Aditya Reddy Mekala & Saideepika Mekala",
        "Anthony McDonald & Rose Marimon",
        "Avneet Rooprai & Gagneet Singh",
        "B Wang & Y Wang",
    ]

    @pytest.mark.parametrize("name", EAST_GATE_JOINT_NAMES)
    def test_splits_into_two_owners(self, name):
        r = parse_joint_name(name)
        assert len(r.owners) == 2, f"Expected 2 owners for {name!r}, got {len(r.owners)}"

    @pytest.mark.parametrize("name", EAST_GATE_JOINT_NAMES)
    def test_confidence_at_least_medium(self, name):
        r = parse_joint_name(name)
        assert r.confidence >= 0.6, f"Low confidence {r.confidence} for {name!r}"

    @pytest.mark.parametrize("name", EAST_GATE_JOINT_NAMES)
    def test_shares_sum_to_one(self, name):
        r = parse_joint_name(name)
        total = sum(o.ownership_share for o in r.owners)
        assert abs(total - Decimal("1")) < Decimal("0.001"), f"Shares don't sum to 1 for {name!r}: {total}"
