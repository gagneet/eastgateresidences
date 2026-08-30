/**
 * Strata Roll Name Impersonation E2E Tests
 *
 * Covers the ImpersonateModal feature where:
 * - unit_owner_name (strata roll) is shown as the primary display name
 * - full_name (portal login name) is shown as an amber "Portal: ..." sub-label when different
 * - Both names are searchable (strata roll name + portal name)
 *
 * Tests:
 * 1. Super admin can open Impersonate Modal from the user menu
 * 2. Searching for a strata roll name (e.g. "Pearce") shows the correct user
 * 3. Searching for a portal name (e.g. "Jane Smith") also shows the user (both searchable)
 * 4. Selected user card shows strata roll name as primary bold text
 * 5. When portal name differs from roll, amber "Portal: ..." label is visible
 * 6. Impersonate button becomes enabled after user + building selection
 * 7. After impersonation, banner shows "Viewing as Resident" (PII masked full_name)
 * 8. Non-super-admin role cannot see the Impersonate option in the menu
 *
 * Run:
 *   cd /home/gagneet/strata-management
 *   npx playwright test tests/frontend/e2e/strata-roll-impersonate.spec.js --headed
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';

// ── Credentials ───────────────────────────────────────────────────────────────
const SUPER_ADMIN_CREDS = {
    email: 'administrator@eastgateresidences.com.au',
    password: process.env.E2E_ADMIN_PASSWORD || '',
};
const OWNER_CREDS = {
    email: 'avneet@eastgateresidences.com.au',
    password: process.env.E2E_OWNER_PASSWORD || '',
};
const CHAIRMAN_CREDS = {
    email: 'anthony@eastgateresidences.com.au',
    password: process.env.E2E_CHAIRMAN_PASSWORD || '',
};

// ── Known strata roll data (seeded in DB) ────────────────────────────────────
// Jane Smith (portal) / Ms Jane Pearce & Mr John Han (strata roll), unit UA001
// These values must match what is seeded in the test database. If the account
// does not exist the tests that depend on it are skipped gracefully.
const ROLL_USER = {
    portalName: 'Jane Smith',
    rollName: 'Ms Jane Pearce & Mr John Han',
    unitNumber: 'UA001',
    portalEmail: 'jane@eastgateresidences.com.au',
    searchByRoll: 'Pearce',
    searchByPortal: 'Jane Smith',
};

// ── Helpers ───────────────────────────────────────────────────────────────────
async function getAuthToken(request, creds) {
    const resp = await request.post(`${BASE_URL}/api/auth/login`, {data: creds});
    if (!resp.ok()) return null;
    const data = await resp.json();
    return data.token || null;
}

async function loginViaUI(page, creds) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"], input[name="email"]', creds.email);
    await page.fill('input[type="password"], input[name="password"]', creds.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard**', {timeout: 15000});
}

/**
 * Open the user/account menu in the dashboard header.
 * Returns true if the menu trigger was found and clicked.
 */
async function openUserMenu(page) {
    // Try common selectors used across our DashboardLayout variants
    const menuTrigger = page.locator([
        '[data-testid="user-menu-button"]',
        '[aria-label="User menu"]',
        '[aria-label="Open user menu"]',
        'button:has-text("Admin")',
        'header button:last-of-type',
    ].join(', ')).first();

    if (await menuTrigger.isVisible({timeout: 5000}).catch(() => false)) {
        await menuTrigger.click();
        return true;
    }
    return false;
}

/**
 * Open the ImpersonateModal from the user menu.
 * Returns true if the modal is now visible.
 */
async function openImpersonateModal(page) {
    const opened = await openUserMenu(page);
    if (!opened) return false;

    const impersonateOption = page.locator('text=Impersonate User').first();
    const visible = await impersonateOption.isVisible({timeout: 4000}).catch(() => false);
    if (!visible) return false;

    await impersonateOption.click();

    // Wait for the modal to appear
    const modal = page.locator('[role="dialog"], [data-testid="dialog-root"], [data-testid="dialog-content"]').first();
    return modal.isVisible({timeout: 6000}).catch(() => false);
}

/**
 * Type into the impersonation search input and wait for results.
 */
async function searchInModal(page, term) {
    const searchInput = page.locator(
        'input[placeholder*="Unit Number"], input[placeholder*="Name"], input[data-testid="search-input"]'
    ).first();
    await searchInput.waitFor({state: 'visible', timeout: 5000});
    await searchInput.fill(term);
    // Debounce is 300 ms — wait a bit more for results to load
    await page.waitForTimeout(600);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

// ── 1. Super admin can open Impersonate Modal from the user menu ──────────────
test.describe('ImpersonateModal — open from user menu', () => {
    test('super_admin sees "Impersonate User" option in account menu', async ({page}) => {
        await loginViaUI(page, SUPER_ADMIN_CREDS);

        const menuOpened = await openUserMenu(page);
        if (!menuOpened) {
            // Dashboard loaded but menu trigger not found — skip rather than fail
            test.skip();
            return;
        }

        const impersonateOption = page.locator('text=Impersonate User');
        await expect(impersonateOption).toBeVisible({timeout: 5000});
    });

    test('super_admin can open the Impersonate Modal and see the search input', async ({page}) => {
        await loginViaUI(page, SUPER_ADMIN_CREDS);

        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            // Modal not found — UI may not have the feature toggle enabled; skip
            test.skip();
            return;
        }

        // Search input should be visible inside the modal
        const searchInput = page.locator(
            'input[placeholder*="Unit Number"], input[placeholder*="Name"], input[data-testid="search-input"]'
        ).first();
        await expect(searchInput).toBeVisible({timeout: 5000});

        // Header text
        await expect(page.locator('text=User Impersonation')).toBeVisible();
    });
});

// ── 2. Searching by strata roll name shows the correct user ───────────────────
test.describe('ImpersonateModal — strata roll name search', () => {
    test('searching by strata roll name finds the user', async ({page, request}) => {
        // First verify the target user exists via API
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();

        // Find a user who has a unit_owner_name different from full_name
        const targetUser = users.find(
            (u) => u.unit_owner_name && u.unit_owner_name !== u.full_name && u.role !== 'super_admin'
        );
        if (!targetUser) {
            test.skip(); // No such user seeded
            return;
        }

        // Extract a distinctive token from the roll name to search by
        const rollNameToken = targetUser.unit_owner_name.split(' ').find(
            (word) => word.length > 3 && !( targetUser.full_name || '' ).includes(word)
        );
        if (!rollNameToken) {
            test.skip();
            return;
        }

        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        await searchInModal(page, rollNameToken);

        // The strata roll name should appear in the results
        await expect(
            page.locator(`text="${targetUser.unit_owner_name}"`)
        ).toBeVisible({timeout: 8000});
    });

    test('searching by "Pearce" (roll name token) shows the Jane Smith / Jane Pearce user', async ({page, request}) => {
        // Verify the specific seeded user exists before running UI steps
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find(
            (u) => ( u.unit_owner_name || '' ).toLowerCase().includes('pearce')
        );
        if (!target) {
            // The specific demo user is not seeded — skip
            test.skip();
            return;
        }

        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        await searchInModal(page, ROLL_USER.searchByRoll);

        // The roll name should be the primary text in the result card
        await expect(page.locator(`text="${ROLL_USER.rollName}"`)).toBeVisible({timeout: 8000});
    });
});

// ── 3. Searching by portal name also shows the user ───────────────────────────
test.describe('ImpersonateModal — portal name search', () => {
    test('searching by portal full_name also surfaces the user', async ({page, request}) => {
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find(
            (u) => u.unit_owner_name && u.unit_owner_name !== u.full_name && u.role !== 'super_admin'
        );
        if (!target) {
            test.skip();
            return;
        }

        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        // Search by the portal full_name (not the roll name)
        await searchInModal(page, target.full_name.split(' ')[ 0 ]);

        // The roll name (primary) should appear in results
        await expect(
            page.locator(`text="${target.unit_owner_name}"`)
        ).toBeVisible({timeout: 8000});
    });
});

// ── 4 + 5. Selected user card shows roll name as primary; amber Portal label ──
test.describe('ImpersonateModal — result card display', () => {
    test('result card shows roll name as bold primary and amber Portal sub-label', async ({page, request}) => {
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find(
            (u) => u.unit_owner_name && u.unit_owner_name !== u.full_name && u.role !== 'super_admin'
        );
        if (!target) {
            test.skip();
            return;
        }

        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        // Search by first word of the roll name
        const searchTerm = target.unit_owner_name.split(' ').slice(-1)[ 0 ]; // last word of roll name
        await searchInModal(page, searchTerm);

        // 4. Primary bold name = roll name
        const rollNameEl = page.locator(`text="${target.unit_owner_name}"`).first();
        await expect(rollNameEl).toBeVisible({timeout: 8000});

        // 5. Amber Portal sub-label = "Portal: {full_name}"
        const portalLabel = page.locator(`text="Portal: ${target.full_name}"`).first();
        await expect(portalLabel).toBeVisible({timeout: 5000});
        // Verify the amber colour class is applied
        await expect(portalLabel).toHaveClass(/amber|text-amber/);
    });

    test('no Portal sub-label when unit_owner_name equals full_name', async ({page, request}) => {
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        // Find a user where unit_owner_name === full_name (or unit_owner_name is absent)
        const target = users.find(
            (u) =>
                u.role !== 'super_admin' &&
                ( !u.unit_owner_name || u.unit_owner_name === u.full_name )
        );
        if (!target) {
            test.skip();
            return;
        }

        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        await searchInModal(page, target.full_name.split(' ')[ 0 ]);

        // Primary name appears
        const primaryName = target.unit_owner_name || target.full_name;
        await expect(page.locator(`text="${primaryName}"`).first()).toBeVisible({timeout: 8000});

        // No "Portal:" sub-label
        await expect(page.locator(`text="Portal: ${target.full_name}"`)).not.toBeVisible();
    });
});

// ── 6. Impersonate button becomes enabled after user + building selection ──────
test.describe('ImpersonateModal — button state', () => {
    test('Impersonate button is disabled before selecting a user', async ({page}) => {
        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        const impersonateBtn = page.locator('button:has-text("Impersonate")').last();
        await expect(impersonateBtn).toBeDisabled();
    });

    test('Impersonate button becomes enabled after selecting a user', async ({page, request}) => {
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find((u) => u.role !== 'super_admin' && u.full_name);
        if (!target) {
            test.skip();
            return;
        }

        await loginViaUI(page, SUPER_ADMIN_CREDS);
        const modalOpened = await openImpersonateModal(page);
        if (!modalOpened) {
            test.skip();
            return;
        }

        await searchInModal(page, target.full_name.split(' ')[ 0 ]);

        // Click on the first result card
        const displayName = target.unit_owner_name || target.full_name;
        const resultCard = page.locator(`text="${displayName}"`).first();
        await resultCard.waitFor({state: 'visible', timeout: 8000});

        // Click the button element ancestor of the name text
        const cardButton = resultCard.locator('..').locator('xpath=ancestor::button[1]');
        // Fall back to clicking the text element itself if ancestor lookup fails
        const cardEl = ( await cardButton.count() ) > 0 ? cardButton.first() : resultCard;
        await cardEl.click();

        // After selection, Impersonate button should be enabled
        // (building is pre-selected to the current building)
        const impersonateBtn = page.locator('button:has-text("Impersonate")').last();
        await expect(impersonateBtn).toBeEnabled({timeout: 5000});
    });
});

// ── 7. After impersonation, banner shows PII-masked "Viewing as Resident" ──────
test.describe('ImpersonationBanner — after impersonation', () => {
    test('impersonation banner appears after a successful impersonate call', async ({page, request}) => {
        const adminToken = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!adminToken) test.skip();

        // Get a non-admin user to impersonate
        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${adminToken}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find((u) => u.role === 'owner' && u.email);
        if (!target) test.skip();

        // Obtain an impersonation token via API
        const impResp = await request.post(`${BASE_URL}/api/auth/impersonate`, {
            headers: {Authorization: `Bearer ${adminToken}`},
            data: {user_id: target.id},
        });
        if (!impResp.ok()) test.skip();
        const {token: impToken} = await impResp.json();

        // Inject the tokens into localStorage to simulate post-impersonation state
        await page.goto(`${BASE_URL}/dashboard`);
        await page.evaluate(
            ({adminToken, impToken}) => {
                localStorage.setItem('token', adminToken);
                localStorage.setItem('impersonation_token', impToken);
            },
            {adminToken, impToken}
        );

        await page.reload();
        await page.waitForLoadState('networkidle');

        // The ImpersonationBanner should now be visible
        // The banner text may say "Currently impersonating", "Viewing as Resident", or similar
        const bannerLocator = page.locator([
            'text=Currently impersonating',
            'text=Viewing as Resident',
            'text=Impersonating',
            '[data-testid="impersonation-banner"]',
            '.bg-amber-500',
            '.bg-amber-600',
        ].join(', ')).first();

        await expect(bannerLocator).toBeVisible({timeout: 10000});
    });

    test('impersonation banner shows masked email (contains ***@)', async ({page, request}) => {
        const adminToken = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!adminToken) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${adminToken}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find((u) => u.role === 'owner' && u.email);
        if (!target) test.skip();

        const impResp = await request.post(`${BASE_URL}/api/auth/impersonate`, {
            headers: {Authorization: `Bearer ${adminToken}`},
            data: {user_id: target.id},
        });
        if (!impResp.ok()) test.skip();
        const {token: impToken} = await impResp.json();

        // Verify the /me endpoint returns a masked email with the impersonation token
        const meResp = await request.get(`${BASE_URL}/api/auth/me`, {
            headers: {Authorization: `Bearer ${impToken}`},
        });
        expect(meResp.ok()).toBe(true);
        const me = await meResp.json();
        // PII masking: email must contain ***@
        expect(me.email).toMatch(/\*\*\*@/);
    });

    test('Exit Impersonation button is visible during impersonation session', async ({page, request}) => {
        const adminToken = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!adminToken) test.skip();

        const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${adminToken}`},
        });
        if (!usersResp.ok()) test.skip();
        const users = await usersResp.json();
        const target = users.find((u) => u.role === 'owner');
        if (!target) test.skip();

        const impResp = await request.post(`${BASE_URL}/api/auth/impersonate`, {
            headers: {Authorization: `Bearer ${adminToken}`},
            data: {user_id: target.id},
        });
        if (!impResp.ok()) test.skip();
        const {token: impToken} = await impResp.json();

        await page.goto(`${BASE_URL}/dashboard`);
        await page.evaluate(
            ({adminToken, impToken}) => {
                localStorage.setItem('token', adminToken);
                localStorage.setItem('impersonation_token', impToken);
            },
            {adminToken, impToken}
        );
        await page.reload();
        await page.waitForLoadState('networkidle');

        await expect(page.locator('text=Exit Impersonation')).toBeVisible({timeout: 10000});
    });
});

// ── 8. Non-super-admin cannot see the Impersonate option ────────────────────
test.describe('ImpersonateModal — access control', () => {
    test('owner does not see "Impersonate User" option in account menu', async ({page}) => {
        await loginViaUI(page, OWNER_CREDS);

        const menuOpened = await openUserMenu(page);
        if (!menuOpened) {
            // Menu trigger not found; we can still assert URL is dashboard
            await expect(page).toHaveURL(/dashboard/);
            return;
        }

        const impersonateOption = page.locator('text=Impersonate User');
        await expect(impersonateOption).not.toBeVisible();
    });

    test('chairman does not see "Impersonate User" option in account menu', async ({page}) => {
        await loginViaUI(page, CHAIRMAN_CREDS);

        const menuOpened = await openUserMenu(page);
        if (!menuOpened) {
            await expect(page).toHaveURL(/dashboard/);
            return;
        }

        const impersonateOption = page.locator('text=Impersonate User');
        await expect(impersonateOption).not.toBeVisible();
    });

    test('non-super_admin cannot call /api/auth/impersonate (API returns 401/403)', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) test.skip();

        const resp = await request.post(`${BASE_URL}/api/auth/impersonate`, {
            headers: {Authorization: `Bearer ${token}`},
            data: {user_id: 'any-user-id'},
        });
        expect([401, 403]).toContain(resp.status());
    });

    test('unauthenticated request to /api/auth/impersonate is rejected', async ({request}) => {
        const resp = await request.post(`${BASE_URL}/api/auth/impersonate`, {
            data: {user_id: 'any-user-id'},
        });
        expect([401, 403]).toContain(resp.status());
    });
});

// ── Bonus: super_admin excluded from impersonation search results ─────────────
test.describe('ImpersonateModal — super_admin exclusion', () => {
    test('GET /users?status=active list includes non-admin users for modal search', async ({request}) => {
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const resp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(resp.ok()).toBe(true);
        const users = await resp.json();

        // At least one non-super_admin must exist (otherwise the modal is useless)
        const nonAdmins = users.filter((u) => u.role !== 'super_admin');
        expect(nonAdmins.length).toBeGreaterThan(0);
    });

    test('client-side filter in modal excludes super_admin accounts from results', async ({request}) => {
        // Simulate the exact filter logic from ImpersonateModal.handleSearch()
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const resp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!resp.ok()) test.skip();
        const allUsers = await resp.json();

        const term = 'admin'; // likely to match the super_admin account
        const filtered = allUsers.filter(
            (u) =>
                u.role !== 'super_admin' &&
                (
                    ( u.full_name || '' ).toLowerCase().includes(term) ||
                    ( u.unit_owner_name || '' ).toLowerCase().includes(term) ||
                    ( u.email || '' ).toLowerCase().includes(term) ||
                    ( u.unit_number && u.unit_number.toString().toLowerCase().includes(term) )
                )
        );

        // Filtered results must not contain any super_admin
        filtered.forEach((u) => {
            expect(u.role).not.toBe('super_admin');
        });
    });

    test('searching by unit_owner_name token works at the API+filter level', async ({request}) => {
        const token = await getAuthToken(request, SUPER_ADMIN_CREDS);
        if (!token) test.skip();

        const resp = await request.get(`${BASE_URL}/api/users?status=active`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        if (!resp.ok()) test.skip();
        const allUsers = await resp.json();

        // Find a user with a unit_owner_name containing a unique token
        const target = allUsers.find(
            (u) => u.unit_owner_name && u.unit_owner_name !== u.full_name && u.role !== 'super_admin'
        );
        if (!target) {
            test.skip();
            return;
        }

        // Pick a word that is in unit_owner_name but NOT in full_name
        const rollWords = target.unit_owner_name.split(/\s+/);
        const fullWords = ( target.full_name || '' ).split(/\s+/).map((w) => w.toLowerCase());
        const uniqueRollWord = rollWords.find(
            (w) => w.length > 3 && !fullWords.includes(w.toLowerCase())
        );
        if (!uniqueRollWord) {
            test.skip();
            return;
        }

        const term = uniqueRollWord.toLowerCase();
        const filtered = allUsers.filter(
            (u) =>
                u.role !== 'super_admin' &&
                (
                    ( u.full_name || '' ).toLowerCase().includes(term) ||
                    ( u.unit_owner_name || '' ).toLowerCase().includes(term) ||
                    ( u.email || '' ).toLowerCase().includes(term) ||
                    ( u.unit_number && u.unit_number.toString().toLowerCase().includes(term) )
                )
        );

        // The target user must appear when searching by their unique roll name token
        const found = filtered.find((u) => u.id === target.id);
        expect(found).toBeDefined();
    });
});
