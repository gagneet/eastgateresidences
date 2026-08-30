// @featuretrace:dashboard-v2 — k6 browser benchmark for authenticated UI render paths.
// Measures real Chromium page load/render metrics for the dashboard and finance overview,
// complementing the API-only k6 scripts and the Lighthouse harness.
//
// Required:
//   UI_BASE_URL=http://localhost:3000
//   MANAGEMENT_SESSION_COOKIE='next-auth.session-token=...'
//   OWNER_SESSION_COOKIE='next-auth.session-token=...'
//
// Optional:
//   FINANCE_SESSION_COOKIE='next-auth.session-token=...'
//     If omitted, the management cookie is reused for Financial Overview because
//     /financials/overview is a building-wide finance surface.
import {browser} from 'k6/browser';
import {check} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        ui_render_smoke: {
            executor: 'shared-iterations',
            vus: 1,
            iterations: 1,
            options: {
                browser: {
                    type: 'chromium',
                },
            },
        },
    },
    thresholds: {
        checks: ['rate>0.95'],
        ui_page_errors: ['count==0'],
        ui_page_load_ms: ['p(95)<5000'],
        browser_web_vital_lcp: ['p(95)<3500'],
        browser_web_vital_cls: ['p(95)<0.1'],
        browser_web_vital_fcp: ['p(95)<2500'],
        browser_web_vital_ttfb: ['p(95)<1500'],
    },
};

const UI_BASE_URL = (__ENV.UI_BASE_URL || 'http://localhost:3000').replace(/\/$/, '');
const MANAGEMENT_COOKIE = __ENV.MANAGEMENT_SESSION_COOKIE || '';
const OWNER_COOKIE = __ENV.OWNER_SESSION_COOKIE || '';
const FINANCE_COOKIE = __ENV.FINANCE_SESSION_COOKIE || MANAGEMENT_COOKIE;
const PAGE_READY_TIMEOUT_MS = Number(__ENV.PAGE_READY_TIMEOUT_MS || 15000);
const pageErrors = new Counter('ui_page_errors');
const pageLoadMs = new Trend('ui_page_load_ms');

type RouteSpec = {
    name: string;
    path: string;
    cookie: string;
    readySelector: string;
};

function routes(): RouteSpec[] {
    return [
        {
            name: 'management_dashboard',
            path: '/dashboard',
            cookie: MANAGEMENT_COOKIE,
            readySelector: 'text=Management Mode Active.',
        },
        {
            name: 'owner_dashboard',
            path: '/dashboard',
            cookie: OWNER_COOKIE,
            readySelector: 'text=Here is your property pulse.',
        },
        {
            name: 'financial_overview',
            path: '/financials/overview',
            cookie: FINANCE_COOKIE,
            readySelector: '[data-testid="finance-page"]',
        },
    ];
}

export function setup() {
    const missing = routes().filter((route) => !route.cookie).map((route) => route.name);
    if (missing.length > 0) {
        throw new Error(
            'Authenticated UI rendering benchmarks require role-specific cookies. ' +
            'Set MANAGEMENT_SESSION_COOKIE and OWNER_SESSION_COOKIE; set FINANCE_SESSION_COOKIE only when finance should use a different user. ' +
            `Missing: ${missing.join(', ')}.`,
        );
    }
}

async function measureRoute(route: RouteSpec) {
    const context = await browser.newContext({
        viewport: {width: 1440, height: 1000},
        screen: {width: 1440, height: 1000},
        extraHTTPHeaders: {
            Cookie: route.cookie,
        },
    });
    const page = await context.newPage();
    const start = Date.now();
    try {
        const response = await page.goto(`${UI_BASE_URL}${route.path}`, {
            waitUntil: 'load',
            timeout: PAGE_READY_TIMEOUT_MS,
        });
        const status = response ? response.status() : 0;
        await page.locator(route.readySelector).waitFor({timeout: PAGE_READY_TIMEOUT_MS});
        pageLoadMs.add(Date.now() - start, {page: route.name});
        check(response, {
            [`${route.name} navigation status is ok`]: () => status >= 200 && status < 400,
        });
        check(page, {
            [`${route.name} remains on authenticated route`]: () => !page.url().includes('/login'),
        });
    } catch (error) {
        pageErrors.add(1, {page: route.name});
        throw error;
    } finally {
        await page.close();
        await context.close();
    }
}

export default async function () {
    for (const route of routes()) {
        await measureRoute(route);
    }
}

export function teardown() {
    // Read-only benchmark. No test data is created.
}
