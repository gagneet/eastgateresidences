"""
Building Handover Models

When a strata management contract ends, outgoing and incoming managers complete
a structured checklist to ensure continuity. This module tracks the handover lifecycle.

State machine:
  initiated → in_progress → completed
  initiated/in_progress → cancelled

Checklist categories:
  financial — trust reconciliation, bank access, outstanding invoices
  legal     — contracts, by-laws, decisions register, compliance certificates
  operational — keys/access, contractor contacts, maintenance schedules, PPM
  communication — owner roll, notice templates, pending owner queries
  compliance — upcoming deadlines, insurance renewals, essential services
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import List, Optional


class HandoverStatus:
    INITIATED = "initiated"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    OPEN = [INITIATED, IN_PROGRESS]


class ChecklistCategory:
    FINANCIAL = "financial"
    LEGAL = "legal"
    OPERATIONAL = "operational"
    COMMUNICATION = "communication"
    COMPLIANCE = "compliance"


# Standard checklist items generated for every handover
DEFAULT_CHECKLIST = [
    # Financial
    {"category": "financial", "item": "Trust account reconciliation completed and signed off"},
    {"category": "financial", "item": "Bank account access transferred to incoming manager"},
    {"category": "financial", "item": "Outstanding invoices list prepared and handed over"},
    {"category": "financial", "item": "BAS/GST obligations up to handover date documented"},
    {"category": "financial", "item": "Levy arrears schedule prepared"},
    # Legal
    {"category": "legal", "item": "Executed management contract copy provided to incoming manager"},
    {"category": "legal", "item": "By-laws current version provided"},
    {"category": "legal", "item": "Decisions register up to handover date provided"},
    {"category": "legal", "item": "Insurance certificates of currency provided"},
    {"category": "legal", "item": "Any active legal proceedings or disputes documented"},
    # Operational
    {"category": "operational", "item": "Master key register provided"},
    {"category": "operational", "item": "Common area access codes transferred"},
    {"category": "operational", "item": "Contractor panel list with contacts and current agreements provided"},
    {"category": "operational", "item": "Preventive maintenance schedule handed over"},
    {"category": "operational", "item": "Outstanding maintenance requests list provided"},
    # Communication
    {"category": "communication", "item": "Owner and tenant contact roll provided"},
    {"category": "communication", "item": "Pending owner queries or complaints handed over"},
    {"category": "communication", "item": "Email account / communication channel access transferred"},
    # Compliance
    {"category": "compliance", "item": "Essential services compliance schedule provided"},
    {"category": "compliance", "item": "Upcoming compliance deadlines documented"},
    {"category": "compliance", "item": "Insurance renewal dates noted"},
    {"category": "compliance", "item": "AGM schedule and any upcoming meeting notices prepared"},
]


class ChecklistItemUpdate(BaseModel):
    completed: bool
    notes: Optional[str] = None


class HandoverCreate(BaseModel):
    """Initiate a handover."""
    outgoing_manager_id: Optional[str] = None  # user_id of outgoing manager (may be off-platform)
    outgoing_manager_name: str
    outgoing_manager_agency: Optional[str] = None
    incoming_manager_id: Optional[str] = None  # user_id of incoming manager (may not be registered yet)
    incoming_manager_name: str
    incoming_manager_agency: Optional[str] = None
    incoming_manager_email: Optional[str] = None
    target_date: str  # ISO date — planned handover date
    contract_id: Optional[str] = None  # reference to manager_contracts collection
    notes: Optional[str] = None


class HandoverResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    outgoing_manager_name: str
    outgoing_manager_agency: Optional[str] = None
    incoming_manager_name: str
    incoming_manager_agency: Optional[str] = None
    incoming_manager_email: Optional[str] = None
    target_date: str
    contract_id: Optional[str] = None
    status: str
    checklist: List[dict] = []
    checklist_completion_pct: float = 0.0
    notes: Optional[str] = None
    initiated_at: str
    initiated_by: str
    completed_at: Optional[str] = None
    completed_by: Optional[str] = None
    export_generated_at: Optional[str] = None
