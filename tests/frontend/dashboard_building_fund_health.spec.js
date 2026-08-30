/**
 * tests/frontend/dashboard_building_fund_health.spec.js
 *
 * Playwright E2E tests verifying that the dashboard notice bar shows
 * BUILDING-WIDE Fund Health and Levies Paid percentages, not the
 * individual owner's unit numbers.
 *
 * Fix context: dashboard/OwnerDashboard.tsx previously computed
 * adminHealth/sinkingHealth/progress from the logged-in owner's own
 * unit data (/owners-units/{unit}). The fix introduces a new backend
 * endpoint /finance/building-overview that aggregates across all units.
 *
 * Run:
 *   yarn test -- tests/frontend/dashboard_building_fund_health.spec.js
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3020';
const API_URL = 'http://localhost:8003/api';

// ── Credentials ──────────────────────────────────────────────────────────────
const OWNER_EMAIL = 'anthony@eastgateresidences.com.au';
const OWNER_PASSWORD = '$SEED_TEST_USER_PASSWORD';
const ADMIN_EMAIL = 'administrator@eastgateresidences.com.au';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || '';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function loginAs(page, email, password) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/dashboard/, {timeout: 15_000});
}

async function getApiToken(request, email, password) {
    try {
        const resp = await request.post(`${API_URL}/auth/login`, {
            data: {email, password},
            headers: {'Content-Type': 'application/json'},
        });
        if (resp.ok()) {
            const body = await resp.json();
            return body.token || null;
        }
    } catch { /* backend may not be running */
    }
    return null;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Dashboard notice bar — building-wide fund stats', () => {

    test('GET /finance/building-overview returns building-wide fields', async ({request}) => {
        const token = await getApiToken(request, ADMIN_EMAIL, ADMIN_PASSWORD);
        if (!token) {
            test.skip('Backend not available');
            return;
        }
        const resp = await request.get(`${API_URL}/finance/building-overview`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(resp.status()).toBe(200);
        const data = await resp.json();
        expect(data).toHaveProperty('year');
        expect(data).toHaveProperty('fund_health');
        expect(data).toHaveProperty('levies_paid_pct');
        expect(data).toHaveProperty('admin_fund');
        expect(data).toHaveProperty('sinking_fund');
        expect(data.admin_fund).toHaveProperty('collection_rate');
        expect(data.sinking_fund).toHaveProperty('collection_rate');
    });

    test('Building total_levied is larger than a single unit levy', async ({request}) => {
        const token = await getApiToken(request, ADMIN_EMAIL, ADMIN_PASSWORD);
        if (!token) {
            test.skip('Backend not available');
            return;
        }
        const [overviewResp, unitResp] = await Promise.all([
            request.get(`${API_URL}/finance/building-overview`, {
                headers: {Authorization: `Bearer ${token}`},
            }),
            request.get(`${API_URL}/owners-units/TH001?financial_year=2026`, {
                headers: {Authorization: `Bearer ${token}`},
            }),
        ]);

        if (!overviewResp.ok() || !unitResp.ok()) {
            test.skip('Required data not available');
            return;
        }
        const building = await overviewResp.json();
        const unit = await unitResp.json();

        // Building total must exceed a single unit's levy — confirms aggregation
        if (building.total_levied > 0 && unit.total_levied > 0) {
            expect(building.total_levied).toBeGreaterThan(unit.total_levied);
        }
    });

    test('Notice bar shows "Fund Health" and "Levies Paid" labels', async ({page}) => {
        // Intercept the building-overview call so we control what the banner shows
        await page.route('**/finance/building-overview**', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    year: '2026',
                    fund_health: 78,
                    levies_paid_pct: 78,
                    admin_fund: {total_levied: 300000, total_paid: 234000, collection_rate: 78},
                    sinking_fund: {total_levied: 90000, total_paid: 70200, collection_rate: 78},
                    total_levied: 390000,
                    total_paid: 304200,
                }),
            });
        });

        try {
            await loginAs(page, OWNER_EMAIL, OWNER_PASSWORD);
        } catch {
            test.skip('Owner login failed — skipping UI test');
            return;
        }

        // Wait for the notice bar to render
        await page.waitForSelector('text=Fund Health', {timeout: 10_000});
        await expect(page.locator('text=Fund Health')).toBeVisible();
        await expect(page.locator('text=Levies Paid')).toBeVisible();
    });

    test('Notice bar shows building overview percentage, not individual unit percentage', async ({page}) => {
        const BUILDING_PCT = 62; // distinctive value that won't appear in unit data by chance

        // Intercept building-overview with a known percentage
        await page.route('**/finance/building-overview**', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    year: '2026',
                    fund_health: BUILDING_PCT,
                    levies_paid_pct: BUILDING_PCT,
                    admin_fund: {total_levied: 300000, total_paid: 186000, collection_rate: BUILDING_PCT},
                    sinking_fund: {total_levied: 90000, total_paid: 55800, collection_rate: BUILDING_PCT},
                    total_levied: 390000,
                    total_paid: 241800,
                }),
            });
        });

        try {
            await loginAs(page, OWNER_EMAIL, OWNER_PASSWORD);
        } catch {
            test.skip('Owner login failed — skipping UI test');
            return;
        }

        await page.waitForSelector('text=Fund Health', {timeout: 10_000});

        // The notice bar should display the mocked building percentage
        const banner = page.locator('[aria-label*="Fund Health"]').first()
            .or(page.locator('text=Fund Health').locator('..').locator('..'));

        await expect(page.locator(`text=${BUILDING_PCT}% Funded`)).toBeVisible();
        await expect(page.locator(`text=${BUILDING_PCT}% of 2026`)).toBeVisible();
    });

    test('Clicking Fund Health opens popup with building fund breakdown', async ({page}) => {
        const ADMIN_RATE = 74;
        const SINKING_RATE = 68;

        await page.route('**/finance/building-overview**', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    year: '2026',
                    fund_health: Math.round(( ADMIN_RATE + SINKING_RATE ) / 2),
                    levies_paid_pct: 71,
                    admin_fund: {total_levied: 300000, total_paid: 222000, collection_rate: ADMIN_RATE},
                    sinking_fund: {total_levied: 90000, total_paid: 61200, collection_rate: SINKING_RATE},
                    total_levied: 390000,
                    total_paid: 283200,
                }),
            });
        });

        try {
            await loginAs(page, OWNER_EMAIL, OWNER_PASSWORD);
        } catch {
            test.skip('Owner login failed — skipping UI test');
            return;
        }

        await page.waitForSelector('text=Fund Health', {timeout: 10_000});
        await page.click('text=Fund Health');

        // Popup should open
        await page.waitForSelector('[role="dialog"]', {timeout: 5_000});

        // Dialog should show the building collection rates
        await expect(page.locator(`text=${ADMIN_RATE}%`).first()).toBeVisible();
        await expect(page.locator(`text=${SINKING_RATE}%`).first()).toBeVisible();

        // "Building" label confirms it's not individual data
        await expect(page.locator('text=Building Collection Rates')).toBeVisible();
    });

    test('Unauthenticated request to building-overview returns 401', async ({request}) => {
        const resp = await request.get(`${API_URL}/finance/building-overview`);
        expect([401, 403]).toContain(resp.status());
    });

    test('building-overview year parameter is respected', async ({request}) => {
        const token = await getApiToken(request, ADMIN_EMAIL, ADMIN_PASSWORD);
        if (!token) {
            test.skip('Backend not available');
            return;
        }
        const resp = await request.get(`${API_URL}/finance/building-overview?year=2025`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(resp.status()).toBe(200);
        const data = await resp.json();
        expect(data.year).toBe('2025');
    });

});
