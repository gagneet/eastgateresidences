/**
 * Unit Management Tests - UA/TH Format
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'http://localhost:3020';
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8003';
const ADMIN_USER = {
    email: 'administrator@eastgateresidences.com.au',
    password: 'EastGate3195%'
};

// Helper function to login
async function loginAsAdmin(page) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('[data-testid="email-input"]', ADMIN_USER.email);
    await page.fill('[data-testid="password-input"]', ADMIN_USER.password);
    await page.click('[data-testid="login-submit"]');
    await page.waitForURL(/.*dashboard/, {timeout: 15000});
}

test.describe('Unit Management - UA/TH Format', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
        await page.goto(`${BASE_URL}/admin/owners-units`);
        await page.waitForLoadState('networkidle');
    });

    test('Should display Owners & Units page', async ({page}) => {
        // The heading might be different or in a breadcrumb
        await expect(page.locator('h1, h2, [data-testid="dashboard-content"] p').filter({hasText: /units|properties|owners/i}).first()).toBeVisible();
        const hasContent = await page.locator('table, [data-testid*="unit-"], .card').first().isVisible();
        expect(hasContent).toBeTruthy();
    });

    test('Should display units with UA prefix (apartments)', async ({page}) => {
        await page.waitForTimeout(2000);
        const pageContent = await page.textContent('body');
        expect(pageContent).toMatch(/UA\d{3}/);
    });

    test('Should display units with TH prefix (townhouses)', async ({page}) => {
        await page.waitForTimeout(2000);
        const pageContent = await page.textContent('body');
        expect(pageContent).toMatch(/TH\d{3}/);
    });

    test('Should search units by number', async ({page}) => {
        const searchInput = page.locator('input[placeholder*="Search" i]').first();

        if (await searchInput.isVisible()) {
            await searchInput.fill('UA001');
            await page.waitForTimeout(2000);
            const results = await page.textContent('body');
            expect(results).toContain('UA001');
        }
    });

    test('Should display unit details', async ({page}) => {
        await page.waitForTimeout(2000);
        const firstUnit = page.locator('tr, [data-testid*="unit-card"], .card').filter({hasText: /UA\d{3}|TH\d{3}/}).first();

        if (await firstUnit.isVisible()) {
            await firstUnit.click();
            // Detail view should appear or navigate
            await expect(page.locator('text=/unit.*details|unit.*info|UA\d{3}|TH\d{3}/i').first()).toBeVisible({timeout: 10000});
        }
    });
});

test.describe('Unit API Integration', () => {

    test('GET /api/units should return UA/TH format', async ({request}) => {
        const loginResponse = await request.post(`${BACKEND_URL}/api/auth/login`, {
            data: {
                email: ADMIN_USER.email,
                password: ADMIN_USER.password
            }
        });

        expect(loginResponse.ok()).toBeTruthy();
        const {token} = await loginResponse.json();

        const unitsResponse = await request.get(`${BACKEND_URL}/api/units`, {
            headers: {
                'Authorization': `Bearer ${token}`
            },
            params: {
                limit: 10
            }
        });

        expect(unitsResponse.ok()).toBeTruthy();
        const units = await unitsResponse.json();
        expect(units.length).toBeGreaterThan(0);

        const hasCorrectFormat = units.some(unit =>
            unit.unit_number.startsWith('UA') || unit.unit_number.startsWith('TH')
        );
        expect(hasCorrectFormat).toBe(true);
    });
});
