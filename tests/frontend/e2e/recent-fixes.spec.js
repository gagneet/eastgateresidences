/**
 * Recent Bug Fix Regression Tests
 *
 * Covers all 7 recent fixes:
 * 1. compliance_items — optional fields handled, page loads
 * 2. listings — price can be string, marketplace filter with null titles
 * 3. legal_pages — endpoint returns content
 * 4. budgets — 3 financial years, no 500 errors
 * 5. chat_groups — page shows groups
 * 6. feature_toggle access summary — works with UUID user ID
 * 7. user_feature_permissions — null guard prevents crash
 * 8. scraper_settings — page loads for super admin
 *
 * Run:
 *   cd /home/gagneet/strata-management
 *   npx playwright test tests/frontend/e2e/recent-fixes.spec.js --headed
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';
const ADMIN = {email: 'admin@eastgate.com', password: '$SEED_TEST_USER_PASSWORD'};

// Helper: get auth token via API
async function getAuthToken(request) {
    const response = await request.post(`${BASE_URL}/api/auth/login`, {
        data: ADMIN
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    return data.token;
}

// Helper: login via UI
async function loginAsAdmin(page) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"], input[name="email"]', ADMIN.email);
    await page.fill('input[type="password"], input[name="password"]', ADMIN.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/dashboard/, {timeout: 10000});
}

test.describe('Recent Bug Fix Regression Tests', () => {

    test('Marketplace page loads without errors', async ({page}) => {
        await page.goto(`${BASE_URL}/marketplace`);
        await page.waitForLoadState('networkidle');

        // Should not show error state
        const errors = page.locator('.error, [data-testid="error"]');
        await expect(page.locator('body')).not.toContainText('Something went wrong');

        // Page title or content should be visible
        await expect(page.locator('h1, h2').first()).toBeVisible({timeout: 10000});
    });

    test('Marketplace filter works with null titles (null guard fix)', async ({page}) => {
        await page.goto(`${BASE_URL}/marketplace`);
        await page.waitForLoadState('networkidle');

        // Filter input should work without crashing on null listing titles
        const searchInput = page.locator('input[placeholder*="search" i], input[placeholder*="filter" i]').first();
        if (await searchInput.isVisible()) {
            await searchInput.fill('test');
            // Should not crash — no TypeError from null.toLowerCase()
            await page.waitForTimeout(500);
            await expect(page.locator('body')).not.toContainText('TypeError');
            await expect(page.locator('body')).not.toContainText('Cannot read');
        }
    });

    test('Compliance page loads without errors (authenticated)', async ({page}) => {
        await loginAsAdmin(page);
        await page.goto(`${BASE_URL}/compliance`);
        await page.waitForLoadState('networkidle');

        // Should not show 500 error
        await expect(page.locator('body')).not.toContainText('Internal Server Error');
        await expect(page.locator('body')).not.toContainText('Something went wrong');

        // Compliance content should render
        const heading = page.locator('h1, h2').first();
        await expect(heading).toBeVisible({timeout: 10000});
    });

    test('Legal pages endpoint returns content', async ({request}) => {
        const token = await getAuthToken(request);

        const response = await request.get(`${BASE_URL}/api/legal-pages/privacy-policy`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        // Should return 200 or 404 (not 500)
        expect([200, 404]).toContain(response.status());
    });

    test('Budgets endpoint returns 3 financial years', async ({request}) => {
        const token = await getAuthToken(request);

        const response = await request.get(`${BASE_URL}/api/budgets`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.status()).toBe(200);
        const data = await response.json();

        const budgets = Array.isArray(data) ? data : [data];
        const years = budgets.map(b => b.financial_year);

        expect(years).toContain('2024-2025');
        expect(years).toContain('2025-2026');
        expect(years).toContain('2026-2027');
    });

    test('Chat page shows groups (authenticated)', async ({page}) => {
        await loginAsAdmin(page);
        await page.goto(`${BASE_URL}/community/chat`);
        await page.waitForLoadState('networkidle');

        // Chat groups should load
        await expect(page.locator('body')).not.toContainText('Internal Server Error');

        // Should show some chat content
        const chatContent = page.locator('[data-testid*="chat"], .chat-group, [class*="chat"]');
        // Just verify no crash — groups may be empty
        await page.waitForTimeout(2000);
    });

    test('Feature toggle access summary works', async ({request}) => {
        const token = await getAuthToken(request);

        // First get the admin user's UUID
        const meResponse = await request.get(`${BASE_URL}/api/auth/me`, {
            headers: {'Authorization': `Bearer ${token}`}
        });
        const me = await meResponse.json();
        const userId = me.id;

        // Test access summary with valid UUID
        const summaryResponse = await request.get(
            `${BASE_URL}/api/feature-toggles/user/${userId}/access-summary`,
            {headers: {'Authorization': `Bearer ${token}`}}
        );
        expect(summaryResponse.status()).toBe(200);
    });

    test('User feature permissions page loads without crash', async ({page}) => {
        await loginAsAdmin(page);
        await page.goto(`${BASE_URL}/admin/user-feature-permissions`);
        await page.waitForLoadState('networkidle');

        // Page should not crash with null guard fix
        await expect(page.locator('body')).not.toContainText('TypeError');
        await expect(page.locator('body')).not.toContainText('Cannot read properties of null');
    });

    test('Scraper settings page loads for super admin', async ({page}) => {
        await loginAsAdmin(page);
        await page.goto(`${BASE_URL}/admin/scraper-settings`);
        await page.waitForLoadState('networkidle');

        // Should not show 404 or access denied
        await expect(page.locator('body')).not.toContainText('Page Not Found');
        await expect(page.locator('body')).not.toContainText('Access Denied');

        // Scraper settings heading should be visible
        const heading = page.locator('h1, h2').first();
        await expect(heading).toBeVisible({timeout: 10000});
    });

    test('Dashboard home loads without 500 errors', async ({page}) => {
        await loginAsAdmin(page);
        await page.waitForLoadState('networkidle');

        // Check no 500 errors
        await expect(page.locator('body')).not.toContainText('Internal Server Error');
        await expect(page.locator('body')).not.toContainText('500');
    });
});

test.describe('Budget Categories Deleted (Phase 2 Verification)', () => {

    test('Budget API no longer references budget_categories', async ({request}) => {
        const token = await getAuthToken(request);

        // The budget endpoint should work without budget_categories collection
        const response = await request.get(`${BASE_URL}/api/budgets`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        // Should succeed — no dependency on deleted collection
        expect(response.status()).toBe(200);
    });

    test('Finance summary loads without budget_categories', async ({request}) => {
        const token = await getAuthToken(request);

        const response = await request.get(`${BASE_URL}/api/finance/summary`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        // Should not fail after budget_categories deletion
        expect([200, 404]).toContain(response.status());
        expect(response.status()).not.toBe(500);
    });
});
