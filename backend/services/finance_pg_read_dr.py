# @featuretrace:finance-postgres-read-cutover — PG-primary finance read with directional Mongo DR fallback.
# Layer: service
# Data flow: route handler -> read_pg_first_with_mongo_dr(source, pg_read, mongo_read) -> payload.
#            Consumes the source decided by finance_route_cutover_service; adds the resilience layer.
#            Freshness gate reads db.dr_snapshot_meta, written by backend/scripts/dr_mongo_snapshot.py.
# Related: backend/services/finance_route_cutover_service.py (decides source: mongo|postgres)
#          backend/scripts/dr_mongo_snapshot.py (writes the snapshot this gate checks)
#          docs/architecture/onboarding/04_onboarding_financial_data_flow.md (§ cutover / DR)
"""PG-primary finance reads with Mongo as a DISASTER-RECOVERY fallback — never a data-completeness one.

When the cutover resolver says a finance route should read from Postgres, the app must serve
Postgres — but it must NOT fall over if Postgres is briefly unavailable. This helper encodes the
one rule that keeps both properties true at once (CLAUDE.md data-rule 8, directional fallback):

    * source != "postgres"        -> serve Mongo (pg_read is never called).
    * source == "postgres":
        - pg_read() succeeds with a real payload (INCLUDING an empty/zero one) -> serve it.
          An empty result is a VALID Postgres answer, not a fallback trigger. "PG has no data yet"
          must render as Postgres's zero, so the migration is visibly incomplete rather than
          silently masked by Mongo. This is the deliberate "even if it returns a zero" directive.
        - pg_read() RAISES, or returns None (could not build a payload at all) -> a DR event:
          fall back to mongo_read(), log a WARNING, fire the optional telemetry hook, and serve
          Mongo so the route stays up.

The distinction is the whole point: fall back on PG *failure*, never on PG *empty*. Mongo remains
the resilience/DR layer; it just stops being the default and stops masquerading behind "PG is empty".

Freshness gate (added 2026-08-11, right-sized DR design): a DR fallback that blindly trusts
whatever Mongo data happens to exist — however stale, however it got there — is unsafe even for a
non-critical app, because the failure mode is "silently wrong dollar figures shown as current," not
just "outage." When a caller opts in via `require_fresh_snapshot=True`, the fallback checks
`db.dr_snapshot_meta` (written by `scripts/dr_mongo_snapshot.py`) for a completed, reconciled
snapshot within `max_snapshot_age_minutes` before serving Mongo — if none exists, the route returns
a controlled 503 instead of serving indeterminately-stale data. Opt-in (default False) so existing
callers' behaviour is unchanged; only routes with a real snapshot mechanism behind them should set it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# The three provenance values a caller can surface (e.g. in a response header / audit) so a DR
# fallback is never silent.
SERVED_POSTGRES = "postgres"
SERVED_MONGO = "mongo"
SERVED_MONGO_DR_FALLBACK = "mongo_dr_fallback"

DEFAULT_MAX_SNAPSHOT_AGE_MINUTES = 30


async def get_dr_snapshot(route_key: str, building_id: str) -> Optional[dict]:
    """Return the current `dr_snapshot_meta` document for (building_id, route_key), or None if
    none has ever been written. Exported so route handlers can stamp `as_of` onto a DR-served
    response without this module's core function needing to change its return signature.
    """
    from database import db

    return await db.dr_snapshot_meta.find_one(
        {"building_id": building_id, "route_key": route_key}, {"_id": 0}
    )


def _snapshot_is_fresh(snapshot: Optional[dict], max_age_minutes: int) -> bool:
    if not snapshot:
        return False
    if snapshot.get("reconciliation_status") != "ok":
        return False
    completed_at = snapshot.get("completed_at")
    if not isinstance(completed_at, datetime):
        return False
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    age = datetime.now(tz=timezone.utc) - completed_at
    return age <= timedelta(minutes=max_age_minutes)


async def read_pg_first_with_mongo_dr(
    *,
    route_key: str,
    building_id: str,
    source: str,
    pg_read: Callable[[], Awaitable[Any]],
    mongo_read: Callable[[], Awaitable[Any]],
    on_dr_fallback: Optional[Callable[[str, str, Exception | None], None]] = None,
    reraise: tuple[type[BaseException], ...] = (),
    require_fresh_snapshot: bool = False,
    max_snapshot_age_minutes: int = DEFAULT_MAX_SNAPSHOT_AGE_MINUTES,
) -> tuple[Any, str]:
    """Serve PG-primary with Mongo as a DR fallback ON FAILURE ONLY.

    Args:
      route_key / building_id: identity for logging + the telemetry hook.
      source: the resolver's decision — "postgres" to attempt PG, anything else serves Mongo.
      pg_read / mongo_read: zero-arg async thunks that produce the route's payload from each store.
      on_dr_fallback: optional hook (route_key, building_id, exc|None) invoked when DR kicks in.
        A raising hook is swallowed — telemetry must never break the fallback.
      reraise: exception types that must PROPAGATE from pg_read rather than trigger the DR
        fallback. A deliberate HTTP response (e.g. FastAPI HTTPException — a 403 auth denial or a
        4xx validation error) is not a Postgres *failure*; falling back to Mongo would mask it and
        serve data the caller was refused. Pass ``reraise=(HTTPException,)``. Default () preserves
        the "any exception is a DR event" behaviour. (An empty tuple in an ``except`` clause
        matches nothing, so the default is a true no-op.)
      require_fresh_snapshot: when True, a DR fallback additionally requires a completed,
        reconciled `dr_snapshot_meta` document (see `scripts/dr_mongo_snapshot.py`) within
        `max_snapshot_age_minutes` — raises HTTPException(503) instead of serving Mongo if none
        exists. Default False preserves prior behaviour for callers with no snapshot mechanism.

    Returns:
      (payload, served_source) where served_source is one of SERVED_POSTGRES / SERVED_MONGO /
      SERVED_MONGO_DR_FALLBACK. Use `get_dr_snapshot(route_key, building_id)` after a
      SERVED_MONGO_DR_FALLBACK result if you need to stamp `as_of` onto the response.
    """
    if source != SERVED_POSTGRES:
        return await mongo_read(), SERVED_MONGO

    # NB: `except Exception`, deliberately NOT `except BaseException`. A PG failure or timeout
    # (asyncio.TimeoutError/TimeoutError are Exception subclasses) must fall back; but
    # asyncio.CancelledError is a BaseException and MUST propagate — swallowing it to run a Mongo
    # fallback during a cancelled request/shutdown breaks cooperative cancellation. Do not "widen"
    # this to BaseException.
    exc: Exception | None = None
    try:
        result = await pg_read()
    except reraise:
        # Deliberate, caller-visible outcomes (HTTP 4xx auth/validation) are NOT DR events —
        # propagate unchanged rather than masking them behind a Mongo fallback. Listed before the
        # broad handler so it wins; with the default reraise=() this clause matches nothing.
        raise
    except Exception as e:  # noqa: BLE001 — DR: a PG failure must never take the route down.
        exc = e
        result = None
    else:
        # A non-None result — INCLUDING an empty dict / zeroed payload — is a valid PG answer.
        if result is not None:
            return result, SERVED_POSTGRES
        # result is None: the PG builder could not produce a payload at all (not the same as
        # "PG has zero data", which would be a populated-with-zeros payload). Treat as unavailable.

    reason = f"{type(exc).__name__}: {exc}" if exc is not None else "PG read returned None (no payload)"
    logger.warning(
        "PG read unavailable for route=%s building=%s; DR fallback to Mongo (%s)",
        route_key, building_id, reason,
    )
    if on_dr_fallback is not None:
        try:
            on_dr_fallback(route_key, building_id, exc)
        except Exception:  # noqa: BLE001 — a telemetry hook must never break the DR fallback.
            logger.exception("on_dr_fallback hook raised for route=%s", route_key)

    if require_fresh_snapshot:
        snapshot = await get_dr_snapshot(route_key, building_id)
        if not _snapshot_is_fresh(snapshot, max_snapshot_age_minutes):
            logger.error(
                "DR fallback refused for route=%s building=%s: no valid snapshot "
                "(status=%s, completed_at=%s) within %d minutes",
                route_key, building_id,
                (snapshot or {}).get("reconciliation_status"),
                (snapshot or {}).get("completed_at"),
                max_snapshot_age_minutes,
            )
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail=(
                    f"PostgreSQL unavailable for {route_key} and no fresh, reconciled DR "
                    "snapshot exists — refusing to serve stale data."
                ),
            )

    return await mongo_read(), SERVED_MONGO_DR_FALLBACK
