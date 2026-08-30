// @ts-check
const {test, expect} = require('@playwright/test');

test.describe('Audit Logs System', () => {
    test.beforeEach(async ({page}) => {
        // Login as admin
        await page.goto('/login');
        await page.fill('input[type="email"]', 'admin@eastgate.com');
        await page.fill('input[type="password"]', '$SEED_TEST_USER_PASSWORD');
        await page.click('button[type="submit"]');
        await page.waitForURL('/dashboard');
    });

    test('should display audit logs in admin console', async ({page}) => {
        // Mock audit logs response
        await page.route('**/api/notifications/admin/audit-logs*', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([
                    {
                        id: 'log1',
                        action: 'created',
                        resource_type: 'announcement',
                        user_name: 'Admin User',
                        details: {title: 'New Year Party'},
                        created_at: new Date().toISOString()
                    }
                ])
            });
        });

        await page.goto('/admin/audit-logs');

        await expect(page.locator('h1')).toHaveText('Audit Logs');
        await expect(page.locator('text=Admin User')).toBeVisible();
        await expect(page.locator('text=announcement')).toBeVisible();
        await expect(page.locator('text=New Year Party')).toBeVisible();
    });
});
