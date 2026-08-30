// @featuretrace:dashboard-v2 — k6 browser benchmark for mobile UI render and overflow checks.
// Measures real Chromium rendering at iPhone 14 Pro and Galaxy S24 Ultra widths.
// Authenticated routes are included only when explicitly requested with
// INCLUDE_AUTHENTICATED=1 and an AUTH_SESSION_COOKIE value.
import {browser} from 'k6/browser';
import {check} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        mobile_responsive_smoke: {
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
        checks: ['rate>0.99'],
        ui_mobile_overflow_errors: ['count==0'],
        ui_mobile_render_ms: ['p(95)<6000'],
        browser_web_vital_cls: ['p(95)<0.1'],
        browser_web_vital_lcp: ['p(95)<4000'],
    },
};

const UI_BASE_URL = (__ENV.UI_BASE_URL || 'http://localhost:3000').replace(/\/$/, '');
const AUTH_SESSION_COOKIE = __ENV.AUTH_SESSION_COOKIE || __ENV.MANAGEMENT_SESSION_COOKIE || '';
const INCLUDE_AUTHENTICATED = __ENV.INCLUDE_AUTHENTICATED === '1';
const PAGE_READY_TIMEOUT_MS = Number(__ENV.PAGE_READY_TIMEOUT_MS || 15000);

const overflowErrors = new Counter('ui_mobile_overflow_errors');
const renderMs = new Trend('ui_mobile_render_ms');

type ViewportSpec = {
    name: string;
    width: number;
    height: number;
};

type RouteSpec = {
    name: string;
    path: string;
    authenticated?: boolean;
};

const VIEWPORTS: ViewportSpec[] = [
    {name: 'iphone_14_pro', width: 393, height: 852},
    {name: 'galaxy_s24_ultra', width: 430, height: 932},
];

const PUBLIC_ROUTES: RouteSpec[] = [
    {name: 'home', path: '/'},
    {name: 'login', path: '/login'},
    {name: 'forgot_password', path: '/forgot-password'},
    {name: 'marketplace', path: '/marketplace'},
    {name: 'emergency_services', path: '/emergency-services'},
];

const AUTHENTICATED_ROUTES: RouteSpec[] = [
    {name: 'dashboard', path: '/dashboard', authenticated: true},
    {name: 'financial_overview', path: '/financials/overview', authenticated: true},
    {name: 'owner_hub_classic', path: '/owner-hub/classic', authenticated: true},
    {name: 'water_bills', path: '/financials/water-bills', authenticated: true},
    {name: 'council_rates', path: '/financials/council-rates', authenticated: true},
];

function selectedRoutes(): RouteSpec[] {
    if (INCLUDE_AUTHENTICATED) {
        return [...PUBLIC_ROUTES, ...AUTHENTICATED_ROUTES];
    }
    return PUBLIC_ROUTES;
}

export function setup() {
    if (INCLUDE_AUTHENTICATED && !AUTH_SESSION_COOKIE) {
        throw new Error(
            'Authenticated mobile UI benchmarks require AUTH_SESSION_COOKIE or MANAGEMENT_SESSION_COOKIE. ' +
            'Without a cookie, dashboard routes redirect to /login and do not validate authenticated UI layout.',
        );
    }
    return {routes: selectedRoutes()};
}

async function measureRoute(route: RouteSpec, viewport: ViewportSpec) {
    const context = await browser.newContext({
        viewport: {width: viewport.width, height: viewport.height},
        screen: {width: viewport.width, height: viewport.height},
        deviceScaleFactor: 3,
        isMobile: true,
        extraHTTPHeaders: route.authenticated ? {Cookie: AUTH_SESSION_COOKIE} : {},
    });
    const page = await context.newPage();
    const start = Date.now();

    try {
        const response = await page.goto(`${UI_BASE_URL}${route.path}`, {
            waitUntil: 'load',
            timeout: PAGE_READY_TIMEOUT_MS,
        });
        await page.waitForLoadState('networkidle', {timeout: PAGE_READY_TIMEOUT_MS}).catch(() => {});
        const status = response ? response.status() : 0;
        const report = await page.evaluate(() => {
            const viewportWidth = window.innerWidth;
            const documentScrollWidth = Math.max(
                document.documentElement.scrollWidth,
                document.body.scrollWidth,
            );
            const overflowCount = Array.from(document.querySelectorAll('body *'))
                .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const intentionallyScrollable = style.overflowX === 'auto' || style.overflowX === 'scroll';
                    return !intentionallyScrollable && (rect.left < -2 || rect.right > viewportWidth + 2);
                }).length;
            return {viewportWidth, documentScrollWidth, overflowCount};
        });
        const pageKey = `${route.name}_${viewport.name}`;
        renderMs.add(Date.now() - start, {page: pageKey});

        const ok = check(report, {
            [`${pageKey} has no document horizontal overflow`]: (r) => r.documentScrollWidth <= r.viewportWidth + 1,
            [`${pageKey} has no off-viewport elements`]: (r) => r.overflowCount === 0,
        });
        check(response, {
            [`${pageKey} navigation status is ok`]: () => status >= 200 && status < 400,
        });
        if (route.authenticated) {
            check(page, {
                [`${pageKey} stayed on authenticated route`]: () => page.url().includes(route.path),
            });
        }
        if (!ok) {
            overflowErrors.add(1, {page: pageKey});
        }
    } catch (error) {
        overflowErrors.add(1, {page: `${route.name}_${viewport.name}`});
        throw error;
    } finally {
        await page.close();
        await context.close();
    }
}

export default async function (data: {routes: RouteSpec[]}) {
    for (const route of data.routes) {
        for (const viewport of VIEWPORTS) {
            await measureRoute(route, viewport);
        }
    }
}

export function teardown(_data: unknown): void {
    // Read-only browser benchmark. No test records are created.
}
