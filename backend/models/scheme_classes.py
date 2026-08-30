"""
Pydantic models for Class A / Class B Scheme Split.

Under the ACT Unit Titles Act 2001, a building may be registered with
two distinct classes of lots (Class A = stacked apartments, Class B =
townhouses). Each class has its own Unit of Entitlement (UOE) pool and
its own levy schedule.

Collections:
  - scheme_classes             : definition of each class (name, units, effective date)
  - class_category_allocations : per-category spend allocation type per financial year
  - scheme_class_history       : immutable audit trail of all changes
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field
from typing import List, Optional


# ── Enums ──────────────────────────────────────────────────────────────────────

class SchemeClassLabel(str, Enum):
    CLASS_A = "class_a"
    CLASS_B = "class_b"


class AllocationDriver(str, Enum):
    """How costs within a class are split across individual units.
    Currently unused — all intra-class levy distributions use UOE weighting.
    Reserved for Phase 3 when per-class distribution rules are introduced.
    """
    UNIT_ENTITLEMENT = "unit_entitlement"  # standard UOE-weighted
    EQUAL_SHARE = "equal_share"  # flat per lot
    LOT_COUNT = "lot_count"  # same as equal_share


class CategoryAllocationType(str, Enum):
    """Who bears the cost of a budget category."""
    CLASS_A_ONLY = "class_a_only"  # paid solely by Class A lots
    CLASS_B_ONLY = "class_b_only"  # paid solely by Class B lots
    COMMON = "common"  # split by class_a_pct / class_b_pct, then UOE within each class
    ALL_LOTS = "all_lots"  # legacy: single pool, all units by UOE (pre-split behaviour)


class HistoryEventType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"
    UNITS_REASSIGNED = "units_reassigned"


# ── Scheme Class models ────────────────────────────────────────────────────────

class SchemeClassCreate(BaseModel):
    class_label: SchemeClassLabel = Field(..., description="'class_a' or 'class_b'")
    class_name: str = Field(..., min_length=1, max_length=100, description="Display name, e.g. 'Apartments'")
    description: Optional[str] = None
    # Units may be assigned explicitly by unit_number OR implied by unit_type prefix
    unit_numbers: List[str] = Field(default_factory=list,
                                    description="Explicit list of unit_number values (e.g. ['UA001','UA002']). Takes priority over unit_type_prefixes.")
    unit_type_prefixes: List[str] = Field(default_factory=list,
                                          description="Auto-assign units whose unit_number starts with these prefixes (e.g. ['UA'] for apartments).")
    effective_from: datetime = Field(...,
                                     description="Date/time at which this class split becomes active (ISO 8601 UTC).")
    notes: Optional[str] = None


class SchemeClassUpdate(BaseModel):
    class_name: Optional[str] = None
    description: Optional[str] = None
    unit_numbers: Optional[List[str]] = None
    unit_type_prefixes: Optional[List[str]] = None
    effective_from: Optional[datetime] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class SchemeClassResponse(BaseModel):
    class_id: str
    building_id: str
    class_label: SchemeClassLabel
    class_name: str
    description: Optional[str]
    unit_numbers: List[str]
    unit_type_prefixes: List[str]
    total_uoe: float = Field(0.0, description="Sum of entitlements for assigned units (computed field)")
    unit_count: int = Field(0, description="Number of lots in this class (computed field)")
    effective_from: datetime
    is_active: bool
    notes: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: datetime


# ── Class Category Allocation models ──────────────────────────────────────────

class ClassCategoryAllocationCreate(BaseModel):
    financial_year: str = Field(..., description="e.g. '2026-2027'")
    category_id: str = Field(..., description="References a budget category _id")
    category_name: str = Field(..., description="Human-readable label, denormalised for display")
    fund: str = Field(..., description="'admin' or 'sinking'")
    allocation_type: CategoryAllocationType = Field(CategoryAllocationType.ALL_LOTS)
    # Used only when allocation_type == "common":
    class_a_pct: float = Field(50.0, ge=0, le=100, description="Percentage of common cost borne by Class A")
    class_b_pct: float = Field(50.0, ge=0, le=100, description="Percentage of common cost borne by Class B")
    amount_cents: int = Field(0, ge=0, description="Total budget amount in cents for this category + year")
    effective_from: datetime
    notes: Optional[str] = None

    def model_post_init(self, __context):  # noqa: ANN001
        """Generated function header.

        Function: ClassCategoryAllocationCreate.model_post_init
        Path: backend/models/scheme_classes.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.allocation_type == CategoryAllocationType.COMMON:
            total = self.class_a_pct + self.class_b_pct
            if abs(total - 100.0) > 0.01:
                raise ValueError(f"class_a_pct + class_b_pct must equal 100 (got {total})")


class ClassCategoryAllocationUpdate(BaseModel):
    allocation_type: Optional[CategoryAllocationType] = None
    class_a_pct: Optional[float] = Field(None, ge=0, le=100)
    class_b_pct: Optional[float] = Field(None, ge=0, le=100)
    amount_cents: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None

    def model_post_init(self, __context):  # noqa: ANN001
        # Only validate pct sum when this update explicitly sets allocation_type=common
        # and provides both percentages. If allocation_type is None (not being updated), skip —
        # we don't know the current DB value from the update payload alone.
        """Generated function header.

        Function: ClassCategoryAllocationUpdate.model_post_init
        Path: backend/models/scheme_classes.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if (self.allocation_type == CategoryAllocationType.COMMON
                and self.class_a_pct is not None
                and self.class_b_pct is not None):
            total = self.class_a_pct + self.class_b_pct
            if abs(total - 100.0) > 0.01:
                raise ValueError(f"class_a_pct + class_b_pct must equal 100 (got {total})")


class ClassCategoryAllocationResponse(BaseModel):
    allocation_id: str
    building_id: str
    financial_year: str
    category_id: str
    category_name: str
    fund: str
    allocation_type: CategoryAllocationType
    class_a_pct: float
    class_b_pct: float
    amount_cents: int
    effective_from: datetime
    notes: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: datetime


# ── Scheme Class History (immutable audit) ────────────────────────────────────

class SchemeClassHistoryEntry(BaseModel):
    history_id: str
    building_id: str
    class_id: str
    event_type: HistoryEventType
    changed_by: str
    changed_at: datetime
    snapshot: dict = Field(default_factory=dict, description="Full state of SchemeClass at time of event")
    notes: Optional[str] = None


# ── Scheme Split Status response (used by levy calculator + LBFI) ─────────────

class UOESummary(BaseModel):
    class_label: SchemeClassLabel
    class_name: str
    unit_count: int
    total_uoe: float
    effective_from: datetime


class SchemeSplitStatus(BaseModel):
    """Returned by GET /scheme-classes/status — tells callers whether a split is configured."""
    building_id: str
    split_active: bool = Field(False, description="True when effective_from <= today and both classes are defined")
    effective_from: Optional[datetime] = None
    class_a: Optional[UOESummary] = None
    class_b: Optional[UOESummary] = None
    total_all_lots_uoe: float = 0.0


# ── Levy impact preview (returned by POST /scheme-classes/preview-levy-impact) ─

class UnitLevyImpact(BaseModel):
    unit_number: str
    scheme_class: Optional[str]
    current_levy: float
    projected_levy: float
    delta: float
    delta_pct: float


class LevyImpactPreview(BaseModel):
    building_id: str
    financial_year: str
    class_a_rate_per_ue: float
    class_b_rate_per_ue: float
    all_lots_rate_per_ue: float
    units: List[UnitLevyImpact]
    class_a_total_levy: float
    class_b_total_levy: float
    grand_total_levy: float
