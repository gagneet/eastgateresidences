const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';
const HAS_TEST_CREDENTIALS = Boolean(process.env.TEST_EMAIL && process.env.TEST_PASSWORD);

const TEST_USER = {
    email: process.env.TEST_EMAIL || '',
    password: process.env.TEST_PASSWORD || '',
};

test.describe('Intelligence Features', () => {
    test.skip(!HAS_TEST_CREDENTIALS,
        'TEST_EMAIL and TEST_PASSWORD environment variables must be set to run E2E tests.');

    test.beforeEach(async ({page}) => {
        await page.goto(`${BASE_URL}/login`);
        await page.fill('input[type="email"]', TEST_USER.email);
        await page.fill('input[type="password"]', TEST_USER.password);
        await page.click('button[type="submit"]');
        await page.waitForURL(`${BASE_URL}/dashboard`, {timeout: 10000});
    });

    test('Property intelligence dashboard loads', async ({page}) => {
        await page.goto(`${BASE_URL}/intelligence/building`);
        await expect(page.locator('text=Property Intelligence')).toBeVisible();
        await expect(
            page.getByTestId('dashboard-content').getByText('Attention Needed').first()
        ).toBeVisible();
    });

    test('Levy fairness page loads', async ({page}) => {
        await page.goto(`${BASE_URL}/intelligence/levy-fairness`);
        await expect(page.locator('text=Levy Fairness Analysis')).toBeVisible();
        await expect(page.locator('text=Levy Impact by Group')).toBeVisible();
    });

    test('Capital shock risk page loads', async ({page}) => {
        await page.goto(`${BASE_URL}/intelligence/capital-risk`);
        await expect(
            page.getByTestId('dashboard-content').getByText('Capital Shock Risk')
        ).toBeVisible();
        await expect(page.locator('text=Projected Shock Events')).toBeVisible();
    });

    test('Asset intelligence page loads', async ({page}) => {
        await page.goto(`${BASE_URL}/intelligence/assets`);
        await expect(
            page.getByTestId('dashboard-content').getByText('Asset Intelligence')
        ).toBeVisible();
        await expect(page.locator('text=Asset Health Snapshot')).toBeVisible();
    });
});
