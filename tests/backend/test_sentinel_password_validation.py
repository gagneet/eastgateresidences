"""
Tests for enforced password length validation across various models and endpoints.
"""

import pytest
from pydantic import ValidationError

from models.user import UserCreate, UserLogin, PasswordResetConfirm, UserUpdate
from routers.auth import ChangePasswordRequest, AdminUserPasswordReset


def test_user_create_password_length():
    # Valid
    UserCreate(email="test@example.com", password="password123", full_name="Test User")

    # Too short
    with pytest.raises(ValidationError):
        UserCreate(email="test@example.com", password="short", full_name="Test User")

    # Too long
    with pytest.raises(ValidationError):
        UserCreate(email="test@example.com", password="a" * 129, full_name="Test User")


def test_user_login_password_length():
    # Valid
    UserLogin(email="test@example.com", password="password123")

    # Short is allowed for login (backward compatibility)
    UserLogin(email="test@example.com", password="123")

    # Too long is rejected
    with pytest.raises(ValidationError):
        UserLogin(email="test@example.com", password="a" * 129)


def test_password_reset_confirm_length():
    # Valid
    PasswordResetConfirm(token="token", new_password="password123")

    # Too short
    with pytest.raises(ValidationError):
        PasswordResetConfirm(token="token", new_password="short")

    # Too long
    with pytest.raises(ValidationError):
        PasswordResetConfirm(token="token", new_password="a" * 129)


def test_user_update_mail_password_length():
    # Valid
    UserUpdate(mail_password="password123")

    # Too short
    with pytest.raises(ValidationError):
        UserUpdate(mail_password="short")

    # Too long
    with pytest.raises(ValidationError):
        UserUpdate(mail_password="a" * 129)


def test_change_password_request_length():
    # Valid
    ChangePasswordRequest(current_password="old_password", new_password="new_password123")

    # New password too short
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="old_password", new_password="short")

    # New password too long
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="old_password", new_password="a" * 129)

    # Current password too long
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="a" * 129, new_password="new_password123")


def test_admin_user_password_reset_length():
    # Valid
    AdminUserPasswordReset(identifier="user@example.com", new_password="password123")

    # Too short
    with pytest.raises(ValidationError):
        AdminUserPasswordReset(identifier="user@example.com", new_password="short")

    # Too long
    with pytest.raises(ValidationError):
        AdminUserPasswordReset(identifier="user@example.com", new_password="a" * 129)
