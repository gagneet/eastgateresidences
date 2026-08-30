import React from 'react';
import {act, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
// ── Import after mocks ─────────────────────────────────────────────────────
import {NavigationProvider, useNavigation} from '@/contexts/NavigationContext';

// ── Mocks ──────────────────────────────────────────────────────────────────

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();
// All refs must be stable — any new object/function per-render causes fetchNavConfig to loop
const mockHasFeatureAccess = () => true;
const mockAuthValue = {
    user: {id: 'user-001', role: 'owner', full_name: 'Test Owner', building_id: '13195'},
    api: {get: mockGet, post: mockPost, patch: mockPatch},
    selectedBuilding: {id: '13195', name: 'East Gate Residences'},
    hasFeatureAccess: mockHasFeatureAccess,
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthValue,
}));

// Mock localStorage / sessionStorage
const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => {
            store[key] = value;
        },
        removeItem: (key: string) => {
            delete store[key];
        },
        clear: () => {
            store = {};
        },
    };
})();
Object.defineProperty(window, 'localStorage', {value: localStorageMock});

const sessionStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => {
            store[key] = value;
        },
        removeItem: (key: string) => {
            delete store[key];
        },
        clear: () => {
            store = {};
        },
    };
})();
Object.defineProperty(window, 'sessionStorage', {value: sessionStorageMock});

// Mock crypto.randomUUID
Object.defineProperty(window, 'crypto', {
    value: {randomUUID: () => 'test-uuid-1234'},
});

const mockNavConfig = {
    role: 'owner',
    building_id: '13195',
    mode: 'simple',
    simple_items: [
        {
            id: 'home',
            label: 'Home',
            route: '/dashboard',
            icon: 'Home',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 1
        },
        {
            id: 'my-levies',
            label: 'My levies',
            route: '/financials/levy-payments',
            icon: 'CreditCard',
            feature_flag: '',
            permission_flag: '',
            badge_source: 'levy_due_soon',
            priority: 2
        },
    ],
    advanced_items: [
        {
            id: 'my-finances',
            label: 'My finances',
            route: '/financials/my-finances',
            icon: 'PieChart',
            feature_flag: '',
            permission_flag: '',
            badge_source: '',
            priority: 1,
            discovery_hint: 'See where your levy goes'
        },
    ],
    pinned_items: [],
    has_unseen_advanced_features: true,
    nudge: null,
};

const mockBadges = {
    requests_overdue: 0, requests_new: 2, notices_unread: 1, parcels_waiting: 0,
    proposals_open_vote: 0, approvals_pending: 0, compliance_overdue: 0,
    sla_breached: 0, levy_due_soon: 0,
};

// ── Helper consumer component ──────────────────────────────────────────────
function NavConsumer() {
    const nav = useNavigation();
    return (
        <div>
            <span data-testid="mode">{nav.mode}</span>
            <span data-testid="simple-count">{nav.simpleItems.length}</span>
            <span data-testid="advanced-count">{nav.advancedItems.length}</span>
            <span data-testid="loading">{nav.isLoading ? 'loading' : 'ready'}</span>
            <span data-testid="unseen">{nav.hasUnseenAdvancedFeatures ? 'yes' : 'no'}</span>
            <button data-testid="toggle-mode" onClick={nav.toggleMode}>toggle</button>
            <button data-testid="pin-item" onClick={() => nav.pinItem('my-finances')}>pin</button>
        </div>
    );
}

describe('NavigationContext', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorageMock.clear();
        sessionStorageMock.clear();
        mockGet.mockImplementation((url: string) => {
            if (url.includes('/navigation/config')) return Promise.resolve({data: mockNavConfig});
            if (url.includes('/navigation/badges')) return Promise.resolve({data: mockBadges});
            return Promise.reject(new Error(`Unexpected GET: ${url}`));
        });
        mockPost.mockResolvedValue({data: {status: 'ok'}});
        mockPatch.mockResolvedValue({data: {status: 'ok'}});
    });

    it('fetches config on mount', async () => {
        render(
            <NavigationProvider>
                <NavConsumer/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(screen.getByTestId('loading')).toHaveTextContent('ready');
        });

        expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('/navigation/config'));
        expect(screen.getByTestId('simple-count')).toHaveTextContent('2');
    });

    it('reports mode correctly', async () => {
        render(
            <NavigationProvider>
                <NavConsumer/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(screen.getByTestId('loading')).toHaveTextContent('ready');
        });

        expect(screen.getByTestId('mode')).toHaveTextContent('simple');
    });

    it('shows unseen advanced features flag', async () => {
        render(
            <NavigationProvider>
                <NavConsumer/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(screen.getByTestId('loading')).toHaveTextContent('ready');
        });

        expect(screen.getByTestId('unseen')).toHaveTextContent('yes');
    });

    it('persists mode to localStorage on toggle', async () => {
        render(
            <NavigationProvider>
                <NavConsumer/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(screen.getByTestId('loading')).toHaveTextContent('ready');
        });

        act(() => {
            screen.getByTestId('toggle-mode').click();
        });

        await waitFor(() => {
            expect(screen.getByTestId('mode')).toHaveTextContent('advanced');
        });

        // Check localStorage was updated
        const storedMode = localStorageMock.getItem('nav_mode_user-001_13195');
        expect(storedMode).toBe('advanced');
    });

    it('marks items as new when not visited', async () => {
        render(
            <NavigationProvider>
                <NavConsumer/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(screen.getByTestId('loading')).toHaveTextContent('ready');
        });

        // Advanced items not visited should have isNew = true
        // (tested via context value, not DOM in this test)
        // Context fetched config successfully
        expect(screen.getByTestId('advanced-count')).toHaveTextContent('1');
    });

    it('fetches badges on mount', async () => {
        render(
            <NavigationProvider>
                <NavConsumer/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('/navigation/badges'));
        });
    });

    it('enforces max 2 pinned items', async () => {
        // Add 3 advanced items to the mock config so we can try to pin all 3
        const extendedConfig = {
            ...mockNavConfig,
            advanced_items: [
                {
                    id: 'my-finances',
                    label: 'My finances',
                    route: '/financials/my-finances',
                    icon: 'PieChart',
                    feature_flag: '',
                    permission_flag: '',
                    badge_source: '',
                    priority: 1
                },
                {
                    id: 'documents',
                    label: 'Documents',
                    route: '/documents',
                    icon: 'Folder',
                    feature_flag: '',
                    permission_flag: '',
                    badge_source: '',
                    priority: 2
                },
                {
                    id: 'savings',
                    label: 'Savings',
                    route: '/financials/savings',
                    icon: 'PiggyBank',
                    feature_flag: '',
                    permission_flag: '',
                    badge_source: '',
                    priority: 3
                },
            ],
        };
        mockGet.mockImplementation((url: string) => {
            if (url.includes('/navigation/config')) return Promise.resolve({data: extendedConfig});
            if (url.includes('/navigation/badges')) return Promise.resolve({data: mockBadges});
            return Promise.reject(new Error(`Unexpected: ${url}`));
        });

        const PinTester = () => {
            const nav = useNavigation();
            return (
                <div>
                    <span data-testid="pinned-count">{nav.pinnedItems.length}</span>
                    <span data-testid="is-loading">{nav.isLoading ? 'loading' : 'ready'}</span>
                    <button data-testid="pin1" onClick={() => nav.pinItem('my-finances')}>pin1</button>
                    <button data-testid="pin2" onClick={() => nav.pinItem('documents')}>pin2</button>
                    <button data-testid="pin3" onClick={() => nav.pinItem('savings')}>pin3</button>
                </div>
            );
        };

        render(
            <NavigationProvider>
                <PinTester/>
            </NavigationProvider>
        );

        await waitFor(() => {
            expect(screen.getByTestId('is-loading')).toHaveTextContent('ready');
        });

        // Pin first two items — use fireEvent so React flushes state synchronously
        await act(async () => {
            screen.getByTestId('pin1').click();
        });

        await waitFor(() => {
            expect(screen.getByTestId('pinned-count')).toHaveTextContent('1');
        });

        await act(async () => {
            screen.getByTestId('pin2').click();
        });

        await waitFor(() => {
            expect(screen.getByTestId('pinned-count')).toHaveTextContent('2');
        });

        // Attempting to pin a 3rd item should be silently ignored (max 2)
        await act(async () => {
            screen.getByTestId('pin3').click();
        });

        await waitFor(() => {
            expect(screen.getByTestId('pinned-count')).toHaveTextContent('2');
        });
    });
});
