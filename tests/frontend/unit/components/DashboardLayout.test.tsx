import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import DashboardLayout from '@/components/layout/DashboardLayout';
import {signOut as nextAuthSignOut} from 'next-auth/react';

// Mock Radix UI Tooltip to avoid missing TooltipProvider context in tests
jest.mock('@/components/ui/tooltip', () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipTrigger: ({children}: any) => <>{children}</>,
    TooltipContent: ({children}: any) => <span>{children}</span>,
    TooltipProvider: ({children}: any) => <>{children}</>,
}));

// Mock Radix UI DropdownMenu to avoid context issues
jest.mock('@/components/ui/dropdown-menu', () => ({
    DropdownMenu: ({children}: any) => <>{children}</>,
    DropdownMenuContent: ({children}: any) => <>{children}</>,
    DropdownMenuItem: ({children, onClick}: any) => <button onClick={onClick}>{children}</button>,
    DropdownMenuLabel: ({children}: any) => <span>{children}</span>,
    DropdownMenuSeparator: () => <hr/>,
    DropdownMenuTrigger: ({children}: any) => <>{children}</>,
}));

// Mock ScrollArea to avoid Radix context issues
jest.mock('@/components/ui/scroll-area', () => ({
    ScrollArea: ({children}: any) => <div>{children}</div>,
}));

jest.mock('next-auth/react', () => ({
    useSession: jest.fn(() => ({data: null, status: 'unauthenticated'})),
    SessionProvider: ({children}: any) => <>{children}</>,
    signIn: jest.fn(),
    signOut: jest.fn(),
}));

jest.mock('@/components/layout/NavCustomiseDrawer', () => ({
    NavCustomiseDrawer: () => null,
}));

jest.mock('@/components/layout/Sidebar', () => ({
    SidebarNav: () => null,
}));

jest.mock('@/contexts/NavigationContext', () => ({
    NavigationProvider: ({children}: any) => <>{children}</>,
    useNavigation: () => ({mode: 'classic', setMode: jest.fn()}),
}));

jest.mock('next/navigation', () => ({
    usePathname: () => '/dashboard',
    useRouter: () => mockRouter,
}));

const mockRouter = {push: jest.fn(), replace: jest.fn()};

const mockApi = {
    get: jest.fn(),
};

const neverResolvingRequest = () => new Promise<never>(() => {
});

let mockAuthState: any = {
    user: {id: 'u1', role: 'owner', full_name: 'Test Owner', is_approved: true, unit_number: 'UA001'},
    isImpersonating: false,
    logout: jest.fn(),
    hasPermission: jest.fn(() => false),
    isAdmin: jest.fn(() => false),
    isRealAdmin: jest.fn(() => false),
    isManager: jest.fn(() => false),
    isECMember: jest.fn(() => false),
    hasFeatureAccess: jest.fn(() => true),
    notifications: [],
    unreadCount: 0,
    markNotificationRead: jest.fn(),
    markAllRead: jest.fn(),
    pendingApprovalsCount: 0,
    fetchPendingApprovalsCount: jest.fn(),
    selectedBuilding: {id: '13195', name: 'East Gate Residences'},
    availableBuildings: [{id: '13195', name: 'East Gate Residences'}],
    switchBuilding: jest.fn(),
    api: mockApi,
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthState,
    AuthProvider: ({children}: any) => <>{children}</>,
}));

jest.mock('@/contexts/IntegrityContext', () => ({
    useIntegrity: () => ({isValid: true}),
}));

describe('DashboardLayout', () => {
    beforeEach(() => {
        (nextAuthSignOut as jest.Mock).mockResolvedValue(undefined);
        mockRouter.push.mockClear();
        mockRouter.replace.mockClear();
        mockApi.get.mockImplementation((url: string) => {
            if (url === '/engagement/nav/badges') {
                return Promise.resolve({data: {notices: 0, compliance: 0, tenant_approvals: 0, user_approvals: 0}});
            }
            if (url === '/settings') {
                // Most tests in this file do not assert the async settings side effect.
                // Keeping the request pending prevents unrelated post-render state updates
                // from triggering React act(...) noise.
                return neverResolvingRequest();
            }
            return Promise.resolve({data: {}});
        });
        mockAuthState = {
            user: {id: 'u1', role: 'owner', full_name: 'Test Owner', is_approved: true, unit_number: 'UA001'},
            isImpersonating: false,
            logout: jest.fn(),
            hasPermission: jest.fn(() => false),
            isAdmin: jest.fn(() => false),
            isRealAdmin: jest.fn(() => false),
            isManager: jest.fn(() => false),
            isECMember: jest.fn(() => false),
            // The Request Maintenance FAB gates on the effective-role helpers rather
            // than a raw user.role string, so the mock has to supply them. Defaults
            // mirror the default mock user above (an owner).
            isOwner: jest.fn(() => true),
            isTenant: jest.fn(() => false),
            isGuest: jest.fn(() => false),
            hasFeatureAccess: jest.fn(() => true),
            notifications: [],
            unreadCount: 0,
            markNotificationRead: jest.fn(),
            markAllRead: jest.fn(),
            pendingApprovalsCount: 0,
            fetchPendingApprovalsCount: jest.fn(),
            selectedBuilding: {id: '13195', name: 'East Gate Residences'},
            availableBuildings: [{id: '13195', name: 'East Gate Residences'}],
            switchBuilding: jest.fn(),
            api: mockApi,
        };
    });

    it('renders without crashing for owner role', () => {
        render(
            <DashboardLayout>
                <div>Test Content</div>
            </DashboardLayout>
        );
        const nav = document.querySelector('nav');
        expect(nav).not.toBeNull();
    });

    it('shows navigation sidebar', () => {
        render(
            <DashboardLayout>
                <div>Test Content</div>
            </DashboardLayout>
        );
        // Sidebar navigation should render — look for a nav or aside element
        const nav = document.querySelector('nav');
        expect(nav).toBeInTheDocument();
    });

    it('renders children content', () => {
        render(
            <DashboardLayout>
                <div data-testid="child-content">Child</div>
            </DashboardLayout>
        );
        expect(screen.getByTestId('child-content')).toBeInTheDocument();
    });

    it('waits for NextAuth sign-out before clearing local auth state and redirecting', async () => {
        render(
            <DashboardLayout>
                <div>Test Content</div>
            </DashboardLayout>
        );

        fireEvent.click(screen.getAllByTestId('sidebar-logout-btn')[0]);

        await waitFor(() => expect(nextAuthSignOut).toHaveBeenCalledWith({redirect: false}));
        await waitFor(() => expect(mockAuthState.logout).toHaveBeenCalled());
        expect(mockRouter.replace).toHaveBeenCalledWith('/');
        expect((nextAuthSignOut as jest.Mock).mock.invocationCallOrder[0]).toBeLessThan(
            mockAuthState.logout.mock.invocationCallOrder[0],
        );
    });

    it('requests resident management badge data for managers', async () => {
        mockAuthState = {
            ...mockAuthState,
            user: {id: 'm1', role: 'strata_manager', full_name: 'Manager', is_approved: true},
            hasPermission: jest.fn((key: string) => key === 'can_manage_users'),
            isManager: jest.fn(() => true),
        };
        mockApi.get.mockImplementation((url: string) => {
            if (url === '/engagement/nav/badges') {
                return Promise.resolve({data: {notices: 0, compliance: 0, tenant_approvals: 0, user_approvals: 7}});
            }
            if (url === '/settings') {
                return neverResolvingRequest();
            }
            return Promise.resolve({data: {}});
        });

        render(
            <DashboardLayout>
                <div>Test Content</div>
            </DashboardLayout>
        );

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/engagement/nav/badges');
        });
    });

    it('shows Financial Onboarding to finance managers with reconstruction access', () => {
        mockAuthState = {
            ...mockAuthState,
            user: {id: 'm1', role: 'strata_manager', full_name: 'Manager', is_approved: true},
            hasPermission: jest.fn((key: string) => key === 'can_manage_finances'),
            isManager: jest.fn(() => true),
            selectedBuilding: {id: 'scheme-1', building_id: '13195', name: 'East Gate Residences'},
        };

        render(
            <DashboardLayout>
                <div>Test Content</div>
            </DashboardLayout>,
        );

        fireEvent.change(screen.getByLabelText('Search navigation menu'), {target: {value: 'financial onboarding'}});

        expect(screen.getAllByText('Financial Onboarding').length).toBeGreaterThan(0);
    });

    // ── Sidebar user info: building_admin role + ec_position + unit_number ──────

    describe('Sidebar user info section', () => {
        it('shows "Strata Admin" label for strata_admin role', () => {
            // Post-migration 0025: building_admin → strata_admin; the display
            // label is now "Strata Admin", not "Building Admin".
            mockAuthState = {
                ...mockAuthState,
                user: {
                    id: 'ba1',
                    role: 'strata_admin',
                    full_name: 'Strata Admin User',
                    is_approved: true,
                    unit_number: null
                },
                isManager: jest.fn(() => true),
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            expect(screen.getAllByText('Strata Admin').length).toBeGreaterThan(0);
        });

        it('shows EC position label for ec_member with ec_position=CHAIRMAN', () => {
            mockAuthState = {
                ...mockAuthState,
                user: {id: 'ec1', role: 'ec_member', ec_position: 'CHAIRMAN', full_name: 'EC Chair', is_approved: true},
                isManager: jest.fn(() => true),
                isECMember: jest.fn(() => true),
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            // Role label + position: "EC Member · Chairman"
            const labels = screen.getAllByText(/EC Member · Chairman/);
            expect(labels.length).toBeGreaterThan(0);
        });

        it('shows EC position label for ec_member with ec_position=TREASURER', () => {
            mockAuthState = {
                ...mockAuthState,
                user: {
                    id: 'ec2',
                    role: 'ec_member',
                    ec_position: 'TREASURER',
                    full_name: 'EC Treasurer',
                    is_approved: true
                },
                isECMember: jest.fn(() => true),
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            expect(screen.getAllByText(/EC Member · Treasurer/).length).toBeGreaterThan(0);
        });

        it('shows EC position label for ec_member with ec_position=SECRETARY', () => {
            mockAuthState = {
                ...mockAuthState,
                user: {
                    id: 'ec3',
                    role: 'ec_member',
                    ec_position: 'SECRETARY',
                    full_name: 'EC Secretary',
                    is_approved: true
                },
                isECMember: jest.fn(() => true),
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            expect(screen.getAllByText(/EC Member · Secretary/).length).toBeGreaterThan(0);
        });

        it('does not append position for MEMBER ec_position', () => {
            mockAuthState = {
                ...mockAuthState,
                user: {id: 'ec4', role: 'ec_member', ec_position: 'MEMBER', full_name: 'EC Regular', is_approved: true},
                isECMember: jest.fn(() => true),
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            expect(screen.queryByText(/EC Member · Member/)).toBeNull();
        });

        it('shows unit number when user has unit_number', () => {
            mockAuthState = {
                ...mockAuthState,
                user: {id: 'o1', role: 'owner', full_name: 'Unit Owner', is_approved: true, unit_number: 'UA042'},
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            expect(screen.getAllByText('Unit UA042').length).toBeGreaterThan(0);
        });

        it('does not show unit line when user has no unit_number', () => {
            mockAuthState = {
                ...mockAuthState,
                user: {
                    id: 'sm1',
                    role: 'strata_manager',
                    full_name: 'Strata Mgr',
                    is_approved: true,
                    unit_number: null
                },
                isManager: jest.fn(() => true),
            };
            render(<DashboardLayout>
                <div>x</div>
            </DashboardLayout>);
            expect(screen.queryByText(/^Unit /)).toBeNull();
        });
    });
});
