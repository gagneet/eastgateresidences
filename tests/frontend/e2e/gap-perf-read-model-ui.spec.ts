import { test, expect, Page } from '@playwright/test';

const BUILDING_ID = process.env.BUILDING_ID ?? '13195';

const mockUser = {
  id: 'gap-perf-user',
  email: 'gap-perf@example.test',
  full_name: 'GAP PERF Test User',
  role: 'super_admin',
  building_id: BUILDING_ID,
  is_approved: true,
  created_at: new Date(0).toISOString(),
  permissions: {},
};

const mockBuilding = {
  id: BUILDING_ID,
  building_id: BUILDING_ID,
  name: 'East Gate Residences',
  address: 'Denman Prospect ACT',
};

async function installReadModelMocks(page: Page) {
  await page.addInitScript((building) => {
    window.localStorage.setItem('selectedBuilding', JSON.stringify(building));
    window.localStorage.setItem('selectedYear', '2026');
  }, mockBuilding);

  await page.route('**/api/auth/session', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        accessToken: 'gap-perf-ui-token',
        user: { data: mockUser },
        expires: new Date(Date.now() + 3600_000).toISOString(),
      }),
    });
  });

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, '');

    // These responses are deliberately small read-model-shaped payloads. The
    // test is for UI shell stability and request hygiene, not business totals.
    const fixtures: Record<string, unknown> = {
      '/auth/me': mockUser,
      '/buildings/me': [mockBuilding],
      '/feature-toggles/access-summary/me': [],
      '/navigation/config': { items: [], sections: [] },
      '/navigation/badges': {},
      '/settings': { building_name: mockBuilding.name, building_address: mockBuilding.address },
      '/years': ['2026', '2025'],
      '/workflow-requests/stats/triage': { overdue: 0, awaiting_review: 0, open: 0 },
      '/workflow-requests': [],
      '/finance/building-overview': {
        current_balance: 0,
        admin_fund_balance: 0,
        sinking_fund_balance: 0,
        as_at: '2026-08-27T00:00:00Z',
      },
      '/finance/summary': { total_income: 0, total_expenses: 0, net_position: 0 },
      '/finance/levy-kpi': { collection_rate: 0, total_due: 0, total_paid: 0 },
      '/stats/building-kpis': { total_units: 87, open_requests: 0, arrears_count: 0 },
      '/analytics/maintenance-stats': { open: 0, overdue: 0 },
      '/analytics/compliance-summary': { overdue: 0, due_soon: 0 },
      '/analytics/activities': [],
      '/analytics/sinking-fund-forecast': { years: [], balances: [] },
      '/maintenance': [],
      '/work-orders': [],
      '/contractors': [],
      '/ppm/upcoming': [],
      '/documents': [],
      '/documents/folders': [],
      '/documents/important': [],
    };

    const body = Object.prototype.hasOwnProperty.call(fixtures, path)
      ? fixtures[path]
      : path.startsWith('/workflow-requests')
        ? []
        : path.startsWith('/documents')
          ? []
          : {};

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
}

async function assertNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(overflow, 'page should not horizontally overflow the viewport').toBeLessThanOrEqual(2);
}

test.describe('GAP-PERF read-model UI/UX smoke', () => {
  test.beforeEach(async ({ page }) => {
    await installReadModelMocks(page);
  });

  for (const viewport of [
    { width: 1440, height: 1000, label: 'desktop' },
    { width: 390, height: 844, label: 'mobile' },
  ]) {
    for (const path of ['/dashboard', '/maintenance', '/documents', '/settings']) {
      test(`${path} renders stable ${viewport.label} shell without API prefix or overflow regressions`, async ({ page }) => {
        const requestedUrls: string[] = [];
        page.on('request', (request) => requestedUrls.push(request.url()));

        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto(path);
        await page.waitForLoadState('networkidle');

        await expect(page.locator('body')).toBeVisible();
        await assertNoHorizontalOverflow(page);

        const doublePrefixed = requestedUrls.filter((url) => url.includes('/api/api/'));
        expect(doublePrefixed, 'frontend must not emit double-prefixed API requests').toEqual([]);

        const visibleErrorText = page.getByText(/Unhandled Runtime Error|Application error|Internal Server Error/i);
        await expect(visibleErrorText).toHaveCount(0);
      });
    }
  }
});

