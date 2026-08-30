"""
Ballot Audit Models — append-only ballot ledger with SHA-256 hash chain.

Each BallotEntry is cryptographically linked to its predecessor via prev_hash,
forming a tamper-evident chain. The final BallotSeal captures the terminal hash
and closes the ballot for a given AGM.

Design principles:
  - entry_hash = SHA-256(canonical JSON of all fields except entry_hash itself)
  - prev_hash for the first entry is the AGM ID (well-known anchor value)
  - Seal hash = SHA-256(seal metadata JSON) — independent of the chain, but
    references the final_hash so it cannot be detached and re-attached.
  - All fields are immutable once written (append-only pattern).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ── Entry hash computation ─────────────────────────────────────────────────────

def compute_entry_hash(entry: dict, prev_hash: str) -> str:
    """Compute a deterministic SHA-256 hash for a ballot entry.

    The hash covers all meaningful fields of the entry plus the prev_hash.
    The field ``entry_hash`` itself is excluded so the computation is
    non-circular.  Fields are sorted by key before serialisation to guarantee
    canonical output regardless of insertion order.

    Args:
        entry:     Dict of ballot entry fields (entry_hash may be absent or
                   present — it is silently excluded either way).
        prev_hash: SHA-256 hex digest of the immediately preceding entry, or
                   the AGM ID string for the genesis entry.

    Returns:
        Lowercase hex SHA-256 digest string (64 characters).
    """
    # Build the canonical payload: entry fields (minus entry_hash) + prev_hash
    payload = {k: v for k, v in entry.items() if k != "entry_hash"}
    payload["prev_hash"] = prev_hash

    # Serialise with sorted keys and no whitespace for a stable canonical form.
    # datetime objects must be converted to ISO strings before calling this
    # function — the caller is responsible for that normalisation.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_seal_hash(seal_meta: dict) -> str:
    """Compute a SHA-256 hash of seal metadata.

    Args:
        seal_meta: Dict containing at minimum agm_id, building_id, sealed_at,
                   sealed_by_user_id, total_entries, final_hash.

    Returns:
        Lowercase hex SHA-256 digest string.
    """
    canonical = json.dumps(seal_meta, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Pydantic models ───────────────────────────────────────────────────────────

class BallotEntry(BaseModel):
    """A single immutable entry in the ballot audit ledger.

    Once written to the database this document must never be modified.
    The chain integrity can be verified by replaying compute_entry_hash()
    across all entries sorted by created_at / seq_number.
    """

    id: str = Field(..., description="UUID for this ballot entry")
    building_id: str = Field(..., description="Tenant partition key")
    agm_id: str = Field(..., description="AGM this vote belongs to")
    motion_id: str = Field(..., description="Motion being voted on")
    unit_number: str = Field(..., description="Lot/unit number casting this vote")
    ue_weight: int = Field(
        ...,
        ge=1,
        description="Unit entitlement weight for this lot at time of voting",
    )
    vote: str = Field(
        ...,
        pattern="^(for|against|abstain)$",
        description="Vote value: 'for', 'against', or 'abstain'",
    )
    proxy_holder_id: Optional[str] = Field(
        None,
        description="User ID of proxy holder if vote cast by proxy; None for direct vote",
    )
    cast_by_user_id: str = Field(
        ..., description="User ID of the person who physically submitted this vote"
    )
    prev_hash: str = Field(
        ...,
        description=(
            "SHA-256 of the preceding entry, or the AGM ID string for the genesis entry"
        ),
    )
    entry_hash: str = Field(
        ...,
        description="SHA-256 of all fields in this entry (excluding entry_hash itself) + prev_hash",
    )
    created_at: str = Field(
        ..., description="ISO-8601 UTC timestamp when this entry was written"
    )

    @model_validator(mode="after")
    def verify_entry_hash(self) -> "BallotEntry":
        """Verify that entry_hash matches a fresh computation of the entry payload.

        This guard fires on model instantiation, catching any attempt to
        deserialise a tampered document from the database.
        """
        payload = self.model_dump(exclude={"entry_hash"})
        expected = compute_entry_hash(payload, self.prev_hash)
        if self.entry_hash != expected:
            raise ValueError(
                f"BallotEntry hash mismatch: stored={self.entry_hash!r}, "
                f"computed={expected!r}. This entry may have been tampered with."
            )
        return self


class BallotEntryCreate(BaseModel):
    """Input model for creating a new ballot entry.  entry_hash is computed server-side."""

    id: str
    building_id: str
    agm_id: str
    motion_id: str
    unit_number: str
    ue_weight: int = Field(..., ge=1)
    vote: str = Field(..., pattern="^(for|against|abstain)$")
    proxy_holder_id: Optional[str] = None
    cast_by_user_id: str
    prev_hash: str
    created_at: Optional[str] = None

    def to_entry(self) -> BallotEntry:
        """Materialise a BallotEntry by computing entry_hash from current fields."""
        now = self.created_at or datetime.now(timezone.utc).isoformat()
        payload = {
            "id": self.id,
            "building_id": self.building_id,
            "agm_id": self.agm_id,
            "motion_id": self.motion_id,
            "unit_number": self.unit_number,
            "ue_weight": self.ue_weight,
            "vote": self.vote,
            "proxy_holder_id": self.proxy_holder_id,
            "cast_by_user_id": self.cast_by_user_id,
            "created_at": now,
        }
        entry_hash = compute_entry_hash(payload, self.prev_hash)
        return BallotEntry(
            **payload,
            prev_hash=self.prev_hash,
            entry_hash=entry_hash,
        )


class BallotSeal(BaseModel):
    """An immutable seal record that closes the ballot for an AGM.

    Written once when the returning officer (chairman / super_admin) calls
    POST /agm/{agm_id}/close-ballot.  The seal_hash covers the seal metadata
    so the record cannot be silently altered after creation.
    """

    agm_id: str = Field(..., description="AGM being sealed")
    building_id: str = Field(..., description="Tenant partition key")
    sealed_at: str = Field(..., description="ISO-8601 UTC timestamp of sealing")
    sealed_by_user_id: str = Field(..., description="User who performed the seal")
    total_entries: int = Field(
        ..., ge=0, description="Number of ballot entries in the chain at time of sealing"
    )
    final_hash: str = Field(
        ..., description="entry_hash of the last BallotEntry in the chain"
    )
    seal_hash: str = Field(
        ...,
        description="SHA-256 of seal metadata (agm_id, building_id, sealed_at, sealed_by_user_id, total_entries, final_hash)",
    )
    is_valid: bool = Field(
        True,
        description=(
            "True when seal_hash verifies correctly against the stored metadata. "
            "Set to False if tampering is detected during an integrity audit."
        ),
    )

    @model_validator(mode="after")
    def verify_seal_hash(self) -> "BallotSeal":
        """Verify that seal_hash matches a recomputation of the seal metadata."""
        meta = {
            "agm_id": self.agm_id,
            "building_id": self.building_id,
            "sealed_at": self.sealed_at,
            "sealed_by_user_id": self.sealed_by_user_id,
            "total_entries": self.total_entries,
            "final_hash": self.final_hash,
        }
        expected = compute_seal_hash(meta)
        if self.seal_hash != expected:
            raise ValueError(
                f"BallotSeal hash mismatch: stored={self.seal_hash!r}, "
                f"computed={expected!r}. This seal may have been tampered with."
            )
        return self


class BallotSealCreate(BaseModel):
    """Input model for creating a BallotSeal.  seal_hash is computed server-side."""

    agm_id: str
    building_id: str
    sealed_by_user_id: str
    total_entries: int = Field(..., ge=0)
    final_hash: str

    def to_seal(self) -> BallotSeal:
        """Materialise a BallotSeal by computing seal_hash."""
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "agm_id": self.agm_id,
            "building_id": self.building_id,
            "sealed_at": now,
            "sealed_by_user_id": self.sealed_by_user_id,
            "total_entries": self.total_entries,
            "final_hash": self.final_hash,
        }
        seal_hash = compute_seal_hash(meta)
        return BallotSeal(
            **meta,
            seal_hash=seal_hash,
            is_valid=True,
        )


# ── Integrity verification helper ─────────────────────────────────────────────

def verify_chain(entries: list[dict]) -> dict:
    """Verify the integrity of a sequence of ballot entry dicts.

    Entries must be ordered by their chain sequence (i.e., sorted by created_at
    ascending or by their natural insertion order).

    Returns:
        {
            "valid": bool,
            "checked": int,         # number of entries examined
            "first_broken_at": int | None,   # 0-based index of first bad entry
            "message": str,
        }
    """
    if not entries:
        return {"valid": True, "checked": 0, "first_broken_at": None, "message": "Empty chain."}

    for i, entry in enumerate(entries):
        stored_hash = entry.get("entry_hash", "")
        prev_hash = entry.get("prev_hash", "")
        payload = {k: v for k, v in entry.items() if k != "entry_hash"}
        expected = compute_entry_hash(payload, prev_hash)
        if stored_hash != expected:
            return {
                "valid": False,
                "checked": i + 1,
                "first_broken_at": i,
                "message": (
                    f"Hash mismatch at index {i} (unit={entry.get('unit_number', '?')}, "
                    f"motion={entry.get('motion_id', '?')}). "
                    f"stored={stored_hash!r}, computed={expected!r}"
                ),
            }

    return {
        "valid": True,
        "checked": len(entries),
        "first_broken_at": None,
        "message": f"Chain verified — {len(entries)} entries intact.",
    }
