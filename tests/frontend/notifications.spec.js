// @ts-check
const {test, expect} = require('@playwright/test');

test.describe('Notifications System', () => {
    test.beforeEach(async ({page}) => {
        // Login as admin
        await page.goto('/login');
        await page.fill('input[type="email"]', 'admin@eastgate.com');
        await page.fill('input[type="password"]', '$SEED_TEST_USER_PASSWORD');
        await page.click('button[type="submit"]');
        await page.waitForURL('/dashboard');
    });

    test('should display notification bell and unread count', async ({page}) => {
        // Mock notifications response
        await page.route('**/api/notifications/unread-count', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({unread_count: 5})
            });
        });

        await page.goto('/dashboard');
        const bellIcon = page.locator('[data-testid="notifications-btn"]');
        await expect(bellIcon).toBeVisible();

        const countBadge = bellIcon.locator('span');
        await expect(countBadge).toHaveText('5');
    });

    test('should show notifications in dropdown', async ({page}) => {
        // Mock notifications list
        await page.route('**/api/notifications/unread', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([
                    {
                        id: '1',
                        title: 'Test Notification',
                        message: 'This is a test message',
                        type: 'general',
                        created_at: new Date().toISOString()
                    }
                ])
            });
        });

        await page.goto('/dashboard');
        await page.click('[data-testid="notifications-btn"]');

        const notifItem = page.locator('text=Test Notification');
        await expect(notifItem).toBeVisible();
        await expect(page.locator('text=This is a test message')).toBeVisible();
    });
});
