# @featuretrace:route-integrity-audit — Cross-checks seeded navigation routes
#   (backend/seeds/navigation_configs.py + snapshot) against the real Next.js
#   App Router page tree so a nav item can never point at a 404 again.
# Layer: test
# Data flow: test runner → seeds.navigation_configs.NAV_CONFIGS +
#            seeds.snapshot_navigation_configs.NAVIGATION_CONFIGS
#            → scripts/validation/audit_route_integrity.extract_app_router_pages()
# Related: backend/seeds/navigation_configs.py
#          scripts/validation/audit_route_integrity.py
#          tasks/GAP-FE-001-frontend-route-consolidation.md
# Tests: this file

"""Every seeded sidebar `route` must resolve to a real App Router page.

The route-integrity audit in ``scripts/validation/audit_route_integrity.py``
only reads nav hrefs hardcoded in ``DashboardLayout.tsx`` — it does NOT read the
per-role navigation seed (``navigation_configs.py``), which is the actual source
of the sidebar for every non-super-admin role. This suite closes that gap: it
pulls every ``route`` out of both the live seed and the DB snapshot and asserts
each one resolves to a page that exists in ``frontend/src/app/**``.

Background: GAP-FE-001 Item 1. The 2026 product-namespace reorg left twelve
seeded routes pointing at pages that were never built (e.g. ``/real-estate/tenants``,
``/maintenance/work-orders``, ``/admin/keys``). Those were repointed to real
surfaces; this test keeps them honest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# seeds.* imports need backend/ on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
# extract_app_router_pages lives in scripts/validation/.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "validation"
sys.path.insert(0, str(SCRIPTS_DIR))

from audit_route_integrity import extract_app_router_pages  # noqa: E402


# Routes that legitimately do not have a page.tsx of their own but are still
# valid destinations (served by non-page handlers or static files). Empty today
# — every seeded route resolves to a real App Router page — but kept as the
# single documented escape hatch so a future intentional exception is explicit.
KNOWN_NON_PAGE_ROUTES: set[str] = set()

# The routes GAP-FE-001 Item 1 removed from the nav because they 404'd. If any
# reappears in a seed, the reorg drift has regressed.
# NOTE (Item 2, 2026-08-10): "/maintenance/work-orders" was previously listed
# here as broken, but Item 2 BUILT it as a real nested App Router page (the
# canonical home for the service_provider "My work orders" item), so it is no
# longer forbidden — it is anchored instead in CANONICAL_NESTED_ROUTES below.
# The rest stay forbidden: Item 2 routed those concepts to *different* real
# pages (e.g. purchase-orders → /maintenance/purchase-orders, keys →
# /requests/access-control), never back to these dead paths.
PREVIOUSLY_BROKEN_ROUTES: set[str] = {
    "/real-estate/tenants",
    "/real-estate/leases",
    "/real-estate/inspections",
    "/real-estate/owner-reports",
    "/financials/purchase-orders",
    "/compliance/my-compliance",
    "/community/visitors",
    "/community/moves",
    "/community/incidents",
    "/admin/keys",
    "/community/guest-stay",
}


# ---------------------------------------------------------------------------
# Route collection + resolution helpers
# ---------------------------------------------------------------------------

def _strip_query(route: str) -> str:
    """Nav routes may carry a ?tab= deep-link; the page is the path only."""
    return route.split("?", 1)[0].split("#", 1)[0]


def _resolves(route_path: str, app_pages: set[str]) -> bool:
    """True if a concrete path maps to a real App Router page.

    Handles exact static matches and dynamic-segment templates
    (``/maintenance/[id]`` matches ``/maintenance/42``).
    """
    if route_path in app_pages or route_path in KNOWN_NON_PAGE_ROUTES:
        return True
    concrete = [s for s in route_path.split("/") if s]
    for template in app_pages:
        segs = [s for s in template.split("/") if s]
        if len(segs) != len(concrete):
            continue
        if all(t.startswith("[") or t == c for t, c in zip(segs, concrete)):
            return True
    return False


def _live_seed_routes() -> list[tuple[str, str, str]]:
    """(role, item_id, route) for every item in the live navigation seed."""
    from seeds.navigation_configs import NAV_CONFIGS

    out: list[tuple[str, str, str]] = []
    for role, cfg in NAV_CONFIGS.items():
        for item in cfg["simple_items"] + cfg["advanced_items"]:
            out.append((role, item["id"], item["route"]))
    return out


def _snapshot_seed_routes() -> list[tuple[str, str, str]]:
    """(role, item_id, route) for every item in the DB snapshot seed."""
    from seeds.snapshot_navigation_configs import NAVIGATION_CONFIGS

    out: list[tuple[str, str, str]] = []
    for entry in NAVIGATION_CONFIGS:
        role = entry["role"]
        for item in entry.get("simple_items", []) + entry.get("advanced_items", []):
            out.append((role, item["id"], item["route"]))
    return out


_APP_PAGES = extract_app_router_pages()
_LIVE = _live_seed_routes()
_SNAPSHOT = _snapshot_seed_routes()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_app_router_pages_extracted():
    """Sanity: the extractor found the tree (guards against a bad path)."""
    assert "/dashboard" in _APP_PAGES
    assert "/real-estate" in _APP_PAGES
    assert len(_APP_PAGES) > 50


@pytest.mark.parametrize("role,item_id,route", _LIVE, ids=lambda v: v if isinstance(v, str) else "")
def test_live_seed_route_resolves(role, item_id, route):
    path = _strip_query(route)
    assert _resolves(path, _APP_PAGES), (
        f"navigation_configs.py role={role} item={item_id!r} route={route!r} "
        f"has no matching App Router page (would 404)"
    )


@pytest.mark.parametrize("role,item_id,route", _SNAPSHOT, ids=lambda v: v if isinstance(v, str) else "")
def test_snapshot_seed_route_resolves(role, item_id, route):
    path = _strip_query(route)
    assert _resolves(path, _APP_PAGES), (
        f"snapshot_navigation_configs.py role={role} item={item_id!r} route={route!r} "
        f"has no matching App Router page (would 404)"
    )


def test_no_previously_broken_routes_reappear():
    """The twelve GAP-FE-001 404 routes must not come back in either seed."""
    live = {r for _, _, r in _LIVE}
    snap = {r for _, _, r in _SNAPSHOT}
    both = live | snap
    regressed = PREVIOUSLY_BROKEN_ROUTES & both
    assert not regressed, f"stale nav 404 routes reintroduced: {sorted(regressed)}"


def test_tab_deeplink_targets_are_real_pages():
    """?tab= deep-links must at least resolve to a real base page."""
    for role, item_id, route in _LIVE:
        if "?tab=" not in route:
            continue
        base = _strip_query(route)
        assert base in _APP_PAGES, (
            f"tab deep-link role={role} item={item_id!r} route={route!r} "
            f"base page {base!r} does not exist"
        )


# ---------------------------------------------------------------------------
# GAP-FE-001 Item 2 — URL nesting + de-duplication guards
# ---------------------------------------------------------------------------

# Canonical nested routes the Item-2 restructure introduced. Each replaces a
# ?tab= deep-link or a mispointed generic hub. Anchored so a future edit can't
# silently revert a nav item back to the flat/mispointed target.
CANONICAL_NESTED_ROUTES: dict[tuple[str, str], str] = {
    ("real_estate_agent", "lease-renewals"): "/real-estate/renewals",
    ("real_estate_agent", "inspections"): "/owner-hub/inspections",
    ("admin_staff", "keys"): "/requests/access-control",
    ("admin_staff", "visitors"): "/community/bookings/visitors",
    ("admin_staff", "moves"): "/community/bookings/moves",
    ("service_provider", "my-work-orders"): "/maintenance/work-orders",
    ("service_provider", "purchase-orders"): "/maintenance/purchase-orders",
    ("guest", "my-stay"): "/community/bookings/my-stay",
}


def test_canonical_nested_routes_are_wired():
    """The Item-2 repoints must stay pointed at their canonical nested pages."""
    live = {(role, item_id): route for role, item_id, route in _LIVE}
    for key, expected in CANONICAL_NESTED_ROUTES.items():
        assert live.get(key) == expected, (
            f"nav item {key} should target {expected!r}, got {live.get(key)!r} "
            f"(GAP-FE-001 Item 2 URL-nesting regression)"
        )


def _within_role_duplicate_destinations(rows):
    """Return {(role, route): [item_ids]} for any role that points two distinct
    nav items at the identical route (path+query). One label → one destination."""
    from collections import defaultdict

    by_role: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for role, item_id, route in rows:
        by_role[role][route].append(item_id)
    return {
        (role, route): ids
        for role, routes in by_role.items()
        for route, ids in routes.items()
        if len(ids) > 1
    }


def test_no_within_role_duplicate_destinations_live():
    """No role may have two menu items resolving to the same route in the live seed."""
    dups = _within_role_duplicate_destinations(_LIVE)
    assert not dups, f"within-role duplicate nav destinations (live seed): {dups}"


def test_no_within_role_duplicate_destinations_snapshot():
    """Same guard for the DB snapshot seed."""
    dups = _within_role_duplicate_destinations(_SNAPSHOT)
    assert not dups, f"within-role duplicate nav destinations (snapshot seed): {dups}"
