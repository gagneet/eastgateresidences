"""
Financial Models — Consolidated Schema (Target Architecture)

This module defines Pydantic models for the refactored financial collections:
  - financial_years       : one document per strata year (replaces parts of annual_levies)
  - financial_categories  : budgeted expense line items per year per fund (replaces levy_categories)
  - financial_transactions: atomic income/expense records (replaces finance collection)
  - levy_plans            : income/expenditure plan per year per fund (refactored annual_levies)

Backward-compatibility strategy
--------------------------------
Old collections (annual_levies, levy_categories, unit_levy_ledger, finance) are NOT
deleted.  All legacy API endpoints continue to read from those collections unchanged.
New endpoints use these models and the new collections.  A migration script
(scripts/migrate_financial_schema.py) back-fills the new collections from the old
ones so both read paths stay consistent after migration is executed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import Any, Dict, List, Optional

from utils.auth import DEFAULT_BUILDING_ID


# ─────────────────────────────────────────────────────────────────────────────
# financial_years — one document per calendar/strata year
# Replaces the summary portion of annual_levies.
# ─────────────────────────────────────────────────────────────────────────────

class PaymentScheduleEntry(BaseModel):
    """A single quarterly due-date entry inside a financial year."""
    quarter: str  # "Q1" … "Q4"
    due_date: str  # ISO-8601 date string, e.g. "2026-04-01"


class FinancialYearCreate(BaseModel):
    """Payload for creating a new financial year document."""
    year: str  # e.g. "2026"
    status: str = "proposed"  # "proposed" | "approved" | "closed"
    plan_id: str = DEFAULT_BUILDING_ID
    total_uoe: int = 10000
    admin_levy_per_uoe: float = 0.0  # annual rate per unit-of-entitlement
    sinking_levy_per_uoe: float = 0.0
    payment_schedule: List[PaymentScheduleEntry] = []


class FinancialYearResponse(BaseModel):
    """API response shape for a financial year document."""
    model_config = ConfigDict(extra="ignore")

    id: str
    year: str
    status: str
    plan_id: str
    total_uoe: int
    admin_levy_per_uoe: float
    sinking_levy_per_uoe: float
    payment_schedule: List[Dict[str, Any]] = []
    created_at: str
    updated_at: str


class FinancialYearUpdate(BaseModel):
    """Allowed fields for a PATCH update on a financial year."""
    status: Optional[str] = None
    admin_levy_per_uoe: Optional[float] = None
    sinking_levy_per_uoe: Optional[float] = None
    payment_schedule: Optional[List[PaymentScheduleEntry]] = None


# ─────────────────────────────────────────────────────────────────────────────
# financial_categories — expense / income line items per year per fund
# Replaces levy_categories.  actual_amount is NOT stored; it is derived
# dynamically from financial_transactions via aggregation.
# ─────────────────────────────────────────────────────────────────────────────

class FinancialCategoryCreate(BaseModel):
    """Payload for creating a budget category."""
    financial_year: str  # e.g. "2026"
    fund_type: str  # "admin" | "sinking"
    name: str
    budgeted_amount: float = 0.0
    description: Optional[str] = None
    plan_id: str = DEFAULT_BUILDING_ID


class FinancialCategoryResponse(BaseModel):
    """
    API response for a financial category.

    actual_amount is computed on-demand via financial_transactions aggregation and
    is included in responses for backward compatibility with existing dashboard
    widgets that previously read actual_amount from levy_categories.
    """
    model_config = ConfigDict(extra="ignore")

    id: str
    financial_year: str
    fund_type: str
    name: str
    budgeted_amount: float
    actual_amount: float = 0.0  # derived — populated by service layer
    description: Optional[str] = None
    plan_id: str
    created_at: str
    updated_at: str


class FinancialCategoryUpdate(BaseModel):
    """Allowed fields for updating a budget category."""
    budgeted_amount: Optional[float] = None
    description: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# financial_transactions — atomic financial truth layer
# Replaces the legacy finance collection.
# ─────────────────────────────────────────────────────────────────────────────

class FinancialTransactionCreate(BaseModel):
    """
    Payload for recording a new financial transaction.

    transaction_type values:
      income        — general income (interest, rent, etc.)
      expense       — general expense against a budget category
      levy          — quarterly levy payment by a lot owner
      interest      — interest charge on arrears
      special_levy  — one-off special levy assessment
    """
    financial_year: str
    fund_type: str  # "admin" | "sinking"
    category_id: Optional[str] = None  # references financial_categories.id
    category_name: Optional[str] = None  # denormalised for fast read
    transaction_type: str  # see docstring above
    lot_number: Optional[str] = None
    unit_number: Optional[str] = None
    amount: float
    gst_amount: float = 0.0
    description: Optional[str] = None
    reference: Optional[str] = None
    transaction_date: str  # ISO-8601 datetime string
    created_by: Optional[str] = None
    plan_id: str = DEFAULT_BUILDING_ID


class FinancialTransactionResponse(BaseModel):
    """API response for a single financial transaction."""
    model_config = ConfigDict(extra="ignore")

    id: str
    financial_year: str
    fund_type: str
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    transaction_type: str
    lot_number: Optional[str] = None
    unit_number: Optional[str] = None
    amount: float
    gst_amount: float
    description: Optional[str] = None
    reference: Optional[str] = None
    transaction_date: str
    created_by: Optional[str] = None
    plan_id: str
    created_at: str


class FinancialTransactionFilter(BaseModel):
    """Query filter for listing transactions."""
    financial_year: Optional[str] = None
    fund_type: Optional[str] = None
    transaction_type: Optional[str] = None
    lot_number: Optional[str] = None
    category_name: Optional[str] = None
    date_from: Optional[str] = None  # ISO-8601
    date_to: Optional[str] = None  # ISO-8601


# ─────────────────────────────────────────────────────────────────────────────
# levy_plans — income/expenditure plan per year per fund
# A lighter-weight refactoring of annual_levies that explicitly tracks
# opening/closing balance projections per fund.
# ─────────────────────────────────────────────────────────────────────────────

class LevyPlanCreate(BaseModel):
    """Payload for creating a levy plan (one per year per fund type)."""
    financial_year: str
    fund_type: str  # "admin" | "sinking"
    total_income_required: float = 0.0
    total_expense_budgeted: float = 0.0
    opening_balance: float = 0.0
    closing_balance_projected: float = 0.0
    plan_id: str = DEFAULT_BUILDING_ID


class LevyPlanResponse(BaseModel):
    """API response for a levy plan document."""
    model_config = ConfigDict(extra="ignore")

    id: str
    financial_year: str
    fund_type: str
    total_income_required: float
    total_expense_budgeted: float
    opening_balance: float
    closing_balance_projected: float
    plan_id: str
    created_at: str
    updated_at: str


class LevyPlanUpdate(BaseModel):
    """Allowed fields for updating a levy plan."""
    total_income_required: Optional[float] = None
    total_expense_budgeted: Optional[float] = None
    opening_balance: Optional[float] = None
    closing_balance_projected: Optional[float] = None


__all__ = [
    # financial_years
    "PaymentScheduleEntry",
    "FinancialYearCreate",
    "FinancialYearResponse",
    "FinancialYearUpdate",
    # financial_categories
    "FinancialCategoryCreate",
    "FinancialCategoryResponse",
    "FinancialCategoryUpdate",
    # financial_transactions
    "FinancialTransactionCreate",
    "FinancialTransactionResponse",
    "FinancialTransactionFilter",
    # levy_plans
    "LevyPlanCreate",
    "LevyPlanResponse",
    "LevyPlanUpdate",
]
