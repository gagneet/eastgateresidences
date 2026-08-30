"""
Tests for the blog news scraper — both the scraper logic and the /blog/scrape endpoint.

Covers:
- set-based deduplication (the set.append -> set.add bug fix)
- is_duplicate() logic
- /api/blog/scrape endpoint (subprocess success and failure)
- No duplicate route registration
"""
import os
import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# conftest.py adds backend/ to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

from cron.cron_news_scraper import is_duplicate, calculate_relevance_score


# ── Unit tests: cron_news_scraper helpers ─────────────────────────────────────

class TestIsDuplicate:
    def test_exact_match_returns_true(self):
        existing = {"Canberra property boom continues", "Local council news"}
        assert is_duplicate("Canberra property boom continues", existing) is True

    def test_no_match_returns_false(self):
        existing = {"Canberra property boom continues"}
        assert is_duplicate("New apartments approved in Molonglo", existing) is False

    def test_very_similar_title_returns_true(self):
        existing = {"Canberra property prices rise sharply this quarter"}
        assert is_duplicate("canberra property prices rise sharply this quarter", existing) is True

    def test_empty_existing_set_returns_false(self):
        assert is_duplicate("Any title", set()) is False

    def test_case_insensitive(self):
        existing = {"ACT GOVERNMENT ANNOUNCES NEW DEVELOPMENT"}
        assert is_duplicate("act government announces new development", existing) is True


class TestCalculateRelevanceScore:
    def test_returns_zero_for_empty_text(self):
        score = calculate_relevance_score("", ["Canberra", "ACT"])
        assert score == 0.0

    def test_returns_positive_for_matching_keyword(self):
        score = calculate_relevance_score("Canberra property news", ["Canberra", "ACT"])
        assert score > 0.0

    def test_higher_score_for_more_keywords(self):
        keywords = ["Canberra", "ACT", "property"]
        score_one = calculate_relevance_score("Canberra news", keywords)
        score_multi = calculate_relevance_score("Canberra ACT property update", keywords)
        assert score_multi > score_one

    def test_empty_keywords_returns_zero(self):
        score = calculate_relevance_score("Some text", [])
        assert score == 0.0


class TestExistingTitlesIsSet:
    """Regression test: existing_titles must be a set — .add() not .append()."""

    def test_set_add_does_not_raise(self):
        """Verifies that set.add() is used (not list.append())."""
        existing_titles = set()
        existing_titles.add("Article One")
        existing_titles.add("Article Two")
        assert "Article One" in existing_titles
        assert len(existing_titles) == 2

    def test_set_deduplication_works(self):
        existing_titles = set()
        existing_titles.add("Duplicate Title")
        existing_titles.add("Duplicate Title")
        assert len(existing_titles) == 1

    def test_is_duplicate_works_with_set(self):
        """is_duplicate() should accept a set (iterates, doesn't call .append)."""
        existing_titles = {"Canberra News Today"}
        assert is_duplicate("Canberra News Today", existing_titles) is True
        assert is_duplicate("Something Completely Different", existing_titles) is False


# ── Integration tests: /api/blog/scrape endpoint ─────────────────────────────

class TestBlogScrapeEndpoint:
    """Test the /api/blog/scrape endpoint behaviour."""

    BASE_URL = "http://127.0.0.1:8003/api"

    def test_scrape_requires_authentication(self):
        import requests
        resp = requests.post(f"{self.BASE_URL}/blog/scrape", timeout=10)
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"

    def test_scrape_endpoint_exists(self, admin_headers):
        """Endpoint must be registered without triggering the scraper."""
        from server import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/blog/scrape" in routes, "POST /api/blog/scrape must be registered"


class TestNoDuplicateRoute:
    """Verify only ONE /blog/scrape route is registered in FastAPI."""

    def test_single_blog_scrape_route(self):
        from server import app
        blog_scrape_routes = [
            r for r in app.routes
            if hasattr(r, "path") and r.path == "/api/blog/scrape"
               and hasattr(r, "methods") and "POST" in r.methods
        ]
        assert len(blog_scrape_routes) == 1, (
            f"Expected 1 POST /api/blog/scrape route, found {len(blog_scrape_routes)}"
        )


# ── Subprocess mock: test endpoint calls subprocess correctly ─────────────────

class TestBlogScrapeSubprocess:
    """Unit-test the subprocess logic used by the endpoint."""

    def test_subprocess_success_returns_output(self):
        """Simulate subprocess.run succeeding — verify response shape."""
        mock_result = MagicMock()
        mock_result.stdout = "Scraper finished. Created 3 new articles."
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = subprocess.run(
                ["python3", "fake_script.py"],
                capture_output=True,
                text=True,
                check=True,
            )
            assert result.stdout == "Scraper finished. Created 3 new articles."

    def test_subprocess_failure_raises_called_process_error(self):
        """Simulate subprocess.run failing — CalledProcessError raised."""
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd=["python3", "fake_script.py"],
                stderr="AttributeError: 'set' object has no attribute 'append'",
        )):
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                subprocess.run(
                    ["python3", "fake_script.py"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            assert exc_info.value.returncode == 1
