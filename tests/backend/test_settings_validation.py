import pytest
from pydantic import ValidationError

from models.settings import SiteSettingsUpdate


def test_validate_custom_dates_valid():
    """Verify that valid custom dates pass validation."""
    valid_data = {
        "levy_due_custom_dates": {
            "3": 31,
            "6": 1,
            "9": 1,
            "12": 1
        }
    }
    settings = SiteSettingsUpdate(**valid_data)
    assert settings.levy_due_custom_dates["3"] == 31


def test_validate_custom_dates_invalid_month():
    """Verify that invalid month indices (e.g. 13) fail validation."""
    invalid_data = {
        "levy_due_custom_dates": {
            "13": 1
        }
    }
    with pytest.raises(ValidationError) as excinfo:
        SiteSettingsUpdate(**invalid_data)
    assert "Month index must be an integer string between 1 and 12" in str(excinfo.value)


def test_validate_custom_dates_invalid_day():
    """Verify that invalid days (e.g. 32) fail validation."""
    invalid_data = {
        "levy_due_custom_dates": {
            "1": 32
        }
    }
    with pytest.raises(ValidationError) as excinfo:
        SiteSettingsUpdate(**invalid_data)
    assert "Day must be between 1 and 31" in str(excinfo.value)


def test_validate_custom_dates_non_integer_month():
    """Verify that non-integer month strings fail validation."""
    invalid_data = {
        "levy_due_custom_dates": {
            "abc": 1
        }
    }
    with pytest.raises(ValidationError) as excinfo:
        SiteSettingsUpdate(**invalid_data)
    assert "Month index must be an integer string" in str(excinfo.value)


def test_ip_string_field():
    """Verify that ip_string field is supported."""
    data = {"ip_string": "Test IP String"}
    settings = SiteSettingsUpdate(**data)
    assert settings.ip_string == "Test IP String"
