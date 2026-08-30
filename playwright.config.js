// @ts-check
const {defineConfig, devices} = require('@playwright/test');

/**
 * Playwright configuration for Strata Management E2E tests
 * @see https://playwright.dev/docs/test-configuration
 */
module.exports = defineConfig({
    testDir: './tests/frontend',
    testMatch: ['**/*.spec.{js,ts,tsx}'],
    testIgnore: ['**/*.test.{js,ts,tsx}'],

    /* Run tests in files in parallel */
    fullyParallel: false,

    /* Fail the build on CI if you accidentally left test.only in the source code. */
    forbidOnly: !!process.env.CI,

    /* Retry on CI only */
    retries: process.env.CI ? 2 : 0,

    /* Opt out of parallel tests on CI. */
    workers: process.env.CI ? 1 : undefined,

    /* Reporter to use. See https://playwright.dev/docs/test-reporters */
    reporter: [
        ['html', {outputFolder: 'test_reports/html'}],
        ['json', {outputFile: 'test_reports/results.json'}],
        ['list'],
    ],

    /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
    use: {
        /* Base URL to use in actions like `await page.goto('/')`. */
        baseURL: process.env.BASE_URL || 'https://eastgateresidences.com.au',

        /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
        trace: 'on-first-retry',

        /* Screenshot on failure */
        screenshot: 'only-on-failure',

        /* Video on failure */
        video: 'retain-on-failure',

        /* Maximum time each action such as `click()` can take */
        actionTimeout: 10000,

        /* Maximum time each navigation can take */
        navigationTimeout: 30000,
    },

    /* Configure projects for major browsers */
    projects: [
        {
            name: 'chromium',
            use: {...devices[ 'Desktop Chrome' ]},
        },

        {
            name: 'firefox',
            use: {...devices[ 'Desktop Firefox' ]},
        },

        {
            name: 'webkit',
            use: {...devices[ 'Desktop Safari' ]},
        },

        /* Test against mobile viewports. */
        {
            name: 'Mobile Chrome',
            use: {...devices[ 'Pixel 5' ]},
        },
        {
            name: 'Mobile Safari',
            use: {...devices[ 'iPhone 12' ]},
        },

        /* Security tests — API-only, no browser rendering overhead.
         * Target: http://localhost:8003/api (local backend).
         * Requires: backend running + demo seed (seeds/demo_customer.py).
         * Run: npx playwright test --project=security --reporter=list
         * Or:  TEST_API=http://localhost:8003/api npx playwright test --project=security
         */
        {
            name: 'security',
            testDir: './tests/frontend/e2e/security',
            use: {
                baseURL: process.env.TEST_API || 'http://localhost:8003',
                // No browser for API-only security tests
                ...devices['Desktop Chrome'],
                // Short timeouts — these are pure API calls
                actionTimeout: 5000,
                navigationTimeout: 10000,
            },
        },
    ],

    /* Folder for test artifacts such as screenshots, videos, traces, etc. */
    outputDir: 'test-results/',

    /* Global setup/teardown scripts */
    globalSetup: require.resolve('./tests/frontend/global-setup.js'),
    globalTeardown: require.resolve('./tests/frontend/global-teardown.js'),
});
