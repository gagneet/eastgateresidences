"""
test_route_registration_uniqueness.py — guard against shadowed route registrations
=================================================================================
When two handlers register the same (method, path), FastAPI serves the one
registered FIRST and the second becomes silently unreachable. Nothing warns you:
the endpoint still exists in the OpenAPI schema, still appears in Swagger, and
still has tests that pass when called directly as a function — it just never
receives an HTTP request.

This bit us for real: `POST /api/payment-plans` was declared in both
`routers/finance.py` (manager-only) and `routers/payment_plans.py` (the NSW
Form 1 s.83A owner-initiated hardship request flow). finance_router is included
first, so the entire owner flow was unreachable and owners got 403 instead of
being able to submit a hardship request.

The allowlist below pins the shadows that exist today (tracked in
tasks/GAP-ARCH-004-duplicate-route-registrations.md). It may SHRINK freely as
they are fixed — a shrink just means this test tells you to update the list.
It must never GROW: a new entry means a live endpoint has been silently killed.

Note: duplicate *operation IDs* are a weaker signal and miss half of these —
two handlers with different function names collide on the path without ever
producing a duplicate-operation-ID warning (see the /invoices and
/notifications/preferences entries). Compare on (method, path), not opid.

Run:
  backend/venv/bin/python3 -m pytest tests/backend/test_route_registration_uniqueness.py -q
"""

import os
import sys
from collections import defaultdict

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, os.path.abspath(_BACKEND))


# (method, path) -> the handler that WINS, followed by the shadowed (dead) ones.
# Each entry is a live bug: the "dead" handler never receives a request.
KNOWN_SHADOWED_ROUTES = {
    ("GET", "/api/invoices"): [
        "routers.maintenance.get_invoices",
        "routers.invoices.list_invoices",
    ],
    ("GET", "/api/notifications"): [
        "server.get_notifications",
        "routers.notifications.get_notifications",
    ],
    ("GET", "/api/notifications/preferences"): [
        "routers.communication.get_notification_preferences",
        "routers.notifications.get_email_preferences",
    ],
    ("POST", "/api/notifications/levy-reminder"): [
        "server.send_levy_reminder",
        "routers.notifications.send_levy_reminder",
    ],
    ("POST", "/api/notifications/send"): [
        "server.send_notification",
        "routers.notifications.send_notification",
    ],
    ("PUT", "/api/notifications/preferences"): [
        "routers.communication.update_notification_preferences",
        "routers.notifications.update_email_preferences",
    ],
}


def _collect_api_routes():
    """Flatten every APIRoute reachable from the app.

    FastAPI includes routers lazily (`_IncludedRouter`), so `app.routes` does
    not contain the real route objects — they have to be walked through the
    wrapper. Routes declared inline in server.py already carry the `/api`
    prefix; routes from `routers/*.py` are prefixed at include time, so they
    are normalised here.
    """
    from fastapi.routing import APIRoute
    import server

    def walk(router):
        found = []
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute):
                found.append(route)
                continue
            inner = getattr(route, "original_router", None) or getattr(route, "app", None)
            if inner is not None and hasattr(inner, "routes"):
                found.extend(walk(inner))
        return found

    registrations = defaultdict(list)
    for route in walk(server.app):
        path = route.path if route.path.startswith("/api") else "/api" + route.path
        for method in sorted(route.methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            registrations[(method, path)].append(
                f"{route.endpoint.__module__}.{route.endpoint.__name__}"
            )
    return registrations


@pytest.fixture(scope="module")
def registrations():
    return _collect_api_routes()


def test_no_new_shadowed_routes(registrations):
    """A (method, path) registered twice means the second handler is dead code."""
    shadowed = {k: v for k, v in registrations.items() if len(v) > 1}
    unexpected = {k: v for k, v in shadowed.items() if k not in KNOWN_SHADOWED_ROUTES}

    assert not unexpected, (
        "New shadowed route registration(s) — the handler listed second is now "
        "unreachable over HTTP, even though it still appears in Swagger:\n"
        + "\n".join(
            f"  {m} {p}\n    WINS: {h[0]}\n    DEAD: {', '.join(h[1:])}"
            for (m, p), h in sorted(unexpected.items())
        )
        + "\n\nEither give the endpoints distinct paths, or delete the dead one."
    )


def test_known_shadowed_routes_have_not_silently_changed(registrations):
    """If a known shadow is fixed, shrink the allowlist (this test says so)."""
    shadowed = {k: v for k, v in registrations.items() if len(v) > 1}
    resolved = sorted(k for k in KNOWN_SHADOWED_ROUTES if k not in shadowed)

    assert not resolved, (
        "These route shadows appear to be fixed — remove them from "
        "KNOWN_SHADOWED_ROUTES so the guard keeps tightening:\n"
        + "\n".join(f"  {m} {p}" for m, p in resolved)
    )


def test_winning_handler_is_stable(registrations):
    """Include order decides which handler serves traffic — pin it.

    Reordering `include_router` calls in server.py silently swaps which
    implementation is live. For these paths the two handlers differ in auth
    guard and response shape, so a swap is a breaking change.
    """
    drift = []
    for key, expected in KNOWN_SHADOWED_ROUTES.items():
        actual = registrations.get(key)
        if actual and actual[0] != expected[0]:
            drift.append(f"  {key[0]} {key[1]}\n    expected: {expected[0]}\n    actual:   {actual[0]}")

    assert not drift, (
        "The handler serving these paths changed — check include_router order "
        "in server.py:\n" + "\n".join(drift)
    )
