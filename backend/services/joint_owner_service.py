"""Joint-owner name parsing and review service.

Implements the parser that splits combined-name strings
(e.g. "John Smith & Jane Smith") into individual owner records,
and the apply step that appends bitemporal ownership_periods
per ADR-025 (reverse the aggregated row, open individual rows).

Discipline boundaries (enforced here, not at the call site):
  - Never mutates an existing ownership_periods row.
  - Never auto-applies: status must be 'confirmed' or 'edited'.
  - The joint_owner_review schema is NOT changed by this service.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from db_postgres.repos import config_repo, ownership_repo
from db_postgres.session import async_session_context, set_tenant

logger = logging.getLogger(__name__)

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"

# ---------------------------------------------------------------------------
# Name-splitting parser
# ---------------------------------------------------------------------------

_TITLES = frozenset(
    {"Dr", "Mr", "Mrs", "Ms", "Miss", "Prof", "Rev", "Sir", "Lady", "A/Prof",
     "Assoc", "Associate"}
)

_AND_RE = re.compile(r"\s+and\s+", re.IGNORECASE)


def _looks_like_name(s: str) -> bool:
    """Rough heuristic: string contains at least one capitalized word."""
    if not s or len(s.strip()) < 2:
        return False
    words = s.strip().split()
    real = [w for w in words if w not in _TITLES]
    if not real:
        return True  # title-only is odd but not invalid
    return real[0][0].isupper()


def _is_surname_first(part: str) -> bool:
    """Return True if 'part' (the segment before a comma) looks like a bare
    surname rather than a full 'Firstname Surname' string.

    Heuristic: a single capitalised word before a comma is almost certainly a
    surname (e.g. "Smith, John").  Two uncapitalised-first-letter words rule out
    the case of titles like "Dr Smith, Jane" — but "Dr" is caught by _TITLES.
    """
    words = part.strip().split()
    if not words:
        return False
    # Strip leading title before counting
    real = [w for w in words if w not in _TITLES]
    # Only one meaningful word → surname-first ("Smith, …")
    return len(real) == 1


@dataclass
class ParsedOwner:
    name: str
    ownership_share: Decimal  # 0 < share ≤ 1, shares sum to 1


@dataclass
class ParseResult:
    original_name: str
    owners: list[ParsedOwner]
    confidence: float  # 0.0–1.0
    split_reason: str  # short debug tag

    def to_dict(self) -> dict:
        """Generated function header.

        Function: ParseResult.to_dict
        Path: backend/services/joint_owner_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "original_name": self.original_name,
            "owners": [
                {"name": o.name, "ownership_share": str(o.ownership_share)}
                for o in self.owners
            ],
            "confidence": self.confidence,
            "split_reason": self.split_reason,
        }


def parse_joint_name(combined: str) -> ParseResult:
    """Split a combined joint-owner name into individual ParsedOwners.

    Priority order:
      1.  " & " → highest confidence
      2.  " and " (case-insensitive)
      3.  ", " — only when NOT surname-first format
      4.  No split — return the original as a single owner

    Ownership shares are assigned equally across parsed parts.
    """
    name = (combined or "").strip()
    if not name:
        return ParseResult(name, [ParsedOwner(name, Decimal("1"))], 0.0, "empty")

    # --- 1. Ampersand separator ---
    if " & " in name:
        parts = [p.strip() for p in name.split(" & ") if p.strip()]
        if len(parts) >= 2:
            valid = [p for p in parts if _looks_like_name(p)]
            confidence = 1.0 if all(len(p.split()) >= 2 for p in valid) else 0.8
            if len(valid) < len(parts):
                confidence = max(0.6, confidence - 0.2)
            return _make_result(name, valid or parts, confidence, "ampersand")

    # --- 2. " and " separator ---
    if _AND_RE.search(name):
        parts = [p.strip() for p in _AND_RE.split(name) if p.strip()]
        if len(parts) >= 2 and all(_looks_like_name(p) for p in parts):
            confidence = 1.0 if all(len(p.split()) >= 2 for p in parts) else 0.75
            return _make_result(name, parts, confidence, "and_keyword")

    # --- 3. Comma separator — must not be surname-first ---
    if ", " in name:
        comma_parts = [p.strip() for p in name.split(", ") if p.strip()]
        if len(comma_parts) == 2:
            first, second = comma_parts
            if not _is_surname_first(first) and _looks_like_name(first) and _looks_like_name(second):
                return _make_result(name, comma_parts, 0.65, "comma_list")
            # Surname-first (e.g. "Smith, John") — do not split
        elif len(comma_parts) >= 3:
            # "Smith, John, Jones, Jane" — ambiguous, low confidence
            if all(_looks_like_name(p) for p in comma_parts):
                return _make_result(name, comma_parts, 0.45, "comma_multi")

    # --- 4. Cannot split ---
    return ParseResult(name, [ParsedOwner(name, Decimal("1"))], 0.1, "no_split")


def _make_result(original: str, parts: list[str], confidence: float, reason: str) -> ParseResult:
    """Generated function header.

    Function: _make_result
    Path: backend/services/joint_owner_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    n = len(parts)
    each = Decimal("1") / Decimal(n)
    # Distribute remainder to first owner so shares sum to exactly 1
    shares = [each] * n
    remainder = Decimal("1") - sum(shares)
    if remainder:
        shares[0] += remainder
    owners = [ParsedOwner(name=p, ownership_share=shares[i]) for i, p in enumerate(parts)]
    return ParseResult(original, owners, round(confidence, 3), reason)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def load_joint_owner_candidates(
        building_id: str,
) -> list[dict]:
    """Return all current ownership_periods for the scheme where the party's
    legal_name contains a known joint-name separator.

    Returns a list of dicts with keys:
      lot_id, lot_number, unit_number, party_id, legal_name
    """
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(f"No Postgres scheme found for building_id={building_id!r}")

    tid = str(scheme["tenant_id"])
    sid = str(scheme["scheme_id"])

    async with async_session_context() as session:
        await set_tenant(session, tid)
        result = await session.execute(
            text("""
                SELECT
                    l.lot_id::TEXT,
                    l.lot_number,
                    l.unit_number,
                    p.party_id::TEXT,
                    p.legal_name
                FROM core.ownership_periods op
                JOIN core.lots    l ON l.lot_id    = op.lot_id
                JOIN core.parties p ON p.party_id  = op.owner_party_id
                WHERE op.scheme_id   = :sid
                  AND op.valid_to    IS NULL
                  AND op.recorded_to IS NULL
                  AND (
                        p.legal_name LIKE '% & %'
                     OR lower(p.legal_name) ~ '\\s+and\\s+'
                  )
                ORDER BY l.lot_number
            """),
            {"sid": sid},
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def seed_review_records(
        building_id: str,
        is_test_data: bool = False,
) -> list[dict]:
    """Parse all joint-owner candidates and insert pending review rows.

    Idempotent: uses ON CONFLICT (lot_id) DO NOTHING so re-running is safe.
    Returns the list of inserted row dicts.
    """
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(f"No Postgres scheme found for building_id={building_id!r}")

    tid = str(scheme["tenant_id"])
    sid = str(scheme["scheme_id"])

    candidates = await load_joint_owner_candidates(building_id)
    inserted = []

    async with async_session_context() as session:
        await set_tenant(session, tid)
        for cand in candidates:
            parsed = parse_joint_name(cand["legal_name"])
            owners_json = json.dumps(
                [{"name": o.name, "ownership_share": str(o.ownership_share)} for o in parsed.owners]
            )
            result = await session.execute(
                text("""
                    INSERT INTO core.joint_owner_review
                        (tenant_id, scheme_id, lot_id, lot_number, unit_number,
                         original_party_id, original_legal_name,
                         parsed_owners, parser_confidence, is_test_data)
                    VALUES
                        (CAST(:tid AS UUID), CAST(:sid AS UUID), CAST(:lot_id AS UUID),
                         :lot_number, :unit_number,
                         CAST(:party_id AS UUID), :legal_name,
                         CAST(:owners AS jsonb), :confidence, :is_test)
                    ON CONFLICT (lot_id) DO NOTHING
                    RETURNING review_id::TEXT, lot_number, original_legal_name
                """),
                {
                    "tid": tid,
                    "sid": sid,
                    "lot_id": cand["lot_id"],
                    "lot_number": cand["lot_number"],
                    "unit_number": cand.get("unit_number"),
                    "party_id": cand["party_id"],
                    "legal_name": cand["legal_name"],
                    "owners": owners_json,
                    "confidence": float(parsed.confidence),
                    "is_test": is_test_data,
                },
            )
            row = result.mappings().fetchone()
            if row:
                inserted.append(dict(row))

    return inserted


async def list_reviews(
        building_id: str,
        status: str | None = None,
) -> list[dict]:
    """Return joint_owner_review rows for a scheme, optionally filtered by status."""
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        return []

    tid = str(scheme["tenant_id"])
    sid = str(scheme["scheme_id"])

    _SELECT = """
                SELECT
                    review_id::TEXT,
                    lot_id::TEXT,
                    lot_number,
                    unit_number,
                    original_party_id::TEXT,
                    original_legal_name,
                    parsed_owners,
                    manager_owners,
                    parser_confidence,
                    status,
                    decided_at,
                    decided_by::TEXT,
                    applied,
                    applied_at,
                    applied_period_ids,
                    is_test_data,
                    created_at
                FROM core.joint_owner_review
                WHERE scheme_id = :sid
            """
    async with async_session_context() as session:
        await set_tenant(session, tid)
        if status:
            result = await session.execute(
                text(_SELECT + " AND status = :status ORDER BY lot_number"),
                {"sid": sid, "status": status},
            )
        else:
            result = await session.execute(
                text(_SELECT + " ORDER BY lot_number"),
                {"sid": sid},
            )
        rows = result.mappings().all()
    return [_serialize_row(dict(r)) for r in rows]


async def update_review(
        building_id: str,
        review_id: str,
        status: str,
        manager_owners: list[dict] | None,
        decided_by_user_id: str,
) -> dict:
    """Update a single review row. manager_owners overrides parsed_owners when present."""
    if status not in {"confirmed", "edited", "rejected"}:
        raise ValueError(f"Invalid status {status!r}")

    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(f"No Postgres scheme found for building_id={building_id!r}")

    tid = str(scheme["tenant_id"])
    now = datetime.now(tz=timezone.utc)

    async with async_session_context() as session:
        await set_tenant(session, tid)
        result = await session.execute(
            text("""
                UPDATE core.joint_owner_review
                SET status        = :status,
                    manager_owners = CAST(:owners AS jsonb),
                    decided_at    = :now,
                    decided_by    = CAST(:uid AS UUID),
                    updated_at    = :now
                WHERE review_id = CAST(:rid AS UUID)
                  AND scheme_id = CAST(:sid AS UUID)
                  AND applied   = false
                RETURNING review_id::TEXT, status, lot_number, original_legal_name
            """),
            {
                "rid": review_id,
                "sid": str(scheme["scheme_id"]),
                "status": status,
                "owners": json.dumps(manager_owners) if manager_owners is not None else None,
                "now": now,
                "uid": decided_by_user_id,
            },
        )
        row = result.mappings().fetchone()
        if row is None:
            raise ValueError(f"Review {review_id!r} not found, already applied, or scheme mismatch")
    return dict(row)


async def apply_confirmed_reviews(
        building_id: str,
        actor_user_id: str,
        dry_run: bool = False,
) -> list[dict]:
    """Apply all confirmed/edited reviews for a scheme.

    For each confirmed review:
      a. Terminate the aggregated ownership_period (valid_to = today, recorded_to = now)
      b. Upsert a party row per individual owner
      c. Open individual ownership_period rows (valid_from = today)
      d. Emit an ADR-025 restatement outbox event per review
      e. Mark the review as applied

    Discipline: never mutates prior ownership_period rows — only appends and
    terminates via the helpers in ownership_repo.

    Returns a list of result dicts (one per review processed).
    """
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(f"No Postgres scheme found for building_id={building_id!r}")

    tid = str(scheme["tenant_id"])
    sid = str(scheme["scheme_id"])
    today = date.today()
    now = datetime.now(tz=timezone.utc)
    results = []

    async with async_session_context() as session:
        await set_tenant(session, tid)

        # Fetch all confirmed/edited reviews not yet applied
        rows_result = await session.execute(
            text("""
                SELECT review_id::TEXT, lot_id::TEXT, lot_number,
                       original_party_id::TEXT, original_legal_name,
                       parsed_owners, manager_owners, parser_confidence
                FROM core.joint_owner_review
                WHERE scheme_id = CAST(:sid AS UUID)
                  AND status IN ('confirmed', 'edited')
                  AND applied = false
                ORDER BY lot_number
            """),
            {"sid": sid},
        )
        reviews = [dict(r) for r in rows_result.mappings().all()]

        for review in reviews:
            review_id = review["review_id"]
            lot_id = review["lot_id"]
            lot_number = review["lot_number"]

            # Resolve the effective owner list (manager override wins)
            owners_json = review["manager_owners"] or review["parsed_owners"]
            if isinstance(owners_json, str):
                owners_data: list[dict] = json.loads(owners_json)
            else:
                owners_data = owners_json or []

            if not owners_data:
                logger.warning("Review %s has empty owner list — skipping", review_id)
                continue

            result_entry: dict[str, Any] = {
                "review_id": review_id,
                "lot_number": lot_number,
                "original_legal_name": review["original_legal_name"],
                "owners_applied": [],
                "dry_run": dry_run,
                "error": None,
            }

            if dry_run:
                result_entry["owners_applied"] = [o["name"] for o in owners_data]
                results.append(result_entry)
                continue

            # Pre-validate all owner data before mutating any DB state.
            # close_ownership_period must NOT be called if any owner row is invalid
            # — a partial write (close without new periods) leaves the lot ownerless.
            n = len(owners_data)
            validated: list[tuple[str, Decimal, bool]] = []  # (name, share, is_primary)
            validation_error: str | None = None
            for idx, owner_data in enumerate(owners_data):
                name = (owner_data.get("name") or "").strip()
                if not name:
                    validation_error = f"Owner at index {idx} has an empty name"
                    break
                share_raw = owner_data.get("ownership_share", str(Decimal("1") / Decimal(n)))
                try:
                    share = Decimal(str(share_raw))
                except Exception:
                    validation_error = f"Invalid ownership_share {share_raw!r} for owner {name!r}"
                    break
                validated.append((name, share, idx == 0))

            if validation_error:
                logger.warning("Skipping review %s — validation failed: %s", review_id, validation_error)
                result_entry["error"] = validation_error
                results.append(result_entry)
                continue

            try:
                # a. Terminate the existing aggregated ownership_period
                await ownership_repo.close_ownership_period(
                    session, lot_id=lot_id, valid_to=today, tenant_id=tid
                )

                # b+c. Create per-individual parties and ownership_periods
                new_period_ids: list[str] = []
                for name, share, is_primary in validated:
                    party_id = await ownership_repo.upsert_owner_party(
                        session, tenant_id=tid, full_name=name, email=None
                    )
                    period_id = await ownership_repo.open_ownership_period_with_share(
                        session,
                        tenant_id=tid,
                        scheme_id=sid,
                        lot_id=lot_id,
                        party_id=party_id,
                        valid_from=today,
                        ownership_share=share,
                        is_primary_owner=is_primary,
                        notes=f"Split from joint party via review {review_id}",
                    )
                    if period_id:
                        new_period_ids.append(period_id)
                    result_entry["owners_applied"].append(name)

                # d. Emit ADR-025 restatement event to core.outbox
                event_id = str(uuid.uuid4())
                restatement_payload = {
                    "restatement_reason": "joint_owner_split",
                    "original_legal_name": review["original_legal_name"],
                    "original_party_id": review["original_party_id"],
                    "review_id": review_id,
                    "decided_by": actor_user_id,
                    "applied_at": now.isoformat(),
                    "new_period_ids": new_period_ids,
                    "provenance": {
                        "tool": "joint_owner_service.apply_confirmed_reviews",
                        "parser_confidence": float(review["parser_confidence"]),
                    },
                }
                await session.execute(
                    text("""
                        INSERT INTO core.outbox
                            (id, tenant_id, scheme_id, event_type, aggregate_id,
                             aggregate_type, payload, created_at)
                        VALUES
                            (CAST(:eid AS UUID), CAST(:tid AS UUID), CAST(:sid AS UUID),
                             'ownership.restatement',
                             CAST(:agg_id AS UUID),
                             'ownership_period',
                             CAST(:payload AS jsonb),
                             :now)
                    """),
                    {
                        "eid": event_id,
                        "tid": tid,
                        "sid": sid,
                        "agg_id": new_period_ids[0] if new_period_ids else str(uuid.uuid4()),
                        "payload": json.dumps(restatement_payload),
                        "now": now,
                    },
                )

                # e. Mark review as applied
                await session.execute(
                    text("""
                        UPDATE core.joint_owner_review
                        SET applied              = true,
                            applied_at           = :now,
                            applied_period_ids   = CAST(:pids AS jsonb),
                            restatement_event_id = CAST(:eid AS UUID),
                            updated_at           = :now
                        WHERE review_id = CAST(:rid AS UUID)
                    """),
                    {
                        "rid": review_id,
                        "now": now,
                        "pids": json.dumps(new_period_ids),
                        "eid": event_id,
                    },
                )

                result_entry["new_period_ids"] = new_period_ids
                result_entry["restatement_event_id"] = event_id

            except Exception as exc:
                logger.error("Failed to apply review %s: %s", review_id, exc)
                result_entry["error"] = str(exc)

            results.append(result_entry)

        if not dry_run:
            await session.commit()

    return results


def _serialize_row(row: dict) -> dict:
    """Convert non-serializable types in a DB row for JSON responses."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = str(v)
        else:
            out[k] = v
    return out
