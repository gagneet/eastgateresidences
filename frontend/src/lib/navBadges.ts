/**
 * Navigation badge resolution utilities.
 */

export interface NavBadges {
    requests_overdue: number;
    requests_new: number;
    notices_unread: number;
    parcels_waiting: number;
    proposals_open_vote: number;
    approvals_pending: number;
    compliance_overdue: number;
    sla_breached: number;
    levy_due_soon: number;
    tenant_approvals_pending: number;
}

export interface BadgeResult {
    count: number;
    label?: string;
    severity: "error" | "warning" | "info" | "none";
}

const BADGE_RULES: Record<string, {
    severity: "error" | "warning" | "info";
    labelFn?: (n: number) => string;
}> = {
    requests_overdue: {severity: "error"},
    sla_breached: {severity: "error"},
    compliance_overdue: {severity: "error"},
    approvals_pending: {severity: "warning"},
    tenant_approvals_pending: {severity: "warning"},
    parcels_waiting: {severity: "warning",
 labelFn: (n) => n === 1 ? "1 parcel" : `${n}`},
    levy_due_soon: {severity: "warning",
 labelFn: () => "Due soon"},
    notices_unread: {severity: "info"},
    proposals_open_vote: {severity: "info",
 labelFn: () => "Vote open"},
    requests_new: {severity: "info"},
};
/**
 * @generated FunctionHeader
 * Function: resolveBadge
 * Path: frontend/src/lib/navBadges.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function resolveBadge(badgeSource: string, badges: NavBadges): BadgeResult {
    if (!badgeSource) return {count: 0, severity: "none"};
    // badge_source values are snake_case and match the NavBadges keys directly
    const value: number = (badges as unknown as Record<string, number>)[badgeSource] ?? 0;
    if (value === 0) return {count: 0, severity: "none"};
    const rule = BADGE_RULES[badgeSource] ?? {severity: "info"};
    return {count: value, label: rule.labelFn?.(value), severity: rule.severity};
}

export const EMPTY_BADGES: NavBadges = {
    requests_overdue: 0,
    requests_new: 0,
    notices_unread: 0,
    parcels_waiting: 0,
    proposals_open_vote: 0,
    approvals_pending: 0,
    compliance_overdue: 0,
    sla_breached: 0,
    levy_due_soon: 0,
    tenant_approvals_pending: 0,
};
