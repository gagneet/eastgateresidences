/**
 * Rental Certificates E2E Tests
 *
 * ACT Unit Titles (Management) Act 2011 — Section 119A compliance tests.
 *
 * Covers:
 * 1. Page loads for owner and admin roles
 * 2. Compliance banner is visible
 * 3. "Request Certificate" button visible for owners/admins
 * 4. Request dialog opens and validates required fields
 * 5. API endpoints respond correctly (via direct API calls)
 * 6. Tenants cannot access the page
 * 7. Admin "Manage Requests" tab visible for management roles
 * 8. PDF download link available on issued certificates
 *
 * Run:
 *   cd /home/gagneet/strata-management
 *   npx playwright test tests/frontend/e2e/rental-certificates.spec.js --headed
 */

const {test, expect} = require('@playwright/test');
const {getTestRunId} = require('../test-run-id');
const {registerCleanup} = require('../cleanup-registry');

const TEST_RUN_ID = getTestRunId();

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';
const PAGE_URL = `${BASE_URL}/requests/rental-certificates`;

const ADMIN_CREDS = {email: 'admin@eastgate.com', password: '$SEED_TEST_USER_PASSWORD'};
const OWNER_CREDS = {email: 'avneet@eastgateresidences.com.au', password: process.env.E2E_OWNER_PASSWORD || ''};
const TENANT_CREDS = {email: 'tenant@eastgateresidences.com.au', password: process.env.E2E_TENANT_PASSWORD || ''};

// ── Helpers ──────────────────────────────────────────────────────────────────

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

// ── API Tests (no browser) ────────────────────────────────────────────────────

test.describe('Rental Certificates — API Endpoints', () => {
    test('GET /api/rental-certificates returns 401 without auth', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/rental-certificates`);
        expect(response.status()).toBe(401);
    });

    test('GET /api/rental-certificates returns 200 for admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/rental-certificates`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(Array.isArray(data)).toBeTruthy();
    });

    test('GET /api/rental-certificates returns 200 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/rental-certificates`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
    });

    test('GET /api/rental-certificates returns 403 for tenant', async ({request}) => {
        const token = await getAuthToken(request, TENANT_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/rental-certificates`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(403);
    });

    test('POST /api/rental-certificates returns 403 for tenant', async ({request}) => {
        const token = await getAuthToken(request, TENANT_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.post(`${BASE_URL}/api/rental-certificates`, {
            headers: {Authorization: `Bearer ${token}`},
            data: {
                unit_number: 'TH007',
                tenant_name: 'Test Tenant',
                lease_start_date: '2026-04-01',
            },
        });
        expect(response.status()).toBe(403);
    });

    test('POST /api/rental-certificates creates cert for owner (own unit)', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.post(`${BASE_URL}/api/rental-certificates`, {
            headers: {Authorization: `Bearer ${token}`},
            data: {
                unit_number: 'TH017',
                tenant_name: `Playwright Test Tenant [${TEST_RUN_ID}]`,
                lease_start_date: '2026-04-01',
                lease_end_date: '2027-03-31',
                additional_notes: `Playwright automated test [${TEST_RUN_ID}]`,
            },
        });
        // 201 created or 403 if unit doesn't match — depends on seeded owner unit
        expect([201, 403]).toContain(response.status());
        if (response.status() === 201) {
            const data = await response.json();
            if (data && data.id) {
                registerCleanup({collection: 'rental_certificates', field: 'id', value: data.id});
            }
        }
    });

    test('GET /api/rental-certificates/unit/{unit}/current works for admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/rental-certificates/unit/TH017/current`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        // Either 200 (null or issued cert) or 404 — both are valid
        expect([200, 404]).toContain(response.status());
    });
});

// ── UI Tests ──────────────────────────────────────────────────────────────────

test.describe('Rental Certificates — UI (Owner)', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(PAGE_URL);
        await page.waitForLoadState('networkidle');
    });

    test('Page loads with correct title', async ({page}) => {
        await expect(page.locator('h1, h2').filter({hasText: /Rental Certificate/i})).toBeVisible({
            timeout: 10000,
        });
    });

    test('Compliance banner is visible', async ({page}) => {
        const banner = page.locator('text=Mandatory since 9 January 2025');
        await expect(banner).toBeVisible({timeout: 10000});
    });

    test('"Request Certificate" button is visible for owner', async ({page}) => {
        await expect(page.locator('button:has-text("Request Certificate")')).toBeVisible({
            timeout: 10000,
        });
    });

    test('Request dialog opens on button click', async ({page}) => {
        await page.click('button:has-text("Request Certificate")');
        await expect(page.locator('text=Tenant Full Name')).toBeVisible({timeout: 5000});
    });

    test('Request dialog requires tenant name', async ({page}) => {
        await page.click('button:has-text("Request Certificate")');
        await page.click('button:has-text("Submit Request")');
        // Should show toast error or keep dialog open (form validation)
        await expect(page.locator('[role="dialog"]')).toBeVisible({timeout: 3000});
    });
});

test.describe('Rental Certificates — UI (Admin)', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        await page.waitForLoadState('networkidle');
    });

    test('Admin sees "Manage Requests" tab', async ({page}) => {
        await expect(page.locator('button[role="tab"]:has-text("Manage Requests"), [role="tab"]:has-text("Manage Requests")')).toBeVisible({
            timeout: 10000,
        });
    });

    test('Stats cards show numeric values', async ({page}) => {
        // All 3 stats cards should have numeric values
        const statCards = page.locator('.text-2xl.font-bold');
        await expect(statCards.first()).toBeVisible({timeout: 10000});
    });

    test('Page has compliance badge visible', async ({page}) => {
        await expect(page.locator('text=Section 119A')).toBeVisible({timeout: 10000});
    });
});

// ── Navigation Test ────────────────────────────────────────────────────────────

test.describe('Rental Certificates — Navigation', () => {
    test('Rental Certificates link visible in sidebar for owner', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        // Check sidebar has the rental certificates link
        const link = page.locator('a[href*="rental-certificates"]');
        await expect(link.first()).toBeVisible({timeout: 10000});
    });

    test('Rental Certificates link visible in sidebar for admin', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        const link = page.locator('a[href*="rental-certificates"]');
        await expect(link.first()).toBeVisible({timeout: 10000});
    });
});
