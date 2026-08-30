// @featuretrace:financial-data-flow — Regression test for the TCO page's auth-loading
// guard race condition, found during the 2026-07-02 financial browser-verification audit.
// Layer: test
// Data flow: mocked AuthContext + api → TrueOwnershipCostPage render guard → no /owner-hub fetch
//            until the session resolves (building-scoped).
// Related: frontend/src/pages/dashboard/ownerhub/TrueOwnershipCostPage.jsx
//          docs/architecture/financial_browser_verification.md
import React from 'react';
import {render, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import TrueOwnershipCostPage from '@/pages/dashboard/ownerhub/TrueOwnershipCostPage';

const mockPush = jest.fn();
jest.mock('next/navigation', () => ({
    useRouter: () => ({push: mockPush}),
}));

const mockApi = {get: jest.fn(), post: jest.fn()};
const mockAuth = {
    api: mockApi,
    user: {id: 'owner-1', role: 'owner', full_name: 'Test Owner'},
    loading: false,
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuth,
}));

describe('TrueOwnershipCostPage auth-loading guard', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockAuth.loading = false;
        mockAuth.user = {id: 'owner-1', role: 'owner', full_name: 'Test Owner'};
        mockApi.get.mockImplementation((url) => {
            if (url === '/owner-hub/properties') return Promise.resolve({data: []});
            if (url === '/owner-hub/units') {
                return Promise.resolve({data: [{unit_number: 'A1', owner_name: 'Test Owner'}]});
            }
            return Promise.resolve({data: {}});
        });
    });

    it('does not redirect an authorized owner while the auth session is still loading', () => {
        // Regression: previously this page had no `if (authLoading) return` before
        // evaluating canAccess, so a real owner got bounced to /dashboard on any render
        // where AuthContext's `user` was still null/undefined during session hydration —
        // an intermittent race, not a deterministic bug, which is why it was only found
        // via a real Playwright browser run (passed 1/2 dry runs before the fix).
        mockAuth.loading = true;
        mockAuth.user = null;

        render(<TrueOwnershipCostPage/>);

        expect(mockPush).not.toHaveBeenCalled();
    });

    it('redirects a genuinely unauthorized role once auth has finished loading', async () => {
        mockAuth.loading = false;
        mockAuth.user = {id: 'tenant-1', role: 'tenant', full_name: 'Test Tenant'};

        render(<TrueOwnershipCostPage/>);

        await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/dashboard'));
    });

    it('does not fetch data while auth is still loading, only after it resolves', async () => {
        mockAuth.loading = true;
        mockAuth.user = null;

        const {rerender} = render(<TrueOwnershipCostPage/>);
        expect(mockApi.get).not.toHaveBeenCalled();

        mockAuth.loading = false;
        mockAuth.user = {id: 'owner-1', role: 'owner', full_name: 'Test Owner'};
        rerender(<TrueOwnershipCostPage/>);

        await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/owner-hub/properties'));
    });
});
