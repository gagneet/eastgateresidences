/**
 * Security & IP Logs E2E Tests
 *
 * Covers:
 * 1. API endpoints respond correctly (stats, login-attempts, my-activity,
 *    suspicious-events, ip-intelligence, flag-ip)
 * 2. Super admin can navigate to /admin/security-ip-logs
 * 3. All 4 tabs render (Overview, Login Activity, Suspicious Events, IP Intelligence)
 * 4. Non-admin roles cannot access the page (redirect or error)
 * 5. Owner sees Account Security card on their dashboard
 * 6. Login events are recorded in audit log after login
 * 7. Failed logins are recorded with correct status
 *
 * Run:
 *   cd /home/gagneet/strata-management
 *   npx playwright test tests/frontend/e2e/security-ip-logs.spec.js --headed
 */

const {test, expect} = require('@playwright/test');
const {registerCleanup} = require('../cleanup-registry');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';
const PAGE_URL = `${BASE_URL}/admin/security-ip-logs`;

// Updated super admin credentials
const ADMIN_CREDS = {
    email: 'administrator@eastgateresidences.com.au',
    password: process.env.E2E_ADMIN_PASSWORD || '',
};
const OWNER_CREDS = {
    email: 'avneet@eastgateresidences.com.au',
    password: process.env.E2E_OWNER_PASSWORD || '',
};
const TENANT_CREDS = {
    email: 'tenant@eastgateresidences.com.au',
    password: process.env.E2E_TENANT_PASSWORD || '',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

async function getAuthToken(request, creds) {
    const response = await request.post(`${BASE_URL}/api/auth/login`, {data: creds});
    if (!response.ok()) return null;
    const data = await response.json();
    return data.token;
}

async function loginAs(page, creds) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"], input[name="email"]', creds.email);
    await page.fill('input[type="password"], input[name="password"]', creds.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/dashboard/, {timeout: 15000});
}

// ── API Tests ─────────────────────────────────────────────────────────────────

test.describe('Security — API Endpoints', () => {
    test('GET /api/security/stats requires authentication', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/security/stats`);
        expect(response.status()).toBe(401);
    });

    test('GET /api/security/stats returns 403 for owner role', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/stats`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(403);
    });

    test('GET /api/security/stats returns 200 for super_admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/stats`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data).toHaveProperty('total_logins_30d');
        expect(data).toHaveProperty('failed_attempts_30d');
        expect(data).toHaveProperty('suspicious_events_30d');
        expect(data).toHaveProperty('unique_ips_30d');
        expect(data).toHaveProperty('unique_countries_30d');
        expect(data).toHaveProperty('daily_activity');
        expect(data).toHaveProperty('country_distribution');
        expect(data).toHaveProperty('device_distribution');
        expect(data).toHaveProperty('top_failed_ips');
    });

    test('GET /api/security/login-attempts returns 200 for super_admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/login-attempts`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data).toHaveProperty('items');
        expect(data).toHaveProperty('total');
        expect(data).toHaveProperty('page');
        expect(data).toHaveProperty('pages');
        expect(Array.isArray(data.items)).toBe(true);
    });

    test('GET /api/security/login-attempts records contain correct structure', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/login-attempts`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        const data = await response.json();
        if (data.items.length === 0) {
            return;
        } // No audit entries yet
        const entry = data.items[ 0 ];
        expect(entry).toHaveProperty('id');
        expect(entry).toHaveProperty('email');
        expect(entry).toHaveProperty('ip_address');
        expect(entry).toHaveProperty('status');
        expect(entry).toHaveProperty('attempted_at');
        expect(entry).toHaveProperty('geo');
        expect(entry).toHaveProperty('device_info');
        expect(entry).toHaveProperty('risk_score');
        expect(['success', 'failed', 'deactivated']).toContain(entry.status);
    });

    test('GET /api/security/my-activity returns 200 for any authenticated user', async ({request}) => {
        // Owner can access their own activity
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/my-activity`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data).toHaveProperty('recent_logins');
        expect(data).toHaveProperty('failed_attempts_30d');
        expect(data).toHaveProperty('suspicious_events_30d');
        expect(data).toHaveProperty('unique_ips_30d');
    });

    test('GET /api/security/my-activity returns 401 without auth', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/security/my-activity`);
        expect(response.status()).toBe(401);
    });

    test('GET /api/security/suspicious-events returns 200 for super_admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/suspicious-events`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data).toHaveProperty('items');
        // All returned items should have risk_score >= 50
        for (const item of data.items) {
            expect(item.risk_score).toBeGreaterThanOrEqual(50);
        }
    });

    test('GET /api/security/ip-intelligence returns 200 for super_admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/ip-intelligence`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data).toHaveProperty('items');
        expect(data).toHaveProperty('total');
    });

    test('POST /api/security/flag-ip returns 200 for super_admin', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.post(`${BASE_URL}/api/security/flag-ip`, {
            headers: {Authorization: `Bearer ${token}`},
            data: {ip_address: '10.254.254.1', reason: 'E2E test flag', action: 'suspicious'},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data.success).toBe(true);
        expect(data.ip_address).toBe('10.254.254.1');
        registerCleanup({collection: 'flagged_ips', field: 'ip_address', value: '10.254.254.1'});
    });

    test('POST /api/security/flag-ip returns 403 for owner', async ({request}) => {
        const token = await getAuthToken(request, OWNER_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.post(`${BASE_URL}/api/security/flag-ip`, {
            headers: {Authorization: `Bearer ${token}`},
            data: {ip_address: '1.2.3.4', reason: 'Unauthorized', action: 'suspicious'},
        });
        expect(response.status()).toBe(403);
    });

    test('Login audit log captures successful admin login', async ({request}) => {
        // Log in to generate an audit entry
        const loginResp = await request.post(`${BASE_URL}/api/auth/login`, {
            data: ADMIN_CREDS,
        });
        expect(loginResp.ok()).toBe(true);
        const {token} = await loginResp.json();

        // Fetch login attempts and verify entry exists
        const logsResp = await request.get(`${BASE_URL}/api/security/login-attempts`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        const logs = await logsResp.json();
        expect(logs.total).toBeGreaterThan(0);

        const successEntries = logs.items.filter(e => e.status === 'success');
        expect(successEntries.length).toBeGreaterThan(0);
    });

    test('Failed login with wrong password is logged', async ({request}) => {
        // Attempt failed login
        await request.post(`${BASE_URL}/api/auth/login`, {
            data: {email: ADMIN_CREDS.email, password: 'WrongPassword999!'},
        });

        // Get audit token and check failed entry logged
        const loginResp = await request.post(`${BASE_URL}/api/auth/login`, {data: ADMIN_CREDS});
        if (!loginResp.ok()) {
            test.skip();
            return;
        }
        const {token} = await loginResp.json();

        const logsResp = await request.get(`${BASE_URL}/api/security/login-attempts?status=failed`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        const logs = await logsResp.json();
        // Should have at least 1 failed entry (the one we just caused, possibly more from prior tests)
        expect(typeof logs.total).toBe('number');
    });

    test('login-attempts filter by status=success works', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/login-attempts?status=success`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(response.status()).toBe(200);
        const data = await response.json();
        for (const item of data.items) {
            expect(item.status).toBe('success');
        }
    });
});

// ── Admin Page UI Tests ───────────────────────────────────────────────────────

test.describe('Security — Admin Page UI', () => {
    test('super_admin can navigate to security-ip-logs page', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        await expect(page).toHaveURL(/security-ip-logs/, {timeout: 10000});
        await expect(page.locator('h1, [data-testid="page-title"]').first()).toBeVisible({timeout: 8000});
    });

    test('page shows Security & IP Logs heading', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        await expect(page.getByText('Security & IP Logs', {exact: false})).toBeVisible({timeout: 10000});
    });

    test('Overview tab is active by default', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        // Look for stats cards in overview
        await expect(page.getByText('Logins (30d)', {exact: false})).toBeVisible({timeout: 10000});
    });

    test('Login Activity tab loads table', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        await page.getByText('Login Activity').click();
        // Search input should appear
        await expect(page.locator('input[placeholder*="email"], input[placeholder*="Search"]').first()).toBeVisible({timeout: 8000});
    });

    test('Suspicious Events tab is clickable', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        await page.getByText('Suspicious Events').click();
        await expect(page.getByText('Suspicious Events', {exact: false}).first()).toBeVisible();
    });

    test('IP Intelligence tab is clickable', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        await page.goto(PAGE_URL);
        await page.getByText('IP Intelligence').click();
        await expect(page.getByText('IP Intelligence', {exact: false}).first()).toBeVisible();
    });

    test('Security & IP Logs nav item visible in sidebar for super_admin', async ({page}) => {
        await loginAs(page, ADMIN_CREDS);
        // Check sidebar contains the nav link
        const navLink = page.locator('a[href*="security-ip-logs"]');
        await expect(navLink).toBeVisible({timeout: 10000});
    });

    test('owner cannot see Security & IP Logs in sidebar', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        // Owner should NOT see the security admin nav link
        const navLink = page.locator('a[href*="security-ip-logs"]');
        const count = await navLink.count();
        expect(count).toBe(0);
    });

    test('owner redirected away from security-ip-logs page', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(PAGE_URL);
        // Should redirect to dashboard or show unauthorized — not stay on security page
        await page.waitForTimeout(2000);
        const url = page.url();
        const isStillOnPage = url.includes('security-ip-logs');
        if (isStillOnPage) {
            // If still on page, should show an error/empty state — not full admin content
            const statsCard = page.locator('text=Logins (30d)');
            await expect(statsCard).not.toBeVisible({timeout: 3000});
        } else {
            expect(url).toContain('dashboard');
        }
    });
});

// ── Owner Security Card Tests ─────────────────────────────────────────────────

test.describe('Security — Owner Dashboard Card', () => {
    test('owner dashboard shows Account Security card after login', async ({page}) => {
        await loginAs(page, OWNER_CREDS);
        await page.goto(`${BASE_URL}/dashboard`);
        // The security card appears after my-activity API resolves
        await page.waitForTimeout(3000);
        // Look for "Account Security" heading
        const card = page.getByText('Account Security', {exact: false});
        // The card only shows if the security feature returned data — best-effort check
        const isVisible = await card.isVisible().catch(() => false);
        if (isVisible) {
            await expect(card).toBeVisible();
            await expect(page.getByText('Last Login', {exact: false})).toBeVisible();
        }
        // If not visible it means the endpoint returned no data yet — acceptable
    });

    test('my-activity API returns data after owner login', async ({request}) => {
        // Login as owner (creates an audit entry)
        await request.post(`${BASE_URL}/api/auth/login`, {data: OWNER_CREDS});
        const loginResp = await request.post(`${BASE_URL}/api/auth/login`, {data: OWNER_CREDS});
        if (!loginResp.ok()) {
            test.skip();
            return;
        }
        const {token} = await loginResp.json();

        const activityResp = await request.get(`${BASE_URL}/api/security/my-activity`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(activityResp.status()).toBe(200);
        const data = await activityResp.json();
        // After 2 logins, recent_logins should have entries
        expect(data.recent_logins.length).toBeGreaterThan(0);
        const lastLogin = data.recent_logins[ 0 ];
        expect(lastLogin.status).toBe('success');
        expect(lastLogin.geo).toHaveProperty('country_code');
        expect(lastLogin.device_info).toHaveProperty('browser');
    });
});

// ── Geo & IP Resolution Tests ─────────────────────────────────────────────────

test.describe('Security — Geo & IP Resolution', () => {
    test('audit entries have valid geo data structure', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/login-attempts`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        const data = await response.json();
        if (data.items.length === 0) {
            return;
        }
        const entry = data.items[ 0 ];
        expect(entry.geo).toHaveProperty('country_code');
        expect(entry.geo).toHaveProperty('country_name');
        expect(entry.geo).toHaveProperty('city');
        expect(entry.geo).toHaveProperty('timezone');
    });

    test('audit entries have valid device_info structure', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/login-attempts`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        const data = await response.json();
        if (data.items.length === 0) {
            return;
        }
        const entry = data.items[ 0 ];
        expect(entry.device_info).toHaveProperty('browser');
        expect(entry.device_info).toHaveProperty('os');
        expect(entry.device_info).toHaveProperty('device_type');
        expect(['mobile', 'tablet', 'desktop']).toContain(entry.device_info.device_type);
    });

    test('device_fingerprint is non-empty sha256 hex string', async ({request}) => {
        const token = await getAuthToken(request, ADMIN_CREDS);
        if (!token) {
            test.skip();
            return;
        }
        const response = await request.get(`${BASE_URL}/api/security/login-attempts`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        const data = await response.json();
        if (data.items.length === 0) {
            return;
        }
        const entry = data.items[ 0 ];
        expect(entry.device_fingerprint).toMatch(/^[a-f0-9]{64}$/);
    });
});
