# @featuretrace:letters — Pydantic models for WeasyPrint letter generation
# Layer: model
# Data flow: LettersPage → POST /letters/generate|send → letter_generator.py → letters_log collection
# Related: backend/routers/letters.py
#           backend/utils/letter_generator.py
#           frontend/src/pages/dashboard/admin/LettersPage.tsx
# Scope: (building-scoped)

from typing import List, Optional
from pydantic import BaseModel, field_validator

# ── Template registry ─────────────────────────────────────────────────────────

TEMPLATE_SCHEMAS = {
    "levy_reminder": {
        "label": "Levy Reminder",
        "description": "Overdue levy notice with amount, unit, due date, and payment methods",
        "fields": [
            {"name": "owner_name", "type": "string", "required": True, "label": "Owner Name"},
            {"name": "unit_number", "type": "string", "required": True, "label": "Unit Number"},
            {"name": "amount_due", "type": "number", "required": True, "label": "Amount Due (AUD)"},
            {"name": "due_date", "type": "string", "required": True, "label": "Due Date (YYYY-MM-DD)"},
            {"name": "quarter", "type": "string", "required": False, "label": "Quarter (e.g. Q1 2026)"},
            {"name": "payment_ref", "type": "string", "required": False, "label": "Payment Reference"},
        ],
    },
    "agm_invitation": {
        "label": "AGM Invitation",
        "description": "Annual General Meeting date, time, location and agenda",
        "fields": [
            {"name": "owner_name", "type": "string", "required": True, "label": "Owner Name"},
            {"name": "agm_date", "type": "string", "required": True, "label": "AGM Date (YYYY-MM-DD)"},
            {"name": "agm_time", "type": "string", "required": True, "label": "AGM Time (e.g. 6:30 PM)"},
            {"name": "agm_location", "type": "string", "required": True, "label": "Location"},
            {"name": "agenda_items", "type": "textarea", "required": False, "label": "Agenda Items (one per line)"},
        ],
    },
    "general_notice": {
        "label": "General Notice",
        "description": "Freeform owner correspondence with building header",
        "fields": [
            {"name": "owner_name", "type": "string", "required": True, "label": "Owner Name"},
            {"name": "subject", "type": "string", "required": True, "label": "Subject"},
            {"name": "body", "type": "textarea", "required": True, "label": "Letter Body"},
            {"name": "closing", "type": "string", "required": False, "label": "Closing (e.g. Kind regards)"},
        ],
    },
}


# ── Request / Response models ──────────────────────────────────────────────────

class LetterGenerateRequest(BaseModel):
    template: str
    data: dict
    unit_numbers: Optional[List[str]] = None  # None = all units; list = specific units

    @field_validator("template")
    @classmethod
    def template_must_be_valid(cls, v: str) -> str:
        """Generated function header.

        Function: LetterGenerateRequest.template_must_be_valid
        Path: backend/models/letter.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v not in TEMPLATE_SCHEMAS:
            raise ValueError(f"Unknown template '{v}'. Valid: {list(TEMPLATE_SCHEMAS)}")
        return v


class LetterSendRequest(LetterGenerateRequest):
    unit_numbers: List[str]  # required for send — must be explicit


class LetterTemplateInfo(BaseModel):
    name: str
    label: str
    description: str
    fields: list


class LetterLogEntry(BaseModel):
    id: str
    building_id: str
    template: str
    unit_numbers: List[str]
    sent_by: str
    sent_by_name: str
    sent_at: str
    recipient_count: int
    subject: str
