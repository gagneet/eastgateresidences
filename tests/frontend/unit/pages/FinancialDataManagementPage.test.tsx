// @featuretrace:levy — Smoke + role-gate test for FinancialDataManagementPage.
// Layer: test
// Tests: frontend/src/pages/data-upload/FinancialDataManagementPage.jsx
import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

import FinancialDataManagementPage from '@/pages/data-upload/FinancialDataManagementPage';

let mockUser: any = {role: 'super_admin'};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        user: mockUser,
        api: {get: jest.fn(), post: jest.fn()},
    }),
}));

jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn(), info: jest.fn()}}));

describe('FinancialDataManagementPage', () => {
    beforeEach(() => {
        mockUser = {role: 'super_admin'};
    });

    it('renders the upload page for an authorized role', () => {
        render(<FinancialDataManagementPage />);

        expect(screen.getByTestId('financial-data-management-page')).toBeInTheDocument();
        expect(screen.getByRole('heading', {name: 'Financial Data Management'})).toBeInTheDocument();
    });

    it('shows Access Restricted for an unauthorized role', () => {
        mockUser = {role: 'owner'};

        render(<FinancialDataManagementPage />);

        expect(screen.getByText('Access Restricted')).toBeInTheDocument();
        expect(screen.queryByTestId('financial-data-management-page')).not.toBeInTheDocument();
    });

    it.each(['ec_member', 'strata_admin'])(
        'allows %s (regression: was missing from allowedRoles, blocking a role the backend accepts)',
        (role) => {
            mockUser = {role};

            render(<FinancialDataManagementPage />);

            expect(screen.getByTestId('financial-data-management-page')).toBeInTheDocument();
            expect(screen.queryByText('Access Restricted')).not.toBeInTheDocument();
        }
    );
});
