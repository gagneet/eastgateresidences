// @ts-check
const {test, expect} = require('@playwright/test');

/**
 * Financial Data Handling Tests
 * Tests graceful handling of missing financial data (404 errors)
 */

test.describe('Financial Data Handling', () => {
    test.beforeEach(async ({page}) => {
        // Login as admin
        await page.goto('/login');
        await page.fill('input[type="email"]', 'admin@eastgate.com');
        await page.fill('input[type="password"]', '$SEED_TEST_USER_PASSWORD');
        await page.click('button[type="submit"]');
        await page.waitForURL('/dashboard');
    });

    test('Should display financial data when available', async ({page, request}) => {
        // Mock successful financial data response
        await page.route('**/api/owners-units/*', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    unit_number: 'UA001',
                    admin_fund: {
                        closing_balance: 1500.00
                    },
                    sinking_fund: {
                        closing_balance: 2500.00
                    },
                    total_levied: 4000.00,
                    total_paid: 4000.00,
                    net_balance: 0,
                    status: 'paid_up'
                })
            });
        });

        await page.goto('/dashboard');
        await page.waitForSelector('[data-testid="owner-dashboard"]', {timeout: 10000});

        // Should show financial summary
        const financialSummary = await page.locator('text=Financial Summary').first();
        await expect(financialSummary).toBeVisible();

        // Should show "Paid Up" or similar status
        const paidUpBadge = await page.locator('text=Paid Up').first();
        await expect(paidUpBadge).toBeVisible({timeout: 5000});
    });

    test('Should gracefully handle missing financial data (404)', async ({page}) => {
        // Mock 404 response for financial data
        await page.route('**/api/owners-units/*', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({
                    detail: 'Owner/unit not found'
                })
            });
        });

        await page.goto('/dashboard');
        await page.waitForSelector('[data-testid="owner-dashboard"]', {timeout: 10000});

        // Should still show financial summary card
        const financialSummary = await page.locator('text=Financial Summary').first();
        await expect(financialSummary).toBeVisible();

        // Should show user-friendly message
        const noDataMessage = await page.locator('text=No financial records found');
        await expect(noDataMessage).toBeVisible({timeout: 5000});

        // Should show explanation
        const explanation = await page.locator('text=Financial data will be available once your first levy period is recorded');
        await expect(explanation).toBeVisible();

        // Should not show "Loading..." indefinitely
        const loadingText = await page.locator('text=Loading financial data...');
        await expect(loadingText).not.toBeVisible();
    });

    test('Should handle server errors gracefully', async ({page}) => {
        // Mock 500 server error
        await page.route('**/api/owners-units/*', async route => {
            await route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({
                    detail: 'Internal server error'
                })
            });
        });

        await page.goto('/dashboard');
        await page.waitForSelector('[data-testid="owner-dashboard"]', {timeout: 10000});

        // Should show error state
        const financialSummary = await page.locator('text=Financial Summary').first();
        await expect(financialSummary).toBeVisible();

        // Should show error message
        const errorMessage = await page.locator('text=There was a problem loading your financial data');
        await expect(errorMessage).toBeVisible({timeout: 5000});

        // Should show instructions
        const instructions = await page.locator('text=Please try refreshing the page');
        await expect(instructions).toBeVisible();
    });

    test('Fund Balance Donut should handle missing data', async ({page}) => {
        // Mock 404 response
        await page.route('**/api/owners-units/*', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({detail: 'Not found'})
            });
        });

        await page.goto('/dashboard');
        await page.waitForSelector('[data-testid="owner-dashboard"]', {timeout: 10000});

        // Fund Balance Donut should show appropriate message or be hidden
        const fundBalance = await page.locator('text=My Fund Distribution').first();
        if (await fundBalance.isVisible()) {
            // If visible, should show "No data available"
            const noData = await page.locator('text=No fund data available yet');
            await expect(noData).toBeVisible();
        }
    });

    test('Should not show payment button when no financial data', async ({page}) => {
        // Mock 404 response
        await page.route('**/api/owners-units/*', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({detail: 'Not found'})
            });
        });

        await page.goto('/dashboard');
        await page.waitForSelector('[data-testid="owner-dashboard"]', {timeout: 10000});

        // Pay Now button should not be visible
        const payButton = await page.locator('button:has-text("Pay Now")').first();
        await expect(payButton).not.toBeVisible();
    });
});

test.describe('Dashboard Navigation', () => {
    test.beforeEach(async ({page}) => {
        // Login
        await page.goto('/login');
        await page.fill('input[type="email"]', 'admin@eastgate.com');
        await page.fill('input[type="password"]', '$SEED_TEST_USER_PASSWORD');
        await page.click('button[type="submit"]');
        await page.waitForURL('/dashboard');
    });

    test('Should navigate to all dashboard pages without errors', async ({page}) => {
        const pages = [
            '/documents',
            '/community/chat',
            '/governance/meetings',
            '/community/marketplace',
            '/community/notices',
            '/governance/todos',
            '/maintenance',
        ];

        for (const pagePath of pages) {
            await page.goto(pagePath);
            await page.waitForLoadState('networkidle');

            // Wait a bit for any API calls
            await page.waitForTimeout(1000);

            // Check page loaded (should have some content)
            const body = await page.locator('body');
            await expect(body).toBeVisible();

            console.log(`✓ ${pagePath} loaded successfully`);
        }
    });
});
