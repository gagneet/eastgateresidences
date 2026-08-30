import { test, expect, APIRequestContext } from '@playwright/test';

const API = process.env.TEST_API ?? 'http://localhost:8003/api';
const BUILDING_ID = process.env.BUILDING_ID ?? '13195';

const ADMIN = {
  email: process.env.TEST_ADMIN_EMAIL || '',
  password: process.env.TEST_ADMIN_PASSWORD || '',
};

async function getToken(request: APIRequestContext): Promise<string | null> {
  if (process.env.TEST_AUTH_TOKEN) return process.env.TEST_AUTH_TOKEN;
  if (!ADMIN.email || !ADMIN.password) return null;
  const res = await request.post(`${API}/auth/login`, {
    data: ADMIN,
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok()) return null;
  const body = await res.json();
  return body.token ?? body.access_token ?? null;
}

test.describe.serial('GAP-PERF read-model API smoke', () => {
  let token: string | null = null;

  test.beforeAll(async ({ request }) => {
    token = await getToken(request);
  });

  function headers() {
    return {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      'X-Building-ID': BUILDING_ID,
    };
  }

  const currentReadPaths = [
    '/auth/me',
    '/buildings/me',
    '/feature-toggles/access-summary/me',
    '/navigation/config',
    '/navigation/badges',
    '/workflow-requests/stats/triage',
    '/workflow-requests?limit=25',
    '/finance/building-overview',
    '/finance/summary',
    '/finance/levy-kpi',
    '/stats/building-kpis',
    '/maintenance?limit=25',
    '/contractors',
    '/ppm/upcoming?days=60',
    '/documents',
    '/documents/folders',
    '/documents/important',
  ];

  for (const path of currentReadPaths) {
    test(`GET ${path} is authenticated, registered and bounded`, async ({ request }) => {
      if (!token) test.skip(true, 'Set TEST_AUTH_TOKEN or TEST_ADMIN_EMAIL/TEST_ADMIN_PASSWORD');
      const started = Date.now();
      const res = await request.get(`${API}${path}`, { headers: headers() });
      const elapsed = Date.now() - started;

      expect(res.status(), `${path} should be registered and authorised for the test user`).not.toBe(404);
      expect([200, 204, 403]).toContain(res.status());
      expect(elapsed, `${path} smoke latency`).toBeLessThan(3000);
    });
  }

  test('future read-model endpoints fail closed or exist without unauthenticated success', async ({ request }) => {
    const futurePaths = [
      '/ops/cases?limit=25',
      '/communications/campaigns?limit=25',
      '/access/devices?limit=25',
      '/portfolio/buildings/archived',
    ];

    for (const path of futurePaths) {
      const anon = await request.get(`${API}${path}`);
      expect([401, 403, 404, 405]).toContain(anon.status());

      if (token) {
        const authed = await request.get(`${API}${path}`, { headers: headers() });
        expect([200, 403, 404, 405]).toContain(authed.status());
      }
    }
  });
});

