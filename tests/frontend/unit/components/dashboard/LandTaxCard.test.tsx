/**
 * @featuretrace:land-tax — Frontend tests for LandTaxCard component.
 * Layer: test
 * Tests: owner-occupied shows exempt msg (no amounts); investment+no tenant shows amounts with warning;
 *        investment+tenant shows full payment button.
 * Data flow: LandTaxCard → /council-rates/{unit} → ACT Revenue cache/API (building-scoped).
 * Related: backend/routers/council_rates.py
 *          frontend/src/components/dashboard/LandTaxCard.jsx
 * Scope: (building-scoped)
 */
import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

const mockApi = {get: jest.fn()};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        user: {unit_number: 'TH017'},
        hasPermission: () => true,
    }),
}));

jest.mock('@/components/ui/skeleton', () => ({
    Skeleton: ({className}: any) => <div className={className} aria-label="Loading"/>,
}));

jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children, open}: any) => open ? <div>{children}</div> : null,
    DialogContent: ({children}: any) => <div>{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <h2>{children}</h2>,
    DialogDescription: ({children}: any) => <p>{children}</p>,
    DialogFooter: ({children}: any) => <div>{children}</div>,
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const ownerOccupiedRatesData = {
    is_owner_occupied: true,
    land_tax_applicable: false,
    land_tax_total: 0,
    financial_year: '2025-26',
};

const investmentRatesData = {
    is_owner_occupied: false,
    land_tax_applicable: true,
    land_tax_total: 4200.0,
    land_tax_q1: 1058.63,
    land_tax_q2: 1058.63,
    land_tax_q3: 1034.61,
    land_tax_q4: 1048.13,
    financial_year: '2025-26',
};

const noOccupants = {occupants: []};
const withTenant = {occupants: [{id: 't1', role: 'tenant', name: 'Jane Smith'}]};

describe('LandTaxCard', () => {
    const renderCard = async (unitNumber = 'TH017') => {
        const {default: LandTaxCard} = await import('@/components/dashboard/LandTaxCard');
        render(<LandTaxCard unitNumber={unitNumber}/>);
        await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('shows owner-occupied exempt message — no land tax amounts displayed', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('council-rates')) return Promise.resolve({data: ownerOccupiedRatesData});
            if (url.includes('occupants')) return Promise.resolve({data: noOccupants});
            return Promise.reject(new Error('unexpected'));
        });

        await renderCard();

        await waitFor(() => {
            expect(screen.getByText(/Owner-Occupied.*Land Tax Exempt/i)).toBeInTheDocument();
        });
        // Should NOT show dollar amounts
        expect(screen.queryByText('$4,200.00')).not.toBeInTheDocument();
        expect(screen.queryByText('Annual Assessment')).not.toBeInTheDocument();
    });

    it('shows investment property without tenant — amounts visible, payment disabled, warning shown', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('council-rates')) return Promise.resolve({data: investmentRatesData});
            if (url.includes('occupants')) return Promise.resolve({data: noOccupants});
            return Promise.reject(new Error('unexpected'));
        });

        await renderCard();

        await waitFor(() => {
            expect(screen.getByText('Annual Assessment')).toBeInTheDocument();
            expect(screen.getByText(/No active tenant registered/i)).toBeInTheDocument();
        });

        const payBtn = screen.getByTestId('land-tax-pay-btn');
        expect(payBtn).toBeDisabled();
    });

    it('shows investment property with tenant — payment button enabled', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('council-rates')) return Promise.resolve({data: investmentRatesData});
            if (url.includes('occupants')) return Promise.resolve({data: withTenant});
            return Promise.reject(new Error('unexpected'));
        });

        await renderCard();

        await waitFor(() => {
            expect(screen.getByText('Annual Assessment')).toBeInTheDocument();
            expect(screen.getByText(/Active tenant record detected/i)).toBeInTheDocument();
        });

        const payBtn = screen.getByTestId('land-tax-pay-btn');
        expect(payBtn).not.toBeDisabled();
        expect(payBtn).toHaveTextContent('Record Land Tax Payment');
    });

    it('shows quarterly breakdown for investment property', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('council-rates')) return Promise.resolve({data: investmentRatesData});
            if (url.includes('occupants')) return Promise.resolve({data: withTenant});
            return Promise.reject(new Error('unexpected'));
        });

        await renderCard();

        await waitFor(() => {
            expect(screen.getByText('Q1 Aug')).toBeInTheDocument();
            expect(screen.getByText('Q2 Nov')).toBeInTheDocument();
            expect(screen.getByText('Q3 Feb')).toBeInTheDocument();
            expect(screen.getByText('Q4 May')).toBeInTheDocument();
        });
    });
});
