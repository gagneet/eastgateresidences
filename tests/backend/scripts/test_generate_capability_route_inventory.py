"""
Tests for scripts/validation/generate_capability_route_inventory.py.

This script had zero test coverage before a 2026-07-16 audit found and fixed 3 real
bugs in it (see docs/features/capability_consolidation_analysis.md's corrections and
git history for scripts/validation/generate_capability_route_inventory.py):

  1. count_inbound_links() used unbounded substring matching, so a route that is a
     literal string-prefix of another route inflated its own count (confirmed in
     production data: /dashboard's count was 307, mostly false positives, before the
     boundary-check fix).
  2. find_legacy_import() only resolved one hop (app-router page -> directly imported
     legacy component), so a component reachable only through another legacy
     component was invisible and fell into the orphan-scan pass.
  3. The orphan-scan classifier treated inbound_link_count <= 1 as
     "retirement_candidate," conflating "referenced by exactly one file" with
     "essentially unreferenced."

These tests exist specifically so none of the three can regress silently. Uses
isolated temp directories via monkeypatching the module's path constants — no
dependency on the real repo tree, no filesystem state left behind (pytest's tmp_path
fixture is auto-cleaned regardless of pass/fail).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_VALIDATION_DIR = str(Path(__file__).resolve().parents[3] / "scripts" / "validation")
if _SCRIPTS_VALIDATION_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_VALIDATION_DIR)

import generate_capability_route_inventory as gen  # noqa: E402


# ─── Pure functions: route_domain, scan_file_body, classify ──────────────────────────

class TestRouteDomain:
    def test_dashboard_subroute_buckets_on_second_segment(self):
        # No live route has this /dashboard/<x>/<y> shape post-migration (only bare
        # /dashboard remains under the (dashboard) route group — everything else moved
        # to (app)) but route_domain()'s dashboard-subroute branch is still real,
        # reachable code (e.g. for legacy/orphan entries), so it still needs coverage.
        assert gen.route_domain("/dashboard/finance/savings") == "dashboard/finance"

    def test_dashboard_root_is_dashboard_home(self):
        assert gen.route_domain("/dashboard") == "dashboard-home"

    def test_non_dashboard_route_buckets_on_first_segment(self):
        assert gen.route_domain("/register/staff") == "register"

    def test_root_route(self):
        assert gen.route_domain("/") == "root"

    def test_orphan_legacy_component_marker(self):
        route = "(no app-router route — orphan legacy component: Foo)"
        assert gen.route_domain(route) == "orphan-legacy-component"


class TestScanFileBody:
    def test_extracts_featuretrace_tags(self):
        text = "# @featuretrace:finance — some feature\n# @featuretrace:levy — another"
        body = gen.scan_file_body(text)
        assert body["featuretrace_tags"] == ["finance", "levy"]

    def test_extracts_role_helpers(self):
        text = "if (isAdmin() || isManager()) { doThing(); }"
        body = gen.scan_file_body(text)
        assert body["role_helpers_used"] == ["isAdmin", "isManager"]

    def test_extracts_feature_flags_and_permissions(self):
        text = "hasFeatureAccess('trust_accounting') && hasPermission('can_manage_finances')"
        body = gen.scan_file_body(text)
        assert body["feature_flags_checked_inline"] == ["trust_accounting"]
        assert body["permissions_checked_inline"] == ["can_manage_finances"]

    def test_extracts_api_dependencies(self):
        text = "api.get('/trust/v2/accounts'); api.post(`/documents/${id}`)"
        body = gen.scan_file_body(text)
        assert "GET /trust/v2/accounts" in body["api_dependencies"]
        assert "POST /documents/${id}" in body["api_dependencies"]


# ─── Bug #1 regression: substring-boundary matching ───────────────────────────────────

class TestInboundLinkMatcher:
    def test_exact_match_found(self):
        matches = gen._inbound_link_matcher("/financials")
        assert matches("router.push('/financials')") is True

    def test_prefix_of_longer_route_not_falsely_matched(self):
        """/dashboard should NOT match text that only references
        /financials/intelligence or any /dashboard/<child> route — the exact
        bug that inflated /dashboard's count to 307 in production."""
        matches = gen._inbound_link_matcher("/dashboard")
        assert matches("href: '/financials/intelligence'") is False
        assert matches("router.push('/admin/buildings')") is False

    def test_prefix_of_hyphenated_slug_not_falsely_matched(self):
        matches = gen._inbound_link_matcher("/financials")
        assert matches("'/financials/intelligence'") is False

    def test_route_at_end_of_string_still_matches(self):
        matches = gen._inbound_link_matcher("/financials")
        assert matches("href=\"/financials\"") is True

    def test_dynamic_segment_matches_template_literal_prefix(self):
        matches = gen._inbound_link_matcher("/maintenance/{id}")
        assert matches("router.push(`/maintenance/${id}`)") is True

    def test_dynamic_segment_does_not_match_unrelated_route(self):
        matches = gen._inbound_link_matcher("/maintenance/{id}")
        assert matches("router.push('/requests/123')") is False

    def test_count_inbound_links_excludes_self(self, tmp_path):
        target = tmp_path / "target.tsx"
        target.write_text("export default function Target() { return null; }")
        other = tmp_path / "other.tsx"
        other.write_text("router.push('/financials')")
        count = gen.count_inbound_links("/financials", [target, other], exclude=target)
        assert count == 1

    def test_count_inbound_links_root_route_always_zero(self, tmp_path):
        f = tmp_path / "any.tsx"
        f.write_text("href='/'")
        assert gen.count_inbound_links("/", [f], exclude=tmp_path / "nonexistent.tsx") == 0


# ─── classify(): status assignment, including the threshold fix ──────────────────────

class TestClassify:
    def _entry(self, **kwargs):
        defaults = dict(
            route="/dashboard/example",
            source_file="frontend/src/app/(dashboard)/dashboard/example/page.tsx",
            component_kind="app_router_page",
        )
        defaults.update(kwargs)
        return gen.RouteEntry(**defaults)

    def test_root_route_always_canonical(self):
        entry = self._entry(route="/", inbound_link_count=0)
        gen.classify(entry)
        assert entry.status == "canonical"

    def test_known_alias_pair_marks_alias_and_canonical(self):
        alias = self._entry(route="/owner-view")
        gen.classify(alias)
        assert alias.status == "alias"

        canonical = self._entry(route="/dashboard")
        gen.classify(canonical)
        assert canonical.status == "canonical"

    def test_app_router_page_zero_inbound_is_retirement_candidate(self):
        entry = self._entry(navigation_mode="not_configured", inbound_link_count=0)
        gen.classify(entry)
        assert entry.status == "retirement_candidate"

    def test_app_router_page_one_inbound_is_supporting_not_retirement_candidate(self):
        """A routed page referenced by exactly one other file is real evidence of use,
        not near-zero evidence of disuse — must not be flagged for retirement."""
        entry = self._entry(navigation_mode="not_configured", inbound_link_count=1)
        gen.classify(entry)
        assert entry.status == "supporting"

    def test_legacy_component_zero_inbound_is_retirement_candidate(self):
        entry = self._entry(component_kind="legacy_dashboard_component",
                             navigation_mode="not_configured", inbound_link_count=0)
        gen.classify(entry)
        assert entry.status == "retirement_candidate"

    def test_legacy_component_one_inbound_is_legacy_not_retirement_candidate(self):
        """Regression test for the exact bug found in RequestsPage.jsx's 8 Tab
        components: a legacy component genuinely imported by exactly one real file
        must not be classified as a retirement candidate."""
        entry = self._entry(component_kind="legacy_dashboard_component",
                             navigation_mode="not_configured", inbound_link_count=1)
        gen.classify(entry)
        assert entry.status == "legacy"

    def test_simple_nav_mode_is_canonical(self):
        entry = self._entry(navigation_mode="simple")
        gen.classify(entry)
        assert entry.status == "canonical"

    def test_mixed_nav_mode_is_supporting_not_canonical(self):
        """Regression test: a route that's Simple-mode for some roles and
        Advanced-mode for others is `mixed`, not `canonical` — confirmed real case:
        /financials (Simple for super_admin/strata_manager, Advanced for
        ec_member/strata_admin)."""
        entry = self._entry(navigation_mode="mixed")
        gen.classify(entry)
        assert entry.status == "supporting"

    def test_feature_flagged_nav_entry_is_specialist(self):
        entry = self._entry(navigation_mode="advanced", feature_flag_from_nav="gst_bas_ledger")
        gen.classify(entry)
        assert entry.status == "specialist"


# ─── Bug #2 regression: transitive legacy-component import resolution ────────────────

class TestTransitiveLegacyImports:
    """Recreates the RequestsPage.jsx -> Tab components scenario in miniature."""

    @pytest.fixture
    def isolated_tree(self, tmp_path, monkeypatch):
        root = tmp_path
        app_dir = root / "frontend" / "src" / "app"
        legacy_dir = root / "frontend" / "src" / "pages" / "dashboard"
        (app_dir / "(dashboard)" / "dashboard" / "requests").mkdir(parents=True)
        (legacy_dir / "requests").mkdir(parents=True)

        monkeypatch.setattr(gen, "ROOT", root)
        monkeypatch.setattr(gen, "APP_DIR", app_dir)
        monkeypatch.setattr(gen, "LEGACY_DASHBOARD_DIR", legacy_dir)

        page_tsx = app_dir / "(dashboard)" / "dashboard" / "requests" / "page.tsx"
        page_tsx.write_text(
            'import RequestsPage from "@/pages/dashboard/RequestsPage";\n'
            "export default function Page() { return <RequestsPage/>; }\n"
        )

        requests_page = legacy_dir / "RequestsPage.jsx"
        requests_page.write_text(
            "import MaintenanceRequestTab from './requests/MaintenanceRequestTab';\n"
            "import PetRequestTab from './requests/PetRequestTab';\n"
            "export default function RequestsPage() {\n"
            "  return <><MaintenanceRequestTab/><PetRequestTab/></>;\n"
            "}\n"
        )

        (legacy_dir / "requests" / "MaintenanceRequestTab.jsx").write_text(
            "export default function MaintenanceRequestTab() { return null; }\n"
        )
        (legacy_dir / "requests" / "PetRequestTab.jsx").write_text(
            "export default function PetRequestTab() { return null; }\n"
        )
        # A genuine orphan, not imported from anywhere, for contrast.
        (legacy_dir / "OrphanPage.jsx").write_text(
            "export default function OrphanPage() { return null; }\n"
        )

        return {
            "root": root, "app_dir": app_dir, "legacy_dir": legacy_dir,
            "page_tsx": page_tsx, "requests_page": requests_page,
        }

    def test_find_legacy_import_resolves_direct_alias_import(self, isolated_tree):
        text = isolated_tree["page_tsx"].read_text()
        resolved = gen.find_legacy_import(text, isolated_tree["page_tsx"])
        assert resolved == "frontend/src/pages/dashboard/RequestsPage.jsx"

    def test_expand_transitive_closure_includes_second_hop_components(self, isolated_tree):
        """The core regression test: MaintenanceRequestTab.jsx and PetRequestTab.jsx
        are only reachable through RequestsPage.jsx (a legacy component itself), not
        directly from any app-router page. Before the fix, both were invisible to the
        routed set and fell through to the orphan-scan pass."""
        direct = {Path("frontend/src/pages/dashboard/RequestsPage.jsx")}
        closure = gen._expand_transitive_legacy_imports(direct)

        assert Path("frontend/src/pages/dashboard/RequestsPage.jsx") in closure
        assert Path("frontend/src/pages/dashboard/requests/MaintenanceRequestTab.jsx") in closure
        assert Path("frontend/src/pages/dashboard/requests/PetRequestTab.jsx") in closure
        # The genuine orphan must NOT be swept in by the closure.
        assert Path("frontend/src/pages/dashboard/OrphanPage.jsx") not in closure

    def test_orphan_scan_excludes_transitively_reachable_files(self, isolated_tree, monkeypatch):
        """End-to-end: running main()'s orphan-scan logic (replicated here at unit
        scope, since main() also touches real navigation_configs.py and stdout)
        must not report MaintenanceRequestTab/PetRequestTab as orphans, but must
        still report OrphanPage.jsx."""
        direct = {Path("frontend/src/pages/dashboard/RequestsPage.jsx")}
        routed_legacy_files = gen._expand_transitive_legacy_imports(direct)

        legacy_dir = isolated_tree["legacy_dir"]
        orphan_names = []
        for legacy_path in sorted(legacy_dir.rglob("*")):
            if legacy_path.suffix not in (".jsx", ".tsx"):
                continue
            rel = legacy_path.relative_to(isolated_tree["root"])
            if rel in routed_legacy_files:
                continue
            orphan_names.append(legacy_path.name)

        assert "MaintenanceRequestTab.jsx" not in orphan_names
        assert "PetRequestTab.jsx" not in orphan_names
        assert "RequestsPage.jsx" not in orphan_names  # captured directly, not an orphan
        assert "OrphanPage.jsx" in orphan_names
