# @featuretrace:feature-toggle-system — Pydantic schemas and discoverable key constants for the feature toggle system.
# Layer: model
# Data flow: seeds/feature_toggles.py → core.feature_toggles (PG) → config_repo.get_resolved_feature_toggle()
#             → utils/permissions.py:get_effective_feature_access() → routers/* (require_feature guards)
# Related: backend/routers/feature_toggles.py
#           backend/utils/permissions.py
#           backend/db_postgres/repos/config_repo.py
#           backend/seeds/feature_toggles.py
# Table: core.feature_toggles, core.feature_toggle_overrides, core.users.permission_overrides
# Tests: tests/backend/test_feature_toggle_dependencies.py
#         tests/backend/test_sentinel_feature_toggle_hardening.py
#         tests/backend/test_gap_ft_router_toggle_enforcement.py
"""
Feature Toggle models for managing feature flags.

Supports both site-wide feature toggles and per-user feature overrides.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from typing import Optional


class FeatureToggleKeys:
    """
    Discoverable registry of feature toggle keys used across routers, workers, and cron jobs.

    Each constant maps to a row in core.feature_toggles (seeded by seeds/feature_toggles.py).
    Use these constants instead of bare strings to avoid silent typos and make grep-able.

    Full catalogue: backend/seeds/feature_toggles.py DEFAULT_FEATURES list.
    Add new keys here when wiring a new require_feature() guard.

    To check which routers use a key:
        grep -rn 'require_feature.*<KEY>' backend/routers/
    """
    # ── Core ────────────────────────────────────────────────────────────────
    DOCUMENTS = "documents"
    SIDEBAR = "sidebar"
    USER_GUIDE = "user_guide"
    EXTERNAL_API = "external_api"
    RATE_LIMITING = "rate_limiting"
    ADMIN_CONSOLE = "admin_console"
    IMPERSONATION = "impersonation"
    SITE_SETTINGS = "site_settings"

    # ── Financial ───────────────────────────────────────────────────────────
    FINANCE = "finance"
    APPROVALS = "approvals"
    TRUST_ACCOUNTING = "trust_accounting"
    ARREARS_RECOVERY = "arrears_recovery"
    ARREARS_RISK = "arrears_risk"
    PAYMENT_PLANS = "payment_plans"
    INVOICE_OCR = "invoice_ocr"
    AP_AUTOMATION = "ap_automation"
    GST_BAS_LEDGER = "gst_bas_ledger"
    SAVINGS = "savings"
    LEVY_KPI_DASHBOARD = "levy_kpi_dashboard"
    FINANCE_INTELLIGENCE = "finance_intelligence"
    FINANCIAL_REPORTING = "financial_reporting"
    FINANCIAL_PROJECTIONS = "financial_projections"
    SPENDING_CATEGORIES = "spending_categories"
    WATER_BILLS = "water_bills"

    # ── Financial cutover (PG migration gates) ──────────────────────────────
    FINANCIAL_INTEGRATION_LAYER_V2 = "financial_integration_layer_v2"
    FINANCIAL_PG_READS_ENABLED = "financial_pg_reads_enabled"
    FINANCIAL_PG_WRITES_ENABLED = "financial_pg_writes_enabled"
    FINANCIAL_SHADOW_READS_ENABLED = "financial_shadow_reads_enabled"
    TRUST_PG_LEDGER_ENABLED = "trust_pg_ledger_enabled"
    TRUST_RECONCILIATION_PG_ENABLED = "trust_reconciliation_pg_enabled"
    OWNER_READ_PG_ENABLED = "owner_read_pg_enabled"
    EXTERNAL_API_FINANCE_PG_ENABLED = "external_api_finance_pg_enabled"
    BANK_INTEGRATION_ABSTRACTION_ENABLED = "bank_integration_abstraction_enabled"

    # ── Maintenance ─────────────────────────────────────────────────────────
    MAINTENANCE = "maintenance"
    WORK_ORDERS = "work_orders"
    DEFECTS = "defects"
    ASSET_REGISTER = "asset_register"
    MAINTENANCE_INTELLIGENCE = "maintenance_intelligence"
    DIGITAL_TWIN = "digital_twin"
    PPM_SCHEDULE = "ppm_schedule"

    # ── Compliance / Legal ──────────────────────────────────────────────────
    COMPLIANCE = "compliance"
    RENTAL_CERTIFICATES = "rental_certificates"
    NSW_COMMISSION_DISCLOSURES = "nsw_commission_disclosures"
    NSW_MAINTENANCE_SCHEDULE = "nsw_maintenance_schedule"
    NSW_STRATA_HUB_RETURNS = "nsw_strata_hub_returns"
    BUILDING_MANAGER_DUTIES = "building_manager_duties"
    CONFLICT_OF_INTEREST = "conflict_of_interest"
    DECISION_REGISTER = "decision_register"

    # ── Governance / Meetings ───────────────────────────────────────────────
    MEETINGS = "meetings"
    PROPOSALS = "proposals"
    AGM_EVOTING = "agm_evoting"
    QUICK_POLLS = "quick_polls"
    WORKFLOW_GOVERNANCE = "workflow_governance"

    # ── Residents / Owners ──────────────────────────────────────────────────
    USER_MANAGEMENT = "user_management"
    OWNER_UNITS_MANAGEMENT = "owner_units_management"
    OWNER_HUB = "owner_hub"
    OWNER_TRANSFERS = "owner_transfers"
    OWNER_NAME_VERIFICATION = "owner_name_verification"
    STRATA_ROLL = "strata_roll"
    TENANT_PORTAL = "tenant_portal"
    TENANT_APPROVALS = "tenant_approvals"
    CHANGE_REQUESTS = "change_requests"
    MULTI_UNIT_OWNERSHIP = "multi_unit_ownership"
    OCCUPANCY_INTELLIGENCE = "occupancy_intelligence"
    TENANCY_PASSPORT = "tenancy_passport"

    # ── Communication ───────────────────────────────────────────────────────
    EMAIL = "email"
    ANNOUNCEMENTS = "announcements"
    CHAT = "chat"
    PRIVATE_MESSAGES = "private_messages"
    DIRECTORY = "directory"
    BOOKINGS = "bookings"
    SMART_REQUESTS = "smart_requests"
    PARCELS = "parcels"

    # ── Market / Analytics ──────────────────────────────────────────────────
    MARKETPLACE = "marketplace"
    STRATA_MARKET_INTELLIGENCE = "strata_market_intelligence"
    BUILDING_RISK_INDEX = "building_risk_index"
    AI_REVIEW_PANEL_ENABLED = "ai_review_panel_enabled"

    # ── Operations (PG-backed routers) ──────────────────────────────────────
    OPS_CASE_MANAGEMENT_PG_ENABLED = "ops_case_management_pg_enabled"
    SUSTAINABILITY_ASSESSMENTS_PG_ENABLED = "sustainability_assessments_pg_enabled"
    ACCESS_DEVICE_LIFECYCLE_PG_ENABLED = "access_device_lifecycle_pg_enabled"
    COMMUNICATIONS_CAMPAIGNS_PG_ENABLED = "communications_campaigns_pg_enabled"
    GOVERNANCE_READ_PG_ENABLED = "governance_read_pg_enabled"


class FeatureToggle(BaseModel):
    """Site-wide feature toggle configuration."""
    model_config = ConfigDict(extra="ignore")

    feature_key: str  # Unique identifier (e.g., "documents", "finance")
    feature_name: str  # Display name (e.g., "Documents Management")
    description: Optional[str] = None
    is_enabled: bool = True  # Site-wide enable/disable
    category: Optional[str] = None  # Group features (e.g., "core", "communication", "financial")
    icon: Optional[str] = None  # Icon name for UI
    routes: list[str] = []  # Associated route patterns
    depends_on: list[str] = []  # Feature keys that must be enabled for this feature to work
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None  # User ID who last updated


class FeatureToggleCreate(BaseModel):
    """Create a new feature toggle."""
    feature_key: str
    feature_name: str
    description: Optional[str] = None
    is_enabled: bool = True
    category: Optional[str] = None
    icon: Optional[str] = None
    routes: list[str] = []
    depends_on: list[str] = []


class FeatureToggleUpdate(BaseModel):
    """Update feature toggle settings."""
    feature_name: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    routes: Optional[list[str]] = None
    depends_on: Optional[list[str]] = None


class FeatureToggleResponse(BaseModel):
    """Feature toggle response."""
    model_config = ConfigDict(extra="ignore")

    id: str
    feature_key: str
    feature_name: str
    description: Optional[str] = None
    is_enabled: bool
    category: Optional[str] = None
    icon: Optional[str] = None
    routes: list[str] = []
    depends_on: list[str] = []
    created_at: str
    updated_at: str
    updated_by: Optional[str] = None


class UserFeatureAccess(BaseModel):
    """Per-user feature access overrides."""
    model_config = ConfigDict(extra="ignore")

    user_id: str
    feature_key: str
    is_enabled: bool  # Override site-wide setting
    granted_by: Optional[str] = None  # Admin who granted access
    granted_at: Optional[datetime] = None
    notes: Optional[str] = None


class UserFeatureAccessCreate(BaseModel):
    """Create user feature access override."""
    user_id: str
    feature_key: str
    is_enabled: bool
    notes: Optional[str] = None


class UserFeatureAccessUpdate(BaseModel):
    """Update user feature access override."""
    is_enabled: bool
    notes: Optional[str] = None


class UserFeatureAccessResponse(BaseModel):
    """User feature access response."""
    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str
    feature_key: str
    is_enabled: bool
    granted_by: Optional[str] = None
    granted_at: str
    notes: Optional[str] = None


class BulkUserFeatureUpdate(BaseModel):
    """Bulk update user feature access."""
    user_ids: list[str]
    feature_key: str
    is_enabled: bool
    notes: Optional[str] = None


class FeatureAccessSummary(BaseModel):
    """Summary of user's feature access."""
    feature_key: str
    feature_name: str
    site_wide_enabled: bool
    user_override: Optional[bool] = None  # None = no override, use site-wide
    effective_access: bool  # Final computed access
    category: Optional[str] = None
