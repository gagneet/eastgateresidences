/**
 * Tests for multi-unit ownership UI additions to RegisterPage.
 *
 * Covers:
 *   - "I own additional units" checkbox shows/hides based on role and primary unit selection
 *   - Additional unit selectors render, can be added and removed
 *   - additional_unit_numbers included in submit payload when checkbox checked
 *   - owner_exists_add_unit 409 shows the correct redirect toast
 *   - Checkbox hidden for tenant/guest roles
 */
import React from 'react';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import RegisterPage from '@/pages/auth/RegisterPage';

// ── Global mocks ────────────────────────────────────────────────────────────

const mockPush = jest.fn();
const mockRegister = jest.fn();
const mockGet = jest.fn();

// RegisterPage reads ?invite=<token> to prefill a building-scoped resident
// invite; these tests exercise the ordinary (no-invite) sign-up path, so the
// search params are empty.
jest.mock('next/navigation', () => ({
    useRouter: () => ({push: mockPush}),
    useSearchParams: () => new URLSearchParams(''),
}));
jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn()}}));
jest.mock('axios', () => ({__esModule: true, default: {get: (...a: any[]) => mockGet(...a)}}));
jest.mock('@/contexts/AuthContext', () => ({useAuth: () => ({register: mockRegister})}));

// Radix Select → native <select> so userEvent.selectOptions works
jest.mock('@/components/ui/select', () => ({
    Select: ({children, onValueChange, value}: any) => (
        <select value={value ?? ''} onChange={e => onValueChange?.(e.target.value)}>
            {children}
        </select>
    ),
    SelectContent: ({children}: any) => <>{children}</>,
    SelectItem: ({children, value, disabled}: any) =>
        disabled ? null : <option value={value}>{children}</option>,
    SelectTrigger: ({children, 'aria-label': al, 'data-testid': dtid, id}: any) => (
        <label aria-label={al} data-testid={dtid} id={id}>{children}</label>
    ),
    SelectValue: ({placeholder}: any) => <option value="">{placeholder}</option>,
}));

// Radix Checkbox → native checkbox
jest.mock('@/components/ui/checkbox', () => ({
    Checkbox: ({checked, onCheckedChange, id, className}: any) => (
        <input
            type="checkbox"
            id={id}
            checked={!!checked}
            onChange={e => onCheckedChange?.(e.target.checked)}
        />
    ),
}));

jest.mock('@/components/ui/card', () => ({
    Card: ({children}: any) => <div>{children}</div>,
    CardContent: ({children}: any) => <div>{children}</div>,
}));

jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children}: any) => <>{children}</>,
    DialogContent: ({children}: any) => <div>{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <h2>{children}</h2>,
    DialogDescription: ({children}: any) => <p>{children}</p>,
    DialogFooter: ({children}: any) => <div>{children}</div>,
}));

// ── Test data ────────────────────────────────────────────────────────────────

const UNITS = [
    {unit_number: 'UA001', unit_type: 'apartment'},
    {unit_number: 'UA010', unit_type: 'apartment'},
    {unit_number: 'TH001', unit_type: 'townhouse'},
];

function setupMocks() {
    mockGet.mockImplementation((url: string) => {
        if (url.includes('/api/auth/buildings'))
            return Promise.resolve({data: [{id: '13195', name: 'East Gate'}]});
        if (url.includes('/api/units/all'))
            return Promise.resolve({data: UNITS});
        if (url.includes('/api/by-laws/current'))
            return Promise.resolve({data: null});
        return Promise.resolve({data: {}});
    });
}

async function renderAndWaitForLoad() {
    render(<RegisterPage/>);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    // Wait for units to load (loading state cleared)
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('/api/units/all')));
}

// Increase Jest timeout for all tests in this file — the registration form
// involves multiple async interactions that routinely exceed the default 5 000 ms in CI.
jest.setTimeout(20000);

// ── Tests ────────────────────────────────────────────────────────────────────

describe('RegisterPage — multi-unit ownership', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        setupMocks();
    });

    describe('checkbox visibility', () => {
        it('does not show multi-unit section before role is selected', async () => {
            await renderAndWaitForLoad();
            expect(screen.queryByTestId('multi-unit-section')).not.toBeInTheDocument();
        });

        it('does not show multi-unit section for tenant role', async () => {
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();
            // First select is the role selector
            const roleSelect = screen.getAllByRole('combobox')[0];
            await user.selectOptions(roleSelect, 'tenant');
            expect(screen.queryByTestId('multi-unit-section')).not.toBeInTheDocument();
        });

        it('does not show multi-unit section for guest role', async () => {
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();
            const roleSelect = screen.getAllByRole('combobox')[0];
            await user.selectOptions(roleSelect, 'guest');
            expect(screen.queryByTestId('multi-unit-section')).not.toBeInTheDocument();
        });

        it('shows multi-unit section after owner selects a primary unit', async () => {
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();

            const selects = screen.getAllByRole('combobox');
            await user.selectOptions(selects[0], 'owner');

            // Unit selector appears after role selection
            await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2));
            const selects2 = screen.getAllByRole('combobox');
            await user.selectOptions(selects2[1], 'UA001');

            await waitFor(() =>
                expect(screen.getByTestId('multi-unit-section')).toBeInTheDocument()
            );
        });
    });

    describe('additional unit selectors', () => {
        async function renderWithOwnerAndUnit() {
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();
            const selects = screen.getAllByRole('combobox');
            await user.selectOptions(selects[0], 'owner');
            await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2));
            await user.selectOptions(screen.getAllByRole('combobox')[1], 'UA001');
            await waitFor(() => expect(screen.getByTestId('multi-unit-section')).toBeInTheDocument());
            return user;
        }

        it('reveals additional unit slots when checkbox is checked', async () => {
            const user = await renderWithOwnerAndUnit();
            const checkbox = within(screen.getByTestId('multi-unit-section')).getByRole('checkbox');
            await user.click(checkbox);
            await waitFor(() =>
                expect(screen.getByText(/Additional unit\(s\) you own/i)).toBeInTheDocument()
            );
        });

        it('adds another slot when "Add another unit" button is clicked', async () => {
            const user = await renderWithOwnerAndUnit();
            const checkbox = within(screen.getByTestId('multi-unit-section')).getByRole('checkbox');
            await user.click(checkbox);

            const addBtn = await screen.findByTestId('add-another-unit-btn');
            await user.click(addBtn);

            // Two additional-unit select elements now (data-testid attribute used)
            await waitFor(() => {
                expect(screen.getByTestId('additional-unit-select-0')).toBeInTheDocument();
                expect(screen.getByTestId('additional-unit-select-1')).toBeInTheDocument();
            });
        });

        it('removes a slot when the remove button is clicked', async () => {
            const user = await renderWithOwnerAndUnit();
            const checkbox = within(screen.getByTestId('multi-unit-section')).getByRole('checkbox');
            await user.click(checkbox);

            await screen.findByTestId('add-another-unit-btn');
            await user.click(screen.getByTestId('add-another-unit-btn'));

            await waitFor(() =>
                expect(screen.getByTestId('additional-unit-select-1')).toBeInTheDocument()
            );

            // Remove the first additional slot
            const removeBtns = screen.getAllByRole('button', {name: /Remove additional unit/i});
            await user.click(removeBtns[0]);

            await waitFor(() =>
                expect(screen.queryByTestId('additional-unit-select-1')).not.toBeInTheDocument()
            );
        });

        it('hides additional unit section when checkbox is unchecked', async () => {
            const user = await renderWithOwnerAndUnit();
            const checkbox = within(screen.getByTestId('multi-unit-section')).getByRole('checkbox');
            await user.click(checkbox);
            await screen.findByText(/Additional unit\(s\) you own/i);

            await user.click(checkbox); // uncheck
            await waitFor(() =>
                expect(screen.queryByText(/Additional unit\(s\) you own/i)).not.toBeInTheDocument()
            );
        });
    });

    describe('submit payload', () => {
        async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
            await user.type(screen.getByLabelText(/full name/i), 'Test Owner');
            await user.type(screen.getByLabelText(/^email/i), 'owner@test.com');
            await user.type(screen.getByLabelText(/^phone/i), '0400000000');

            const passwordInputs = screen.getAllByLabelText(/password/i);
            await user.type(passwordInputs[0], 'jest-fixture-password-not-a-credential');
            await user.type(passwordInputs[1], 'jest-fixture-password-not-a-credential');

            // By-laws checkbox
            const byLawsCheckbox = screen.getByLabelText(/I have read/i);
            await user.click(byLawsCheckbox);
        }

        it('includes additional_unit_numbers in payload when multi-unit checked', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockResolvedValueOnce({});
            await renderAndWaitForLoad();

            // Select owner role + primary unit
            const selects = screen.getAllByRole('combobox');
            await user.selectOptions(selects[0], 'owner');
            await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2));
            await user.selectOptions(screen.getAllByRole('combobox')[1], 'UA001');
            await waitFor(() => expect(screen.getByTestId('multi-unit-section')).toBeInTheDocument());

            // Enable multi-unit
            const checkbox = within(screen.getByTestId('multi-unit-section')).getByRole('checkbox');
            await user.click(checkbox);

            // Select an additional unit
            await waitFor(() => expect(screen.getByTestId('additional-unit-select-0')).toBeInTheDocument());
            const addSelect = screen.getByTestId('additional-unit-select-0').closest('select') ||
                screen.getAllByRole('combobox').find((el: any) =>
                    el.closest('[data-testid="additional-unit-select-0"]') || el.dataset?.testid === 'additional-unit-select-0'
                );
            // The additional select rendered as a native select via our mock — get it by finding the select next to the testid label
            const allSelects = screen.getAllByRole('combobox');
            // Primary unit is index 1; additional units start at index 2
            await user.selectOptions(allSelects[2], 'TH001');

            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(mockRegister).toHaveBeenCalledTimes(1));
            const payload = mockRegister.mock.calls[0][0];
            expect(payload.additional_unit_numbers).toEqual(expect.arrayContaining(['TH001']));
            expect(payload.unit_number).toBe('UA001');
            expect(payload.role).toBe('owner');
        });

        it('omits additional_unit_numbers when multi-unit checkbox is unchecked', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockResolvedValueOnce({});
            await renderAndWaitForLoad();

            const selects = screen.getAllByRole('combobox');
            await user.selectOptions(selects[0], 'owner');
            await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2));
            await user.selectOptions(screen.getAllByRole('combobox')[1], 'UA001');

            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(mockRegister).toHaveBeenCalledTimes(1));
            const payload = mockRegister.mock.calls[0][0];
            expect(payload).not.toHaveProperty('additional_unit_numbers');
        });
    });

    describe('owner_exists_add_unit error handling', () => {
        it('shows redirect toast when backend returns owner_exists_add_unit 409', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {data: {detail: {code: 'owner_exists_add_unit'}}},
            });
            await renderAndWaitForLoad();

            const selects = screen.getAllByRole('combobox');
            await user.selectOptions(selects[0], 'owner');
            await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2));
            await user.selectOptions(screen.getAllByRole('combobox')[1], 'UA001');

            await user.type(screen.getByLabelText(/full name/i), 'Existing Owner');
            await user.type(screen.getByLabelText(/^email/i), 'existing@test.com');
            await user.type(screen.getByLabelText(/^phone/i), '0400000000');
            const passwordInputs = screen.getAllByLabelText(/password/i);
            await user.type(passwordInputs[0], 'jest-fixture-password-not-a-credential');
            await user.type(passwordInputs[1], 'jest-fixture-password-not-a-credential');
            await user.click(screen.getByLabelText(/I have read/i));

            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() =>
                expect(toast.error).toHaveBeenCalledWith(
                    expect.stringMatching(/log in.*add additional units/i),
                    expect.objectContaining({duration: 8000})
                )
            );
        });
    });
});
