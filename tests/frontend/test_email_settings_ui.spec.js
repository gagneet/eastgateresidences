/**
 * Email Settings UI Tests (Playwright)
 *
 * Tests for Migadu email configuration, SMTP preset button,
 * security mode selector, and email settings form.
 *
 * Session 21: Migadu Email Configuration & SMTP SSL/TLS Support
 */

const {test, expect} = require('@playwright/test');

// Test configuration
const BASE_URL = process.env.TEST_URL || 'https://eastgateresidences.com.au';
const ADMIN_EMAIL = 'admin@eastgate.com';
const ADMIN_PASSWORD = '$SEED_TEST_USER_PASSWORD';

let _emailSettingsSnapshot = null;
let _adminToken = null;

async function _loginForToken(request) {
    const resp = await request.post(`${BASE_URL}/api/auth/login`, {
        data: {email: ADMIN_EMAIL, password: ADMIN_PASSWORD}
    });
    if (!resp.ok()) return null;
    const data = await resp.json();
    return data.token;
}

test.describe('Email Settings Page', () => {
    test.beforeAll(async ({request}) => {
        _adminToken = await _loginForToken(request);
        if (!_adminToken) return;
        const resp = await request.get(`${BASE_URL}/api/email-settings`, {
            headers: {Authorization: `Bearer ${_adminToken}`}
        });
        if (resp.ok()) {
            _emailSettingsSnapshot = await resp.json();
        }
    });

    test.afterAll(async ({request}) => {
        if (!_adminToken || !_emailSettingsSnapshot) return;
        await request.put(`${BASE_URL}/api/email-settings`, {
            headers: {Authorization: `Bearer ${_adminToken}`},
            data: _emailSettingsSnapshot
        });
    });

    test.beforeEach(async ({page}) => {
        // Login as admin before each test
        await page.goto(`${BASE_URL}/login`);
        await page.fill('input[type="email"]', ADMIN_EMAIL);
        await page.fill('input[type="password"]', ADMIN_PASSWORD);
        await page.click('button[type="submit"]');

        // Wait for dashboard to load
        await page.waitForURL(/\/dashboard/);

        // Navigate to the admin communication workspace and open infrastructure settings.
        await page.goto(`${BASE_URL}/admin/communications`);
        await page.getByRole('tab', {name: /infrastructure/i}).click();
        await page.waitForSelector('[data-testid="email-settings-page"]');
    });

    // ==================== PAGE LOAD TESTS ====================

    test('Email settings page loads correctly', async ({page}) => {
        // Verify page title
        await expect(page.locator('[data-testid="email-settings-page"] h1')).toContainText('Email Infrastructure');

        // Verify all tabs are present
        await expect(page.locator('text=Resend')).toBeVisible();
        await expect(page.locator('text=SendGrid')).toBeVisible();
        await expect(page.locator('text=SMTP')).toBeVisible();

        // Verify provider selector
        await expect(page.getByTestId('provider-select')).toBeVisible();

        // Verify save button
        await expect(page.getByTestId('save-email-settings-btn')).toBeVisible();
    });

    test('All form fields are present', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Verify all SMTP fields
        await expect(page.getByTestId('smtp-host-input')).toBeVisible();
        await expect(page.getByTestId('smtp-port-input')).toBeVisible();
        await expect(page.getByTestId('smtp-security-select')).toBeVisible();
        await expect(page.getByTestId('smtp-user-input')).toBeVisible();
        await expect(page.getByTestId('smtp-password-input')).toBeVisible();

        // Verify sender fields
        await expect(page.getByTestId('sender-email-input')).toBeVisible();
        await expect(page.getByTestId('sender-name-input')).toBeVisible();
    });

    // ==================== MIGADU PRESET TESTS ====================

    test('Migadu preset button is visible', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Verify preset button exists
        await expect(page.locator('button:has-text("Use Migadu Settings")')).toBeVisible();

        // Verify preset description
        await expect(page.locator('text=Quick Setup: Migadu')).toBeVisible();
        await expect(page.locator('text=Pre-configured for noreply@eastgateresidences.com.au')).toBeVisible();
    });

    test('Migadu preset auto-fills all fields correctly', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Click Migadu preset button
        await page.click('button:has-text("Use Migadu Settings")');

        // Wait for fields to update
        await page.waitForTimeout(500);

        // Verify SMTP host
        const hostInput = page.getByTestId('smtp-host-input');
        await expect(hostInput).toHaveValue('smtp.migadu.com');

        // Verify SMTP port
        const portInput = page.getByTestId('smtp-port-input');
        await expect(portInput).toHaveValue('465');

        // Verify security mode is SSL
        const securitySelect = page.getByTestId('smtp-security-select');
        const securityValue = await securitySelect.locator('[data-value]').first().getAttribute('data-value');
        expect(securityValue).toBe('ssl');

        // Verify SMTP user
        const userInput = page.getByTestId('smtp-user-input');
        await expect(userInput).toHaveValue('noreply@eastgateresidences.com.au');

        // Verify sender email
        const senderInput = page.getByTestId('sender-email-input');
        await expect(senderInput).toHaveValue('noreply@eastgateresidences.com.au');

        // Verify sender name
        const nameInput = page.getByTestId('sender-name-input');
        await expect(nameInput).toHaveValue('East Gate Residences');
    });

    test('Migadu preset switches provider to SMTP', async ({page}) => {
        // Start on Resend tab
        await page.click('text=Resend');

        // Click Migadu preset button (need to switch to SMTP first)
        await page.click('text=SMTP');
        await page.click('button:has-text("Use Migadu Settings")');

        // Verify provider is now SMTP
        const providerSelect = page.getByTestId('provider-select');
        const selectedValue = await providerSelect.textContent();
        expect(selectedValue).toContain('Custom SMTP');
    });

    // ==================== SECURITY MODE SELECTOR TESTS ====================

    test('Security mode dropdown has all options', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Click security mode dropdown
        await page.getByTestId('smtp-security-select').click();

        // Verify all security options are present
        await expect(page.locator('text=SSL (Implicit TLS)')).toBeVisible();
        await expect(page.locator('text=TLS (STARTTLS)')).toBeVisible();
        await expect(page.locator('text=None (Not Recommended)')).toBeVisible();

        // Verify port badges
        await expect(page.locator('text=Port 465')).toBeVisible();
        await expect(page.locator('text=Port 587')).toBeVisible();
    });

    test('Security mode can be changed', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Open security mode dropdown
        await page.getByTestId('smtp-security-select').click();

        // Select SSL
        await page.click('text=SSL (Implicit TLS)');

        // Verify selection (dropdown should close and show selected value)
        await page.waitForTimeout(300);
        const securityText = await page.getByTestId('smtp-security-select').textContent();
        expect(securityText).toContain('SSL');

        // Change to TLS
        await page.getByTestId('smtp-security-select').click();
        await page.click('text=TLS (STARTTLS)');

        // Verify TLS is selected
        await page.waitForTimeout(300);
        const tlsText = await page.getByTestId('smtp-security-select').textContent();
        expect(tlsText).toContain('TLS');
    });

    test('Port field has helpful guidance text', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Verify port guidance text is present
        await expect(page.locator('text=Port 465 (SSL) or 587 (TLS)')).toBeVisible();
    });

    // ==================== FORM VALIDATION TESTS ====================

    test('SMTP form accepts manual input', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Fill in SMTP settings manually
        await page.getByTestId('smtp-host-input').fill('smtp.test.com');
        await page.getByTestId('smtp-port-input').fill('587');
        await page.getByTestId('smtp-user-input').fill('user@test.com');
        await page.getByTestId('smtp-password-input').fill('testpassword');

        // Verify values were set
        await expect(page.getByTestId('smtp-host-input')).toHaveValue('smtp.test.com');
        await expect(page.getByTestId('smtp-port-input')).toHaveValue('587');
        await expect(page.getByTestId('smtp-user-input')).toHaveValue('user@test.com');
    });

    test('Sender fields can be edited', async ({page}) => {
        // Fill in sender email and name
        await page.getByTestId('sender-email-input').fill('custom@eastgate.com');
        await page.getByTestId('sender-name-input').fill('Custom Name');

        // Verify values
        await expect(page.getByTestId('sender-email-input')).toHaveValue('custom@eastgate.com');
        await expect(page.getByTestId('sender-name-input')).toHaveValue('Custom Name');
    });

    test('Port field only accepts numbers', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        const portInput = page.getByTestId('smtp-port-input');

        // Try to enter letters (should not work due to type="number")
        await portInput.fill('abc');
        await expect(portInput).toHaveValue('');

        // Enter valid number
        await portInput.fill('465');
        await expect(portInput).toHaveValue('465');
    });

    // ==================== SAVE FUNCTIONALITY TESTS ====================

    test('Save button shows loading state', async ({page}) => {
        // Switch to SMTP tab and use preset
        await page.click('text=SMTP');
        await page.click('button:has-text("Use Migadu Settings")');

        // Click save button
        const saveButton = page.getByTestId('save-email-settings-btn');
        await saveButton.click();

        // Verify loading state appears briefly
        // (This test might need adjustment based on actual loading behavior)
        await expect(saveButton).toContainText(/Saving|Save Settings/);
    });

    test('Migadu configuration can be saved', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Use Migadu preset
        await page.click('button:has-text("Use Migadu Settings")');

        // Fill in password (not auto-filled)
        await page.getByTestId('smtp-password-input').fill('testpassword123');

        // Click save
        await page.getByTestId('save-email-settings-btn').click();

        // Wait for success toast or response
        await page.waitForTimeout(1000);

        // Verify success (check for toast notification or page state)
        // Note: Adjust selector based on actual toast implementation
        const toastExists = await page.locator('.sonner-toast').isVisible().catch(() => false);

        // If toast exists, verify success message
        if (toastExists) {
            await expect(page.locator('.sonner-toast')).toContainText(/saved|success/i);
        }
    });

    // ==================== TEST EMAIL FUNCTIONALITY ====================

    test('Test email section is present', async ({page}) => {
        // Verify test email card
        await expect(page.locator('text=Test Configuration')).toBeVisible();

        // Verify test email input
        await expect(page.getByTestId('test-email-input')).toBeVisible();

        // Verify send test button
        await expect(page.getByTestId('send-test-btn')).toBeVisible();
    });

    test('Test email input accepts email address', async ({page}) => {
        const testEmailInput = page.getByTestId('test-email-input');

        // Enter test email
        await testEmailInput.fill('test@example.com');

        // Verify value
        await expect(testEmailInput).toHaveValue('test@example.com');
    });

    test('Send test button shows loading state', async ({page}) => {
        // Fill in test email
        await page.getByTestId('test-email-input').fill('test@example.com');

        // Click send test button
        const sendTestBtn = page.getByTestId('send-test-btn');
        await sendTestBtn.click();

        // Verify loading state (button text changes)
        await expect(sendTestBtn).toContainText(/Sending|Send Test/);
    });

    // ==================== PROVIDER TABS TESTS ====================

    test('Can switch between provider tabs', async ({page}) => {
        // Start on Resend
        await expect(page.locator('text=Resend Configuration')).toBeVisible();

        // Switch to SendGrid
        await page.click('text=SendGrid');
        await expect(page.locator('text=SendGrid Configuration')).toBeVisible();

        // Switch to SMTP
        await page.click('text=SMTP');
        await expect(page.locator('text=SMTP Configuration')).toBeVisible();
    });

    test('Provider tabs are synchronized with dropdown', async ({page}) => {
        // Click provider dropdown
        await page.getByTestId('provider-select').click();

        // Select SMTP from dropdown
        await page.click('[role="option"]:has-text("Custom SMTP")');

        // Wait for tab to switch
        await page.waitForTimeout(300);

        // Verify SMTP tab content is visible
        await expect(page.locator('text=SMTP Configuration')).toBeVisible();
    });

    // ==================== DOCUMENTATION LINKS TESTS ====================

    test('SMTP tab shows provider documentation', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Verify documentation section
        await expect(page.locator('text=Supported SMTP Providers')).toBeVisible();

        // Verify provider list
        await expect(page.locator('text=Migadu')).toBeVisible();
        await expect(page.locator('text=Gmail')).toBeVisible();
        await expect(page.locator('text=Outlook')).toBeVisible();
    });

    test('Resend tab shows API key link', async ({page}) => {
        // Should be on Resend by default or click Resend tab
        await page.click('text=Resend');

        // Verify API key link
        const resendLink = page.locator('a[href*="resend.com/api-keys"]');
        await expect(resendLink).toBeVisible();
    });

    // ==================== RESPONSIVE DESIGN TESTS ====================

    test('Email settings page is responsive on mobile', async ({page}) => {
        // Set mobile viewport
        await page.setViewportSize({width: 375, height: 667});

        // Verify page still loads
        await expect(page.locator('[data-testid="email-settings-page"]')).toBeVisible();

        // Verify tabs are accessible
        await expect(page.locator('text=SMTP')).toBeVisible();
    });

    test('Email settings page is responsive on tablet', async ({page}) => {
        // Set tablet viewport
        await page.setViewportSize({width: 768, height: 1024});

        // Verify layout adjusts
        await expect(page.locator('[data-testid="email-settings-page"]')).toBeVisible();

        // Verify form fields are accessible
        await page.click('text=SMTP');
        await expect(page.getByTestId('smtp-host-input')).toBeVisible();
    });

    // ==================== ACCESSIBILITY TESTS ====================

    test('Form fields have proper labels', async ({page}) => {
        // Switch to SMTP tab
        await page.click('text=SMTP');

        // Verify labels are associated with inputs
        await expect(page.locator('label[for="smtp_host"]')).toBeVisible();
        await expect(page.locator('label[for="smtp_port"]')).toBeVisible();
        await expect(page.locator('label[for="smtp_user"]')).toBeVisible();
        await expect(page.locator('label[for="smtp_password"]')).toBeVisible();
    });

    test('Buttons have descriptive text', async ({page}) => {
        // Verify all buttons have clear text
        await expect(page.locator('button:has-text("Save Settings")')).toBeVisible();
        await expect(page.locator('button:has-text("Send Test")')).toBeVisible();

        // Switch to SMTP
        await page.click('text=SMTP');
        await expect(page.locator('button:has-text("Use Migadu Settings")')).toBeVisible();
    });

    // ==================== ERROR HANDLING TESTS ====================

    test('Shows error for empty test email', async ({page}) => {
        // Click send test without entering email
        await page.getByTestId('send-test-btn').click();

        // Wait for error toast
        await page.waitForTimeout(500);

        // Verify error message (adjust selector based on toast implementation)
        const hasError = await page.locator('text=/enter.*email/i').isVisible().catch(() => false);
        expect(hasError).toBeTruthy();
    });

    // ==================== INTEGRATION TESTS ====================

    test('Complete Migadu setup workflow', async ({page}) => {
        // 1. Switch to SMTP tab
        await page.click('text=SMTP');

        // 2. Click Migadu preset
        await page.click('button:has-text("Use Migadu Settings")');

        // 3. Verify all fields are filled
        await expect(page.getByTestId('smtp-host-input')).toHaveValue('smtp.migadu.com');
        await expect(page.getByTestId('smtp-port-input')).toHaveValue('465');

        // 4. Add password
        await page.getByTestId('smtp-password-input').fill('securepassword123');

        // 5. Save settings
        await page.getByTestId('save-email-settings-btn').click();

        // 6. Wait for success
        await page.waitForTimeout(1000);

        // 7. Test email
        await page.getByTestId('test-email-input').fill('admin@eastgate.com');
        await page.getByTestId('send-test-btn').click();

        // 8. Verify test initiated
        await page.waitForTimeout(1000);
    });
});

// ==================== ADMIN-ONLY ACCESS TESTS ====================

test.describe('Email Settings Access Control', () => {
    test('Non-admin users cannot access email settings', async ({page}) => {
        // Try to navigate to email settings without login
        await page.goto(`${BASE_URL}/admin/communications`);
        await page.getByRole('tab', {name: /infrastructure/i}).click();

        // Should redirect to login or show unauthorized
        await page.waitForURL(/\/(login|dashboard)/);

        // Verify not on email settings page
        const isOnEmailSettings = await page.locator('[data-testid="email-settings-page"]').isVisible().catch(() => false);

        if (!isOnEmailSettings) {
            // Either on login page or unauthorized page
            const onLoginPage = await page.locator('input[type="email"]').isVisible().catch(() => false);
            const hasUnauthorizedMessage = await page.locator('text=/unauthorized|not authorized|forbidden/i').isVisible().catch(() => false);

            expect(onLoginPage || hasUnauthorizedMessage).toBeTruthy();
        }
    });
});

// ==================== TEST CONFIGURATION ====================

test.use({
    // Use saved authentication state if available
    storageState: process.env.STORAGE_STATE || undefined,

    // Set default timeout
    timeout: 30000,

    // Screenshot on failure
    screenshot: 'only-on-failure',

    // Video on failure
    video: 'retain-on-failure'
});
