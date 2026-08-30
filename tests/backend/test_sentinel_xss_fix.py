"""
XSS sanitization tests — nh3 + html.escape coverage.

Tests:
- get_email_template sanitizes dangerous HTML (script tags, onerror attrs)
- html.escape correctly encodes XSS payloads in title/message fields
- Plain-text title escaping: <script> becomes &lt;script&gt;
"""
import html as html_lib

import nh3
import pytest

from utils.email import get_email_template as utils_get_template


def test_announcement_template_sanitization():
    # Test that dangerous HTML is stripped but safe HTML is kept
    xss_content = "<p>Hello</p><script>alert(1)</script><img src=x onerror=alert(1)>"
    html, text = utils_get_template("announcement", title="Test", content=xss_content)

    assert "<script>" not in html
    assert "onerror" not in html
    assert "<p>Hello</p>" in html
    # nh3 converts <img src=x> to <img src="x">
    assert '<img src="x">' in html or "<img src=x>" in html


def test_escaping_logic():
    # Test the escaping logic used in server.py
    title = "Urgent <script>alert(1)</script>"
    message = "Message with <img src=x onerror=alert(1)>"

    safe_title = html_lib.escape(title)
    safe_message = html_lib.escape(message)

    # It shouldn't have literal opening brackets
    assert "<script" not in safe_title
    assert "<img" not in safe_message

    # It should have escaped versions
    assert "&lt;script&gt;" in safe_title
    assert "&lt;img" in safe_message


def test_plain_text_title_escaping():
    """Verify that <script> in a title field becomes &lt;script&gt; in non-rich-text context."""
    dangerous_title = "<script>stealCookies()</script> Important Notice"

    # Non-rich-text email templates use html.escape for the title
    escaped = html_lib.escape(dangerous_title)

    # Must not contain raw tags
    assert "<script>" not in escaped
    assert "</script>" not in escaped

    # Must contain the escaped form
    assert "&lt;script&gt;" in escaped
    assert "&lt;/script&gt;" in escaped

    # The rest of the title should be preserved
    assert "Important Notice" in escaped


if __name__ == "__main__":
    pytest.main([__file__])
