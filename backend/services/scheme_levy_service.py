"""
Scheme Levy Service — Class A / Class B levy calculation engine.

When a building has an active scheme class split (Class A = apartments,
Class B = townhouses) this service computes per-unit levy amounts that
respect the legal ACT separation:

  - class_a_only costs  → distributed across Class A units by their UOE
  - class_b_only costs  → distributed across Class B units by their UOE
  - common costs        → split by class_a_pct/class_b_pct, then within
                          each class by that class's UOE
  - all_lots costs      → single pool, all units by UOE (legacy behaviour)

All monetary values inside this service work in DOLLARS (float),
converting from cents at the boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Simple data containers (avoid circular imports with models) ────────────────

class UnitRecord:
    """Lightweight unit data needed for levy calculation."""

    def __init__(self, unit_number: str, entitlement: float, scheme_class: Optional[str]):
        """Generated function header.

        Function: UnitRecord.__init__
        Path: backend/services/scheme_levy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.unit_number = unit_number
        self.entitlement = float(entitlement or 0)
        self.scheme_class = scheme_class  # "class_a" | "class_b" | None


class CategoryAllocation:
    """Lightweight category allocation row."""

    def __init__(
            self,
            category_id: str,
            category_name: str,
            fund: str,
            allocation_type: str,
            class_a_pct: float,
            class_b_pct: float,
            amount_cents: int,
    ):
        """Generated function header.

        Function: CategoryAllocation.__init__
        Path: backend/services/scheme_levy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.category_id = category_id
        self.category_name = category_name
        self.fund = fund
        self.allocation_type = allocation_type  # "class_a_only"|"class_b_only"|"common"|"all_lots"
        self.class_a_pct = float(class_a_pct)
        self.class_b_pct = float(class_b_pct)
        self.amount = amount_cents / 100  # convert to AUD


# ── Main calculation function ──────────────────────────────────────────────────

def compute_class_split_levies(
        units: List[UnitRecord],
        category_allocations: List[CategoryAllocation],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Compute per-unit levy amounts under a Class A/B scheme split.

    Returns two dicts:
      admin_levy  : {unit_number: dollar_amount}
      sinking_levy: {unit_number: dollar_amount}

    Any unit with scheme_class=None is treated as "all_lots" (no split
    assigned yet — falls back to the undifferentiated pool).
    """
    class_a = [u for u in units if u.scheme_class == "class_a"]
    class_b = [u for u in units if u.scheme_class == "class_b"]

    total_ue_a = sum(u.entitlement for u in class_a) or 1  # guard /0
    total_ue_b = sum(u.entitlement for u in class_b) or 1
    total_ue_all = sum(u.entitlement for u in units) or 1

    admin_levy: Dict[str, float] = {u.unit_number: 0.0 for u in units}
    sinking_levy: Dict[str, float] = {u.unit_number: 0.0 for u in units}

    def _target(fund: str) -> Dict[str, float]:
        """Generated function header.

        Function: _target
        Path: backend/services/scheme_levy_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return admin_levy if fund == "admin" else sinking_levy

    for cat in category_allocations:
        levy = _target(cat.fund)
        amount = cat.amount

        if cat.allocation_type == "class_a_only":
            if not class_a:
                logger.warning("class_a_only allocation '%s' but no Class A units — skipping", cat.category_name)
                continue
            for u in class_a:
                levy[u.unit_number] += amount * (u.entitlement / total_ue_a)

        elif cat.allocation_type == "class_b_only":
            if not class_b:
                logger.warning("class_b_only allocation '%s' but no Class B units — skipping", cat.category_name)
                continue
            for u in class_b:
                levy[u.unit_number] += amount * (u.entitlement / total_ue_b)

        elif cat.allocation_type == "common":
            share_a = amount * cat.class_a_pct / 100
            share_b = amount * cat.class_b_pct / 100
            for u in class_a:
                levy[u.unit_number] += share_a * (u.entitlement / total_ue_a)
            for u in class_b:
                levy[u.unit_number] += share_b * (u.entitlement / total_ue_b)

        else:  # "all_lots" — single pool
            for u in units:
                levy[u.unit_number] += amount * (u.entitlement / total_ue_all)

    return admin_levy, sinking_levy


def compute_class_rates(
        units: List[UnitRecord],
        category_allocations: List[CategoryAllocation],
) -> Dict[str, float]:
    """
    Return the effective rate-per-UE for each class and the all_lots pool.

    Returns dict with keys:
      "class_a_admin_rate", "class_a_sinking_rate",
      "class_b_admin_rate", "class_b_sinking_rate",
      "all_lots_admin_rate", "all_lots_sinking_rate"
    """
    class_a = [u for u in units if u.scheme_class == "class_a"]
    class_b = [u for u in units if u.scheme_class == "class_b"]

    total_ue_a = sum(u.entitlement for u in class_a) or 1
    total_ue_b = sum(u.entitlement for u in class_b) or 1
    total_ue_all = sum(u.entitlement for u in units) or 1

    totals = {
        "class_a_admin": 0.0, "class_a_sinking": 0.0,
        "class_b_admin": 0.0, "class_b_sinking": 0.0,
        "all_lots_admin": 0.0, "all_lots_sinking": 0.0,
    }

    for cat in category_allocations:
        key_suffix = "admin" if cat.fund == "admin" else "sinking"
        amount = cat.amount

        if cat.allocation_type == "class_a_only":
            totals[f"class_a_{key_suffix}"] += amount
        elif cat.allocation_type == "class_b_only":
            totals[f"class_b_{key_suffix}"] += amount
        elif cat.allocation_type == "common":
            totals[f"class_a_{key_suffix}"] += amount * cat.class_a_pct / 100
            totals[f"class_b_{key_suffix}"] += amount * cat.class_b_pct / 100
        else:  # all_lots — apportion by class UE proportion
            totals[f"class_a_{key_suffix}"] += amount * (total_ue_a / total_ue_all)
            totals[f"class_b_{key_suffix}"] += amount * (total_ue_b / total_ue_all)
            totals[f"all_lots_{key_suffix}"] += amount

    return {
        "class_a_admin_rate": totals["class_a_admin"] / total_ue_a,
        "class_a_sinking_rate": totals["class_a_sinking"] / total_ue_a,
        "class_b_admin_rate": totals["class_b_admin"] / total_ue_b,
        "class_b_sinking_rate": totals["class_b_sinking"] / total_ue_b,
        "all_lots_admin_rate": totals["all_lots_admin"] / total_ue_all,
        "all_lots_sinking_rate": totals["all_lots_sinking"] / total_ue_all,
    }


def is_split_active(effective_from: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """Return True if the scheme split's effective date has passed."""
    if effective_from is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    # Ensure both are timezone-aware for comparison
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=timezone.utc)
    return now >= effective_from
