/**
 * usePermissions Hook
 *
 * React hook for permission checking with the current session.
 * Works with both new slug-based permissions and legacy boolean permissions.
 *
 * The NextAuth session stores the full user record at session.user.data, including
 * a `permissions` object with legacy boolean fields (e.g. can_view_finances: true).
 * This hook bridges that structure into the UserSession shape expected by the
 * permission utilities, so callers don't need to know the session layout.
 *
 * Usage:
 *   const { can, canAny, canAll, isSuperAdmin, isStrataAdmin } = usePermissions()
 *   if (can('financial.approve_invoice')) { ... }
 */

import {useMemo} from 'react';
import {useSession} from 'next-auth/react';
import {
    can as _can,
    canAll as _canAll,
    canAny as _canAny,
    getPermissions as _getPermissions,
    hasRole as _hasRole,
    isStrataAdmin as _isStrataAdmin,
    isCommitteeMember as _isCommitteeMember,
    isSuperAdmin as _isSuperAdmin,
    type PermissionSlug,
    type UserSession,
} from '@/utils/permissions';

/**
 * Shape of the data stored at session.user.data (from next-auth.d.ts augmentation).
 * Kept local to avoid coupling this hook to the global NextAuth types.
 */
interface SessionUserData {
    id?: string;
    role?: string;
    permissions?: Record<string, boolean> | string[];

    [key: string]: unknown;
}
/** Build a UserSession from the raw NextAuth session. */
function buildUserSession(session: ReturnType<typeof useSession>['data']): UserSession {
    if (!session?.user) return {};

    const rawUser = session.user as Record<string, unknown>;
    const data = (rawUser.data ?? {}) as SessionUserData;

    // role lives both on session.user (set in auth callbacks) and inside data
    const role = (data.role ?? rawUser.role ?? '') as string;

    // permissions may be a Record<string, boolean> (legacy) or string[] (new)
    const perms = data.permissions ?? {};

    let slugArray: string[] | undefined;
    let legacyBooleans: Record<string, boolean> = {};

    if (Array.isArray(perms)) {
        slugArray = perms as string[];
    } else if (perms && typeof perms === 'object') {
        legacyBooleans = perms as Record<string, boolean>;
    }

    return {
        user: {
            id: (data.id ?? rawUser.id ?? '') as string,
            role,
            permissions: slugArray,
            building_id: (data.building_id ?? rawUser.building_id ?? '') as string,
            // Spread legacy boolean fields so can() can fall back to them
            can_view_finances: legacyBooleans.can_view_finances,
            can_manage_finances: legacyBooleans.can_manage_finances,
            can_manage_users: legacyBooleans.can_manage_users,
            can_post_announcements: legacyBooleans.can_post_announcements,
            can_view_documents: legacyBooleans.can_view_documents,
            can_upload_documents: legacyBooleans.can_upload_documents,
            can_view_meetings: legacyBooleans.can_view_meetings,
            can_manage_meetings: legacyBooleans.can_manage_meetings,
            can_chat: legacyBooleans.can_chat,
            can_access_email: legacyBooleans.can_access_email,
            can_manage_requests: legacyBooleans.can_manage_requests,
            can_manage_access_device_settings: legacyBooleans.can_manage_access_device_settings,
            can_manage_tenancy: legacyBooleans.can_manage_tenancy,
            can_view_schedule: legacyBooleans.can_view_schedule,
            can_send_notifications: legacyBooleans.can_send_notifications,
            can_manage_document_permissions: legacyBooleans.can_manage_document_permissions,
        },
    };
}

export interface UsePermissionsReturn {
    /** All permission slugs active for this session. */
    permissions: string[];

    /** Check a single permission slug. */
    can(permission: PermissionSlug, buildingId?: string): boolean;

    /** Check that the user holds ALL of the given permissions. */
    canAll(permissions: PermissionSlug[], buildingId?: string): boolean;

    /** Check that the user holds ANY of the given permissions. */
    canAny(permissions: PermissionSlug[], buildingId?: string): boolean;

    /** Check if the current user has a specific role. */
    hasRole(role: string): boolean;

    /** True when the current user is a super admin. */
    isSuperAdmin(): boolean;

    /** True when the current user is a Strata Management Company admin. */
    isStrataAdmin(): boolean;

    /** True when the current user is EC member, strata admin, strata manager, or super admin. */
    isCommitteeMember(): boolean;
}
/**
 * @generated FunctionHeader
 * Function: usePermissions
 * Path: frontend/src/hooks/usePermissions.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function usePermissions(): UsePermissionsReturn {
    const {data: session} = useSession();

    return useMemo<UsePermissionsReturn>(() => {
        const userSession = buildUserSession(session);

        return {

            can: (permission, buildingId) => _can(userSession, permission, buildingId),

            canAll: (permissions, buildingId) => _canAll(userSession, permissions, buildingId),

            canAny: (permissions, buildingId) => _canAny(userSession, permissions, buildingId),

            hasRole: (role) => _hasRole(userSession, role),

            isSuperAdmin: () => _isSuperAdmin(userSession),

            isStrataAdmin: () => _isStrataAdmin(userSession),

            isCommitteeMember: () => _isCommitteeMember(userSession),
            permissions: _getPermissions(userSession),
        };
    }, [session]);
}
