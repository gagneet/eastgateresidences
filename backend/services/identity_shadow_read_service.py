# @featuretrace:pg-migration-shadow — Identity composition instrumentation, identity_core domain.
# Layer: service
# Data flow: server.py's GET /users → record_user_list_composition() → core.shadow_diffs
#            (diff_type=composition_stats) + core.shadow_read_coverage_daily (building-scoped).
#            Never affects the route's response.
# Related: backend/services/shadow_read_service.py
#          backend/server.py (GET /users — the live composite-read handler)
#          docs/migration/phase-d-choices-mongodb-postgres.md
#          tasks/GAP-IDENTITY-USERS-LIST-001.md
"""Identity composition instrumentation — Phase D of the PostgreSQL shadow-read rollout.

`GET /users` (server.py) is not a Mongo-primary/Postgres-shadow pair — it's a deliberate
Mongo + Postgres UNION (Mongo supplies the base set, Postgres contributes users not yet
backfilled into Mongo). Forcing a field-level Mongo-vs-PG diff onto a route whose two
sources are *intentionally* different sets would generate meaningless "mismatch" noise on
every request. Per docs/migration/phase-d-choices-mongodb-postgres.md's analysis: this
route gets composition/coverage instrumentation (how many from each source, how much
overlap, did PG fail) — not field-parity diffs.

Redaction: only counts and internal (non-secret) user IDs may be recorded — never names,
emails, or phone numbers. `identity_conflict_user_ids` intentionally stays out of the
persisted record for the same reason; conflict *count* is enough for telemetry, root-causing
a specific conflict is a manual, ad hoc investigation, not something to store per-request.

Route strategy classification (Phase D5, code-config for now, not yet a control-plane
table per the analysis doc's staged recommendation):
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from services.shadow_read_service import _safe_record_coverage
from services.cutover_status_service import record_shadow_diff

logger = logging.getLogger(__name__)

_DOMAIN = "identity_core"


class ReadStrategy(str, Enum):
    MONGO_ONLY = "mongo_only"
    POSTGRES_ONLY = "postgres_only"
    MONGO_PRIMARY_PG_SHADOW = "mongo_primary_pg_shadow"
    POSTGRES_PRIMARY_MONGO_FALLBACK = "postgres_primary_mongo_fallback"
    MONGO_PG_UNION = "mongo_pg_union"


# Route strategy classification — code configuration for now (Phase D5's staged
# recommendation: "Initially, this can be code configuration plus a document. Later it can
# move into a control-plane table"). Not all of these routes are wired into this rollout
# yet; this documents each route's *actual* live shape so a future reader doesn't have to
# rediscover it the way this session did.
ROUTE_READ_STRATEGY: dict[str, ReadStrategy] = {
    "identity.users.list": ReadStrategy.MONGO_PG_UNION,
    "identity.auth.login": ReadStrategy.POSTGRES_PRIMARY_MONGO_FALLBACK,  # live since 2026-05-01, unmanaged — see GAP-IDENTITY-LOGIN-001
    # The 4 owner_service.py entry points — keep in lockstep with
    # owner_service._OWNER_METHOD_ROUTE_KEYS and phase_d_shadow_status's
    # DOMAIN_CONTRACT_ROUTES["identity_core"].
    "ownership.owner.current": ReadStrategy.MONGO_PRIMARY_PG_SHADOW,          # get_owner_info
    "ownership.owner.all_units": ReadStrategy.MONGO_PRIMARY_PG_SHADOW,        # get_all_unit_owners
    "ownership.owner.agm_voters": ReadStrategy.MONGO_PRIMARY_PG_SHADOW,       # resolve_agm_voters
    "ownership.owner.membership_check": ReadStrategy.MONGO_PRIMARY_PG_SHADOW, # is_user_current_owner_of_unit
}


@dataclass
class CompositionStats:
    """Diagnostics for one mongo_pg_union route invocation. Counts only — no PII."""

    mongo_count: int
    pg_count: int
    pg_available: bool
    overlap_count: int = 0
    pg_only_count: int = 0
    mongo_only_count: int = 0
    merged_count: int = 0
    merge_conflict_count: int = 0
    latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: CompositionStats.as_dict
        Path: backend/services/identity_shadow_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "mongo_count": self.mongo_count,
            "pg_count": self.pg_count,
            "pg_available": self.pg_available,
            "overlap_count": self.overlap_count,
            "pg_only_count": self.pg_only_count,
            "mongo_only_count": self.mongo_only_count,
            "merged_count": self.merged_count,
            "merge_conflict_count": self.merge_conflict_count,
            "latency_ms": self.latency_ms,
        }


def compute_composition_stats(
        *,
        mongo_ids: set[str],
        pg_ids: set[str],
        merged_count: int,
        pg_available: bool,
        latency_ms: float | None = None,
        merge_conflict_count: int = 0,
) -> CompositionStats:
    """Pure function: derive composition counts from two ID sets. No I/O, no PII in, no PII out."""
    overlap = mongo_ids & pg_ids
    return CompositionStats(
        mongo_count=len(mongo_ids),
        pg_count=len(pg_ids),
        pg_available=pg_available,
        overlap_count=len(overlap),
        pg_only_count=len(pg_ids - mongo_ids),
        mongo_only_count=len(mongo_ids - pg_ids),
        merged_count=merged_count,
        merge_conflict_count=merge_conflict_count,
        latency_ms=latency_ms,
    )


async def record_user_list_composition(
        *,
        building_id: str,
        stats: CompositionStats,
        is_test_data: bool = False,
) -> None:
    """Persist composition diagnostics for identity.users.list. Best-effort — never raises,
    never affects the route's response (call this from a background task only, same
    contract as every other shadow-read entry point in this rollout).
    """
    route_key = "identity.users.list"
    try:
        await record_shadow_diff(
            building_id=building_id,
            domain=_DOMAIN,
            route=route_key,
            diff_type="composition_stats",
            mongo_value=stats.as_dict(),
            pg_value=None,
            # Not a correctness "divergence" — 0.0 unless PG was unavailable (which is a
            # real degradation: incomplete results for anyone who should have appeared
            # via the PG-only branch).
            divergence_score=0.0 if stats.pg_available else 0.5,
            is_test_data=is_test_data,
        )
    except Exception as exc:
        logger.error("identity_shadow_read: record_user_list_composition failed: %s", exc)

    await _safe_record_coverage(
        building_id=building_id,
        domain=_DOMAIN,
        route=route_key,
        attempted=1,
        matched=1 if stats.pg_available else 0,
        pg_unavailable=0 if stats.pg_available else 1,
        is_test_data=is_test_data,
    )
