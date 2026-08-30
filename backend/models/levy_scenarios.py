"""
Pydantic models for the Levy Scenario Modeller.

A scenario lets strata committees and managers model "what-if" levy allocations:
 - Divide all units into N custom groups (not just the legal Class A/B)
 - Assign each group a percentage share of the admin and sinking budgets
 - Optionally adjust per-unit UOE values within a group
   (total UOE across all groups must equal the original total)
 - Compute per-unit levies for the scenario and compare with the current base
 - Optionally publish UOE overrides to the units collection (requires chairman+)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioStatus(str, Enum):
    DRAFT = "draft"
    COMPUTED = "computed"
    PUBLISHED = "published"  # UOE overrides applied to units collection


# ─────────────────────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────────────────────

class UnitUOEOverride(BaseModel):
    """Optional per-unit UOE override within a group."""
    unit_number: str
    adjusted_uoe: float = Field(..., gt=0, description="Replacement UOE for this unit in the scenario")


class ScenarioGroupCreate(BaseModel):
    """Define one group in a scenario."""
    group_name: str = Field(..., min_length=1, max_length=80)
    color: str = Field(default="#6366f1", description="Hex colour for UI rendering")
    # Unit assignment — at least one of these must be non-empty
    unit_numbers: List[str] = Field(default_factory=list,
                                    description="Explicit unit numbers assigned to this group")
    unit_type_prefixes: List[str] = Field(default_factory=list,
                                          description="Unit number prefixes to auto-include (e.g. 'UA', 'TH')")
    # Budget shares — must sum to 100 across all groups
    admin_share_pct: float = Field(..., ge=0.0, le=100.0,
                                   description="Percentage of total admin budget this group pays")
    sinking_share_pct: float = Field(..., ge=0.0, le=100.0,
                                     description="Percentage of total sinking budget this group pays")
    # Optional per-unit UOE adjustments
    uoe_overrides: List[UnitUOEOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unit_assignment(self) -> "ScenarioGroupCreate":
        """Generated function header.

        Function: ScenarioGroupCreate.validate_unit_assignment
        Path: backend/models/levy_scenarios.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not self.unit_numbers and not self.unit_type_prefixes:
            raise ValueError(
                f"Group '{self.group_name}': at least one unit_number or "
                "unit_type_prefix must be specified."
            )
        return self


class ScenarioGroupUpdate(BaseModel):
    """Partial update for a group (all fields optional)."""
    group_name: Optional[str] = Field(None, min_length=1, max_length=80)
    color: Optional[str] = None
    unit_numbers: Optional[List[str]] = None
    unit_type_prefixes: Optional[List[str]] = None
    admin_share_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    sinking_share_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    uoe_overrides: Optional[List[UnitUOEOverride]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Scenario CRUD models
# ─────────────────────────────────────────────────────────────────────────────

class LevyScenarioCreate(BaseModel):
    scenario_name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    financial_year: str = Field(..., description="e.g. '2026-2027'")
    total_admin_budget: float = Field(..., ge=0.0,
                                      description="Total admin fund budget for this scenario (AUD)")
    total_sinking_budget: float = Field(..., ge=0.0,
                                        description="Total capital works fund budget for this scenario (AUD)")
    groups: List[ScenarioGroupCreate] = Field(..., min_length=1)

    @field_validator("financial_year")
    @classmethod
    def validate_fy(cls, v: str) -> str:
        """Generated function header.

        Function: LevyScenarioCreate.validate_fy
        Path: backend/models/levy_scenarios.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        parts = v.split("-")
        if len(parts) != 2 or not all(p.isdigit() and len(p) == 4 for p in parts):
            raise ValueError("financial_year must be 'YYYY-YYYY', e.g. '2026-2027'")
        return v

    @model_validator(mode="after")
    def validate_group_shares(self) -> "LevyScenarioCreate":
        """Generated function header.

        Function: LevyScenarioCreate.validate_group_shares
        Path: backend/models/levy_scenarios.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        admin_total = round(sum(g.admin_share_pct for g in self.groups), 4)
        sinking_total = round(sum(g.sinking_share_pct for g in self.groups), 4)
        if abs(admin_total - 100.0) > 0.01:
            raise ValueError(
                f"admin_share_pct across all groups must sum to 100.0 (got {admin_total})"
            )
        if abs(sinking_total - 100.0) > 0.01:
            raise ValueError(
                f"sinking_share_pct across all groups must sum to 100.0 (got {sinking_total})"
            )
        return self


class LevyScenarioUpdate(BaseModel):
    scenario_name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    total_admin_budget: Optional[float] = Field(None, ge=0.0)
    total_sinking_budget: Optional[float] = Field(None, ge=0.0)
    groups: Optional[List[ScenarioGroupCreate]] = None

    @model_validator(mode="after")
    def validate_group_shares_if_provided(self) -> "LevyScenarioUpdate":
        """Generated function header.

        Function: LevyScenarioUpdate.validate_group_shares_if_provided
        Path: backend/models/levy_scenarios.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.groups is not None:
            admin_total = round(sum(g.admin_share_pct for g in self.groups), 4)
            sinking_total = round(sum(g.sinking_share_pct for g in self.groups), 4)
            if abs(admin_total - 100.0) > 0.01:
                raise ValueError(
                    f"admin_share_pct across all groups must sum to 100.0 (got {admin_total})"
                )
            if abs(sinking_total - 100.0) > 0.01:
                raise ValueError(
                    f"sinking_share_pct across all groups must sum to 100.0 (got {sinking_total})"
                )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Computation result models
# ─────────────────────────────────────────────────────────────────────────────

class UnitScenarioResult(BaseModel):
    """Per-unit levy computation result."""
    unit_number: str
    group_name: str
    group_color: str
    original_uoe: float
    scenario_uoe: float
    uoe_changed: bool
    # Current levy (based on existing budgets + UOE)
    current_admin_levy: float
    current_sinking_levy: float
    current_total_levy: float
    # Scenario levy
    scenario_admin_levy: float
    scenario_sinking_levy: float
    scenario_total_levy: float
    # Deltas
    admin_delta: float
    sinking_delta: float
    total_delta: float
    total_delta_pct: float


class GroupSummaryResult(BaseModel):
    """Per-group summary within computation results."""
    group_name: str
    group_color: str
    unit_count: int
    total_original_uoe: float
    total_scenario_uoe: float
    admin_share_pct: float
    sinking_share_pct: float
    group_admin_levy: float
    group_sinking_levy: float
    group_total_levy: float
    avg_levy_per_unit: float


class ScenarioComputeResult(BaseModel):
    """Full computation result stored on the scenario."""
    computed_at: str
    # Totals
    grand_total_admin: float
    grand_total_sinking: float
    grand_total_levy: float
    # UOE integrity check
    total_original_uoe: float
    total_scenario_uoe: float
    uoe_total_preserved: bool
    uoe_diff: float  # should be ~0 if all overrides respect the constraint
    # Current base (for comparison)
    current_admin_rate_per_uoe: float
    current_sinking_rate_per_uoe: float
    # Per-group summaries
    group_summaries: List[GroupSummaryResult]
    # Per-unit results
    unit_results: List[UnitScenarioResult]


# ─────────────────────────────────────────────────────────────────────────────
# Response models
# ─────────────────────────────────────────────────────────────────────────────

class LevyScenarioResponse(BaseModel):
    """Full scenario representation returned by the API."""
    id: str
    building_id: str
    scenario_name: str
    description: Optional[str]
    financial_year: str
    total_admin_budget: float
    total_sinking_budget: float
    groups: List[dict]
    status: ScenarioStatus
    last_result: Optional[dict]
    published_at: Optional[str]
    published_by: Optional[str]
    created_by: str
    created_at: str
    updated_at: str


class LevyScenariosListResponse(BaseModel):
    """Paginated list of scenarios."""
    total: int
    items: List[LevyScenarioResponse]


class BaseDataResponse(BaseModel):
    """Current building levy data to pre-populate scenario inputs."""
    building_id: str
    financial_year: str
    total_admin_budget: float
    total_sinking_budget: float
    current_admin_rate_per_uoe: float
    current_sinking_rate_per_uoe: float
    total_uoe: float
    unit_count: int
    units: List[dict]  # [{unit_number, entitlement, unit_type, scheme_class}]
    available_years: List[str]
