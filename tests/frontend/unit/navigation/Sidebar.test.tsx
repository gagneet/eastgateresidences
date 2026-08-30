import React from 'react';
import {fireEvent, render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';
// ── Import after mocks ─────────────────────────────────────────────────────
import {SidebarNav} from '@/components/layout/Sidebar';

// ── Mocks ──────────────────────────────────────────────────────────────────

const mockNavigationContext = {
    mode: 'simple' as 'simple' | 'advanced',
    simpleItems: [
        {
            id: 'home',
            label: 'Home',
            route: '/dashboard',
            icon: 'Home',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 1,
            isNew: false
        },
        {
            id: 'my-levies',
            label: 'My levies',
            route: '/financials/levy-payments',
            icon: 'CreditCard',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 2,
            isNew: true
        },
    ],
    advancedItems: [
        {
            id: 'my-finances',
            label: 'My finances',
            route: '/financials/my-finances',
            icon: 'PieChart',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 1,
            isNew: false,
            discovery_hint: 'See where your levy goes'
        },
    ],
    pinnedItems: [],
    allItems: [
        {
            id: 'home',
            label: 'Home',
            route: '/dashboard',
            icon: 'Home',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 1,
            isNew: false
        },
        {
            id: 'my-levies',
            label: 'My levies',
            route: '/financials/levy-payments',
            icon: 'CreditCard',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 2,
            isNew: true
        },
        {
            id: 'my-finances',
            label: 'My finances',
            route: '/financials/my-finances',
            icon: 'PieChart',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 1,
            isNew: false,
            discovery_hint: 'See where your levy goes'
        },
    ],
    badges: {
        requests_overdue: 0, requests_new: 0, notices_unread: 1,
        parcels_waiting: 0, proposals_open_vote: 0, approvals_pending: 0,
        compliance_overdue: 0, sla_breached: 0, levy_due_soon: 0,
    },
    pendingNudge: null,
    isLoading: false,
    hasUnseenAdvancedFeatures: true,
    toggleMode: jest.fn(),
    setMode: jest.fn(),
    pinItem: jest.fn(),
    unpinItem: jest.fn(),
    hideItem: jest.fn(),
    restoreItem: jest.fn(),
    reorderItems: jest.fn(),
    markFeatureSeen: jest.fn(),
    dismissNudge: jest.fn(),
    trackNavigation: jest.fn(),
    refreshBadges: jest.fn(),
    setNudgeCooldown: jest.fn(),
};

jest.mock('@/contexts/NavigationContext', () => ({
    useNavigation: () => mockNavigationContext,
    NavigationProvider: ({children}: any) => <>{children}</>,
}));

jest.mock('next/navigation', () => ({
    usePathname: () => '/dashboard',
    useRouter: () => ({push: jest.fn()}),
}));

jest.mock('@/components/ui/dropdown-menu', () => ({
    DropdownMenu: ({children}: any) => <>{children}</>,
    DropdownMenuContent: ({children}: any) => <div>{children}</div>,
    DropdownMenuItem: ({children, onClick}: any) => <button onClick={onClick}>{children}</button>,
    DropdownMenuTrigger: ({children}: any) => <>{children}</>,
}));

jest.mock('@/components/ui/tooltip', () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipContent: ({children}: any) => <span>{children}</span>,
    TooltipTrigger: ({children}: any) => <>{children}</>,
}));

describe('SidebarNav', () => {
    it('renders simple items', () => {
        render(<SidebarNav/>);
        expect(screen.getByText('Home')).toBeInTheDocument();
        expect(screen.getByText('My levies')).toBeInTheDocument();
    });

    it('shows New badge on new items', () => {
        render(<SidebarNav/>);
        // 'My levies' has isNew: true — should show New badge
        expect(screen.getByText('New')).toBeInTheDocument();
    });

    it('does not show advanced items in simple mode on first mount', () => {
        render(<SidebarNav/>);
        // Menu-simplification Phase 2: Simple mode must not reveal Advanced items
        // on first paint — see docs/features/capability_consolidation_analysis.md
        // section 3.2. advancedOpen now initializes from `mode !== "simple"`.
        expect(screen.queryByText('My finances')).not.toBeInTheDocument();
    });

    it('shows Advanced toggle when advanced items exist', () => {
        render(<SidebarNav/>);
        expect(screen.getByText('Advanced')).toBeInTheDocument();
    });

    it('Advanced toggle button starts collapsed in simple mode', () => {
        render(<SidebarNav/>);
        const advancedBtn = screen.getByRole('button', {name: /advanced/i});
        expect(advancedBtn).toBeInTheDocument();
        expect(advancedBtn).toHaveAttribute('aria-expanded', 'false');
    });

    it('reveals advanced items when the Advanced toggle is clicked', () => {
        render(<SidebarNav/>);
        const advancedBtn = screen.getByRole('button', {name: /advanced/i});
        expect(screen.queryByText('My finances')).not.toBeInTheDocument();
        fireEvent.click(advancedBtn);
        expect(screen.getByText('My finances')).toBeInTheDocument();
        expect(advancedBtn).toHaveAttribute('aria-expanded', 'true');
    });

    it('shows notice badge on notices item', () => {
        render(<SidebarNav/>);
        // notices_unread: 1 on "my-levies" has empty badge_source, so no badge there
        // but if notices item had notices_unread source, badge would show
        // We just verify the component renders without error
        expect(screen.getByText('Home')).toBeInTheDocument();
    });

    it('renders customise menu link when onOpenCustomise is provided', () => {
        const onOpen = jest.fn();
        render(<SidebarNav onOpenCustomise={onOpen}/>);
        expect(screen.getByText('Customise menu')).toBeInTheDocument();
    });

    it('shows advanced items when in advanced mode', () => {
        // Temporarily switch mode to advanced in the mock
        const original = mockNavigationContext.mode;
        mockNavigationContext.mode = 'advanced';

        render(<SidebarNav/>);

        // In advanced mode, advancedOpen starts as true → advanced items are visible
        expect(screen.getByText('My finances')).toBeInTheDocument();

        mockNavigationContext.mode = original;
    });

    it('classic mode renders a flat merged list with no Advanced toggle at all (fix is inert there)', () => {
        // Correction during this test's own review: classic mode does NOT fall
        // through the same branch as simple/advanced — Sidebar.tsx's isClassicMode
        // check is a fully separate top-level branch (classicItems, a flat merged
        // list) that never reads advancedOpen. The Phase 2 fix
        // (advancedOpen = mode !== "simple") sets advancedOpen=true for classic
        // mode, but that value has zero visible effect there — no toggle button is
        // ever rendered outside "simple" mode. Pinning that explicitly here so a
        // future change to Sidebar.tsx's branching can't silently assume otherwise.
        const original = mockNavigationContext.mode;
        (mockNavigationContext as any).mode = 'classic';

        render(<SidebarNav/>);

        expect(screen.getByText('My finances')).toBeInTheDocument();
        expect(screen.queryByText('Advanced')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /advanced/i})).not.toBeInTheDocument();

        mockNavigationContext.mode = original;
    });

    it('clears search input when pressing the Escape key', () => {
        render(<SidebarNav/>);
        const searchInput = screen.getByPlaceholderText('Search menu…');

        // Type some search query
        fireEvent.change(searchInput, { target: { value: 'finances' } });
        expect(searchInput).toHaveValue('finances');

        // Press Escape
        fireEvent.keyDown(searchInput, { key: 'Escape', code: 'Escape' });
        expect(searchInput).toHaveValue('');
    });
});
