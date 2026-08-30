import React from 'react';
import {render, screen, waitFor, fireEvent, within} from '@testing-library/react';
import '@testing-library/jest-dom';
import JurisdictionalRulesPage from '@/pages/dashboard/admin/JurisdictionalRulesPage';

jest.mock('next/navigation', () => ({
    useRouter: () => ({replace: jest.fn(), push: jest.fn()}),
    usePathname: () => '/admin/jurisdictional-rules',
}));

// Tabs mock wires onValueChange through context so TabsTrigger clicks actually
// update the parent component state — validating real tab-switch behaviour.
jest.mock('@/components/ui/tabs', () => {
    const React = require('react') as typeof import('react');
    const TabsCtx = React.createContext<{ onValueChange?: (value: string) => void }>({});
    return {
        Tabs: ({children, value, onValueChange}: any) => (
            <TabsCtx.Provider value={{onValueChange}}>
                <div data-testid="tabs" data-value={value}>{children}</div>
            </TabsCtx.Provider>
        ),
        TabsList: ({children}: any) => <div>{children}</div>,
        TabsTrigger: ({children, value, 'data-testid': dt}: any) => {
            const {onValueChange} = React.useContext(TabsCtx);
            return (
                <button
                    data-testid={dt || `tab-${value}`}
                    onClick={() => onValueChange?.(value)}
                >
                    {children}
                </button>
            );
        },
        TabsContent: ({children, value}: any) => (
            <div data-testid={`tab-content-${value}`}>{children}</div>
        ),
    };
});

const mockActRules = {
    building_id: '13195',
    jurisdiction: 'ACT',
    meta: {
        jurisdiction: 'ACT',
        primary_legislation: 'Unit Titles (Management) Act 2011 (ACT)',
        last_reviewed: '2026-04-25',
    },
    typed_rules: {
        max_interest_rate_pa: '10',
        interest_grace_period_days: 0,
        can_transfer_between_funds: true,
        inter_fund_transfer_resolution_required: 'ordinary',
        overbudget_threshold_pct: null,
        trust_withdrawal_authority_roles: ['principal_licensee', 'strata_manager'],
        trial_balance_deadline_days: 15,
        audit_deadline: '30 September',
        pooled_trust_permitted: true,
        interest_belongs_to: 'scheme',
        committee_spending_cap_formula: null,
        min_withholding_pct_no_abn: '47',
    },
    full_rules: {
        max_interest_rate_pa: {value: 10, unit: 'percent', legislation: 'UTMA 2011 s.94', notes: 'Default 10% p.a.'},
        pooled_trust_permitted: {value: true, unit: 'bool', legislation: 'Agents Act 2003 Part 7', notes: 'Permitted.'},
    },
};

const mockAllJurisdictions = {
    jurisdictions: {
        ACT: {
            meta: {jurisdiction: 'ACT', primary_legislation: 'UTMA 2011'},
            typed_rules: {
                max_interest_rate_pa: '10',
                can_transfer_between_funds: true,
                pooled_trust_permitted: true,
                interest_belongs_to: 'scheme'
            },
            full_rules: {max_interest_rate_pa: {value: 10, legislation: 'UTMA 2011 s.94', notes: ''}},
        },
        NSW: {
            meta: {jurisdiction: 'NSW', primary_legislation: 'SSMA 2015'},
            typed_rules: {
                max_interest_rate_pa: '10',
                can_transfer_between_funds: true,
                pooled_trust_permitted: true,
                interest_belongs_to: 'statutory_account'
            },
            full_rules: {
                interest_belongs_to: {
                    value: 'statutory_account',
                    legislation: 'SSMA 2015 s.75',
                    notes: 'Must be held in statutory trust.'
                }
            },
        },
        VIC: {
            meta: {jurisdiction: 'VIC', primary_legislation: 'OCA 2006'},
            typed_rules: {
                max_interest_rate_pa: '10',
                can_transfer_between_funds: true,
                pooled_trust_permitted: false,
                interest_belongs_to: 'scheme'
            },
            full_rules: {},
        },
        QLD: {
            meta: {jurisdiction: 'QLD', primary_legislation: 'BCCM 1997'},
            typed_rules: {
                max_interest_rate_pa: '30',
                can_transfer_between_funds: false,
                pooled_trust_permitted: false,
                interest_belongs_to: 'body_corporate'
            },
            full_rules: {},
        },
    },
};

const mockApi = {
    get: jest.fn(),
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

const mockAuthState = {
    api: mockApi,
    user: {role: 'super_admin', building_id: '13195'},
    isAdmin: () => true,
    isManager: () => true,
    loading: false,
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthState,
    AuthProvider: ({children}: any) => <div>{children}</div>,
}));

beforeEach(() => {
    jest.clearAllMocks();
    mockAuthState.user = {role: 'super_admin', building_id: '13195'};
    mockAuthState.isAdmin = () => true;
    mockAuthState.isManager = () => true;
    mockAuthState.loading = false;
    mockApi.get.mockImplementation((url: string) => {
        if (url.includes('/all')) return Promise.resolve({data: mockAllJurisdictions});
        return Promise.resolve({data: mockActRules});
    });
});

describe('JurisdictionalRulesPage', () => {
    it('renders the page title', async () => {
        render(<JurisdictionalRulesPage/>);
        expect(screen.getByText(/Jurisdictional Rules/i)).toBeInTheDocument();
        await waitFor(() => {
            expect(screen.getByText(/Australian Capital Territory/i)).toBeInTheDocument();
        });
    });

    it('fetches and displays building rules on mount', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith(
                expect.stringContaining('/jurisdictional-rules/?building_id=13195')
            );
        });
    });

    it('displays jurisdiction name after load', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => {
            expect(screen.getByText(/Australian Capital Territory/i)).toBeInTheDocument();
        });
    });

    it('displays typed rules table', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => {
            expect(screen.getByTestId('typed-rules-table')).toBeInTheDocument();
        });
    });

    it('shows statutory citation from full_rules in typed-rules legislation column', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => {
            expect(screen.getByTestId('typed-rules-table')).toBeInTheDocument();
        });
        // Citation appears in typed-rules-table (sourced from full_rules.max_interest_rate_pa.legislation)
        const typedTable = screen.getByTestId('typed-rules-table');
        expect(within(typedTable).getByText('UTMA 2011 s.94')).toBeInTheDocument();
    });

    it('shows statutory citation in full-rules-table', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => {
            expect(screen.getByTestId('full-rules-table')).toBeInTheDocument();
        });
        const fullTable = screen.getByTestId('full-rules-table');
        expect(within(fullTable).getByText('UTMA 2011 s.94')).toBeInTheDocument();
    });

    it('refresh button triggers re-fetch', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => screen.getByTestId('refresh-btn'));
        fireEvent.click(screen.getByTestId('refresh-btn'));
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledTimes(2);
        });
    });

    it('shows error alert on API failure', async () => {
        mockApi.get.mockRejectedValueOnce({
            response: {data: {detail: 'Server error'}},
        });
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => {
            expect(screen.getByTestId('error-alert')).toBeInTheDocument();
        });
    });

    it('clicking All Jurisdictions tab fetches all jurisdictions and renders jurisdiction cards', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => screen.getByTestId('tab-all'));

        fireEvent.click(screen.getByTestId('tab-all'));

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/jurisdictional-rules/all');
        });
        await waitFor(() => {
            expect(screen.getByTestId('jurisdiction-card-ACT')).toBeInTheDocument();
            expect(screen.getByTestId('jurisdiction-card-NSW')).toBeInTheDocument();
        });
    });

    it('NSW all-jurisdictions card shows statutory_account interest_belongs_to', async () => {
        render(<JurisdictionalRulesPage/>);
        await waitFor(() => screen.getByTestId('tab-all'));

        fireEvent.click(screen.getByTestId('tab-all'));

        await waitFor(() => {
            expect(screen.getByTestId('jurisdiction-card-NSW')).toBeInTheDocument();
        });
        expect(screen.getByText('statutory_account')).toBeInTheDocument();
    });
});

describe('JurisdictionalRulesPage admin guard', () => {
    it('renders null and does not crash for non-admin (guard handles redirect)', () => {
        mockAuthState.user = {role: 'owner', building_id: '13195'};
        mockAuthState.isAdmin = () => false;
        mockAuthState.isManager = () => false;
        // Component renders null when guard fires — no crash expected
        expect(() => render(<JurisdictionalRulesPage/>)).not.toThrow();
        expect(mockApi.get).not.toHaveBeenCalled();
    });
});
