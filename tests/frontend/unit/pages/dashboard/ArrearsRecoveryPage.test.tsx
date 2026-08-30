import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import ArrearsRecoveryPage from '@/pages/dashboard/ArrearsRecoveryPage';

// Mock next-auth/react BEFORE anything else imports it
jest.mock('next-auth/react', () => ({
    useSession: jest.fn(() => ({data: null, status: 'unauthenticated'})),
    SessionProvider: ({children}: any) => <div>{children}</div>,
    signIn: jest.fn(),
    signOut: jest.fn(),
}));

jest.mock('next/navigation', () => ({
    usePathname: () => '/intelligence/debt-recovery',
    useRouter: () => ({
        push: jest.fn(),
    }),
    useSearchParams: () => ({
        get: (key: string) => (key === 'year' ? '2026' : null),
    }),
}));

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        hasPermission: () => true,
        user: {role: 'strata_manager', unit_number: null},
    }),
    AuthProvider: ({children}: any) => <div>{children}</div>,
}));

const ARREARS_ROW_WITH_INTEREST = {
    unit_number: 'TH087',
    owner_name: 'Avneet Rooprai',
    owner_email: 'avneet@example.com',
    total_arrears: 1000.0,
    opening_arrears: 1000.0,
    accrued_interest: 12.34,
    total_owing: 1012.34,
    interest_rate_pct: 10.0,
    days_overdue: 45,
    severity: 'serious',
    dca_status: null,
    active_payment_plan: false,
    legal_referral_status: null,
};

const ARREARS_ROW_WITHOUT_INTEREST = {
    unit_number: 'UA063',
    owner_name: 'No Interest Owner',
    owner_email: 'noint@example.com',
    total_arrears: 500.0,
    opening_arrears: 500.0,
    accrued_interest: 0,
    total_owing: 500.0,
    interest_rate_pct: 10.0,
    days_overdue: 5,
    severity: 'overdue',
    dca_status: null,
    active_payment_plan: false,
    legal_referral_status: null,
};

describe('ArrearsRecoveryPage — interest/penalty display', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/arrears/detail')) {
                return Promise.resolve({data: [ARREARS_ROW_WITH_INTEREST, ARREARS_ROW_WITHOUT_INTEREST]});
            }
            if (url.includes('/stats/building-kpis')) {
                if (url.includes('financial_year=2025')) {
                    return Promise.resolve({
                        data: {total_arrears: 5000, units_in_arrears: 14, total_units: 42, collection_rate: 91.1},
                    });
                }
                if (url.includes('financial_year=2026')) {
                    return Promise.resolve({
                        data: {total_arrears: 9999, units_in_arrears: 99, total_units: 42, collection_rate: 86.4},
                    });
                }
                return Promise.resolve({
                    data: {total_arrears: 0, units_in_arrears: 0, total_units: 42, collection_rate: 0},
                });
            }
            return Promise.resolve({data: {}});
        });
    });

    it('renders accrued interest and rate for a unit with non-zero interest', async () => {
        render(<ArrearsRecoveryPage/>);

        await waitFor(() => expect(screen.getByText('TH087')).toBeInTheDocument());

        expect(screen.getByText(/interest.*total owing/i)).toBeInTheDocument();
        expect(screen.getByText(/10%\s*p\.a\./i)).toBeInTheDocument();
    });

    it('does not render an interest line for a unit with zero accrued interest', async () => {
        render(<ArrearsRecoveryPage/>);

        await waitFor(() => expect(screen.getByText('UA063')).toBeInTheDocument());

        // Only the TH087 row should show the interest/total-owing breakdown; UA063 must not.
        const interestLines = screen.queryAllByText(/interest.*total owing/i);
        expect(interestLines).toHaveLength(1);
    });

    it('uses /arrears/detail rows for the recoverable arrears card, not broader building KPIs', async () => {
        render(<ArrearsRecoveryPage/>);

        await waitFor(() => expect(screen.getByText('TH087')).toBeInTheDocument());

        expect(mockApi.get).toHaveBeenCalledWith('/arrears/detail?year=2026');
        expect(screen.getByText(/Recoverable Arrears/i)).toBeInTheDocument();
        expect(screen.getByText('$1,500.00')).toBeInTheDocument();
        expect(screen.getByText('of 42 units')).toBeInTheDocument();
        expect(screen.queryByText('$9,999.00')).not.toBeInTheDocument();
    });

    it('opens a metric details popup from the recoverable arrears card', async () => {
        render(<ArrearsRecoveryPage/>);

        await waitFor(() => expect(screen.getByText('TH087')).toBeInTheDocument());

        fireEvent.click(screen.getByText(/Recoverable Arrears/i));

        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getAllByText(/Total owing/i).length).toBeGreaterThan(0);
        expect(screen.getByText('$1,512.34')).toBeInTheDocument();
    });

    it('keeps KPI year-on-year comparison on the KPI arrears source', async () => {
        render(<ArrearsRecoveryPage/>);

        await waitFor(() => expect(screen.getByText('TH087')).toBeInTheDocument());

        fireEvent.click(screen.getByText(/KPI YoY Change/i));

        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByText('$9,999.00')).toBeInTheDocument();
        expect(screen.getByText('$5,000.00')).toBeInTheDocument();
        expect(screen.getAllByText('+100.0%').length).toBeGreaterThanOrEqual(2);
    });
});
