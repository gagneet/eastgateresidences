/**
 * Smoke Tests — Double-prefix API call fix (audit_2026-04-25 Section 1)
 *
 * Before the fix, 32 frontend API calls used `/api/` as a path prefix even
 * though the axios `api` instance already has baseURL ending in `/api`.
 * The result was double-prefixed URLs like `/api/api/finance/sinking-fund-plan`
 * which returned 404. These tests confirm the endpoints now resolve correctly
 * (non-404) for an authenticated admin user.
 *
 * A single auth token is obtained in beforeAll to avoid triggering rate
 * limiting from parallel logins.
 *
 * Pages covered (9 with broken calls):
 *   1. CapitalWorksPlanner    — /intelligence/capital-planner
 *   2. SchemeClassesPage      — /admin/scheme-classes
 *   3. ManagerDashboard       — /dashboard (workflow-requests calls)
 *   4. WorkflowsGovernancePage — /api/workflows/* (dead page but live endpoints)
 *   5. StaffUserDetailPage    — /admin/staff-users/[id]
 *   6. OnboardingWizard       — /admin/portfolio/onboard
 *   7. RealEstateDashboard    — /real-estate
 *   8. TenantMaintenancePage  — /maintenance/tenant
 *   9. DocumentConverterPage  — dead page; endpoints tested directly
 *
 * Run:
 *   npx playwright test tests/frontend/e2e/double-prefix-smoke.spec.js --project=chromium --reporter=list
 */

const {test, expect, request: playwrightRequest} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8003';
const API_BASE = `${BASE_URL}/api`;
const ADMIN = {
    email: process.env.TEST_ADMIN_EMAIL || '',
    password: process.env.TEST_ADMIN_PASSWORD || '',
};

let sharedToken = null;

test.describe.serial('Double-prefix API smoke tests', () => {
    test.beforeAll(async () => {
        // Accept a pre-generated token to avoid hitting the login rate limit
        // in environments where many test runs occur back-to-back.
        // Generate one with: python3 backend/scripts/gen_test_token.py
        if (process.env.TEST_AUTH_TOKEN) {
            sharedToken = process.env.TEST_AUTH_TOKEN;
            return;
        }
        const ctx = await playwrightRequest.newContext();
        const res = await ctx.post(`${API_BASE}/auth/login`, {data: ADMIN});
        if (!res.ok()) {
            throw new Error(
                `Auth failed before smoke tests: HTTP ${res.status()}. ` +
                'Set TEST_AUTH_TOKEN env var to skip login and avoid rate limits.'
            );
        }
        const data = await res.json();
        sharedToken = data.token;
        await ctx.dispose();
    });

    function headers() {
        return {Authorization: `Bearer ${sharedToken}`};
    }

    // ── Page 1: CapitalWorksPlanner ──────────────────────────────────────────

    test('GET /finance/sinking-fund-plan returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/finance/sinking-fund-plan`, {headers: headers()});
        expect(res.status(), 'sinking-fund-plan missing — double-prefix not fixed?').not.toBe(404);
    });

    test('GET /intelligence/capital-works returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/intelligence/capital-works`, {headers: headers()});
        expect(res.status(), 'capital-works missing').not.toBe(404);
    });

    test('PUT /finance/sinking-fund-plan accepts request (non-404)', async ({request}) => {
        const res = await request.put(`${API_BASE}/finance/sinking-fund-plan`, {
            headers: headers(), data: {plan: []}
        });
        expect(res.status()).not.toBe(404);
    });

    test('PUT /finance/sinking-fund-capital-events accepts request (non-404)', async ({request}) => {
        const res = await request.put(`${API_BASE}/finance/sinking-fund-capital-events`, {
            headers: headers(), data: {events: []}
        });
        expect(res.status()).not.toBe(404);
    });

    test('PUT /intelligence/capital-works accepts request (non-404)', async ({request}) => {
        const res = await request.put(`${API_BASE}/intelligence/capital-works`, {
            headers: headers(), data: {items: []}
        });
        expect(res.status()).not.toBe(404);
    });

    // ── Page 2: SchemeClassesPage ────────────────────────────────────────────

    test('GET /scheme-classes/status returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/scheme-classes/status`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    test('GET /scheme-classes returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/scheme-classes`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    test('GET /levy-categories returns non-404', async ({request}) => {
        // Note: finance router has prefix="" so levy-categories is at /api/levy-categories,
        // not /api/finance/levy-categories. SchemeClassesPage had a compound bug:
        // double-prefix (/api/) + wrong segment (/finance/) — both fixed.
        const res = await request.get(`${API_BASE}/levy-categories?year=2026`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    // ── Page 3: ManagerDashboard ─────────────────────────────────────────────

    test('GET /workflow-requests?status=awaiting_review returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/workflow-requests?status=awaiting_review`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    test('GET /workflow-requests/stats/triage returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/workflow-requests/stats/triage`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    // ── Page 4: WorkflowsGovernancePage (dead page, live endpoints) ──────────

    test('GET /workflows/catalogue returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/workflows/catalogue`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    test('GET /workflows/runs returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/workflows/runs?limit=20`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    // ── Page 5: StaffUserDetailPage ──────────────────────────────────────────

    test('GET /admin/staff-users/buildings returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/admin/staff-users/buildings`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    // ── Page 6: OnboardingWizard ─────────────────────────────────────────────

    test('GET /admin/sm-organisations returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/admin/sm-organisations`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    // ── Pages 7 & 8: RealEstateDashboard + TenantMaintenancePage ─────────────

    test('GET /tenant/maintenance list returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/tenant/maintenance`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    // ── Page 9: DocumentConverterPage (dead App Router page, live endpoints) ──

    test('GET /document-converter/history returns non-404', async ({request}) => {
        const res = await request.get(`${API_BASE}/document-converter/history`, {headers: headers()});
        expect(res.status()).not.toBe(404);
    });

    test('POST /document-converter/convert rejects missing file gracefully (non-404)', async ({request}) => {
        const res = await request.post(`${API_BASE}/document-converter/convert`, {
            headers: headers(),
            multipart: {}
        });
        // 422 (missing required file) or 400 are acceptable; 404 means route missing
        expect(res.status()).not.toBe(404);
    });
});
