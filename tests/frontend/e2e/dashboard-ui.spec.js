/**
 * Dashboard UI/UX Playwright Tests
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'http://localhost:3020';
const TEST_USER = {
    email: 'administrator@strataos.live',
    password: process.env.E2E_ADMIN_PASSWORD || ''
};

test.describe('Dashboard UI/UX Tests', () => {

    test.beforeEach(async ({page}) => {
        await page.goto(`${BASE_URL}/login`);
        await page.fill('[data-testid="email-input"]', TEST_USER.email);
        await page.fill('[data-testid="password-input"]', TEST_USER.password);
        await page.click('[data-testid="login-submit"]');
        await page.waitForURL(/.*select-building/, {timeout: 15000});
        await page.getByRole('button', {name: /East Gate Residences/i}).click();
        await page.waitForURL(/.*dashboard/, {timeout: 15000});
    });

    test('Dashboard Layout should be visible', async ({page}) => {
        const sidebar = page.locator('aside');
        await expect(sidebar).toBeVisible();

        const main = page.locator('main');
        await expect(main).toBeVisible();
    });

    test('Header elements should be visible on desktop', async ({page}) => {
        await page.setViewportSize({width: 1280, height: 720});

        const header = page.locator('[data-testid="main-header"]');
        await expect(header).toBeVisible();

        // Notification bell
        const bellIcon = header.locator('[data-testid="notifications-btn"]');
        await expect(bellIcon).toBeVisible();

        // User menu trigger
        const userMenu = header.locator('[data-testid="user-menu-trigger"]');
        await expect(userMenu).toBeVisible();
    });

    test('Logout functionality should work from sidebar', async ({page}) => {
        await page.setViewportSize({width: 1280, height: 720});
        const logoutButton = page.locator('button:has-text("Sign Out")');
        await expect(logoutButton).toBeVisible();
        await logoutButton.click();
        await page.waitForURL(/.*(login|logout|$)/, {timeout: 10000});
    });

    test('Levy KPI page should load from the current finance route', async ({page}) => {
        await page.waitForURL(/.*dashboard/, {timeout: 15000});
        await page.goto(`${BASE_URL}/financials/levy-kpi`);
        await expect(page.getByRole('heading', {name: /Levy KPI Analysis/i})).toBeVisible({
            timeout: 15000,
        });
    });

});
