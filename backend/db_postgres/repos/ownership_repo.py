# @featuretrace:owner-transfers — Postgres-side write helpers backing the
#   owner-transfer approval workflow's ownership_periods sync.
# Layer: model
"""
Postgres ownership period management — Phase G prep.

Provides reusable helpers for writing to core.ownership_periods and
related tables (core.parties, core.lots).

⚠️  core.lots HAS TWO DIFFERENT IDENTIFIERS. Filter on the right one.

    lot_number   = the plan lot number   -> "79"      (bare digits)
    unit_number  = the addressable unit  -> "TH079"   (what people say and type)

They are NOT interchangeable, and mixing them up FAILS SILENTLY: a query for
`WHERE lot_number = 'UA019'` matches nothing and returns an empty result set,
which is indistinguishable from "this lot has no owner" or "the data was never
restored". It has caused a wrong diagnosis more than once — most recently on
2026-08-27, when six lots were reported as having zero ownership periods and the
real answer was that every one of them had an owner all along.

Anything a user, a CSV, a levy notice or a URL refers to is the UNIT number.
Reach for `unit_number` unless you specifically mean the plan lot. When a lookup
returns zero rows, check which column you filtered before concluding the row is
missing.

Used by:
  - server.py: _write_postgres_ownership_period() → _finalize_owner_transfer_approval()
  - Phase G: ownership read endpoints (to be built)

IMPORTANT: Wrap every call at the call site in try/except. These functions
are non-fatal during the Phase F/G transition — if core.lots is not yet
populated for a building (not yet onboarded via the CSV import pipeline),
all functions silently return None and log a warning rather than blocking
the MongoDB write path.

Phase G contract: once all buildings are onboarded and core.lots is
authoritative, the try/except wrappers at call sites should be removed and
these writes should be mandatory.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text

logger = logging.getLogger(__name__)

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


async def get_lot_id_by_number(pg_session, scheme_id: str, unit_number: str) -> str | None:
    """Look up core.lots.lot_id by scheme_id + lot_number or unit_number.

    Primary lookup: lot_number = unit_number (East Gate and CSV-onboarded buildings
    where the MongoDB unit_number matches the legal lot_number).

    Fallback lookup: unit_number column — used by the Acme demo building and any
    future buildings where the legal lot_number ("1", "2", ...) differs from the
    display unit_number ("A1", "A2", ...).

    Returns None if the lot does not exist — indicating the building has not yet
    been onboarded via the CSV import pipeline.
    """
    result = await pg_session.execute(
        text("""
             SELECT lot_id::TEXT
             FROM core.lots
             WHERE scheme_id = :sid
               AND (lot_number = :num OR unit_number = :num)
             LIMIT 1
             """),
        {"sid": str(scheme_id), "num": str(unit_number)},
    )
    row = result.fetchone()
    return row[0] if row else None


async def upsert_owner_party(
        pg_session,
        tenant_id: str,
        full_name: str,
        email: str | None = None,
) -> str:
    """Find or create a core.parties row for the owner.

    Lookup order (prevents merging two people who share a name):
      1. Email match in metadata jsonb — most precise; email is unique per person.
      2. Case-insensitive legal_name within tenant — fallback when no email.
    Returns party_id as str.
    """
    name = (full_name or "").strip() or (email or "unknown")

    # Prefer email lookup when available — two people can share a name but
    # not an email, so this avoids corrupt party deduplication.
    if email:
        result = await pg_session.execute(
            text("""
                 SELECT party_id::TEXT
                 FROM core.parties
                 WHERE tenant_id = :tid
                   AND metadata->>'email' = :email
                 LIMIT 1
                 """),
            {"tid": str(tenant_id), "email": email.strip().lower()},
        )
        row = result.fetchone()
        if row:
            return row[0]

    # Fall back to name-only match (used when email is absent)
    result = await pg_session.execute(
        text("""
             SELECT party_id::TEXT
             FROM core.parties
             WHERE tenant_id = :tid
               AND lower(legal_name) = lower(:name)
             LIMIT 1
             """),
        {"tid": str(tenant_id), "name": name},
    )
    row = result.fetchone()
    if row:
        return row[0]

    import json as _json
    metadata = {}
    if email:
        metadata["email"] = email

    result = await pg_session.execute(
        text("""
             INSERT INTO core.parties (tenant_id, party_type, legal_name, metadata)
             VALUES (:tid, 'individual', :name, CAST(:meta AS jsonb))
             RETURNING party_id::TEXT
             """),
        {"tid": str(tenant_id), "name": name, "meta": _json.dumps(metadata)},
    )
    return result.scalar()


async def close_ownership_period(
        pg_session,
        lot_id: str,
        valid_to: date,
        tenant_id: str,
) -> int:
    """Close the current (valid_to IS NULL) ownership period(s) for a lot.

    Sets valid_to on the business-time axis and recorded_to on the system-time
    axis so the bitemporal table retains full audit history. Returns the
    number of rows closed — callers should treat 0 as worth investigating
    (either there was genuinely no open period, or something upstream already
    left the lot without one), since a stale open period left in place will
    silently suppress the next open_ownership_period() insert via the table's
    EXCLUDE constraint on overlapping (lot_id, valid range) — no error, no
    rows changed, easy to miss (found live 2026-07-24, TH078).

    A row whose own valid_from is on or after the requested valid_to cannot
    be "closed" with a business-time end date — ownership_periods_check
    requires valid_to > valid_from, and setting valid_to == valid_from would
    record a zero-length ownership interval that never happened. This arises
    when correcting a period that was itself recorded in error (e.g. a sync
    that ran before an ownership transfer was formally approved, materialising
    the outgoing owner as "current" on the same date the incoming owner's
    period should actually start — found live 2026-07-24, TH078). For those
    rows this is a bitemporal *retraction*, not a closure: only recorded_to is
    set, valid_to is left as-is (already NULL), so the erroneous row drops out
    of the recorded_to IS NULL "current" scope without asserting a business-time
    end date that never occurred.
    """
    result = await pg_session.execute(
        text("""
             UPDATE core.ownership_periods
             SET    valid_to      = CASE WHEN valid_from < :vt THEN :vt ELSE valid_to END,
                    recorded_to   = NOW()
             WHERE  lot_id        = :lid
               AND  valid_to      IS NULL
               AND  recorded_to   IS NULL
             """),
        {"lid": str(lot_id), "vt": valid_to},
    )
    return result.rowcount


async def open_ownership_period(
        pg_session,
        tenant_id: str,
        scheme_id: str,
        lot_id: str,
        party_id: str,
        valid_from: date,
        source_document_id: str | None = None,
) -> str | None:
    """Insert a new ownership period row.

    Uses ON CONFLICT DO NOTHING on the EXCLUDE constraint so duplicate calls
    (e.g. from a retry) are safe.  Returns the ownership_period_id or None
    if a conflict was suppressed.
    """
    result = await pg_session.execute(
        text("""
             INSERT INTO core.ownership_periods
                 (tenant_id, scheme_id, lot_id, owner_party_id,
                  valid_from, valid_to, source_document_id)
             VALUES
                 (:tid, :sid, :lid, :pid, :vf, NULL, :src)
             ON CONFLICT DO NOTHING
             RETURNING ownership_period_id::TEXT
             """),
        {
            "tid": str(tenant_id),
            "sid": str(scheme_id),
            "lid": str(lot_id),
            "pid": str(party_id),
            "vf": valid_from,
            "src": source_document_id,
        },
    )
    row = result.fetchone()
    return row[0] if row else None


async def open_ownership_period_with_share(
        pg_session,
        tenant_id: str,
        scheme_id: str,
        lot_id: str,
        party_id: str,
        valid_from: date,
        ownership_share: "Decimal | None" = None,
        is_primary_owner: bool = False,
        source_document_id: str | None = None,
        notes: str | None = None,
) -> str | None:
    """Insert a new ownership period with explicit share and primary-owner flag.

    Used by the joint-owner apply step to open individual rows after the
    aggregated row has been terminated.  All other semantics are identical
    to ``open_ownership_period``.
    """
    from decimal import Decimal as _Decimal
    share = ownership_share if ownership_share is not None else _Decimal("1")
    result = await pg_session.execute(
        text("""
             INSERT INTO core.ownership_periods
                 (tenant_id, scheme_id, lot_id, owner_party_id,
                  valid_from, valid_to, ownership_share, is_primary_owner,
                  source_document_id, notes)
             VALUES
                 (:tid, :sid, :lid, :pid, :vf, NULL, :share, :primary,
                  :src, :notes)
             ON CONFLICT DO NOTHING
             RETURNING ownership_period_id::TEXT
             """),
        {
            "tid": str(tenant_id),
            "sid": str(scheme_id),
            "lid": str(lot_id),
            "pid": str(party_id),
            "vf": valid_from,
            "share": share,
            "primary": is_primary_owner,
            "src": source_document_id,
            "notes": notes,
        },
    )
    row = result.fetchone()
    return row[0] if row else None
