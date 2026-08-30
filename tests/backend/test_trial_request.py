"""
Tests for the public trial-request endpoint (POST /api/public/trial-request).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_body(**overrides):
    base = {
        "name": "Jane Smith",
        "company": "Acme Strata",
        "email": "jane@acmestrata.com.au",
        "phone": "0400 000 000",
        "lots": "48",
        "state": "ACT",
        "message": "Interested in ACT compliance module.",
        "captcha_token": "test-token",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------

class TestTrialRequestBody:
    def test_valid_body(self):
        from routers.trial_request import TrialRequestBody
        b = TrialRequestBody(**_make_body())
        assert b.name == "Jane Smith"
        assert b.company == "Acme Strata"
        assert b.email == "jane@acmestrata.com.au"

    def test_empty_name_rejected(self):
        from routers.trial_request import TrialRequestBody
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TrialRequestBody(**_make_body(name=""))

    def test_empty_company_rejected(self):
        from routers.trial_request import TrialRequestBody
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TrialRequestBody(**_make_body(company=""))

    def test_invalid_email_rejected(self):
        from routers.trial_request import TrialRequestBody
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TrialRequestBody(**_make_body(email="not-an-email"))

    def test_message_truncated_at_2000(self):
        from routers.trial_request import TrialRequestBody
        b = TrialRequestBody(**_make_body(message="x" * 3000))
        assert len(b.message) == 2000

    def test_optional_fields_default_to_empty(self):
        from routers.trial_request import TrialRequestBody
        b = TrialRequestBody(name="A", company="B", email="a@b.com", captcha_token="tok")
        assert b.phone == ""
        assert b.lots == ""
        assert b.state == ""
        assert b.message == ""

    def test_name_too_long_rejected(self):
        from routers.trial_request import TrialRequestBody
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TrialRequestBody(**_make_body(name="x" * 201))


# ---------------------------------------------------------------------------
# reCAPTCHA verification
# ---------------------------------------------------------------------------

class TestRecaptchaVerification:
    @pytest.mark.asyncio
    async def test_no_secret_key_bypasses(self):
        from routers.trial_request import _verify_recaptcha
        with patch("routers.trial_request.RECAPTCHA_SECRET", ""):
            result = await _verify_recaptcha("any-token", "127.0.0.1")
        assert result is True

    @pytest.mark.asyncio
    async def test_passes_when_score_above_threshold(self):
        from routers.trial_request import _verify_recaptcha
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "score": 0.9}
        with patch("routers.trial_request.RECAPTCHA_SECRET", "secret"), \
                patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _verify_recaptcha("token", "1.2.3.4")
        assert result is True

    @pytest.mark.asyncio
    async def test_fails_when_score_below_threshold(self):
        from routers.trial_request import _verify_recaptcha
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "score": 0.2}
        with patch("routers.trial_request.RECAPTCHA_SECRET", "secret"), \
                patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _verify_recaptcha("token", "1.2.3.4")
        assert result is False

    @pytest.mark.asyncio
    async def test_fails_when_recaptcha_success_false(self):
        from routers.trial_request import _verify_recaptcha
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": False, "error-codes": ["invalid-input-response"]}
        with patch("routers.trial_request.RECAPTCHA_SECRET", "secret"), \
                patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _verify_recaptcha("token", "1.2.3.4")
        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        from routers.trial_request import _verify_recaptcha
        with patch("routers.trial_request.RECAPTCHA_SECRET", "secret"), \
                patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("timeout"))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _verify_recaptcha("token", "1.2.3.4")
        assert result is False


# ---------------------------------------------------------------------------
# Email HTML builder
# ---------------------------------------------------------------------------

class TestBuildEmailHtml:
    def test_contains_all_fields(self):
        from routers.trial_request import _build_email_html, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        html = _build_email_html(body)
        assert "Jane Smith" in html
        assert "Acme Strata" in html
        assert "jane@acmestrata.com.au" in html
        assert "0400 000 000" in html
        assert "48" in html
        assert "ACT" in html
        assert "ACT compliance module" in html

    def test_empty_message_omitted(self):
        from routers.trial_request import _build_email_html, TrialRequestBody
        body = TrialRequestBody(**_make_body(message=""))
        html = _build_email_html(body)
        assert "Message:" not in html


# ---------------------------------------------------------------------------
# Endpoint logic — submit_trial_request
# ---------------------------------------------------------------------------

def _make_request():
    from starlette.requests import Request as StarletteRequest
    scope = {"type": "http", "method": "POST", "path": "/api/public/trial-request",
             "query_string": b"", "headers": [], "client": ("127.0.0.1", 9999)}
    return StarletteRequest(scope)


def _mock_db_with_admins(*admin_ids):
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[{"id": aid} for aid in admin_ids])
    mock_db = MagicMock()
    mock_db.users.find.return_value = mock_cursor
    return mock_db


class TestSubmitTrialRequest:
    def setup_method(self, _method):
        # Reset slowapi in-memory rate limit storage before each test so limits
        # from previous tests in this class don't bleed over.
        from utils.rate_limit import limiter
        storage = getattr(limiter, "_storage", None)
        if storage and hasattr(storage, "reset"):
            storage.reset()

    @pytest.mark.asyncio
    async def test_captcha_failure_raises_400(self):
        from routers.trial_request import submit_trial_request, TrialRequestBody
        from fastapi import HTTPException
        body = TrialRequestBody(**_make_body())
        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await submit_trial_request(_make_request(), body)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_success_sends_email_and_notifies_admins(self):
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins("admin1", "admin2")

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.send_email_async", AsyncMock()) as mock_email, \
                patch("routers.trial_request.create_user_notification", AsyncMock()) as mock_notif, \
                patch("routers.trial_request.db", mock_db):
            result = await submit_trial_request(_make_request(), body)

        assert result["status"] == "ok"
        mock_email.assert_awaited_once()
        assert mock_notif.await_count == 2  # one per admin

    @pytest.mark.asyncio
    async def test_email_failure_does_not_crash(self):
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins()

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.send_email_async", AsyncMock(side_effect=Exception("SMTP down"))), \
                patch("routers.trial_request.create_user_notification", AsyncMock()), \
                patch("routers.trial_request.db", mock_db):
            result = await submit_trial_request(_make_request(), body)

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_notification_uses_correct_type(self):
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins("admin1")
        notif_calls = []

        async def capture_notif(**kwargs):
            notif_calls.append(kwargs)

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.send_email_async", AsyncMock()), \
                patch("routers.trial_request.create_user_notification", capture_notif), \
                patch("routers.trial_request.db", mock_db):
            await submit_trial_request(_make_request(), body)

        assert notif_calls[0]["notification_type"] == "trial_request"
        assert notif_calls[0]["user_id"] == "admin1"
        assert "Acme Strata" in notif_calls[0]["message"]

    @pytest.mark.asyncio
    async def test_notification_link_points_to_leads_page(self):
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins("admin1")
        notif_calls = []

        async def capture_notif(**kwargs):
            notif_calls.append(kwargs)

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.send_email_async", AsyncMock()), \
                patch("routers.trial_request.create_user_notification", capture_notif), \
                patch("routers.trial_request.db", mock_db):
            await submit_trial_request(_make_request(), body)

        assert notif_calls[0]["link"] == "/admin/leads"

    @pytest.mark.asyncio
    async def test_lead_persisted_via_create_trial_request(self):
        # Post-migration: persistence goes through Postgres `create_trial_request`,
        # not Mongo `db.trial_requests.insert_one`.
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins()
        create_calls = []

        async def capture_create(**kwargs):
            create_calls.append(kwargs)
            return {"request_id": "rid-1"}

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.create_trial_request", capture_create), \
                patch("routers.trial_request.send_email_async", AsyncMock()), \
                patch("routers.trial_request.create_user_notification", AsyncMock()), \
                patch("routers.trial_request.db", mock_db):
            await submit_trial_request(_make_request(), body)

        assert len(create_calls) == 1
        kwargs = create_calls[0]
        assert kwargs["org_name"] == "Acme Strata"
        assert kwargs["contact_name"] == "Jane Smith"
        assert kwargs["contact_email"] == "jane@acmestrata.com.au"
        assert kwargs["jurisdiction"] in ("ACT", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT")
        assert "Lots: 48" in kwargs["notes"]

    @pytest.mark.asyncio
    async def test_email_failure_is_non_fatal(self):
        # Post-migration: there is no `email_sent` flag — email failure is logged
        # and swallowed so the lead is still saved and the API returns 200.
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins()

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.create_trial_request",
                      AsyncMock(return_value={"request_id": "rid-1"})), \
                patch("routers.trial_request.send_email_async",
                      AsyncMock(side_effect=Exception("SMTP down"))), \
                patch("routers.trial_request.create_user_notification", AsyncMock()), \
                patch("routers.trial_request.db", mock_db):
            result = await submit_trial_request(_make_request(), body)

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_db_failure_is_non_fatal(self):
        # If the Postgres insert itself fails, the endpoint still returns 200
        # so the user sees the friendly message.
        from routers.trial_request import submit_trial_request, TrialRequestBody
        body = TrialRequestBody(**_make_body())
        mock_db = _mock_db_with_admins()

        with patch("routers.trial_request._verify_recaptcha", AsyncMock(return_value=True)), \
                patch("routers.trial_request.create_trial_request",
                      AsyncMock(side_effect=Exception("DB down"))), \
                patch("routers.trial_request.send_email_async", AsyncMock()), \
                patch("routers.trial_request.create_user_notification", AsyncMock()), \
                patch("routers.trial_request.db", mock_db):
            result = await submit_trial_request(_make_request(), body)

        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# TrialRequestPatch model
# ---------------------------------------------------------------------------

class TestTrialRequestPatch:
    # Post-migration design: only `notes` is mutable via PATCH.
    # Status transitions go through dedicated /approve and /reject endpoints.
    def test_notes_trimmed(self):
        from routers.trial_request import TrialRequestPatch
        p = TrialRequestPatch(notes="  leading space  ")
        assert p.notes == "leading space"

    def test_notes_truncated_at_4000(self):
        from routers.trial_request import TrialRequestPatch
        p = TrialRequestPatch(notes="x" * 5000)
        assert len(p.notes) == 4000

    def test_notes_optional(self):
        from routers.trial_request import TrialRequestPatch
        p = TrialRequestPatch()
        assert p.notes is None


# ---------------------------------------------------------------------------
# _require_super_admin guard
# ---------------------------------------------------------------------------

class TestRequireSuperAdmin:
    def test_super_admin_passes(self):
        from routers.trial_request import _require_super_admin
        _require_super_admin({"role": "super_admin"})  # no exception

    def test_effective_role_super_admin_passes(self):
        from routers.trial_request import _require_super_admin
        _require_super_admin({"role": "owner", "effective_role": "super_admin"})

    def test_chairman_rejected(self):
        from routers.trial_request import _require_super_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_super_admin({"role": "chairman"})
        assert exc.value.status_code == 403

    def test_strata_manager_rejected(self):
        from routers.trial_request import _require_super_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _require_super_admin({"role": "strata_manager"})


# ---------------------------------------------------------------------------
# GET /admin/trial-requests
# ---------------------------------------------------------------------------

def _make_admin_user():
    return {"id": "admin1", "role": "super_admin"}


def _list_defaults(**overrides):
    """Explicit defaults so Query(None) FieldInfo objects are never used directly."""
    base = dict(status=None, from_date=None, to_date=None, search=None, page=1, limit=25)
    base.update(overrides)
    return base


# Post-migration: list/patch endpoints delegate to Postgres repo helpers
# (`_pg_list`, `_pg_update`) imported at module scope. Tests mock those.

class TestListTrialRequests:
    @pytest.mark.asyncio
    async def test_non_super_admin_rejected(self):
        from routers.trial_request import list_trial_requests_endpoint
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await list_trial_requests_endpoint(**_list_defaults(current_user={"role": "chairman"}))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_data_pagination_counts(self):
        from routers.trial_request import list_trial_requests_endpoint
        pg_row = {
            "request_id": "rid-1",
            "submitted_at": None,
            "status": "submitted",
            "org_name": "BC",
            "contact_first_name": "Bob",
            "contact_last_name": "",
            "contact_email": "bob@bc.com",
            "contact_phone": "",
            "jurisdiction": "ACT",
            "message": "",
            "notes": "",
            "reviewed_at": None,
            "reject_reason": None,
            "expires_at": None,
            "created_tenant_id": None,
            "invitation_id": None,
        }
        pg_result = {
            "data": [pg_row],
            "total": 1,
            "counts": {"submitted": 1, "approved": 0, "rejected": 0, "expired": 0, "total": 1},
        }
        with patch("routers.trial_request._pg_list", AsyncMock(return_value=pg_result)):
            result = await list_trial_requests_endpoint(**_list_defaults(current_user=_make_admin_user()))

        assert len(result["data"]) == 1
        assert result["data"][0]["request_id"] == "rid-1"
        assert result["pagination"]["total"] == 1
        assert result["counts"]["submitted"] == 1

    @pytest.mark.asyncio
    async def test_invalid_status_filter_raises_422(self):
        from routers.trial_request import list_trial_requests_endpoint
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await list_trial_requests_endpoint(
                **_list_defaults(status="bogus_status", current_user=_make_admin_user())
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_from_date_raises_422(self):
        from routers.trial_request import list_trial_requests_endpoint
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await list_trial_requests_endpoint(
                **_list_defaults(from_date="not-a-date", current_user=_make_admin_user())
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_pagination_total_pages_computed(self):
        from routers.trial_request import list_trial_requests_endpoint
        pg_result = {
            "data": [],
            "total": 60,
            "counts": {"submitted": 60, "approved": 0, "rejected": 0, "expired": 0, "total": 60},
        }
        with patch("routers.trial_request._pg_list", AsyncMock(return_value=pg_result)):
            result = await list_trial_requests_endpoint(
                **_list_defaults(limit=25, current_user=_make_admin_user())
            )
        assert result["pagination"]["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_counts_zero_for_missing_statuses(self):
        from routers.trial_request import list_trial_requests_endpoint
        from db_postgres.repos.trial_request_repo import ALL_PG_STATUSES
        pg_result = {
            "data": [],
            "total": 0,
            "counts": {s: 0 for s in ALL_PG_STATUSES} | {"total": 0},
        }
        with patch("routers.trial_request._pg_list", AsyncMock(return_value=pg_result)):
            result = await list_trial_requests_endpoint(**_list_defaults(current_user=_make_admin_user()))
        for s in ALL_PG_STATUSES:
            assert result["counts"][s] == 0


# ---------------------------------------------------------------------------
# PATCH /admin/trial-requests/{lead_id}
# ---------------------------------------------------------------------------

def _pg_row(**overrides):
    base = {
        "request_id": "rid-1",
        "submitted_at": None,
        "status": "submitted",
        "org_name": "Acme",
        "contact_first_name": "Jane",
        "contact_last_name": "",
        "contact_email": "jane@acme.com",
        "contact_phone": "",
        "jurisdiction": "ACT",
        "message": "",
        "notes": "Follow up",
        "reviewed_at": None,
        "reject_reason": None,
        "expires_at": None,
        "created_tenant_id": None,
        "invitation_id": None,
    }
    base.update(overrides)
    return base


class TestUpdateTrialRequest:
    @pytest.mark.asyncio
    async def test_non_super_admin_rejected(self):
        from routers.trial_request import patch_trial_request, TrialRequestPatch
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await patch_trial_request("rid-1", TrialRequestPatch(), current_user={"role": "owner"})
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from routers.trial_request import patch_trial_request, TrialRequestPatch
        from fastapi import HTTPException
        with patch("routers.trial_request._pg_update", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await patch_trial_request(
                    "missing", TrialRequestPatch(notes="x"), current_user=_make_admin_user()
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_notes_updated(self):
        from routers.trial_request import patch_trial_request, TrialRequestPatch
        updated_row = _pg_row(notes="Follow up Friday")
        update_calls = []

        async def capture_update(request_id, *, notes=None):
            update_calls.append({"request_id": request_id, "notes": notes})
            return updated_row

        with patch("routers.trial_request._pg_update", capture_update):
            result = await patch_trial_request(
                "rid-1", TrialRequestPatch(notes="Follow up Friday"),
                current_user=_make_admin_user(),
            )
        assert result["notes"] == "Follow up Friday"
        assert update_calls[0]["request_id"] == "rid-1"
        assert update_calls[0]["notes"] == "Follow up Friday"

    @pytest.mark.asyncio
    async def test_chairman_rejected(self):
        from routers.trial_request import patch_trial_request, TrialRequestPatch
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await patch_trial_request(
                "rid-1", TrialRequestPatch(notes="x"),
                current_user={"role": "chairman"},
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_serialized_row(self):
        from routers.trial_request import patch_trial_request, TrialRequestPatch
        updated_row = _pg_row(notes="updated")
        with patch("routers.trial_request._pg_update", AsyncMock(return_value=updated_row)):
            result = await patch_trial_request(
                "rid-1", TrialRequestPatch(notes="updated"),
                current_user=_make_admin_user(),
            )
        assert result["request_id"] == "rid-1"
        assert result["status"] == "submitted"
        assert result["notes"] == "updated"
