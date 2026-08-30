# @featuretrace:route-integrity-audit — Test suite for the route integrity audit script.
# Layer: test
# Data flow: test runner → scripts/validation/audit_route_integrity.py functions (local file reads).
# Related: scripts/validation/audit_route_integrity.py
#          docs/migration/ui-api-route-integrity-audit.md
# Tests: this file

"""Tests for the route integrity audit script.

Verifies:
  - Manifest extraction functions (App Router pages, nav hrefs, backend routes)
  - Broken link detection (nav → missing page)
  - Orphan page detection
  - Staged/known-covered router classification
  - API path mismatch detection
  - False-positive suppression for parameterized routes
  - Powerhouse and admin-internal page classification
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Add scripts/validation to path so we can import the module
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "validation"
sys.path.insert(0, str(SCRIPTS_DIR))

from audit_route_integrity import (
    AuditReport,
    RouteIssue,
    INTENTIONALLY_UNLISTED_PAGES,
    POWERHOUSE_SHELL_PAGES,
    ADMIN_INTERNAL_PAGES,
    STAGED_UNREGISTERED_ROUTERS,
    NON_ROUTER_OR_COVERED_FILES,
    KNOWN_PARAMETERIZED_MATCHES,
    extract_app_router_pages,
    extract_frontend_api_calls,
    extract_nav_hrefs,
    extract_registered_routers,
    run_audit,
    render_markdown_report,
)


# ---------------------------------------------------------------------------
# Unit tests: manifest extraction
# ---------------------------------------------------------------------------

class TestExtractAppRouterPages:
    def test_returns_set_of_strings(self):
        pages = extract_app_router_pages()
        assert isinstance(pages, set)
        for p in pages:
            assert isinstance(p, str)

    def test_includes_known_dashboard_page(self):
        pages = extract_app_router_pages()
        assert "/dashboard" in pages

    def test_includes_powerhouse_pages(self):
        pages = extract_app_router_pages()
        assert "/powerhouse/conversations" in pages
        assert "/powerhouse/automation" in pages
        assert "/powerhouse/inbox-settings" in pages

    def test_includes_admin_cutover_page(self):
        pages = extract_app_router_pages()
        assert "/admin/cutover-status" in pages

    def test_does_not_include_page_tsx_suffix(self):
        pages = extract_app_router_pages()
        for p in pages:
            assert not p.endswith("page.tsx"), f"Page should not include file suffix: {p}"


class TestExtractNavHrefs:
    def test_returns_set_of_strings(self):
        hrefs = extract_nav_hrefs()
        assert isinstance(hrefs, set)

    def test_includes_core_dashboard_links(self):
        hrefs = extract_nav_hrefs()
        assert "/dashboard" in hrefs
        # Nav restructured to sub-routes -- there is no longer a bare "/financials" index
        # link, only e.g. "/financials/overview" (verified live 2026-08-12: no nav component
        # references a bare "/financials" href at all).
        assert "/financials/overview" in hrefs
        assert "/maintenance" in hrefs

    def test_does_not_include_mailto_links(self):
        hrefs = extract_nav_hrefs()
        for href in hrefs:
            assert not href.startswith("mailto:"), f"mailto link leaked into hrefs: {href}"

    def test_safety_feed_page_exists(self):
        """safety-feed page.tsx was created — nav link is now satisfied."""
        hrefs = extract_nav_hrefs()
        pages = extract_app_router_pages()
        assert "/intelligence/safety-feed" in hrefs
        assert "/intelligence/safety-feed" in pages

    def test_document_converter_page_exists(self):
        """document-converter is in nav and has an App Router page."""
        hrefs = extract_nav_hrefs()
        pages = extract_app_router_pages()
        assert "/documents/converter" in hrefs
        assert "/documents/converter" in pages


class TestExtractRegisteredRouters:
    def test_returns_two_sets(self):
        all_r, registered = extract_registered_routers()
        assert isinstance(all_r, set)
        assert isinstance(registered, set)

    def test_registered_is_subset_of_all(self):
        all_r, registered = extract_registered_routers()
        assert registered.issubset(all_r)

    def test_finance_router_is_registered(self):
        _, registered = extract_registered_routers()
        assert "finance.py" in registered

    def test_settings_and_users_not_registered(self):
        """settings.py and users.py are staged but not yet registered."""
        _, registered = extract_registered_routers()
        assert "settings.py" not in registered
        assert "users.py" not in registered

    def test_cutover_admin_is_registered(self):
        _, registered = extract_registered_routers()
        assert "cutover_admin.py" in registered

    def test_powerhouse_conversations_is_registered(self):
        _, registered = extract_registered_routers()
        assert "powerhouse_conversations.py" in registered


class TestExtractFrontendApiCalls:
    def test_returns_set_of_strings(self):
        paths = extract_frontend_api_calls()
        assert isinstance(paths, set)
        for path in paths:
            assert isinstance(path, str)

    def test_includes_app_router_calls(self):
        paths = extract_frontend_api_calls()
        assert "/api/settings" in paths
        assert "/api/units?limit=1" in paths

    def test_includes_component_and_lib_calls(self):
        paths = extract_frontend_api_calls()
        assert "/api/engagement/nav/badges" in paths
        assert "/api/engagement/triage" in paths


# ---------------------------------------------------------------------------
# Unit tests: known exception sets
# ---------------------------------------------------------------------------

class TestKnownExceptionSets:
    def test_dynamic_routes_in_intentionally_unlisted(self):
        assert "/maintenance/[id]" in INTENTIONALLY_UNLISTED_PAGES
        assert "/admin/buildings/[id]" in INTENTIONALLY_UNLISTED_PAGES

    def test_powerhouse_shell_pages_set(self):
        assert "/powerhouse/conversations" in POWERHOUSE_SHELL_PAGES
        assert "/powerhouse/automation" in POWERHOUSE_SHELL_PAGES
        assert "/powerhouse/inbox-settings" in POWERHOUSE_SHELL_PAGES

    def test_admin_internal_pages_set(self):
        assert "/admin/cutover-status" in ADMIN_INTERNAL_PAGES

    def test_staged_unregistered_routers(self):
        assert "settings.py" in STAGED_UNREGISTERED_ROUTERS
        assert "users.py" in STAGED_UNREGISTERED_ROUTERS

    def test_parcels_in_non_router_files(self):
        assert "parcels.py" in NON_ROUTER_OR_COVERED_FILES

    def test_covered_server_py_routers(self):
        assert "community.py" in NON_ROUTER_OR_COVERED_FILES
        assert "documents.py" in NON_ROUTER_OR_COVERED_FILES
        assert "meetings.py" in NON_ROUTER_OR_COVERED_FILES

    def test_parameterized_matches(self):
        assert "/api/legal-pages/privacy-policy" in KNOWN_PARAMETERIZED_MATCHES
        assert "/api/legal-pages/terms-of-use" in KNOWN_PARAMETERIZED_MATCHES
        assert "/api/tenant/maintenance" in KNOWN_PARAMETERIZED_MATCHES


# ---------------------------------------------------------------------------
# Integration tests: full audit run
# ---------------------------------------------------------------------------

class TestRunAudit:
    """Integration tests that run the full audit against the real codebase."""

    @pytest.fixture(scope="class")
    def report(self) -> AuditReport:
        return run_audit()

    def test_returns_audit_report(self, report):
        assert isinstance(report, AuditReport)

    def test_document_converter_not_in_missing_pages(self, report):
        paths = [i.path for i in report.missing_pages]
        assert "/documents/converter" not in paths

    def test_safety_feed_not_in_missing_pages(self, report):
        """safety-feed page.tsx now exists — it should no longer appear as a missing page."""
        paths = [i.path for i in report.missing_pages]
        assert "/intelligence/safety-feed" not in paths

    def test_missing_pages_are_errors(self, report):
        for issue in report.missing_pages:
            assert issue.severity == "error"

    def test_powerhouse_pages_now_in_nav(self, report):
        """Powerhouse pages were added to DashboardLayout nav — no longer orphaned warnings."""
        nav_hrefs = extract_nav_hrefs()
        assert "/powerhouse/conversations" in nav_hrefs
        assert "/powerhouse/automation" in nav_hrefs
        assert "/powerhouse/inbox-settings" in nav_hrefs
        # Not flagged as warnings because they now have nav entries
        orphan_paths = [i.path for i in report.powerhouse_status]
        assert "/powerhouse/conversations" not in orphan_paths

    def test_cutover_status_now_in_nav(self, report):
        """cutover-status was added to DashboardLayout nav — no longer an admin-internal orphan."""
        nav_hrefs = extract_nav_hrefs()
        assert "/admin/cutover-status" in nav_hrefs
        admin_internal_paths = [i.path for i in report.admin_internal]
        assert "/admin/cutover-status" not in admin_internal_paths

    def test_finance_years_api_mismatch_resolved(self, report):
        """Frontend /finance/years call was corrected — mismatch no longer present."""
        paths = [i.path for i in report.api_issues]
        assert "/api/finance/years" not in paths

    def test_trust_period_locks_api_mismatch_resolved(self, report):
        """Frontend /trust/reconciliation/period-locks call was corrected — mismatch resolved."""
        paths = [i.path for i in report.api_issues]
        assert "/api/trust/reconciliation/period-locks" not in paths

    def test_no_false_positive_for_legal_pages(self, report):
        """legal-pages/{slug} parameterized routes must not appear as mismatches."""
        mismatch_paths = [i.path for i in report.api_issues]
        assert "/api/legal-pages/privacy-policy" not in mismatch_paths
        assert "/api/legal-pages/terms-of-use" not in mismatch_paths

    def test_no_false_positive_for_tenant_maintenance(self, report):
        mismatch_paths = [i.path for i in report.api_issues]
        assert "/api/tenant/maintenance" not in mismatch_paths

    def test_settings_in_staged_info(self, report):
        info_paths = [i.path for i in report.info]
        assert "backend/routers/settings.py" in info_paths

    def test_users_in_staged_info(self, report):
        info_paths = [i.path for i in report.info]
        assert "backend/routers/users.py" in info_paths

    def test_no_broken_nav_links(self, report):
        """Navigation entries should resolve to an App Router page, not a known 404 list."""
        error_paths = {i.path for i in report.missing_pages if i.severity == "error"}
        assert not error_paths, f"Broken nav links found: {sorted(error_paths)}"


# ---------------------------------------------------------------------------
# Unit tests: report rendering
# ---------------------------------------------------------------------------

class TestRenderMarkdownReport:
    def test_renders_without_exception(self):
        report = AuditReport(
            missing_pages=[
                RouteIssue(
                    severity="error",
                    category="missing_page",
                    path="/dashboard/test-missing",
                    description="Test missing page",
                    recommended_fix="Create the page",
                )
            ]
        )
        md = render_markdown_report(report)
        assert isinstance(md, str)
        assert len(md) > 0

    def test_markdown_contains_summary_section(self):
        report = AuditReport()
        md = render_markdown_report(report)
        assert "## Summary" in md

    def test_markdown_contains_powerhouse_matrix(self):
        report = AuditReport()
        md = render_markdown_report(report)
        assert "Powerhouse Feature Visibility Matrix" in md

    def test_markdown_contains_missing_page_path(self):
        report = AuditReport(
            missing_pages=[
                RouteIssue(
                    severity="error",
                    category="missing_page",
                    path="/intelligence/safety-feed",
                    description="Missing page",
                    recommended_fix="Fix it",
                )
            ]
        )
        md = render_markdown_report(report)
        assert "/intelligence/safety-feed" in md

    def test_error_count_in_summary(self):
        report = AuditReport(
            missing_pages=[
                RouteIssue(severity="error", category="missing_page",
                           path="/x", description="d", recommended_fix="f"),
                RouteIssue(severity="error", category="missing_page",
                           path="/y", description="d", recommended_fix="f"),
            ]
        )
        md = render_markdown_report(report)
        assert "| Broken nav links" in md
        assert "2" in md
