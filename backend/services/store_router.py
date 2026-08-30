# @featuretrace:cutover-toggle-safety — the one place a router asks which store serves a request.
# Layer: service
# Data flow: any router/service → resolve_store() → domain_source_guard.require_domain_source()
#            → core.domain_cutover_status (building-scoped).
# Related: backend/services/domain_source_guard.py (the guard this wraps)
#          backend/services/finance_route_cutover_service.py (the finance-only precedent)
#          backend/services/documents_store.py (first consumer)
# Tests: tests/backend/test_store_router.py
"""Generalised source-of-truth dispatch for any domain, read or write.

Why this exists
---------------
`domain_source_guard.require_domain_source()` has always been able to answer "which
store serves this domain for this building". Almost nothing asked it. Measured
2026-08-29: **10 of 133 router files** consult the cutover control plane at all, and
**103 call `db.<collection>` directly** with no dispatch. The only route-level
dispatcher in the codebase — `finance_route_cutover_service` — covers 37 of 1,292
routes, and every one of its policies is `read_only=True`, so **no mutating route
anywhere had a Postgres write path**.

That is the gap this closes. It is deliberately domain-generic and operation-generic,
so a domain does not need its own bespoke policy table to participate.

The contract
------------
* **Fails closed.** A domain with no `core.domain_cutover_status` row resolves to
  MongoDB (footgun #17). Adding a consumer can never silently move a read.
* **Fallback is directional.** PostgreSQL is attempted first only when the control
  plane says so; on failure the caller falls back to MongoDB. Never the reverse —
  falling back to Postgres when Mongo is the designated live source is a
  wrong-direction fallback and a P0.3-class incident risk.
* **Toggles are not consulted here.** A feature toggle only means a PostgreSQL path
  *exists*; the control plane decides what *serves*. `domain_source_guard`'s own
  docstring states this and it is the rule that survived being contradicted by our
  documentation for three weeks.
* **It never raises for a caller that can fall back.** `raise_on_blocked_postgres`
  stays False so a blocked domain degrades to Mongo rather than 503-ing a page.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from models.cutover_status import DataSource
from services.domain_source_guard import DomainSourceAuditContext, require_domain_source

logger = logging.getLogger(__name__)

StoreOperation = Literal["read", "write"]


@dataclass(frozen=True)
class StoreDecision:
    """Which store serves this request, and whether the other one must be kept current."""

    domain: str
    building_id: str
    operation: StoreOperation
    source: str  # "postgres" | "mongo"
    shadow_enabled: bool
    blocked_reason: str | None

    @property
    def use_postgres(self) -> bool:
        return self.source == "postgres"

    @property
    def mirror_to_mongo(self) -> bool:
        """True when a Postgres write must also be mirrored into MongoDB.

        For as long as MongoDB is the store that could rebuild Postgres, a
        Postgres-primary write that is not mirrored silently destroys the DR
        position — the point of no return CLAUDE.md flags for `postgres_write`.
        Mirroring stays on for every write while both stores are live; it is
        switched off per domain only at decommission (Phase 5), never as an
        optimisation.
        """
        return self.operation == "write" and self.source == "postgres"


async def resolve_store(
    *,
    domain: str,
    building_id: str,
    operation: StoreOperation = "read",
    route: str | None = None,
) -> StoreDecision:
    """Return the authoritative store for this domain/building/operation.

    Never raises on a blocked domain — returns a Mongo decision with the reason
    attached, so the caller degrades instead of failing.
    """
    try:
        decision = await require_domain_source(
            building_id=building_id,
            domain=domain,
            operation=operation,
            raise_on_blocked_postgres=False,
            audit_context=DomainSourceAuditContext(
                route=route,
                source_service="store_router",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # The control plane itself being unreachable must not take a page down.
        # Mongo is the fail-closed answer, consistent with a missing status row.
        logger.warning(
            "store_router: control plane unavailable for %s/%s (%s) — falling back to mongo",
            building_id, domain, exc,
        )
        return StoreDecision(
            domain=domain,
            building_id=building_id,
            operation=operation,
            source="mongo",
            shadow_enabled=False,
            blocked_reason=f"control_plane_unavailable: {exc}",
        )

    source = (
        decision.source.value
        if isinstance(decision.source, DataSource)
        else str(decision.source)
    )
    return StoreDecision(
        domain=domain,
        building_id=building_id,
        operation=operation,
        source=source,
        shadow_enabled=bool(getattr(decision, "shadow_enabled", False)),
        blocked_reason=getattr(decision, "blocked_reason", None),
    )


# ---------------------------------------------------------------------------
# Read-through — the shape every wired route uses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadResult:
    """What a store-agnostic read returned, and which store answered.

    `source` is deliberately not a bare "postgres"/"mongo". Four outcomes need
    telling apart and collapsing them hides the ones that need action:

      postgres                        - PostgreSQL answered.
      mongo                           - the control plane says Mongo; nothing else happened.
      mongo_fallback_pg_empty         - PG is designated but holds nothing yet. EXPECTED
                                        during the coexistence window; not an incident.
      mongo_fallback_pg_unavailable   - PG was designated and FAILED. An incident.

    The last two look identical from a caller that only checks "did I get rows",
    which is exactly how a silently-empty Postgres read gets mistaken for a fact.
    """

    items: Any
    source: str
    decision: StoreDecision
    error: str | None = None

    @property
    def served_by_postgres(self) -> bool:
        return self.source == "postgres"


async def read_through(
    *,
    domain: str,
    building_id: str,
    route: str,
    postgres: Callable[[], Awaitable[Any]],
    mongo: Callable[[], Awaitable[Any]],
    empty_falls_back: bool = True,
) -> ReadResult:
    """Try PostgreSQL when the control plane says so; otherwise, or on failure, Mongo.

    Both arguments are zero-argument callables rather than awaited values, so the
    Postgres query is never *executed* for a Mongo-primary domain. Passing coroutines
    would run both stores on every request and quietly double the load — and would
    execute a Postgres query for a domain the control plane has explicitly refused.

    `empty_falls_back=True` is the coexistence-window behaviour: a Postgres table that
    exists but has not been populated yet returns nothing, and blanking a live page on
    that basis is worse than serving MongoDB. Set it False once a domain's data genesis
    has run, at which point empty legitimately means empty and falling back would mask
    a real deletion.

    Fallback is DIRECTIONAL. There is no path here from a Mongo-primary domain to
    Postgres: that inversion is a P0.3-class incident risk and the absence of the code
    to do it is the safeguard.
    """
    decision = await resolve_store(
        domain=domain, building_id=building_id, operation="read", route=route,
    )

    if not decision.use_postgres:
        return ReadResult(items=await mongo(), source="mongo", decision=decision)

    try:
        pg_items = await postgres()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "read_through: Postgres read failed for %s/%s (%s) — falling back to MongoDB",
            building_id, route, exc,
        )
        return ReadResult(
            items=await mongo(),
            source="mongo_fallback_pg_unavailable",
            decision=decision,
            error=str(exc),
        )

    # `None` means the reader could not scope the request at all (no financial-year
    # window, no scheme). That is "unavailable", not "empty" — a distinction the
    # readers in financial_read_service already make deliberately.
    if pg_items is None:
        return ReadResult(
            items=await mongo(),
            source="mongo_fallback_pg_unavailable",
            decision=decision,
            error="postgres reader returned None (could not scope the request)",
        )

    if empty_falls_back and not pg_items:
        return ReadResult(
            items=await mongo(), source="mongo_fallback_pg_empty", decision=decision,
        )

    return ReadResult(items=pg_items, source="postgres", decision=decision)
