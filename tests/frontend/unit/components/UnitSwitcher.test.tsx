import React from 'react';
import {render, screen, fireEvent, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import UnitSwitcher from '@/components/layout/UnitSwitcher';

jest.mock('@/components/ui/tooltip', () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipTrigger: ({children}: any) => <>{children}</>,
    TooltipContent: () => null,  // suppress to avoid duplicate text in assertions
    TooltipProvider: ({children}: any) => <>{children}</>,
}));

jest.mock('@/components/ui/dropdown-menu', () => ({
    DropdownMenu: ({children}: any) => <>{children}</>,
    DropdownMenuContent: ({children, 'data-testid': dtid}: any) => <div data-testid={dtid}>{children}</div>,
    DropdownMenuItem: ({children, onClick, 'data-testid': dtid, 'aria-current': ac}: any) => (
        <button onClick={onClick} data-testid={dtid} aria-current={ac}>{children}</button>
    ),
    DropdownMenuLabel: ({children}: any) => <span>{children}</span>,
    DropdownMenuSeparator: () => <hr/>,
    DropdownMenuTrigger: ({children}: any) => <>{children}</>,
}));

jest.mock('@/components/ui/button', () => ({
    // When asChild=true the real Button delegates to its child via Radix Slot;
    // mimic that here so we don't double-nest <button> elements.
    Button: ({children, disabled, 'aria-label': al, asChild}: any) =>
        asChild ? <>{children}</> : <button disabled={disabled} aria-label={al}>{children}</button>,
}));

jest.mock('@/components/ui/badge', () => ({
    Badge: ({children}: any) => <span>{children}</span>,
}));

jest.mock('framer-motion', () => ({
    motion: {
        button: React.forwardRef(({children, ...props}: any, ref: any) => (
            <button ref={ref} {...props}>{children}</button>
        )),
    },
}));

const mockSwitchUnit = jest.fn();

// Simple mock for useAuth injected per test
let mockAuth: any = {};
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuth,
}));

beforeEach(() => {
    jest.clearAllMocks();
    mockAuth = {
        user: {role: 'owner', unit_number: 'UA001', owned_units: ['UA001']},
        selectedUnit: 'UA001',
        availableUnits: ['UA001'],
        switchUnit: mockSwitchUnit,
        hasFeatureAccess: jest.fn(() => true),
    };
});

describe('UnitSwitcher', () => {
    describe('feature gating', () => {
        it('renders nothing when feature toggle is off', () => {
            mockAuth.hasFeatureAccess = jest.fn(() => false);
            const {container} = render(<UnitSwitcher/>);
            expect(container.firstChild).toBeNull();
        });

        it('renders nothing for tenant role', () => {
            mockAuth.user = {role: 'tenant', unit_number: 'UA001', owned_units: ['UA001']};
            const {container} = render(<UnitSwitcher/>);
            expect(container.firstChild).toBeNull();
        });

        it('renders for owner role', () => {
            render(<UnitSwitcher/>);
            expect(screen.getByText(/Unit UA001/i)).toBeInTheDocument();
        });

        it('renders for strata_manager role with a unit', () => {
            mockAuth.user = {role: 'strata_manager', unit_number: 'UA010', owned_units: ['UA010']};
            mockAuth.selectedUnit = 'UA010';
            mockAuth.availableUnits = ['UA010'];
            render(<UnitSwitcher/>);
            expect(screen.getByText(/Unit UA010/i)).toBeInTheDocument();
        });
    });

    describe('single-unit display', () => {
        it('shows static unit badge when only one unit owned', () => {
            render(<UnitSwitcher/>);
            expect(screen.getByText(/Your unit/i)).toBeInTheDocument();
            expect(screen.getByText(/Unit UA001/i)).toBeInTheDocument();
        });

        it('renders nothing when single-unit owner has no unit_number', () => {
            mockAuth.user = {role: 'owner', unit_number: null, owned_units: []};
            mockAuth.selectedUnit = null;
            mockAuth.availableUnits = [];
            const {container} = render(<UnitSwitcher/>);
            expect(container.firstChild).toBeNull();
        });
    });

    describe('multi-unit dropdown', () => {
        beforeEach(() => {
            mockAuth.user = {role: 'owner', unit_number: 'UA001', owned_units: ['UA001', 'UA010', 'UA053']};
            mockAuth.selectedUnit = 'UA001';
            mockAuth.availableUnits = ['UA001', 'UA010', 'UA053'];
        });

        it('renders a dropdown trigger with current unit', () => {
            render(<UnitSwitcher/>);
            expect(screen.getByText(/Active unit/i)).toBeInTheDocument();
            // "Unit UA001" appears in both trigger and dropdown item — check at least one
            expect(screen.getAllByText(/Unit UA001/i).length).toBeGreaterThanOrEqual(1);
        });

        it('renders all unit options', () => {
            render(<UnitSwitcher/>);
            expect(screen.getByTestId('unit-option-UA001')).toBeInTheDocument();
            expect(screen.getByTestId('unit-option-UA010')).toBeInTheDocument();
            expect(screen.getByTestId('unit-option-UA053')).toBeInTheDocument();
        });

        it('marks the active unit with aria-current', () => {
            render(<UnitSwitcher/>);
            const active = screen.getByTestId('unit-option-UA001');
            expect(active).toHaveAttribute('aria-current', 'true');
            const inactive = screen.getByTestId('unit-option-UA010');
            expect(inactive).not.toHaveAttribute('aria-current');
        });

        it('shows Active badge on current unit option', () => {
            render(<UnitSwitcher/>);
            const ua001 = screen.getByTestId('unit-option-UA001');
            expect(ua001).toHaveTextContent('Active');
            const ua010 = screen.getByTestId('unit-option-UA010');
            expect(ua010).not.toHaveTextContent('Active');
        });

        it('calls switchUnit when a different unit option is clicked', async () => {
            mockSwitchUnit.mockResolvedValueOnce(undefined);
            render(<UnitSwitcher/>);
            fireEvent.click(screen.getByTestId('unit-option-UA010'));
            await waitFor(() => expect(mockSwitchUnit).toHaveBeenCalledWith('UA010'));
        });

        it('does not call switchUnit when the active unit is clicked', async () => {
            render(<UnitSwitcher/>);
            fireEvent.click(screen.getByTestId('unit-option-UA001'));
            await waitFor(() => expect(mockSwitchUnit).not.toHaveBeenCalled());
        });

        it('falls back to user.owned_units when availableUnits is empty', () => {
            mockAuth.availableUnits = [];
            render(<UnitSwitcher/>);
            expect(screen.getByTestId('unit-option-UA001')).toBeInTheDocument();
            expect(screen.getByTestId('unit-option-UA010')).toBeInTheDocument();
        });
    });
});
