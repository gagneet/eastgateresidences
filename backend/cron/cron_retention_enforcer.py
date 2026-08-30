"""
Data Retention Enforcer — Privacy Act 1988 (Cth) / ACT UTMA 2011

Scans collections for records that have exceeded their 7-year statutory
retention period and flags them for manual review.

Currently in READ-ONLY / FLAG mode — no records are deleted.  A separate
manual destruction process (with committee sign-off) is required before any
record can be purged, consistent with the ACT UTMA 2011 s.89 7-year minimum
and the POLICY: never hard-delete real records without explicit authorisation.

Schedule suggestion (external systemd timer / PM2):
    0 2 * * 0   # 02:00 every Sunday

Run manually:
    cd backend && python3 cron/cron_retention_enforcer.py
    cd backend && python3 cron/cron_retention_enforcer.py --building-id 13195
"""
from __future__ import annotations

import asyncio
import argparse
import logging
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

# Allow direct execution from backend/cron/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_RETENTION_YEARS = 7  # ACT UTMA 2011 s.89 minimum


def _cutoff_dt(years: int = _RETENTION_YEARS) -> datetime:
    """Return a timezone-aware UTC datetime exactly `years` ago."""
    return datetime.now(timezone.utc) - timedelta(days=years * 365)


def _cutoff_iso(years: int = _RETENTION_YEARS) -> str:
    """Generated function header.

    Function: _cutoff_iso
    Path: backend/cron/cron_retention_enforcer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _cutoff_dt(years).isoformat()


async def run_retention_check(building_id: Optional[str] = None) -> dict:
    """
    Scan retention-sensitive collections for records older than 7 years.

    Parameters
    ----------
    building_id:
        When provided, scope the check to a single building.
        When None, check ALL buildings (global sweep).

    Returns
    -------
    dict with keys:
        checked_collections  — list of collection names examined
        flagged_for_review   — list of dicts describing what was found
        checked_at           — ISO-8601 timestamp of this run
    """
    from database import db  # late import — Motor client not available at module level

    # Use raw underlying database — cron runs outside HTTP request context so
    # TenantScopedDatabase._inject_bid() would raise RuntimeError (no ctx building_id).
    # We explicitly include building_id in the filter when scoped, or omit it for a
    # cross-building global sweep.
    raw_db = db._db

    cutoff = _cutoff_iso()
    building_filter: dict = {}
    if building_id:
        building_filter = {"building_id": building_id}

    flagged: list[dict] = []
    checked: list[str] = []

    async def _check(collection_name: str, legal_basis: str, action_required: str) -> None:
        """Generated function header.

        Function: _check
        Path: backend/cron/cron_retention_enforcer.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        checked.append(collection_name)
        collection = getattr(raw_db, collection_name)
        count = await collection.count_documents({**building_filter, "created_at": {"$lt": cutoff}})
        if count > 0:
            logger.warning(
                "RETENTION FLAG | %s | %d records older than %d years | building_id=%s | cutoff=%s",
                collection_name, count, _RETENTION_YEARS, building_id or "ALL", cutoff,
            )
            flagged.append({
                "collection": collection_name,
                "count": count,
                "retention_years": _RETENTION_YEARS,
                "legal_basis": legal_basis,
                "cutoff": cutoff,
                "building_id": building_id or "ALL",
                "action_required": action_required,
            })
        else:
            logger.info("%s: no records older than %d years (building_id=%s)",
                        collection_name, _RETENTION_YEARS, building_id or "ALL")

    await _check(
        "audit_logs",
        "Privacy Act 1988 APP 11; ISO 27001",
        "Manual review and secure deletion authorised by strata manager",
    )
    await _check(
        "data_breach_log",
        "Privacy Act 1988 Part IIIC; OAIC guidelines",
        "Manual review; obtain OAIC confirmation before deletion",
    )

    result = {
        "checked_collections": checked,
        "flagged_for_review": flagged,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "retention_years": _RETENTION_YEARS,
        "building_id_scope": building_id or "ALL",
        "mode": "flag_only",
        "note": (
            "No records have been deleted. Flagged records require manual review and "
            "authorised destruction in accordance with ACT UTMA 2011 s.89."
        ),
    }

    if flagged:
        logger.warning(
            "Retention check complete — %d collection(s) flagged for review (building=%s)",
            len(flagged), building_id or "ALL",
        )
    else:
        logger.info(
            "Retention check complete — all collections within retention window (building=%s)",
            building_id or "ALL",
        )

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data retention enforcer (flag-only mode)")
    parser.add_argument(
        "--building-id",
        default=None,
        help="Scope check to a single building (default: all buildings)",
    )
    args = parser.parse_args()

    result = asyncio.run(run_retention_check(building_id=args.building_id))
    import json

    print(json.dumps(result, indent=2))
