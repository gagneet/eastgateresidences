/**
 * Runtime Undefined-Reference Detection
 *
 * Visits every major dashboard route while logged in as a management-level user
 * and collects browser console errors. The test fails if any page produces a
 * ReferenceError (e.g. "Dog is not defined") or similar client-side exception.
 *
 * This is a runtime complement to the static analysis in:
 *   backend/tests/test_frontend_static_analysis.py
 *
 * Why two layers?
 *   Static analysis  — fast, no browser, runs in CI without a live server.
 *                      Catches undefined imports before the app is even built.
 *   Runtime (here)  — catches errors that slip past static analysis, such as
 *                      dynamic requires, tree-shaken re-exports, or Next.js
 *                      hydration failures that only appear in a real browser.
 *
 * Run:
 *   cd /home/gagneet/strata-management
 *   npx playwright test tests/frontend/e2e/undefined-references.spec.js
 *   npx playwright test tests/frontend/e2e/undefined-references.spec.js --headed
 */

// @ts-check
const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';

// Management-level account — sees all dashboard routes
const ADMIN = {
    email: 'anthony@eastgateresidences.com.au',
    password: process.env.E2E_CHAIRMAN_PASSWORD || '',
};

// ── Dashboard routes to probe ──────────────────────────────────────────────
// Covers every sidebar section so that any component rendered in the nav or
// on a page is exercised. Add new routes here when new pages are added.
const DASHBOARD_ROUTES = [
    // Core
    '/dashboard',
    // Financial
    '/financials/overview',
    '/intelligence/financial',
    '/financials/levy-payments',
    '/intelligence/projections',
    '/financials/spending-categories',
    '/financials/council-rates',
    '/financials/water-bills',
    // Community
    '/community/notices',
    '/governance/agm',
    '/governance/meetings',
    '/community/events',
    '/community/chat',
    '/community/marketplace',
    // Property & Maintenance
    '/maintenance',
    '/maintenance/defects',
    '/compliance',
    '/community/bookings',
    '/community/parcels',
    '/requests',
    // Governance / Compliance
    '/requests/rental-certificates',
    '/community/pet-register',
    '/admin/strata-roll',
    '/admin/assets',
    // User self-service
    '/profile',
    '/notifications',
    '/my-communications',
    '/admin/communications',
    // Admin
    '/admin/users',
    '/admin/owners-units',
    '/settings',
    '/admin/feature-toggles',
    '/admin/audit-logs',
];

// ── Error classifier ───────────────────────────────────────────────────────
// Console messages we want to catch and fail on
const FATAL_PATTERNS = [
    /ReferenceError/i,
    /is not defined/i,
    /Cannot read propert/i,    // "Cannot read property X of undefined/null"
    /is not a function/i,
    /Uncaught.*Error/i,
    /Application error/i,
    /Unhandled.*rejection/i,
];

// Noise we deliberately ignore (third-party scripts, known non-critical warnings)
const IGNORE_PATTERNS = [
    /cloudflare/i,
    /stripe/i,
    /intercom/i,
    /hotjar/i,
    /gtag/i,
    /google-analytics/i,
    /net::ERR_/i,             // network-level errors outside our control
    /favicon/i,
    /Failed to load resource.*\/api\/(market|council-rates)/i,  // optional external APIs
    /Warning:.*React/i,        // React dev-mode warnings, not errors
    /Download the React/i,
];

function isFatal(msg) {
    const text = msg.text();
    if (IGNORE_PATTERNS.some((p) => p.test(text))) return false;
    return FATAL_PATTERNS.some((p) => p.test(text));
}

// ── Helpers ────────────────────────────────────────────────────────────────
async function loginAsAdmin(page) {
    await page.goto(`${BASE_URL}/login`, {waitUntil: 'domcontentloaded'});
    await page.fill('input[type="email"], input[name="email"]', ADMIN.email);
    await page.fill('input[type="password"], input[name="password"]', ADMIN.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/dashboard/, {timeout: 15000});
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('Dashboard — no undefined-reference errors', () => {
    test.describe.configure({mode: 'serial'}); // share one browser session

    /** @type {import('@playwright/test').Page} */
    let page;
    /** Accumulated errors from ALL pages so we get one consolidated failure */
    const allErrors = [];

    test.beforeAll(async ({browser}) => {
        page = await browser.newPage();

        // Collect ALL console errors throughout the session
        page.on('console', (msg) => {
            if (msg.type() === 'error' && isFatal(msg)) {
                allErrors.push({url: page.url(), message: msg.text()});
            }
        });

        // Also catch uncaught page exceptions (harder to ignore)
        page.on('pageerror', (err) => {
            const text = err.message || String(err);
            if (!IGNORE_PATTERNS.some((p) => p.test(text))) {
                allErrors.push({url: page.url(), message: `pageerror: ${text}`});
            }
        });

        await loginAsAdmin(page);
    });

    test.afterAll(async () => {
        await page.close();
    });

    // Dynamically create one sub-test per route so failures are easy to locate
    for (const route of DASHBOARD_ROUTES) {
        test(`${route} — loads without ReferenceError`, async () => {
            const errsBefore = allErrors.length;

            await page.goto(`${BASE_URL}${route}`, {
                waitUntil: 'domcontentloaded',
                timeout: 20000,
            });

            // Brief pause for hydration / lazy component mounting
            await page.waitForTimeout(800);

            const newErrs = allErrors.slice(errsBefore);
            const routeErrs = newErrs.filter((e) => e.url.includes(route));

            if (routeErrs.length > 0) {
                const messages = routeErrs.map((e) => `  [${e.url}] ${e.message}`).join('\n');
                throw new Error(
                    `${routeErrs.length} undefined-reference error(s) on ${route}:\n${messages}`
                );
            }
        });
    }

    test('no accumulated errors across all routes', async () => {
        // Final consolidated assertion — catches errors whose URL may not match
        // the route exactly (e.g. redirects, nested async fetches)
        if (allErrors.length > 0) {
            const grouped = {};
            for (const {url, message} of allErrors) {
                grouped[ url ] = grouped[ url ] || [];
                grouped[ url ].push(message);
            }
            const lines = Object.entries(grouped).map(
                ([url, msgs]) => `  ${url}:\n${msgs.map((m) => `    ${m}`).join('\n')}`
            );
            throw new Error(
                `${allErrors.length} undefined-reference error(s) across all dashboard routes:\n` +
                lines.join('\n')
            );
        }
    });
});

// ── Standalone: static-analysis smoke via API ─────────────────────────────
// Hits the backend to verify it is live — a dead backend produces a cascade
// of "Failed to fetch" errors that pollute the console-error assertions above.
test.describe('Pre-flight — backend must be reachable', () => {
    test('GET /api/ec-members returns 200 (backend alive)', async ({request}) => {
        const res = await request.get(`${BASE_URL}/api/ec-members`);
        expect(res.status()).toBe(200);
    });
});
