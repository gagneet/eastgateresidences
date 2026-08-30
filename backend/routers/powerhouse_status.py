# @featuretrace:powerhouse-ui-navigation — Read-only Powerhouse feature visibility/status endpoint.
# Layer: router
# Data flow: PowerhouseControlCentrePage -> GET /api/features/powerhouse/status -> config_repo feature toggles (building-scoped).
# Related: frontend/src/lib/powerhouseFeatureCatalogue.ts
#          frontend/src/pages/powerhouse/PowerhouseControlCentrePage.tsx
#          docs/architecture/powerhouse-ui-navigation.md

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from db_postgres.repos import config_repo
from utils.auth import get_current_building, get_current_user

router = APIRouter(prefix="/features/powerhouse", tags=["Powerhouse Visibility"])

INTERNAL_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}

POWERHOUSE_FEATURES: list[dict[str, Any]] = [
    {
        "key": "powerhouse_control_centre",
        "displayName": "Powerhouse Control Centre",
        "description": "Role-aware index for Powerhouse readiness, routes, toggles, and safe entry points.",
        "status": "internal_preview",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "scope": "internal",
        "requiredToggles": [],
        "routePath": "/powerhouse",
        "apiRoute": "/api/features/powerhouse/status",
        "icon": "Gauge",
        "category": "control",
        "documentationLink": "/docs/architecture/powerhouse-ui-navigation.md",
        "supportMessage": "Use this page to inspect readiness; do not promote features from here.",
        "disabledReason": "Visible only to internal operating roles.",
        "riskLabel": "medium",
        "nextRequiredStep": "Keep this catalogue aligned with FeatureTrace and router maps.",
    },
    {
        "key": "cutover_control_plane",
        "displayName": "Cutover Control",
        "description": "Internal MongoDB to PostgreSQL cutover status and controlled promotion/rollback surface.",
        "status": "internal_preview",
        "allowedRoles": ["super_admin"],
        "scope": "internal",
        "requiredToggles": [],
        "routePath": "/admin/cutover-status",
        "apiRoute": "/api/admin/cutover/status",
        "icon": "GitBranch",
        "category": "control",
        "documentationLink": "/docs/migration/powerhouse-prompt-2-cutover-control-report.md",
        "supportMessage": "Super-admin only. Requires an explicit cutover plan before any action.",
        "disabledReason": "Unsafe for non-operator roles.",
        "riskLabel": "critical",
        "nextRequiredStep": "Use the cutover runbook and audit log before promotion or rollback.",
    },
    {
        "key": "finance_postgres_readiness",
        "displayName": "Finance Readiness",
        "description": "Finance shadow, read, write, and PostgreSQL cutover readiness panels.",
        "status": "internal_preview",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": ["financial_shadow_reads_enabled", "financial_pg_reads_enabled", "financial_pg_writes_enabled"],
        "routePath": "/admin/cutover-status",
        "apiRoute": "/api/admin/cutover/status-finance-routes/{building_id}",
        "icon": "Database",
        "category": "finance",
        "documentationLink": "/docs/migration/powerhouse-prompt-6-finance-postgres-read-cutover-report.md",
        "supportMessage": "Readiness visibility only; finance promotion remains controlled by the cutover plane.",
        "disabledReason": "Building has not passed finance readiness gates.",
        "riskLabel": "critical",
        "nextRequiredStep": "Confirm shadow/read/write readiness for the selected building.",
    },
    {
        "key": "powerhouse_command_foundation_health",
        "displayName": "Command Foundation Health",
        "description": "Outbox delivery, idempotency, and per-domain PostgreSQL cutover readiness for the Powerhouse command foundation.",
        "status": "internal_preview",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": [],
        "routePath": "/powerhouse",
        "apiRoute": "/api/admin/cutover/powerhouse-command-foundation/{building_id}",
        "icon": "Activity",
        "category": "control",
        "documentationLink": "/docs/features/powerhouse_p2a_deep_dive_audit_2026-07-20.md",
        "supportMessage": "Diagnostic only. Confirms outbox/idempotency plumbing is healthy; never promotes or mutates a domain.",
        "disabledReason": "Command foundation diagnostics are hidden from resident and committee roles.",
        "riskLabel": "medium",
        "nextRequiredStep": "Wire a real read path through this foundation (P2B) before any domain leaves mongo_primary.",
    },
    {
        "key": "feature_toggle_status",
        "displayName": "Feature Toggle Status",
        "description": "Read-only diagnostic view of Powerhouse toggle defaults, building state, and role visibility.",
        "status": "internal_preview",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": [],
        "routePath": "/powerhouse",
        "apiRoute": "/api/features/powerhouse/status",
        "icon": "ToggleLeft",
        "category": "control",
        "documentationLink": "/docs/architecture/feature-toggle-governance.md",
        "supportMessage": "Toggle mutation remains in the existing admin feature-toggle system.",
        "disabledReason": "Diagnostics are hidden from resident roles.",
        "riskLabel": "medium",
        "nextRequiredStep": "Use the admin feature-toggle page for approved per-building overrides.",
    },
    {
        "key": "powerhouse_conversations",
        "displayName": "Conversation Centre",
        "description": "Shell conversation centre for controlled request and committee communication workflows.",
        "status": "shell_only",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "owner", "tenant", "guest"],
        "scope": "building",
        "requiredToggles": ["powerhouse_conversations"],
        "routePath": "/powerhouse/conversations",
        "apiRoute": "/api/powerhouse/conversations/threads",
        "icon": "MessagesSquare",
        "category": "communications",
        "documentationLink": "/docs/architecture/powerhouse-ui-navigation.md",
        "supportMessage": "Available only when enabled for the building and role.",
        "disabledReason": "The umbrella Powerhouse conversations toggle is disabled by default.",
        "riskLabel": "high",
        "nextRequiredStep": "Complete privacy, participant-scope, and production data readiness checks.",
    },
    {
        "key": "powerhouse_shared_inbox",
        "displayName": "Shared Inbox",
        "description": "Manager-facing inbox configuration and intake shell.",
        "status": "shell_only",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": ["powerhouse_conversations", "powerhouse_shared_inbox"],
        "routePath": "/powerhouse/inbox-settings",
        "apiRoute": "/api/powerhouse/inboxes",
        "icon": "Inbox",
        "category": "communications",
        "documentationLink": "/docs/architecture/feature-toggle-governance.md",
        "supportMessage": "Do not expose to owners or tenants until provider send/intake is production-ready.",
        "disabledReason": "Shared inbox remains a manager-only shell.",
        "riskLabel": "high",
        "nextRequiredStep": "Complete provider validation and inbox send safety checks.",
    },
    {
        "key": "powerhouse_email_intake",
        "displayName": "Email Intake",
        "description": "Inbound email webhook and thread mapping foundation.",
        "status": "internal_preview",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": ["powerhouse_conversations", "powerhouse_shared_inbox", "powerhouse_email_intake"],
        "routePath": None,
        "apiRoute": "/api/powerhouse/inboxes/{inbox_id}/inbound-email",
        "icon": "Mail",
        "category": "communications",
        "documentationLink": "/docs/architecture/feature-toggle-governance.md",
        "supportMessage": "No direct navigation until webhook auth and provider mapping are complete.",
        "disabledReason": "Internal API preview only; no safe user-facing page yet.",
        "riskLabel": "critical",
        "nextRequiredStep": "Add signed provider webhook verification before broad use.",
    },
    {
        "key": "powerhouse_ai_summary",
        "displayName": "AI Summary",
        "description": "AI summary and response draft placeholder with mandatory human review.",
        "status": "shell_only",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": ["powerhouse_conversations", "powerhouse_ai_summary"],
        "routePath": "/powerhouse/conversations",
        "apiRoute": "/api/powerhouse/conversations/threads/{thread_id}/ai-summary",
        "icon": "Sparkles",
        "category": "communications",
        "documentationLink": "/docs/architecture/feature-toggle-governance.md",
        "supportMessage": "Shown as placeholder only; never auto-sends or auto-decides.",
        "disabledReason": "No production AI provider or privacy consent gate is enabled.",
        "riskLabel": "critical",
        "nextRequiredStep": "Complete prompt-injection, PII, and human-approval hardening.",
    },
    {
        "key": "powerhouse_workflow_engine",
        "displayName": "Workflow Queue",
        "description": "Workflow instance shell for manager and committee action queues.",
        "status": "shell_only",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "scope": "building",
        "requiredToggles": ["powerhouse_conversations", "powerhouse_workflow_engine"],
        "routePath": None,
        "apiRoute": "/api/powerhouse/workflows/instances",
        "icon": "ListChecks",
        "category": "workflow",
        "documentationLink": "/docs/architecture/workflow-event-engine.md",
        "supportMessage": "Dynamic workflow detail links are not exposed until instances exist.",
        "disabledReason": "Workflow engine is shell-only and hidden from residents.",
        "riskLabel": "high",
        "nextRequiredStep": "Add queue listing page before adding direct navigation.",
    },
    {
        "key": "powerhouse_automation_rules",
        "displayName": "Workflow Automation",
        "description": "Automation rule run shell for internal workflow testing.",
        "status": "shell_only",
        "allowedRoles": ["super_admin", "strata_admin", "strata_manager"],
        "scope": "building",
        "requiredToggles": ["powerhouse_conversations", "powerhouse_workflow_engine", "powerhouse_automation_rules"],
        "routePath": "/powerhouse/automation",
        "apiRoute": "/api/powerhouse/workflows/automation-rule-runs",
        "icon": "Bot",
        "category": "workflow",
        "documentationLink": "/docs/architecture/powerhouse-ui-navigation.md",
        "supportMessage": "Internal preview only; automation must not act without explicit operator review.",
        "disabledReason": "Automation rules are disabled by default and manager-only.",
        "riskLabel": "critical",
        "nextRequiredStep": "Complete rule execution audit, rollback, and approval gates.",
    },
    {
        "key": "future_mongo_archive_controls",
        "displayName": "Future Mongo Archive Controls",
        "description": "Future controls for read-only Mongo archive after domain cutover.",
        "status": "disabled",
        "allowedRoles": ["super_admin"],
        "scope": "internal",
        "requiredToggles": [],
        "routePath": None,
        "apiRoute": None,
        "icon": "Archive",
        "category": "archive",
        "documentationLink": "/docs/migration/mongo-postgres-coexistence.md",
        "supportMessage": "No UI or API exists yet; track only as a future cutover requirement.",
        "disabledReason": "Not implemented. Do not add navigation until Prompt 8 or later explicitly scopes it.",
        "riskLabel": "critical",
        "nextRequiredStep": "Define archive controls after production cutover policy is approved.",
    },
]


def _effective_role(user: dict) -> str:
    """Generated function header.

    Function: _effective_role
    Path: backend/routers/powerhouse_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return user.get("effective_role") or user.get("role", "guest")


def _is_internal(role: str) -> bool:
    """Generated function header.

    Function: _is_internal
    Path: backend/routers/powerhouse_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return role in INTERNAL_ROLES


async def _toggle_state(building_id: str, feature_key: str) -> tuple[bool, bool | None]:
    """Generated function header.

    Function: _toggle_state
    Path: backend/routers/powerhouse_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    global_toggle = await config_repo.get_global_feature_toggle(feature_key)
    global_default = bool(global_toggle.get("is_enabled")) if global_toggle else False
    enabled = await config_repo.resolve_feature_toggle(building_id, feature_key, default=global_default)
    return bool(enabled), global_default


async def build_powerhouse_status(user: dict, building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: build_powerhouse_status
    Path: backend/routers/powerhouse_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = _effective_role(user)
    internal = _is_internal(role)
    features: list[dict[str, Any]] = []

    for feature in POWERHOUSE_FEATURES:
        visible_for_role = role in feature["allowedRoles"]
        if not visible_for_role:
            continue
        if not internal and feature["scope"] == "internal":
            continue

        enabled = True
        global_defaults: list[bool] = []
        for toggle_key in feature["requiredToggles"]:
            toggle_enabled, global_default = await _toggle_state(building_id, toggle_key)
            enabled = enabled and toggle_enabled
            global_defaults.append(global_default)

        reason = "" if enabled else feature["disabledReason"]
        item = {
            **feature,
            "enabled": enabled,
            "currentStatus": "enabled" if enabled else "disabled",
            "visibleForRole": visible_for_role,
            "reason": reason,
            "buildingScope": building_id,
        }
        if internal:
            item["globalDefault"] = all(global_defaults) if global_defaults else None
            item["buildingOverride"] = enabled if feature["requiredToggles"] else None
        else:
            item.pop("apiRoute", None)
            item.pop("nextRequiredStep", None)

        features.append(item)

    return {
        "building_id": building_id,
        "role": role,
        "diagnostic": internal,
        "features": features,
    }


@router.get("/status")
async def get_powerhouse_status(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_powerhouse_status
    Path: backend/routers/powerhouse_status.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await build_powerhouse_status(current_user, building_id)
