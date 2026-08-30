"""
Financial Service — atomic transaction service with backward-compatibility adapter.

This service is the primary write path for the refactored financial architecture.
It writes to the NEW collections (financial_transactions, financial_categories,
financial_years, levy_plans) while keeping the legacy collections untouched.

Backward-compatibility adapter
--------------------------------
``get_legacy_annual_levy_shape()`` reconstructs the old annual_levies response
format by aggregating from financial_transactions + levy_plans.  This allows
the service to be used as a drop-in compute layer if a future migration moves
all reads to the new collections, without changing any existing API contracts.

``get_legacy_levy_categories_shape()`` reconstructs the levy_categories list
with dynamically computed actual_amount values from financial_transactions.

Design decisions
-----------------
* actual_amount is NEVER stored on financial_categories; it is always derived.
* All writes go through repository functions (financial_repository.py) to keep
  DB calls testable and isolated.
* Legacy read helpers still query the old collections directly — no migration
  of reads is forced here.  Services call the adapter only if they opt in.
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone

from typing import Any, Dict, List, Optional

from repositories.financial_repository import (
    aggregate_actual_by_category,
    aggregate_fund_totals,
    get_financial_categories,
    get_financial_year,
    get_levy_plans,
    insert_transaction,
    list_transactions,
    upsert_financial_category,
    upsert_financial_year,
    upsert_levy_plan,
)
from utils.finance_helpers import (
    TOTAL_UOE,
    get_current_levy_year,
    legacy_fund_type,
    normalise_fund_type,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/financial_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Financial Year management
# ─────────────────────────────────────────────────────────────────────────────

async def create_or_update_financial_year(
        year: str,
        building_id: str,
        status: str = "proposed",
        admin_levy_per_uoe: float = 0.0,
        sinking_levy_per_uoe: float = 0.0,
        total_uoe: int = TOTAL_UOE,
        payment_schedule: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Create or update a financial_years document.
    Returns the full stored document.
    """
    data = {
        "year": year,
        "status": status,
        "building_id": building_id,
        "total_uoe": total_uoe,
        "admin_levy_per_uoe": admin_levy_per_uoe,
        "sinking_levy_per_uoe": sinking_levy_per_uoe,
        "payment_schedule": payment_schedule or [],
    }
    return await upsert_financial_year(data, building_id)


# ─────────────────────────────────────────────────────────────────────────────
# Levy Plan management
# ─────────────────────────────────────────────────────────────────────────────

async def create_or_update_levy_plan(
        year: str,
        fund_type: str,
        building_id: str,
        total_income_required: float = 0.0,
        total_expense_budgeted: float = 0.0,
        opening_balance: float = 0.0,
        closing_balance_projected: float = 0.0,
) -> Dict[str, Any]:
    """
    Upsert a levy_plans document for (year, fund_type).
    closing_balance_projected defaults to: opening + income - expenses.
    """
    if closing_balance_projected == 0.0:
        closing_balance_projected = (
                opening_balance + total_income_required - total_expense_budgeted
        )
    data = {
        "financial_year": year,
        "fund_type": fund_type,
        "building_id": building_id,
        "total_income_required": total_income_required,
        "total_expense_budgeted": total_expense_budgeted,
        "opening_balance": opening_balance,
        "closing_balance_projected": round(closing_balance_projected, 2),
    }
    return await upsert_levy_plan(data, building_id)


# Onboarding-wizard levy-total key -> levy_plans ``fund_type``.
# "cw" = capital works, which is the sinking fund in the levy_plans fund
# vocabulary ("admin" | "sinking").
_ONBOARDING_LEVY_FUND_MAP: tuple[tuple[str, str], ...] = (
    ("total_admin_levy", "admin"),
    ("total_cw_levy", "sinking"),
)


def _parse_levy_amount(value: Any) -> Optional[float]:
    """Parse a wizard levy total (AUD dollars) into a non-negative float.

    The onboarding wizard sends the amount as a string from an HTML number input
    (e.g. ``"1234.56"``) or omits the key entirely. Blank / ``None`` / non-numeric
    -> ``None`` (the plan is skipped); a negative value is treated as invalid and
    also returns ``None``.
    """
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        amount = float(text_value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return amount


async def create_levy_plans_from_onboarding(
        building_id: str,
        step_data: Dict[str, Any],
        financial_year: Optional[str] = None,
) -> Dict[str, Any]:
    """Create ``levy_plans`` rows for a Track A (clean-state) onboarded building.

    Consumes the levy totals captured by the onboarding wizard's "Current Levy
    Schedule" step and preserved in ``step_data`` (``total_admin_levy`` /
    ``total_cw_levy``, both AUD dollars) and creates the matching admin and
    capital-works (sinking) ``levy_plans`` documents, so a freshly onboarded
    building actually has a levy plan to bill against (GAP-ONBOARD-002 Item 1).

    Mapping:
      * ``total_admin_levy`` -> ``levy_plans(fund_type="admin")``
      * ``total_cw_levy``    -> ``levy_plans(fund_type="sinking")`` (capital works)

    ``total_income_required`` is set from the captured total; ``opening_balance``
    is left at 0.0 — fund opening balances are posted as genesis journals during
    finalize, not carried on the levy plan. A fund whose total is missing, blank,
    or non-positive is skipped (capital works is optional in the wizard).

    Amounts stay as dollar floats to match the ``levy_plans`` collection's
    existing convention (it is a per-year income/expense *plan*, not a cents-based
    ledger table). ``financial_year`` defaults to the building's current levy year
    (:func:`utils.finance_helpers.get_current_levy_year`).

    Idempotent: ``upsert_levy_plan`` matches on
    ``(financial_year, fund_type, building_id)``, so re-running finalize produces
    the same rows rather than duplicates.

    NOTE: ``levy_plans`` is a tenant-scoped Mongo collection — the caller MUST
    have set the Mongo building context (``set_ctx_building_id(building_id)``)
    before calling this. The onboarding finalize path sets it explicitly because
    it otherwise runs in a Postgres-native context with no Mongo scope.

    Returns ``{"financial_year", "created": [...], "skipped": [...]}``.
    """
    step_data = step_data or {}
    year = financial_year or await get_current_levy_year(building_id)

    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for step_key, fund_type in _ONBOARDING_LEVY_FUND_MAP:
        amount = _parse_levy_amount(step_data.get(step_key))
        if not amount:  # None or 0.0 -> nothing to bill for this fund
            skipped.append({"fund_type": fund_type, "source_key": step_key})
            continue
        plan = await create_or_update_levy_plan(
            year=year,
            fund_type=fund_type,
            building_id=building_id,
            total_income_required=amount,
        )
        created.append({
            "fund_type": fund_type,
            "financial_year": year,
            "total_income_required": amount,
            "id": (plan or {}).get("id"),
        })

    if created:
        logger.info(
            "Track A onboarding: created %d levy_plan(s) for building=%s year=%s (%s)",
            len(created), building_id, year,
            ", ".join(p["fund_type"] for p in created),
        )
    return {"financial_year": year, "created": created, "skipped": skipped}


# ─────────────────────────────────────────────────────────────────────────────
# Financial Category management
# ─────────────────────────────────────────────────────────────────────────────

async def create_or_update_category(
        year: str,
        fund_type: str,
        name: str,
        building_id: str,
        budgeted_amount: float = 0.0,
        description: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a financial_categories document."""
    data = {
        "financial_year": year,
        "fund_type": fund_type,
        "name": name,
        "building_id": building_id,
        "budgeted_amount": budgeted_amount,
        "description": description,
    }
    return await upsert_financial_category(data, building_id)


async def get_categories_with_actuals(
        year: str,
        building_id: str,
        fund_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return financial_categories enriched with computed actual_amount.

    actual_amount is derived from financial_transactions (expense type),
    NOT stored on the category document — honouring the schema contract that
    removes actual_amount as a stored field.
    """
    cats = await get_financial_categories(year, fund_type=fund_type, building_id=building_id)
    actuals = await aggregate_actual_by_category(year, fund_type=fund_type, building_id=building_id)

    for cat in cats:
        cat["actual_amount"] = actuals.get(cat["name"], 0.0)

    return cats


# ─────────────────────────────────────────────────────────────────────────────
# Transaction recording
# ─────────────────────────────────────────────────────────────────────────────

async def record_transaction(
        year: str,
        fund_type: str,
        building_id: str,
        transaction_type: str,
        amount: float,
        transaction_date: str,
        category_name: Optional[str] = None,
        category_id: Optional[str] = None,
        lot_number: Optional[str] = None,
        unit_number: Optional[str] = None,
        gst_amount: float = 0.0,
        description: Optional[str] = None,
        reference: Optional[str] = None,
        created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record an atomic financial transaction in financial_transactions.

    transaction_type must be one of:
      income | expense | levy | interest | special_levy
    """
    data = {
        "financial_year": year,
        "fund_type": fund_type,
        "building_id": building_id,
        "transaction_type": transaction_type,
        "amount": round(amount, 2),
        "gst_amount": round(gst_amount, 2),
        "transaction_date": transaction_date,
    }
    if category_name:
        data["category_name"] = category_name
    if category_id:
        data["category_id"] = category_id
    if lot_number:
        data["lot_number"] = lot_number
    if unit_number:
        data["unit_number"] = unit_number
    if description:
        data["description"] = description
    if reference:
        data["reference"] = reference
    if created_by:
        data["created_by"] = created_by

    return await insert_transaction(data)


async def get_transactions(
        building_id: str,
        year: Optional[str] = None,
        fund_type: Optional[str] = None,
        transaction_type: Optional[str] = None,
        lot_number: Optional[str] = None,
        category_name: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 500,
) -> List[Dict[str, Any]]:
    """Retrieve financial transactions with optional filters. Scoped to building."""
    return await list_transactions(
        year=year,
        fund_type=fund_type,
        transaction_type=transaction_type,
        lot_number=lot_number,
        category_name=category_name,
        date_from=date_from,
        date_to=date_to,
        building_id=building_id,
        limit=limit,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compatibility adapter layer
# ─────────────────────────────────────────────────────────────────────────────

async def get_legacy_annual_levy_shape(year: str, building_id: str) -> Optional[Dict[str, Any]]:
    """
    Reconstruct the legacy annual_levies document shape from the new collections.

    Returns a dict that matches the AnnualLevyResponse schema so existing API
    consumers see no change.  Returns None if no data exists for the year.

    Legacy shape expected by the frontend::

        {
          "year": "2026",
          "status": "proposed",
          "building_id": "...",
          "total_uoe": 10000,
          "admin_fund": {
            "levy_income": ..., "other_income": ..., "total_income": ...,
            "total_expenses": ..., "opening_balance": ...,
            "closing_balance": ..., "surplus_deficit": ...
          },
          "sinking_fund": { ... },
          "admin_levy_per_uoe_annual": ...,
          "admin_levy_per_uoe_quarterly": ...,
          "sinking_levy_per_uoe_annual": ...,
          "sinking_levy_per_uoe_quarterly": ...,
          "payment_schedule": [...],
          "created_at": "...",
          "updated_at": "..."
        }
    """
    fy = await get_financial_year(year, building_id)
    if not fy:
        return None

    plans = await get_levy_plans(year, building_id)
    fund_totals = await aggregate_fund_totals(year, building_id)

    def _build_fund(fund_type: str) -> Dict[str, Any]:
        """Generated function header.

        Function: _build_fund
        Path: backend/services/financial_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        plan = next((p for p in plans if p["fund_type"] == fund_type), {})
        totals = fund_totals.get(fund_type, {"income": 0.0, "expense": 0.0})
        levy_income = totals["income"]
        total_expenses = totals["expense"]
        other_income = 0.0
        total_income = levy_income + other_income
        opening_balance = plan.get("opening_balance", 0.0)
        closing_balance = plan.get("closing_balance_projected", 0.0)
        surplus_deficit = closing_balance - opening_balance
        return {
            "levy_income": round(levy_income, 2),
            "other_income": round(other_income, 2),
            "total_income": round(total_income, 2),
            "total_expenses": round(total_expenses, 2),
            "opening_balance": round(opening_balance, 2),
            "closing_balance": round(closing_balance, 2),
            "surplus_deficit": round(surplus_deficit, 2),
        }

    admin_rate = fy.get("admin_levy_per_uoe", 0.0)
    sinking_rate = fy.get("sinking_levy_per_uoe", 0.0)

    return {
        "id": fy.get("id", ""),
        "year": year,
        "status": fy.get("status", "proposed"),
        "building_id": fy.get("building_id", building_id),
        "total_uoe": fy.get("total_uoe", TOTAL_UOE),
        "admin_fund": _build_fund("admin"),
        "sinking_fund": _build_fund("sinking"),
        "admin_levy_per_uoe_annual": round(admin_rate, 4),
        "admin_levy_per_uoe_quarterly": round(admin_rate / 4, 4),
        "sinking_levy_per_uoe_annual": round(sinking_rate, 4),
        "sinking_levy_per_uoe_quarterly": round(sinking_rate / 4, 4),
        "payment_schedule": fy.get("payment_schedule", []),
        "created_at": fy.get("created_at", _now()),
        "updated_at": fy.get("updated_at", _now()),
    }


async def get_legacy_levy_categories_shape(
        year: str, fund_type: Optional[str] = None, building_id: str = None
) -> List[Dict[str, Any]]:
    """
    Return levy categories in the legacy levy_categories response shape,
    with actual_amount computed dynamically from financial_transactions.

    Legacy shape per item::

        {
          "id": "...", "year": "2026", "fund_type": "administrative",
          "name": "Insurance", "budgeted_amount": 50000.0,
          "actual_amount": 48000.0,   ← derived
          "description": "...", "building_id": "...", "status": "proposed",
          "created_at": "...", "updated_at": "..."
        }

    Note: the new schema uses "admin" / "sinking" for fund_type.  Legacy
    clients may send "administrative" / "sinking" — both are accepted.
    """
    # normalise legacy fund_type name
    normalised_fund_type = normalise_fund_type(fund_type) if fund_type else None

    cats = await get_categories_with_actuals(year, building_id=building_id, fund_type=normalised_fund_type)

    result = []
    for c in cats:
        result.append({
            "id": c.get("id", ""),
            "year": year,
            "fund_type": legacy_fund_type(c.get("fund_type", "")),
            "name": c.get("name", ""),
            "budgeted_amount": c.get("budgeted_amount", 0.0),
            "actual_amount": c.get("actual_amount", 0.0),
            "description": c.get("description"),
            "building_id": c.get("building_id", building_id),
            "status": "proposed",
            "created_at": c.get("created_at", _now()),
            "updated_at": c.get("updated_at", _now()),
        })
    return result


__all__ = [
    "create_or_update_financial_year",
    "create_or_update_levy_plan",
    "create_or_update_category",
    "get_categories_with_actuals",
    "record_transaction",
    "get_transactions",
    "get_legacy_annual_levy_shape",
    "get_legacy_levy_categories_shape",
]
