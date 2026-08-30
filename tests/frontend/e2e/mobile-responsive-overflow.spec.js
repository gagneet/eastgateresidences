/**
 * Mobile responsive overflow regression coverage.
 *
 * This spec verifies the failure mode reported for iPhone 14 Pro and Samsung
 * Galaxy S24 Ultra widths: content, text, buttons, overlays, and decorative
 * elements must not force page-level horizontal scrolling.
 *
 * Public routes run without credentials. Authenticated routes are deliberately
 * skipped unless E2E_EMAIL and E2E_PASSWORD are supplied, because a redirect to
 * /login is not evidence that the dashboard UI rendered correctly.
 */
const {test, expect} = require('@playwright/test');
const {BASE, EMAIL, PASSWORD, loginOnce} = require('./helpers/visual-sweep');

const VIEWPORTS = [
    {name: 'iPhone 14 Pro', width: 393, height: 852},
    {name: 'Samsung Galaxy S24 Ultra', width: 430, height: 932},
];

const PUBLIC_ROUTES = [
    ['home', '/'],
    ['login', '/login'],
    ['forgot-password', '/forgot-password'],
    ['marketplace', '/marketplace'],
    ['emergency-services', '/emergency-services'],
];

const AUTHENTICATED_ROUTES = [
    ['dashboard', '/dashboard'],
    ['financial-overview', '/financials/overview'],
    ['owner-hub-classic', '/owner-hub/classic'],
    ['water-bills', '/financials/water-bills'],
    ['council-rates', '/financials/council-rates'],
];

async function mobileOverflowReport(page) {
    return page.evaluate(() => {
        const width = window.innerWidth;
        const documentScrollWidth = Math.max(
            document.documentElement.scrollWidth,
            document.body.scrollWidth,
        );
        // A wide element is only acceptable when an ancestor actually SCROLLS
        // it — a table inside the overflow-x:auto wrapper ui/table.tsx renders
        // is a deliberate scroll region, and checking the element's own
        // overflowX alone would flag the table itself.
        //
        // Ancestors that merely clip (overflow-x: hidden/clip, which both
        // <html> and the dashboard <main> use as a safety net) are NOT a pass:
        // clipping hides the content instead of making it reachable, so that
        // is still a defect this spec must report.
        const scrolledByAncestor = (el) => {
            for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
                const overflowX = window.getComputedStyle(parent).overflowX;
                if (overflowX === 'auto' || overflowX === 'scroll') {
                    return true;
                }
            }
            return false;
        };
        const overflowElements = Array.from(document.querySelectorAll('body *'))
            .filter((el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const intentionallyScrollable = style.overflowX === 'auto' || style.overflowX === 'scroll';
                if (intentionallyScrollable) {
                    return false;
                }
                // A fixed element is positioned against the viewport, so an
                // ancestor scroll container never brings it back into reach.
                // The FAB and any fixed toolbar must be judged on their own
                // rect regardless of what they happen to sit inside.
                if (style.position !== 'fixed' && scrolledByAncestor(el)) {
                    return false;
                }
                return rect.left < -2 || rect.right > width + 2;
            })
            .slice(0, 12)
            .map((el) => {
                const rect = el.getBoundingClientRect();
                return {
                    tag: el.tagName,
                    className: String(el.className || '').slice(0, 160),
                    text: String(el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120),
                    left: Math.round(rect.left),
                    right: Math.round(rect.right),
                    width: Math.round(rect.width),
                };
            });

        return {
            viewportWidth: width,
            documentScrollWidth,
            overflowElements,
        };
    });
}

async function expectNoMobileOverflow(page, routeName, viewportName) {
    const report = await mobileOverflowReport(page);
    expect(
        report.documentScrollWidth,
        `${routeName} at ${viewportName} should not create document-level horizontal scroll`,
    ).toBeLessThanOrEqual(report.viewportWidth + 1);
    expect(
        report.overflowElements,
        `${routeName} at ${viewportName} has off-viewport elements: ${JSON.stringify(report.overflowElements, null, 2)}`,
    ).toEqual([]);
}

test.describe('mobile responsive overflow - public routes', () => {
    for (const viewport of VIEWPORTS) {
        test.describe(viewport.name, () => {
            test.use({
                viewport: {width: viewport.width, height: viewport.height},
                isMobile: true,
                deviceScaleFactor: 3,
            });

            for (const [routeName, route] of PUBLIC_ROUTES) {
                test(`${routeName} stays inside the viewport`, async ({page}) => {
                    await page.goto(`${BASE}${route}`, {waitUntil: 'networkidle', timeout: 60000});
                    await expectNoMobileOverflow(page, routeName, viewport.name);
                });
            }
        });
    }
});

test.describe('mobile responsive overflow - authenticated routes', () => {
    test.skip(!EMAIL || !PASSWORD, 'Set E2E_EMAIL and E2E_PASSWORD to verify authenticated mobile routes.');
    test.describe.configure({mode: 'serial'});

    let page;

    test.beforeAll(async ({browser}) => {
        test.setTimeout(180000);
        page = await loginOnce(browser, 'tests/reports/mobile-responsive-overflow');
    });

    test.afterAll(async () => {
        await page?.close();
    });

    for (const viewport of VIEWPORTS) {
        for (const [routeName, route] of AUTHENTICATED_ROUTES) {
            test(`${routeName} stays inside ${viewport.name} viewport`, async () => {
                await page.setViewportSize({width: viewport.width, height: viewport.height});
                await page.goto(`${BASE}${route}`, {waitUntil: 'networkidle', timeout: 60000});
                expect(page.url(), `redirected away from authenticated route ${route}`).toContain(route);
                await expectNoMobileOverflow(page, routeName, viewport.name);
            });
        }
    }
});
