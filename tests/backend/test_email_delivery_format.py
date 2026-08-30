from unittest.mock import AsyncMock

import pytest


def test_html_to_text_strips_markup():
    from utils.email import _html_to_text

    assert _html_to_text("<h1>Hello</h1><p>Line <strong>two</strong></p>") == "Hello\nLine two"


def test_build_email_message_plain_text_excludes_html_part():
    from utils.email import EMAIL_FORMAT_PLAIN, _build_email_message

    msg = _build_email_message(
        subject="Subject",
        sender="sender@example.com",
        sender_name="Sender",
        to_email="owner@example.com",
        html_content="<p>Visible body</p>",
        text_content=None,
        email_format=EMAIL_FORMAT_PLAIN,
    )

    payload = msg.get_payload()
    assert len(payload) == 1
    assert payload[0].get_content_type() == "text/plain"
    assert "Visible body" in payload[0].get_payload(decode=True).decode()


def test_build_email_message_html_includes_plain_fallback_and_contrast_wrapper():
    from utils.email import EMAIL_FORMAT_HTML, _build_email_message

    msg = _build_email_message(
        subject="Subject",
        sender="sender@example.com",
        sender_name="Sender",
        to_email="owner@example.com",
        html_content="<p>Visible body</p>",
        text_content=None,
        email_format=EMAIL_FORMAT_HTML,
    )

    payload = msg.get_payload()
    assert [part.get_content_type() for part in payload] == ["text/plain", "text/html"]
    html = payload[1].get_payload(decode=True).decode()
    assert "background:#ffffff" in html
    assert "color:#111827" in html


def test_full_html_document_receives_contrast_baseline():
    from utils.email import _wrap_email_html

    html = _wrap_email_html(
        "<html><head><title>T</title></head><body style=\"color:#fff\"><p>Body</p></body></html>",
        "Subject",
    )

    assert "color-scheme:light" in html
    assert "body{background:#f3f6f8;color:#111827;}" in html
    assert html.index("color-scheme:light") < html.index("</head>")


def test_full_html_document_with_attributed_html_tag_and_no_head_receives_contrast_baseline():
    from utils.email import _wrap_email_html

    html = _wrap_email_html(
        '<html lang="en"><body style="color:#fff"><p>Body</p></body></html>',
        "Subject",
    )

    assert "color-scheme:light" in html
    assert "body{background:#f3f6f8;color:#111827;}" in html
    assert '<html lang="en">' in html
    assert html.index('<html lang="en">') < html.index("color-scheme:light")


@pytest.mark.asyncio
async def test_recipient_format_prefers_plain_text_from_preferences(monkeypatch):
    from utils import email as email_utils

    monkeypatch.setattr(
        "request_context.get_ctx_building_id",
        lambda: "13195",
    )
    monkeypatch.setattr(
        email_utils.db.users,
        "find_one",
        AsyncMock(return_value={"id": "user-1"}),
    )
    monkeypatch.setattr(
        email_utils.db.email_preferences,
        "find_one",
        AsyncMock(return_value={"email_format": "plain_text"}),
    )
    monkeypatch.setattr(
        email_utils.db.email_notification_preferences,
        "find_one",
        AsyncMock(return_value=None),
    )

    assert await email_utils._get_recipient_email_format("owner@example.com") == "plain_text"


@pytest.mark.asyncio
async def test_recipient_format_defaults_to_html_without_building_context(monkeypatch):
    from utils import email as email_utils

    monkeypatch.setattr(
        "request_context.get_ctx_building_id",
        lambda: None,
    )
    users_find_one = AsyncMock(return_value={"id": "user-1"})
    monkeypatch.setattr(email_utils.db.users, "find_one", users_find_one)

    assert await email_utils._get_recipient_email_format("owner@example.com") == "html"
    users_find_one.assert_not_called()
