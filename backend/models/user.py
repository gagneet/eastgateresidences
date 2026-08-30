"""
User-related Pydantic models.

Contains models for user authentication, roles, permissions, and user management.
These models define the structure of user data exchanged between the frontend and backend.
"""

from pydantic import BaseModel, EmailStr, ConfigDict, Field
from typing import Optional, Dict, List


class UserStatus:
    """Lifecycle status of a user account."""
    ACTIVE = "active"
    PENDING_OWNER_APPROVAL = "pending_owner_approval"  # Tenant/Guest awaiting owner confirmation
    INFO_REQUESTED = "info_requested"  # Admin requested registration correction
    ARCHIVED = "archived"  # Superseded owner/tenant; soft-deleted


class UserRole:
    """Enumeration of user roles within the strata management platform.

    Roles map onto the org model as follows::

        StrataOS Platform                  — super_admin
        Strata Management Company (tenant) — strata_admin       (manages the tenant)
                                            strata_manager      (employed; manages buildings)
                                            admin_staff         (back office)
        Building / Scheme                  — ec_member           (volunteer EC, incl. Chairman role)
                                            owner / tenant
                                            real_estate_agent
                                            service_provider
                                            guest
    """
    SUPER_ADMIN = "super_admin"
    STRATA_ADMIN = "strata_admin"  # Admin of a Strata Management Company (the tenant)
    EC_MEMBER = "ec_member"  # Executive Committee Member (ec_position: CHAIRMAN/TREASURER/SECRETARY/MEMBER)
    STRATA_MANAGER = "strata_manager"  # Professional Strata Manager employed by a Strata Management Company
    ADMIN_STAFF = "admin_staff"  # Building admin staff with request handling access
    RECEPTION = ADMIN_STAFF  # Legacy alias kept for backwards-compatible imports
    OWNER = "owner"  # Property Owner
    TENANT = "tenant"  # Resident Tenant
    REAL_ESTATE_AGENT = "real_estate_agent"  # Agent managing specific units on behalf of owners
    SERVICE_PROVIDER = "service_provider"  # External Contractor or Service Provider
    GUEST = "guest"  # Temporary visitor or prospective resident


def normalize_user_role(role: Optional[str]) -> str:
    """Map UI aliases to the canonical runtime role.

    ``reception`` is a UI label that resolves to ``admin_staff``. All other
    inputs pass through unchanged.

    Note: the legacy ``building_admin`` and ``chairman`` slugs are no longer
    handled here. Migration 0025 renamed the Postgres enum value, the Mongo
    identity collections were dropped, and seed data uses the new slugs —
    so no live source of those old strings remains. If they ever appear at
    runtime, they pass through unchanged and downstream role-set checks
    treat them as unknown roles, which is the correct loud-failure behaviour.
    """
    if role == "reception":
        return UserRole.ADMIN_STAFF
    return role or UserRole.GUEST


class ECPosition:
    """Sub-roles for EC members and appointed internal managers.

    CHAIRMAN/TREASURER/SECRETARY/MEMBER are volunteer EC office bearer positions.
    STRATA_MANAGER/BUILDING_MANAGER are appointment-based positions that require
    an explicit core.scheme_manager_appointments row (appointment_type =
    'ec_internal_strata_manager' or 'building_manager') and EC approval before
    granting manager-level building operations access.
    """
    CHAIRMAN = "CHAIRMAN"
    TREASURER = "TREASURER"
    SECRETARY = "SECRETARY"
    MEMBER = "MEMBER"
    # Appointment-based (requires scheme_manager_appointments row + EC approval)
    STRATA_MANAGER = "STRATA_MANAGER"
    BUILDING_MANAGER = "BUILDING_MANAGER"

    # Ordered set of all valid positions for validation helpers
    ALL: tuple[str, ...] = (
        "CHAIRMAN", "TREASURER", "SECRETARY", "MEMBER",
        "STRATA_MANAGER", "BUILDING_MANAGER",
    )

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Generated function header.

        Function: ECPosition.is_valid
        Path: backend/models/user.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return value in cls.ALL

    @classmethod
    def requires_appointment(cls, value: str) -> bool:
        """True for positions that need a scheme_manager_appointments row."""
        return value in {"STRATA_MANAGER", "BUILDING_MANAGER"}


class ManagerFunction:
    """What an individual on the managing agent's team actually DOES.

    Why this is a sub-role and not a role
    -------------------------------------
    A strata management agency fields specialists — East Gate's Civium team is a
    Strata Manager, a Levies Manager, an Insurance Manager and a Maintenance
    Manager — but none of them is a separate legal office.

    Under the Unit Titles (Management) Act 2011 (ACT) s 58, the owners corporation
    (or its executive committee) delegates functions **to the manager**: one legal
    person, the appointed managing agent. There is no statutory delegation to a
    "levies manager". The code of conduct in sch 1 pt 1.2 confirms the direction of
    authority — a manager must take reasonable steps to ensure that the manager's
    EMPLOYEES comply with the Act "when exercising the manager's functions". The
    functions are the manager's; staff exercise them derivatively.

    Licensing points the same way. Access Canberra's guide to the obligations of
    owners corporation managers: "Individual managers working for a licensed real
    estate agent or licensed owners corporation manager will not be required to hold
    a licence." The AGENCY holds the licence; the specialists do not hold one each.

    So a functional title confers no authority of its own, and must never become a
    top-level UserRole — a new role would be a new trust boundary the legislation
    does not recognise, and (as `chairman` did before migration 0025) it would drift
    into role guards as a bare string that nothing validates.

    Direction of effect: NARROWING, never widening
    ----------------------------------------------
    A function can only ever be a subset of what `strata_manager` already grants.
    The basis is Privacy Act 1988 (Cth) APP 6 (use limited to the purpose of
    collection) and APP 11 (protection against unauthorised access), plus the
    agent's confidentiality duty under the Agents Regulation 2003 rules of conduct:
    a levies clerk has no purpose that requires reading WHS incident reports.

    Whether a function is ENFORCED is a separate, per-agency decision — narrowing an
    existing team's access silently 403s people mid-shift. Today these values are
    descriptive: they record who does what, and they are the vocabulary the guards
    will read when an agency opts in. See
    docs/architecture/strata_management_staff_access_model.md.

    Trust money is the one hard line
    --------------------------------
    Under the Agents Act 2003 (ACT) pt 7 the duties attaching to trust money — the
    separate trust account, quarterly statements to the regulator, the annual audit,
    recording the material details of every transaction — sit on the LICENSED AGENT.
    LEVIES_MANAGER describes someone who works levies; it is not a grant of trust
    account authority, and no function value may be treated as one.
    """

    #: The appointed manager themselves — the agency's named strata manager.
    STRATA_MANAGER = "STRATA_MANAGER"
    #: On-site building/facilities manager. Already an ECPosition; named here too so
    #: one vocabulary covers the whole team.
    BUILDING_MANAGER = "BUILDING_MANAGER"
    #: Levy issue, receipting, arrears follow-up. NOT trust-account authority.
    LEVIES_MANAGER = "LEVIES_MANAGER"
    #: Policy placement, renewals, claims.
    INSURANCE_MANAGER = "INSURANCE_MANAGER"
    #: Repairs, contractors, defects, WHS and the compliance registers.
    MAINTENANCE_MANAGER = "MAINTENANCE_MANAGER"

    ALL: tuple[str, ...] = (
        "STRATA_MANAGER",
        "BUILDING_MANAGER",
        "LEVIES_MANAGER",
        "INSURANCE_MANAGER",
        "MAINTENANCE_MANAGER",
    )

    #: core.scheme_manager_appointments.appointment_type for each function.
    #: STRATA_MANAGER has no single mapping on purpose — how a strata manager is
    #: engaged (agency / independent / EC-internal / owner volunteer) is a different
    #: axis from what they do, and the caller already knows which applies.
    APPOINTMENT_TYPE: dict[str, str] = {
        "BUILDING_MANAGER": "building_manager",
        "LEVIES_MANAGER": "levies_manager",
        "INSURANCE_MANAGER": "insurance_manager",
        "MAINTENANCE_MANAGER": "maintenance_manager",
    }

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """True if `value` is a known manager function."""
        return value in cls.ALL


class Permission(BaseModel):
    """
    Detailed permission flags for granular access control.
    Determines which features and actions a user can access.
    """
    model_config = ConfigDict(extra="ignore")
    can_view_documents: bool = False
    can_upload_documents: bool = False
    can_manage_document_permissions: bool = False  # Allows setting who can see specific documents
    can_view_finances: bool = False
    can_manage_finances: bool = False
    can_create_listings: bool = False
    can_chat: bool = False
    can_view_meetings: bool = False
    can_manage_meetings: bool = False
    can_manage_users: bool = False
    can_view_schedule: bool = False
    can_post_announcements: bool = False
    can_send_notifications: bool = False  # Allows sending levy reminders and other system notifications
    can_access_email: bool = False  # Allows access to community email account
    can_manage_requests: bool = False  # Allows managing building requests
    can_manage_access_device_settings: bool = False  # Allows editing access-device pricing and request rules
    can_manage_tenancy: bool = False  # Allows managing tenancies and rental properties
    can_submit_supplier_invoice: bool = False  # Allows supplier invoice upload to AP queue
    # Added 2026-08-23. Both keys were already referenced as navigation
    # permission_flags in seeds/navigation_configs.py but existed on no model and
    # on no user document, so navigation.py's `can_flags.get(pf, False)` always
    # resolved False and hid the items from EVERY user, super_admin included.
    can_manage_settings: bool = False   # Building/organisation settings and platform config screens
    can_view_audit: bool = False        # Platform audit log (/admin/audit-logs)


# Default permissions mapped to each user role
DEFAULT_PERMISSIONS = {
    UserRole.SUPER_ADMIN: Permission(
        can_view_documents=True, can_upload_documents=True, can_manage_document_permissions=True,
        can_view_finances=True, can_manage_finances=True, can_create_listings=True, can_chat=True,
        can_view_meetings=True, can_manage_meetings=True, can_manage_users=True,
        can_view_schedule=True, can_post_announcements=True, can_send_notifications=True, can_access_email=True,
        can_manage_requests=True, can_manage_access_device_settings=True, can_manage_tenancy=True,
        can_submit_supplier_invoice=True, can_manage_settings=True, can_view_audit=True
    ),
    UserRole.STRATA_ADMIN: Permission(
        can_view_documents=True, can_upload_documents=True, can_manage_document_permissions=True,
        can_view_finances=True, can_manage_finances=True, can_create_listings=True, can_chat=True,
        can_view_meetings=True, can_manage_meetings=True, can_manage_users=True,
        can_view_schedule=True, can_post_announcements=True, can_send_notifications=True, can_access_email=True,
        can_manage_requests=True, can_manage_access_device_settings=True, can_manage_tenancy=True,
        can_submit_supplier_invoice=True, can_manage_settings=True
    ),
    UserRole.STRATA_MANAGER: Permission(
        can_view_documents=True, can_upload_documents=True, can_manage_document_permissions=True,
        can_view_finances=True, can_manage_finances=True, can_create_listings=True, can_chat=True,
        can_view_meetings=True, can_manage_meetings=True, can_manage_users=True,
        can_view_schedule=True, can_post_announcements=True, can_send_notifications=True, can_access_email=True,
        can_manage_requests=True, can_manage_access_device_settings=True, can_manage_tenancy=True,
        can_submit_supplier_invoice=True, can_manage_settings=True
    ),
    UserRole.EC_MEMBER: Permission(
        can_view_documents=True, can_upload_documents=True, can_manage_document_permissions=False,
        can_view_finances=True, can_manage_finances=True, can_create_listings=True, can_chat=True,
        can_view_meetings=True, can_manage_meetings=True, can_manage_users=True,
        can_view_schedule=True, can_post_announcements=True, can_send_notifications=True, can_access_email=True,
        can_manage_requests=True, can_manage_tenancy=False, can_submit_supplier_invoice=True
    ),
    # can_manage_users=True is required for the resident-onboarding approval flow:
    # admin_staff are notified as registration reviewers (_STAFF_REVIEWER_ROLES in
    # routers/auth.py), and both GET /users and PUT /users/{id} gate on this
    # permission — without it they receive an approval email whose link 403s.
    # Note this also grants them the rest of user management (create/edit users,
    # change roles, archive accounts, and unmasked resident PII via GET /users).
    # Privilege escalation is still blocked separately: only a super_admin may
    # assign the super_admin role, and system-administrator accounts are protected.
    UserRole.ADMIN_STAFF: Permission(
        can_view_documents=True, can_upload_documents=False, can_manage_document_permissions=False,
        can_view_finances=False, can_manage_finances=False, can_create_listings=False, can_chat=True,
        can_view_meetings=True, can_manage_meetings=False, can_manage_users=True,
        can_view_schedule=True, can_post_announcements=False, can_send_notifications=False, can_access_email=False,
        can_manage_requests=True, can_manage_tenancy=True
    ),
    UserRole.OWNER: Permission(
        can_view_documents=True, can_upload_documents=False, can_manage_document_permissions=False,
        can_view_finances=True, can_manage_finances=False, can_create_listings=True, can_chat=True,
        can_view_meetings=True, can_manage_meetings=False, can_manage_users=False,
        can_view_schedule=False, can_post_announcements=False, can_send_notifications=False, can_access_email=True,
        can_manage_requests=False, can_manage_tenancy=True
    ),
    UserRole.TENANT: Permission(
        can_view_documents=True, can_upload_documents=False, can_manage_document_permissions=False,
        can_view_finances=False, can_manage_finances=False, can_create_listings=True, can_chat=True,
        can_view_meetings=True, can_manage_meetings=False, can_manage_users=False,
        can_view_schedule=False, can_post_announcements=False, can_send_notifications=False,
        can_manage_requests=False, can_manage_tenancy=False
    ),
    UserRole.REAL_ESTATE_AGENT: Permission(
        can_view_documents=True, can_upload_documents=True, can_manage_document_permissions=False,
        can_view_finances=False, can_manage_finances=False, can_create_listings=True, can_chat=True,
        can_view_meetings=False, can_manage_meetings=False, can_manage_users=False,
        can_view_schedule=True, can_post_announcements=False, can_send_notifications=False, can_access_email=False,
        can_manage_requests=True, can_manage_tenancy=True
    ),
    UserRole.SERVICE_PROVIDER: Permission(
        can_view_documents=False, can_upload_documents=False, can_view_finances=False,
        can_manage_finances=False, can_create_listings=False, can_chat=True,
        can_view_meetings=False, can_manage_meetings=False, can_manage_users=False,
        can_view_schedule=True, can_post_announcements=False, can_send_notifications=False,
        can_manage_requests=False, can_manage_tenancy=False, can_submit_supplier_invoice=True
    ),
    UserRole.GUEST: Permission(
        can_view_documents=False, can_upload_documents=False, can_view_finances=False,
        can_manage_finances=False, can_create_listings=False, can_chat=False,
        can_view_meetings=False, can_manage_meetings=False, can_manage_users=False,
        can_view_schedule=False, can_post_announcements=False, can_send_notifications=False,
        can_manage_requests=False, can_manage_tenancy=False
    )
}

DEFAULT_PERMISSIONS["reception"] = DEFAULT_PERMISSIONS[UserRole.ADMIN_STAFF]


class UserBase(BaseModel):
    """Base user data common to most models."""
    email: EmailStr
    full_name: str
    unit_number: Optional[str] = None
    phone: Optional[str] = None
    role: str = UserRole.GUEST
    is_active: bool = True
    is_approved: bool = False
    profile_image: Optional[str] = None


class UserCreate(BaseModel):
    """Model for new user registration."""
    model_config = ConfigDict(extra="ignore")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., max_length=200)
    unit_number: Optional[str] = Field(None, max_length=20)
    additional_unit_numbers: List[str] = Field(
        default_factory=list,
        description="Additional units owned by this user in the same building (owners only, max 10)",
        max_length=10,
    )
    phone: Optional[str] = Field(None, max_length=20)
    role: str = UserRole.GUEST
    end_date: Optional[str] = None  # Expected end of stay (for Guests)
    by_laws_acknowledged: Optional[bool] = False
    terms_accepted: Optional[bool] = False
    invite_token: Optional[str] = Field(None, max_length=200)


class UserLogin(BaseModel):
    """Model for user login credentials."""
    email: EmailStr
    password: str = Field(..., max_length=128)


class UserResponse(BaseModel):
    """
    Comprehensive user data returned to the frontend.
    Includes full profile details and computed permissions.
    """
    model_config = ConfigDict(extra="ignore")
    id: str
    email: str
    full_name: str
    unit_number: Optional[str] = None
    phone: Optional[str] = None
    phone_home: Optional[str] = Field(None, max_length=20)
    phone_mobile: Optional[str] = Field(None, max_length=20)
    phone_business: Optional[str] = Field(None, max_length=20)
    home_address: Optional[str] = Field(None, max_length=300)
    home_suburb: Optional[str] = None
    home_state: Optional[str] = None
    home_postcode: Optional[str] = None
    postal_same_as_home: Optional[bool] = True
    postal_address: Optional[str] = None
    postal_suburb: Optional[str] = None
    postal_state: Optional[str] = None
    postal_postcode: Optional[str] = None
    is_managing_agent: Optional[bool] = False
    is_tenanted: Optional[bool] = False
    general_correspondence_email: Optional[bool] = True
    general_correspondence_post: Optional[bool] = False
    levy_notices_email: Optional[bool] = True
    levy_notices_post: Optional[bool] = False
    meeting_notices_email: Optional[bool] = True
    meeting_notices_post: Optional[bool] = False
    strata_roll_consent: Optional[bool] = False
    role: str
    is_active: bool
    is_approved: bool = False
    status: str = UserStatus.ACTIVE  # active | info_requested | archived
    ec_position: Optional[str] = None  # CHAIRMAN | TREASURER | SECRETARY | MEMBER
    info_request_reason: Optional[str] = None  # wrong_unit | wrong_user_type
    info_requested_at: Optional[str] = None
    archived_at: Optional[str] = None
    archived_by: Optional[str] = None
    archived_reason: Optional[str] = None
    profile_image: Optional[str] = None
    mail_username: Optional[str] = None
    mail_password: Optional[str] = None
    temp_elevation: Optional[dict] = None  # {role, elevated_by, elevated_at, expires_at, duration_days}
    is_elevated: bool = False  # True when temp_elevation is active (not expired)
    permissions: Permission
    created_at: str
    last_login_at: Optional[str] = None
    last_login_ip: Optional[str] = None
    # Split out by migration 0094. Either may be None, and None is meaningful:
    # "no public address was established for this login". Never backfilled from
    # the other — telling those cases apart is the whole point.
    last_login_public_ip: Optional[str] = None
    last_login_local_ip: Optional[str] = None
    is_name_flagged: Optional[bool] = False
    flag_reason: Optional[str] = None  # "name_mismatch" | None
    # TOTP 2FA fields (soft enforcement — accounts with totp_enabled=False login normally)
    totp_enabled: bool = False
    totp_verified_at: Optional[str] = None
    # Strata roll owner name from the units collection (single source of truth).
    # Populated when the user has a unit_number that exists in the units collection.
    # May differ from full_name when the registered name doesn't exactly match the roll.
    unit_owner_name: Optional[str] = None
    # Co-owner name (owner_name_b from the unit record). Only set when the unit has
    # two owners. Used to display "Co-owner: <name>" on the profile page.
    co_owner_name: Optional[str] = None
    co_owner_email: Optional[str] = None
    primary_email: Optional[str] = None
    secondary_email: Optional[str] = None
    # All unit numbers this user is actively associated with in the current building.
    # Populated at login / /auth/me from user_units collection. Drives the UnitSwitcher.
    owned_units: List[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    """Model for updating an existing user profile.

    Display-name rule (enforced in update_user endpoint):
      full_name is the canonical display field. When first_name or last_name
      is supplied without full_name, the endpoint computes full_name =
      first_name + last_name automatically — keeping the DB consistent.
    """
    full_name: Optional[str] = Field(None, max_length=200)
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    unit_number: Optional[str] = Field(None, max_length=20)
    phone: Optional[str] = Field(None, max_length=20)
    phone_home: Optional[str] = Field(None, max_length=20)
    phone_mobile: Optional[str] = Field(None, max_length=20)
    phone_business: Optional[str] = Field(None, max_length=20)
    home_address: Optional[str] = Field(None, max_length=300)
    home_suburb: Optional[str] = None
    home_state: Optional[str] = None
    home_postcode: Optional[str] = None
    postal_same_as_home: Optional[bool] = None
    postal_address: Optional[str] = None
    postal_suburb: Optional[str] = None
    postal_state: Optional[str] = None
    postal_postcode: Optional[str] = None
    is_managing_agent: Optional[bool] = None
    is_tenanted: Optional[bool] = None
    general_correspondence_email: Optional[bool] = None
    general_correspondence_post: Optional[bool] = None
    levy_notices_email: Optional[bool] = None
    levy_notices_post: Optional[bool] = None
    meeting_notices_email: Optional[bool] = None
    meeting_notices_post: Optional[bool] = None
    strata_roll_consent: Optional[bool] = None
    email: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    is_approved: Optional[bool] = None
    ec_position: Optional[str] = None
    profile_image: Optional[str] = None
    mail_password: Optional[str] = Field(None, min_length=8, max_length=128)
    custom_permissions: Optional[Dict[str, bool]] = None
    is_name_flagged: Optional[bool] = None
    flag_reason: Optional[str] = None


class AuthResponse(BaseModel):
    """Response containing JWT token and user profile after successful auth."""
    token: str
    user: UserResponse


class PasswordResetRequest(BaseModel):
    """Model for requesting a password reset email."""
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Model for confirming password reset with token."""
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


__all__ = [
    "UserRole",
    "ECPosition",
    "UserStatus",
    "Permission",
    "DEFAULT_PERMISSIONS",
    "UserBase",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserUpdate",
    "AuthResponse",
    "PasswordResetRequest",
    "PasswordResetConfirm",
]
