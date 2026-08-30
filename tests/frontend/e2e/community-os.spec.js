/**
 * Community OS — E2E Playwright Tests (S1–S5)
 *
 * Covers:
 *   S1 — Smart Requests / Comms Intake  (POST /api/engagement/requests/smart)
 *   S2 — Workflow Governance             (GET  /api/workflows/status)
 *   S3 — Portfolio Operations            (GET  /api/portfolio/dashboard)
 *   S4 — Financial Transparency          (GET  /api/engagement/property-finance/:unit)
 *   S5 — Attention Architecture          (GET  /api/engagement/morning-card,
 *                                         GET  /api/engagement/building-pulse,
 *                                         GET  /api/engagement/nav/badges)
 *
 * Auth:
 *   - Owner  : avneet@eastgateresidences.com.au / $E2E_OWNER_PASSWORD
 *   - Admin  : anthony@eastgateresidences.com.au / $E2E_CHAIRMAN_PASSWORD
 *   - Manager: super@eastgateresidences.com.au  / SuperAdmin123! (super_admin)
 */

const {test, expect} = require('@playwright/test');
const {registerCleanup} = require('../cleanup-registry');
const {getTestRunId} = require('../test-run-id');

const TEST_RUN_ID = getTestRunId();
const SUPER_ADMIN_CREDS = {
    email: 'administrator@strataos.live',
    password: process.env.E2E_ADMIN_PASSWORD || '',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8003';

async function loginAs(request, email, password) {
    const res = await request.post(`${BASE_URL}/api/auth/login`, {
        data: {email, password},
    });
    if (res.ok()) {
        const body = await res.json();
        return body.access_token || body.token;
    }

    const adminLogin = await request.post(`${BASE_URL}/api/auth/login`, {
        data: SUPER_ADMIN_CREDS,
    });
    if (!adminLogin.ok()) {
        throw new Error(`Login failed for ${email}: ${res.status()} ${await res.text()}`);
    }
    const adminBody = await adminLogin.json();
    const adminToken = adminBody.access_token || adminBody.token;

    const usersResp = await request.get(`${BASE_URL}/api/users?status=active`, {
        headers: {
            Authorization: `Bearer ${adminToken}`,
            'X-Building-ID': '13195',
        },
    });
    if (!usersResp.ok()) {
        throw new Error(`Unable to resolve impersonation target for ${email}: ${usersResp.status()} ${await usersResp.text()}`);
    }

    const users = await usersResp.json();
    const target = users.find(u => u.email === email) || users.find(u => u.role === 'owner' && email.includes('avneet'))
        || users.find(u => u.role === 'ec_member' && email.includes('anthony'));
    if (!target) {
        throw new Error(`No impersonation target found for ${email}`);
    }

    const impResp = await request.post(`${BASE_URL}/api/auth/impersonate`, {
        headers: {Authorization: `Bearer ${adminToken}`},
        data: {
            user_id: target.id,
            building_id: target.building_id || target.buildingId || '13195',
        },
    });
    if (!impResp.ok()) {
        throw new Error(`Impersonation failed for ${email}: ${impResp.status()} ${await impResp.text()}`);
    }

    const impBody = await impResp.json();
    return impBody.access_token || impBody.token;
}

// ---------------------------------------------------------------------------
// S5 — Morning Card
// ---------------------------------------------------------------------------

test.describe('S5 — Morning Card', () => {
    test('GET /engagement/morning-card returns a card dict for an owner', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/engagement/morning-card`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(200);
        const body = await res.json();
        // Must be an object (card or empty dict)
        expect(typeof body).toBe('object');
        expect(body).not.toBeNull();
    });

    test('GET /engagement/morning-card requires auth', async ({request}) => {
        const res = await request.get(`${BASE_URL}/api/engagement/morning-card`);
        expect([401, 403, 422]).toContain(res.status());
    });
});

// ---------------------------------------------------------------------------
// S5 — Building Pulse
// ---------------------------------------------------------------------------

test.describe('S5 — Building Pulse', () => {
    test('GET /engagement/building-pulse returns a list', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/engagement/building-pulse`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(200);
        const body = await res.json();
        // building-pulse returns either an array or an object with an events/activities key
        expect(Array.isArray(body) || typeof body === 'object').toBeTruthy();
    });

    test('GET /engagement/building-pulse requires auth', async ({request}) => {
        const res = await request.get(`${BASE_URL}/api/engagement/building-pulse`);
        expect([401, 403, 422]).toContain(res.status());
    });
});

// ---------------------------------------------------------------------------
// S5 — Nav Badges
// ---------------------------------------------------------------------------

test.describe('S5 — Nav Badges', () => {
    test('GET /engagement/nav/badges returns badge counts', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/engagement/nav/badges`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(200);
        const body = await res.json();
        expect(typeof body).toBe('object');
        // Must include numeric badge fields
        expect(typeof body.notifications).toBe('number');
    });

    test('GET /engagement/nav/badges requires auth', async ({request}) => {
        const res = await request.get(`${BASE_URL}/api/engagement/nav/badges`);
        expect([401, 403, 422]).toContain(res.status());
    });
});

// ---------------------------------------------------------------------------
// S1 — Smart Requests (Comms Intake)
// ---------------------------------------------------------------------------

test.describe('S1 — Smart Requests', () => {
    test('POST /engagement/requests/smart creates a workflow request', async ({request}) => {
        const token = await loginAs(request, SUPER_ADMIN_CREDS.email, SUPER_ADMIN_CREDS.password);
        const res = await request.post(`${BASE_URL}/api/engagement/requests/smart`, {
            headers: {
                Authorization: `Bearer ${token}`,
                'X-Building-ID': '13195',
            },
            data: {
                subject: `E2E Test: Noise from upstairs [${TEST_RUN_ID}]`,
                body: `There has been loud music from the unit above every night this week. [${TEST_RUN_ID}]`,
                source_channel: 'web_portal',
                is_test_data: true,
            },
        });
        expect([200, 201]).toContain(res.status());
        const body = await res.json();
        expect(body).toHaveProperty('id');
        expect(body).toHaveProperty('request_number');
        expect(body.request_type).toBeDefined();
        registerCleanup({collection: 'workflow_requests', field: 'id', value: body.id});
    });

    test('POST /engagement/requests/smart auto-classifies as noise_complaint', async ({request}) => {
        const token = await loginAs(request, SUPER_ADMIN_CREDS.email, SUPER_ADMIN_CREDS.password);
        const res = await request.post(`${BASE_URL}/api/engagement/requests/smart`, {
            headers: {
                Authorization: `Bearer ${token}`,
                'X-Building-ID': '13195',
            },
            data: {
                subject: `Noise complaint from neighbour [${TEST_RUN_ID}]`,
                body: `Constant noise, loud parties every weekend. [${TEST_RUN_ID}]`,
                source_channel: 'web_portal',
                is_test_data: true,
            },
        });
        expect([200, 201]).toContain(res.status());
        const body = await res.json();
        expect(body.request_type).toBe('noise_complaint');
        registerCleanup({collection: 'workflow_requests', field: 'id', value: body.id});
    });

    test('POST /engagement/requests/smart requires auth', async ({request}) => {
        const res = await request.post(`${BASE_URL}/api/engagement/requests/smart`, {
            data: {subject: 'Test', body: 'Test body', source_channel: 'web_portal'},
        });
        expect([401, 403, 422]).toContain(res.status());
    });

    test('GET /engagement/requests returns owner-scoped list', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/engagement/requests`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(200);
        const body = await res.json();
        expect(Array.isArray(body)).toBeTruthy();
    });

    test('GET /engagement/triage requires manager role — owner gets 403', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/engagement/triage`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(403);
    });
});

// ---------------------------------------------------------------------------
// S2 — Workflow Governance
// ---------------------------------------------------------------------------

test.describe('S2 — Workflow Governance', () => {
    test('GET /workflows/status returns list with health field (manager)', async ({request}) => {
        const token = await loginAs(request, 'anthony@eastgateresidences.com.au', process.env.E2E_CHAIRMAN_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/workflows/status`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(200);
        const body = await res.json();
        expect(typeof body).toBe('object');
        // May be array or {workflows:[...]}
        const workflows = Array.isArray(body) ? body : body.workflows;
        if (Array.isArray(workflows) && workflows.length > 0) {
            expect(workflows[ 0 ]).toHaveProperty('health');
        }
    });

    test('GET /workflows/status requires manager role — tenant gets 403', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/workflows/status`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        // Owner/tenant should not access governance dashboard
        expect([403, 401]).toContain(res.status());
    });

    test('GET /workflows/catalogue requires super_admin', async ({request}) => {
        // Chairman (non-super-admin) should get 403
        const token = await loginAs(request, 'anthony@eastgateresidences.com.au', process.env.E2E_CHAIRMAN_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/workflows/catalogue`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect([403, 404]).toContain(res.status());
    });
});

// ---------------------------------------------------------------------------
// S3 — Portfolio Operations
// ---------------------------------------------------------------------------

test.describe('S3 — Portfolio Operations', () => {
    test('GET /portfolio/dashboard requires manager role', async ({request}) => {
        const ownerToken = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/portfolio/dashboard`, {
            headers: {Authorization: `Bearer ${ownerToken}`},
        });
        expect([403, 401]).toContain(res.status());
    });

    test('GET /portfolio/onboarding/template returns template steps', async ({request}) => {
        const token = await loginAs(request, 'anthony@eastgateresidences.com.au', process.env.E2E_CHAIRMAN_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/portfolio/onboarding/template`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(200);
        const body = await res.json();
        expect(body).toHaveProperty('steps');
        expect(Array.isArray(body.steps)).toBeTruthy();
    });

    test('GET /portfolio/buildings requires manager role', async ({request}) => {
        const ownerToken = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/portfolio/buildings`, {
            headers: {Authorization: `Bearer ${ownerToken}`},
        });
        expect([403, 401]).toContain(res.status());
    });
});

// ---------------------------------------------------------------------------
// S4 — Financial Transparency (property finance)
// ---------------------------------------------------------------------------

test.describe('S4 — Property Finance', () => {
    test('GET /engagement/property-finance/:unit returns finance data for own unit', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        const res = await request.get(`${BASE_URL}/api/engagement/property-finance/TH017`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        // Either 200 with data or 404 if unit not found, but NOT 403
        expect([200, 404]).toContain(res.status());
    });

    test('GET /engagement/property-finance/:unit blocks cross-unit access for owner', async ({request}) => {
        const token = await loginAs(request, 'avneet@eastgateresidences.com.au', process.env.E2E_OWNER_PASSWORD || '');
        // TH017 owner trying to access UA063 (Anthony's unit)
        const res = await request.get(`${BASE_URL}/api/engagement/property-finance/UA063`, {
            headers: {Authorization: `Bearer ${token}`},
        });
        expect(res.status()).toBe(403);
    });
});

// ---------------------------------------------------------------------------
// Email Ingestion (S1 — webhook)
// ---------------------------------------------------------------------------

test.describe('Email Ingestion Webhook', () => {
    test('POST /ingestion/email/inbound rejects missing signature', async ({request}) => {
        const res = await request.post(`${BASE_URL}/api/ingestion/email/inbound`, {
            data: {recipient: 'requests@eastgateresidences.com.au', subject: 'Test', body: 'Test'},
        });
        // No valid Mailgun signature → 401 or 400
        expect([400, 401, 403, 422]).toContain(res.status());
    });
});
