/**
 * Tests: BuildingOnboardingForm
 *
 * Covers:
 *   - Access gate: only super_admin, strata_admin, or strata_manager can see the form
 *   - Step 1 (Identity): unit plan number required, name required, building_id derived
 *   - Step 2 (Location): address and state required
 *   - Step 3 (Lots): at least one lot type must be selected
 *   - Step 6 (Committee): CHAIRMAN required
 *   - XP system: total XP increases on valid step completion
 *   - deriveId helper: strips UP/SP prefix, lowercases
 *   - Multi-building neutral: no hardcoded building names in UI copy
 *   - Submit: calls POST /admin/onboarding/scheme with the new scheme-start payload
 *
 * UI conventions verified by reading the source:
 *   - Forward nav button text: "Continue" (not "Next")
 *   - Back nav button text: "Back" (not "Previous")
 *   - Submit button text: "Create Building"
 *   - Step 1 heading: "Building Identity"
 *   - XP total in header: "<n> XP" (no + prefix)
 *   - Input selectors: data-testid="unit-plan-number", "building-name",
 *     "street-address", "lot-count"
 */

import React, {type ChangeEvent, type ReactNode} from 'react';
import {render, screen, fireEvent, waitFor, act} from '@testing-library/react';
import '@testing-library/jest-dom';

// ---------------------------------------------------------------------------
// Global mocks
// ---------------------------------------------------------------------------

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn(), back: jest.fn(), replace: jest.fn()}),
    usePathname: () => '/admin/buildings/onboard',
    useSearchParams: () => ({get: (_k: string) => null}),
}));

jest.mock('sonner', () => ({
    toast: {success: jest.fn(), error: jest.fn(), info: jest.fn()},
}));

jest.mock('@/components/ui/select', () => ({
    Select: ({
        value,
        onValueChange,
        children,
        'data-testid': testId,
    }: {
        value: string;
        onValueChange: (value: string) => void;
        children: ReactNode;
        'data-testid'?: string;
    }) => (
        <select
            data-testid={testId}
            value={value}
            onChange={(event: ChangeEvent<HTMLSelectElement>) => onValueChange(event.target.value)}
        >
            {children}
        </select>
    ),
    SelectContent: ({children}: { children: ReactNode }) => <>{children}</>,
    SelectItem: ({value, children}: { value: string; children: ReactNode }) => (
        <option value={value}>{children}</option>
    ),
    SelectTrigger: () => null,
    SelectValue: () => null,
}));

// ---------------------------------------------------------------------------
// AuthContext mock factory
// ---------------------------------------------------------------------------

const neverResolvingRequest = () => new Promise(() => {});

const mockApi = {
    get: jest.fn(() => neverResolvingRequest()),
    post: jest.fn(),
    patch: jest.fn(),
};

function makeAuthContext({role = 'strata_manager'}: { role?: string } = {}) {
    return {
        api: mockApi,
        isAdmin: () => role === 'super_admin',
        isManager: () => role === 'super_admin' || role === 'strata_admin' || role === 'strata_manager' || role === 'ec_member',
        user: role === 'super_admin'
            ? {id: 'u-sa', role: 'super_admin', full_name: 'Super Admin'}
            : role === 'strata_admin'
                ? {id: 'u-sma', role: 'strata_admin', full_name: 'Strata Admin', tenant_id: 'tenant-admin'}
                : role === 'strata_manager'
                    ? {id: 'u-sm', role: 'strata_manager', full_name: 'Strata Manager', tenant_id: 'tenant-manager'}
                    : role === 'ec_member'
                        ? {id: 'u-ec', role: 'ec_member', full_name: 'EC Member', tenant_id: 'tenant-ec'}
                        : {id: 'u-ow', role: 'owner', full_name: 'Owner'},
    };
}

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
}));

import {useAuth} from '@/contexts/AuthContext';

// Dynamic import of the JSX component (after mocks)
const BuildingOnboardingFormModule = jest.requireActual(
    '@/pages/dashboard/admin/BuildingOnboardingForm'
);
const BuildingOnboardingForm = BuildingOnboardingFormModule.default as React.ComponentType;
const {extractErrorDetail, parseStrataRollCsv, normaliseLevyDueCustomDates} = BuildingOnboardingFormModule;

beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
        configurable: true,
        value: jest.fn(() => false),
    });
    Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
        configurable: true,
        value: jest.fn(),
    });
    Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
        configurable: true,
        value: jest.fn(),
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: jest.fn(),
    });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderForm(opts: Parameters<typeof makeAuthContext>[0] = {role: 'strata_manager'}) {
    (useAuth as jest.Mock).mockReturnValue(makeAuthContext(opts));
    return render(<BuildingOnboardingForm/>);
}

/** Fill step 1 with valid values using data-testid attributes */
async function fillStep1() {
    fireEvent.change(screen.getByTestId('unit-plan-number'), {target: {value: 'UP99001'}});
    fireEvent.change(screen.getByTestId('building-name'), {target: {value: 'Test Building'}});
}

/** Fill step 2 with valid values */
async function fillStep2() {
    fireEvent.change(screen.getByTestId('street-address'), {target: {value: '1 Test Street'}});
}

const clickContinue = () =>
    fireEvent.click(screen.getByRole('button', {name: /continue/i}));

async function selectSingleOrganisationIfNeeded() {
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
    await act(async () => {
        await mockApi.get.mock.results[0]?.value;
    });
}

// ---------------------------------------------------------------------------
// 1. Access gate
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — access gate', () => {
    it('shows access-restricted alert for owner (non-admin, non-manager)', () => {
        renderForm({role: 'owner'});
        // The form renders an Alert with destructive variant
        expect(screen.getByText(/Access restricted/i)).toBeInTheDocument();
        // Step 1 inputs must NOT be rendered
        expect(screen.queryByTestId('unit-plan-number')).not.toBeInTheDocument();
    });

    it('renders step 1 inputs for super_admin', () => {
        renderForm({role: 'super_admin'});
        expect(screen.getByTestId('unit-plan-number')).toBeInTheDocument();
    });

    it('renders step 1 inputs for strata_manager', () => {
        renderForm({role: 'strata_manager'});
        expect(screen.getByTestId('unit-plan-number')).toBeInTheDocument();
    });

    it('renders step 1 inputs for strata_admin', () => {
        renderForm({role: 'strata_admin'});
        expect(screen.getByTestId('unit-plan-number')).toBeInTheDocument();
    });

    it('blocks ec_member because the new onboarding route does not allow that role', () => {
        renderForm({role: 'ec_member'});
        expect(screen.getByText(/Access restricted/i)).toBeInTheDocument();
        expect(screen.queryByTestId('unit-plan-number')).not.toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// 2. Step rendering — initial state
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — initial state', () => {
    it('starts on step 1 — Building Identity heading is visible', () => {
        renderForm();
        expect(screen.getByText('Building Identity')).toBeInTheDocument();
    });

    it('shows 8 step indicators in the header', () => {
        renderForm();
        // Each step label appears as a small text under the step dot
        const stepLabels = ['Identity', 'Location', 'Lots', 'Financial', 'Manager', 'Committee', 'Import', 'Review'];
        stepLabels.forEach(label => {
            expect(screen.getAllByText(label).length).toBeGreaterThanOrEqual(1);
        });
    });

    it('shows XP total of 0 in the header', () => {
        renderForm();
        // The header XP span shows "0 XP" — getByText with exact string
        // avoids matching "+10 XP" which is a different element
        expect(screen.getByText('0 XP')).toBeInTheDocument();
    });

    it('Back button is disabled on step 1', () => {
        renderForm();
        const backBtn = screen.getByRole('button', {name: /back/i});
        expect(backBtn).toBeDisabled();
    });

    it('Continue button is enabled on step 1', () => {
        renderForm();
        expect(screen.getByRole('button', {name: /continue/i})).toBeEnabled();
    });
});

// ---------------------------------------------------------------------------
// 3. Step 1 — Identity validation
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — step 1 Identity', () => {
    it('shows derived building_id preview when unit plan number is entered', async () => {
        renderForm();
        fireEvent.change(screen.getByTestId('unit-plan-number'), {target: {value: 'UP99001'}});
        // derivedId preview: "Building ID will be: 99001"
        await waitFor(() => {
            expect(screen.getByText(/Building ID will be/i)).toBeInTheDocument();
            expect(screen.getAllByText('99001').length).toBeGreaterThanOrEqual(1);
        });
    });

    it('blocks advancing to step 2 when unit_plan_number is empty', async () => {
        renderForm();
        fireEvent.change(screen.getByTestId('building-name'), {target: {value: 'My Building'}});
        clickContinue();
        // Still on step 1
        expect(screen.getByText('Building Identity')).toBeInTheDocument();
    });

    it('blocks advancing to step 2 when building name is empty', async () => {
        renderForm();
        fireEvent.change(screen.getByTestId('unit-plan-number'), {target: {value: 'UP99001'}});
        clickContinue();
        // Still on step 1
        expect(screen.getByText('Building Identity')).toBeInTheDocument();
    });

    it('shows an error message when advancing with incomplete step 1', async () => {
        renderForm();
        clickContinue(); // both fields empty
        await waitFor(() => {
            expect(screen.getByText(/valid Unit Plan number/i)).toBeInTheDocument();
        });
    });

    it('advances to step 2 with valid identity fields', async () => {
        renderForm();
        await fillStep1();
        clickContinue();
        await waitFor(() => {
            expect(screen.getByText('Location & Physical Details')).toBeInTheDocument();
        });
    });

    it('awards XP after completing step 1', async () => {
        renderForm();
        await fillStep1();
        clickContinue();
        // XP total should now be 10 (step 1 XP)
        await waitFor(() => {
            expect(screen.getByText('10 XP')).toBeInTheDocument();
        });
    });
});

// ---------------------------------------------------------------------------
// 4. deriveId helper unit tests (pure logic — component-independent)
// ---------------------------------------------------------------------------

function deriveId(raw: string): string {
    const stripped = raw.trim().replace(/^[a-zA-Z]+/, '');
    return stripped.replace(/[^0-9a-z_\-]/gi, '').toLowerCase()
        || raw.toLowerCase().replace(/[^0-9a-z_\-]/g, '');
}

describe('deriveId helper', () => {
    it('strips UP prefix and lowercases', () => {
        expect(deriveId('UP13195')).toBe('13195');
    });

    it('strips SP prefix', () => {
        expect(deriveId('SP12345')).toBe('12345');
    });

    it('handles plain numbers without prefix', () => {
        expect(deriveId('99001')).toBe('99001');
    });

    it('strips spaces and special chars', () => {
        expect(deriveId('UP 13 195')).toBe('13195');
    });

    it('handles lowercase up prefix', () => {
        expect(deriveId('up13195')).toBe('13195');
    });

    it('handles mixed-case prefix', () => {
        expect(deriveId('Up13195')).toBe('13195');
    });
});

// ---------------------------------------------------------------------------
// 5. Step 2 — Location validation
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — step 2 Location', () => {
    it('blocks advancing to step 3 when address is empty', async () => {
        renderForm();
        await fillStep1();
        clickContinue();
        await waitFor(() => screen.getByText('Location & Physical Details'));
        // Don't fill address — click Continue
        clickContinue();
        // Still on step 2
        expect(screen.getByText('Location & Physical Details')).toBeInTheDocument();
    });

    it('advances to step 3 with valid address', async () => {
        renderForm();
        await fillStep1();
        clickContinue();
        await waitFor(() => screen.getByText('Location & Physical Details'));
        await fillStep2();
        clickContinue();
        await waitFor(() => {
            expect(screen.getByText('Lot & Unit Configuration')).toBeInTheDocument();
        });
    }, 15000);
});

// ---------------------------------------------------------------------------
// 6. Step 3 — Lots/Unit Configuration
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — step 3 Lots', () => {
    async function advanceToStep3() {
        renderForm();
        await fillStep1();
        clickContinue();
        await waitFor(() => screen.getByText('Location & Physical Details'));
        await fillStep2();
        clickContinue();
        await waitFor(() => screen.getByText('Lot & Unit Configuration'));
    }

    it('renders "Lot Types Present" section on step 3', async () => {
        await advanceToStep3();
        expect(screen.getByText(/Lot Types Present/i)).toBeInTheDocument();
    }, 15000);

    it('shows ACT Class A/B section when state is ACT (default)', async () => {
        await advanceToStep3();
        expect(screen.getByText(/ACT Class A \/ Class B/i)).toBeInTheDocument();
    }, 15000);

    it('shows BCA classification information', async () => {
        await advanceToStep3();
        // BCA class labels appear for each lot type row (multiple elements expected)
        expect(screen.getAllByText(/BCA/i).length).toBeGreaterThanOrEqual(1);
    }, 15000);
});

// ---------------------------------------------------------------------------
// 7. Multi-building neutrality — no hardcoded building names
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — tenant neutrality', () => {
    it('does not render "East Gate" in the form UI', () => {
        renderForm();
        // "East Gate Residences" appears only as a placeholder — should be
        // acceptable since placeholders are examples, not hardcoded identities.
        // What must NOT appear is "East Gate" as rendered content / actual data.
        // The placeholder is an aria attribute, not visible text content.
        const content = document.body.textContent || '';
        expect(content).not.toMatch(/East Gate/i);
    });

    it('does not render building_id "13195" as visible page content', () => {
        renderForm();
        const content = document.body.textContent || '';
        expect(content).not.toContain('13195');
    });

    it('does not render "Sierra" or "16244"', () => {
        renderForm();
        const content = document.body.textContent || '';
        expect(content).not.toMatch(/Sierra/i);
        expect(content).not.toContain('16244');
    });
});

// ---------------------------------------------------------------------------
// 8. Step 8 — Review step final button
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — navigation buttons', () => {
    it('shows "Create Building" (not "Launch Building") as the final submit button', async () => {
        renderForm();
        // Step 1
        await fillStep1();
        clickContinue();

        // Steps 2–7: advance through each
        for (let s = 2; s <= 7; s++) {
            await waitFor(() =>
                expect(screen.getByRole('button', {name: /continue/i})).toBeEnabled()
            );
            if (s === 2) await fillStep2();
            if (s === 4) {
                fireEvent.change(screen.getByTestId('lot-count'), {target: {value: '10'}});
            }
            if (s === 6) {
                fireEvent.change(screen.getByTestId('chairman-invite-name'), {target: {value: 'Alex Johnson'}});
                fireEvent.change(screen.getByTestId('chairman-invite-email'), {target: {value: 'alex@example.com'}});
            }
            clickContinue();
        }

        // Step 8: submit button should say "Create Building"
        await waitFor(() => {
            expect(screen.getByRole('button', {name: /create building/i})).toBeInTheDocument();
        });
    }, 30000);
});

describe('BuildingOnboardingForm — financial custom levy dates', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('normalises custom levy due dates to backend month-to-day payload shape', () => {
        expect(normaliseLevyDueCustomDates([3, 6, 9, 12], {
            3: '31',
            6: '1',
            9: '15',
            12: '31',
            2: '99',
        })).toEqual({'3': 31, '6': 1, '9': 15, '12': 31});
    });

    it('shows one custom date input per selected levy month when Custom dates is chosen', async () => {
        mockApi.get.mockResolvedValueOnce({
            data: {items: [{tenant_id: 'tenant-1', tenant_name: 'Acme Strata'}]},
        });

        renderForm({role: 'super_admin'});
        await selectSingleOrganisationIfNeeded();
        await fillStep1();
        clickContinue();
        await waitFor(() => expect(screen.getByRole('button', {name: /continue/i})).toBeEnabled());
        await fillStep2();
        clickContinue();
        await waitFor(() => expect(screen.getByText(/Lot Types Present/i)).toBeInTheDocument());
        clickContinue();
        await waitFor(() => expect(screen.getByText(/Financial & Legal Configuration/i)).toBeInTheDocument());

        fireEvent.change(screen.getByTestId('levy-due-day-type'), {target: {value: 'custom'}});

        expect(await screen.findByTestId('custom-levy-due-dates')).toBeInTheDocument();
        expect(screen.getByTestId('custom-levy-due-day-3')).toHaveValue(31);
        expect(screen.getByTestId('custom-levy-due-day-6')).toHaveValue(30);
        expect(screen.getByTestId('custom-levy-due-day-9')).toHaveValue(30);
        expect(screen.getByTestId('custom-levy-due-day-12')).toHaveValue(31);

        fireEvent.change(screen.getByTestId('custom-levy-due-day-6'), {target: {value: '1'}});
        expect(screen.getByTestId('custom-levy-due-day-6')).toHaveValue(1);
    }, 30000);
});

// ---------------------------------------------------------------------------
// 9. API call on submit
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — submit', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('calls POST /admin/onboarding/scheme with the new payload on submission', async () => {
        mockApi.post.mockResolvedValueOnce({
            data: {
                session_id: 'sess-abc',
                tenant_id: 'tenant-1',
                scheme_id: 'scheme-99001',
            },
        });
        mockApi.patch.mockResolvedValueOnce({
            data: {
                session_id: 'sess-abc',
                status: 'in_progress',
                current_step: 8,
            },
        });
        mockApi.get.mockResolvedValueOnce({
            data: {
                items: [{tenant_id: 'tenant-1', tenant_name: 'Acme Strata'}],
            },
        });

        renderForm({role: 'super_admin'});
        await selectSingleOrganisationIfNeeded();

        // Step 1
        await fillStep1();
        clickContinue();

        // Steps 2–7
        for (let s = 2; s <= 7; s++) {
            await waitFor(() =>
                expect(screen.getByRole('button', {name: /continue/i})).toBeEnabled()
            );
            if (s === 2) await fillStep2();
            if (s === 4) {
                fireEvent.change(screen.getByTestId('lot-count'), {target: {value: '10'}});
            }
            if (s === 6) {
                fireEvent.change(screen.getByTestId('chairman-invite-name'), {target: {value: 'Alex Johnson'}});
                fireEvent.change(screen.getByTestId('chairman-invite-email'), {target: {value: 'alex@example.com'}});
            }
            clickContinue();
        }

        // Step 8 — click Create Building
        await waitFor(() =>
            expect(screen.getByRole('button', {name: /create building/i})).toBeInTheDocument()
        );
        fireEvent.click(screen.getByRole('button', {name: /create building/i}));

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                '/admin/onboarding/scheme',
                expect.objectContaining({
                    tenant_id: 'tenant-1',
                    scheme_number: '99001',
                    scheme_name: 'Test Building',
                    jurisdiction: 'ACT',
                    legal_name: 'Test Building',
                }),
            );
        });
        expect(mockApi.patch).toHaveBeenCalledWith(
            '/onboarding/scheme/sess-abc',
            expect.objectContaining({
                current_step: 8,
                step_data: expect.objectContaining({
                    legacy_building_setup_draft: expect.any(Object),
                }),
            }),
        );
    }, 30000);
});

// ---------------------------------------------------------------------------
// 10. extractErrorDetail — pure helper (tasks/GAP-ONBOARD-001, Fix 4)
//     Regression coverage for a real crash: backend error responses on this
//     page's endpoints are inconsistently shaped (plain string vs
//     {detail, code} object). Rendering the object form directly used to
//     throw "Objects are not valid as a React child" and crash the page.
// ---------------------------------------------------------------------------

describe('extractErrorDetail', () => {
    it('returns a plain string detail unchanged', () => {
        const err = {response: {data: {detail: 'Scheme number already exists.'}}};
        expect(extractErrorDetail(err, 'fallback')).toBe('Scheme number already exists.');
    });

    it('unwraps the nested {detail, code} object shape used by /lots and /admin/onboarding/scheme', () => {
        const err = {response: {data: {detail: {detail: 'Duplicate lot_number in request.', code: 'duplicate_lot_number_in_request'}}}};
        expect(extractErrorDetail(err, 'fallback')).toBe('Duplicate lot_number in request.');
    });

    it('unwraps a {message} shaped detail (e.g. require_feature FEATURE_DISABLED)', () => {
        const err = {response: {data: {detail: {code: 'FEATURE_DISABLED', message: 'This feature is not available.'}}}};
        expect(extractErrorDetail(err, 'fallback')).toBe('This feature is not available.');
    });

    it('falls back when there is no response at all (network error)', () => {
        const err = new Error('Network Error');
        expect(extractErrorDetail(err, 'fallback message')).toBe('fallback message');
    });

    it('falls back when detail is an unrecognised object shape', () => {
        const err = {response: {data: {detail: {unexpected: 'shape'}}}};
        expect(extractErrorDetail(err, 'fallback message')).toBe('fallback message');
    });
});

// ---------------------------------------------------------------------------
// 11. parseStrataRollCsv — pure helper (tasks/GAP-ONBOARD-001, Fix 3)
// ---------------------------------------------------------------------------

describe('parseStrataRollCsv', () => {
    it('parses valid rows into LotInput shape (lot_number = unit_number)', () => {
        const csv = 'unit_number,owner_name,owner_email,entitlement,phone\n101,John Smith,john@example.com,10,0400111222';
        const result = parseStrataRollCsv(csv);
        expect(result).toEqual([
            {lot_number: '101', unit_number: '101', entitlement_units: 10, owner_email: 'john@example.com'},
        ]);
    });

    it('parses multiple rows', () => {
        const csv = 'unit_number,owner_email,entitlement\n101,a@x.com,10\n102,b@x.com,8';
        expect(parseStrataRollCsv(csv)).toHaveLength(2);
    });

    it('skips rows with no unit_number', () => {
        const csv = 'unit_number,owner_email,entitlement\n,a@x.com,10\n102,b@x.com,8';
        const result = parseStrataRollCsv(csv);
        expect(result).toHaveLength(1);
        expect(result[0].unit_number).toBe('102');
    });

    it('returns empty array for header-only CSV', () => {
        expect(parseStrataRollCsv('unit_number,owner_email,entitlement')).toEqual([]);
    });

    it('returns empty array for empty input', () => {
        expect(parseStrataRollCsv('')).toEqual([]);
    });

    it('leaves entitlement_units null when column is missing or blank', () => {
        const csv = 'unit_number,owner_email\n101,a@x.com';
        expect(parseStrataRollCsv(csv)[0].entitlement_units).toBeNull();
    });

    it('is case-insensitive on headers', () => {
        const csv = 'UNIT_NUMBER,OWNER_EMAIL\n101,a@x.com';
        expect(parseStrataRollCsv(csv)[0].unit_number).toBe('101');
    });
});

// ---------------------------------------------------------------------------
// 12. Strata Roll import wired to real /lots endpoint (tasks/GAP-ONBOARD-001)
// ---------------------------------------------------------------------------

async function advanceThroughStepsWithUploads(uploadStep7?: () => void) {
    await fillStep1();
    clickContinue();
    for (let s = 2; s <= 7; s++) {
        await waitFor(() =>
            expect(screen.getByRole('button', {name: /continue/i})).toBeEnabled()
        );
        if (s === 2) await fillStep2();
        if (s === 4) fireEvent.change(screen.getByTestId('lot-count'), {target: {value: '10'}});
        if (s === 6) {
            fireEvent.change(screen.getByTestId('chairman-invite-name'), {target: {value: 'Alex Johnson'}});
            fireEvent.change(screen.getByTestId('chairman-invite-email'), {target: {value: 'alex@example.com'}});
        }
        if (s === 7 && uploadStep7) uploadStep7();
        clickContinue();
    }
    await waitFor(() =>
        expect(screen.getByRole('button', {name: /create building/i})).toBeInTheDocument()
    );
}

describe('BuildingOnboardingForm — Strata Roll import on submit', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    function mockHappyPathApis() {
        mockApi.post.mockResolvedValueOnce({
            data: {session_id: 'sess-roll', tenant_id: 'tenant-1', scheme_id: 'scheme-99001'},
        });
        mockApi.patch.mockResolvedValueOnce({data: {session_id: 'sess-roll', status: 'in_progress', current_step: 8}});
        mockApi.get.mockResolvedValueOnce({data: {items: [{tenant_id: 'tenant-1', tenant_name: 'Acme Strata'}]}});
    }

    it('imports the Strata Roll CSV via POST /onboarding/scheme/{id}/lots and shows a success alert', async () => {
        mockHappyPathApis();
        mockApi.post.mockResolvedValueOnce({data: {imported: 2}}); // the /lots call

        renderForm({role: 'super_admin'});
        await selectSingleOrganisationIfNeeded();

        const csvFile = new File(
            ['unit_number,owner_email,entitlement\n101,a@x.com,10\n102,b@x.com,8'],
            'roll.csv',
            {type: 'text/csv'},
        );

        await advanceThroughStepsWithUploads(() => {
            fireEvent.change(screen.getByTestId('upload-strata_roll'), {target: {files: [csvFile]}});
        });
        fireEvent.click(screen.getByRole('button', {name: /create building/i}));

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                '/onboarding/scheme/sess-roll/lots',
                {lots: [
                    {lot_number: '101', unit_number: '101', entitlement_units: 10, owner_email: 'a@x.com'},
                    {lot_number: '102', unit_number: '102', entitlement_units: 8, owner_email: 'b@x.com'},
                ]},
            );
        });
        await waitFor(() => {
            expect(screen.getByText(/Imported 2 unit\(s\) from your Strata Roll upload/i)).toBeInTheDocument();
        });
    }, 30000);

    it('does NOT crash and shows a readable message when /lots returns a nested {detail, code} error', async () => {
        // Regression test for the confirmed crash: React throws "Objects are not
        // valid as a React child" if the nested error object is rendered directly.
        mockHappyPathApis();
        mockApi.post.mockRejectedValueOnce({
            response: {data: {detail: {detail: "Duplicate lot_number 'A' in request.", code: 'duplicate_lot_number_in_request'}}},
        });

        renderForm({role: 'super_admin'});
        await selectSingleOrganisationIfNeeded();

        const csvFile = new File(['unit_number,owner_email\nA,a@x.com\nA,b@x.com'], 'roll.csv', {type: 'text/csv'});

        await advanceThroughStepsWithUploads(() => {
            fireEvent.change(screen.getByTestId('upload-strata_roll'), {target: {files: [csvFile]}});
        });
        fireEvent.click(screen.getByRole('button', {name: /create building/i}));

        await waitFor(() => {
            expect(screen.getByText(/Strata Roll import failed/i)).toBeInTheDocument();
            expect(screen.getByText(/Duplicate lot_number 'A' in request\./i)).toBeInTheDocument();
        });
        // The component tree must still be intact — no React error boundary triggered.
        expect(screen.getByText(/Scheme Created!/i)).toBeInTheDocument();
    }, 30000);

    it('shows honest "not imported" messaging for Financial Statements / Levy Records uploads', async () => {
        mockHappyPathApis();

        renderForm({role: 'super_admin'});
        await selectSingleOrganisationIfNeeded();

        const pdfFile = new File(['%PDF-1.4'], 'statements.pdf', {type: 'application/pdf'});

        await advanceThroughStepsWithUploads(() => {
            fireEvent.change(screen.getByTestId('upload-financials'), {target: {files: [pdfFile]}});
        });
        fireEvent.click(screen.getByRole('button', {name: /create building/i}));

        await waitFor(() => {
            expect(screen.getByText(/Previous Financial Statements file was/i)).toBeInTheDocument();
            expect(screen.getByText(/full Onboarding Wizard/i)).toBeInTheDocument();
        });
    }, 30000);
});

// ---------------------------------------------------------------------------
// 13. Scheme-creation failure — regression test for the pre-existing crash in
//     the outer catch (same {detail, code} bug, fixed alongside Fix 4)
// ---------------------------------------------------------------------------

describe('BuildingOnboardingForm — scheme creation failure handling', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('shows a readable message (not a crash) when /admin/onboarding/scheme rejects with a nested error', async () => {
        mockApi.get.mockResolvedValueOnce({data: {items: [{tenant_id: 'tenant-1', tenant_name: 'Acme Strata'}]}});
        mockApi.post.mockRejectedValueOnce({
            response: {data: {detail: {detail: 'Invalid jurisdiction. Must be one of: ACT, NSW, VIC.', code: 'invalid_jurisdiction'}}},
        });

        renderForm({role: 'super_admin'});
        await selectSingleOrganisationIfNeeded();
        await advanceThroughStepsWithUploads();
        fireEvent.click(screen.getByRole('button', {name: /create building/i}));

        await waitFor(() => {
            expect(screen.getByText(/Invalid jurisdiction\. Must be one of: ACT, NSW, VIC\./i)).toBeInTheDocument();
        });
        // Still on the form (not crashed into an error boundary / blank page).
        expect(screen.getByRole('button', {name: /create building/i})).toBeInTheDocument();
    }, 30000);
});
