"""
RBAC + ABAC Models

New authorization system models. These work alongside the existing role/Permission
system (fully backward compatible). The existing system continues working unchanged.

Implements:
- Role-Based Access Control (RBAC) via RoleDefinition + RolePermission + UserRoleAssignment
- Attribute-Based / Relationship-Based Access Control (ABAC/ReBAC) via RelationshipTuple
  using a Zanzibar-inspired subject→relation→object triple store.
- Permission slugs as string identifiers (e.g. "financial.view", "workorder.create")
"""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PERMISSION_SLUGS: dict[str, tuple[str, str]] = {
    # Building
    "building.view": ("building", "View building information"),
    "building.update": ("building", "Update building settings"),
    "building.config": ("building", "Configure building system settings"),
    # Financial
    "financial.view": ("financial", "View financial reports and data"),
    "financial.view_own": ("financial", "View own levy and financial data"),
    "financial.manage": ("financial", "Manage financial records"),
    "financial.approve_invoice": ("financial", "Approve invoices"),
    "financial.export": ("financial", "Export financial data"),
    # Work Orders
    "workorder.view": ("workorder", "View work orders"),
    "workorder.create": ("workorder", "Create work orders"),
    "workorder.update": ("workorder", "Update work orders"),
    "workorder.approve": ("workorder", "Approve work orders"),
    # Documents
    "documents.view": ("documents", "View documents"),
    "documents.upload": ("documents", "Upload documents"),
    "documents.delete": ("documents", "Delete documents"),
    "documents.manage": ("documents", "Manage document permissions"),
    # Committee
    "committee.vote": ("committee", "Vote on committee motions"),
    "committee.finalize_minutes": ("committee", "Finalize and publish meeting minutes"),
    "committee.manage": ("committee", "Manage committee operations"),
    # Users
    "users.view": ("users", "View user list"),
    "users.manage": ("users", "Manage users (approve, edit roles)"),
    "users.invite": ("users", "Invite new users"),
    # Announcements
    "announcements.view": ("announcements", "View announcements"),
    "announcements.create": ("announcements", "Create announcements"),
    "announcements.manage": ("announcements", "Manage all announcements"),
    # Meetings
    "meetings.view": ("meetings", "View meetings and agendas"),
    "meetings.manage": ("meetings", "Manage meetings"),
    # Listings (Marketplace)
    "listings.view": ("listings", "View marketplace listings"),
    "listings.create": ("listings", "Create marketplace listings"),
    "listings.manage": ("listings", "Manage all listings"),
    # Maintenance
    "maintenance.request": ("maintenance", "Submit maintenance requests"),
    "maintenance.manage": ("maintenance", "Manage maintenance requests"),
    # Tenancy
    "tenancy.view": ("tenancy", "View tenancy information"),
    "tenancy.manage": ("tenancy", "Manage tenancies"),
    # Chat
    "chat.access": ("chat", "Access community chat"),
    # Notifications
    "notifications.send": ("notifications", "Send notifications to residents"),
    # Schedule
    "schedule.view": ("schedule", "View maintenance schedule"),
    "schedule.manage": ("schedule", "Manage maintenance schedule"),
    # Email
    "email.access": ("email", "Access community email"),
}

# All slugs that exist — convenience set for membership tests.
ALL_PERMISSION_SLUGS: frozenset[str] = frozenset(PERMISSION_SLUGS.keys())

# ---------------------------------------------------------------------------
# Role → permission slug mappings (mirrors DEFAULT_PERMISSIONS in user.py)
# ---------------------------------------------------------------------------

_ALL = list(ALL_PERMISSION_SLUGS)

ROLE_PERMISSION_MAP: dict[str, list[str]] = {
    "super_admin": _ALL,

    # STRATA_ADMIN has every permission except system-level building configuration.
    # Admin of a Strata Management Company (the tenant) — see UserRole in models/user.py.
    "strata_admin": [s for s in _ALL if s != "building.config"],

    # STRATA_MANAGER has the same scope as STRATA_ADMIN.
    "strata_manager": [s for s in _ALL if s != "building.config"],

    "ec_member": [
        "financial.view",
        "financial.manage",
        "workorder.view",
        "workorder.create",
        "workorder.update",
        "workorder.approve",
        "documents.view",
        "documents.upload",
        "committee.vote",
        "committee.finalize_minutes",
        "committee.manage",
        "users.view",
        "users.manage",
        "announcements.view",
        "announcements.create",
        "meetings.view",
        "meetings.manage",
        "listings.view",
        "listings.create",
        "maintenance.request",
        "maintenance.manage",
        "tenancy.view",
        "chat.access",
        "notifications.send",
        "schedule.view",
        "email.access",
    ],

    "owner": [
        "financial.view_own",
        "workorder.view",
        "workorder.create",
        "documents.view",
        "committee.vote",
        "announcements.view",
        "meetings.view",
        "listings.view",
        "listings.create",
        "maintenance.request",
        "tenancy.view",
        "tenancy.manage",
        "chat.access",
        "email.access",
    ],

    "tenant": [
        "workorder.view",
        "documents.view",
        "announcements.view",
        "meetings.view",
        "listings.view",
        "listings.create",
        "maintenance.request",
        "chat.access",
    ],

    "real_estate_agent": [
        "workorder.view",
        "documents.view",
        "documents.upload",
        "listings.view",
        "listings.create",
        "listings.manage",
        "maintenance.request",
        "maintenance.manage",
        "tenancy.view",
        "tenancy.manage",
        "chat.access",
        "schedule.view",
    ],

    "admin_staff": [
        "announcements.view",
        "documents.view",
        "meetings.view",
        "maintenance.request",
        "maintenance.manage",
        "tenancy.view",
        "tenancy.manage",
        "workorder.view",
        "workorder.approve",
        "chat.access",
        "schedule.view",
    ],

    "service_provider": [
        "workorder.view",
        "workorder.update",
        "chat.access",
        "schedule.view",
    ],

    "guest": [
        "announcements.view",
    ],
}

# ---------------------------------------------------------------------------
# Relation types used in RelationshipTuple (Zanzibar-style)
# ---------------------------------------------------------------------------

RELATION_TYPES: list[str] = [
    "owner_of",
    "tenant_of",
    "agent_of",
    "manager_of",
    "committee_member",
    "chairman",
    "treasurer",
    "secretary",
    "created_by",
    "assigned_to",
    "belongs_to",
    "part_of",
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _new_id() -> str:
    """Return a new UUID4 string."""
    return str(uuid4())


def _utcnow() -> datetime:
    """Generated function header.

    Function: _utcnow
    Path: backend/models/rbac_models.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core RBAC models
# ---------------------------------------------------------------------------


class PermissionSlug(BaseModel):
    """
    A single named permission that can be granted to roles.

    Slugs follow the ``<category>.<action>`` convention, e.g. ``financial.view``.
    They are stored in the ``permission_slugs`` MongoDB collection and seeded
    from :data:`PERMISSION_SLUGS` at startup.
    """

    id: str = Field(default_factory=_new_id, description="UUID primary key")
    slug: str = Field(..., description="Dot-separated permission identifier, e.g. 'financial.view'")
    description: str = Field(..., description="Human-readable description of what this permission grants")
    category: str = Field(
        ...,
        description=(
            "Top-level grouping for the permission "
            "(e.g. 'financial', 'workorder', 'documents', 'committee', "
            "'building', 'users', 'announcements', 'meetings')"
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow)


class RoleDefinition(BaseModel):
    """
    Defines a named role in the RBAC system.

    ``is_base_role`` distinguishes primary identity roles (OWNER, TENANT, GUEST …)
    from elevated/committee roles (EC_MEMBER, CHAIRMAN …).  Elevated roles may
    optionally *inherit* all permissions from a base role via ``inherits_from``.
    """

    id: str = Field(default_factory=_new_id, description="UUID primary key")
    name: str = Field(..., description="Internal role key, e.g. 'OWNER' or 'CHAIRMAN'")
    display_name: str = Field(..., description="User-facing role label")
    is_base_role: bool = Field(
        ...,
        description="True for primary identity roles (OWNER, TENANT, etc.); False for elevated roles",
    )
    inherits_from: Optional[str] = Field(
        None,
        description="Role id whose permissions are inherited (used for elevated roles)",
    )
    description: str = Field(..., description="What responsibilities this role represents")
    created_at: datetime = Field(default_factory=_utcnow)


class RolePermission(BaseModel):
    """
    Join record granting a specific :class:`PermissionSlug` to a :class:`RoleDefinition`.

    Stored in the ``role_permissions`` MongoDB collection.
    """

    id: str = Field(default_factory=_new_id, description="UUID primary key")
    role_id: str = Field(..., description="References RoleDefinition.id")
    permission_slug: str = Field(..., description="References PermissionSlug.slug")
    created_at: datetime = Field(default_factory=_utcnow)


class UserRoleAssignment(BaseModel):
    """
    Assigns a role to a user, optionally scoped to a building or unit.

    Supports time-bounded assignments (e.g. a term as EC member) via
    ``start_date`` / ``end_date``.  ``is_active`` can be used to soft-revoke
    without deleting the audit record.
    """

    id: str = Field(default_factory=_new_id, description="UUID primary key")
    user_id: str = Field(..., description="The user receiving the role")
    role_id: str = Field(..., description="References RoleDefinition.id")
    building_id: Optional[str] = Field(None, description="Scope assignment to a specific building")
    unit_id: Optional[str] = Field(None, description="Scope assignment to a specific unit")
    start_date: Optional[datetime] = Field(None, description="When the assignment becomes effective")
    end_date: Optional[datetime] = Field(None, description="When the assignment expires (None = indefinite)")
    is_active: bool = Field(True, description="False after revocation; record is kept for audit")
    assigned_by: Optional[str] = Field(None, description="user_id of the admin who made this assignment")
    notes: Optional[str] = Field(None, description="Free-text reason or context for the assignment")
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Zanzibar-style ReBAC model
# ---------------------------------------------------------------------------


class RelationshipTuple(BaseModel):
    """
    A Zanzibar-inspired subject → relation → object triple.

    Examples::

        subject="user:87"  relation="owner_of"        object="unit:45"
        subject="unit:45"  relation="belongs_to"       object="building:101"
        subject="user:12"  relation="committee_member" object="building:101"

    ``building_id`` is **always** populated to support tenant isolation — queries
    can filter by building without parsing the object string.
    """

    id: str = Field(default_factory=_new_id, description="UUID primary key")
    subject: str = Field(
        ...,
        description="Entity holding the relation, prefixed by type, e.g. 'user:87' or 'unit:45'",
    )
    relation: str = Field(
        ...,
        description=(
            "Relationship type; see RELATION_TYPES — e.g. 'owner_of', "
            "'tenant_of', 'committee_member', 'chairman'"
        ),
    )
    object: str = Field(
        ...,
        description="Entity the relation points to, e.g. 'unit:45' or 'building:101'",
    )
    building_id: Optional[str] = Field(
        None,
        description="Always set for multi-tenancy queries; enables fast building-scoped lookups",
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="When this relationship expires (None = permanent)",
    )
    is_active: bool = Field(True, description="False after soft-deletion; kept for audit")
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Request / response models for the permission-check API
# ---------------------------------------------------------------------------


class PermissionCheckRequest(BaseModel):
    """
    Payload for ``POST /api/rbac/check`` — asks whether a user may perform
    an action, optionally scoped to a specific object and building.
    """

    user_id: str = Field(..., description="The user whose access is being evaluated")
    action: str = Field(..., description="Permission slug to test, e.g. 'financial.view'")
    object_id: Optional[str] = Field(
        None,
        description="Specific resource id the action targets (omit for global checks)",
    )
    building_id: Optional[str] = Field(
        None,
        description="Restrict evaluation to a specific building",
    )


class PermissionCheckResponse(BaseModel):
    """
    Result returned by the permission-check endpoint.

    ``matched_roles`` and ``matched_relations`` explain *why* access was
    granted or denied — useful for debugging and audit logging.
    """

    allowed: bool = Field(..., description="True if the action is permitted")
    reason: str = Field(..., description="Human-readable explanation of the decision")
    matched_roles: List[str] = Field(
        default_factory=list,
        description="Role ids/names that contributed to granting access",
    )
    matched_relations: List[str] = Field(
        default_factory=list,
        description="Relationship tuple ids that contributed to granting access",
    )


# ---------------------------------------------------------------------------
# Write request models (used by management endpoints)
# ---------------------------------------------------------------------------


class UserRoleAssignRequest(BaseModel):
    """
    Request body for ``POST /api/rbac/users/{user_id}/roles`` — assigns a role
    to a user, optionally scoped and time-bounded.
    """

    role_id: str = Field(..., description="References RoleDefinition.id to assign")
    building_id: Optional[str] = Field(None, description="Scope to a specific building")
    unit_id: Optional[str] = Field(None, description="Scope to a specific unit")
    start_date: Optional[datetime] = Field(None, description="Effective from this date")
    end_date: Optional[datetime] = Field(None, description="Expires on this date")
    notes: Optional[str] = Field(None, description="Reason or context for the assignment")


class RelationshipCreateRequest(BaseModel):
    """
    Request body for ``POST /api/rbac/relationships`` — creates a new
    Zanzibar-style relationship tuple.
    """

    subject: str = Field(
        ...,
        description="Entity holding the relation, e.g. 'user:87' or 'unit:45'",
    )
    relation: str = Field(
        ...,
        description="Relationship type from RELATION_TYPES, e.g. 'owner_of'",
    )
    object: str = Field(
        ...,
        description="Entity the relation points to, e.g. 'unit:45' or 'building:101'",
    )
    building_id: Optional[str] = Field(
        None,
        description="Always recommended; enables building-scoped tenant isolation",
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="Optional expiry for temporary relationships",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "PERMISSION_SLUGS",
    "ALL_PERMISSION_SLUGS",
    "ROLE_PERMISSION_MAP",
    "RELATION_TYPES",
    # Core models
    "PermissionSlug",
    "RoleDefinition",
    "RolePermission",
    "UserRoleAssignment",
    "RelationshipTuple",
    # Request / response
    "PermissionCheckRequest",
    "PermissionCheckResponse",
    "UserRoleAssignRequest",
    "RelationshipCreateRequest",
]
