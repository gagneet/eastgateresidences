"""
Regression test: exactly one GET /api/users route must exist, and it must be
server.py's own handler — not routers/users.py's (confirmed dead code, never
included into server.py's api_router, see docs/migration/phase-d-choices-mongodb-postgres.md
and the DEAD CODE banners at the top of routers/users.py / services/users_pg_service.py).

If this test starts failing because routers/users.py's router gets included somewhere,
that's a signal someone is reviving dead code with a different (toggle-gated
PG-primary/Mongo-fallback) design than the live composite-read handler — resolve the
conflict deliberately, don't let two /users handlers silently coexist.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

import server  # noqa: E402


def _effective_routes():
    """Flatten server.app.routes into leaf routes with real .path/.methods/.endpoint.

    FastAPI (0.141+) no longer eagerly flattens include_router() calls into
    app.routes — a single include_router(api_router) leaves one lazy
    _IncludedRouter wrapper in app.routes, and the actual per-endpoint routes
    (including ones nested via api_router.include_router(sub_router, ...))
    only materialise via that wrapper's effective_candidates(). Recurse
    through any nested _IncludedRouter so this also works if that nesting
    depth changes again in a future FastAPI release.
    """
    leaves = []
    stack = list(server.app.routes)
    while stack:
        route = stack.pop()
        if hasattr(route, "effective_candidates"):
            stack.extend(route.effective_candidates())
        else:
            leaves.append(route)
    return leaves


def test_exactly_one_get_users_route_registered():
    matches = [
        route for route in _effective_routes()
        if getattr(route, "path", None) == "/api/users" and "GET" in (getattr(route, "methods", None) or set())
    ]
    assert len(matches) == 1, (
        f"Expected exactly one GET /api/users route, found {len(matches)}: "
        f"{[getattr(r, 'endpoint', r) for r in matches]}"
    )


def test_canonical_users_route_is_servers_own_handler():
    matches = [
        route for route in _effective_routes()
        if getattr(route, "path", None) == "/api/users" and "GET" in (getattr(route, "methods", None) or set())
    ]
    assert matches, "No GET /api/users route registered at all"
    endpoint = matches[0].endpoint
    assert endpoint is server.get_users, (
        "The live GET /api/users route must be server.py's own get_users handler "
        "(the mongo_pg_union composite read) — routers/users.py's handler is dead code "
        "and must never be the one that's actually reachable."
    )


def test_routers_users_module_is_not_included_in_api_router():
    """routers/users.py must stay unwired — this is what makes it correctly dead code."""
    import routers.users as dead_users_router

    for route in _effective_routes():
        endpoint = getattr(route, "endpoint", None)
        assert endpoint is not getattr(dead_users_router, "get_users", None), (
            "routers/users.py's get_users has been wired into the live app — this "
            "contradicts its DEAD CODE banner and services/identity_shadow_read_service.py's "
            "design, which targets server.py's different composite-read handler instead."
        )
