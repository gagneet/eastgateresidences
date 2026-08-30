/**
 * Shared harness for the design-system visual sweeps.
 *
 * WHY THIS FILE EXISTS
 * `intelligence-visual-verification.spec.js` worked out, in painful detail, how to
 * log this app in from Playwright unattended: which locator is unambiguous, why
 * outcome detection has to be URL-based, and how to report a bad credential as a
 * cause rather than a timeout. GAP-UI-002 needs exactly the same sweep over a
 * different route list, and every batch after it will need one more. Copying 120
 * lines per batch guarantees the copies drift and the hard-won details rot out of
 * the newer ones, so the harness lives here and the specs are just route lists.
 *
 * What a sweep asserts is deliberately thin. A screenshot diff would fail on every
 * intended change, so appearance is captured for a human and the machine only
 * checks what a human cannot do faster by looking: did the route render, did a
 * guard bounce us, did the console log an error.
 *
 * Run against a LOCAL stack — never production; it navigates while logged in.
 *
 *   cd backend && uvicorn server:app --port 8003     # terminal 1
 *   cd frontend && yarn dev                          # terminal 2
 *   BASE_URL=http://localhost:3000 \
 *   E2E_EMAIL=<super_admin email> E2E_PASSWORD=<password> \
 *     npx playwright test tests/frontend/e2e/<spec> --project=chromium --reporter=list
 *
 * Pin --project=chromium: the config defines five browser projects, so without it
 * the sweep runs five times and the screenshots overwrite each other.
 *
 * Credentials come from the environment — never hardcode them in a spec.
 */
const {test, expect} = require('@playwright/test');

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

/**
 * Log in once and hand back the page, or throw with a cause a reader can act on.
 *
 * @param {import('@playwright/test').Browser} browser
 * @param {string} out  Directory for the failure screenshot.
 */
async function loginOnce(browser, out) {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    await page.goto(`${BASE}/login`, {waitUntil: 'domcontentloaded', timeout: 120000});

    // Scope to the login form: the public header also carries a "Sign In"
    // button, so an unscoped locator is ambiguous.
    const form = page.getByTestId('login-form');
    await form.waitFor({state: 'visible', timeout: 120000});
    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASSWORD);
    await form.locator('button[type="submit"]').click();

    // Outcome detection is URL-based on purpose. An earlier version raced a
    // visible-error locator, but the login page's only role="alert" is a
    // conditional pending-approval banner and the app mounts a toast container
    // that reads as visible-but-empty — so the locator matched nothing meaningful
    // and reported "rejected" with a blank message. Leaving /login is the one
    // unambiguous success signal.
    const outcome = await Promise.race([
        page.waitForURL((u) => !u.pathname.startsWith('/login'), {timeout: 150000})
            .then(() => 'left-login'),
        page.waitForURL('**/login/totp-challenge**', {timeout: 150000}).then(() => 'totp'),
    ]).catch(() => 'stayed');

    if (outcome === 'totp') {
        throw new Error(
            'Login stopped at the TOTP challenge — this account has two-factor enabled, so it ' +
            'cannot drive an unattended sweep. Use an account without TOTP, or pre-seed a ' +
            'session with storageState.',
        );
    }

    if (outcome === 'stayed') {
        // Still on /login after the click. Capture whatever the page is actually
        // saying so the failure names a cause instead of a timeout.
        await page.screenshot({path: `${out}/_login-failure.png`, fullPage: true});
        const toast = await page.locator('[data-sonner-toast], [role="alert"]')
            .allInnerTexts().catch(() => []);
        const said = toast.map((t) => t.trim()).filter(Boolean).join(' | ') || '(no message shown)';
        throw new Error(
            `Login did not leave /login. Page said: ${said}\n` +
            `Most likely a wrong credential for ${EMAIL}. Screenshot: ${out}/_login-failure.png\n` +
            'Note: when the backend runs with APP_ENV=production it rejects any account flagged ' +
            'is_test_data, so a seeded test user will fail here by design.',
        );
    }

    // Landed somewhere authenticated — settle before the route walk starts, but do
    // not require the dashboard: some roles land elsewhere.
    await page.waitForLoadState('networkidle', {timeout: 60000}).catch(() => {});
    return page;
}

/**
 * Define a visual sweep suite.
 *
 * @param {object}   opts
 * @param {string}   opts.title            Suite name.
 * @param {Array}    opts.routes           `[[name, path], …]`.
 * @param {string}   opts.out              Screenshot directory.
 * @param {boolean} [opts.requireSingleH1] Assert exactly one `<h1>`. Only true for
 *                                         route groups whose PAGES are migrated —
 *                                         PageHeader is what supplies that `<h1>`,
 *                                         so asserting it on an unmigrated page
 *                                         just reports work that is not due yet.
 */
function defineVisualSweep({title, routes, out, requireSingleH1 = false}) {
    test.describe(title, () => {
        test.skip(!EMAIL || !PASSWORD, 'Set E2E_EMAIL and E2E_PASSWORD to run this sweep.');
        test.describe.configure({mode: 'serial'});

        let page;

        test.beforeAll(async ({browser}) => {
            // A `beforeAll` hook carries its OWN timeout (30s by default), which caps
            // every await inside it — an inner waitForURL({timeout: 60000}) can never
            // elapse. Raise the hook budget explicitly, because against a cold `next
            // dev` server the first compile of /login and /dashboard can take tens of
            // seconds on its own.
            test.setTimeout(180000);
            page = await loginOnce(browser, out);
        });

        test.afterAll(async () => { await page?.close(); });

        for (const [name, route] of routes) {
            test(`${name} renders without console errors`, async () => {
                const problems = [];
                const onConsole = (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); };
                const onFailed = (r) => problems.push(`request failed: ${r.url()}`);
                page.on('console', onConsole);
                page.on('requestfailed', onFailed);

                await page.goto(`${BASE}${route}`, {waitUntil: 'networkidle', timeout: 60000});
                // Charts mount after data resolves; give recharts a beat to paint.
                await page.waitForTimeout(1500);
                await page.screenshot({path: `${out}/${test.info().project.name}/${name}.png`, fullPage: true});

                page.off('console', onConsole);
                page.off('requestfailed', onFailed);

                // A redirect away from the route means a guard rejected us — that is a
                // real finding (wrong role, or a broken FeatureGuard), not a pass.
                expect(page.url(), `redirected away from ${route}`).toContain(route);

                if (requireSingleH1) {
                    // PageHeader supplies it, and the gradient <div> hero bands it
                    // replaced supplied none.
                    const h1s = await page.locator('h1').count();
                    expect(h1s, `${route} should have exactly one <h1>`).toBe(1);
                }

                expect(problems, `${route} logged errors:\n${problems.join('\n')}`).toEqual([]);
            });
        }
    });
}

module.exports = {BASE, EMAIL, PASSWORD, loginOnce, defineVisualSweep};
