# @featuretrace:occupancy_intelligence — Pydantic models for Building Occupancy Intelligence
# Layer: model
# Data flow: OccupancyIntelligencePage → /api/occupancy/* → occupancy_status (building-scoped)
# Related: backend/routers/occupancy.py
#           backend/services/occupancy_service.py
#           backend/services/occupancy_recompute.py
#           frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Status constants ──────────────────────────────────────────────────────────
class OccupancyStatusType:
    TENANT = "tenant"
    OWNER_OCCUPIED = "owner_occupied"
    INVESTOR_UNKNOWN = "investor_unknown"


# ── Stored document ────────────────────────────────────────────────────────────
class OccupancyStatus(BaseModel):
    """Persisted per-lot occupancy classification.

    Stored in: occupancy_status collection.
    Unique index: (building_id, lot_number).
    """
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    lot_number: str  # matches unit_number in units collection ("UA001", "TH017")
    unit_type: Optional[str] = None  # "apartment" | "townhouse" | "penthouse"

    status: Literal["tenant", "owner_occupied", "investor_unknown"]
    confidence: float = Field(ge=0.0, le=1.0)  # 0.0–1.0
    sources: List[str]  # ["rental_certificate", "address_match", …]

    last_verified: datetime
    created_at: datetime
    updated_at: datetime


# ── Summary response ───────────────────────────────────────────────────────────
class OccupancySummaryResponse(BaseModel):
    """Aggregate counts + percentages for a building."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    total_lots: int
    tenant_count: int
    owner_occupied_count: int
    investor_unknown_count: int

    tenant_pct: float
    owner_occupied_pct: float
    investor_unknown_pct: float

    avg_confidence: float
    last_computed_at: Optional[datetime] = None


# ── Per-lot response ───────────────────────────────────────────────────────────
class OccupancyLotResponse(BaseModel):
    """Single lot classification, safe for frontend consumption."""
    model_config = ConfigDict(extra="ignore")

    lot_number: str
    unit_type: Optional[str] = None
    status: str
    confidence: float
    sources: List[str]
    last_verified: datetime


# ── Trend data point ───────────────────────────────────────────────────────────
class OccupancyTrendPoint(BaseModel):
    month: str  # "YYYY-MM"
    tenant_count: int
    owner_occupied_count: int
    investor_unknown_count: int
    total: int


class OccupancyTrendsResponse(BaseModel):
    building_id: str
    trend: List[OccupancyTrendPoint]
