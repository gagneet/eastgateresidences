import pytest

from models.user import UserRole
# utils.auth and models.user do not use Motor — no mock needed.
from utils.auth import is_approved_user


def test_guest_is_not_approved():
    """Verify that even an 'approved' guest is not considered an approved resident for PII access."""
    guest_user = {
        "id": "guest1",
        "role": UserRole.GUEST,
        "full_name": "Guest User",
        "is_approved": True,  # Guests are auto-approved in registration
        "is_active": True
    }

    assert is_approved_user(guest_user) is False


def test_service_provider_is_not_approved():
    """Verify that an approved service provider is not considered an approved resident."""
    sp_user = {
        "id": "sp1",
        "role": UserRole.SERVICE_PROVIDER,
        "full_name": "Plumber",
        "is_approved": True,
        "is_active": True
    }

    assert is_approved_user(sp_user) is False


def test_approved_tenant_is_approved():
    """Verify that an approved tenant is correctly identified as approved."""
    tenant_user = {
        "id": "tenant1",
        "role": UserRole.TENANT,
        "full_name": "Tenant User",
        "is_approved": True,
        "is_active": True
    }

    assert is_approved_user(tenant_user) is True


def test_approved_owner_is_approved():
    """Verify that an approved owner is correctly identified as approved."""
    owner_user = {
        "id": "owner1",
        "role": UserRole.OWNER,
        "full_name": "Owner User",
        "is_approved": True,
        "is_active": True
    }

    assert is_approved_user(owner_user) is True


def test_unapproved_owner_is_not_approved():
    """Verify that an unapproved owner is correctly identified as NOT approved."""
    owner_user = {
        "id": "owner2",
        "role": UserRole.OWNER,
        "full_name": "Pending Owner",
        "is_approved": False,
        "is_active": True
    }

    assert is_approved_user(owner_user) is False


def test_admin_is_always_approved():
    """Verify that administrative roles are always considered approved."""
    admin_user = {
        "id": "admin1",
        "role": UserRole.SUPER_ADMIN,
        "full_name": "Admin",
        "is_approved": False,  # Even if flag is False
        "is_active": True
    }

    assert is_approved_user(admin_user) is True

    chairman_user = {
        "id": "chairman1",
        "role": UserRole.EC_MEMBER,
        "full_name": "Chairman",
        "is_approved": False,
        "is_active": True
    }

    assert is_approved_user(chairman_user) is True
