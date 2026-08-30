import {expect, test} from '@playwright/test';
import fs from 'fs';
import path from 'path';

const AUTH_DIR = path.join(__dirname, '.auth', 'request-catalogue');
const METADATA_PATH = path.join(AUTH_DIR, 'metadata.json');
const ALLOWED_BUILDINGS = new Set(['TEST-REQUEST-A', 'TEST-REQUEST-B']);

type RoleName = 'owner' | 'tenant' | 'real_estate_agent' | 'ec_member' | 'strata_manager';

type Metadata = {
  primary_building_id: string;
  secondary_building_id: string;
  roles: Record<RoleName, {
    storage_state: string;
    primary_building_id: string;
    secondary_building_id: string | null;
  }>;
};

const readMetadata = (): Metadata => {
  if (!fs.existsSync(METADATA_PATH)) {
    throw new Error('Request-catalogue storage states were not generated. Use playwright.request-catalogue.config.js.');
  }
  const metadata = JSON.parse(fs.readFileSync(METADATA_PATH, 'utf8')) as Metadata;
  for (const buildingId of [metadata.primary_building_id, metadata.secondary_building_id]) {
    if (!ALLOWED_BUILDINGS.has(buildingId)) throw new Error(`Disallowed test building in metadata: ${buildingId}`);
  }
  return metadata;
};

const statePathFor = (role: RoleName) => path.join(AUTH_DIR, `${role}.json`);

const validateStateFor = (metadata: Metadata, role: RoleName) => {
  const entry = metadata.roles[role];
  if (!entry?.storage_state) throw new Error(`Missing runtime storage state for ${role}.`);
  if (!ALLOWED_BUILDINGS.has(entry.primary_building_id)) throw new Error(`${role} state uses ${entry.primary_building_id}.`);
  const absolute = path.resolve(process.cwd(), entry.storage_state);
  if (!fs.existsSync(absolute)) throw new Error(`Runtime storage state missing on disk for ${role}: ${absolute}`);
};

const roleJourneys = [
  {
    role: 'owner',
    visible: 'Pet application',
    search: 'pet',
    startKey: 'pet',
  },
  {
    role: 'tenant',
    visible: 'Access device request',
    search: 'access',
    startKey: 'access-control',
  },
  {
    role: 'real_estate_agent',
    visible: 'Alterations application',
    search: 'alterations',
    startKey: 'alterations',
  },
  {
    role: 'ec_member',
    visible: 'Work orders',
    search: 'work',
    startKey: 'work-order',
  },
  {
    role: 'strata_manager',
    visible: 'Work orders',
    search: 'work',
    startKey: 'work-order',
  },
] as const;

test.describe('Requests & Applications catalogue journeys', () => {
  let metadata: Metadata;

  test.beforeAll(() => {
    metadata = readMetadata();
    for (const journey of roleJourneys) validateStateFor(metadata, journey.role);
  });

  for (const journey of roleJourneys) {
    test.describe(journey.role, () => {
      test.use({storageState: statePathFor(journey.role)});

      test('can discover and start an authorised request', async ({page}) => {
        await page.goto('/requests');
        await expect(page.getByRole('heading', {name: 'Requests & Applications'})).toBeVisible();
        await expect(page.getByText(journey.visible).first()).toBeVisible();
        await page.getByLabel('Search requests and applications').fill(journey.search);
        await expect(page.getByText(journey.visible)).toBeVisible();
        await page.getByTestId(`start-request-${journey.startKey}`).click();
        await expect(page).toHaveURL(new RegExp(`tab=${journey.startKey}`));
      });
    });
  }

  test.describe('resident request tracking', () => {
    test.use({storageState: statePathFor('owner')});

    test('keeps resident-facing wording on the tracking list', async ({page}) => {
      // An owner only ever receives their own requests from the API, so the
      // resident wording is the accurate one for this role.
      await page.goto('/requests?tab=my-requests');
      await expect(page.getByRole('heading', {name: 'My Requests'})).toBeVisible();
    });
  });

  test.describe('shared catalogue behaviours', () => {
    test.use({storageState: statePathFor('strata_manager')});

    test('supports request-queue navigation and status filtering', async ({page}) => {
      await page.goto('/requests');
      await page.getByTestId('my-requests-button').click();
      await expect(page).toHaveURL(/tab=my-requests/);
      // This block runs as strata_manager, and GET /workflow-requests returns the
      // WHOLE BUILDING's requests to a manager — so the heading must not claim these
      // are the manager's own requests. Residents still see "My Requests"; that
      // branch is covered by the owner journey below.
      await expect(page.getByRole('heading', {name: 'Request Queue'})).toBeVisible();
      await page.getByLabel('Filter requests by status').click();
      await page.getByRole('option', {name: /In progress/}).click();
      await expect(page).toHaveURL(/status=in_progress/);
    });

    test('a bare ?status= deep link opens the queue, not the form catalogue', async ({page}) => {
      // The Management dashboard's "N requests past SLA" card links here.
      await page.goto('/requests?status=overdue');
      await expect(page.getByRole('heading', {name: 'Request Queue'})).toBeVisible();
      await expect(page.getByTestId('request-catalogue-grid')).toBeHidden();
    });

    test('supports keyboard access through search and start actions', async ({page}) => {
      await page.goto('/requests');
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      await expect(page.getByLabel('Search requests and applications')).toBeFocused();
      await page.keyboard.type('work');
      await page.getByTestId('start-request-work-order').focus();
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/tab=work-order/);
    });

    test('supports legacy link aliases without leaving the catalogue shell', async ({page}) => {
      await page.goto('/requests?tab=remote');
      await expect(page.getByRole('heading', {name: 'Access device request'})).toBeVisible();
      await page.getByTestId('back-to-request-catalogue').click();
      await expect(page).toHaveURL(/\/dashboard\/requests$/);
    });

    test('mobile layout keeps catalogue controls reachable', async ({page}) => {
      await page.goto('/requests');
      await expect(page.getByRole('heading', {name: 'Requests & Applications'})).toBeVisible();
      await expect(page.getByTestId('my-requests-button')).toBeVisible();
      await page.getByLabel('Search requests and applications').fill('pet');
      await expect(page.getByTestId('request-catalogue-grid')).toBeVisible();
    });

    test('can switch between synthetic request buildings without touching production', async ({page}) => {
      const manager = metadata.roles.strata_manager;
      if (!manager.secondary_building_id) throw new Error('strata_manager metadata lacks secondary TEST-REQUEST building.');
      await page.goto('/requests');
      const storedBefore = await page.evaluate(() => JSON.parse(localStorage.getItem('selectedBuilding') || '{}'));
      expect(storedBefore.building_id).toBe(metadata.primary_building_id);
      expect(ALLOWED_BUILDINGS.has(storedBefore.building_id)).toBe(true);

      const apiBase = process.env.TEST_API || 'http://localhost:8003/api';
      const switchResult = await page.evaluate(async ({secondaryBuildingId, apiBaseUrl}) => {
        const session = await fetch('/api/auth/session').then((response) => response.json());
        const token = session?.accessToken;
        if (!token) return {ok: false, reason: 'missing accessToken'};
        const response = await fetch(`${apiBaseUrl}/auth/switch-building`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
            'X-Building-ID': secondaryBuildingId,
          },
          body: JSON.stringify({building_id: secondaryBuildingId}),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) return {ok: false, status: response.status, body};
        localStorage.setItem('selectedBuilding', JSON.stringify(body.building));
        return {ok: true, building: body.building};
      }, {secondaryBuildingId: manager.secondary_building_id, apiBaseUrl: apiBase});
      expect(switchResult).toMatchObject({ok: true});
      expect(ALLOWED_BUILDINGS.has((switchResult as any).building.building_id)).toBe(true);
      expect((switchResult as any).building.building_id).toBe(manager.secondary_building_id);
    });
  });

  test.describe('unauthorised direct form access', () => {
    test.use({storageState: statePathFor('tenant')});

    test('tenant direct access to owner-only reimbursement form returns to catalogue', async ({page}) => {
      await page.goto('/requests?tab=reimbursement');
      await expect(page).toHaveURL(/\/dashboard\/requests$/);
      await expect(page.getByRole('heading', {name: 'Requests & Applications'})).toBeVisible();
      await expect(page.getByRole('heading', {name: 'Reimbursement request'})).toHaveCount(0);
    });
  });
});
