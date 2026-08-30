/**
 * RBAC Permission Utilities
 *
 * Frontend permission checking helpers. These work with permission slugs
 * stored in the user session.
 *
 * IMPORTANT: These are UI-only helpers for showing/hiding elements.
 * All actual authorization is enforced server-side. Never trust frontend checks alone.
 *
 * Usage:
 *   import { can, canAll, canAny } from '@/utils/permissions'
 *   if (can(session, 'financial.approve_invoice', buildingId)) { ... }
 */

export interface UserSession {
    user?: {
        id?: string;
        role?: string;
        permissions?: string[]; // New: list of permission slugs
        building_id?: string;
        // Legacy boolean permissions still supported
        can_view_finances?: boolean;
        can_manage_finances?: boolean;
        can_manage_users?: boolean;
        can_post_announcements?: boolean;
        can_view_documents?: boolean;
        can_upload_documents?: boolean;
        can_view_meetings?: boolean;
        can_manage_meetings?: boolean;
        can_chat?: boolean;
        can_access_email?: boolean;
        can_manage_requests?: boolean;
        can_manage_access_device_settings?: boolean;
        can_manage_tenancy?: boolean;
        can_view_schedule?: boolean;
        can_send_notifications?: boolean;
        can_manage_document_permissions?: boolean;
    };
}

export type PermissionSlug =
    | 'building.view' | 'building.update' | 'building.config'
    | 'financial.view' | 'financial.view_own' | 'financial.manage'
    | 'financial.approve_invoice' | 'financial.export'
    | 'workorder.view' | 'workorder.create' | 'workorder.update' | 'workorder.approve'
    | 'documents.view' | 'documents.upload' | 'documents.delete' | 'documents.manage'
    | 'committee.vote' | 'committee.finalize_minutes' | 'committee.manage'
    | 'users.view' | 'users.manage' | 'users.invite'
    | 'announcements.view' | 'announcements.create' | 'announcements.manage'
    | 'meetings.view' | 'meetings.manage'
    | 'listings.view' | 'listings.create' | 'listings.manage'
    | 'maintenance.request' | 'maintenance.manage'
    | 'access_devices.configure'
    | 'tenancy.view' | 'tenancy.manage'
    | 'chat.access'
    | 'notifications.send'
    | 'schedule.view' | 'schedule.manage'
    | 'email.access'
    | 'trust.view' | 'trust.manage'
    | string; // allow custom slugs

/**
 * Maps new permission slugs to legacy boolean field names on the user object.
 * Used as a fallback when the session doesn't carry the new permissions array.
 */
const LEGACY_SLUG_MAP: Record<string, keyof NonNullable<UserSession['user']>> = {
    'financial.view': 'can_view_finances',
    'financial.view_own': 'can_view_finances',
    'financial.manage': 'can_manage_finances',
    'financial.approve_invoice': 'can_manage_finances',
    'financial.export': 'can_manage_finances',
    'documents.view': 'can_view_documents',
    'documents.upload': 'can_upload_documents',
    'documents.manage': 'can_manage_document_permissions',
    'users.manage': 'can_manage_users',
    'users.view': 'can_manage_users',
    'announcements.create': 'can_post_announcements',
    'meetings.view': 'can_view_meetings',
    'meetings.manage': 'can_manage_meetings',
    'chat.access': 'can_chat',
    'email.access': 'can_access_email',
    'trust.view': 'can_view_finances',
    'trust.manage': 'can_manage_finances',
    'maintenance.manage': 'can_manage_requests',
    'access_devices.configure': 'can_manage_access_device_settings',
    'tenancy.manage': 'can_manage_tenancy',
    'schedule.view': 'can_view_schedule',
    'notifications.send': 'can_send_notifications',
};
/**
 * Check if a user session has a specific permission.
 * Checks new slug-based permissions first, falls back to legacy boolean permissions.
 */
export function can(
    session: UserSession | null | undefined,
    permission: PermissionSlug,
    _buildingId?: string,
): boolean {
    if (!session?.user) return false;

    const {user} = session;

    // Super admin bypasses all permission checks
    if (user.role === 'super_admin') return true;

    // Check new slug-based permissions array first
    if (Array.isArray(user.permissions) && user.permissions.length > 0) {
        return user.permissions.includes(permission);
    }

    // Fall back to legacy boolean fields
    const legacyKey = LEGACY_SLUG_MAP[permission];
    if (legacyKey) {
        return user[legacyKey] === true;
    }

    return false;
}
/**
 * Check if user has ALL of the given permissions.
 */
export function canAll(
    session: UserSession | null | undefined,
    permissions: PermissionSlug[],
    buildingId?: string,
): boolean {
    return permissions.every((p) => can(session, p, buildingId));
}
/**
 * Check if user has ANY of the given permissions.
 */
export function canAny(
    session: UserSession | null | undefined,
    permissions: PermissionSlug[],
    buildingId?: string,
): boolean {
    return permissions.some((p) => can(session, p, buildingId));
}
/**
 * Get all permission slugs for the current session.
 * Returns the new slug array if available, otherwise derives slugs from legacy booleans.
 */
export function getPermissions(session: UserSession | null | undefined): string[] {
    if (!session?.user) return [];

    const {user} = session;

    if (Array.isArray(user.permissions) && user.permissions.length > 0) {
        return user.permissions;
    }

    // Derive from legacy booleans — return every slug whose mapped legacy field is true
    return Object.entries(LEGACY_SLUG_MAP)
        .filter(([, legacyKey]) => user[legacyKey] === true)
        .map(([slug]) => slug);
}
/**
 * Check if user is a specific role (backward compat).
 */
export function hasRole(session: UserSession | null | undefined, role: string): boolean {
    return session?.user?.role === role;
}
/**
 * Check if user is super admin.
 */
export function isSuperAdmin(session: UserSession | null | undefined): boolean {
    return hasRole(session, 'super_admin');
}
/**
 * Check if user is committee member or above (EC_MEMBER, STRATA_ADMIN,
 * STRATA_MANAGER, SUPER_ADMIN). EC office-bearer positions (Chairman,
 * Treasurer, Secretary) are conveyed by the ``ec_position`` field on
 * the user, not by the role.
 */
export function isCommitteeMember(session: UserSession | null | undefined): boolean {
    const role = session?.user?.role;
    return (
        role === 'ec_member' ||
        role === 'strata_manager' ||
        role === 'strata_admin' ||
        role === 'super_admin'
    );
}
/**
 * Check if user is the admin of a Strata Management Company.
 */
export function isStrataAdmin(session: UserSession | null | undefined): boolean {
    return hasRole(session, 'strata_admin');
}
