/**
 * PermissionGuard Component
 *
 * Conditionally renders children if the current user has the required permission(s).
 *
 * Usage (single permission):
 *   permission="financial.approve_invoice"  → wraps ApproveButton
 *
 * Usage (any of a set):
 *   anyOf={['committee.vote', 'committee.manage']}  → wraps VotingSection
 *
 * Usage (all of a set, with fallback):
 *   allOf={['documents.upload', 'documents.manage']} fallback={<p>Access denied</p>}  → wraps DocumentManager
 */

import React from 'react';
import {usePermissions} from '@/hooks/usePermissions';
import type {PermissionSlug} from '@/utils/permissions';

interface PermissionGuardProps {
    children: React.ReactNode;
    /** Single permission — user must hold this slug. */
    permission?: PermissionSlug;
    /** User must hold ALL of these slugs. */
    allOf?: PermissionSlug[];
    /** User must hold ANY of these slugs. */
    anyOf?: PermissionSlug[];
    /** Rendered instead of children when the check fails. Defaults to null. */
    fallback?: React.ReactNode;
    /** Optional building scope forwarded to the permission check. */
    buildingId?: string;
}
/**
 * @generated FunctionHeader
 * Function: PermissionGuard
 * Path: frontend/src/components/rbac/PermissionGuard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function PermissionGuard({
                                    children,
                                    permission,
                                    allOf,
                                    anyOf,
                                    fallback = null,
                                    buildingId,
                                }: PermissionGuardProps): React.ReactElement {
    const {can, canAll, canAny} = usePermissions();

    const hasAccess = (() => {
        if (permission !== undefined && !can(permission, buildingId)) return false;
        if (allOf !== undefined && allOf.length > 0 && !canAll(allOf, buildingId)) return false;
        if (anyOf !== undefined && anyOf.length > 0 && !canAny(anyOf, buildingId)) return false;
        return true;
    })();

    return <>{hasAccess ? children : fallback}</>;
}
