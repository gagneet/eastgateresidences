"""
Settings-related Pydantic models.

Contains models for site settings, email settings, and scheduling.
"""

from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional


class WorkOrderApprovalThreshold(BaseModel):
    max_amount: float
    approval_required: bool = True
    approval_roles: List[str] = []  # e.g. ["CHAIRMAN", "TREASURER"]
    approval_mode: str = "SINGLE_APPROVAL"  # SINGLE_APPROVAL, DUAL_APPROVAL, MAJORITY


class UnitDisplayRule(BaseModel):
    """One display-prefix rule for a contiguous lot range.

    Lots are identified by their numeric component; the rule controls how the
    number is presented (and how legacy stored keys are generated), e.g.
    prefix="TH", min=71, max=87, pad=3 → lot 87 displays as "TH087".
    """
    prefix: str
    min: int
    max: int
    pad: int = 3

    @field_validator("prefix")
    @classmethod
    def _prefix_alpha(cls, v: str) -> str:
        """Generated function header.

        Function: UnitDisplayRule._prefix_alpha
        Path: backend/models/settings.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        v = (v or "").strip().upper()
        if len(v) > 5 or (v and not v.isalpha()):
            raise ValueError("prefix must be 0-5 letters")
        return v

    @field_validator("min", "max")
    @classmethod
    def _positive(cls, v: int) -> int:
        """Generated function header.

        Function: UnitDisplayRule._positive
        Path: backend/models/settings.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v < 1 or v > 9999:
            raise ValueError("lot bounds must be between 1 and 9999")
        return v

    @field_validator("pad")
    @classmethod
    def _pad_range(cls, v: int) -> int:
        """Generated function header.

        Function: UnitDisplayRule._pad_range
        Path: backend/models/settings.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v < 1 or v > 4:
            raise ValueError("pad must be between 1 and 4")
        return v


class UnitDisplayRulesUpdate(BaseModel):
    rules: List[UnitDisplayRule]

    @field_validator("rules")
    @classmethod
    def _no_overlap(cls, rules: List[UnitDisplayRule]) -> List[UnitDisplayRule]:
        """Generated function header.

        Function: UnitDisplayRulesUpdate._no_overlap
        Path: backend/models/settings.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if len(rules) > 20:
            raise ValueError("at most 20 rules per building")
        spans = sorted((r.min, r.max) for r in rules)
        for (lo1, hi1), (lo2, _hi2) in zip(spans, spans[1:]):
            if hi1 < lo1:
                raise ValueError("rule max must be >= min")
            if lo2 <= hi1:
                raise ValueError("rule lot ranges must not overlap")
        if spans and spans[-1][1] < spans[-1][0]:
            raise ValueError("rule max must be >= min")
        return rules


class SiteSettingsUpdate(BaseModel):
    building_name: Optional[str] = None
    building_address: Optional[str] = None
    strata_address: Optional[str] = None
    plan_number: Optional[str] = None
    building_abn: Optional[str] = None
    building_logo_url: Optional[str] = None
    logo_url: Optional[str] = None  # legacy alias retained for existing buildings
    building_description: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    hero_image: Optional[str] = None
    about_content: Optional[str] = None
    footer_text: Optional[str] = None
    ip_string: Optional[str] = None
    quick_links: Optional[List[dict]] = None
    resident_links: Optional[List[dict]] = None
    notification_retention_days: Optional[int] = 30
    reminder_lead_days: Optional[int] = 14  # Default 14 days, must be 7-30
    financial_year_start_month: Optional[int] = 1  # Default to January
    timezone: Optional[str] = "Australia/Sydney"  # Timezone for date/time display
    # Levy Collection Schedule
    levy_collection_frequency: Optional[str] = None  # monthly, quarterly, half_yearly, yearly
    levy_due_months: Optional[List[int]] = None  # e.g. [3, 6, 9, 12] for quarterly
    levy_due_day_type: Optional[str] = None  # "first" | "middle" | "last" | "custom"
    levy_due_day: Optional[int] = None  # 1–31, only used when type="custom"
    levy_due_custom_dates: Optional[dict[str, int]] = None  # Mapping of month string index to specific day
    grace_period_days: Optional[int] = None  # days after due date before overdue
    interest_rate_per_month: Optional[float] = None  # e.g. 0.02 = 2% per month
    penalty_amount: Optional[float] = None  # fixed late payment penalty
    gst_registered: Optional[bool] = True
    levy_gst_rate: Optional[float] = 0.10  # decimal or percent; normalized by GST helpers
    # Managing-agent branding for Levy Notices (email + PDF). "StrataOS" is only a
    # platform default — configuring these substitutes the strata management
    # company's identity everywhere the notice is issued. Resolved by
    # services/levy_notice_email_service.resolve_managing_agent_branding.
    strata_management_company: Optional[str] = None  # e.g. "Civium Property Group"
    strata_management_logo_url: Optional[str] = None
    strata_management_abn: Optional[str] = None
    strata_management_licence: Optional[str] = None
    strata_management_website: Optional[str] = None
    strata_manager_phone: Optional[str] = None
    strata_manager_email: Optional[str] = None
    strata_manager_address: Optional[str] = None
    # Shared document appearance for notices, meeting letters and finance exports.
    document_branding_mode: Optional[str] = "dual"  # dual | agency | building
    document_accent_color: Optional[str] = "#B8823D"
    document_footer_text: Optional[str] = None
    document_show_page_numbers: Optional[bool] = True
    agm_recording_disclosure: Optional[str] = None
    agm_insurance_disclosure: Optional[str] = None
    levies_department_phone: Optional[str] = None  # "levies_team" email format contact
    levies_department_email: Optional[str] = None
    # Levy Notice email delivery options.
    levy_notice_email_format: Optional[str] = None  # "standard" | "levies_team"
    levy_notice_support_email: Optional[str] = None  # explicit support address override
    levy_notice_support_domain: Optional[str] = None  # domain for the derived UP<plan>@ address
    levy_notice_disclaimer: Optional[str] = None  # optional PDF footer disclaimer
    # Work Order & Approval Settings
    work_order_thresholds: Optional[List[WorkOrderApprovalThreshold]] = None
    quotes_required_thresholds: Optional[List[dict]] = None  # e.g. [{"max_amount": 2000, "quotes": 1}, ...]
    invoice_approval_threshold: Optional[float] = None
    emergency_override_enabled: Optional[bool] = True
    # Maintenance Intelligence Configuration
    inflation_rate: Optional[float] = 0.03  # Default 3%
    repair_weight: Optional[float] = 3.0
    age_weight: Optional[float] = 20.0
    maintenance_overdue_weight: Optional[float] = 2.0
    levy_change_limit_percent: Optional[float] = 5.0
    reserve_target_months: Optional[int] = 12
    projection_horizon_years: Optional[int] = 10
    # Rate limiting (requests per minute per IP)
    rate_limit_register: Optional[int] = None
    rate_limit_login: Optional[int] = None
    rate_limit_forgot_password: Optional[int] = None
    rate_limit_reset_password: Optional[int] = None
    rate_limit_change_password: Optional[int] = None
    rate_limit_registration_decision: Optional[int] = None
    rate_limit_multiplier: Optional[float] = None

    @field_validator("document_branding_mode")
    @classmethod
    def validate_document_branding_mode(cls, value):
        if value is None:
            return value
        mode = str(value).strip().lower()
        if mode not in {"dual", "agency", "building"}:
            raise ValueError("document_branding_mode must be dual, agency, or building")
        return mode

    @field_validator("document_accent_color")
    @classmethod
    def validate_document_accent_color(cls, value):
        if value is None:
            return value
        colour = str(value).strip().upper()
        if len(colour) != 7 or not colour.startswith("#"):
            raise ValueError("document_accent_color must be a six-digit hex colour")
        try:
            int(colour[1:], 16)
        except ValueError as exc:
            raise ValueError("document_accent_color must be a six-digit hex colour") from exc
        return colour

    @field_validator("levy_due_custom_dates")
    @classmethod
    def validate_custom_dates(cls, v):
        """Generated function header.

        Function: SiteSettingsUpdate.validate_custom_dates
        Path: backend/models/settings.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v is None:
            return v
        for month, day in v.items():
            try:
                m_int = int(month)
                if not (1 <= m_int <= 12):
                    raise ValueError(f"Invalid month index: {month}")
            except ValueError:
                raise ValueError(f"Month index must be an integer string between 1 and 12: {month}")

            if not (1 <= day <= 31):
                raise ValueError(f"Day must be between 1 and 31: {day}")
        return v


class EmailSettingsUpdate(BaseModel):
    provider: str = "resend"  # resend, sendgrid, smtp
    resend_api_key: Optional[str] = None
    sendgrid_api_key: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None


class ScheduleCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_time: str
    end_time: str
    location: Optional[str] = None
    assigned_to: Optional[str] = None
    schedule_type: str = "general"  # general, cleaning, maintenance, inspection


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    start_time: str
    end_time: str
    location: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    schedule_type: str
    status: str
    created_by: str
    created_at: str


__all__ = [
    "SiteSettingsUpdate",
    "EmailSettingsUpdate",
    "ScheduleCreate",
    "ScheduleResponse",
]
