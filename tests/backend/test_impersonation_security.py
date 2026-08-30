from unittest.mock import patch

import pytest

from models.user import UserRole, UserStatus, Permission
from utils.permissions import user_to_response


def test_user_to_response_pii_masking_impersonated():
    """Verify that PII fields are masked when an impersonator_id is present in the user dict."""
    user = {
        "id": "user-123",
        "email": "john.doe@example.com",
        "full_name": "John Doe",
        "role": UserRole.OWNER,
        "is_active": True,
        "is_approved": True,
        "status": UserStatus.ACTIVE,
        "phone": "+61412345678",
        "phone_home": "0261000000",
        "home_address": "123 Fake St",
        "home_suburb": "Springfield",
        "home_state": "ACT",
        "home_postcode": "2600",
        "postal_address": "PO BOX 1",
        "postal_suburb": "Canberra",
        "postal_state": "ACT",
        "postal_postcode": "2601",
        "last_login_ip": "127.0.0.1",
        "mail_username": "john.d@eastgateresidences.com.au",
        "created_at": "2026-01-01T00:00:00Z",
        "impersonator_id": "admin-456"  # This triggers masking
    }

    with patch('utils.permissions.get_user_permissions') as mock_perms:
        mock_perms.return_value = Permission()
        response = user_to_response(user)

    # Check masked fields
    assert response.email == "j***@example.com"
    assert response.phone == "+6******78"
    assert response.phone_home == "02******00"
    assert response.home_address == "REDACTED"
    assert response.home_suburb == "REDACTED"
    assert response.home_state == "REDACTED"
    assert response.home_postcode == "REDACTED"
    assert response.postal_address == "REDACTED"
    assert response.postal_suburb == "REDACTED"
    assert response.postal_state == "REDACTED"
    assert response.postal_postcode == "REDACTED"
    assert response.last_login_ip == "REDACTED"
    assert response.mail_username == "j***@eastgateresidences.com.au"

    # Check masked/non-masked fields
    assert response.full_name == "Resident"
    assert response.id == "user-123"


def test_user_to_response_no_masking_normal():
    """Verify that PII fields are NOT masked when impersonator_id is absent."""
    user = {
        "id": "user-123",
        "email": "john.doe@example.com",
        "full_name": "John Doe",
        "role": UserRole.OWNER,
        "is_active": True,
        "is_approved": True,
        "status": UserStatus.ACTIVE,
        "phone": "+61412345678",
        "home_address": "123 Fake St",
        "created_at": "2026-01-01T00:00:00Z"
    }

    with patch('utils.permissions.get_user_permissions') as mock_perms:
        mock_perms.return_value = Permission()
        response = user_to_response(user)

    assert response.email == "john.doe@example.com"
    assert response.phone == "+61412345678"
    assert response.home_address == "123 Fake St"
