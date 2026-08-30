/**
 * Financial GUI Audit — Session 1 (2026-07-02)
 *
 * Real browser verification (headless Chromium via Playwright, not a mocked page.route()
 * fixture) that the dollar figures rendered on the page match the backend API's own values,
 * for the highest-value slice of pages previously "verified" only at the code/DB level. See
 * docs/architecture/financial_browser_verification.md for the full methodology and page
 * inventory this spec is Cluster A of.
 *
 * Safety: targets ONLY http://localhost:3020 (BASE_URL must be set explicitly — never runs
 * against production) and ONLY the Acme demo tenant (UP-DEMO-001). No writes; read-only pages.
 *
 * Run:
 *   BASE_URL=http://localhost:3020 npx playwright test tests/frontend/financial_gui_audit.spec.js --project=chromium
 */

const {test, expect} = require('@playwright/test');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8003';

const MANAGER = {email: 'manager@acmestrata.demo', password: 'DemoUser$01'};
const OWNER = {email: 'james.mitchell@acmedemo.au', password: 'DemoUser$01'};

test.beforeAll(async () => {
    const base = test.info().project.use.baseURL || '';
    if (!base) {
        throw new Error('Refusing to run financial_gui_audit.spec.js: BASE_URL must be set to http://localhost:3020.');
    }

    let baseUrl;
    let backendUrl;
    try {
        baseUrl = new URL(base);
        backendUrl = new URL(BACKEND_URL);
    } catch {
        throw new Error('Refusing to run financial_gui_audit.spec.js: BASE_URL and BACKEND_URL must be valid URLs.');
    }

    if (baseUrl.origin !== 'http://localhost:3020') {
        throw new Error(`Refusing to run financial_gui_audit.spec.js: BASE_URL must be exactly http://localhost:3020 (got ${baseUrl.origin}).`);
    }
    if (backendUrl.protocol !== 'http:' || backendUrl.hostname !== 'localhost') {
        throw new Error(`Refusing to run financial_gui_audit.spec.js: BACKEND_URL must be a localhost http URL (got ${backendUrl.origin}).`);
    }
});

/** Log in via a raw API call and return the bearer token — used to fetch ground-truth
 * values independently of whatever the page's own fetch does. */
async function apiLogin(request, creds) {
    const res = await request.post(`${BACKEND_URL}/api/auth/login`, {data: creds});
    if (!res.ok()) {
        throw new Error(`API login failed for ${creds.email}: ${res.status()} ${await res.text()}`);
    }
    const body = await res.json();
    return body.token;
}

async function apiGet(request, path, token) {
    const res = await request.get(`${BACKEND_URL}${path}`, {headers: {Authorization: `Bearer ${token}`}});
    if (!res.ok()) {
        throw new Error(`API GET ${path} failed: ${res.status()} ${await res.text()}`);
    }
    return res.json();
}

/** Log in via the real UI form (not a mocked route) — exercises the actual login page,
 * NextAuth session bootstrap, and dashboard redirect. */
async function uiLogin(page, creds) {
    await page.goto('/login');
    await page.getByTestId('email-input').fill(creds.email);
    await page.getByTestId('password-input').fill(creds.password);
    await page.getByTestId('login-submit').click();
    await page.waitForURL('**/dashboard**', {timeout: 15000});
}

function fmtAUD(amount) {
    return new Intl.NumberFormat('en-AU', {style: 'currency', currency: 'AUD'}).format(amount);
}

// ─────────────────────────────────────────────────────────────────────────────

test.describe('Financial GUI audit — owner pages', () => {
    test('My Finances: rendered quarterly levy matches /owner-finance/levy-breakdown', async ({page, request}) => {
        const token = await apiLogin(request, OWNER);
        const truth = await apiGet(request, '/api/owner-finance/levy-breakdown', token);
        expect(truth.quarterly_levy, 'demo data precondition: quarterly_levy must be > 0 for this check to be meaningful').toBeGreaterThan(0);

        await uiLogin(page, OWNER);
        await page.goto('/financials/my-finances');
        await expect(page.getByText('Where does my levy go?')).toBeVisible({timeout: 15000});

        // fmtDollarsWhole in MyFinancesPage.jsx renders 2dp with thousands separator, no cents rounding.
        const expected = truth.quarterly_levy.toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        await expect(page.getByText(new RegExp(expected.replace('.', '\\.')))).toBeVisible();
    });

    test('True Cost of Ownership: rendered strata levies match /owner-hub/unit-tco', async ({page, request}) => {
        const token = await apiLogin(request, OWNER);
        const truth = await apiGet(request, '/api/owner-hub/unit-tco?unit_number=A1', token);
        expect(truth.strata_levies, 'demo data precondition').toBeGreaterThan(0);

        await uiLogin(page, OWNER);
        await page.goto('/owner-hub/tco');
        await expect(page.getByTestId('unit-combobox-trigger')).toBeVisible({timeout: 15000});

        // Owner-role users land pre-scoped to their own unit. The cost breakdown card
        // (Total Annual Cost + Strata Levies rows) loads via a second async fetch after
        // the combobox mounts — use an auto-retrying locator assertion (not a one-shot
        // body.textContent() snapshot, which races the fetch and reads a stale DOM).
        const expected = truth.strata_levies.toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        await expect(page.getByText(`$${expected}`).first()).toBeVisible({timeout: 15000});
    });

    test('Main Dashboard (owner): page renders with real financial data, no crash', async ({page, request}) => {
        const token = await apiLogin(request, OWNER);
        const truth = await apiGet(request, '/api/owners-units/A1', token);
        expect(truth.unit_number).toBe('A1');
        // Cross-check against the same annual-levy figure independently verified on the
        // My Finances and TCO pages (quarterly_levy × 4 = annual) — this dashboard variant
        // ("Dashboard v2" / Live View, frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx)
        // has no data-testid hooks at all, so the ground-truth dollar figure itself is the
        // only reliable, meaningful thing to assert on here.
        const levyTruth = await apiGet(request, '/api/owner-finance/levy-breakdown', token);
        const annual = levyTruth.quarterly_levy * 4;
        const expected = annual.toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2});

        const consoleErrors = [];
        page.on('console', (msg) => {
            if (msg.type() === 'error') consoleErrors.push(msg.text());
        });

        await uiLogin(page, OWNER);
        await page.goto('/dashboard');
        await expect(page.getByText(`$${expected}`).first()).toBeVisible({timeout: 15000});

        const realErrors = consoleErrors.filter((e) => !e.includes('favicon') && !e.includes('Failed to load resource'));
        expect(realErrors, `unexpected console errors on owner Dashboard: ${realErrors.join('; ')}`).toHaveLength(0);
    });
});

test.describe('Financial GUI audit — manager/admin pages', () => {
    test('Finance page: rendered total levied matches /finance/summary.unit_ledger_summary.total_levied', async ({page, request}) => {
        const token = await apiLogin(request, MANAGER);
        const truth = await apiGet(request, '/api/finance/summary', token);
        const totalLevied = truth.unit_ledger_summary?.total_levied;
        expect(totalLevied, 'demo data precondition: unit_ledger_summary.total_levied must exist').toBeGreaterThan(0);

        await uiLogin(page, MANAGER);
        await page.goto('/financials');
        await expect(page.getByTestId('finance-page')).toBeVisible({timeout: 15000});

        await expect(page.getByText(fmtAUD(totalLevied))).toBeVisible({timeout: 10000});
    });

    test('Arrears Board: rendered total arrears matches /stats/building-kpis.total_arrears', async ({page, request}) => {
        const token = await apiLogin(request, MANAGER);
        const truth = await apiGet(request, '/api/stats/building-kpis', token);
        expect(truth.total_arrears, 'demo data precondition: total_arrears must be > 0 for this check to be meaningful').toBeGreaterThan(0);

        await uiLogin(page, MANAGER);
        await page.goto('/intelligence/debt-recovery');
        await expect(page.getByText(fmtAUD(truth.total_arrears))).toBeVisible({timeout: 15000});
    });

    test('Trust Bank Accounts: renders the correct empty state for a tenant with zero configured accounts', async ({page, request}) => {
        const token = await apiLogin(request, MANAGER);
        const truth = await apiGet(request, '/api/trust/v2/accounts', token);
        // This is itself a real finding, not an assumption: the Acme demo tenant currently
        // has zero trust accounts configured. If this ever becomes non-empty, this test
        // should be updated to assert the rendered balance instead of the empty state.
        expect(Array.isArray(truth.data)).toBe(true);

        await uiLogin(page, MANAGER);
        await page.goto('/financials/trust-bank-accounts');
        await expect(page.getByText('Trust Account & Bank Accounts')).toBeVisible({timeout: 15000});

        if (truth.data.length === 0) {
            await expect(page.getByText('No trust accounts configured yet. Create accounts via Trust Accounting.')).toBeVisible();
        } else {
            const totalBalance = truth.data.reduce((s, a) => s + (a.computed_balance_cents ?? a.current_balance_cents ?? 0), 0);
            await expect(page.getByText(fmtAUD(totalBalance / 100))).toBeVisible();
        }
    });
});
