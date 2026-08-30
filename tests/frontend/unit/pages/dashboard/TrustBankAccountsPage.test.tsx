// @featuretrace:trust-accounting — Tests for TrustBankAccountsPage (per-building bank account
// settings, interest posting, advance-payment interest summary, interest history).
// Layer: test
// Data flow: Jest -> TrustBankAccountsPage -> mocked axios calls against /api/trust/v2/* (building-scoped).
// Related: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
//          backend/routers/trust_phase1.py
// Tests: tests/frontend/unit/pages/dashboard/TrustBankAccountsPage.test.tsx
import React from 'react';
import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import '@testing-library/jest-dom';
import {useSession} from 'next-auth/react';
import TrustBankAccountsPage from '@/pages/dashboard/TrustBankAccountsPage';

jest.mock('next-auth/react', () => ({
    useSession: jest.fn(),
}));

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();

jest.mock('axios', () => ({
    __esModule: true,
    default: {
        get: (...args: unknown[]) => mockGet(...args),
        post: (...args: unknown[]) => mockPost(...args),
        patch: (...args: unknown[]) => mockPatch(...args),
    },
}));

jest.mock('sonner', () => ({
    toast: {
        success: jest.fn(),
        error: jest.fn(),
    },
}));

const mockSession = (role: string) => {
    (useSession as jest.Mock).mockReturnValue({
        data: {
            accessToken: 'test-token',
            user: {data: {building_id: '13195', role}},
        },
        status: 'authenticated',
    });
};

const adminAccount = {
    id: 'acct-admin-1',
    building_id: '13195',
    account_type: 'admin_fund',
    bank_name: 'Macquarie Bank',
    account_name: 'The Proprietors Of Units Plan 13195',
    bsb: '182-266',
    account_number_masked: '****4567',
    account_category: 'transaction',
    interest_rate_pa: 0.045,
    interest_calc_method: 'daily_balance',
    computed_balance_cents: 5000000,
    computed_balance_display: '$50,000.00',
    current_balance_cents: 5000000,
    current_balance_display: '$50,000.00',
    opening_balance_cents: 4000000,
    last_reconciled_balance_cents: 5000000,
    last_reconciled_balance_display: '$50,000.00',
    reconciliation_gap_cents: 0,
    reconciliation_gap_display: '$0.00',
    interest_ytd_cents: 120000,
    interest_ytd_display: '$1,200.00',
    is_active: true,
};

const sinkingAccount = {
    ...adminAccount,
    id: 'acct-sinking-1',
    account_type: 'sinking_fund',
    account_name: 'The Proprietors Of Units Plan 13195 — Capital Works Fund',
    interest_ytd_cents: 80000,
    interest_ytd_display: '$800.00',
};

const advancePayment = {
    levy_schedule_id: 'levy-1',
    unit_number: '12',
    lot_number: 12,
    quarter: 'Q1',
    financial_year: '2026',
    due_date: '2026-03-31',
    paid_date: '2026-03-01',
    advance_days: 30,
    total_paid_cents: 100000,
    total_paid_display: '$1,000.00',
    interest_earned_cents: 500,
    interest_earned_display: '$5.00',
    interest_posted: false,
};

function mockAccountsAndAdvances({
    accounts = [adminAccount, sinkingAccount],
    advanceItems = [advancePayment],
    unpostedInterestCents = 500,
} = {}) {
    mockGet.mockImplementation((url: string) => {
        if (url.includes('/accounts/interest-history')) {
            return Promise.resolve({data: {data: []}});
        }
        if (url.includes('/levies/advance-payments')) {
            return Promise.resolve({
                data: {
                    data: {
                        items: advanceItems,
                        unposted_interest_cents: unpostedInterestCents,
                        unposted_interest_display: `$${(unpostedInterestCents / 100).toFixed(2)}`,
                    },
                },
            });
        }
        if (url.includes('/accounts')) {
            return Promise.resolve({data: {data: accounts}});
        }
        return Promise.resolve({data: {data: []}});
    });
}

describe('TrustBankAccountsPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockSession('strata_manager');
        mockAccountsAndAdvances();
    });

    it('shows a loading spinner before session/data resolve', () => {
        (useSession as jest.Mock).mockReturnValue({data: null, status: 'loading'});
        const {container} = render(<TrustBankAccountsPage/>);
        expect(container.querySelector('.animate-spin')).toBeInTheDocument();
    });

    it('renders trust accounts with computed totals after loading', async () => {
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });

        await waitFor(() => {
            expect(screen.getByText('The Proprietors Of Units Plan 13195')).toBeInTheDocument();
        });
        // Total balance = sum of both accounts' computed_balance_cents ($50,000 + $50,000).
        expect(screen.getByText('$100,000.00')).toBeInTheDocument();
        // YTD interest = sum of both accounts' interest_ytd_cents ($1,200 + $800).
        expect(screen.getByText('$2,000.00')).toBeInTheDocument();
    });

    it('displays the backend-computed unposted advance interest total, not a client recompute', async () => {
        // Regression test: TrustBankAccountsPage previously recomputed unposted advance
        // interest client-side via advancePayments.filter(...).reduce(...) over only the
        // current (paginated) page of items. That undercounts once results span more than
        // one page. The backend's own total (trust_phase1.py::list_advance_payments) must be
        // the source of truth — this test sets the backend total higher than what a
        // client-side reduce over the single returned item would produce, and asserts the
        // page displays the backend figure.
        mockAccountsAndAdvances({
            advanceItems: [advancePayment],
            unpostedInterestCents: 99999, // deliberately != advancePayment.interest_earned_cents (500)
        });

        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });

        await waitFor(() => {
            expect(screen.getByText('$999.99')).toBeInTheDocument();
        });
        // The naive client recompute of a single unposted $5.00 line must NOT be shown.
        expect(screen.queryByText('$5.00')).not.toBeInTheDocument();
    });

    it('counts pending advance payments client-side for the tab badge', async () => {
        mockAccountsAndAdvances({
            advanceItems: [
                {...advancePayment, levy_schedule_id: 'levy-1', interest_posted: false},
                {...advancePayment, levy_schedule_id: 'levy-2', interest_posted: true},
            ],
        });

        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });

        await waitFor(() => {
            // Only 1 of the 2 advance payments is unposted -> badge shows "1".
            expect(screen.getByText('1')).toBeInTheDocument();
        });
    });

    it('shows Edit and Post Interest actions for a manager role', async () => {
        mockSession('strata_manager');
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });

        await waitFor(() => {
            expect(screen.getAllByRole('button', {name: /edit/i}).length).toBeGreaterThan(0);
        });
        expect(screen.getAllByRole('button', {name: /post interest income/i}).length).toBeGreaterThan(0);
    });

    it('hides Edit and Post Interest actions for a non-manager role', async () => {
        mockSession('owner');
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });

        await waitFor(() => {
            expect(screen.getByText('The Proprietors Of Units Plan 13195')).toBeInTheDocument();
        });
        expect(screen.queryByRole('button', {name: /^edit$/i})).not.toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /post interest income/i})).not.toBeInTheDocument();
    });

    it('fetches interest history only when the History tab is opened', async () => {
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('The Proprietors Of Units Plan 13195')).toBeInTheDocument();
        });
        expect(mockGet).not.toHaveBeenCalledWith(
            expect.stringContaining('/accounts/interest-history'),
            expect.anything(),
        );

        const historyTab = screen.getByRole('tab', {name: /interest history/i});
        await act(async () => {
            fireEvent.mouseDown(historyTab, {button: 0, ctrlKey: false});
        });

        await waitFor(() => {
            expect(mockGet).toHaveBeenCalledWith(
                expect.stringContaining('/accounts/interest-history'),
                expect.anything(),
            );
        });
    });

    it('saves edited account settings via PATCH and reloads accounts', async () => {
        mockPatch.mockResolvedValue({data: {data: adminAccount}});

        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('The Proprietors Of Units Plan 13195')).toBeInTheDocument();
        });

        const editButtons = screen.getAllByRole('button', {name: /edit/i});
        await act(async () => {
            fireEvent.click(editButtons[0]);
        });

        const dialog = await screen.findByRole('dialog');
        // The Label component here isn't wired to the input via htmlFor/id, so query by
        // the input's placeholder rather than an associated label.
        const bankNameInput = within(dialog).getByPlaceholderText(/e\.g\. nab, macquarie bank/i);
        fireEvent.change(bankNameInput, {target: {value: 'Updated Bank'}});

        const saveButton = within(dialog).getByRole('button', {name: /save changes/i});
        await act(async () => {
            fireEvent.click(saveButton);
        });

        await waitFor(() => {
            expect(mockPatch).toHaveBeenCalledWith(
                expect.stringContaining(`/accounts/${adminAccount.id}`),
                expect.objectContaining({bank_name: 'Updated Bank'}),
                expect.anything(),
            );
        });
        // loadData() re-fires GET /accounts after a successful save.
        await waitFor(() => {
            expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('/accounts'), expect.anything());
        });
    });

    it('renders an empty state when no advance payments exist', async () => {
        mockAccountsAndAdvances({advanceItems: [], unpostedInterestCents: 0});

        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('The Proprietors Of Units Plan 13195')).toBeInTheDocument();
        });

        const advanceTab = screen.getByRole('tab', {name: /advance payments/i});
        await act(async () => {
            fireEvent.mouseDown(advanceTab, {button: 0, ctrlKey: false});
        });

        await waitFor(() => {
            expect(screen.getByText('No advance levy payments recorded.')).toBeInTheDocument();
        });
    });
});

// ---------------------------------------------------------------------------
// Add Trust Account dialog (tasks/GAP-ONBOARD-001, Fix 2/5/6)
// ---------------------------------------------------------------------------

describe('TrustBankAccountsPage — Add Account visibility (Fix 5)', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockAccountsAndAdvances();
    });

    it.each(['super_admin', 'strata_admin', 'strata_manager', 'treasurer'])(
        'shows the Add Account button for %s',
        async (role) => {
            mockSession(role);
            await act(async () => {
                render(<TrustBankAccountsPage/>);
            });
            await waitFor(() => {
                expect(screen.getByTestId('add-trust-account')).toBeInTheDocument();
            });
        },
    );

    it('hides the Add Account button for a non-manage role (owner)', async () => {
        mockSession('owner');
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('The Proprietors Of Units Plan 13195')).toBeInTheDocument();
        });
        expect(screen.queryByTestId('add-trust-account')).not.toBeInTheDocument();
    });
});

describe('TrustBankAccountsPage — Add Account dialog', () => {
    const {toast} = require('sonner');

    beforeEach(() => {
        jest.clearAllMocks();
        mockSession('super_admin');
        mockAccountsAndAdvances({accounts: [], advanceItems: [], unpostedInterestCents: 0});
    });

    async function openDialog() {
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });
        await waitFor(() => screen.getByTestId('add-trust-account'));
        await act(async () => {
            fireEvent.click(screen.getByTestId('add-trust-account'));
        });
        await screen.findByText('Add Trust Account');
    }

    it('rejects submission when required fields are missing', async () => {
        await openDialog();
        await act(async () => {
            fireEvent.click(screen.getByTestId('create-trust-account-submit'));
        });
        expect(toast.error).toHaveBeenCalledWith(
            expect.stringMatching(/Bank name, account name, BSB, and account number are all required/i),
        );
        expect(mockPost).not.toHaveBeenCalled();
    });

    it('rejects a BSB that is not 6 digits', async () => {
        await openDialog();
        fireEvent.change(screen.getByLabelText('Bank Name'), {target: {value: 'NAB'}});
        fireEvent.change(screen.getByLabelText('Account Name'), {target: {value: 'OC Trust'}});
        fireEvent.change(screen.getByLabelText('BSB'), {target: {value: '123'}});
        fireEvent.change(screen.getByLabelText('Account Number'), {target: {value: '12345678'}});
        await act(async () => {
            fireEvent.click(screen.getByTestId('create-trust-account-submit'));
        });
        expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/BSB must be 6 digits/i));
        expect(mockPost).not.toHaveBeenCalled();
    });

    it('rejects an account number outside the 4-10 digit range', async () => {
        await openDialog();
        fireEvent.change(screen.getByLabelText('Bank Name'), {target: {value: 'NAB'}});
        fireEvent.change(screen.getByLabelText('Account Name'), {target: {value: 'OC Trust'}});
        fireEvent.change(screen.getByLabelText('BSB'), {target: {value: '082001'}});
        fireEvent.change(screen.getByLabelText('Account Number'), {target: {value: '12'}});
        await act(async () => {
            fireEvent.click(screen.getByTestId('create-trust-account-submit'));
        });
        expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/Account number must be 4-10 digits/i));
        expect(mockPost).not.toHaveBeenCalled();
    });

    it('creates the account with a masked account number and reloads on success', async () => {
        mockPost.mockResolvedValueOnce({data: {success: true, id: 'new-account-id'}});
        await openDialog();

        fireEvent.change(screen.getByLabelText('Bank Name'), {target: {value: 'NAB'}});
        fireEvent.change(screen.getByLabelText('Account Name'), {target: {value: 'OC Trust Account'}});
        fireEvent.change(screen.getByLabelText('BSB'), {target: {value: '082-001'}});
        fireEvent.change(screen.getByLabelText('Account Number'), {target: {value: '123456789'}});
        fireEvent.change(screen.getByLabelText('Opening Balance (AUD)'), {target: {value: '500.00'}});
        await act(async () => {
            fireEvent.click(screen.getByTestId('create-trust-account-submit'));
        });

        await waitFor(() => {
            expect(mockPost).toHaveBeenCalledWith(
                expect.stringContaining('/trust/v2/accounts'),
                expect.objectContaining({
                    account_type: 'admin_fund',
                    bank_name: 'NAB',
                    account_name: 'OC Trust Account',
                    bsb: '082-001',
                    account_number_masked: '****6789',
                    account_category: 'transaction',
                    current_balance_cents: 50000,
                }),
                expect.anything(),
            );
        });
        await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Trust account created.'));
    });

    it('shows a readable message (not [object Object]) when require_feature blocks creation', async () => {
        // require_feature("trust_accounting")'s 403 body is a typed
        // {code, message, feature_key, retryable} object, not a plain string.
        mockPost.mockRejectedValueOnce({
            response: {
                data: {
                    detail: {
                        code: 'FEATURE_DISABLED',
                        message: 'This feature is not available for your building yet.',
                        feature_key: 'trust_accounting',
                        retryable: false,
                    },
                },
            },
        });
        await openDialog();

        fireEvent.change(screen.getByLabelText('Bank Name'), {target: {value: 'NAB'}});
        fireEvent.change(screen.getByLabelText('Account Name'), {target: {value: 'OC Trust'}});
        fireEvent.change(screen.getByLabelText('BSB'), {target: {value: '082001'}});
        fireEvent.change(screen.getByLabelText('Account Number'), {target: {value: '12345678'}});
        await act(async () => {
            fireEvent.click(screen.getByTestId('create-trust-account-submit'));
        });

        await waitFor(() => {
            expect(toast.error).toHaveBeenCalledWith('This feature is not available for your building yet.');
        });
        expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining('[object Object]'));
    });

    it('empty state includes an "Add one now" link that opens the dialog', async () => {
        await act(async () => {
            render(<TrustBankAccountsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText(/No trust accounts configured yet/i)).toBeInTheDocument();
        });
        await act(async () => {
            fireEvent.click(screen.getByText('Add one now.'));
        });
        expect(await screen.findByText('Add Trust Account')).toBeInTheDocument();
    });
});
