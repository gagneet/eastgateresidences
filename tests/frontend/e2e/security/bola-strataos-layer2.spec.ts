// @featuretrace:user-management — Layer 2 E2E security: ID-format validation, mass assignment, same-building horizontal BOLA, cron endpoint discovery.
// Layer: test
// Data flow: Playwright → POST /auth/login → GET/PUT/DELETE across /maintenance, /documents, /announcements, /users (building-scoped).
// Related: tests/frontend/e2e/security/bola-strataos.spec.ts
//           backend/routers/auth.py
//           backend/utils/rate_limit.py

/**
 * Layer 2 Security Tests — StrataOS
 *
 * Tests beyond the initial BOLA/JWT suite, covering:
 *  1. ID format validation (invalid ObjectID handling — no 500s)
 *  2. Mass assignment on user write endpoints
 *  3. Horizontal BOLA within same building (same-tenant owner-to-owner)
 *  4. Cron endpoint discovery and protection
 *
 * Run: npx playwright test --project=security tests/frontend/e2e/security/bola-strataos-layer2.spec.ts
 */

import { test, expect, request as globalRequest } from '@playwright/test';

const API = process.env.TEST_API || 'http://localhost:8003/api';

// Demo credentials (public seed data — DemoUser$01 is the intentionally-public demo password)
const ACME_MANAGER = { email: 'manager@acmestrata.demo', password: 'DemoUser$01' };
const ACME_OWNER_1 = { email: 'james.mitchell@acmedemo.au', password: 'DemoUser$01' };
const ACME_OWNER_2 = { email: 's.chen@acmedemo.au', password: 'DemoUser$01' };

async function getToken(email: string, password: string): Promise<string | null> {
  try {
    const ctx = await globalRequest.newContext();
    const res = await ctx.post(`${API}/auth/login`, {
      data: { email, password },
      headers: { 'Content-Type': 'application/json' },
    });
    const status = res.status();
    const body = status === 200 ? await res.json() : null;
    await ctx.dispose();
    return body?.token ?? null;
  } catch {
    return null;
  }
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

// ---------------------------------------------------------------------------
// 1. ID Format Validation
// ---------------------------------------------------------------------------

test.describe('Layer 2: ID Format Validation', () => {
  let managerToken: string;

  test.beforeAll(async () => {
    // Retry login up to 3 times with a short back-off — parallel workers can
    // transiently rate-limit the login endpoint when both security spec files
    // start their beforeAll at the same time.
    let t: string | null = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      t = await getToken(ACME_MANAGER.email, ACME_MANAGER.password);
      if (t) break;
      if (attempt < 2) await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
    }
    if (!t) throw new Error('Manager login failed after 3 attempts — cannot run ID format tests');
    managerToken = t;
  });

  const invalidIds = [
    { value: 'not-an-objectid',           desc: 'arbitrary string' },
    { value: "1' OR '1'='1",              desc: 'SQL injection string' },
    { value: '{"$gt":""}',                desc: 'JSON NoSQL injection as string' },
    { value: '000000000000000000000000',   desc: 'null-format ObjectID' },
    { value: 'A'.repeat(24),              desc: 'valid length invalid hex chars' },
    { value: '',                           desc: 'empty string (path segment)' },
    { value: 'null',                       desc: 'string "null"' },
    { value: 'undefined',                  desc: 'string "undefined"' },
    { value: '../../../etc/passwd',        desc: 'path traversal' },
    { value: '<script>alert(1)</script>',  desc: 'XSS payload' },
  ];

  // Endpoints that take a document ID in the URL
  const endpoints: Array<{ method: 'GET' | 'DELETE'; path: string }> = [
    { method: 'GET',    path: '/maintenance/{id}' },
    { method: 'GET',    path: '/documents/{id}' },
    { method: 'GET',    path: '/announcements/{id}' },
    { method: 'DELETE', path: '/documents/{id}' },
  ];

  for (const endpoint of endpoints) {
    for (const { value, desc } of invalidIds) {
      // Skip truly empty path segment — it produces a different route entirely
      if (value === '') continue;

      test(`${endpoint.method} ${endpoint.path} [${desc}] → no 500`, async ({ request }) => {
        const url = `${API}${endpoint.path.replace('{id}', encodeURIComponent(value))}`;
        const opts: Parameters<typeof request.get>[1] = {
          headers: auth(managerToken),
        };
        const response = endpoint.method === 'GET'
          ? await request.get(url, opts)
          : await request.delete(url, opts);

        // 400 = validation error (ideal)
        // 404 = not found (acceptable)
        // 405 = method not allowed (route exists, method not registered for ID path — not a security issue)
        // 422 = unprocessable entity (Pydantic rejected it)
        // 500 = CRITICAL — invalid ID reached the DB driver without validation
        expect(response.status()).not.toBe(500);
        expect([400, 403, 404, 405, 422]).toContain(response.status());
      });
    }
  }
});

// ---------------------------------------------------------------------------
// 2. Mass Assignment on Write Endpoints
// ---------------------------------------------------------------------------

test.describe('Layer 2: Mass Assignment on Write Endpoints', () => {
  let managerToken: string;
  let managerId: string;

  test.beforeAll(async () => {
    let t: string | null = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      t = await getToken(ACME_MANAGER.email, ACME_MANAGER.password);
      if (t) break;
      if (attempt < 2) await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
    }
    if (!t) throw new Error('Manager login failed after 3 attempts — cannot run mass assignment tests');
    managerToken = t;

    // Get the manager's own user ID
    const ctx = await globalRequest.newContext();
    const meRes = await ctx.get(`${API}/auth/me`, { headers: auth(managerToken) });
    const meStatus = meRes.status();
    const meBody = meStatus === 200 ? await meRes.json() : null;
    await ctx.dispose();
    if (meBody) {
      managerId = meBody.id ?? meBody.user_id ?? meBody._id ?? '';
    }
  });

  // Fields that must be silently ignored or rejected — not writable by the user themselves
  const privilegeEscalationAttempts = [
    { field: 'role', value: 'super_admin' },
    { field: 'role', value: 'admin' },
    { field: 'is_super_admin', value: true },
    { field: 'building_id', value: '13195' },
    { field: 'building_ids', value: ['UP-DEMO-001', '13195'] },
    { field: 'permissions', value: { can_manage_users: true } },
  ];

  for (const { field, value } of privilegeEscalationAttempts) {
    test(`PUT /users/{id} with "${field}=${JSON.stringify(value)}" ignored or rejected`, async ({ request }) => {
      if (!managerId) {
        test.skip(true, 'Could not resolve manager user ID');
        return;
      }

      const response = await request.put(`${API}/users/${managerId}`, {
        headers: auth(managerToken),
        data: {
          full_name: 'Test Mass Assignment',
          [field]: value,
        },
      });

      if (response.status() === 200 || response.status() === 204) {
        // Read back the profile to verify no escalation occurred
        const getRes = await request.get(`${API}/auth/me`, { headers: auth(managerToken) });
        if (getRes.status() === 200) {
          const body = await getRes.json();
          if (field === 'role') {
            // Role must NOT be super_admin or admin after update by a non-super-admin
            expect(body.role).not.toBe('super_admin');
            expect(body.role).not.toBe('admin');
          }
          if (field === 'building_id') {
            // building_id must not jump to production East Gate building
            // (manager belongs to UP-DEMO-001, not 13195)
            expect(body.building_id).not.toBe('13195');
          }
          if (field === 'is_super_admin') {
            expect(body.is_super_admin).not.toBe(true);
          }
        }
      } else {
        // 403 or 422 — field was rejected at model/permission layer (also correct)
        expect([400, 403, 422, 404]).toContain(response.status());
      }
    });
  }

  test('PUT /users/{id} role=super_admin is rejected with 403 for non-super-admin', async ({ request }) => {
    if (!managerId) {
      test.skip(true, 'Could not resolve manager user ID');
      return;
    }
    const response = await request.put(`${API}/users/${managerId}`, {
      headers: auth(managerToken),
      data: { full_name: 'Test', role: 'super_admin' },
    });
    // Strata manager attempting role escalation must be denied
    // 403 = permission guard fired; 404 = membership check blocked before guard; 422 = Pydantic rejected
    expect([403, 404, 422]).toContain(response.status());
  });
});

// ---------------------------------------------------------------------------
// 3. Horizontal BOLA Within Same Building
// ---------------------------------------------------------------------------

test.describe('Layer 2: Same-Building Horizontal BOLA', () => {
  let owner1Token: string | null = null;
  let owner2Token: string | null = null;
  let skipReason = '';

  test.beforeAll(async () => {
    owner1Token = await getToken(ACME_OWNER_1.email, ACME_OWNER_1.password);
    owner2Token = await getToken(ACME_OWNER_2.email, ACME_OWNER_2.password);
    if (!owner1Token || !owner2Token) {
      skipReason = `Owner login returned non-200 (owners may be Mongo-only, not Postgres-resolved). ` +
                   `owner1: ${owner1Token ? 'OK' : 'FAIL'}, owner2: ${owner2Token ? 'OK' : 'FAIL'}`;
    }
  });

  test('Owner 1 cannot enumerate Owner 2 levy records via unit_id filter', async ({ request }) => {
    if (!owner1Token || !owner2Token) {
      test.skip(true, skipReason);
      return;
    }

    // Get owner2's unit list
    const owner2Units = await request.get(`${API}/units`, {
      headers: auth(owner2Token),
    });
    if (owner2Units.status() !== 200) {
      test.skip(true, 'Owner 2 unit list unavailable');
      return;
    }
    const units2Body = await owner2Units.json();
    const units2 = Array.isArray(units2Body) ? units2Body : units2Body.units ?? units2Body.data ?? [];
    if (!units2.length) {
      test.skip(true, 'Owner 2 has no units');
      return;
    }
    const targetUnitId = units2[0].id ?? units2[0]._id;

    // Owner 1 tries to query levies for Owner 2's unit
    const res = await request.get(`${API}/finance/levies/?unit_id=${targetUnitId}`, {
      headers: auth(owner1Token),
    });

    // Should be 403 or empty — not Owner 2's records
    if (res.status() === 200) {
      const body = await res.json();
      const levies = Array.isArray(body) ? body : body.levies ?? body.data ?? [];
      // If records are returned, they must belong to owner1, not owner2
      for (const levy of levies) {
        expect(levy.unit_id ?? levy.unit_number).not.toBe(targetUnitId);
      }
    } else {
      expect([403, 404]).toContain(res.status());
    }
  });

  test('Owner 1 cannot access Owner 2 maintenance request by known ID', async ({ request }) => {
    if (!owner1Token || !owner2Token) {
      test.skip(true, skipReason);
      return;
    }

    // Get owner2's maintenance requests
    const maint2Res = await request.get(`${API}/maintenance`, {
      headers: auth(owner2Token),
    });
    if (maint2Res.status() !== 200) {
      test.skip(true, 'Owner 2 maintenance list unavailable');
      return;
    }
    const maint2Body = await maint2Res.json();
    const maint2 = Array.isArray(maint2Body) ? maint2Body : maint2Body.work_orders ?? maint2Body.data ?? [];
    if (!maint2.length) {
      test.skip(true, 'Owner 2 has no maintenance requests');
      return;
    }
    const targetId = maint2[0].id ?? maint2[0]._id;

    // Owner 1 tries to access it
    const res = await request.get(`${API}/maintenance/${targetId}`, {
      headers: auth(owner1Token),
    });
    // Must be forbidden or not found — not 200 with Owner 2's data
    expect([403, 404]).toContain(res.status());
  });
});

// ---------------------------------------------------------------------------
// 4. Cron Endpoint Discovery
// ---------------------------------------------------------------------------

test.describe('Layer 2: Cron Endpoint Discovery', () => {
  // Common path patterns that cron-style endpoints use
  const cronPaths = [
    'cron/levies/generate',
    'cron/levies/due',
    'cron/payments/process',
    'cron/payments/reconcile',
    'cron/maintenance/sla-check',
    'cron/maintenance/escalate',
    'cron/notifications/email',
    'cron/notifications/sms',
    'cron/announcements/schedule',
    'cron/reports/generate',
    'cron/backup',
    'cron/sync',
    'cron/cleanup',
    'cron/health',
    'cron/ping',
    'internal/cron',
    'admin/cron',
    'scheduler/run',
    'jobs/run',
    'tasks/run',
  ];

  // Header names to probe
  const cronSecretHeaders = [
    'X-CRON-SECRET',
    'X-Cron-Secret',
    'x-cron-secret',
    'X-Webhook-Secret',
  ];

  for (const path of cronPaths) {
    test(`POST /${path} — 404 or auth-protected, never 200 unauthenticated`, async ({ request }) => {
      const url = `${API}/${path}`;

      // No auth
      const noAuth = await request.post(url, {
        data: {},
        headers: { 'Content-Type': 'application/json' },
      });

      // 404 = endpoint doesn't exist (all crons run as standalone scripts — expected)
      // 401/403 = endpoint exists and is auth-protected (also fine)
      // 200 = CRITICAL — cron runs without credentials
      expect(noAuth.status()).not.toBe(200);
      expect(noAuth.status()).not.toBe(500);

      // If endpoint exists (not 404), probe for header-based auth bypass
      if (noAuth.status() !== 404) {
        for (const header of cronSecretHeaders) {
          const withBadSecret = await request.post(url, {
            data: {},
            headers: { 'Content-Type': 'application/json', [header]: 'wrong-value' },
          });
          // A wrong secret must not grant access
          expect(withBadSecret.status()).not.toBe(200);
        }
      }
    });
  }
});
