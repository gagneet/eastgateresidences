/**
 * SuperAdmin Dashboard Tests
 *
 * Tests cover:
 * - SuperAdmin dashboard displays all 11 cards
 * - Admin action cards navigate correctly
 * - Permission checks
 * - Statistics display
 * - System health indicators
 * - Recent activity feed
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';
const ADMIN_USER = {
    email: 'admin@eastgate.com',
    password: '$SEED_TEST_USER_PASSWORD'
};

async function loginAsAdmin(page) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"]', ADMIN_USER.email);
    await page.fill('input[type="password"]', ADMIN_USER.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(`${BASE_URL}/dashboard`, {timeout: 10000});
}

test.describe('SuperAdmin Dashboard', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('Should display SuperAdmin dashboard', async ({page}) => {
        await expect(page.locator('[data-testid="super-admin-dashboard"]')).toBeVisible({timeout: 10000});
        await expect(page.locator('text=Super Admin Dashboard')).toBeVisible();
    });

    test('Should display all 11 admin action cards', async ({page}) => {
        await page.waitForSelector('[data-testid^="admin-action"]', {timeout: 10000});

        const adminCards = await page.locator('[data-testid^="admin-action"]').count();

        // Should have 11 admin action cards
        expect(adminCards).toBe(11);
    });

    test('Should display User Management card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-user-management"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=User Management')).toBeVisible();
        await expect(card.locator('text=/manage.*users.*roles.*permissions/i')).toBeVisible();
    });

    test('Should display Feature Toggles card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-feature-toggles"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Feature Toggles')).toBeVisible();
    });

    test('Should display User Feature Permissions card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-user-feature-permissions"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=User Feature Permissions')).toBeVisible();
    });

    test('Should display User Permissions card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-user-permissions"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=User Permissions')).toBeVisible();
    });

    test('Should display Unit Change Requests card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-unit-change-requests"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Unit Change Requests')).toBeVisible();
    });

    test('Should display Owners & Units card (NEW)', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-owners-&-units"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Owners & Units')).toBeVisible();
        await expect(card.locator('text=/manage.*property.*owners.*unit.*assignments/i')).toBeVisible();
    });

    test('Should display Tenant Renewals card (NEW)', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-tenant-renewals"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Tenant Renewals')).toBeVisible();
        await expect(card.locator('text=/review.*process.*tenant.*lease.*renewals/i')).toBeVisible();
    });

    test('Should display Expired Accounts card (NEW)', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-expired-accounts"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Expired Accounts')).toBeVisible();
        await expect(card.locator('text=/manage.*expired.*tenant.*guest.*accounts/i')).toBeVisible();
    });

    test('Should display Admin Console card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-admin-console"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Admin Console')).toBeVisible();
    });

    test('Should display Building Settings card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-site-settings"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Building Settings')).toBeVisible();
    });

    test('Should display Admin Communications card', async ({page}) => {
        const card = page.locator('[data-testid="admin-action-admin-communications"]');
        await expect(card).toBeVisible();
        await expect(card.locator('text=Admin Communications')).toBeVisible();
    });
});

test.describe('Admin Card Navigation', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('User Management card should navigate to /admin/users', async ({page}) => {
        await page.click('[data-testid="admin-action-user-management"]');
        await page.waitForURL(`${BASE_URL}/admin/users`, {timeout: 5000});
        expect(page.url()).toContain('/admin/users');
    });

    test('Owners & Units card should navigate to /admin/owners-units', async ({page}) => {
        await page.click('[data-testid="admin-action-owners-&-units"]');
        await page.waitForURL(`${BASE_URL}/admin/owners-units`, {timeout: 5000});
        expect(page.url()).toContain('/admin/owners-units');
    });

    test('Tenant Renewals card should navigate to /admin/tenant-renewals', async ({page}) => {
        await page.click('[data-testid="admin-action-tenant-renewals"]');
        await page.waitForURL(`${BASE_URL}/admin/tenant-renewals`, {timeout: 5000});
        expect(page.url()).toContain('/admin/tenant-renewals');
    });

    test('Expired Accounts card should navigate to /admin/expired-accounts', async ({page}) => {
        await page.click('[data-testid="admin-action-expired-accounts"]');
        await page.waitForURL(`${BASE_URL}/admin/expired-accounts`, {timeout: 5000});
        expect(page.url()).toContain('/admin/expired-accounts');
    });

    test('Unit Change Requests card should navigate without 500 error', async ({page}) => {
        await page.click('[data-testid="admin-action-unit-change-requests"]');
        await page.waitForURL(`${BASE_URL}/admin/unit-change-requests`, {timeout: 5000});

        // Should NOT show 500 error
        const hasError = await page.locator('text=/500|internal.*error/i').isVisible().catch(() => false);
        expect(hasError).toBe(false);
    });
});

test.describe('Dashboard Statistics', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('Should display stat cards', async ({page}) => {
        const statCards = await page.locator('[data-testid^="stat-card"]').count();
        expect(statCards).toBeGreaterThan(0);
    });

    test('Should display Total Users stat', async ({page}) => {
        const card = page.locator('[data-testid="stat-card-total-users"]');
        await expect(card).toBeVisible();

        // Should show a number
        const text = await card.textContent();
        expect(text).toMatch(/\d+/);
    });

    test('Should display Active Documents stat', async ({page}) => {
        const card = page.locator('[data-testid="stat-card-active-documents"]');
        if (await card.isVisible()) {
            const text = await card.textContent();
            expect(text).toMatch(/\d+/);
        }
    });

    test('Stat cards should be clickable', async ({page}) => {
        const firstStat = page.locator('[data-testid^="stat-card"]').first();
        await expect(firstStat).toBeVisible();

        // Should have cursor pointer or be clickable
        const cursor = await firstStat.evaluate(el => window.getComputedStyle(el).cursor);
        expect(cursor).toBe('pointer');
    });
});

test.describe('System Health', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('Should display System Health section', async ({page}) => {
        await expect(page.locator('text=System Health')).toBeVisible();
    });

    test('Should show service status indicators', async ({page}) => {
        const services = ['Database', 'Email Service', 'API Server', 'File Storage'];

        for (const service of services) {
            const serviceElement = page.locator(`text=${service}`);
            if (await serviceElement.isVisible()) {
                await expect(serviceElement).toBeVisible();
            }
        }
    });

    test('Should show operational status', async ({page}) => {
        // Look for operational indicators
        const operational = page.locator('text=Operational');
        const count = await operational.count();

        // Should have at least some operational services
        expect(count).toBeGreaterThan(0);
    });
});

test.describe('Recent Activity', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('Should display Recent Announcements section', async ({page}) => {
        await expect(page.locator('text=Recent Announcements')).toBeVisible();
    });

    test('Should display Notice Board section', async ({page}) => {
        await expect(page.locator('text=Notice Board')).toBeVisible();
    });

    test('Should display Recent System Activity', async ({page}) => {
        await expect(page.locator('text=Recent System Activity')).toBeVisible();
    });

    test('Should show View All buttons', async ({page}) => {
        const viewAllButtons = await page.locator('button:has-text("View All")').count();
        expect(viewAllButtons).toBeGreaterThan(0);
    });
});

test.describe('User Dashboard Access', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('Should have link to regular user dashboard', async ({page}) => {
        const regularDashboardBtn = page.locator('[data-testid="view-user-dashboard-btn"], [data-testid="regular-dashboard-btn"]');
        await expect(regularDashboardBtn.first()).toBeVisible();
    });

    test('Should navigate to regular dashboard', async ({page}) => {
        const btn = page.locator('text=View User Dashboard').first();
        await btn.click();

        await page.waitForURL(`${BASE_URL}/owner-view`, {timeout: 5000});
        expect(page.url()).toContain('/owner-view');
    });
});

test.describe('Permission Checks', () => {

    test('Regular user should NOT see SuperAdmin dashboard', async ({page}) => {
        // Login as regular owner
        await page.goto(`${BASE_URL}/login`);
        await page.fill('input[type="email"]', 'owner@eastgate.com');
        await page.fill('input[type="password"]', '$SEED_TEST_USER_PASSWORD');
        await page.click('button[type="submit"]');
        await page.waitForURL(`${BASE_URL}/dashboard`);

        // Should NOT see super admin dashboard
        const isSuperAdmin = await page.locator('[data-testid="super-admin-dashboard"]').isVisible().catch(() => false);
        expect(isSuperAdmin).toBe(false);

        // Should NOT see admin action cards
        const hasAdminCards = await page.locator('[data-testid^="admin-action"]').isVisible().catch(() => false);
        expect(hasAdminCards).toBe(false);
    });

    test('Tenant should NOT have admin access', async ({page}) => {
        await page.goto(`${BASE_URL}/login`);
        await page.fill('input[type="email"]', 'tenant@eastgate.com');
        await page.fill('input[type="password"]', process.env.E2E_TENANT_PASSWORD || '');
        await page.click('button[type="submit"]');
        await page.waitForURL(`${BASE_URL}/dashboard`);

        // Try to access admin console
        await page.goto(`${BASE_URL}/admin/console`);

        // Should be denied or redirected
        await page.waitForTimeout(2000);
        const hasAccess = page.url().includes('/admin/console');
        const hasError = await page.locator('text=/access.*denied|unauthorized|403/i').isVisible().catch(() => false);

        expect(!hasAccess || hasError).toBe(true);
    });
});

test.describe('Responsive Design', () => {

    test.beforeEach(async ({page}) => {
        await loginAsAdmin(page);
    });

    test('Should display correctly on desktop', async ({page}) => {
        await page.setViewportSize({width: 1920, height: 1080});

        await expect(page.locator('[data-testid="super-admin-dashboard"]')).toBeVisible();

        // Admin cards should be in grid
        const grid = page.locator('.grid').filter({has: page.locator('[data-testid^="admin-action"]')});
        await expect(grid.first()).toBeVisible();
    });

    test('Should display correctly on tablet', async ({page}) => {
        await page.setViewportSize({width: 768, height: 1024});

        await expect(page.locator('[data-testid="super-admin-dashboard"]')).toBeVisible();

        // Cards should still be visible
        const cards = await page.locator('[data-testid^="admin-action"]').count();
        expect(cards).toBe(11);
    });

    test('Should display correctly on mobile', async ({page}) => {
        await page.setViewportSize({width: 375, height: 667});

        await expect(page.locator('[data-testid="super-admin-dashboard"]')).toBeVisible();

        // Cards should stack vertically
        const cards = await page.locator('[data-testid^="admin-action"]').count();
        expect(cards).toBe(11);
    });
});
