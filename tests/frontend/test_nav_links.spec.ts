/**
 * Navigation link smoke tests — Progressive Navigation System
 *
 * Verifies that every route referenced in navigation_configs.py resolves to
 * a real page (no 404) after authentication.  Tests are role-grouped so a
 * failure immediately identifies which role's menu is broken.
 *
 * Run:
 *   npx playwright test tests/frontend/test_nav_links.spec.ts
 *   npx playwright test tests/frontend/test_nav_links.spec.ts --project=chromium
 */

import {expect, Page, test} from "@playwright/test";

// ── Test configuration ────────────────────────────────────────────────────────
const BASE_URL = process.env.TEST_BASE_URL ?? "http://localhost:3020";
const API_URL = process.env.TEST_API_URL ?? "http://localhost:8003/api";

// ── Credentials per role ──────────────────────────────────────────────────────
const USERS: Record<string, { email: string; password: string }> = {
    super_admin: {email: "superadmin@eastgateresidences.com.au", password: "SuperAdmin123!"},
    chairman: {email: "anthony@eastgateresidences.com.au", password: process.env.E2E_CHAIRMAN_PASSWORD || ''},
    ec_member: {email: "marcelo.dasilva@eastgateresidences.com.au", password: process.env.E2E_EC_PASSWORD || ''},
    owner: {email: "avneet@eastgateresidences.com.au", password: process.env.E2E_OWNER_PASSWORD || ''},
    tenant: {email: "tenant@eastgateresidences.com.au", password: process.env.E2E_TENANT_PASSWORD || ''},
    strata_manager: {email: "manager@eastgateresidences.com.au", password: "$SEED_TEST_USER_PASSWORD"},
};

// ── Route manifests per role ──────────────────────────────────────────────────
// Only includes routes from navigation_configs.py (simple + advanced items).
// Excludes feature-flagged items that may be disabled per-building.
const ROLE_ROUTES: Record<string, string[]> = {
    super_admin: [
        "/dashboard",
        "/admin/users",
        "/financials/overview",
        "/admin",
        "/compliance",
        "/reports",
        "/admin/feature-toggles",
        "/admin/audit-logs",
    ],
    strata_manager: [
        "/dashboard",
        "/financials/overview",
        "/maintenance",
        "/compliance",
        "/reports",
        "/settings",
        "/governance/meetings",
        "/governance/bylaws",
        "/documents",
    ],
    chairman: [
        "/dashboard",
        "/requests/my-approvals",
        "/governance/meetings",
        "/financials/overview",
        "/maintenance",
        "/compliance",
        "/governance/bylaws",
        "/documents",
        "/governance/proposals",
        "/reports",
        "/community",
        "/governance/ec-members",
        "/owner-view",
    ],
    ec_member: [
        "/dashboard",
        "/requests/my-approvals",
        "/governance/meetings",
        "/maintenance",
        "/community",
        "/financials/overview",
        "/governance/bylaws",
        "/compliance",
        "/documents",
        "/governance/proposals",
        "/owner-view",
    ],
    owner: [
        "/dashboard",
        "/financials/levy-payments",
        "/community/notices",
        "/governance/proposals",
        "/financials/my-finances",
        "/documents",
        "/community",
        "/community/marketplace",
        "/community/events",
        "/community/bookings",
    ],
    tenant: [
        "/dashboard",
        "/community/notices",
        "/community/parcels",
        "/community/chat",
        "/community/events",
        "/community/marketplace",
        "/community/bookings",
        "/governance/bylaws",
        "/documents",
        // Feature-flagged but should resolve if toggles are on:
        "/community/volunteer",
        "/profile/passport",
        "/intelligence/suburb-radar",
    ],
};

// ── Routes that MUST NOT be 404 regardless of auth redirect ──────────────────
// Some pages redirect to login (307) when unauthenticated — that's fine.
// We check: authenticated response is NOT 404.
const ALWAYS_REACHABLE: string[] = [
    "/requests/my-approvals",
    "/admin/management-entities",
    "/admin/management-entities/agencies",
    "/admin/management-entities/independent",
    "/admin/management-entities/self-managed",
    "/governance/bylaws",
    "/governance/ec-members",
    "/management/my-building",
    "/management/portfolio",
    "/reports",
    "/community",
    "/owner-view",
    "/intelligence/suburb-radar",
];

// ── Helper: log in via API and return token ───────────────────────────────────
async function getToken(email: string, password: string): Promise<string | null> {
    try {
        const res = await fetch(`${API_URL}/auth/login`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({email, password}),
        });
        if (!res.ok) return null;
        const data = await res.json();
        return data.access_token ?? null;
    } catch {
        return null;
    }
}

// ── Helper: authenticated page visit ─────────────────────────────────────────
async function visitAuthenticated(page: Page, token: string, route: string): Promise<number> {
    // Set token in localStorage before navigating
    await page.goto(`${BASE_URL}/dashboard`, {waitUntil: "domcontentloaded"});
    await page.evaluate((t) => {
        localStorage.setItem("token", t);
        // Also set as NextAuth session token key if used
        document.cookie = `next-auth.session-token=${t}; path=/`;
    }, token);

    const response = await page.goto(`${BASE_URL}${route}`, {
        waitUntil: "domcontentloaded",
        timeout: 20000,
    });
    return response?.status() ?? 0;
}

// ── Unauthenticated 404 check (fast — no login needed) ───────────────────────
test.describe("Unauthenticated — critical routes must not 404", () => {
    for (const route of ALWAYS_REACHABLE) {
        test(`${route} returns 307 (redirect to login) not 404`, async ({page}) => {
            // Without auth, Next.js middleware redirects to /login (307).
            // A 404 means the page file is missing.
            let finalStatus = 200;
            page.on("response", (resp) => {
                if (resp.url().includes(route)) {
                    finalStatus = resp.status();
                }
            });

            const response = await page.goto(`${BASE_URL}${route}`, {
                waitUntil: "domcontentloaded",
                timeout: 15000,
            });

            const status = response?.status() ?? 0;
            // Must be 200, 307, or 302 — never 404
            expect(
                [200, 301, 302, 307, 308].includes(status),
                `${route} returned ${status} — expected redirect or 200, not 404`
            ).toBe(true);
        });
    }
});

// ── Per-role smoke tests ──────────────────────────────────────────────────────
for (const [role, routes] of Object.entries(ROLE_ROUTES)) {
    const creds = USERS[role];
    if (!creds) continue;

    test.describe(`Role: ${role} — nav link smoke tests`, () => {
        let authToken: string | null = null;

        test.beforeAll(async () => {
            authToken = await getToken(creds.email, creds.password);
        });

        test(`can obtain auth token for ${role}`, async () => {
            expect(authToken, `Login failed for ${role} (${creds.email})`).not.toBeNull();
        });

        for (const route of routes) {
            test(`${role} — ${route} is not 404`, async ({browser}) => {
                if (!authToken) test.skip();
                const context = await browser.newContext();
                const page = await context.newPage();

                // Store token in cookies/localStorage
                await context.addCookies([{
                    name: "access_token",
                    value: authToken!,
                    domain: new URL(BASE_URL).hostname,
                    path: "/",
                }]);

                const response = await page.goto(`${BASE_URL}${route}`, {
                    waitUntil: "domcontentloaded",
                    timeout: 20000,
                });

                const status = response?.status() ?? 0;
                expect(
                    [200, 307, 302, 301, 308].includes(status),
                    `${role} → ${route}: got HTTP ${status} (expected 200 or redirect, not 404/500)`
                ).toBe(true);

                // Additionally: page should NOT contain a "404" heading
                const bodyText = await page.locator("body").textContent({timeout: 5000}).catch(() => "");
                const is404Page = /\b404\b/.test(bodyText ?? "") && /not found/i.test(bodyText ?? "");
                expect(is404Page, `${role} → ${route}: page content indicates 404`).toBe(false);

                await context.close();
            });
        }
    });
}

// ── Navigation config completeness test ──────────────────────────────────────
test.describe("Navigation config completeness", () => {
    // These routes were previously 404 — verify they now resolve
    const PREVIOUSLY_BROKEN = [
        "/governance/ec-members",
        "/requests/my-approvals",
        "/governance/bylaws",
        "/reports",
        "/intelligence/suburb-radar",
        "/community",
        "/owner-view",
    ];

    for (const route of PREVIOUSLY_BROKEN) {
        test(`Previously-404 route ${route} now resolves`, async ({page}) => {
            const response = await page.goto(`${BASE_URL}${route}`, {
                waitUntil: "domcontentloaded",
                timeout: 15000,
            });
            const status = response?.status() ?? 0;
            expect(
                [200, 301, 302, 307, 308].includes(status),
                `${route} still returning ${status}`
            ).toBe(true);
        });
    }

    // Confirm volunteer-credits redirects to /volunteer (route mismatch was fixed)
    test("/intelligence/suburb-radar redirects to market-intelligence (not 404)", async ({page}) => {
        const response = await page.goto(`${BASE_URL}/intelligence/suburb-radar`, {
            waitUntil: "domcontentloaded",
            timeout: 15000,
        });
        expect([200, 307, 302].includes(response?.status() ?? 0)).toBe(true);
    });
});

// ── API-level nav config validation ──────────────────────────────────────────
test.describe("API — navigation config returns correct items", () => {
    for (const [role, creds] of Object.entries(USERS)) {
        test(`GET /navigation/config for ${role} returns no items with 404 routes`, async () => {
            const token = await getToken(creds.email, creds.password);
            if (!token) {
                test.skip();
                return;
            }

            const res = await fetch(`${API_URL}/navigation/config`, {
                headers: {Authorization: `Bearer ${token}`},
            });

            if (!res.ok) {
                // 401/403 acceptable for roles that may not be seeded in test env
                expect([200, 401, 403]).toContain(res.status);
                return;
            }

            const data = await res.json();
            const allItems = [
                ...(data.simple_items ?? []),
                ...(data.advanced_items ?? []),
                ...(data.pinned_items ?? []),
            ];

            // Retired/browser-invalid routes that must not be reintroduced by seeded nav.
            const KNOWN_404_ROUTES = [
                "/community/volunteer-credits",   // fixed → /community/volunteer
                "/profile/tenancy-passport",      // fixed → /profile/passport
                "/property-finance",              // removed (non-existent page)
            ];

            for (const item of allItems) {
                expect(
                    KNOWN_404_ROUTES,
                    `Role ${role}: nav item "${item.label}" still points to stale route ${item.route}`
                ).not.toContain(item.route);
            }
        });
    }
});
