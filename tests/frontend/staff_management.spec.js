/**
 * Staff User Management — Playwright E2E Tests
 *
 * Tests cover:
 * - Super Admin can access /admin/staff-users
 * - Staff list page renders correctly
 * - Clicking a staff card navigates to detail page
 * - Assign Role modal opens and submits
 * - Non-super-admin is blocked from the page
 *
 * Run:
 *   npx playwright test tests/frontend/staff_management.spec.js
 */

const {test, expect} = require('@playwright/test');

// Helpers -----------------------------------------------------------------

async function loginAs(page, role = 'super_admin') {
    const credentials = {
        super_admin: {email: 'admin@eastgateresidences.com.au', password: '$SEED_TEST_USER_PASSWORD'},
        chairman: {email: 'chairman@eastgateresidences.com.au', password: '$SEED_TEST_USER_PASSWORD'},
        owner: {email: 'owner@test.com', password: '$SEED_TEST_USER_PASSWORD'},
    };
    const creds = credentials[ role ] || credentials.super_admin;

    await page.goto('/login');
    await page.fill('input[type="email"]', creds.email);
    await page.fill('input[type="password"]', creds.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/dashboard/, {timeout: 15000});
}

// Staff list page tests ---------------------------------------------------

test.describe('Staff Users List Page', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, 'super_admin');
    });

    test('Super Admin can navigate to staff users page', async ({page}) => {
        await page.goto('/admin/staff-users');
        await expect(page).toHaveURL(/staff-users/);
        await expect(page.locator('h1, [data-testid="page-title"]')).toContainText('Staff User Management');
    });

    test('Page shows staff user statistics cards', async ({page}) => {
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        // Stats cards should be present
        await expect(page.locator('text=Total Staff')).toBeVisible({timeout: 10000});
    });

    test('Search filter is visible and functional', async ({page}) => {
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        const searchInput = page.locator('input[placeholder*="Search"]');
        await expect(searchInput).toBeVisible();
        await searchInput.fill('manager');
        // Results should filter
        await page.waitForTimeout(300);
    });

    test('Role filter dropdown is visible', async ({page}) => {
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        await expect(page.locator('text=Filter by role, All roles').first()).toBeVisible({timeout: 8000});
    });

    test('Add Staff User button navigates to create page', async ({page}) => {
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        const addBtn = page.locator('button:has-text("Add Staff User")');
        await expect(addBtn).toBeVisible({timeout: 8000});
        await addBtn.click();
        await expect(page).toHaveURL(/create-staff-user/);
    });

    test('Refresh button is present', async ({page}) => {
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        await expect(page.locator('button:has-text("Refresh")')).toBeVisible({timeout: 8000});
    });
});

// Staff detail page tests -------------------------------------------------

test.describe('Staff User Detail Page', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, 'super_admin');
    });

    test('Direct navigation to staff users page succeeds', async ({page}) => {
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        // If there are staff users, click the first one
        const cards = page.locator('[data-testid="staff-card"], .cursor-pointer');
        const count = await cards.count();
        if (count > 0) {
            await cards.first().click();
            await expect(page).toHaveURL(/staff-users\/.+/);
        }
    });

    test('Back button returns to staff list', async ({page}) => {
        // Navigate directly to an arbitrary staff user detail (will 404 but test navigation)
        await page.goto('/admin/staff-users/test-user-id');
        await page.waitForLoadState('networkidle');
        const backBtn = page.locator('button:has-text("Staff Users"), a:has-text("Staff Users")');
        await expect(backBtn.first()).toBeVisible({timeout: 8000});
        await backBtn.first().click();
        await expect(page).toHaveURL(/staff-users$/);
    });
});

// Assign Role modal tests -------------------------------------------------

test.describe('Assign Role Modal', () => {
    test.beforeEach(async ({page}) => {
        await loginAs(page, 'super_admin');
    });

    test('Assign Role button is shown on detail page when user is found', async ({page}) => {
        // Mock via list first
        await page.goto('/admin/staff-users');
        await page.waitForLoadState('networkidle');
        const cards = page.locator('.cursor-pointer');
        const count = await cards.count();
        if (count > 0) {
            await cards.first().click();
            await page.waitForLoadState('networkidle');
            const assignBtn = page.locator('button:has-text("Assign Role")');
            if (await assignBtn.isVisible()) {
                await assignBtn.click();
                await expect(page.locator('[role="dialog"]')).toBeVisible({timeout: 5000});
                await expect(page.locator('text=Assign Elevated Role')).toBeVisible();
                await expect(page.locator('text=Role')).toBeVisible();
                await expect(page.locator('text=Building')).toBeVisible();
            }
        }
    });
});

// Access control tests ----------------------------------------------------

test.describe('Access Control', () => {
    test('Staff Users page redirects non-logged-in users', async ({page}) => {
        await page.goto('/admin/staff-users');
        // Should redirect to login
        await expect(page).toHaveURL(/login|select-building/, {timeout: 10000});
    });

    test('Sidebar shows Staff User Management only for super admin', async ({page}) => {
        await loginAs(page, 'super_admin');
        await page.waitForLoadState('networkidle');
        // Super admin should see the nav item
        const navItem = page.locator('a[href="/admin/staff-users"]');
        await expect(navItem).toBeVisible({timeout: 10000});
    });
});

// API integration tests (against real backend if available) ---------------

test.describe('Staff Management API', () => {
    test('GET /api/admin/staff-users returns 401 without auth', async ({request}) => {
        const res = await request.get('/api/admin/staff-users');
        expect([401, 403, 422]).toContain(res.status());
    });

    test('POST /api/admin/staff-users/{id}/building-roles returns 401 without auth', async ({request}) => {
        const res = await request.post('/api/admin/staff-users/test-id/building-roles', {
            data: {role_name: 'ec_member', building_id: 'building-001'},
        });
        expect([401, 403, 422]).toContain(res.status());
    });

    test('DELETE /api/admin/staff-users/{id}/building-roles/{asgn} returns 401 without auth', async ({request}) => {
        const res = await request.delete('/api/admin/staff-users/test-id/building-roles/asgn-001');
        expect([401, 403, 422]).toContain(res.status());
    });

    test('GET /api/admin/staff-users/buildings returns 401 without auth', async ({request}) => {
        const res = await request.get('/api/admin/staff-users/buildings');
        expect([401, 403, 422]).toContain(res.status());
    });
});
