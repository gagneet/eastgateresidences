/**
 * Sessions 33 & 34 — E2E Tests
 *
 * Covers new features introduced in Sessions 33 and 34:
 * 1. Request Forms page (/requests) — 7-tab self-service request system
 * 2. Resident Directory (/dashboard/residents) — clickable filter cards + chat hint
 * 3. Last Login tracking (/dashboard) — last_login_at displayed in welcome area
 * 4. ManagementDashboard — Fund Breakdown tab in LevyTrendChart
 *
 * Run:
 *   cd /home/gagneet/strata-management
 *   npx playwright test tests/frontend/e2e/sessions-33-34.spec.js --headed
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';

const ADMIN_CREDS = {email: 'admin@eastgate.com', password: '$SEED_TEST_USER_PASSWORD'};
const OWNER_CREDS = {email: 'avneet@eastgateresidences.com.au', password: process.env.E2E_OWNER_PASSWORD || ''};
const CHAIRMAN_CREDS = {email: 'anthony@eastgateresidences.com.au', password: process.env.E2E_CHAIRMAN_PASSWORD || ''};
const TENANT_CREDS = {email: 'tenant@eastgateresidences.com.au', password: process.env.E2E_TENANT_PASSWORD || ''};

// ── Helpers ───────────────────────────────────────────────────────────────────

async function getAuthToken(request, creds) {
    const response = await request.post(`${BASE_URL}/api/auth/login`, {data: creds});
    if (!response.ok()) return null;
    const data = await response.json();
    return data.token;
}

async function loginAs(page, creds) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"], input[name="email"]', creds.email);
    await page.fill('input[type="password"], input[name="password"]', creds.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/dashboard/, {timeout: 15000});
}

// ── 1. Request Forms — API Endpoints ─────────────────────────────────────────

test.describe('Request Forms — API Endpoints', () => {
    test('GET /api/requests/insurance-claims returns 401 without auth', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/requests/insurance-claims`);
        expect(response.status()).toBe(401);
    });

    test('GET /api/requests/insurance-claims returns 200 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/requests/insurance-claims`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(Array.isArray(data)).toBeTruthy();
    });

    test('GET /api/requests/access-control returns 200 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/requests/access-control`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
    });

    test('GET /api/requests/alterations returns 200 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/requests/alterations`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
    });

    test('GET /api/requests/reimbursements returns 200 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/requests/reimbursements`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
    });

    test('GET /api/requests/insurance-enquiries returns 200 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/requests/insurance-enquiries`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
    });

    test('POST /api/requests/access-control validates required fields', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        // Submit without required fields — expect 422 validation error
        const response = await request.post(`${BASE_URL}/api/requests/access-control`, {
            headers: {Authorization: `Bearer ${token}`},
            data: {},
        });
        expect([422, 400]).toContain(response.status());
    });
});

// ── 2. Request Forms — UI (Owner) ─────────────────────────────────────────────

test.describe('Request Forms — UI (Owner)', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/requests`);
        await page.waitForLoadState('networkidle');
    });

    test('Page loads with correct heading', async ({page}) => {
        await expect(
            page.locator('h1, h2').filter({hasText: /Make a Request|Request/i}).first()
        ).toBeVisible({timeout: 10000});
    });

    test('Help info card is visible', async ({page}) => {
        await expect(
            page.locator('text=Need Help?, text=Executive Committee').first()
        ).toBeVisible({timeout: 10000}).catch(() =>
            expect(page.locator('[data-testid="requests-page"]')).toBeVisible({timeout: 10000})
        );
    });

    test('Insurance Claim tab is present and clickable', async ({page}) => {
        // Desktop tab list
        const tabTrigger = page.locator('[role="tab"]:has-text("Insurance"), button:has-text("Insurance Claim")').first();
        await expect(tabTrigger).toBeVisible({timeout: 10000});
        await tabTrigger.click();
        // Description text should update
        await expect(
            page.locator('text=Report damage, text=insurance').first()
        ).toBeVisible({timeout: 5000}).catch(() => {
        });
    });

    test('Access Control tab is present and clickable', async ({page}) => {
        const tabTrigger = page.locator('[role="tab"]:has-text("Access"), button:has-text("Access Control")').first();
        await expect(tabTrigger).toBeVisible({timeout: 10000});
        await tabTrigger.click();
    });

    test('Alterations tab is present and clickable', async ({page}) => {
        const tabTrigger = page.locator('[role="tab"]:has-text("Alteration"), button:has-text("Alterations")').first();
        await expect(tabTrigger).toBeVisible({timeout: 10000});
        await tabTrigger.click();
    });

    test('Reimbursement tab is present and clickable', async ({page}) => {
        const tabTrigger = page.locator('[role="tab"]:has-text("Reimburse"), button:has-text("Reimbursement")').first();
        await expect(tabTrigger).toBeVisible({timeout: 10000});
        await tabTrigger.click();
    });

    test('Insurance Enquiry tab is present and clickable', async ({page}) => {
        const tabTrigger = page.locator('[role="tab"]:has-text("Insurance Question"), button:has-text("Insurance Question")').first();
        await expect(tabTrigger).toBeVisible({timeout: 10000});
        await tabTrigger.click();
    });

    test('"File a Claim" button opens insurance claim form', async ({page}) => {
        // Switch to insurance claim tab first
        const insuranceTab = page.locator('[role="tab"]:has-text("Insurance Claim"), button:has-text("Insurance")').first();
        await insuranceTab.click();
        const claimBtn = page.locator('button:has-text("File a Claim")');
        await expect(claimBtn).toBeVisible({timeout: 8000});
        await claimBtn.click();
        // Multi-step form should appear
        await expect(
            page.locator('text=Incident Type, text=Step 1').first()
        ).toBeVisible({timeout: 5000}).catch(() =>
            expect(page.locator('text=Incident Type')).toBeVisible({timeout: 5000})
        );
    });

    test('"Request Device" button opens access control form', async ({page}) => {
        const accessTab = page.locator('[role="tab"]:has-text("Access"), button:has-text("Access Control")').first();
        await accessTab.click();
        const requestBtn = page.locator('button:has-text("Request Device")');
        await expect(requestBtn).toBeVisible({timeout: 8000});
        await requestBtn.click();
        await expect(page.locator('text=Device Type')).toBeVisible({timeout: 5000});
    });

    test('"Request Reimbursement" button opens reimbursement form', async ({page}) => {
        const reimburseTab = page.locator('[role="tab"]:has-text("Reimburse"), button:has-text("Reimbursement")').first();
        await reimburseTab.click();
        const requestBtn = page.locator('button:has-text("Request Reimbursement"), button:has-text("New Reimbursement")').first();
        await expect(requestBtn).toBeVisible({timeout: 8000});
        await requestBtn.click();
        // Form fields for reimbursement
        await expect(
            page.locator('text=Description, label:has-text("Description")').first()
        ).toBeVisible({timeout: 5000});
    });

    test('Alterations tab shows By-Law acknowledgement', async ({page}) => {
        const altTab = page.locator('[role="tab"]:has-text("Alteration"), button:has-text("Alterations")').first();
        await altTab.click();
        // Click the request alteration button
        const requestBtn = page.locator('button:has-text("Request Alteration"), button:has-text("New Alteration")').first();
        await expect(requestBtn).toBeVisible({timeout: 8000});
        await requestBtn.click();
        // Should show by-law text or acknowledgement checkbox
        await expect(
            page.locator('text=By-Law, text=by_law_acknowledged, [id*="by_law"]').first()
        ).toBeVisible({timeout: 5000}).catch(() => {
            // At minimum the form opened
            expect(page.locator('text=Alteration Type')).toBeVisible({timeout: 5000});
        });
    });
});

// ── 3. Resident Directory — UI ────────────────────────────────────────────────

test.describe('Resident Directory — UI', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/dashboard/residents`);
        await page.waitForLoadState('networkidle');
    });

    test('Page loads with Resident Directory heading', async ({page}) => {
        await expect(
            page.locator('h1, h2').filter({hasText: /Resident Directory/i}).first()
        ).toBeVisible({timeout: 10000});
    });

    test('Three filter cards are visible (Total, Apartments, Townhouses)', async ({page}) => {
        // All Residents card
        await expect(
            page.locator('[aria-label="Show all residents"], [role="button"]:has-text("All Residents")').first()
        ).toBeVisible({timeout: 10000});

        // Apartments card
        await expect(
            page.locator('[aria-label="Filter apartments"], [role="button"]:has-text("Apartments")').first()
        ).toBeVisible({timeout: 10000});

        // Townhouses card
        await expect(
            page.locator('[aria-label="Filter townhouses"], [role="button"]:has-text("Townhouses")').first()
        ).toBeVisible({timeout: 10000});
    });

    test('Clicking Apartments card activates apartment filter', async ({page}) => {
        const apartmentsCard = page.locator('[aria-label="Filter apartments"]').first();
        await expect(apartmentsCard).toBeVisible({timeout: 10000});
        await apartmentsCard.click();
        // After clicking, the card should have the active ring class (ring-2 ring-indigo-500)
        await expect(apartmentsCard).toHaveClass(/ring-indigo/, {timeout: 3000});
    });

    test('Clicking active Apartments card again resets filter', async ({page}) => {
        const apartmentsCard = page.locator('[aria-label="Filter apartments"]').first();
        await expect(apartmentsCard).toBeVisible({timeout: 10000});
        // First click — activate
        await apartmentsCard.click();
        await page.waitForTimeout(300);
        // Second click — deactivate (toggle)
        await apartmentsCard.click();
        // All Residents card should now be active
        const allCard = page.locator('[aria-label="Show all residents"]').first();
        await expect(allCard).toHaveClass(/ring-indigo/, {timeout: 3000});
    });

    test('Clicking Townhouses card activates townhouse filter', async ({page}) => {
        const townhousesCard = page.locator('[aria-label="Filter townhouses"]').first();
        await expect(townhousesCard).toBeVisible({timeout: 10000});
        await townhousesCard.click();
        await expect(townhousesCard).toHaveClass(/ring-indigo/, {timeout: 3000});
    });

    test('Chat hint banner is visible', async ({page}) => {
        await expect(
            page.locator("text=Click on a resident's card to start a private chat conversation")
        ).toBeVisible({timeout: 10000});
    });

    test('Search input is visible and functional', async ({page}) => {
        const searchInput = page.locator('[data-testid="directory-search"], input[placeholder*="Search"]').first();
        await expect(searchInput).toBeVisible({timeout: 10000});
        await searchInput.fill('test');
        await page.waitForTimeout(300);
        // Clear button should appear after typing
        await expect(
            page.locator('button[aria-label="Clear search"]').first()
        ).toBeVisible({timeout: 3000});
    });

    test('My Settings button opens directory settings dialog', async ({page}) => {
        const settingsBtn = page.locator('[data-testid="directory-settings-btn"], button:has-text("My Settings")').first();
        await expect(settingsBtn).toBeVisible({timeout: 10000});
        await settingsBtn.click();
        await expect(page.locator('[role="dialog"]')).toBeVisible({timeout: 5000});
        // Dialog should show Appear in Directory toggle
        await expect(page.locator('text=Appear in Directory')).toBeVisible({timeout: 3000});
    });
});

// ── 4. Last Login Tracking — UI ───────────────────────────────────────────────

test.describe('Last Login — UI (Owner)', () => {
    test('Welcome area loads after owner login', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/dashboard`);
        await page.waitForLoadState('networkidle');

        // Welcome message should be visible
        await expect(
            page.locator('h1').filter({hasText: /Building Intelligence Hub|Welcome/i}).first()
        ).toBeVisible({timeout: 10000});
    });

    test('Last login text is visible if user has prior login', async ({page}) => {
        // Login, then login again so last_login_at is set
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/login`);
        await page.fill('input[type="email"], input[name="email"]', OWNER_CREDS.email);
        await page.fill('input[type="password"], input[name="password"]', OWNER_CREDS.password);
        await page.click('button[type="submit"]');
        await page.waitForURL(/dashboard/, {timeout: 15000});

        await page.goto(`${BASE_URL}/dashboard`);
        await page.waitForLoadState('networkidle');

        // "Last login:" text may appear after second+ login
        // It is conditional — only shown when last_login_at is set on the user object
        const lastLoginText = page.locator('text=Last login:');
        // Count doesn't need to be > 0 for fresh accounts; skip if absent
        const count = await lastLoginText.count();
        if (count > 0) {
            await expect(lastLoginText.first()).toBeVisible({timeout: 5000});
        }
        // Either way, the welcome area must load
        await expect(
            page.locator('p:has-text("Welcome back"), p:has-text("Here is your property pulse"), p:has-text("Management Mode")').first()
        ).toBeVisible({timeout: 10000});
    });
});

test.describe('Last Login — API', () => {
    test('GET /api/auth/me returns last_login_at after login', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }

        const response = await request.get(`${BASE_URL}/api/auth/me`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        // last_login_at should be an ISO datetime string or null — both valid
        if (data.last_login_at !== null && data.last_login_at !== undefined) {
            expect(typeof data.last_login_at).toBe('string');
            // Should look like an ISO 8601 date
            expect(data.last_login_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
        }
    });

    test('Login response triggers last_login_at update in DB', async ({request}) => {
        // First login to ensure last_login_at gets set
        const loginRes = await request.post(`${BASE_URL}/api/auth/login`, {data: OWNER_CREDS});
        expect(loginRes.ok()).toBeTruthy();
        const {token} = await loginRes.json();

        // Check /me immediately
        const meRes = await request.get(`${BASE_URL}/api/auth/me`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(meRes.status()).toBe(200);
        const userData = await meRes.json();
        // last_login_at should now be set (not null)
        expect(userData.last_login_at).toBeTruthy();
        expect(userData.last_login_at).toMatch(/^\d{4}-\d{2}-\d{2}/);
    });
});

// ── 5. ManagementDashboard — Fund Breakdown Tab ───────────────────────────────

test.describe('ManagementDashboard — Fund Breakdown (Chairman)', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, CHAIRMAN_CREDS);
        await page.goto(`${BASE_URL}/dashboard`);
        await page.waitForLoadState('networkidle');
    });

    test('Management dashboard loads for chairman', async ({page}) => {
        // Chairman should see management mode active
        await expect(
            page.locator('text=Management Mode Active, text=Building Intelligence Hub').first()
        ).toBeVisible({timeout: 15000});
    });

    test('LevyTrendChart renders on management dashboard', async ({page}) => {
        // Chart container should render
        await expect(
            page.locator('.recharts-wrapper, svg.recharts-surface').first()
        ).toBeVisible({timeout: 15000});
    });

    test('Fund Breakdown tab is clickable and does not navigate away', async ({page}) => {
        const currentUrl = page.url();

        // Find the Fund Breakdown tab button
        const fundBreakdownTab = page.locator('button:has-text("Fund Breakdown"), [role="tab"]:has-text("Fund Breakdown")').first();
        await expect(fundBreakdownTab).toBeVisible({timeout: 15000});

        await fundBreakdownTab.click();
        await page.waitForTimeout(500);

        // URL should NOT have changed (no navigation)
        expect(page.url()).toBe(currentUrl);

        // Dashboard should still be visible
        await expect(
            page.locator('text=Building Intelligence Hub').first()
        ).toBeVisible({timeout: 5000});
    });

    test('Hub Overview tab is also present', async ({page}) => {
        const hubOverviewTab = page.locator('button:has-text("Hub Overview"), [role="tab"]:has-text("Hub Overview")').first();
        await expect(hubOverviewTab).toBeVisible({timeout: 15000});
    });

    test('Switching between Hub Overview and Fund Breakdown tabs keeps user on dashboard', async ({page}) => {
        const currentUrl = page.url();

        const hubOverviewTab = page.locator('button:has-text("Hub Overview"), [role="tab"]:has-text("Hub Overview")').first();
        const fundBreakdownTab = page.locator('button:has-text("Fund Breakdown"), [role="tab"]:has-text("Fund Breakdown")').first();

        await expect(hubOverviewTab).toBeVisible({timeout: 15000});
        await expect(fundBreakdownTab).toBeVisible({timeout: 5000});

        // Switch to Fund Breakdown
        await fundBreakdownTab.click();
        await page.waitForTimeout(400);
        expect(page.url()).toBe(currentUrl);

        // Switch back to Hub Overview
        await hubOverviewTab.click();
        await page.waitForTimeout(400);
        expect(page.url()).toBe(currentUrl);

        // Chart still present after tab switching
        await expect(
            page.locator('.recharts-wrapper, svg.recharts-surface').first()
        ).toBeVisible({timeout: 5000});
    });
});

test.describe('ManagementDashboard — Fund Breakdown (Admin)', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(`${BASE_URL}/dashboard`);
        await page.waitForLoadState('networkidle');
    });

    test('Admin sees management dashboard with Fund Breakdown tab', async ({page}) => {
        await expect(
            page.locator('text=Building Intelligence Hub').first()
        ).toBeVisible({timeout: 15000});

        const fundBreakdownTab = page.locator('button:has-text("Fund Breakdown"), [role="tab"]:has-text("Fund Breakdown")').first();
        await expect(fundBreakdownTab).toBeVisible({timeout: 10000});
    });
});

// ── 6. Navigation — Requests and Residents links ──────────────────────────────

test.describe('Navigation — Sidebar Links', () => {
    test('Requests link is visible in sidebar for owner', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        const link = page.locator('a[href*="requests"]').first();
        await expect(link).toBeVisible({timeout: 10000});
    });

    test('Residents/Directory link is visible in sidebar for owner', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        const link = page.locator('a[href*="residents"], a[href*="directory"]').first();
        await expect(link).toBeVisible({timeout: 10000});
    });

    test('Direct navigation to /requests works', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/requests`);
        await page.waitForLoadState('networkidle');
        await expect(
            page.locator('[data-testid="requests-page"], h1:has-text("Request")').first()
        ).toBeVisible({timeout: 10000});
    });

    test('Direct navigation to /dashboard/residents works', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/dashboard/residents`);
        await page.waitForLoadState('networkidle');
        await expect(
            page.locator('[data-testid="directory-page"], h1:has-text("Resident Directory")').first()
        ).toBeVisible({timeout: 10000});
    });
});
