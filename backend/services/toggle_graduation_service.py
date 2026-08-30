# @featuretrace:cutover-toggle-safety — Live-state graduation for protected feature toggles.
# Layer: service
# Data flow: config_repo.create/update_global_feature_toggle -> graduated_protected_keys()
#            -> core.schemes (active, non-demo, non-test) + core.domain_cutover_status
#            -> assert_global_enable_allowed(graduated=...)
# Related: backend/core/toggle_classification.py       (classification + pure evaluator)
#          backend/services/cutover_status_service.py  (the control plane it reads)
#          scripts/audits/toggle_drift.py              (deploy gate)
#          scripts/audits/toggle_drift_autoheal.py     (the thing that used to revert blindly)
# Table: core.schemes, core.domain_cutover_status
# Tests: tests/backend/test_toggle_graduation_service.py
"""Decide, from live cutover state, which protected toggles may be enabled globally.

``core.toggle_classification`` says a toggle is dangerous to enable globally. It
could not say when that stops being true, because it is a pure module with no
database access — so the answer was permanently "never", and the deploy autoheal
reverted such a key on every run without checking whether the migration it guards
had finished.

This module supplies the missing evidence. A protected key GRADUATES when every
active, non-demo, non-test scheme is already authoritative in PostgreSQL — mode
``postgres_write`` or ``mongo_archive`` — for every domain that key routes.

Three deliberate properties:

* **Production buildings only.** Demo and test schemes do not vote. They are not
  unprotected by that: ``require_domain_source`` fails closed on a missing
  ``core.domain_cutover_status`` row, so a building with no cutover state keeps
  reading MongoDB regardless of the global default. The global default decides
  what a building gets *absent* its own state; the guard decides what it actually
  gets.
* **No buildings means no graduation.** An empty or unreadable control plane is
  absence of evidence, never evidence of promotion.
* **Fail closed.** Any exception answers "not graduated". A protected toggle stays
  protected when we cannot prove otherwise — the opposite bias to
  ``get_cutover_status``, which swallows errors into "not registered".
"""
from __future__ import annotations

import logging
from typing import Any

from core.toggle_classification import (
    GRADUATING_CUTOVER_MODES,
    PROTECTED_TOGGLE_KEYS,
    cutover_domains_for,
    evaluate_graduation,
    is_protected_toggle,
)

logger = logging.getLogger(__name__)


async def _promoted_domains_by_production_building() -> dict[str, set[str]]:
    """Map each active production building_id to its fully-promoted domains.

    Reads the control plane under the RLS bypass sentinel, which
    ``core.domain_cutover_status``'s policy honours explicitly.
    """
    from db_postgres.repos.identity_repo import list_all_active_schemes
    from services.cutover_status_service import _get_bypass_session_context

    from sqlalchemy import text

    schemes = await list_all_active_schemes()
    building_ids = [
        str(s["scheme_number"])
        for s in schemes
        if not s.get("is_demo") and s.get("scheme_number")
    ]
    if not building_ids:
        return {}

    promoted: dict[str, set[str]] = {bid: set() for bid in building_ids}
    async with _get_bypass_session_context() as session:
        rows = await session.execute(
            text(
                """
                SELECT building_id, domain, mode
                  FROM core.domain_cutover_status
                 WHERE building_id = ANY(:building_ids)
                   AND is_test_data = FALSE
                """
            ),
            {"building_ids": building_ids},
        )
        for building_id, domain, mode in rows.fetchall():
            if str(mode) in GRADUATING_CUTOVER_MODES:
                promoted[str(building_id)].add(str(domain))
    return promoted


async def graduated_protected_keys() -> frozenset[str]:
    """Every protected key that live cutover state now permits enabling globally."""
    try:
        promoted = await _promoted_domains_by_production_building()
    except Exception as exc:  # fail closed — see the module docstring
        logger.warning(
            "toggle_graduation: control-plane read failed (%s); "
            "treating every protected toggle as still protected",
            exc,
        )
        return frozenset()
    return frozenset(
        key for key in PROTECTED_TOGGLE_KEYS
        if evaluate_graduation(key, promoted)
    )


async def is_graduated(feature_key: str) -> bool:
    """True when this specific protected key may now be enabled globally."""
    if not is_protected_toggle(feature_key):
        return True
    if not cutover_domains_for(feature_key):
        return False
    return feature_key in await graduated_protected_keys()


async def graduation_report() -> dict[str, Any]:
    """Operator-facing breakdown: what graduated, what has not, and what is missing.

    Used by the deploy drift gate and the autoheal so their output names the
    evidence rather than just the verdict.
    """
    try:
        promoted = await _promoted_domains_by_production_building()
        error: str | None = None
    except Exception as exc:
        promoted, error = {}, repr(exc)

    graduated: list[str] = []
    blocked: dict[str, dict[str, list[str]]] = {}
    for key in sorted(PROTECTED_TOGGLE_KEYS):
        domains = cutover_domains_for(key)
        if evaluate_graduation(key, promoted):
            graduated.append(key)
            continue
        missing = {
            bid: sorted(set(domains) - promoted_domains)
            for bid, promoted_domains in promoted.items()
            if not set(domains).issubset(promoted_domains)
        }
        blocked[key] = {
            "requires_domains": list(domains) or ["<unmapped — never graduates>"],
            "missing_by_building": missing,
        }

    return {
        "error": error,
        "production_buildings": sorted(promoted),
        "promoted_domains_by_building": {b: sorted(d) for b, d in promoted.items()},
        "graduated": graduated,
        "blocked": blocked,
    }
