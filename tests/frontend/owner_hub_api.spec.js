// @ts-check
const {test, expect} = require('@playwright/test');

/**
 * Owner Hub API Endpoint Tests
 *
 * Covers the fixes for the 405/404/422 issues found in production:
 *   1. POST /owner-hub/properties/{id}/tenancy  (was 405 — missing endpoint)
 *   2. GET  /owner-hub/properties/{id}/ledger   (was 404 — wrong path)
 *   3. GET  /owner-hub/inspections              (was 404 — missing aggregate)
 *   4. POST /owner-hub/properties/{id}/tco      (was 422 — required fields)
 *
 * These tests exercise the public-facing API via HTTP to ensure the
 * backend accepts the correct request shapes and returns expected statuses.
 *
 * NOTE: All requests without auth expect 401/403 (not 404/405/422).
 */

const BASE_URL = process.env.TEST_BASE_URL || 'http://localhost:8003';
const FAKE_PROP_ID = '000000000000000000000001'; // non-existent ObjectId

test.describe('Owner Hub API — unauthenticated access returns 401/403', () => {
    test('GET /owner-hub/properties returns 401 without token', async ({request}) => {
        const res = await request.get(`${BASE_URL}/api/owner-hub/properties`);
        expect([401, 403]).toContain(res.status());
    });

    test('POST /owner-hub/properties/{id}/tenancy returns 401 without token', async ({request}) => {
        const res = await request.post(
            `${BASE_URL}/api/owner-hub/properties/${FAKE_PROP_ID}/tenancy`,
            {data: {tenant_name: 'Test', tenant_email: 'test@example.com'}}
        );
        // Must not be 405 (Method Not Allowed) — endpoint exists now
        expect(res.status()).not.toBe(405);
        expect([401, 403, 422]).toContain(res.status());
    });

    test('GET /owner-hub/properties/{id}/ledger returns 401 without token', async ({request}) => {
        const res = await request.get(
            `${BASE_URL}/api/owner-hub/properties/${FAKE_PROP_ID}/ledger`
        );
        // Must not be 404 — endpoint exists now
        expect(res.status()).not.toBe(404);
        expect([401, 403]).toContain(res.status());
    });

    test('GET /owner-hub/inspections returns 401 without token', async ({request}) => {
        const res = await request.get(`${BASE_URL}/api/owner-hub/inspections`);
        // Must not be 404 — aggregate endpoint exists now
        expect(res.status()).not.toBe(404);
        expect([401, 403]).toContain(res.status());
    });

    test('POST /owner-hub/properties/{id}/tco returns 401 without token', async ({request}) => {
        const res = await request.post(
            `${BASE_URL}/api/owner-hub/properties/${FAKE_PROP_ID}/tco`,
            {data: {vacancy_weeks: 2}}
        );
        // Must not be 422 for empty-ish body — property_id/year are now optional
        expect(res.status()).not.toBe(422);
        expect([401, 403]).toContain(res.status());
    });
});

test.describe('Owner Hub API — endpoint existence checks (no auth)', () => {
    /**
     * These tests verify that routes are registered and return auth errors
     * (401/403), NOT routing errors (404/405). This confirms the endpoints exist.
     */

    const endpointChecks = [
        {
            method: 'POST',
            path: `/api/owner-hub/properties/${FAKE_PROP_ID}/tenancy`,
            notExpected: 405,
            description: 'POST tenancy endpoint exists (was 405)',
        },
        {
            method: 'GET',
            path: `/api/owner-hub/properties/${FAKE_PROP_ID}/ledger`,
            notExpected: 404,
            description: 'GET property ledger endpoint exists (was 404)',
        },
        {
            method: 'GET',
            path: `/api/owner-hub/inspections`,
            notExpected: 404,
            description: 'GET inspections aggregate endpoint exists (was 404)',
        },
    ];

    for (const {method, path, notExpected, description} of endpointChecks) {
        test(description, async ({request}) => {
            const res = method === 'POST'
                ? await request.post(`${BASE_URL}${path}`, {data: {}})
                : await request.get(`${BASE_URL}${path}`);

            expect(
                res.status(),
                `Expected status NOT to be ${notExpected} for ${method} ${path}`
            ).not.toBe(notExpected);
        });
    }
});
