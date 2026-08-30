# @featuretrace:by-law-breach-register — Breach status vocabulary + request/response models.
# Layer: model
# Data flow: routers/by_law_breach.py -> BreachStatus/ByLawBreachCreate -> by_law_breach_reports
#            (building-scoped) -> BreachStatus.UNRESOLVED -> health_score_service._dispute.
# Related: backend/routers/by_law_breach.py
#          backend/routers/community_dashboard.py
#          backend/services/health_score_service.py
#
# LESSON (2026-08-27): BreachStatus.OPEN and BreachStatus.UNRESOLVED are NOT interchangeable.
# OPEN models workflow state and deliberately excludes TRIBUNAL_REFERRED, because the register
# has handed the matter to ACAT/NCAT and there is nothing left to do in-app. For REPORTING,
# a matter before a tribunal is the most serious live dispute a scheme has -- counting with
# OPEN would show a building with five active tribunal cases as having none. UNRESOLVED is
# derived by subtracting CLOSED from ALL so a status added later fails safe (counts as live)
# rather than silently dropping out of every total.
"""
By-law Breach Report Models

State machine:
  reported → acknowledged → courtesy_notice_sent → formal_notice_sent
           → escalated (ACAT/NCAT) → resolved / tribunal_referred
  resolved at any stage if parties reach agreement.

ACT: UTMA 2011 — formal notice before ACAT referral required.
NSW: SSMA 2015 s.147 — notice required before NCAT application.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import List, Optional


class BreachStatus:
    REPORTED = "reported"
    ACKNOWLEDGED = "acknowledged"
    COURTESY_NOTICE_SENT = "courtesy_notice_sent"
    FORMAL_NOTICE_SENT = "formal_notice_sent"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    TRIBUNAL_REFERRED = "tribunal_referred"
    WITHDRAWN = "withdrawn"

    ALL = [
        REPORTED, ACKNOWLEDGED, COURTESY_NOTICE_SENT, FORMAL_NOTICE_SENT,
        ESCALATED, RESOLVED, TRIBUNAL_REFERRED, WITHDRAWN,
    ]
    OPEN = [REPORTED, ACKNOWLEDGED, COURTESY_NOTICE_SENT, FORMAL_NOTICE_SENT, ESCALATED]
    TERMINAL = [RESOLVED, TRIBUNAL_REFERRED, WITHDRAWN]

    # Statuses that count as a live dispute for reporting (building health, dashboards).
    #
    # Deliberately NOT `OPEN`. A matter referred to ACAT/NCAT is the most serious kind of
    # unresolved dispute a scheme can have, but `OPEN` classifies it as terminal because
    # the WORKFLOW is finished — the register has handed off to a tribunal. Counting
    # "open disputes" with `OPEN` would report a building with five active tribunal cases
    # as having none.
    #
    # Defined by SUBTRACTION so it fails safe: a status added later is treated as
    # unresolved until someone decides otherwise, rather than being silently dropped from
    # the count the way an addition to an inclusion list would be.
    CLOSED = [RESOLVED, WITHDRAWN]
    # UNRESOLVED is derived just below the class body, not here: a comprehension inside a
    # class body cannot see the class's own names, so `[s for s in ALL if s not in CLOSED]`
    # raises NameError on CLOSED at import time.
    UNRESOLVED: list = []

    # Valid transitions: {from_status: [allowed_to_statuses]}
    TRANSITIONS = {
        REPORTED: [ACKNOWLEDGED, WITHDRAWN],
        ACKNOWLEDGED: [COURTESY_NOTICE_SENT, RESOLVED, WITHDRAWN],
        COURTESY_NOTICE_SENT: [FORMAL_NOTICE_SENT, RESOLVED, WITHDRAWN],
        FORMAL_NOTICE_SENT: [ESCALATED, RESOLVED, WITHDRAWN],
        ESCALATED: [TRIBUNAL_REFERRED, RESOLVED],
        RESOLVED: [],
        TRIBUNAL_REFERRED: [],
        WITHDRAWN: [],
    }


# Derived by SUBTRACTION so it fails safe: a status added to ALL later counts as
# unresolved until someone deliberately closes it, rather than vanishing from the count
# the way an addition to an inclusion list silently would.
BreachStatus.UNRESOLVED = [s for s in BreachStatus.ALL if s not in BreachStatus.CLOSED]

class BreachSeverity:
    MINOR = "minor"  # first-time, low impact — courtesy notice appropriate
    MODERATE = "moderate"  # repeated or ongoing — formal notice required
    SERIOUS = "serious"  # safety risk or significant impact — fast-track escalation


class ByLawBreachCreate(BaseModel):
    """Reporter submits a by-law breach report."""
    alleged_unit: str  # unit allegedly in breach
    by_law_section: Optional[str] = None  # e.g. "Section 4 — Parking and Vehicles"
    description: str  # detailed description of alleged breach
    incident_date: Optional[str] = None  # ISO date of incident
    severity: str = BreachSeverity.MINOR
    evidence_file_ids: List[str] = []  # ids from documents collection
    is_repeat_offence: bool = False
    notes: Optional[str] = None


class BreachNotice(BaseModel):
    """A formal or courtesy notice issued in response to a breach.

    `issued_at` and `issued_by` are set server-side and may be omitted by callers.
    """
    notice_type: str  # "courtesy" | "formal"
    issued_at: Optional[str] = None  # set by router from server clock
    issued_by: Optional[str] = None  # set by router from current_user
    template_used: str
    delivery_method: str = "email"  # "email" | "post" | "hand_delivered"
    response_due_by: Optional[str] = None  # ISO date
    notes: Optional[str] = None


class BreachStatusUpdate(BaseModel):
    """Manager advances the breach to a new state."""
    new_status: str
    notes: Optional[str] = None
    resolution_outcome: Optional[str] = None  # for RESOLVED / TRIBUNAL_REFERRED
    escalation_target: Optional[str] = None  # "ACAT" | "NCAT" for ESCALATED


class ByLawBreachResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    reporter_id: str
    reporter_unit: Optional[str] = None
    alleged_unit: str
    by_law_section: Optional[str] = None
    description: str
    incident_date: Optional[str] = None
    severity: str
    status: str
    is_repeat_offence: bool = False
    evidence_file_ids: List[str] = []
    notices: List[dict] = []
    escalated_at: Optional[str] = None
    escalation_target: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_outcome: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    updated_at: str
