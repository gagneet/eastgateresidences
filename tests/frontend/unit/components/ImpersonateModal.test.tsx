/**
 * Tests for ImpersonateModal component.
 *
 * Covers:
 * 1. Modal renders when isOpen=true
 * 2. Modal does not render when isOpen=false
 * 3. Search input triggers API call with GET /users?status=active
 * 4. Results filtered to exclude super_admin role users
 * 5. Results display unit_owner_name as primary name (when present)
 * 6. Results display full_name as fallback when unit_owner_name is absent
 * 7. Amber "Portal: {full_name}" sub-label renders when unit_owner_name !== full_name
 * 8. Sub-label NOT rendered when unit_owner_name === full_name
 * 9. Unit badge renders when user has unit_number
 * 10. Impersonate button disabled when no user selected
 * 11. Impersonate button enabled when user selected AND building selected
 * 12. impersonate() called with correct user.id and selectedBuildingId on button click
 * 13. Modal closes after successful impersonation
 * 14. Search includes unit_owner_name field (searching by strata roll name works)
 */

import React from 'react';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

// ─── UI component mocks ──────────────────────────────────────────────────────
// Mock Radix Dialog to render children when open=true
jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({open, onOpenChange, children}: any) =>
        open ? <div data-testid="dialog-root">{children}</div> : null,
    DialogContent: ({children}: any) => <div data-testid="dialog-content">{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <h2>{children}</h2>,
    DialogDescription: ({children}: any) => <p>{children}</p>,
    DialogFooter: ({children}: any) => <div data-testid="dialog-footer">{children}</div>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, disabled, onClick, variant}: any) => (
        <button disabled={disabled} onClick={onClick} data-variant={variant}>
            {children}
        </button>
    ),
}));

jest.mock('@/components/ui/input', () => ({
    Input: ({value, onChange, placeholder, autoFocus}: any) => (
        <input
            data-testid="search-input"
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            autoFocus={autoFocus}
        />
    ),
}));

jest.mock('@/components/ui/label', () => ({
    Label: ({children}: any) => <label>{children}</label>,
}));

jest.mock('@/components/ui/scroll-area', () => ({
    ScrollArea: ({children}: any) => <div data-testid="scroll-area">{children}</div>,
}));

jest.mock('@/components/ui/badge', () => ({
    Badge: ({children, variant}: any) => (
        <span data-testid="badge" data-variant={variant}>{children}</span>
    ),
}));

jest.mock('@/components/ui/avatar', () => ({
    Avatar: ({children}: any) => <div>{children}</div>,
    AvatarImage: ({src}: any) => <img src={src} alt=""/>,
    AvatarFallback: ({children}: any) => <span data-testid="avatar-fallback">{children}</span>,
}));

jest.mock('@/components/ui/select', () => ({
    Select: ({value, onValueChange, children}: any) => (
        <div data-testid="select-wrapper">
            <select
                data-testid="building-select"
                value={value}
                onChange={(e) => onValueChange && onValueChange(e.target.value)}
            >
                {children}
            </select>
        </div>
    ),
    SelectContent: ({children}: any) => <>{children}</>,
    SelectItem: ({value, children}: any) => <option value={value}>{children}</option>,
    SelectTrigger: ({children}: any) => <>{children}</>,
    SelectValue: ({placeholder}: any) => <span>{placeholder}</span>,
}));

// Mock lucide-react icons to avoid SVG rendering issues
jest.mock('lucide-react', () => ({
    Search: () => <span data-testid="icon-search"/>,
    User: () => <span data-testid="icon-user"/>,
    Building: () => <span data-testid="icon-building"/>,
    Loader2: () => <span data-testid="icon-loader"/>,
    UserSearch: () => <span data-testid="icon-usersearch"/>,
    ShieldAlert: () => <span data-testid="icon-shield"/>,
    ArrowRight: () => <span data-testid="icon-arrow"/>,
}));

// Mock lib/utils
jest.mock('@/lib/utils', () => ({
    getInitials: (name: string) => (name || '').split(' ').map((n: string) => n[0]).join('').toUpperCase(),
    roleDisplayNames: {
        owner: 'Owner',
        chairman: 'Chairman',
        ec_member: 'EC Member',
        super_admin: 'Super Admin',
    },
    cn: (...args: any[]) => args.filter(Boolean).join(' '),
}));

// ─── AuthContext mock ────────────────────────────────────────────────────────
const mockImpersonate = jest.fn();
const mockApiGet = jest.fn();

const mockApi = {
    get: mockApiGet,
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
};

// Real shape from GET /buildings/me: `id` is the Postgres scheme UUID,
// `building_id` is the plan number the backend membership check and JWT
// claim expect — they must be kept distinct so tests catch UUID/plan-number
// precedence regressions.
const mockAvailableBuildings = [
    {id: 'aaaaaaaa-0000-4000-8000-000000000001', building_id: '13195', name: 'East Gate Residences'},
    {id: 'bbbbbbbb-0000-4000-8000-000000000002', building_id: '16244', name: 'Sierra Gungahlin'},
];

const mockSelectedBuilding = {id: 'aaaaaaaa-0000-4000-8000-000000000001', building_id: '13195', name: 'East Gate Residences'};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        impersonate: mockImpersonate,
        availableBuildings: mockAvailableBuildings,
        selectedBuilding: mockSelectedBuilding,
    }),
}));

// ─── Test data ───────────────────────────────────────────────────────────────
const mockUsers = [
    {
        id: 'u1',
        full_name: 'Jane Smith',
        unit_owner_name: 'Ms Jane Pearce & Mr John Han',
        email: 'jane@test.com',
        unit_number: 'UA001',
        role: 'owner',
    },
    {
        id: 'u2',
        full_name: 'Anthony McDonald',
        unit_owner_name: 'Anthony McDonald & Rose Marimon',
        email: 'anthony@test.com',
        unit_number: 'UA063',
        role: 'chairman',
    },
    {
        id: 'u3',
        full_name: 'Admin User',
        unit_owner_name: null,
        email: 'admin@test.com',
        unit_number: null,
        role: 'super_admin',
    },
];

// A user whose unit_owner_name exactly matches full_name (no sub-label expected)
const userExactMatch = {
    id: 'u4',
    full_name: 'Avneet Rooprai',
    unit_owner_name: 'Avneet Rooprai',
    email: 'avneet@test.com',
    unit_number: 'TH087',
    role: 'owner',
};

// A user with no unit_owner_name (falls back to full_name)
const userNoRollName = {
    id: 'u5',
    full_name: 'Bob Tenant',
    unit_owner_name: null,
    email: 'bob@test.com',
    unit_number: null,
    role: 'tenant',
};

// ─── Import component under test ─────────────────────────────────────────────
// Use require to avoid hoisting issues with mocks
// eslint-disable-next-line @typescript-eslint/no-var-requires
const ImpersonateModal = require('@/components/layout/ImpersonateModal').default;

// ─── Helpers ─────────────────────────────────────────────────────────────────
function renderModal(props: { isOpen?: boolean; onClose?: () => void } = {}) {
    const onClose = props.onClose ?? jest.fn();
    const isOpen = props.isOpen ?? true;
    return render(<ImpersonateModal isOpen={isOpen} onClose={onClose}/>);
}

/**
 * Type a search term and wait for the debounced API call to fire.
 * The component debounces search by 300 ms — we advance fake timers.
 */
async function typeAndSearch(term: string) {
    const input = screen.getByTestId('search-input');
    await act(async () => {
        fireEvent.change(input, {target: {value: term}});
        // Advance past the 300 ms debounce
        jest.advanceTimersByTime(350);
    });
    // Let async state updates settle
    await act(async () => {
    });
}

// ─── Setup / teardown ────────────────────────────────────────────────────────
beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    // Default: API returns all mock users
    mockApiGet.mockResolvedValue({data: mockUsers});
    mockImpersonate.mockResolvedValue(true);
});

afterEach(() => {
    jest.useRealTimers();
});

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('ImpersonateModal', () => {
    // 1. Modal renders when isOpen=true
    it('renders the modal when isOpen is true', () => {
        renderModal({isOpen: true});
        expect(screen.getByTestId('dialog-root')).toBeInTheDocument();
        expect(screen.getByText('User Impersonation')).toBeInTheDocument();
    });

    // 2. Modal does not render when isOpen=false
    it('does not render the modal when isOpen is false', () => {
        renderModal({isOpen: false});
        expect(screen.queryByTestId('dialog-root')).not.toBeInTheDocument();
        expect(screen.queryByText('User Impersonation')).not.toBeInTheDocument();
    });

    // 3. Search input triggers API call with GET /users?status=active
    it('calls GET /users?status=active when search term is typed', async () => {
        renderModal();
        await typeAndSearch('jane');
        expect(mockApiGet).toHaveBeenCalledWith('/users?status=active');
    });

    // 4. Results filtered to exclude super_admin role users
    it('excludes super_admin users from displayed results', async () => {
        renderModal();
        await typeAndSearch('admin');

        // "Admin User" has role super_admin — should not appear
        // The filter is client-side; because we search "admin" which matches their email,
        // they would appear if not filtered. Confirm they are absent.
        await waitFor(() => {
            expect(screen.queryByText('Admin User')).not.toBeInTheDocument();
        });
    });

    // 5. Results display unit_owner_name as primary name (when present)
    it('shows unit_owner_name as the primary bold name when present', async () => {
        renderModal();
        await typeAndSearch('jane');

        await waitFor(() => {
            // Primary display: strata roll name
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });
    });

    // 6. Results display full_name as fallback when unit_owner_name is absent
    it('shows full_name as primary name when unit_owner_name is absent', async () => {
        mockApiGet.mockResolvedValue({data: [userNoRollName]});
        renderModal();
        await typeAndSearch('bob');

        await waitFor(() => {
            expect(screen.getByText('Bob Tenant')).toBeInTheDocument();
        });
    });

    // 7. Amber "Portal: {full_name}" sub-label renders when unit_owner_name !== full_name
    it('renders amber Portal sub-label when unit_owner_name differs from full_name', async () => {
        renderModal();
        await typeAndSearch('jane');

        await waitFor(() => {
            // The sub-label text: "Portal: Jane Smith"
            expect(screen.getByText('Portal: Jane Smith')).toBeInTheDocument();
        });
    });

    // 8. Sub-label NOT rendered when unit_owner_name === full_name
    it('does not render Portal sub-label when unit_owner_name equals full_name', async () => {
        mockApiGet.mockResolvedValue({data: [userExactMatch]});
        renderModal();
        await typeAndSearch('avneet');

        await waitFor(() => {
            expect(screen.getByText('Avneet Rooprai')).toBeInTheDocument();
        });

        // No "Portal:" sub-label since names match
        expect(screen.queryByText(/Portal:/)).not.toBeInTheDocument();
    });

    // 9. Unit badge renders when user has unit_number
    it('renders a unit badge when user has a unit_number', async () => {
        renderModal();
        await typeAndSearch('jane');

        await waitFor(() => {
            // Badge contains "UNIT UA001"
            expect(screen.getByText('UNIT UA001')).toBeInTheDocument();
        });
    });

    it('does not render a unit badge when user has no unit_number', async () => {
        mockApiGet.mockResolvedValue({data: [userNoRollName]});
        renderModal();
        await typeAndSearch('bob');

        await waitFor(() => {
            expect(screen.getByText('Bob Tenant')).toBeInTheDocument();
        });

        // No "UNIT" prefix since unit_number is null
        expect(screen.queryByText(/^UNIT /)).not.toBeInTheDocument();
    });

    // 10. Impersonate button disabled when no user selected
    it('keeps the Impersonate button disabled when no user is selected', () => {
        renderModal();
        const impersonateBtn = screen.getByRole('button', {name: /Impersonate/i});
        expect(impersonateBtn).toBeDisabled();
    });

    // 11. Impersonate button enabled when user selected AND building selected
    it('enables the Impersonate button after selecting a user (building pre-selected)', async () => {
        renderModal();
        await typeAndSearch('jane');

        await waitFor(() => {
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });

        // Click the user card to select it
        const userCard = screen.getByText('Ms Jane Pearce & Mr John Han').closest('button');
        expect(userCard).not.toBeNull();
        fireEvent.click(userCard!);

        // Building is pre-selected to selectedBuilding.id ('13195') on open,
        // so the button should now be enabled.
        await waitFor(() => {
            const impersonateBtn = screen.getByRole('button', {name: /Impersonate/i});
            expect(impersonateBtn).not.toBeDisabled();
        });
    });

    // 12. impersonate() called with correct user.id and selectedBuildingId on button click
    it('calls impersonate() with the correct user.id and building id on click', async () => {
        renderModal();
        await typeAndSearch('jane');

        await waitFor(() => {
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });

        // Select the user
        const userCard = screen.getByText('Ms Jane Pearce & Mr John Han').closest('button');
        fireEvent.click(userCard!);

        await waitFor(() => {
            const impersonateBtn = screen.getByRole('button', {name: /Impersonate/i});
            expect(impersonateBtn).not.toBeDisabled();
        });

        const impersonateBtn = screen.getByRole('button', {name: /Impersonate/i});
        await act(async () => {
            fireEvent.click(impersonateBtn);
        });

        expect(mockImpersonate).toHaveBeenCalledWith('u1', '13195');
    });

    // 13. Modal closes after successful impersonation
    it('calls onClose after successful impersonation', async () => {
        const onClose = jest.fn();
        mockImpersonate.mockResolvedValue(true);

        render(<ImpersonateModal isOpen={true} onClose={onClose}/>);
        await typeAndSearch('jane');

        await waitFor(() => {
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });

        const userCard = screen.getByText('Ms Jane Pearce & Mr John Han').closest('button');
        fireEvent.click(userCard!);

        await waitFor(() => {
            expect(screen.getByRole('button', {name: /Impersonate/i})).not.toBeDisabled();
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', {name: /Impersonate/i}));
        });

        await waitFor(() => {
            expect(onClose).toHaveBeenCalled();
        });
    });

    it('does NOT call onClose when impersonation fails', async () => {
        const onClose = jest.fn();
        mockImpersonate.mockResolvedValue(false);

        render(<ImpersonateModal isOpen={true} onClose={onClose}/>);
        await typeAndSearch('jane');

        await waitFor(() => {
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Ms Jane Pearce & Mr John Han').closest('button')!);

        await waitFor(() => {
            expect(screen.getByRole('button', {name: /Impersonate/i})).not.toBeDisabled();
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', {name: /Impersonate/i}));
        });

        await waitFor(() => {
            expect(mockImpersonate).toHaveBeenCalled();
        });

        expect(onClose).not.toHaveBeenCalled();
    });

    // 14. Search includes unit_owner_name in filter (searching by strata roll name works)
    it('finds a user by strata roll name (unit_owner_name) even when it differs from full_name', async () => {
        // The API returns all users; the component filters client-side.
        // "Pearce" is in unit_owner_name but NOT in full_name ("Jane Smith").
        renderModal();
        await typeAndSearch('pearce');

        await waitFor(() => {
            // The strata roll name should appear as the primary result
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });

        // The portal (full_name) sub-label should also be visible
        expect(screen.getByText('Portal: Jane Smith')).toBeInTheDocument();
    });

    it('finds a user by portal name (full_name) as well', async () => {
        renderModal();
        await typeAndSearch('jane smith');

        await waitFor(() => {
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });
    });

    // Multiple results: chairman also appears with roll name
    it('renders the roll name for chairman with a differing full_name', async () => {
        renderModal();
        await typeAndSearch('anthony');

        await waitFor(() => {
            expect(screen.getByText('Anthony McDonald & Rose Marimon')).toBeInTheDocument();
        });
        // Portal label should appear since "Anthony McDonald & Rose Marimon" !== "Anthony McDonald"
        expect(screen.getByText('Portal: Anthony McDonald')).toBeInTheDocument();
    });

    // API error does not crash the component
    it('handles API errors gracefully without crashing', async () => {
        mockApiGet.mockRejectedValue(new Error('Network error'));
        renderModal();

        await expect(typeAndSearch('any')).resolves.not.toThrow();

        // Modal is still mounted
        expect(screen.getByTestId('dialog-root')).toBeInTheDocument();
    });

    // No API call for empty search term
    it('does not call the API when search term is empty', async () => {
        renderModal();

        await act(async () => {
            jest.advanceTimersByTime(350);
        });

        expect(mockApiGet).not.toHaveBeenCalled();
    });

    // Cancel button calls onClose
    it('calls onClose when Cancel button is clicked', () => {
        const onClose = jest.fn();
        render(<ImpersonateModal isOpen={true} onClose={onClose}/>);
        fireEvent.click(screen.getByRole('button', {name: /Cancel/i}));
        expect(onClose).toHaveBeenCalled();
    });

    // Building selector shows when multiple buildings available
    it('renders a building selector when multiple buildings are available', () => {
        renderModal();
        // The Select component is rendered for multi-building setups
        expect(screen.getByTestId('building-select')).toBeInTheDocument();
    });

    // Changing building in selector updates selectedBuildingId
    it('updates selected building when the selector changes', async () => {
        renderModal();
        await typeAndSearch('jane');

        await waitFor(() => {
            expect(screen.getByText('Ms Jane Pearce & Mr John Han')).toBeInTheDocument();
        });

        // Select user
        fireEvent.click(screen.getByText('Ms Jane Pearce & Mr John Han').closest('button')!);

        // Change building to Sierra (16244)
        const select = screen.getByTestId('building-select');
        fireEvent.change(select, {target: {value: '16244'}});

        await waitFor(() => {
            expect(screen.getByRole('button', {name: /Impersonate/i})).not.toBeDisabled();
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', {name: /Impersonate/i}));
        });

        expect(mockImpersonate).toHaveBeenCalledWith('u1', '16244');
    });
});
