// @featuretrace:levy — Smoke + role-gate test for FinancialYearImportPage.
// Layer: test
// Tests: frontend/src/pages/data-upload/FinancialYearImportPage.jsx
import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

import FinancialYearImportPage from '@/pages/data-upload/FinancialYearImportPage';

const apiGet = jest.fn(() => Promise.resolve({data: {imports: []}}));
let mockUser: any = {role: 'super_admin'};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        user: mockUser,
        api: {get: (...args: any[]) => apiGet(...args)},
    }),
}));

jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn(), info: jest.fn()}}));

describe('FinancialYearImportPage', () => {
    beforeEach(() => {
        apiGet.mockClear();
        mockUser = {role: 'super_admin'};
    });

    it('renders the import wizard and fetches history for an authorized role', async () => {
        render(<FinancialYearImportPage />);

        expect(await screen.findByText('Select Import Type')).toBeInTheDocument();
        expect(apiGet).toHaveBeenCalledWith('/financial-import/history?limit=10');
    });

    it('shows Access Restricted and skips the history fetch for an unauthorized role', () => {
        mockUser = {role: 'owner'};

        render(<FinancialYearImportPage />);

        expect(screen.getByText('Access Restricted')).toBeInTheDocument();
        expect(apiGet).not.toHaveBeenCalled();
    });

    it('allows strata_admin (regression: was missing from ALLOWED_ROLES, blocking a role the backend accepts)', async () => {
        mockUser = {role: 'strata_admin'};

        render(<FinancialYearImportPage />);

        expect(await screen.findByText('Select Import Type')).toBeInTheDocument();
        expect(screen.queryByText('Access Restricted')).not.toBeInTheDocument();
    });
});
