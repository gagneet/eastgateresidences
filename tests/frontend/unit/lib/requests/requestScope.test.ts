import {
    isTerminalRequestStatus,
    managesBuildingRequests,
    OVERDUE_REQUEST_QUEUE_HREF,
    REQUEST_QUEUE_HREF,
    TERMINAL_REQUEST_STATUSES,
} from '@/lib/requests/requestScope';

import {readFileSync} from 'fs';
import {join} from 'path';

const ROUTER = readFileSync(
    join(__dirname, '../../../../../backend/routers/workflow_requests.py'),
    'utf8',
);

describe('requestScope mirrors backend/routers/workflow_requests.py', () => {
    it('keeps the manager role set identical to _MANAGER_ROLES', () => {
        // Parse the backend set literal rather than restating it, so a role added
        // or removed there fails here instead of silently producing a screen whose
        // heading contradicts what the API returned.
        const block = ROUTER.match(/_MANAGER_ROLES = \{([^}]*)\}/);
        expect(block).not.toBeNull();
        const backendRoles = [...(block as RegExpMatchArray)[1].matchAll(/UserRole\.([A-Z_]+)/g)]
            .map(m => m[1].toLowerCase())
            .sort();

        expect(backendRoles).toEqual(['ec_member', 'strata_admin', 'strata_manager', 'super_admin']);
        backendRoles.forEach(role => {
            expect(managesBuildingRequests({role})).toBe(true);
        });
    });

    it('keeps the terminal status set identical to _TERMINAL_STATUSES', () => {
        const block = ROUTER.match(/_TERMINAL_STATUSES = \{([^}]*)\}/);
        expect(block).not.toBeNull();
        const body = (block as RegExpMatchArray)[1];
        const backendStatuses = [
            ...[...body.matchAll(/WorkflowRequestStatus\.([A-Z_]+)/g)].map(m => m[1].toLowerCase()),
            ...[...body.matchAll(/"([a-z_]+)"/g)].map(m => m[1]),
        ].sort();

        expect(backendStatuses).toEqual([...TERMINAL_REQUEST_STATUSES].sort());
    });

    it('prefers effective_role so an elevated user is scoped like the API scopes them', () => {
        // `_is_manager` reads `effective_role or role`; AuthContext.isManager()
        // reads the raw role only, which is why this module exists.
        expect(managesBuildingRequests({role: 'owner', effective_role: 'ec_member'})).toBe(true);
        expect(managesBuildingRequests({role: 'owner'})).toBe(false);
        expect(managesBuildingRequests({role: 'admin_staff'})).toBe(false);
        expect(managesBuildingRequests(null)).toBe(false);
        expect(managesBuildingRequests(undefined)).toBe(false);
    });

    it('treats auto_resolved as terminal', () => {
        expect(isTerminalRequestStatus('auto_resolved')).toBe(true);
        expect(isTerminalRequestStatus('in_progress')).toBe(false);
        expect(isTerminalRequestStatus(undefined)).toBe(false);
    });

    it('always names the tracking tab in queue links', () => {
        // /requests without ?tab= renders the form catalogue and drops ?status=.
        expect(REQUEST_QUEUE_HREF).toContain('tab=my-requests');
        expect(OVERDUE_REQUEST_QUEUE_HREF).toContain('tab=my-requests');
        expect(OVERDUE_REQUEST_QUEUE_HREF).toContain('status=overdue');
    });
});
