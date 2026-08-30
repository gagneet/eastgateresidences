export enum UserRole {
    SUPER_ADMIN = "super_admin",
    STRATA_ADMIN = "strata_admin",
    EC_MEMBER = "ec_member",
    STRATA_MANAGER = "strata_manager",
    ADMIN_STAFF = "admin_staff",
    OWNER = "owner",
    TENANT = "tenant",
    REAL_ESTATE_AGENT = "real_estate_agent",
    SERVICE_PROVIDER = "service_provider",
    GUEST = "guest",
}

export enum UserStatus {
    ACTIVE = "active",
    PENDING_OWNER_APPROVAL = "pending_owner_approval",
    INFO_REQUESTED = "info_requested",
    ARCHIVED = "archived",
}

export enum ECPosition {
    CHAIRMAN = "CHAIRMAN",
    TREASURER = "TREASURER",
    SECRETARY = "SECRETARY",
    MEMBER = "MEMBER",
}

export interface Permission {
    can_view_documents: boolean;
    can_upload_documents: boolean;
    can_manage_document_permissions: boolean;
    can_view_finances: boolean;
    can_manage_finances: boolean;
    can_create_listings: boolean;
    can_chat: boolean;
    can_view_meetings: boolean;
    can_manage_meetings: boolean;
    can_manage_users: boolean;
    can_view_schedule: boolean;
    can_post_announcements: boolean;
    can_send_notifications: boolean;
    can_access_email: boolean;
    can_manage_requests: boolean;
    can_manage_access_device_settings: boolean;
}

export interface UserResponse {
    id: string;
    email: string;
    full_name: string;
    unit_number?: string | null;
    phone?: string | null;
    phone_home?: string | null;
    phone_mobile?: string | null;
    phone_business?: string | null;
    home_address?: string | null;
    home_suburb?: string | null;
    home_state?: string | null;
    home_postcode?: string | null;
    postal_same_as_home?: boolean;
    postal_address?: string | null;
    postal_suburb?: string | null;
    postal_state?: string | null;
    postal_postcode?: string | null;
    is_managing_agent?: boolean;
    is_tenanted?: boolean;
    general_correspondence_email?: boolean;
    general_correspondence_post?: boolean;
    levy_notices_email?: boolean;
    levy_notices_post?: boolean;
    meeting_notices_email?: boolean;
    meeting_notices_post?: boolean;
    strata_roll_consent?: boolean;
    role: UserRole;
    is_active: boolean;
    is_approved: boolean;
    status: UserStatus;
    ec_position?: ECPosition | null;
    info_request_reason?: string | null;
    info_requested_at?: string | null;
    archived_at?: string | null;
    archived_by?: string | null;
    archived_reason?: string | null;
    profile_image?: string | null;
    mail_username?: string | null;
    mail_password?: string | null;
    temp_elevation?: {
        role: UserRole;
        elevated_by: string;
        elevated_at: string;
        expires_at: string;
        duration_days: number;
    } | null;
    is_elevated: boolean;
    permissions: Permission;
    created_at: string;
    last_login_at?: string | null;
    last_login_ip?: string | null;
}

export interface AuthResponse {
    token: string;
    user: UserResponse;
}
