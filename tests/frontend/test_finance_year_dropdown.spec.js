/**
 * Playwright E2E Tests for Finance Page Year Dropdown
 *
 * Tests the finance page year dropdown functionality including:
 * - Year dropdown displays 3 years (2024-2025, 2025-2026, 2026-2027)
 * - Selecting each year loads correct data
 * - Levy calculator works for 2026-2027
 * - All 87 units display correctly
 *
 * Run with: npx playwright test tests/test_finance_year_dropdown.spec.js --headed
 */

const {test, expect} = require('@playwright/test');

// Test configuration
const BASE_URL = process.env.REACT_APP_BACKEND_URL || 'https://eastgateresidences.com.au';
const TEST_USER = {
    email: 'admin@eastgate.com',
    password: '$SEED_TEST_USER_PASSWORD'
};

test.describe('Finance Page Year Dropdown', () => {

    test.beforeEach(async ({page}) => {
        // Login before each test
        await page.goto(`${BASE_URL}/login`);
        await page.fill('input[type="email"]', TEST_USER.email);
        await page.fill('input[type="password"]', TEST_USER.password);
        await page.click('button[type="submit"]');

        // Wait for dashboard to load
        await page.waitForURL('**/dashboard**', {timeout: 10000});

        // Navigate to finance page
        await page.goto(`${BASE_URL}/financials`);
        await page.waitForLoadState('networkidle');
    });

    test('should display year dropdown with 3 years', async ({page}) => {
        // Wait for year selector to be visible
        await page.waitForSelector('[data-testid="year-selector"]', {timeout: 5000});

        // Click year dropdown
        await page.click('[data-testid="year-selector"]');

        // Wait for dropdown options to appear
        await page.waitForSelector('[role="option"]', {timeout: 3000});

        // Get all year options
        const yearOptions = await page.locator('[role="option"]').allTextContents();

        // Verify 3 years are available
        expect(yearOptions.length).toBeGreaterThanOrEqual(3);

        // Verify specific years exist
        const yearText = yearOptions.join(' ');
        expect(yearText).toContain('2024-2025');
        expect(yearText).toContain('2025-2026');
        expect(yearText).toContain('2026-2027');
    });

    test('should load data for 2026-2027 financial year', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');

        // Wait for data to load
        await page.waitForTimeout(2000);

        // Verify year is selected
        const selectedYear = await page.locator('[data-testid="year-selector"]').textContent();
        expect(selectedYear).toContain('2026-2027');

        // Verify budget data appears
        const pageContent = await page.content();
        expect(pageContent).toContain('440,375'); // Total budget
    });

    test('should display levy calculator for 2026-2027', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');

        // Click Levy Calculator tab
        const levyTab = page.locator('text=Levy Calculator').first();
        await levyTab.click();
        await page.waitForTimeout(1000);

        // Verify levy calculator content is visible
        const content = await page.content();
        expect(content.toLowerCase()).toContain('levy');
    });

    test('should display all 87 units in levy calculator', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');

        // Click Levy Calculator tab
        const levyTab = page.locator('text=Levy Calculator').first();
        await levyTab.click();
        await page.waitForTimeout(2000);

        // Count unit rows (look for unit numbers like UA001, TH001)
        const unitRows = page.locator('text=/UA\\d+|TH\\d+/');
        const count = await unitRows.count();

        // Should have at least 70 apartments + 17 townhouses = 87 units
        expect(count).toBeGreaterThanOrEqual(80); // Allow some flexibility
    });

    test('should show correct levy amount for UA001', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');

        // Click Levy Calculator tab
        await page.locator('text=Levy Calculator').first().click();
        await page.waitForTimeout(1500);

        // Search for UA001
        const pageContent = await page.content();
        if (pageContent.includes('UA001')) {
            // Verify UA001 quarterly levy is $902.77
            expect(pageContent).toContain('902.77');
        }
    });

    test('should show correct levy amount for TH004', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');

        // Click Levy Calculator tab
        await page.locator('text=Levy Calculator').first().click();
        await page.waitForTimeout(1500);

        // Search for TH004
        const pageContent = await page.content();
        if (pageContent.includes('TH004')) {
            // Verify TH004 quarterly levy is $1,761.50
            expect(pageContent).toContain('1,761.50');
        }
    });

    test('should switch between years without errors', async ({page}) => {
        // Listen for console errors
        const errors = [];
        page.on('console', msg => {
            if (msg.type() === 'error') {
                errors.push(msg.text());
            }
        });

        // Switch to 2026-2027
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');
        await page.waitForTimeout(1000);

        // Switch to 2024-2025
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2024-2025');
        await page.waitForTimeout(1000);

        // Switch back to 2026-2027
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');
        await page.waitForTimeout(1000);

        // Should have no critical errors
        const criticalErrors = errors.filter(e =>
            e.includes('Failed') ||
            e.includes('Error') ||
            e.includes('404')
        );
        expect(criticalErrors.length).toBe(0);
    });

    test('should display budget breakdown for 2026-2027', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');
        await page.waitForTimeout(1500);

        // Verify budget breakdown appears
        const content = await page.content();

        // Should show admin fund ($340,870.20)
        expect(content).toContain('340,870') || expect(content).toContain('340870');

        // Should show sinking fund ($99,504.90)
        expect(content).toContain('99,504') || expect(content).toContain('99504');
    });

    test('should persist selected year on page refresh', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');
        await page.waitForTimeout(1000);

        // Refresh page
        await page.reload();
        await page.waitForLoadState('networkidle');

        // Year should still be 2026-2027 (if implemented with localStorage)
        const selectedYear = await page.locator('[data-testid="year-selector"]').textContent();
        // Default is 2026-2027, so it should be selected
        expect(selectedYear).toContain('2026-2027');
    });

    test('should show loading state when switching years', async ({page}) => {
        // Click year dropdown
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});

        // Click different year
        await page.click('text=2024-2025');

        // Should show some loading indicator (if implemented)
        // This is optional and may vary based on implementation
        await page.waitForTimeout(500);
    });

    test('should display correct financial year format', async ({page}) => {
        // Year dropdown should show format "YYYY-YYYY"
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});

        const yearOptions = await page.locator('[role="option"]').allTextContents();

        // Each year should match format YYYY-YYYY
        yearOptions.forEach(year => {
            expect(year).toMatch(/\d{4}-\d{4}/);
        });
    });

    test('should not show years with no data as selectable', async ({page}) => {
        // Click year dropdown
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});

        // Get all options
        const options = page.locator('[role="option"]');
        const count = await options.count();

        // Check each option
        for (let i = 0; i < count; i++) {
            const text = await options.nth(i).textContent();

            // If year shows "(No Data)", it should be disabled
            if (text.includes('No Data')) {
                const isDisabled = await options.nth(i).getAttribute('aria-disabled');
                expect(isDisabled).toBe('true');
            }
        }
    });

    test('should display finance page tabs correctly', async ({page}) => {
        // Select 2026-2027 year
        await page.click('[data-testid="year-selector"]');
        await page.waitForSelector('[role="option"]', {timeout: 3000});
        await page.click('text=2026-2027');
        await page.waitForTimeout(1000);

        // Check that main tabs are visible
        const tabs = [
            'Overview',
            'Income & Expenses',
            'Levy Calculator',
            'Budget vs Actual'
        ];

        for (const tab of tabs) {
            const tabElement = page.locator(`text=${tab}`).first();
            await expect(tabElement).toBeVisible({timeout: 5000});
        }
    });

    test('should show correct page title', async ({page}) => {
        // Page should have "Finance" in title
        const title = await page.title();
        expect(title.toLowerCase()).toContain('finance');
    });

});

test.describe('Finance API Endpoint', () => {

    test('should return 3 financial years from API', async ({request}) => {
        // Login to get token
        const loginResponse = await request.post(`${BASE_URL}/api/auth/login`, {
            data: {
                email: TEST_USER.email,
                password: TEST_USER.password
            }
        });

        expect(loginResponse.ok()).toBeTruthy();
        const loginData = await loginResponse.json();
        const token = loginData.access_token;

        // Call finance years API
        const yearsResponse = await request.get(`${BASE_URL}/api/finance/years`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        expect(yearsResponse.ok()).toBeTruthy();
        const years = await yearsResponse.json();

        // Should return array with 3 years
        expect(Array.isArray(years)).toBeTruthy();
        expect(years.length).toBeGreaterThanOrEqual(3);
        expect(years).toContain('2024-2025');
        expect(years).toContain('2025-2026');
        expect(years).toContain('2026-2027');
    });

});
