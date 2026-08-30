import fs from 'fs';
import path from 'path';

/**
 * Guards the shape of the dashboard's data loader — the thing that decides how long
 * a user stares at a skeleton.
 *
 * These are source-text assertions, which is unusual, but the property being guarded
 * (what runs concurrently, and what first paint waits on) is invisible to a rendering
 * test: with everything mocked, a sequential loader and a parallel one both resolve on
 * the same tick and look identical. The failure mode is real and has happened — the
 * classic dashboards drifted into 20 chained `await`s — so it is worth pinning.
 *
 * Rewritten 2026-08-24: the previous version asserted on two specific local variable
 * names (`coreDashboardPromise` / `ownerDashboardPromise`), so it broke the moment the
 * loader was restructured even though the invariant it cared about still held. It now
 * asserts the invariants themselves.
 */
describe('DashboardPage performance-sensitive data loader', () => {
    const source = () => fs.readFileSync(
        path.join(process.cwd(), 'src/app/(dashboard)/dashboard/page.tsx'),
        'utf8',
    );

    it('does not block first render on the full arrears detail board', () => {
        // The header cards get their aggregate arrears figure from
        // /finance/building-overview. Pulling the whole per-unit board here would add
        // one of the slowest finance calls to first paint for no extra information.
        expect(source()).not.toMatch(/get\([`'"]\/arrears\/detail/);
    });

    it('starts every request wave before awaiting any of them', () => {
        const content = source();
        const heroDeclared = content.indexOf('const heroPromise');
        const secondaryDeclared = content.indexOf('const secondaryPromise');
        const firstAwait = content.indexOf('await heroPromise');

        expect(heroDeclared).toBeGreaterThan(-1);
        expect(secondaryDeclared).toBeGreaterThan(-1);
        expect(firstAwait).toBeGreaterThan(-1);

        // Both waves must be in flight before either is awaited. If the secondary wave
        // were declared after `await heroPromise`, the two waves would serialise and the
        // page would cost hero+secondary instead of max(hero, secondary).
        expect(secondaryDeclared).toBeLessThan(firstAwait);
        expect(heroDeclared).toBeLessThan(firstAwait);
    });

    it('starts owner-specific requests in the same wave as building-wide ones', () => {
        const content = source();
        const hero = content.slice(
            content.indexOf('const heroPromise'),
            content.indexOf('const secondaryPromise'),
        );
        // The owner's own money and the building-wide figures both sit above the fold,
        // so they belong in one concurrent wave — never chained one after the other.
        expect(hero).toContain('/finance/building-overview');
        expect(hero).toContain('/finance/unit-dashboard-overview/');
        expect(hero).toContain('/owner-hub/unit-tco');
    });

    it('releases the first-paint skeleton before the secondary wave resolves', () => {
        const content = source();
        const releaseSkeleton = content.indexOf('setLoading(false)');
        const awaitSecondary = content.indexOf('await secondaryPromise');

        expect(releaseSkeleton).toBeGreaterThan(-1);
        expect(awaitSecondary).toBeGreaterThan(-1);
        // Progressive render: the page becomes usable once the hero cards land, rather
        // than waiting on the slowest chart in the fan-out.
        expect(releaseSkeleton).toBeLessThan(awaitSecondary);
    });

    it('keeps the page dimmed until the whole fan-out lands, not just the hero wave', () => {
        const content = source();
        // `loading` is released early (previous test), so it must NOT be what dims the
        // page during a refresh — otherwise a year switch would briefly show the
        // PREVIOUS year's charts undimmed and interactive while their wave is still in
        // flight. That is what `refreshing` is for.
        expect(content).toContain("${refreshing && data ? 'opacity-60 pointer-events-none'");
        expect(content).not.toContain("${loading && data ? 'opacity-60 pointer-events-none'");
    });
});
