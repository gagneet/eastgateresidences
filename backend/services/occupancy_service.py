# @featuretrace:occupancy_intelligence — Classification service for Building Occupancy Intelligence
# Layer: service
# Data flow: lot data → classify_occupancy() → occupancy_status collection (building-scoped)
# Related: backend/routers/occupancy.py
#           backend/services/occupancy_recompute.py
#           backend/models/occupancy.py

"""
Occupancy Classification Service
=================================
Classifies each lot as:
  - "tenant"           — active rental certificate present         (confidence 0.95)
  - "owner_occupied"   — address match between owner & property    (confidence 0.85–0.90)
  - "investor_unknown" — real estate agent present OR fallback     (confidence 0.50–0.70)

Rule priority (highest wins):
  1. Active rental certificate   → tenant          (1.0 → 0.95)
  2. Owner address matches lot   → owner_occupied  (0.85–0.90)
  3. Agent / property manager    → investor_unknown (~0.70)
  4. Fallback                    → investor_unknown (0.50)

Extensibility hooks are documented at the bottom of this file.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ── Internal helpers ───────────────────────────────────────────────────────────

def _normalise_address(addr: Optional[str]) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    if not addr:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", addr.lower())).strip()


def _addresses_match(addr_a: Optional[str], addr_b: Optional[str]) -> bool:
    """True if normalised addresses share at least 3 meaningful tokens."""
    a, b = _normalise_address(addr_a), _normalise_address(addr_b)
    if not a or not b:
        return False
    tokens_a = set(t for t in a.split() if len(t) > 2)
    tokens_b = set(t for t in b.split() if len(t) > 2)
    overlap = tokens_a & tokens_b
    # Need ≥3 matching tokens to avoid false positives on suburb-only matches
    return len(overlap) >= 3


def _has_agent_indicator(owner_data: Dict[str, Any]) -> bool:
    """Returns True if owner data suggests a real estate agent / PM is involved."""
    indicators = ["real estate", "property management", "realty", "pm:", "c/-", "c/o"]
    combined = " ".join(
        str(v).lower()
        for k, v in owner_data.items()
        if k in ("owner_name", "owner_name_b", "contact_address", "mailing_address") and v
    )
    return any(ind in combined for ind in indicators)


# ── Public classification function ─────────────────────────────────────────────

def classify_occupancy(
        lot: Dict[str, Any],
        rental_certificates: List[Dict[str, Any]],
        owner_data: Dict[str, Any],
        building_address: Optional[str] = None,
) -> Tuple[str, float, List[str]]:
    """Classify a single lot's occupancy.

    Parameters
    ----------
    lot:
        Document from the ``units`` collection (must contain ``unit_number``).
    rental_certificates:
        All active rental certificates for this lot (``status == "issued"``).
    owner_data:
        Owner document (from ``units`` or ``lot_ownerships``) with fields such as
        ``owner_name``, ``contact_address``, ``mailing_address``.
    building_address:
        Normalised street address of the building (e.g. "1 Denman Prospect").
        Used for negative address matching (if owner's address IS the building,
        they're likely owner-occupied).

    Returns
    -------
    (status, confidence, sources)
    """
    sources: List[str] = []

    # ── Rule 0: Explicit occupancy_type on unit → highest confidence ─────
    unit_occupancy_type = lot.get("occupancy_type", "")
    if unit_occupancy_type == "owner_occupied":
        sources.append("unit_occupancy_type")
        return "owner_occupied", 0.99, sources
    if unit_occupancy_type in ("rented", "tenant"):
        sources.append("unit_occupancy_type")
        return "tenant", 0.99, sources

    # ── Rule 1: Active rental certificate → tenant ────────────────────────
    active_certs = [
        c for c in rental_certificates
        if c.get("status") == "issued"
    ]
    if active_certs:
        sources.append("rental_certificate")
        return "tenant", 0.95, sources

    # ── Rule 2: Owner address matches building address → owner_occupied ───
    owner_address = (
            owner_data.get("contact_address")
            or owner_data.get("mailing_address")
            or owner_data.get("postal_address")
            or ""
    )
    # If the owner's contact address matches the building address they live here
    if building_address and owner_address:
        if _addresses_match(owner_address, building_address):
            sources.append("address_match")
            return "owner_occupied", 0.90, sources

    # ── Rule 3a: Registered user has role="tenant" → tenant ──────────────
    # The users collection is the authoritative source for registered tenants.
    # A tenant registered for this unit without a rental certificate is still
    # a known tenant (confidence slightly lower than a cert, but trustworthy).
    registered_user = owner_data.get("_registered_user")  # injected by recompute engine
    if registered_user and registered_user.get("role") == "tenant":
        sources.append("registered_tenant")
        return "tenant", 0.85, sources

    # ── Rule 3b: Registered user's address matches building → owner_occupied ──
    if registered_user:
        user_address = (
                registered_user.get("address")
                or registered_user.get("mailing_address")
                or ""
        )
        if building_address and user_address and _addresses_match(user_address, building_address):
            sources.append("user_address_match")
            return "owner_occupied", 0.85, sources

    # ── Rule 4: Agent / property manager indicator → investor_unknown ─────
    if _has_agent_indicator(owner_data):
        sources.append("agent_indicator")
        return "investor_unknown", 0.70, sources

    # ── Rule 5: Fallback → investor_unknown ───────────────────────────────
    sources.append("fallback")
    return "investor_unknown", 0.50, sources

# ── Extensibility hooks (DO NOT DELETE — future correlations) ──────────────────
#
# To add capital-shock correlation:
#   1. Accept `sinking_fund_balance` param → lower confidence for investor lots
#      with high balances (special levy risk signal)
#
# To add levy-risk scoring:
#   1. Accept `arrears_cents` param → flag investor_unknown lots with arrears
#      as levy_risk_elevated (new status extension point)
#
# To add tenant-turnover prediction:
#   1. Accept `cert_history: List[Dict]` → count cert transitions over 12 months
#      → high turnover (≥3 in 12m) → flag as "high_turnover_tenant"
