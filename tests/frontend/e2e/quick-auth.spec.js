const {test, expect} = require('@playwright/test');

test.describe('Login Flow', () => {
    test('should login successfully as admin', async ({page}) => {
        await page.goto('http://localhost:3020/login');

        // Fill credentials
        await page.fill('input[type="email"]', 'administrator@eastgateresidences.com.au');
        await page.fill('input[type="password"]', process.env.E2E_ADMIN_PASSWORD || '');

        // The "Sign In" button on the login form itself (usually inside the card)
        // Avoid the header nav buttons by being more specific
        const signInBtn = page.locator('form button:has-text("Sign In")');
        await signInBtn.click();

        // Wait for navigation - use state: 'networkidle' for Next.js hydration
        await page.waitForURL('**/dashboard', {timeout: 30000});

        // Verify we are on dashboard
        await expect(page).toHaveURL(/.*dashboard/);

        // Take a screenshot for verification
        await page.screenshot({path: 'login-success.png'});
    });
});
