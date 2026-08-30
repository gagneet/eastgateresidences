from utils.unit_number import canonicalise_unit_from_existing
from utils.unit_number import normalise_unit_token
from utils.unit_number import unit_number_candidates


def test_normalise_unit_token_removes_unit_prefix_and_spaces():
    assert normalise_unit_token("Unit 87") == "87"
    assert normalise_unit_token(" th 087 ") == "TH087"


def test_unit_87_candidates_include_canonical_townhouse_key():
    candidates = unit_number_candidates("87")
    assert "87" in candidates
    assert "U87" in candidates
    assert "U087" in candidates
    assert "UA087" in candidates
    assert "TH087" in candidates


def test_unit_87_display_value_resolves_to_existing_th087():
    assert canonicalise_unit_from_existing("87", ["TH087"]) == "TH087"
    assert canonicalise_unit_from_existing("U87", ["TH087"]) == "TH087"
    assert canonicalise_unit_from_existing("Unit 87", ["TH087"]) == "TH087"


def test_apartment_lot_resolves_to_existing_ua_key():
    assert canonicalise_unit_from_existing("1", ["UA001"]) == "UA001"
    assert canonicalise_unit_from_existing("U1", ["UA001"]) == "UA001"


def test_unknown_unit_falls_back_to_normalised_input():
    assert canonicalise_unit_from_existing("Unit 999", ["TH087"]) == "999"
