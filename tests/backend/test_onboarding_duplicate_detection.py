from routers.onboarding import _normalise_scheme_name, _scheme_number_identity_keys


def test_scheme_number_identity_keys_match_up_and_bare_numbers():
    assert _scheme_number_identity_keys("UP13195") & _scheme_number_identity_keys("13195")


def test_scheme_number_identity_keys_ignore_spacing_and_separators():
    assert _scheme_number_identity_keys("UP 13-195") & _scheme_number_identity_keys("13195")


def test_normalise_scheme_name_is_case_and_whitespace_insensitive():
    assert _normalise_scheme_name("  East   Gate Residences ") == _normalise_scheme_name("east gate residences")
