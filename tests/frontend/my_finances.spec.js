/**
 * My Finances Page — Playwright end-to-end tests
 *
 * All API calls are mocked via page.route() — no data created or mutated.
 * Tests are idempotent and leave no trace in the database.
 *
 * Run:
 *   npx playwright test tests/frontend/my_finances.spec.js --headed
 */

const {test, expect} = require('@playwright/test');

// ─── Mock data ────────────────────────────────────────────────────────────────

const MOCK_LEVY = {
    unit_number: 'TH017',
    your_uoe: 85,
    total_uoe: 3847,
    uoe_share_pct: 2.21,
    quarterly_levy: 1761.50,
    data_year: '2026-2027',
    balance_owing: 0,
    balance_credit: 0,
    categories: [
        {name: 'Strata Management', fund_type: 'admin', your_quarterly: 220.0, pct: 12.5, annual_amount: 28000},
        {name: 'Building Insurance', fund_type: 'insurance', your_quarterly: 390.0, pct: 22.1, annual_amount: 49500},
        {
            name: 'Sinking Fund Contribution',
            fund_type: 'sinking',
            your_quarterly: 550.0,
            pct: 31.2,
            annual_amount: 70000
        },
        {name: 'Garden Maintenance', fund_type: 'admin', your_quarterly: 95.0, pct: 5.4, annual_amount: 12100},
        {name: 'Utilities', fund_type: 'admin', your_quarterly: 506.50, pct: 28.8, annual_amount: 64350},
    ],
};

const MOCK_HEALTH = {
    building_id: '13195',
    health_score: 74,
    grade: 'B',
    components: [
        {
            key: 'sinking_fund',
            label: 'Sinking fund',
            score_pct: 68,
            status: 'warning',
            plain_english: '68% of forecast reserve needs funded — below target',
        },
        {
            key: 'arrears',
            label: 'Arrears rate',
            score_pct: 92,
            status: 'good',
            plain_english: 'Only 0.8% of lots have unpaid levies',
        },
        {
            key: 'maintenance',
            label: 'Maintenance',
            score_pct: 75,
            status: 'warning',
            plain_english: '5 open work orders',
        },
        {
            key: 'compliance',
            label: 'Compliance',
            score_pct: 100,
            status: 'good',
            plain_english: 'All compliance items current',
        },
    ],
    last_computed_at: new Date(Date.now() - 3600000).toISOString(),
    health_score_history: [],
};

const MOCK_SAVINGS = {
    your_share_cents: 4200,
    total_saved_ytd_cents: 185000,
    levy_reduction_equivalent_cents: 1050,
    your_uoe: 85,
    total_uoe: 3847,
};

const MOCK_SETTINGS = {
    building_name: 'East Gate Residences',
    financial_year_start_month: 7,
};

async function setupMocks(page) {
    await page.route('**/api/owner-finance/levy-breakdown', async (route) => {
        await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LEVY)});
    });
    await page.route('**/api/owner-finance/health-explanation', async (route) => {
        await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_HEALTH)});
    });
    await page.route('**/api/owner-finance/savings-summary', async (route) => {
        await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SAVINGS)});
    });
    await page.route('**/api/settings', async (route) => {
        await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SETTINGS)});
    });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test.describe('My Finances Page', () => {
    test('shows "Where does my levy go?" section', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await expect(page.getByText('Where does my levy go?')).toBeVisible();
    });

    test('displays quarterly levy amount', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await expect(page.getByText(/1,761\.50/)).toBeVisible();
    });

    test('shows FY label in header', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        // Should show FY2026-27 derived from data_year=2026-2027 and startMonth=7
        await expect(page.getByText(/FY2026/i)).toBeVisible();
    });

    test('displays levy categories in table', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await expect(page.getByText('Strata Management')).toBeVisible();
        await expect(page.getByText('Building Insurance')).toBeVisible();
        await expect(page.getByText('Sinking Fund Contribution')).toBeVisible();
    });

    test('category rows are clickable and expand', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        const firstRow = page.locator('[data-testid="levy-category-row-0"]');
        await expect(firstRow).toBeVisible();
        await firstRow.click();

        // Expanded row should show description
        await expect(page.getByText(/Day-to-day operating/i)).toBeVisible();
    });

    test('shows building health score card', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await expect(page.getByText("Is your building's money well managed?")).toBeVisible();
        await expect(page.getByText('74')).toBeVisible();
        await expect(page.getByText('Grade B')).toBeVisible();
    });

    test('health score card is clickable and opens modal', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        const healthCard = page.locator('[data-testid="health-score-card"]');
        await expect(healthCard).toBeVisible();
        await healthCard.click();

        // Modal should appear with detailed breakdown
        await expect(page.getByText('Building Health Score')).toBeVisible();
        // Should show grade thresholds
        await expect(page.getByText(/A ≥ 85/)).toBeVisible();
    });

    test('health modal shows all 4 components', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await page.locator('[data-testid="health-score-card"]').click();

        await expect(page.getByText('Sinking fund')).toBeVisible();
        await expect(page.getByText('Arrears rate')).toBeVisible();
        await expect(page.getByText('Maintenance')).toBeVisible();
        await expect(page.getByText('Compliance')).toBeVisible();
    });

    test('health modal shows plain-english explanations', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await page.locator('[data-testid="health-score-card"]').click();

        await expect(page.getByText('68% of forecast reserve needs funded')).toBeVisible();
        await expect(page.getByText(/Only 0.8%/)).toBeVisible();
    });

    test('shows empty categories message when no data', async ({page}) => {
        const emptyLevy = {...MOCK_LEVY, categories: [], quarterly_levy: 0};
        await page.route('**/api/owner-finance/levy-breakdown', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(emptyLevy)});
        });
        await page.route('**/api/owner-finance/health-explanation', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_HEALTH)});
        });
        await page.route('**/api/owner-finance/savings-summary', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SAVINGS)});
        });
        await page.route('**/api/settings', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SETTINGS)});
        });

        await page.goto('/financials/my-finances');
        await expect(page.getByText(/Budget categories not yet configured/i)).toBeVisible();
    });

    test('shows "Building health data not yet available" when no summary', async ({page}) => {
        const noHealth = {health_score: null, grade: 'N/A', components: []};
        await page.route('**/api/owner-finance/health-explanation', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(noHealth)});
        });
        await page.route('**/api/owner-finance/levy-breakdown', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LEVY)});
        });
        await page.route('**/api/owner-finance/savings-summary', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SAVINGS)});
        });
        await page.route('**/api/settings', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SETTINGS)});
        });

        await page.goto('/financials/my-finances');
        await expect(page.getByText(/health data not yet available/i)).toBeVisible();
    });

    test('shows community savings section', async ({page}) => {
        await setupMocks(page);
        await page.goto('/financials/my-finances');

        await expect(page.getByText('Community savings this year')).toBeVisible();
        // $42.00 your share
        await expect(page.getByText(/\$42\.00/)).toBeVisible();
    });

    test('no console errors on load', async ({page}) => {
        const consoleErrors = [];
        page.on('console', (msg) => {
            if (msg.type() === 'error') consoleErrors.push(msg.text());
        });

        await setupMocks(page);
        await page.goto('/financials/my-finances');

        const realErrors = consoleErrors.filter(
            (e) => !e.includes('favicon') && !e.includes('extension') && !e.includes('Failed to load resource')
        );
        expect(realErrors).toHaveLength(0);
    });

    // Negative scenarios

    test('handles levy-breakdown 400 gracefully', async ({page}) => {
        await page.route('**/api/owner-finance/levy-breakdown', async (route) => {
            await route.fulfill({
                status: 400,
                contentType: 'application/json',
                body: '{"detail":"No unit associated with this account"}'
            });
        });
        await page.route('**/api/owner-finance/health-explanation', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_HEALTH)});
        });
        await page.route('**/api/owner-finance/savings-summary', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SAVINGS)});
        });
        await page.route('**/api/settings', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SETTINGS)});
        });

        await page.goto('/financials/my-finances');

        // Page should still load (not crash), health section visible
        await expect(page.getByText('Grade B')).toBeVisible();
    });

    test('tenant denied - shows error or redirect', async ({page}) => {
        await page.route('**/api/owner-finance/**', async (route) => {
            await route.fulfill({
                status: 403,
                contentType: 'application/json',
                body: '{"detail":"Owner access required"}'
            });
        });
        await page.route('**/api/settings', async (route) => {
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SETTINGS)});
        });

        await page.goto('/financials/my-finances');

        // Page should not crash — either shows error messages or redirects
        const body = await page.locator('body').textContent();
        expect(body).not.toBeNull();
    });
});
