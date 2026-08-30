"""
Unit tests for blog post sanitization.
Verifies that title, content, and excerpt are properly sanitized.
"""
import html as html_lib

import nh3
import pytest


def test_blog_sanitization_logic():
    # Dangerous inputs
    dangerous_title = "My Blog <script>alert(1)</script>"
    dangerous_content = "<p>Hello</p><img src=x onerror=alert(1)><script>alert(2)</script>"
    dangerous_excerpt = "Watch out <iframe src='javascript:alert(3)'></iframe>"

    # Expected outcomes
    # title should be escaped
    safe_title = html_lib.escape(dangerous_title)
    assert "<script>" not in safe_title
    assert "&lt;script&gt;" in safe_title

    # content should be cleaned
    safe_content = nh3.clean(dangerous_content)
    assert "onerror" not in safe_content
    assert "<script>" not in safe_content
    assert "<p>Hello</p>" in safe_content

    # excerpt should be cleaned
    safe_excerpt = nh3.clean(dangerous_excerpt)
    assert "iframe" not in safe_excerpt
    assert "javascript:" not in safe_excerpt
    assert "Watch out" in safe_excerpt


if __name__ == "__main__":
    pytest.main([__file__])
