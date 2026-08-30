"""Every inline route in ``server.py`` must belong to a GAP-SEC-005 migration group.

GAP-SEC-005 filed ``server.py``'s inline routes as "group 12" and then said group 12
cannot be a group: the routes span every domain, so they migrate into groups 1-11
individually. The classification is only useful if it stays complete — a route added to
``server.py`` next month with no home in the plan is a route nobody will migrate.

These tests are that ratchet. They do not assert *which* group a route is in (that is a
judgement recorded in the script and its report); they assert that the classification
covers everything, that the count has not silently grown, and that no inline route has
crept back in with no authentication dependency at all.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "backend" / "scripts" / "audits" / "classify_server_inline_routes.py"
REPORT = REPO_ROOT / "docs" / "security" / "server_inline_route_classification_2026_08_24.md"

#: The count measured on 2026-08-24. This is a ratchet, not a target: routes should leave
#: ``server.py`` for domain routers over time (F-011 Phase B), never arrive. Raising this
#: number means somebody added an inline route to the file the plan is trying to empty.
BASELINE_INLINE_ROUTES = 189

#: Triaged 2026-08-24 and recorded in the report. ``stripe_webhook`` verifies a Stripe
#: signature rather than a session; ``root`` returns a static version banner.
KNOWN_UNAUTHENTICATED = {"stripe_webhook", "root"}


@pytest.fixture(scope="module")
def classifier():
    """Load the audit script as a module without requiring it to be importable as a package."""
    spec = importlib.util.spec_from_file_location("classify_server_inline_routes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def routes(classifier):
    """Generated function header.

    Function: routes
    Path: tests/backend/test_server_inline_route_classification.py
    """
    return classifier.scan((REPO_ROOT / "backend" / "server.py").read_text())


def test_every_inline_route_has_a_migration_group(routes):
    """No route may sit outside groups 0-11 — an unclassified route is an unmigrated one."""
    unclassified = [f"server.py:{r['line']} {r['method']} {r['path']}" for r in routes if r["group"] < 0]
    assert not unclassified, (
        "These inline routes have no GAP-SEC-005 migration group. Add a rule to "
        f"backend/scripts/audits/classify_server_inline_routes.py:\n  " + "\n  ".join(unclassified)
    )


def test_inline_route_count_does_not_grow(routes):
    """server.py's inline surface is being drained, not extended."""
    assert len(routes) <= BASELINE_INLINE_ROUTES, (
        f"server.py now has {len(routes)} inline routes, up from the {BASELINE_INLINE_ROUTES} "
        "measured on 2026-08-24. New routes belong in a domain router under backend/routers/, "
        "where they can carry a capability guard. If this growth is deliberate, classify the "
        "new routes and raise the baseline in the same commit."
    )


def test_no_new_unauthenticated_inline_routes(routes, classifier):
    """A route with no auth dependency at all must be a triaged, named exception."""
    unauthenticated = {
        r["func"] for r in routes if classifier._guard_style(r["guards"]) == "none"
    }
    assert unauthenticated <= KNOWN_UNAUTHENTICATED, (
        "Inline route(s) with no authentication dependency: "
        f"{sorted(unauthenticated - KNOWN_UNAUTHENTICATED)}. Either add a dependency or "
        "triage it into KNOWN_UNAUTHENTICATED with a documented reason."
    )


def test_classification_report_is_present(routes):
    """The report is referenced from the plan; a dangling reference makes the plan wrong."""
    assert REPORT.exists(), f"Missing classification report at {REPORT}"
    text = REPORT.read_text()
    assert "GAP-SEC-005" in text
    # Spot-check that the report was generated from the same script, not hand-maintained.
    assert "classify_server_inline_routes.py" in text
