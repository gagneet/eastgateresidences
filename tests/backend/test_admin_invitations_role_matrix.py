"""Tests for the invitation role matrix (Task B) and resend endpoint (Task D-bonus).

Role matrix under test:
  super_admin   → any role, any tenant
  strata_admin  → strata_manager | admin_staff, own tenant only
  strata_manager → admin_staff, own tenant only
  all others    → 403

Resend endpoint:
  - refreshes token and re-sends email
  - 404 on unknown invitation_id
  - 403 when caller role does not allow inviting target role
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _user(role: str, tenant_id: str = "tenant-abc") -> dict:
    return {"id": "user-1", "role": role, "effective_role": role, "tenant_id": tenant_id}


def _invite_row(target_role: str, inv_id: str = "inv-001") -> dict:
    return {
        "invitation_id": inv_id,
        "tenant_id": "tenant-abc",
        "email": "new@example.com",
        "invited_role": target_role,
        "prefill_first_name": "Test",
        "prefill_last_name": "User",
        "scheme_id": None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from backend.server import app
    return TestClient(app, raise_server_exceptions=True)


# ──────────────────────────────────────────────────────────────────────────────
# Task B — Role matrix on POST /admin/invitations/send
# ──────────────────────────────────────────────────────────────────────────────

class TestInvitationRoleMatrix:

    @pytest.mark.parametrize("target_role", [
        "strata_admin", "strata_manager", "admin_staff", "owner", "tenant", "guest",
    ])
    def test_super_admin_may_invite_any_role(self, target_role):
        with (
            patch("routers.admin_invitations.get_current_user", return_value=_user("super_admin")),
            patch("routers.admin_invitations.create_invitation", new_callable=AsyncMock,
                  return_value=("inv-123", "raw-tok")),
            patch("routers.admin_invitations.send_email_async", new_callable=AsyncMock),
        ):
            from routers.admin_invitations import send_invitation
            from routers.admin_invitations import SendInvitationRequest
            import asyncio
            body = SendInvitationRequest(email="x@y.com", role=target_role)
            result = asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user("super_admin"))
            )
            assert result["invitation_id"] == "inv-123"

    @pytest.mark.parametrize("target_role", ["strata_manager", "admin_staff"])
    def test_strata_admin_may_invite_allowed_roles(self, target_role):
        with (
            patch("routers.admin_invitations.create_invitation", new_callable=AsyncMock,
                  return_value=("inv-456", "raw-tok")),
            patch("routers.admin_invitations.send_email_async", new_callable=AsyncMock),
        ):
            from routers.admin_invitations import send_invitation
            from routers.admin_invitations import SendInvitationRequest
            from fastapi import HTTPException
            import asyncio
            body = SendInvitationRequest(email="x@y.com", role=target_role)
            result = asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user("strata_admin"))
            )
            assert result["invitation_id"] == "inv-456"

    @pytest.mark.parametrize("target_role", ["strata_admin", "super_admin", "owner", "ec_member"])
    def test_strata_admin_cannot_invite_higher_roles(self, target_role):
        from routers.admin_invitations import send_invitation
        from routers.admin_invitations import SendInvitationRequest
        from fastapi import HTTPException
        import asyncio
        body = SendInvitationRequest(email="x@y.com", role=target_role)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user("strata_admin"))
            )
        assert exc_info.value.status_code == 403

    def test_strata_manager_may_invite_admin_staff(self):
        with (
            patch("routers.admin_invitations.create_invitation", new_callable=AsyncMock,
                  return_value=("inv-789", "raw-tok")),
            patch("routers.admin_invitations.send_email_async", new_callable=AsyncMock),
        ):
            from routers.admin_invitations import send_invitation
            from routers.admin_invitations import SendInvitationRequest
            import asyncio
            body = SendInvitationRequest(email="x@y.com", role="admin_staff")
            result = asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user("strata_manager"))
            )
            assert result["invitation_id"] == "inv-789"

    @pytest.mark.parametrize("target_role", ["strata_admin", "strata_manager", "owner"])
    def test_strata_manager_cannot_invite_above_staff(self, target_role):
        from routers.admin_invitations import send_invitation
        from routers.admin_invitations import SendInvitationRequest
        from fastapi import HTTPException
        import asyncio
        body = SendInvitationRequest(email="x@y.com", role=target_role)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user("strata_manager"))
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.parametrize("caller_role", ["owner", "tenant", "ec_member", "guest"])
    def test_non_staff_roles_cannot_send_invitations(self, caller_role):
        from routers.admin_invitations import send_invitation
        from routers.admin_invitations import SendInvitationRequest
        from fastapi import HTTPException
        import asyncio
        body = SendInvitationRequest(email="x@y.com", role="admin_staff")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user(caller_role))
            )
        assert exc_info.value.status_code == 403

    def test_strata_admin_tenant_id_locked_to_jwt(self):
        """Non-SA caller cannot sneak a different tenant_id into the invitation."""
        calls = []

        async def mock_create(**kwargs):
            calls.append(kwargs)
            return ("inv-abc", "raw")

        with (
            patch("routers.admin_invitations.create_invitation", side_effect=mock_create),
            patch("routers.admin_invitations.send_email_async", new_callable=AsyncMock),
        ):
            from routers.admin_invitations import send_invitation
            from routers.admin_invitations import SendInvitationRequest
            import asyncio
            body = SendInvitationRequest(email="x@y.com", role="strata_manager")
            asyncio.get_event_loop().run_until_complete(
                send_invitation(body, _user("strata_admin", tenant_id="tenant-xyz"))
            )
        assert calls[0]["tenant_id"] == "tenant-xyz"


# ──────────────────────────────────────────────────────────────────────────────
# Task D-bonus — POST /admin/invitations/{id}/resend
# ──────────────────────────────────────────────────────────────────────────────

class TestResendInvitation:

    def test_resend_refreshes_token_and_sends_email(self):
        with (
            patch("routers.admin_invitations.find_invitation_by_id",
                  new_callable=AsyncMock, return_value=_invite_row("strata_manager")),
            patch("routers.admin_invitations.refresh_invitation_token",
                  new_callable=AsyncMock, return_value="fresh-raw-token"),
            patch("routers.admin_invitations.send_email_async", new_callable=AsyncMock) as mock_email,
        ):
            from routers.admin_invitations import resend_invitation
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                resend_invitation("inv-001", _user("super_admin"))
            )
        assert result["emails_sent"] == 1
        assert result["invitation_id"] == "inv-001"
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args.kwargs
        assert "fresh-raw-token" in call_kwargs["body"]

    def test_resend_404_on_unknown_invitation(self):
        with patch("routers.admin_invitations.find_invitation_by_id",
                   new_callable=AsyncMock, return_value=None):
            from routers.admin_invitations import resend_invitation
            from fastapi import HTTPException
            import asyncio
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    resend_invitation("no-such-id", _user("super_admin"))
                )
            assert exc_info.value.status_code == 404

    def test_resend_403_when_matrix_blocks_caller(self):
        """strata_manager cannot resend an invite for strata_admin target."""
        with patch("routers.admin_invitations.find_invitation_by_id",
                   new_callable=AsyncMock, return_value=_invite_row("strata_admin")):
            from routers.admin_invitations import resend_invitation
            from fastapi import HTTPException
            import asyncio
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    resend_invitation("inv-001", _user("strata_manager"))
                )
            assert exc_info.value.status_code == 403

    def test_resend_still_returns_on_email_failure(self):
        with (
            patch("routers.admin_invitations.find_invitation_by_id",
                  new_callable=AsyncMock, return_value=_invite_row("admin_staff")),
            patch("routers.admin_invitations.refresh_invitation_token",
                  new_callable=AsyncMock, return_value="tok"),
            patch("routers.admin_invitations.send_email_async",
                  new_callable=AsyncMock, side_effect=Exception("SMTP down")),
        ):
            from routers.admin_invitations import resend_invitation
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                resend_invitation("inv-001", _user("strata_admin"))
            )
        assert result["emails_sent"] == 0
        assert "email delivery failed" in result["message"]
