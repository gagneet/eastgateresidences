import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import FinancialOnboardingPage from '@/pages/dashboard/admin/FinancialOnboardingPage';

jest.mock('@/pages/dashboard/financial/HistoricalReconstructionPage', () => ({
    __esModule: true,
    default: ({buildingIdOverride, title}: any) => (
        <div data-testid="historical-reconstruction-child">
            {title}:{buildingIdOverride}
        </div>
    ),
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockApi = {
    get: jest.fn(),
};

let mockAuthState: any;

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthState,
}));

describe('FinancialOnboardingPage', () => {
    beforeEach(() => {
        mockApi.get.mockResolvedValue({
            data: [
                {id: 'scheme-1', building_id: '13195', name: 'East Gate Residences', tenant_name: 'East Gate'},
                {id: 'scheme-2', building_id: '16244', name: 'Sierra', tenant_name: 'Demo Org'},
            ],
        });
        mockAuthState = {
            api: mockApi,
            user: {id: 'u1', role: 'strata_manager', effective_role: 'strata_manager'},
            loading: false,
            selectedBuilding: {id: 'scheme-1', building_id: '13195', name: 'East Gate Residences'},
            availableBuildings: [
                {id: 'scheme-1', building_id: '13195', name: 'East Gate Residences', tenant_name: 'East Gate'},
                {id: 'scheme-2', building_id: '16244', name: 'Sierra', tenant_name: 'Demo Org'},
            ],
            switchBuilding: jest.fn().mockResolvedValue(undefined),
            hasPermission: jest.fn((key: string) => key === 'can_manage_finances'),
            hasFeatureAccess: jest.fn(() => true),
        };
        jest.clearAllMocks();
    });

    it('renders the reconstruction workflow when the selected building is already active', async () => {
        render(<FinancialOnboardingPage/>);

        expect(await screen.findByTestId('historical-reconstruction-child')).toHaveTextContent(
            'Financial Onboarding:13195',
        );
    });

    it('allows strata admins with finance permission to use financial onboarding', async () => {
        mockAuthState = {
            ...mockAuthState,
            user: {id: 'sa1', role: 'strata_admin', effective_role: 'strata_admin'},
        };

        render(<FinancialOnboardingPage/>);

        expect(await screen.findByTestId('historical-reconstruction-child')).toHaveTextContent(
            'Financial Onboarding:13195',
        );
    });

    it('switches building context and returns to the financial onboarding route', async () => {
        render(<FinancialOnboardingPage/>);

        fireEvent.change(screen.getByLabelText('Building'), {target: {value: 'scheme-2'}});
        fireEvent.click(screen.getByRole('button', {name: /Open Building Workflow/i}));

        await waitFor(() => {
            expect(mockAuthState.switchBuilding).toHaveBeenCalledWith(
                'scheme-2',
                {redirectTo: '/admin/financial-onboarding'},
            );
        });
    });

    it('blocks users without finance management access', () => {
        mockAuthState = {
            ...mockAuthState,
            hasPermission: jest.fn(() => false),
        };

        render(<FinancialOnboardingPage/>);

        expect(screen.getByText(/Access restricted/i)).toBeInTheDocument();
    });
});
