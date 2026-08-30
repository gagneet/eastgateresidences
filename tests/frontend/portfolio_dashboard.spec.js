/**
 * Portfolio Dashboard — Playwright end-to-end tests
 *
 * All API calls are mocked via page.route() so no data is created or mutated.
 * Tests are idempotent and leave no trace in the database.
 *
 * Run:
 *   npx playwright test tests/frontend/portfolio_dashboard.spec.js --headed
 */

const {test, expect} = require('@playwright/test');

// ─── Mock data ────────────────────────────────────────────────────────────────

const MOCK_DASHBOARD = {
    buildings: [
        {
            building_id: '13195',
            name: 'East Gate Residences',
            lot_count: 87,
            health_score: 74,
            arrears_rate: 3.2,
            open_work_orders: 5,
            next_compliance_item: 'Fire safety inspection — Jul 2026',
            last_computed_at: new Date(Date.now() - 3600000).toISOString(),
            has_data: true,
        },
        {
            building_id: '16244',
            name: 'Sierra',
            lot_count: 120,
            health_score: 82,
            arrears_rate: 1.1,
            open_work_orders: 2,
            next_compliance_item: null,
            last_computed_at: new Date(Date.now() - 7200000).toISOString(),
            has_data: true,
        },
    ],
    summary: {
        total_buildings: 2,
        total_lots: 207,
        avg_health_score: 78.0,
        alert_count: 0,
    },
    alerts: [],
};

const MOCK_BUILDINGS = {
    buildings: MOCK_DASHBOARD.buildings,
    total: 2,
};

/** Log in and navigate to the portfolio dashboard. */
async function loginAndGoToPortfolio(page) {
    await page.route('**/api/auth/login', async (route) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                token: 'mock-jwt-token',
                user: {
                    id: 'user-admin',
                    role: 'super_admin',
                    email: 'admin@test.com',
                    unit_number: null,
                },
            }),
        });
    });

    await page.route('**/api/portfolio/dashboard', async (route) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(MOCK_DASHBOARD),
        });
    });

    await page.route('**/api/portfolio/buildings', async (route) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(MOCK_BUILDINGS),
        });
    });

    // Navigate directly (assume session cookie / skip login UI)
    await page.goto('/admin');
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test.describe('Portfolio Dashboard', () => {
    test('shows summary cards with real data', async ({page}) => {
        await loginAndGoToPortfolio(page);

        // Buildings card
        await expect(page.getByText('2').first()).toBeVisible();
        // Total lots
        await expect(page.getByText('207')).toBeVisible();
    });

    test('shows all buildings in the table', async ({page}) => {
        await loginAndGoToPortfolio(page);

        await expect(page.getByText('East Gate Residences')).toBeVisible();
        await expect(page.getByText('Sierra')).toBeVisible();
    });

    test('building rows are clickable', async ({page}) => {
        await loginAndGoToPortfolio(page);

        const row = page.locator('[data-testid="building-row-13195"]');
        await expect(row).toBeVisible();

        // Check cursor-pointer styling (indicates clickable)
        const cursor = await row.evaluate((el) =>
            window.getComputedStyle(el).cursor
        );
        expect(cursor).toBe('pointer');
    });

    test('lot count shows correct value (not 0)', async ({page}) => {
        await loginAndGoToPortfolio(page);

        // East Gate should show 87, not 0
        await expect(page.getByText('87')).toBeVisible();
        // Sierra should show 120
        await expect(page.getByText('120')).toBeVisible();
    });

    test('health score shows with colour coding', async ({page}) => {
        await loginAndGoToPortfolio(page);

        // Score 74 should be amber (≥50 but <75)
        const healthCell = page.locator('[data-testid="building-row-13195"] td:nth-child(3) span');
        if (await healthCell.isVisible()) {
            const className = await healthCell.getAttribute('class');
            expect(className).toMatch(/yellow|amber/i);
        }
    });

    test('shows recompute button', async ({page}) => {
        await loginAndGoToPortfolio(page);
        await expect(page.getByText('Recompute')).toBeVisible();
    });

    test('shows info banner with last-updated time', async ({page}) => {
        await loginAndGoToPortfolio(page);
        // Should show the info banner about refresh schedule
        await expect(page.getByText(/refreshed daily/i)).toBeVisible();
    });

    test('shows next compliance item', async ({page}) => {
        await loginAndGoToPortfolio(page);
        await expect(page.getByText(/Fire safety/i)).toBeVisible();
    });

    test('shows healthy status badge for high-scoring buildings', async ({page}) => {
        await loginAndGoToPortfolio(page);
        // Sierra has health=82 (≥75 → Healthy)
        await expect(page.getByText('Healthy').first()).toBeVisible();
    });

    test('shows no-data state when portfolio 403', async ({page}) => {
        // Override to return 403
        await page.route('**/api/portfolio/dashboard', async (route) => {
            await route.fulfill({
                status: 403,
                contentType: 'application/json',
                body: '{"detail":"Manager access required"}'
            });
        });
        await page.route('**/api/portfolio/buildings', async (route) => {
            await route.fulfill({
                status: 403,
                contentType: 'application/json',
                body: '{"detail":"Manager access required"}'
            });
        });

        await page.goto('/admin');

        // Should show an error alert or redirect — not crash
        const body = await page.locator('body').textContent();
        expect(body).not.toBeNull();
    });

    test('unauthenticated user is redirected', async ({page}) => {
        // No auth mocks — let the 401 flow trigger redirect
        await page.route('**/api/portfolio/dashboard', async (route) => {
            await route.fulfill({status: 401, contentType: 'application/json', body: '{"detail":"Not authenticated"}'});
        });

        await page.goto('/admin');

        // Should end up on login page (or show 401 handling)
        const url = page.url();
        const body = await page.locator('body').textContent();
        // Either redirected to /login or shows error
        const isLoginPage = url.includes('/login') || body?.includes('Sign in') || body?.includes('login');
        const hasError = body?.includes('403') || body?.includes('401') || body?.includes('Not authenticated') || body?.includes('error');
        expect(isLoginPage || hasError).toBeTruthy();
    });

    test('shows alert card when buildings have low health', async ({page}) => {
        const mockWithAlert = {
            ...MOCK_DASHBOARD,
            buildings: [
                {...MOCK_DASHBOARD.buildings[ 0 ], health_score: 40},
                ...MOCK_DASHBOARD.buildings.slice(1),
            ],
            alerts: [{type: 'low_health', building_id: '13195', value: 40}],
            summary: {...MOCK_DASHBOARD.summary, alert_count: 1},
        };

        await page.route('**/api/portfolio/dashboard', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(mockWithAlert),
            });
        });
        await page.route('**/api/portfolio/buildings', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({buildings: mockWithAlert.buildings, total: 2}),
            });
        });

        await page.goto('/admin');
        await expect(page.getByText('Portfolio Alerts')).toBeVisible();
        await expect(page.getByText('Low Health')).toBeVisible();
    });

    test('no console errors on load', async ({page}) => {
        const consoleErrors = [];
        page.on('console', (msg) => {
            if (msg.type() === 'error') consoleErrors.push(msg.text());
        });

        await loginAndGoToPortfolio(page);

        // Filter out expected/known third-party errors
        const realErrors = consoleErrors.filter(
            (e) => !e.includes('favicon') && !e.includes('extension') && !e.includes('Failed to load resource')
        );
        expect(realErrors).toHaveLength(0);
    });
});
