// @ts-check
const {test, expect} = require('@playwright/test');

/**
 * Console Error Detection Tests
 * Ensures pages load without console errors
 */

test.describe('Console Errors Detection', () => {
    const consoleErrors = [];
    const consoleWarnings = [];

    test.beforeEach(async ({page}) => {
        // Clear arrays before each test
        consoleErrors.length = 0;
        consoleWarnings.length = 0;

        // Listen for console messages
        page.on('console', msg => {
            const type = msg.type();
            const text = msg.text();

            // Filter out expected/acceptable messages
            const acceptablePatterns = [
                /Partitioned cookie or storage access/i,
                /DuckDuckGo Privacy Essentials/i,
                /Cloudflare/i,
                /stripe\.com/i,
                /web-client-content-script/i, // Browser extension
                /background\.js/i, // Browser extension
                /installHook\.js/i, // React DevTools
            ];

            const isAcceptable = acceptablePatterns.some(pattern => pattern.test(text));

            if (type === 'error' && !isAcceptable) {
                consoleErrors.push(text);
            } else if (type === 'warning' && !isAcceptable) {
                consoleWarnings.push(text);
            }
        });

        // Listen for page errors
        page.on('pageerror', error => {
            consoleErrors.push(`Page Error: ${error.message}`);
        });
    });

    test('Homepage should load without console errors', async ({page}) => {
        await page.goto('/');
        await page.waitForLoadState('networkidle');

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });

    test('Login page should load without console errors', async ({page}) => {
        await page.goto('/login');
        await page.waitForLoadState('networkidle');

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });

    test('About page should load without console errors', async ({page}) => {
        await page.goto('/about');
        await page.waitForLoadState('networkidle');

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });

    test('Blog page should load without console errors', async ({page}) => {
        await page.goto('/blog');
        await page.waitForLoadState('networkidle');

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });

    test('Marketplace page should load without console errors', async ({page}) => {
        await page.goto('/marketplace');
        await page.waitForLoadState('networkidle');

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });
});

test.describe('Authenticated Pages Console Errors', () => {
    const consoleErrors = [];

    test.beforeEach(async ({page}) => {
        consoleErrors.length = 0;

        page.on('console', msg => {
            const type = msg.type();
            const text = msg.text();

            // Filter out expected/acceptable messages
            const acceptablePatterns = [
                /Partitioned cookie or storage access/i,
                /DuckDuckGo Privacy Essentials/i,
                /Cloudflare/i,
                /stripe\.com/i,
                /web-client-content-script/i,
                /background\.js/i,
                /installHook\.js/i,
            ];

            const isAcceptable = acceptablePatterns.some(pattern => pattern.test(text));

            if (type === 'error' && !isAcceptable) {
                consoleErrors.push(text);
            }
        });

        page.on('pageerror', error => {
            consoleErrors.push(`Page Error: ${error.message}`);
        });

        // Login as admin
        await page.goto('/login');
        await page.fill('input[type="email"]', 'admin@eastgate.com');
        await page.fill('input[type="password"]', '$SEED_TEST_USER_PASSWORD');
        await page.click('button[type="submit"]');
        await page.waitForURL('/dashboard');
    });

    test('Owner Dashboard should handle missing financial data without console errors', async ({page}) => {
        // Wait for dashboard to load
        await page.waitForSelector('[data-testid="owner-dashboard"]', {timeout: 10000});
        await page.waitForTimeout(2000); // Wait for all API calls to complete

        // Check if financial summary card shows appropriate message
        const financialCard = await page.locator('text=Financial Summary').first();
        await expect(financialCard).toBeVisible();

        // Should not have any application errors in console
        // (404 errors are expected and should be handled gracefully)
        const appErrors = consoleErrors.filter(error =>
            !error.includes('404') &&
            !error.includes('Failed to fetch financial data')
        );

        expect(appErrors, `Application errors found: ${appErrors.join(', ')}`).toHaveLength(0);
    });

    test('Documents page should load without console errors', async ({page}) => {
        await page.goto('/documents');
        await page.waitForLoadState('networkidle');

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });

    test('Chat page should load without console errors', async ({page}) => {
        await page.goto('/community/chat');
        await page.waitForSelector('[data-testid="chat-page"]', {timeout: 10000});
        await page.waitForTimeout(1000);

        expect(consoleErrors, `Console errors found: ${consoleErrors.join(', ')}`).toHaveLength(0);
    });

    test('Super admin dashboard should load without console errors', async ({page}) => {
        await page.goto('/dashboard');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(2000);

        const appErrors = consoleErrors.filter(error =>
            !error.includes('404') &&
            !error.includes('net::ERR_')
        );
        expect(appErrors, `Dashboard console errors: ${appErrors.join(', ')}`).toHaveLength(0);
    });

    test('Compliance page should load without console errors', async ({page}) => {
        await page.goto('/compliance');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(2000);

        const appErrors = consoleErrors.filter(error =>
            !error.includes('404') &&
            !error.includes('net::ERR_')
        );
        expect(appErrors, `Compliance console errors: ${appErrors.join(', ')}`).toHaveLength(0);
    });

    test('Events/calendar page should load without console errors', async ({page}) => {
        await page.goto('/community/events');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(2000);

        const appErrors = consoleErrors.filter(error =>
            !error.includes('404') &&
            !error.includes('net::ERR_')
        );
        expect(appErrors, `Events console errors: ${appErrors.join(', ')}`).toHaveLength(0);
    });

    test('Finance page should load without console errors', async ({page}) => {
        await page.goto('/financials');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(2000);

        const appErrors = consoleErrors.filter(error =>
            !error.includes('404') &&
            !error.includes('net::ERR_')
        );
        expect(appErrors, `Finance console errors: ${appErrors.join(', ')}`).toHaveLength(0);
    });
});
