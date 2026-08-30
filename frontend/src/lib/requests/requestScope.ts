// @featuretrace:smart-request — Shared contract mirroring the workflow-requests
// router's server-side scoping and status vocabulary.
// Layer: frontend
// Data flow: RequestsPage / MyRequestsTab / OwnerDashboard(s) → these predicates →
//            GET /workflow-requests (building-scoped, role-scoped server-side).
// Related: backend/routers/workflow_requests.py
//          backend/services/morning_card_service.py

/**
 * Mirrors `_MANAGER_ROLES` in backend/routers/workflow_requests.py EXACTLY.
 *
 * This is the set the API itself uses to decide whether `GET /workflow-requests`
 * returns the whole building's requests or only the caller's own. Any drift
 * between this list and the backend's produces a screen whose heading contradicts
 * its contents, which is the specific bug this module exists to prevent.
 *
 * Note `admin_staff` is deliberately absent: the sidebar shows them the requests
 * page, but the API scopes them to their own requests, so they get resident copy.
 */
const REQUEST_MANAGER_ROLES = ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'];

/**
 * Mirrors `_TERMINAL_STATUSES` in backend/routers/workflow_requests.py EXACTLY.
 *
 * `auto_resolved` IS terminal — a request the deflection rules closed is closed.
 * Omitting it makes an auto-resolved request count as "open", which is how the
 * two owner dashboards previously disagreed about the same owner's open count.
 */
export const TERMINAL_REQUEST_STATUSES: readonly string[] = [
    'closed',
    'auto_resolved',
    'completed',
    'cancelled',
];

/**
 * True when the API will return the whole building's request queue to this user.
 *
 * Reads `effective_role` before `role`, matching `_is_manager`'s
 * `user.get("effective_role") or user.get("role")`. AuthContext's `isManager()`
 * checks the RAW `role` field only, so it reports false for a temporarily
 * elevated user (effective_role="ec_member", raw role still "owner") even though
 * the backend hands them the building-wide queue — the same trap documented in
 * pages/dashboard/financial/ReconciliationPage.tsx. Do not swap this for
 * `isManager()`.
 */
export function managesBuildingRequests(user: {effective_role?: string; role?: string} | null | undefined): boolean {
    return REQUEST_MANAGER_ROLES.includes(user?.effective_role || user?.role || '');
}

/** True once a request has reached a terminal status and should not read as open. */
export function isTerminalRequestStatus(status: string | null | undefined): boolean {
    return TERMINAL_REQUEST_STATUSES.includes(status || '');
}

/**
 * Canonical deep links into the request tracking list.
 *
 * `/requests` on its own renders the request FORM CATALOGUE and reads only
 * `?tab=`; a bare `?status=` there is silently dropped. Anything linking to the
 * tracking list must name the tab, or it lands the user on a page that cannot
 * show what the link promised.
 */
export const REQUEST_QUEUE_HREF = '/requests?tab=my-requests';
export const OVERDUE_REQUEST_QUEUE_HREF = '/requests?tab=my-requests&status=overdue';
