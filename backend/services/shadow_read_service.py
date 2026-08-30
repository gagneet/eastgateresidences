# @featuretrace:pg-migration-shadow — Generic PostgreSQL shadow-read comparison engine, domain-agnostic.
# Layer: service
# Data flow: domain shadow-hook (identity/trust/finance) → schedule_shadow_compare() → run_shadow_compare()
#            → core.shadow_diffs + core.shadow_read_coverage_daily (building-scoped). MongoDB response is
#            returned unchanged; PostgreSQL values are comparison-only.
# Related: backend/services/finance_shadow_read_service.py
#          backend/services/cutover_status_service.py
#          backend/alembic/versions/0065_shadow_read_coverage_daily.py
#          docs/migration/shadow-correction-updates-plan-p1.md
#          docs/migration/shadow-correction-updates-plan-p2.md
#          docs/migration/shadow-correction-updates-plan-p3.md
"""Generic, domain-agnostic PostgreSQL shadow-read comparison engine.

Extracted from finance_shadow_read_service.py (2026-07-14) so identity/ownership and trust
domains don't each hand-roll their own comparator/persistence/scheduling machinery.

Rules (unchanged from the finance-only version, now enforced for every domain):
  - MongoDB is always the response source during shadow mode. PostgreSQL is comparison-only.
  - Money comparisons use integer cents. Never compare formatted strings.
  - Comparison failures must never raise user-facing errors, and never block the primary
    response — every entry point here is designed to be called from a background task,
    never awaited inline before returning a route's response.
  - Diff records are building-scoped and never contain sensitive owner PII (see the
    redaction contract on FieldDiff below) — PII-bearing domains hash or boolean-match
    instead of storing raw values.
  - Comparator ownership stays domain-owned: this module owns scheduling, persistence,
    the coverage/redaction contract, and generic field-comparison primitives. Each domain
    module (finance_shadow_read_service.py, identity_shadow_read_service.py,
    trust_shadow_read_service.py) owns its own route_key -> comparator dict, payload
    normalization, and tolerances.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import text

from services.cutover_status_service import (
    _get_bypass_session_context,
    canonical_domain,
    record_shadow_diff,
)

logger = logging.getLogger(__name__)

_DEFAULT_TOLERANCE_CENTS = 0  # strict by default

# Operational-day timezone for coverage_date, per building. Only East Gate (13195) is
# in scope for this rollout; a per-building lookup can replace this constant later
# without changing any caller — deliberately not building a full settings-driven
# timezone resolver for a single-building rollout.
_DEFAULT_COVERAGE_TZ = ZoneInfo("Australia/Sydney")


def _coverage_date_for(building_id: str, when: datetime | None = None) -> date:
    """Operational day for coverage bucketing, in the building's local timezone.

    Event timestamps themselves stay UTC (created_at on shadow_diffs/coverage rows) —
    only the *bucketing* date uses local time, so traffic near midnight doesn't split
    across two reporting days.
    """
    moment = when or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_DEFAULT_COVERAGE_TZ).date()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FieldDiff:
    """One divergent field comparison.

    PII-bearing domains must not put raw email/name/phone into mongo_value/pg_value —
    hash or boolean-match instead (enforced by the domain module's comparator, not
    here; this module has no way to know which fields are sensitive for a given
    domain).
    """

    field_path: str
    mongo_value: Any
    pg_value: Any
    tolerance_cents: int
    severity: str  # "info" | "warn" | "critical"
    diff_cents: int | None = None


@dataclass
class ShadowCompareResult:
    """Result of a single shadow comparison run."""

    building_id: str
    route_key: str
    matched: bool
    domain: str = ""
    diffs: list[FieldDiff] = field(default_factory=list)
    pg_available: bool = True
    error: str | None = None
    compared_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def severity(self) -> str:
        """Generated function header.

        Function: ShadowCompareResult.severity
        Path: backend/services/shadow_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.error:
            return "error"
        if not self.diffs:
            return "pass"
        # sev_order is most-severe-first; min picks the lowest index = most severe.
        sev_order = ["critical", "warn", "info"]
        worst = min(self.diffs, key=lambda d: sev_order.index(d.severity) if d.severity in sev_order else 99)
        return worst.severity


# ---------------------------------------------------------------------------
# Field comparison primitives (domain-agnostic)
# ---------------------------------------------------------------------------

def compare_money_fields(
        *,
        field_path: str,
        mongo_aud: float | int | None,
        pg_cents: int | None,
        tolerance_cents: int = _DEFAULT_TOLERANCE_CENTS,
) -> FieldDiff | None:
    """Compare a Mongo AUD float vs PG integer cents. None if within tolerance."""
    mongo_cents = int(round((mongo_aud or 0) * 100))
    pg = int(pg_cents or 0)
    diff_abs = abs(mongo_cents - pg)

    if diff_abs <= tolerance_cents:
        return None

    severity = "critical" if diff_abs > 100 else "warn"
    return FieldDiff(
        field_path=field_path,
        mongo_value=mongo_cents,
        pg_value=pg,
        tolerance_cents=tolerance_cents,
        severity=severity,
        diff_cents=mongo_cents - pg,
    )


def compare_count_fields(
        *,
        field_path: str,
        mongo_count: int | None,
        pg_count: int | None,
) -> FieldDiff | None:
    """Compare integer count fields. None if equal."""
    m = int(mongo_count or 0)
    p = int(pg_count or 0)
    if m == p:
        return None
    return FieldDiff(
        field_path=field_path,
        mongo_value=m,
        pg_value=p,
        tolerance_cents=0,
        severity="warn",
    )


def compare_status_fields(
        *,
        field_path: str,
        mongo_status: str | None,
        pg_status: str | None,
) -> FieldDiff | None:
    """Compare status string fields (case-insensitive). None if equivalent."""
    m = (mongo_status or "").lower().strip()
    p = (pg_status or "").lower().strip()
    if m == p:
        return None
    return FieldDiff(
        field_path=field_path,
        mongo_value=m,
        pg_value=p,
        tolerance_cents=0,
        severity="info",
    )


def compare_list_lengths(
        *,
        field_path: str,
        mongo_list: list | None,
        pg_list: list | None,
) -> FieldDiff | None:
    """Compare list lengths. None if equal."""
    return compare_count_fields(
        field_path=field_path,
        mongo_count=len(mongo_list) if mongo_list else 0,
        pg_count=len(pg_list) if pg_list else 0,
    )


def compare_boolean_match_fields(
        *,
        field_path: str,
        mongo_value: Any,
        pg_value: Any,
) -> FieldDiff | None:
    """Redaction-safe primitive for PII-bearing fields: compare truthiness/equality of a
    *hashed or boolean* representation the caller has already computed, never the raw
    value. None if the two representations match. Intended use — identity/ownership
    comparators hash email/name/phone before calling this, never pass raw PII here.
    """
    if mongo_value == pg_value:
        return None
    return FieldDiff(
        field_path=field_path,
        mongo_value=mongo_value,
        pg_value=pg_value,
        tolerance_cents=0,
        severity="warn",
    )


# ---------------------------------------------------------------------------
# Core shadow compare orchestrator (generic — domain modules supply the comparator)
# ---------------------------------------------------------------------------

async def run_shadow_compare(
        *,
        building_id: str,
        domain: str,
        route_key: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any] | None,
        comparator: Callable[..., list[FieldDiff]],
        tolerance_cents: int = _DEFAULT_TOLERANCE_CENTS,
        is_test_data: bool = False,
) -> ShadowCompareResult:
    """Compare Mongo and PG payloads for one route, using the caller-supplied comparator.

    Records diffs to core.shadow_diffs and increments core.shadow_read_coverage_daily.
    Never raises — failures are caught and returned in result.error so callers can
    always proceed with the original MongoDB response unchanged.
    """
    domain = canonical_domain(domain)

    if pg_payload is None:
        result = ShadowCompareResult(
            building_id=building_id, domain=domain, route_key=route_key,
            matched=False, pg_available=False, error="pg_payload_unavailable",
        )
        await _safe_record_diff(
            building_id=building_id, domain=domain, route_key=route_key,
            diff_type="pg_unavailable", mongo_value=None, pg_value=None,
            divergence_score=0.5, is_test_data=is_test_data,
        )
        await _safe_record_coverage(
            building_id=building_id, domain=domain, route=route_key,
            pg_unavailable=1, is_test_data=is_test_data,
        )
        return result

    try:
        diffs = comparator(
            mongo_payload=mongo_payload,
            pg_payload=pg_payload,
            tolerance_cents=tolerance_cents,
        )
    except Exception as exc:
        logger.error("shadow_read: compare failed for %s/%s/%s: %s", building_id, domain, route_key, exc)
        await _safe_record_coverage(
            building_id=building_id, domain=domain, route=route_key,
            compare_error=1, is_test_data=is_test_data,
        )
        return ShadowCompareResult(
            building_id=building_id, domain=domain, route_key=route_key,
            matched=False, error=f"compare_error: {exc}",
        )

    matched = len(diffs) == 0
    result = ShadowCompareResult(
        building_id=building_id, domain=domain, route_key=route_key,
        matched=matched, diffs=diffs,
    )

    if diffs:
        worst_severity = result.severity
        divergence_score = {"critical": 1.0, "warn": 0.6, "info": 0.2}.get(worst_severity, 0.5)
        diff_summary = {
            f.field_path: {"mongo": f.mongo_value, "pg": f.pg_value, "diff_cents": f.diff_cents}
            for f in diffs
        }
        await _safe_record_diff(
            building_id=building_id, domain=domain, route_key=route_key,
            diff_type=f"field_mismatch:{worst_severity}",
            mongo_value={"fields": diff_summary}, pg_value=None,
            divergence_score=divergence_score, is_test_data=is_test_data,
        )
        await _safe_record_coverage(
            building_id=building_id, domain=domain, route=route_key,
            mismatch=1, is_test_data=is_test_data,
        )
    else:
        if await _should_record_shadow_ok(building_id=building_id, domain=domain, route_key=route_key):
            await _safe_record_diff(
                building_id=building_id, domain=domain, route_key=route_key,
                diff_type="shadow_ok", mongo_value={"matched": True}, pg_value=None,
                divergence_score=0.0, is_test_data=is_test_data,
            )
        await _safe_record_coverage(
            building_id=building_id, domain=domain, route=route_key,
            matched=1, is_test_data=is_test_data,
        )

    return result


# ---------------------------------------------------------------------------
# Safe, bounded background-task scheduler
# ---------------------------------------------------------------------------
# Lazy per-domain semaphore registry, not module-level asyncio.Semaphore() — creating
# asyncio primitives at import time can bind to the wrong event loop or complicate
# tests (docs/migration/shadow-correction-updates-plan-p3.md).

_DEFAULT_DOMAIN_CONCURRENCY = 5
_DOMAIN_CONCURRENCY_LIMITS: dict[str, int] = {
    # Override per domain here if a route needs a different bound than the default.
}
_domain_semaphores: dict[str, asyncio.Semaphore] = {}


def get_domain_concurrency_limit(domain: str) -> int:
    """Generated function header.

    Function: get_domain_concurrency_limit
    Path: backend/services/shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _DOMAIN_CONCURRENCY_LIMITS.get(canonical_domain(domain), _DEFAULT_DOMAIN_CONCURRENCY)


def _get_shadow_semaphore(domain: str) -> asyncio.Semaphore:
    """Generated function header.

    Function: _get_shadow_semaphore
    Path: backend/services/shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    domain = canonical_domain(domain)
    sem = _domain_semaphores.get(domain)
    if sem is None:
        sem = asyncio.Semaphore(get_domain_concurrency_limit(domain))
        _domain_semaphores[domain] = sem
    return sem


def schedule_shadow_compare(
        coro: Awaitable[Any],
        *,
        domain: str,
        context: dict[str, Any] | None = None,
) -> asyncio.Task | None:
    """Fire-and-forget a shadow comparison coroutine, bounded and exception-safe.

    A request must NEVER wait on this — call it, don't await it, then return the
    Mongo response immediately. If the domain's concurrency limit is already
    exhausted, the comparison is skipped (not queued) and counted as
    skipped_capacity, never silently dropped without a trace.

    Returns the created Task (or None if skipped for capacity) — callers don't
    need to hold onto it; the done-callback here handles logging so exceptions
    the coroutine's own try/except didn't catch are never lost.
    """
    domain = canonical_domain(domain)
    sem = _get_shadow_semaphore(domain)
    ctx = context or {}

    async def _skip_for_capacity() -> None:
        # The caller's coroutine is never going to run — close it explicitly so
        # Python doesn't warn "coroutine was never awaited" (a plain coroutine
        # object, unlike a Task, is never scheduled just by being dropped).
        """Generated function header.

        Function: _skip_for_capacity
        Path: backend/services/shadow_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        route = ctx.get("route") or ctx.get("route_key")
        building_id = ctx.get("building_id")
        if building_id and route:
            await _safe_record_coverage(
                building_id=building_id, domain=domain, route=route,
                skipped_capacity=1, is_test_data=bool(ctx.get("is_test_data")),
            )
        logger.warning("shadow_compare_skipped_capacity domain=%s context=%s", domain, ctx)

    # sem.locked() means the concurrency limit is already fully claimed — skip and
    # record it rather than queuing behind other comparisons. A request must never
    # wait for a shadow semaphore before returning its Mongo response, and this
    # scheduler is only ever invoked fire-and-forget, so "wait" isn't an option here
    # anyway; skipping is the correct behavior, not a fallback for one.
    if sem.locked():
        return asyncio.create_task(_skip_for_capacity())

    async def _bounded() -> None:
        """Generated function header.

        Function: _bounded
        Path: backend/services/shadow_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            async with sem:
                await coro
        except Exception:
            logger.exception("shadow_compare_failed domain=%s context=%s", domain, ctx)

    task = asyncio.create_task(_bounded())

    def _done(completed: asyncio.Task) -> None:
        """Generated function header.

        Function: _done
        Path: backend/services/shadow_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if completed.cancelled():
            return
        exc = completed.exception()
        if exc is not None:
            logger.exception("shadow_compare_task_failed domain=%s context=%s", domain, ctx, exc_info=exc)

    task.add_done_callback(_done)
    return task


# ---------------------------------------------------------------------------
# Coverage counter (atomic upsert — never read-then-increment-then-write)
# ---------------------------------------------------------------------------

async def _safe_record_coverage(
        *,
        building_id: str,
        domain: str,
        route: str,
        attempted: int = 1,
        matched: int = 0,
        mismatch: int = 0,
        pg_unavailable: int = 0,
        compare_error: int = 0,
        timeout: int = 0,
        skipped_capacity: int = 0,
        is_test_data: bool = False,
) -> None:
    """Best-effort coverage increment. Never raises — a telemetry write failure must
    never affect the primary Mongo response path (this is only ever called from a
    background comparison task, never inline in a request handler).
    """
    domain = canonical_domain(domain)
    coverage_date = _coverage_date_for(building_id)
    try:
        async with _get_bypass_session_context() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO core.shadow_read_coverage_daily (
                        id, tenant_id, building_id, domain, route, coverage_date,
                        attempted_count, matched_count, mismatch_count,
                        pg_unavailable_count, compare_error_count, timeout_count,
                        skipped_capacity_count, is_test_data, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), CAST(:tenant_id AS UUID), :building_id, :domain, :route, :coverage_date,
                        :attempted, :matched, :mismatch, :pg_unavailable, :compare_error, :timeout,
                        :skipped_capacity, :is_test_data, NOW(), NOW()
                    )
                    ON CONFLICT (building_id, domain, route, coverage_date) DO UPDATE SET
                        attempted_count = core.shadow_read_coverage_daily.attempted_count + EXCLUDED.attempted_count,
                        matched_count = core.shadow_read_coverage_daily.matched_count + EXCLUDED.matched_count,
                        mismatch_count = core.shadow_read_coverage_daily.mismatch_count + EXCLUDED.mismatch_count,
                        pg_unavailable_count = core.shadow_read_coverage_daily.pg_unavailable_count + EXCLUDED.pg_unavailable_count,
                        compare_error_count = core.shadow_read_coverage_daily.compare_error_count + EXCLUDED.compare_error_count,
                        timeout_count = core.shadow_read_coverage_daily.timeout_count + EXCLUDED.timeout_count,
                        skipped_capacity_count = core.shadow_read_coverage_daily.skipped_capacity_count + EXCLUDED.skipped_capacity_count,
                        updated_at = NOW()
                    """
                ),
                {
                    "tenant_id": "00000000-0000-0000-0000-000000000000",
                    "building_id": building_id,
                    "domain": domain,
                    "route": route,
                    "coverage_date": coverage_date,
                    "attempted": attempted,
                    "matched": matched,
                    "mismatch": mismatch,
                    "pg_unavailable": pg_unavailable,
                    "compare_error": compare_error,
                    "timeout": timeout,
                    "skipped_capacity": skipped_capacity,
                    "is_test_data": is_test_data,
                },
            )
    except Exception as exc:
        logger.error("shadow_read: _safe_record_coverage failed: %s", exc)


# ---------------------------------------------------------------------------
# Readiness / clean-day classification
# ---------------------------------------------------------------------------

_MIN_ATTEMPTS_FOR_COVERAGE = 3  # below this, a day is "insufficient_coverage" not "clean"


async def classify_route_day(
        *,
        building_id: str,
        domain: str,
        route: str,
        coverage_date: date,
) -> str:
    """Classify one (building, domain, route, day) using the coverage table.

    Returns one of: no_traffic | insufficient_coverage | infrastructure_failure |
    dirty | clean. Only "clean" should ever advance a consecutive-day streak.
    """
    domain = canonical_domain(domain)
    try:
        async with _get_bypass_session_context() as session:
            result = await session.execute(
                text(
                    """
                    SELECT attempted_count, matched_count, mismatch_count,
                           pg_unavailable_count, compare_error_count, timeout_count
                    FROM core.shadow_read_coverage_daily
                    WHERE building_id = :building_id AND domain = :domain
                      AND route = :route AND coverage_date = :coverage_date
                      AND is_test_data = FALSE
                    """
                ),
                {
                    "building_id": building_id, "domain": domain,
                    "route": route, "coverage_date": coverage_date,
                },
            )
            row = result.fetchone()
    except Exception as exc:
        logger.warning("classify_route_day: query failed (%s)", exc)
        return "infrastructure_failure"

    if row is None or int(row.attempted_count or 0) == 0:
        return "no_traffic"
    if int(row.attempted_count) < _MIN_ATTEMPTS_FOR_COVERAGE:
        return "insufficient_coverage"
    if int(row.pg_unavailable_count or 0) > 0 or int(row.compare_error_count or 0) > 0 or int(row.timeout_count or 0) > 0:
        return "infrastructure_failure"
    if int(row.mismatch_count or 0) > 0:
        return "dirty"
    return "clean"


async def get_route_shadow_readiness(
        *,
        building_id: str,
        domain: str,
        route_key: str,
        lookback_hours: int = 24,
) -> dict[str, Any]:
    """Return shadow readiness for one route, sourced from core.shadow_diffs (unresolved
    critical mismatches) — mirrors finance_shadow_read_service.py's original function,
    generalized by domain. Use classify_route_day() separately for clean-day streak logic.
    """
    domain = canonical_domain(domain)
    try:
        async with _get_bypass_session_context() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        COUNT(CASE WHEN diff_type != 'shadow_ok' THEN 1 END) AS total_diffs,
                        COUNT(CASE WHEN diff_type = 'shadow_ok' THEN 1 END) AS pass_samples,
                        COUNT(CASE WHEN diff_type LIKE :crit_pattern THEN 1 END) AS critical_count,
                        MAX(created_at) AS last_at
                    FROM core.shadow_diffs
                    WHERE building_id = :building_id
                      AND domain = :domain
                      AND route = :route
                      AND is_test_data = FALSE
                      AND created_at > NOW() - INTERVAL '1 hour' * :hours
                    """
                ),
                {
                    "building_id": building_id, "domain": domain, "route": route_key,
                    "hours": lookback_hours, "crit_pattern": "%:critical%",
                },
            )
            row = result.fetchone()
    except Exception as exc:
        logger.warning("get_route_shadow_readiness: DB query failed: %s", exc)
        return {"status": "not_started", "diff_count": 0, "critical_count": 0, "last_compared_at": None, "error": str(exc)}

    total = int((row.total_diffs if row is not None else 0) or 0)
    pass_samples = int((row.pass_samples if row is not None else 0) or 0)
    critical = int((row.critical_count if row is not None else 0) or 0)
    last_at = row.last_at.isoformat() if row and row.last_at else None

    if row is None or (total == 0 and pass_samples == 0 and last_at is None):
        return {"status": "not_started", "diff_count": 0, "critical_count": 0, "last_compared_at": None}

    if critical > 0:
        status = "shadow_fail"
    elif pass_samples > 0 and total == 0:
        status = "shadow_pass"
    elif total > 0:
        status = "shadow_warn"
    else:
        status = "shadow_running" if pass_samples > 0 else "not_started"

    return {"status": status, "diff_count": total, "critical_count": critical, "last_compared_at": last_at, "pass_samples": pass_samples}


async def get_building_domain_shadow_readiness(
        *, building_id: str, domain: str, route_keys: list[str],
) -> dict[str, Any]:
    """Return shadow readiness summary across a domain's contract routes for a building."""
    domain = canonical_domain(domain)
    readiness_tasks = [
        get_route_shadow_readiness(building_id=building_id, domain=domain, route_key=r)
        for r in route_keys
    ]
    results = await asyncio.gather(*readiness_tasks, return_exceptions=True)

    per_route: dict[str, Any] = {}
    overall_critical = 0
    overall_diffs = 0
    started = 0

    for route_key, res in zip(route_keys, results):
        if isinstance(res, Exception):
            per_route[route_key] = {"status": "error", "error": str(res)}
        else:
            per_route[route_key] = res
            if res.get("status") != "not_started":
                started += 1
                overall_diffs += res.get("diff_count", 0)
                overall_critical += res.get("critical_count", 0)

    if started == 0:
        overall_status = "not_started"
    elif overall_critical > 0:
        overall_status = "shadow_fail"
    elif overall_diffs > 0:
        overall_status = "shadow_warn"
    else:
        overall_status = "shadow_pass"

    return {
        "building_id": building_id, "domain": domain, "overall_status": overall_status,
        "routes_started": started, "total_diff_count": overall_diffs,
        "total_critical_count": overall_critical, "per_route": per_route,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _safe_record_diff(
        *,
        building_id: str,
        domain: str,
        route_key: str,
        diff_type: str,
        mongo_value: dict[str, Any] | None,
        pg_value: dict[str, Any] | None,
        divergence_score: float,
        is_test_data: bool,
) -> None:
    """Record a shadow diff without raising. record_shadow_diff() canonicalizes domain."""
    try:
        await record_shadow_diff(
            building_id=building_id, domain=domain, route=route_key, diff_type=diff_type,
            mongo_value=mongo_value, pg_value=pg_value, divergence_score=divergence_score,
            is_test_data=is_test_data,
        )
    except Exception as exc:
        logger.error("shadow_read: record_shadow_diff failed: %s", exc)


async def _should_record_shadow_ok(*, building_id: str, domain: str, route_key: str) -> bool:
    """Bound storage: persist at most one shadow_ok sample per 30 minutes per route."""
    domain = canonical_domain(domain)
    try:
        async with _get_bypass_session_context() as session:
            result = await session.execute(
                text(
                    """
                    SELECT 1 FROM core.shadow_diffs
                    WHERE building_id = :building_id AND domain = :domain AND route = :route
                      AND diff_type = 'shadow_ok' AND is_test_data = FALSE
                      AND created_at > NOW() - INTERVAL '30 minutes'
                    LIMIT 1
                    """
                ),
                {"building_id": building_id, "domain": domain, "route": route_key},
            )
            return result.fetchone() is None
    except Exception:
        return False


def summarize_shadow_result(result: ShadowCompareResult) -> dict[str, Any]:
    """Serialise a ShadowCompareResult to a plain dict (for logging/responses)."""
    return {
        "building_id": result.building_id,
        "domain": result.domain,
        "route_key": result.route_key,
        "matched": result.matched,
        "severity": result.severity,
        "diff_count": len(result.diffs),
        "pg_available": result.pg_available,
        "error": result.error,
        "compared_at": result.compared_at.isoformat(),
        "diffs": [
            {
                "field_path": d.field_path,
                "mongo_value": d.mongo_value,
                "pg_value": d.pg_value,
                "diff_cents": d.diff_cents,
                "severity": d.severity,
            }
            for d in result.diffs
        ],
    }
