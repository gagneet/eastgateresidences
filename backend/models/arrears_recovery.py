"""
Arrears Recovery Action Models

State machine:
  (none) → reminder_sent → formal_demand_sent → lod_issued → debt_collector_referred / solicitor_referred
  Any state → payment_plan_active  (when Form 1 plan is approved)
  Any state → written_off          (committee resolution)
  Any state → cleared              (debt paid in full)

A unit can have multiple recovery actions across multiple debt cycles.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import List, Optional


class RecoveryActionType:
    REMINDER = "reminder_sent"
    FORMAL_DEMAND = "formal_demand_sent"
    LOD_ISSUED = "lod_issued"  # Letter of Demand — fee posted to unit ledger
    DEBT_COLLECTOR = "debt_collector_referred"
    SOLICITOR = "solicitor_referred"
    PAYMENT_PLAN = "payment_plan_active"
    WRITTEN_OFF = "written_off"
    CLEARED = "cleared"
    SUPPRESSED = "suppressed"  # manual hold by manager

    ALL = [
        REMINDER, FORMAL_DEMAND, LOD_ISSUED, DEBT_COLLECTOR,
        SOLICITOR, PAYMENT_PLAN, WRITTEN_OFF, CLEARED, SUPPRESSED,
    ]
    # Actions that require manager_only role to trigger manually
    MANAGER_ONLY = {LOD_ISSUED, DEBT_COLLECTOR, SOLICITOR, WRITTEN_OFF, SUPPRESSED}


class RecoveryAction(BaseModel):
    """A single recovery action event on a unit."""
    id: str
    building_id: str
    unit_number: str
    action_type: str  # RecoveryActionType.*
    triggered_at: str  # ISO-8601
    triggered_by: str  # user_id or "system"
    days_overdue_at_trigger: int
    amount_overdue_cents: int
    letter_template_used: Optional[str] = None
    fee_posted_cents: int = 0  # LOD fee charged to unit
    active_plan_id: Optional[str] = None  # payment_plans._id when PAYMENT_PLAN
    notes: Optional[str] = None
    suppressed_until: Optional[str] = None  # ISO-8601, only for SUPPRESSED actions


class RecoveryStatus(BaseModel):
    """Current recovery state for a unit — derived from latest action."""
    building_id: str
    unit_number: str
    current_action: Optional[str] = None  # latest RecoveryActionType
    amount_overdue_cents: int = 0
    days_overdue: int = 0
    suppressed: bool = False
    suppressed_until: Optional[str] = None
    active_plan_id: Optional[str] = None
    history: List[RecoveryAction] = []


class ManualActionRequest(BaseModel):
    """Manager manually triggers or overrides a recovery action."""
    action_type: str  # must be in RecoveryActionType.ALL
    notes: Optional[str] = None
    suppressed_until: Optional[str] = None  # ISO-8601, for SUPPRESSED only
    fee_override_cents: Optional[int] = None  # override default LOD fee


class RecoverySummary(BaseModel):
    """Building-level recovery summary for the manager dashboard."""
    building_id: str
    total_units_in_recovery: int
    breakdown: dict  # {action_type: count}
    total_amount_overdue_cents: int
    units_suppressed: int
    units_on_payment_plan: int
    generated_at: str
