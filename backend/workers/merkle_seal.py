"""
backend/workers/merkle_seal.py — Daily Merkle seal job for trust ledger tamper detection.

# @featuretrace:financial_integration_v2 — nightly Merkle seal over closed journals.
# Layer: worker
# Data flow: APScheduler nightly_merkle_seal job (01:00 AEST) → seal_period() → trust_ledger_seals (building-scoped).
# Related: backend/domain/period_lifecycle.py
#           backend/domain/journals.py

The seal provides a cryptographic audit trail. Each day's seal contains:
  - merkle_root: SHA-256 Merkle tree over all posted journal entry hashes for the period
  - control_total_cents: sum of all debit-side amounts (invariant: == sum of credit-side)
  - previous_seal_hash: links to the prior seal, forming a hash chain
  - this_seal_hash: SHA-256(building_id + merkle_root + control_total + previous_seal_hash + date)

Retroactive tampering breaks the hash chain, which any auditor can detect by
re-running seal_period() and comparing this_seal_hash against the stored value.

The seal is also written to the local filesystem (SEAL_EXPORT_PATH env var, default
/tmp/trust_seals). In production, this path is an S3-backed mount with Object Lock
(7-year compliance retention per NSW SSMA s.180).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Hashing primitives ────────────────────────────────────────────────────────

def _sha256(data: str) -> str:
    """Generated function header.

    Function: _sha256
    Path: backend/workers/merkle_seal.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compute_merkle_root(leaf_hashes: List[str]) -> str:
    """Compute a SHA-256 Merkle root over a list of leaf hashes.

    Empty tree → hash of the empty string (deterministic sentinel).
    Odd number of leaves → duplicate the last leaf (standard Bitcoin-style padding).
    """
    if not leaf_hashes:
        return _sha256("")

    layer = list(leaf_hashes)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [
            _sha256(layer[i] + layer[i + 1])
            for i in range(0, len(layer), 2)
        ]
    return layer[0]


def _journal_leaf_hash(entry: Dict[str, Any]) -> str:
    """Deterministic hash of a single journal entry document.

    Uses only fields that must not change post-posting (id, amounts, date).
    Sorted-key JSON ensures the same bytes regardless of dict insertion order.
    """
    canonical = json.dumps(
        {
            "id": str(entry.get("_id", entry.get("id", ""))),
            "date": entry.get("date", ""),
            "debit_total_cents": entry.get("debit_total_cents", entry.get("debit_total", 0)),
            "credit_total_cents": entry.get("credit_total_cents", entry.get("credit_total", 0)),
            "reference": entry.get("reference", ""),
            "building_id": entry.get("building_id", ""),
        },
        sort_keys=True,
    )
    return _sha256(canonical)


# ── Seal computation ─────────────────────────────────────────────────────────

async def seal_period(
        building_id: str,
        period_id: str,
        db: Any,
        seal_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute and persist the Merkle seal for one building × period.

    Args:
        building_id: Tenant identifier.
        period_id: Period label, e.g. '2026-Q1'.
        db: Motor database handle (must expose trust_ledger_entries and trust_ledger_seals).
        seal_date: Override the seal date (ISO YYYY-MM-DD). Defaults to today UTC.

    Returns:
        The persisted seal document (without the MongoDB _id).
    """
    today = seal_date or datetime.now(timezone.utc).date().isoformat()

    # Fetch the most recent prior seal. Serves two purposes:
    #   1. Idempotency guard — if it's already for today, return it without re-inserting.
    #   2. Hash-chain link — provides previous_seal_hash for the new seal.
    prev = await db.trust_ledger_seals.find_one(
        {"tenant_id": building_id, "period_id": period_id},
        sort=[("date", -1)],
    )
    if prev and prev.get("date") == today:
        prev.pop("_id", None)
        logger.info(
            "Seal already exists for building=%s period=%s date=%s — skipping",
            building_id, period_id, today,
        )
        return prev
    previous_seal_hash = prev["this_seal_hash"] if prev else _sha256("genesis")

    # Fetch all POSTED journal entries for this building × period, ordered for determinism.
    entries = await db.trust_ledger_entries.find(
        {"building_id": building_id, "period_id": period_id, "status": "posted"},
    ).sort("created_at", 1).to_list(None)

    leaf_hashes = [_journal_leaf_hash(e) for e in entries]
    merkle_root = compute_merkle_root(leaf_hashes)
    control_total_cents = sum(
        int(e.get("debit_total_cents", e.get("debit_total", 0))) for e in entries
    )
    journal_count = len(entries)

    # Include building_id so seals from different tenants are never hash-identical,
    # even when journals, totals, and dates are coincidentally the same.
    this_seal_hash = _sha256(
        building_id + merkle_root + str(control_total_cents) + previous_seal_hash + today
    )

    seal: Dict[str, Any] = {
        "tenant_id": building_id,
        "period_id": period_id,
        "date": today,
        "merkle_root": merkle_root,
        "journal_count": journal_count,
        "control_total_cents": control_total_cents,
        "previous_seal_hash": previous_seal_hash,
        "this_seal_hash": this_seal_hash,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.trust_ledger_seals.insert_one(seal)
    logger.info(
        "Merkle seal written: building=%s period=%s date=%s journals=%d",
        building_id, period_id, today, journal_count,
    )

    # Export to filesystem (S3-backed mount in production).
    export_path = _export_seal_to_filesystem(seal, building_id, period_id, today)
    logger.info("Seal exported to %s", export_path)

    seal.pop("_id", None)
    return seal


def verify_seal(
        seal: Dict[str, Any],
        entries: List[Dict[str, Any]],
        previous_seal_hash: str,
) -> bool:
    """Re-compute the seal from raw entries and compare against stored hash.

    Returns True if the seal is intact, False if tampered.
    Called by the audit CLI and regression tests.
    """
    leaf_hashes = [_journal_leaf_hash(e) for e in entries]
    merkle_root = compute_merkle_root(leaf_hashes)
    control_total_cents = sum(
        int(e.get("debit_total_cents", e.get("debit_total", 0))) for e in entries
    )

    expected_hash = _sha256(
        seal.get("tenant_id", "")
        + merkle_root
        + str(control_total_cents)
        + previous_seal_hash
        + seal["date"]
    )
    return expected_hash == seal["this_seal_hash"]


# ── Filesystem export ─────────────────────────────────────────────────────────

def _export_seal_to_filesystem(
        seal: Dict[str, Any],
        building_id: str,
        period_id: str,
        date: str,
) -> Path:
    """Generated function header.

    Function: _export_seal_to_filesystem
    Path: backend/workers/merkle_seal.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    base = Path(os.getenv("SEAL_EXPORT_PATH", "/tmp/trust_seals"))
    export_dir = base / building_id / period_id
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"seal_{date}.json"
    exportable = {k: v for k, v in seal.items() if k != "_id"}
    path.write_text(json.dumps(exportable, indent=2))
    return path
