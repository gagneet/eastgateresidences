"""Router registration hygiene — guards against two classes of bug found in
the Phase F security review of `feat/upgrade-strataos-system-financials`:

1.  **Stray decorator applied to a private helper.** A `@router.get(...)` with
    no function body immediately below silently binds to the next `def`. If
    that next `def` is an internal helper (named with a leading underscore)
    it gets registered as a public route — usually with no auth dependency —
    which is an authentication-bypass primitive.

2.  **Wrong concrete class imported from the financial_core adapter package.**
    `routers/finance.py` and `routers/onboarding.py` previously imported
    `LedgerRepository` and `OutboxRepository` (the protocol names) from the
    Postgres adapters package, where the actual classes are
    ``PostgresLedgerRepository`` / ``PostgresOutboxRepository``. The
    resulting ImportError was swallowed by ``server.py``'s try/except and
    silently disabled both routers in production.

These tests fail fast at module import time, so any future re-introduction
of either bug breaks CI before reaching staging.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/backend/ → up 2 levels = project root → into backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ──────────────────────────────────────────────────────────────────────────
# 1. No registered route may map to a private (leading underscore) endpoint
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("router_module", [
    "routers.finance",
    "routers.onboarding",
    "routers.auth",
    "routers.financial_onboarding",
])
def test_no_private_endpoint_registered_as_route(router_module: str) -> None:
    """A `@router.get(...)` followed by a private `_helper` would silently
    expose the helper as a public, unauthenticated route. Catch it here.
    """
    import importlib

    module = importlib.import_module(router_module)
    router = getattr(module, "router", None)
    assert router is not None, f"{router_module} has no `router` symbol"

    offenders = [
        (route.path, route.endpoint.__name__)
        for route in router.routes
        if hasattr(route, "endpoint") and route.endpoint.__name__.startswith("_")
    ]
    assert not offenders, (
        f"{router_module}: private helper functions are registered as public routes. "
        f"Offenders (path, endpoint): {offenders}. "
        "Likely cause: a stray @router decorator with no function body below it. "
        "Either remove the decorator or rename the helper without the leading underscore."
    )


# ──────────────────────────────────────────────────────────────────────────
# 2. Routers that depend on financial_core adapters must import cleanly
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("router_module", [
    "routers.finance",
    "routers.onboarding",
    "routers.financial_onboarding",
])
def test_router_imports_without_error(router_module: str) -> None:
    """Pre-existing finance/onboarding routers used to silently fail with
    ``ImportError: cannot import name 'LedgerRepository' from
    services.financial_core.adapters.db_postgres.ledger_repo``. Detect any
    similar regression at test time rather than via "Warning: X router not
    available" in deploy logs.
    """
    import importlib

    try:
        importlib.import_module(router_module)
    except Exception as exc:  # noqa: BLE001 — re-raise with context
        pytest.fail(
            f"{router_module} failed to import: {type(exc).__name__}: {exc}. "
            "If this is a financial_core adapter rename, the routers must be "
            "updated in lockstep — server.py swallows ImportError silently and "
            "the router will not be registered in production."
        )


# ──────────────────────────────────────────────────────────────────────────
# 3. Concrete adapter classes referenced by routers must exist
# ──────────────────────────────────────────────────────────────────────────

def test_postgres_ledger_repository_class_exists() -> None:
    from services.financial_core.adapters.db_postgres.ledger_repo import (
        PostgresLedgerRepository,
    )
    assert isinstance(PostgresLedgerRepository, type)


def test_postgres_outbox_repository_class_exists() -> None:
    from services.financial_core.adapters.db_postgres.outbox_repo import (
        PostgresOutboxRepository,
    )
    assert isinstance(PostgresOutboxRepository, type)
