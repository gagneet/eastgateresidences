// @ts-check
const {defineConfig, devices} = require('@playwright/test');

module.exports = defineConfig({
    testDir: './tests/frontend/e2e',
    testMatch: ['request-catalogue-journeys.spec.ts'],
    fullyParallel: false,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    workers: 1,
    reporter: [
        ['html', {outputFolder: 'test_reports/request-catalogue/html'}],
        ['json', {outputFile: 'test_reports/request-catalogue/results.json'}],
        ['list'],
    ],
    use: {
        baseURL: process.env.BASE_URL || 'http://localhost:3020',
        trace: 'on-first-retry',
        screenshot: 'only-on-failure',
        video: 'retain-on-failure',
        actionTimeout: 10000,
        navigationTimeout: 30000,
    },
    projects: [
        {
            name: 'request-catalogue-chromium',
            use: {...devices['Desktop Chrome']},
        },
        {
            name: 'request-catalogue-mobile',
            use: {...devices['Pixel 5']},
            grep: /mobile layout|legacy link/,
        },
    ],
    outputDir: 'test-results/request-catalogue/',
    globalSetup: require.resolve('./tests/frontend/e2e/request-catalogue.global-setup.js'),
});
