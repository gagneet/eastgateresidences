# @featuretrace:powerhouse-foundation — Read-only health snapshot for the P2A command foundation (outbox, idempotency, per-domain readiness).
# Layer: service
# Data flow: PowerhouseControlCentrePage -> GET /api/admin/cutover/powerhouse-command-foundation/{building_id} -> this service -> core.outbox / core.command_idempotency_records / core.domain_cutover_status (building-scoped).
# Related: backend/routers/cutover_admin.py
#          backend/services/powerhouse_command_foundation.py
#          backend/scripts/powerhouse_postgres_readiness.py
#          docs/features/powerhouse_p2a_deep_dive_audit_2026-07-20.md
# Table: core.outbox, core.command_idempotency_records, core.domain_cutover_status

"""Read-only health snapshot for the Powerhouse command foundation.

Mirrors the query logic in scripts/powerhouse_postgres_readiness.py section
[6] "OUTBOX, IDEMPOTENCY & CONTINUITY HEALTH", which was live-verified against
the real database on 2026-07-20 (see the P2A deep-dive audit doc). Exposed
here as an authenticated API so the Powerhouse Control Centre can surface it
without an operator running the CLI script by hand.

This is diagnostic visibility only. It does not promote or mutate anything —
per Finding 6 of the P2A audit, no Powerhouse domain has an operational path
to leave mongo_primary today, and this module does not add one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from services.cutover_status_service import get_or_default_cutover_status

# The four Powerhouse domains registered by seed_powerhouse_control_plane_statuses().
# Kept in sync with scripts/powerhouse_postgres_readiness.py section [4].
POWERHOUSE_DOMAINS = [
    "powerhouse_conversations",
    "powerhouse_inbox",
    "powerhouse_workflows",
    "powerhouse_automation",
]


async def get_powerhouse_command_foundation_health(building_id: str) -> dict[str, Any]:
    """Return outbox/idempotency health and per-domain cutover readiness for a building.

    Returns ``scheme_found: False`` with ``outbox``/``idempotency`` as ``None``
    if this building has no Postgres scheme context yet (e.g. a not-yet-onboarded
    demo building), rather than raising — this endpoint is diagnostic, not
    a hard dependency for any write path.
    """
    domains: list[dict[str, Any]] = []
    for domain in POWERHOUSE_DOMAINS:
        status = await get_or_default_cutover_status(building_id, domain)
        domains.append(
            {
                "domain": domain,
                "mode": status.mode.value,
                "readiness_status": status.readiness_status.value,
            }
        )

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        return {
            "building_id": building_id,
            "scheme_found": False,
            "domains": domains,
            "outbox": None,
            "idempotency": None,
        }

    tenant_id = str(scheme["tenant_id"])
    scheme_id = str(scheme["scheme_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        outbox_row = (
            await session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE published_at IS NULL AND dead_lettered_at IS NULL) AS pending,
                        MIN(created_at) FILTER (WHERE published_at IS NULL AND dead_lettered_at IS NULL) AS oldest_pending,
                        COUNT(*) FILTER (WHERE dead_lettered_at IS NOT NULL) AS dead_lettered
                    FROM core.outbox
                    WHERE scheme_id = CAST(:scheme_id AS UUID)
                    """
                ),
                {"scheme_id": scheme_id},
            )
        ).fetchone()

        idempotency_incomplete = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS n
                    FROM core.command_idempotency_records
                    WHERE tenant_id = CAST(:tenant_id AS UUID)
                      AND completed_at IS NULL
                    """
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar()

    return {
        "building_id": building_id,
        "scheme_found": True,
        "domains": domains,
        "outbox": {
            "pending": outbox_row.pending if outbox_row else 0,
            "oldest_pending": (
                outbox_row.oldest_pending.isoformat()
                if outbox_row and outbox_row.oldest_pending
                else None
            ),
            "dead_lettered": outbox_row.dead_lettered if outbox_row else 0,
        },
        "idempotency": {
            "incomplete_records": idempotency_incomplete or 0,
        },
    }
