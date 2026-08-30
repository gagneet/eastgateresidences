/**
 * Tests for RegisterPage — focusing on:
 *   - Basic form rendering
 *   - archived_user_return_request 409 → blue informational banner (not a red error)
 *   - Banner text and no login/reset-password links for the blue variant
 *   - Generic 409 (different code) → red toast error
 *   - 400 error → red toast error
 *   - already_registered 409 → amber banner with login link
 *   - pending_approval 409 → blue banner
 *   - pending_now_approved 409 → green banner
 *   - Successful registration → redirect to /register/success
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import RegisterPage from '@/pages/auth/RegisterPage';

// ── Global mocks ─────────────────────────────────────────────────────────────

const mockPush = jest.fn();
const mockRegister = jest.fn();
const mockGet = jest.fn();

jest.mock('next/navigation', () => ( {
    useRouter: () => ( {push: mockPush} ),
    usePathname: () => '/',
    useSearchParams: () => new URLSearchParams(),
} ));

jest.mock('sonner', () => ( {
    toast: {success: jest.fn(), error: jest.fn()},
} ));

jest.mock('axios', () => ( {
    __esModule: true,
    default: {get: (...a) => mockGet(...a)},
} ));

jest.mock('@/contexts/AuthContext', () => ( {
    useAuth: () => ( {register: mockRegister} ),
} ));

// Radix Select → native <select> so userEvent.selectOptions works
jest.mock('@/components/ui/select', () => ( {
    Select: ({children, onValueChange, value}) => (
        <select value={value ?? ''} onChange={e => onValueChange?.(e.target.value)}>
            {children}
        </select>
    ),
    SelectContent: ({children}) => <>{children}</>,
    SelectItem: ({children, value, disabled}) =>
        disabled ? null : <option value={value}>{children}</option>,
    SelectTrigger: ({children, 'aria-label': al, 'data-testid': dtid, id}) => (
        <label aria-label={al} data-testid={dtid} id={id}>{children}</label>
    ),
    SelectValue: ({placeholder}) => <option value="">{placeholder}</option>,
} ));

// Radix Checkbox → native checkbox
jest.mock('@/components/ui/checkbox', () => ( {
    Checkbox: ({checked, onCheckedChange, id}) => (
        <input
            type="checkbox"
            id={id}
            checked={!!checked}
            onChange={e => onCheckedChange?.(e.target.checked)}
        />
    ),
} ));

jest.mock('@/components/ui/card', () => ( {
    Card: ({children}) => <div>{children}</div>,
    CardContent: ({children}) => <div>{children}</div>,
} ));

jest.mock('@/components/ui/dialog', () => ( {
    Dialog: ({children}) => <>{children}</>,
    DialogContent: ({children}) => <div>{children}</div>,
    DialogHeader: ({children}) => <div>{children}</div>,
    DialogTitle: ({children}) => <h2>{children}</h2>,
    DialogDescription: ({children}) => <p>{children}</p>,
    DialogFooter: ({children}) => <div>{children}</div>,
} ));

// ── Test data ─────────────────────────────────────────────────────────────────

const UNITS = [
    {unit_number: 'UA001', unit_type: 'apartment'},
    {unit_number: 'UA010', unit_type: 'apartment'},
    {unit_number: 'TH001', unit_type: 'townhouse'},
];

function setupMocks() {
    mockGet.mockImplementation((url) => {
        if (url.includes('/api/auth/buildings'))
            return Promise.resolve({data: [{id: '13195', name: 'East Gate'}]});
        if (url.includes('/api/units/all'))
            return Promise.resolve({data: UNITS});
        if (url.includes('/api/by-laws/current'))
            return Promise.resolve({data: null});
        return Promise.resolve({data: {}});
    });
}

/** Render and wait for the initial API fetches (buildings + units) to settle. */
async function renderAndWaitForLoad() {
    render(<RegisterPage/>);
    await waitFor(() =>
        expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('/api/units/all'))
    );
}

/**
 * Select a role, then a unit — this makes the personal-details fields and the
 * submit button appear.
 */
async function selectRoleAndUnit(user, role = 'owner', unit = 'UA001') {
    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[ 0 ], role);
    // Unit selector is revealed after role selection
    await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2));
    await user.selectOptions(screen.getAllByRole('combobox')[ 1 ], unit);
    // Personal fields should now be visible
    await waitFor(() => expect(screen.getByLabelText(/full name/i)).toBeInTheDocument());
}

/** Fill the minimum required form fields to pass client-side validation. */
async function fillRequiredFields(user) {
    await user.type(screen.getByLabelText(/full name/i), 'Jane Owner');
    await user.type(screen.getByLabelText(/^email/i), 'jane@example.com');
    await user.type(screen.getByLabelText(/^phone/i), '0400000000');

    const passwordInputs = screen.getAllByLabelText(/password/i);
    await user.type(passwordInputs[ 0 ], 'jest-fixture-password-not-a-credential');
    await user.type(passwordInputs[ 1 ], 'jest-fixture-password-not-a-credential');

    // By-laws checkbox
    await user.click(screen.getByLabelText(/I have read/i));
}

// Increase Jest timeout for all tests in this file — the registration form
// involves multiple async interactions (API fetch, role select, unit select,
// form fill, submit) that routinely exceed the default 5 000 ms in CI.
jest.setTimeout(20000);

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('RegisterPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        setupMocks();
    });

    // ── Rendering ────────────────────────────────────────────────────────────

    describe('initial render', () => {
        it('renders the register page wrapper', async () => {
            await renderAndWaitForLoad();
            expect(screen.getByTestId('register-page')).toBeInTheDocument();
        });

        it('renders the registration form', async () => {
            await renderAndWaitForLoad();
            expect(screen.getByTestId('register-form')).toBeInTheDocument();
        });

        it('renders a role selector', async () => {
            await renderAndWaitForLoad();
            const selects = screen.getAllByRole('combobox');
            expect(selects.length).toBeGreaterThanOrEqual(1);
        });

        it('shows personal-detail fields once a role and unit are selected', async () => {
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);

            expect(screen.getByLabelText(/full name/i)).toBeInTheDocument();
            expect(screen.getByLabelText(/^email/i)).toBeInTheDocument();
            expect(screen.getByLabelText(/^phone/i)).toBeInTheDocument();
        });

        it('shows the submit button once a role and unit are selected', async () => {
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);

            expect(screen.getByTestId('register-submit')).toBeInTheDocument();
        });
    });

    // ── archived_user_return_request — the feature under test ────────────────

    describe('archived_user_return_request 409 handling', () => {
        it('shows a blue informational banner instead of a red error', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'archived_user_return_request',
                            message: 'Your return request has been submitted. An administrator will review it shortly.',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            const banner = await screen.findByTestId('already-registered-banner');
            expect(banner).toBeInTheDocument();
            // Must be blue variant — not amber/green
            expect(banner).toHaveAttribute('data-variant', 'blue');
        });

        it('shows the backend message text inside the banner', async () => {
            const user = userEvent.setup({ delay: null });
            const returnMessage =
                'Your return request has been submitted. An administrator will review it shortly.';
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'archived_user_return_request',
                            message: returnMessage,
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await screen.findByTestId('already-registered-banner');
            expect(screen.getByText(returnMessage)).toBeInTheDocument();
        });

        it('does NOT show login or reset-password links in the blue banner', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'archived_user_return_request',
                            message: 'Your return request has been submitted.',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            const banner = await screen.findByTestId('already-registered-banner');
            // Blue variant: no "Sign in" or "Reset password" action buttons inside the banner
            expect(banner.querySelector('a[href="/login"]')).toBeNull();
            expect(banner.querySelector('a[href*="forgot-password"]')).toBeNull();
        });

        it('does NOT call toast.error for the archived_user_return_request code', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'archived_user_return_request',
                            message: 'Your return request has been submitted.',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await screen.findByTestId('already-registered-banner');
            expect(toast.error).not.toHaveBeenCalled();
        });

        it('hides the submit button once the archived_user_return_request banner is shown', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'archived_user_return_request',
                            message: 'Your return request has been submitted.',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await screen.findByTestId('already-registered-banner');
            // Submit button hidden when alreadyRegistered banner is shown
            expect(screen.queryByTestId('register-submit')).not.toBeInTheDocument();
        });
    });

    // ── Other 409 error codes ─────────────────────────────────────────────────

    describe('already_registered 409 handling', () => {
        it('shows an amber banner with a sign-in link', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'already_registered',
                            message: 'An account with this email already exists.',
                            unit_number: 'UA001',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            const banner = await screen.findByTestId('already-registered-banner');
            expect(banner).toHaveAttribute('data-variant', 'amber');
            expect(banner.querySelector('a[href="/login"]')).not.toBeNull();
        });
    });

    describe('pending_approval 409 handling', () => {
        it('shows a blue banner for pending_approval code', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'pending_approval',
                            message: 'Your application is already pending approval.',
                            unit_number: 'UA001',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            const banner = await screen.findByTestId('already-registered-banner');
            expect(banner).toHaveAttribute('data-variant', 'blue');
        });
    });

    describe('pending_now_approved 409 handling', () => {
        it('shows a green banner for pending_now_approved code', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'pending_now_approved',
                            message: 'Your account has been approved. Please sign in.',
                            unit_number: 'UA001',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            // Wait for the banner to appear AND confirm it has the green variant.
            // waitFor is more reliable than findByTestId here because it retries
            // the assertion until the React state update settles.
            await waitFor(() => {
                const banner = screen.getByTestId('already-registered-banner');
                expect(banner).toHaveAttribute('data-variant', 'green');
            });
        });
    });

    // ── Generic / fallback error handling ────────────────────────────────────

    describe('generic error handling', () => {
        it('calls toast.error when 409 carries an unknown code', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 409,
                    data: {
                        detail: {
                            code: 'some_unknown_code',
                            message: 'Something went wrong.',
                        },
                    },
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(toast.error).toHaveBeenCalled());
            expect(screen.queryByTestId('already-registered-banner')).not.toBeInTheDocument();
        });

        it('calls toast.error with the detail string when API returns a 400', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 400,
                    data: {detail: 'Email address is already in use.'},
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() =>
                expect(toast.error).toHaveBeenCalledWith('Email address is already in use.')
            );
        });

        it('calls toast.error with a fallback message when error has no detail', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {status: 500, data: {}},
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(toast.error).toHaveBeenCalled());
        });

        it('does not show the banner for generic errors', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockRejectedValueOnce({
                response: {
                    status: 400,
                    data: {detail: 'Registration failed.'},
                },
            });

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(require('sonner').toast.error).toHaveBeenCalled());
            expect(screen.queryByTestId('already-registered-banner')).not.toBeInTheDocument();
        });
    });

    // ── Successful registration ───────────────────────────────────────────────

    describe('successful registration', () => {
        it('redirects to /register/success on successful owner registration', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            mockRegister.mockResolvedValueOnce({data: {status: 'pending'}});

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user, 'owner', 'UA001');
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/register/success'));
            expect(toast.success).toHaveBeenCalledWith(
                expect.stringContaining('Registration successful')
            );
        });

        it('calls register() with the correct building id', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockResolvedValueOnce({});

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user, 'owner', 'UA001');
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(mockRegister).toHaveBeenCalledTimes(1));
            // Second argument to register() is the building id
            expect(mockRegister.mock.calls[ 0 ][ 1 ]).toBe('13195');
        });

        it('does not include confirmPassword in the submitted payload', async () => {
            const user = userEvent.setup({ delay: null });
            mockRegister.mockResolvedValueOnce({});

            await renderAndWaitForLoad();
            await selectRoleAndUnit(user, 'owner', 'UA001');
            await fillRequiredFields(user);
            await user.click(screen.getByTestId('register-submit'));

            await waitFor(() => expect(mockRegister).toHaveBeenCalledTimes(1));
            const payload = mockRegister.mock.calls[ 0 ][ 0 ];
            expect(payload).not.toHaveProperty('confirmPassword');
        });
    });

    // ── Client-side validation ────────────────────────────────────────────────

    describe('client-side validation', () => {
        it('does not submit when required fields are empty', async () => {
            const {toast} = require('sonner');
            const user = userEvent.setup({ delay: null });
            await renderAndWaitForLoad();
            await selectRoleAndUnit(user);
            // Do NOT fill required fields
            await user.click(screen.getByTestId('register-submit'));

            expect(mockRegister).not.toHaveBeenCalled();
            expect(toast.error).toHaveBeenCalledWith(
                expect.stringMatching(/fix the errors/i)
            );
        });
    });
});
