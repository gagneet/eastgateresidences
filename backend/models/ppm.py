# @featuretrace:ppm — Pydantic schemas for PPM schedules and the compliance dashboard.
# Layer: model
# Data flow: routers/ppm.py -> PPMDashboardSummary (health_score is Optional:
#            None means "no compliance items tracked", never 100).
# Related: backend/routers/ppm.py
# Tests: tests/backend/test_ppm.py

"""
PPM (Preventive Maintenance Plan) Pydantic Models.

Building ID: 13195 — East Gate Residences, 14 Hoolihan Street, Denman Prospect
Source: docs/finances/EastGate-UP13195-PPM-January2026.xlsx
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class PPMScheduleItem(BaseModel):
    """Full response model for a PPM maintenance schedule item."""

    model_config = ConfigDict(extra="ignore")

    id: str
    schedule_id: str
    building_id: str
    section: str
    description: str
    vendor_name: Optional[str] = None
    vendor_contact: Optional[str] = None
    contractor_id: Optional[str] = None
    inspection_type: Optional[str] = None
    standard: Optional[str] = None
    frequency: str
    frequency_days: int
    last_completed_date: Optional[str] = None
    next_due_date: str
    status: str  # scheduled, due_soon, overdue, completed
    is_compliance: bool
    lifespan_years: Optional[int] = None
    capital_years: List[int] = []
    notification_log: List[dict] = []
    completion_log: List[dict] = []
    created_at: str
    updated_at: str


class PPMScheduleItemLimited(BaseModel):
    """Limited fields for owner/resident access."""

    model_config = ConfigDict(extra="ignore")

    id: str
    schedule_id: str
    section: str
    description: str
    inspection_type: Optional[str] = None
    frequency: str
    next_due_date: str
    status: str
    is_compliance: bool


class PPMScheduleUpdate(BaseModel):
    """Fields that managers can update on a PPM item."""

    vendor_name: Optional[str] = None
    vendor_contact: Optional[str] = None
    contractor_id: Optional[str] = None
    next_due_date: Optional[str] = None
    notes: Optional[str] = None


class PPMCompletionLog(BaseModel):
    """Body for logging completion of a PPM item."""

    completed_by: str
    completion_date: str  # ISO date string
    notes: Optional[str] = None
    certificate_ref: Optional[str] = None


class PPMDashboardSummary(BaseModel):
    """Dashboard summary for PPM compliance health."""

    model_config = ConfigDict(extra="ignore")

    total_items: int
    overdue_count: int
    due_soon_count: int
    compliance_total: int
    compliance_overdue_count: int
    # 0-100, % of compliance items on time. None when the building tracks NO
    # compliance items at all: "nothing is overdue because nothing is tracked"
    # is not 100% compliance, and rendering it as such told managers a building
    # with zero PPM coverage was perfectly compliant. Missing and zero are
    # distinct states (CLAUDE.md, mandatory).
    health_score: Optional[float] = None
