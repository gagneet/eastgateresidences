"""
Tests for the WeasyPrint letter generation router.

Coverage:
  1.  GET /letters/templates — list templates, role guard (owner → 403)
  2.  POST /letters/generate — happy path (WeasyPrint mocked), invalid template 422
  3.  POST /letters/generate — WeasyPrint unavailable → 503
  4.  POST /letters/send — happy path, emails queued, letter logged
  5.  POST /letters/send — rate limit (>10 in last 60s) → 429
  6.  POST /letters/send — role guard (owner → 403)
  7.  GET /letters/history — list sent letters, is_test_data excluded
  8.  Cross-tenant: history from building A not visible to building B
  9.  HTML escaping — XSS characters in data are escaped in generated HTML
 10.  All three template renderers produce valid non-empty HTML
"""

from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from server import app, get_current_user, get_current_building

client = TestClient(app)

# ── Fixtures ───────────────────────────────────────────────────────────────────

BUILDING_A = "building-letters-a"
BUILDING_B = "building-letters-b"
MANAGER_ID = "user-mgr-letters"
OWNER_ID = "user-owner-letters"

SAMPLE_PDF = b"%PDF-1.4 fake-pdf-bytes"

LEVY_DATA = {
    "owner_name": "John Smith",
    "unit_number": "42",
    "amount_due": "1530.50",
    "due_date": "2026-06-01",
    "quarter": "Q2 2026",
    "payment_ref": "REF-42-Q2",
}
AGM_DATA = {
    "owner_name": "Jane Doe",
    "agm_date": "2026-07-15",
    "agm_time": "6:30 PM",
    "agm_location": "Common Room, Level 2",
    "agenda_items": "1. Welcome\n2. Financials\n3. Election of EC",
}
NOTICE_DATA = {
    "owner_name": "Bob Builder",
    "subject": "Maintenance Works",
    "body": "We will be conducting lift maintenance on Monday.\nPlease plan accordingly.",
    "closing": "Kind regards",
}


def _manager(uid=MANAGER_ID, role="strata_manager"):
    return {"id": uid, "full_name": "Manager", "role": role, "is_approved": True}


def _owner():
    return {"id": OWNER_ID, "full_name": "Owner", "role": "owner", "is_approved": True}


def _building(building_id=BUILDING_A):
    return {"id": building_id, "name": "Test Building", "address": "1 Test St"}


def _log_entry(building_id=BUILDING_A, is_test=False):
    entry = {
        "id": "log-001",
        "building_id": building_id,
        "template": "levy_reminder",
        "unit_numbers": ["42"],
        "sent_by": MANAGER_ID,
        "sent_by_name": "Manager",
        "sent_at": "2026-04-26T10:00:00Z",
        "recipient_count": 1,
        "subject": "Levy Reminder",
    }
    if is_test:
        entry["is_test_data"] = True
    return entry


_DEFAULT_OWNERS = {
    "42": {"owner_name": "John Smith", "owner_email": "owner@example.com", "owner_id": "u-42", "source": "user_units"},
}


def _mock_db(building_id=BUILDING_A, log_entries=None, recent_count=0):
    mock = MagicMock()

    # buildings.find_one → building info
    mock.buildings.find_one = AsyncMock(return_value=_building(building_id))

    # letters_log.count_documents → recent rate-limit count
    mock.letters_log.count_documents = AsyncMock(return_value=recent_count)

    # letters_log.find → history
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=log_entries or [])
    mock.letters_log.find.return_value = cursor

    # letters_log.insert_one
    mock.letters_log.insert_one = AsyncMock(return_value=MagicMock(inserted_id="log-new"))

    return mock


# ── 1. GET /letters/templates ──────────────────────────────────────────────────

class TestListTemplates:
    def test_returns_three_templates(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()):
            resp = client.get("/api/letters/templates")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "levy_reminder" in names
        assert "agm_invitation" in names
        assert "general_notice" in names

    def test_owner_gets_403(self):
        app.dependency_overrides[get_current_user] = lambda: _owner()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()):
            resp = client.get("/api/letters/templates")
        app.dependency_overrides.clear()
        assert resp.status_code == 403

    def test_each_template_has_fields_schema(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()):
            resp = client.get("/api/letters/templates")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        for tmpl in resp.json():
            assert "fields" in tmpl
            assert len(tmpl["fields"]) > 0


# ── 2 & 3. POST /letters/generate ─────────────────────────────────────────────

class TestGenerateLetter:
    def test_generate_levy_reminder_returns_pdf(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()), \
                patch("routers.letters.generate_letter_pdf", AsyncMock(return_value=SAMPLE_PDF)):
            resp = client.post("/api/letters/generate", json={"template": "levy_reminder", "data": LEVY_DATA})
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == SAMPLE_PDF

    def test_generate_agm_invitation(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()), \
                patch("routers.letters.generate_letter_pdf", AsyncMock(return_value=SAMPLE_PDF)):
            resp = client.post("/api/letters/generate", json={"template": "agm_invitation", "data": AGM_DATA})
        app.dependency_overrides.clear()
        assert resp.status_code == 200

    def test_generate_general_notice(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()), \
                patch("routers.letters.generate_letter_pdf", AsyncMock(return_value=SAMPLE_PDF)):
            resp = client.post("/api/letters/generate", json={"template": "general_notice", "data": NOTICE_DATA})
        app.dependency_overrides.clear()
        assert resp.status_code == 200

    def test_invalid_template_returns_422(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()):
            resp = client.post("/api/letters/generate", json={"template": "nonexistent", "data": {}})
        app.dependency_overrides.clear()
        assert resp.status_code == 422

    def test_weasyprint_unavailable_returns_503(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()), \
                patch("routers.letters.generate_letter_pdf",
                      AsyncMock(side_effect=RuntimeError("WeasyPrint is not installed"))):
            resp = client.post("/api/letters/generate", json={"template": "levy_reminder", "data": LEVY_DATA})
        app.dependency_overrides.clear()
        assert resp.status_code == 503

    def test_ec_member_chairman_can_generate(self):
        """Regression: _LETTER_ROLES previously omitted 'ec_member' on the false premise
        that 'chairman' normalized to 'strata_admin' (it doesn't — see models/user.py's
        normalize_user_role docstring). A real chairman has role='ec_member', so EC
        members — including the chairman — must be able to generate letters."""
        app.dependency_overrides[get_current_user] = lambda: _manager(role="ec_member")
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()), \
                patch("routers.letters.generate_letter_pdf", AsyncMock(return_value=SAMPLE_PDF)):
            resp = client.post("/api/letters/generate", json={"template": "levy_reminder", "data": LEVY_DATA})
        app.dependency_overrides.clear()
        assert resp.status_code == 200

    def test_owner_cannot_generate(self):
        app.dependency_overrides[get_current_user] = lambda: _owner()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()), \
                patch("routers.letters.generate_letter_pdf", AsyncMock(return_value=SAMPLE_PDF)):
            resp = client.post("/api/letters/generate", json={"template": "levy_reminder", "data": LEVY_DATA})
        app.dependency_overrides.clear()
        assert resp.status_code == 403


# ── 4 & 5. POST /letters/send ─────────────────────────────────────────────────

class TestSendLetters:
    def test_send_queues_email_and_logs(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        mock_db = _mock_db()
        with patch("routers.letters.db", mock_db), \
                patch("routers.letters.get_all_unit_owners", AsyncMock(return_value=_DEFAULT_OWNERS)), \
                patch("routers.letters.send_email_async", AsyncMock()):
            resp = client.post("/api/letters/send", json={
                "template": "levy_reminder",
                "data": LEVY_DATA,
                "unit_numbers": ["42"],
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] == 1
        mock_db.letters_log.insert_one.assert_awaited_once()

    def test_send_logs_correct_template_and_units(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        mock_db = _mock_db()
        multi_owners = {
            "10": {"owner_name": "Alice", "owner_email": "alice@example.com", "owner_id": "u-10",
                   "source": "user_units"},
            "11": {"owner_name": "Bob", "owner_email": "bob@example.com", "owner_id": "u-11", "source": "user_units"},
        }
        with patch("routers.letters.db", mock_db), \
                patch("routers.letters.get_all_unit_owners", AsyncMock(return_value=multi_owners)), \
                patch("routers.letters.send_email_async", AsyncMock()):
            client.post("/api/letters/send", json={
                "template": "general_notice",
                "data": NOTICE_DATA,
                "unit_numbers": ["10", "11"],
            })
        app.dependency_overrides.clear()
        call_kwargs = mock_db.letters_log.insert_one.call_args[0][0]
        assert call_kwargs["template"] == "general_notice"
        assert call_kwargs["building_id"] == BUILDING_A
        assert call_kwargs["sent_by"] == MANAGER_ID

    def test_rate_limit_429_when_10_recent(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db(recent_count=10)):
            resp = client.post("/api/letters/send", json={
                "template": "levy_reminder",
                "data": LEVY_DATA,
                "unit_numbers": ["42"],
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 429

    def test_owner_cannot_send(self):
        app.dependency_overrides[get_current_user] = lambda: _owner()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()):
            resp = client.post("/api/letters/send", json={
                "template": "levy_reminder",
                "data": LEVY_DATA,
                "unit_numbers": ["42"],
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 403

    def test_units_without_email_skipped(self):
        """Units that resolve to no owner_email must not count toward sent."""
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        no_email_owners = {
            "42": {"owner_name": "No Email", "owner_email": None, "owner_id": "u-42", "source": "units_legacy"},
        }
        mock_db = _mock_db()
        with patch("routers.letters.db", mock_db), \
                patch("routers.letters.get_all_unit_owners", AsyncMock(return_value=no_email_owners)), \
                patch("routers.letters.send_email_async", AsyncMock()):
            resp = client.post("/api/letters/send", json={
                "template": "levy_reminder",
                "data": LEVY_DATA,
                "unit_numbers": ["42"],
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["sent"] == 0


# ── 6. GET /letters/history ────────────────────────────────────────────────────

class TestLetterHistory:
    def test_returns_sent_letters(self):
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db(log_entries=[_log_entry()])):
            resp = client.get("/api/letters/history")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["template"] == "levy_reminder"

    def test_is_test_data_excluded(self):
        """is_test_data=True entries must not appear via the production list endpoint."""
        # The filter is applied at DB query level; here we verify the query filter is passed
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        mock_db = _mock_db(log_entries=[])
        with patch("routers.letters.db", mock_db):
            resp = client.get("/api/letters/history")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        call_filter = mock_db.letters_log.find.call_args[0][0]
        assert call_filter.get("is_test_data") == {"$ne": True}

    def test_owner_cannot_view_history(self):
        app.dependency_overrides[get_current_user] = lambda: _owner()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A
        with patch("routers.letters.db", _mock_db()):
            resp = client.get("/api/letters/history")
        app.dependency_overrides.clear()
        assert resp.status_code == 403


# ── 7. Cross-tenant isolation ──────────────────────────────────────────────────

class TestCrossTenantIsolation:
    def test_history_scoped_to_building(self):
        """History query must include building_id — verified via call_args."""
        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_B
        mock_db = _mock_db(building_id=BUILDING_B, log_entries=[])
        with patch("routers.letters.db", mock_db):
            resp = client.get("/api/letters/history")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        call_filter = mock_db.letters_log.find.call_args[0][0]
        assert call_filter["building_id"] == BUILDING_B


# ── 8. HTML escaping (XSS prevention) ─────────────────────────────────────────

class TestHtmlEscaping:
    """Verify that user-supplied XSS payloads are escaped in rendered HTML."""

    def _render(self, template, data):
        from utils.letter_generator import render_letter_html
        return render_letter_html(template, data,
                                  {"name": "Test Building", "building_id": BUILDING_A, "address": "1 St"})

    def test_levy_owner_name_escaped(self):
        html = self._render("levy_reminder", {**LEVY_DATA, "owner_name": "<script>alert(1)</script>"})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_levy_amount_escaped(self):
        html = self._render("levy_reminder", {**LEVY_DATA, "amount_due": '"><img src=x onerror=alert(1)>'})
        assert "<img" not in html

    def test_general_body_newlines_safe(self):
        html = self._render("general_notice", {**NOTICE_DATA, "body": "Line1\n<b>Line2</b>"})
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_agm_agenda_items_escaped(self):
        html = self._render("agm_invitation", {**AGM_DATA, "agenda_items": "Item1\n<script>xss</script>"})
        assert "<script>" not in html


# ── 9. Template renderers produce valid HTML ───────────────────────────────────

class TestTemplateRenderers:
    def _render(self, template, data):
        from utils.letter_generator import render_letter_html
        return render_letter_html(template, data,
                                  {"name": "Test Building", "building_id": BUILDING_A, "address": "1 Test St"})

    def test_levy_reminder_contains_amount(self):
        html = self._render("levy_reminder", LEVY_DATA)
        assert "1530.50" in html
        assert "42" in html

    def test_agm_invitation_contains_date_and_location(self):
        html = self._render("agm_invitation", AGM_DATA)
        assert "2026-07-15" in html
        assert "Common Room" in html

    def test_general_notice_contains_subject_and_body(self):
        html = self._render("general_notice", NOTICE_DATA)
        assert "Maintenance Works" in html
        assert "lift maintenance" in html

    def test_all_renderers_produce_doctype(self):
        for template, data in [
            ("levy_reminder", LEVY_DATA),
            ("agm_invitation", AGM_DATA),
            ("general_notice", NOTICE_DATA),
        ]:
            html = self._render(template, data)
            assert "<!DOCTYPE html>" in html, f"{template} missing DOCTYPE"
            assert "</html>" in html, f"{template} missing closing </html>"
