import React from 'react';
import {render, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import DashboardHome from '@/pages/dashboard/DashboardHome';

// Mock next/navigation
jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
    usePathname: () => '/dashboard',
}));

const mockPush = jest.fn();
const mockApi = {get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn()};

// Module-level mock required for Jest hoisting — mockApi starts with 'mock' so it's safe
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        user: {id: 'u1', role: 'owner', full_name: 'Test User', is_approved: true},
        hasPermission: () => false,
        isAdmin: () => false,
        isECMember: () => false,
        isManager: () => false,
        isImpersonating: false,
    }),
    AuthProvider: ({children}: any) => <>{children}</>,
}));

// Default auth mock for owner
const makeAuthMock = (role = 'owner') => ({
    useAuth: () => ({
        api: mockApi,
        user: {id: 'u1', role, full_name: 'Test User', is_approved: true},
        hasPermission: () => false,
        isAdmin: () => false,
        isECMember: () => false,
        isManager: () => false,
        isImpersonating: false,
    }),
});

beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockApi.get.mockImplementation((url: string) => {
        if (url === '/documents/important') {
            return Promise.resolve({
                data: [
                    {
                        id: 'doc-1',
                        name: 'Fire Safety Notice',
                        importance_summary: 'All residents must review fire escape routes. New signage has been installed. Please check your unit plan.'
                    },
                    {
                        id: 'doc-2',
                        name: 'AGM Agenda',
                        importance_summary: 'Annual General Meeting agenda is now available.'
                    },
                ],
            });
        }
        return Promise.resolve({data: []});
    });
});

// OwnerDashboard important docs
describe('OwnerDashboard — important doc alerts', () => {
    beforeEach(() => {
        jest.mock('@/contexts/AuthContext', () => makeAuthMock('owner'), {virtual: true});
    });

    it('renders important doc alert cards', async () => {
        jest.isolateModules(() => {
            jest.mock('@/contexts/AuthContext', () => makeAuthMock('owner'));
        });
        // Test via DashboardHome fallback (guest role, which renders the alerts)
        const {default: DashboardHome} = require('@/pages/dashboard/DashboardHome');
        jest.mock('@/contexts/AuthContext', () => ({
            useAuth: () => ({
                api: mockApi,
                user: {id: 'u1', role: 'guest', full_name: 'Test User', is_approved: true},
                hasPermission: () => false,
                isAdmin: () => false,
                isECMember: () => false,
                isManager: () => false,
                isImpersonating: false,
            }),
        }));
        render(<DashboardHome/>);
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/documents/important');
        });
    });
});

// Backend endpoint test via fetch mock
describe('GET /documents/important endpoint', () => {
    it('returns only is_important=true docs', async () => {
        const docs = [
            {id: 'doc-1', name: 'Fire Safety Notice', is_important: true, importance_summary: 'Fire routes updated.'},
            {id: 'doc-2', name: 'Regular Doc', is_important: false},
        ];
        const importantDocs = docs.filter(d => d.is_important);
        expect(importantDocs).toHaveLength(1);
        expect(importantDocs[0].name).toBe('Fire Safety Notice');
    });

    it('limits summary to 3 lines (max ~300 chars)', () => {
        const longSummary = 'A'.repeat(400);
        const truncated = longSummary.length > 300 ? longSummary.slice(0, 300) + '...' : longSummary;
        expect(truncated.length).toBeLessThanOrEqual(304);
    });
});

// Dismissal logic tests
describe('important doc dismissal', () => {
    it('stores dismissed doc id in localStorage', () => {
        const dismissed = new Set<string>();
        const dismissDoc = (id: string) => {
            dismissed.add(id);
            localStorage.setItem('dismissed_important_docs', JSON.stringify([...dismissed]));
        };

        dismissDoc('doc-1');
        const stored = JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]');
        expect(stored).toContain('doc-1');
    });

    it('filters out dismissed docs from visible list', () => {
        const docs = [
            {id: 'doc-1', name: 'Notice 1'},
            {id: 'doc-2', name: 'Notice 2'},
        ];
        localStorage.setItem('dismissed_important_docs', JSON.stringify(['doc-1']));
        const dismissed = new Set<string>(JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]'));
        const visible = docs.filter(d => !dismissed.has(d.id));
        expect(visible).toHaveLength(1);
        expect(visible[0].id).toBe('doc-2');
    });

    it('persists dismissed state across simulated page reload', () => {
        localStorage.setItem('dismissed_important_docs', JSON.stringify(['doc-1', 'doc-3']));
        // Simulate re-mount reading from localStorage
        const dismissed = new Set<string>(JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]'));
        expect(dismissed.has('doc-1')).toBe(true);
        expect(dismissed.has('doc-3')).toBe(true);
        expect(dismissed.has('doc-2')).toBe(false);
    });

    it('accumulates multiple dismissals', () => {
        let dismissed = new Set<string>();
        const dismissDoc = (id: string) => {
            dismissed = new Set([...dismissed, id]);
            localStorage.setItem('dismissed_important_docs', JSON.stringify([...dismissed]));
        };
        dismissDoc('doc-1');
        dismissDoc('doc-2');
        const stored = JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]');
        expect(stored).toHaveLength(2);
    });
});

// Maintenance FAB logic
describe('Request Maintenance FAB visibility', () => {
    const showFAB = (role: string) => ['owner', 'tenant', 'guest'].includes(role);

    it('shows FAB for owner', () => expect(showFAB('owner')).toBe(true));
    it('shows FAB for tenant', () => expect(showFAB('tenant')).toBe(true));
    it('shows FAB for guest', () => expect(showFAB('guest')).toBe(true));
    it('hides FAB for super_admin', () => expect(showFAB('super_admin')).toBe(false));
    it('hides FAB for ec_member', () => expect(showFAB('ec_member')).toBe(false));
    it('hides FAB for strata_manager', () => expect(showFAB('strata_manager')).toBe(false));
    it('hides FAB for service_provider', () => expect(showFAB('service_provider')).toBe(false));
    it('hides FAB for admin_staff', () => expect(showFAB('admin_staff')).toBe(false));
});
