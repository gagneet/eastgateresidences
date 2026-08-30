/**
 * Structural contract for migrated pages.
 *
 * WHY THIS EXISTS
 * The design-token ratchet counts raw-palette CLASS STRINGS. That is a proxy, and
 * this session proved how weak a proxy it is: every /intelligence/* page reached
 * zero raw-palette classes in Phase 3 and still looked unchanged, because each one
 * hand-rolled its own header at text-3xl/font-bold while the migrated pages use
 * PageHeader at text-2xl/font-semibold. A colour swap is nearly invisible; the
 * chrome is what a user actually perceives as "the same application".
 *
 * So this asserts the STRUCTURE the ratchet cannot see:
 *   - the page renders PageHeader (which owns the single <h1>)
 *   - it does not hand-roll a competing <h1>
 *   - it carries no glassmorphic card shell, arbitrary radius, or font-black
 *
 * Static scan on purpose: no DOM, no rendering, runs in milliseconds, and fails at
 * the line where a regression is written rather than after a visual review that may
 * never happen.
 */
import {readFileSync, existsSync} from "fs";
import {join} from "path";

const ROOT = join(__dirname, "..", "..", "..", "frontend");

/** Pages migrated to the design system. Add to this list as GAP-UI-002 progresses. */
const MIGRATED_PAGES = [
    "src/pages/dashboard/FinanceIntelligencePage.jsx",
    "src/pages/dashboard/LevyStabilityPage.jsx",
    "src/pages/dashboard/CapitalRiskPage.jsx",
    "src/pages/dashboard/IntelligenceAssetsPage.jsx",
    "src/pages/dashboard/OccupancyIntelligencePage.jsx",
    "src/pages/dashboard/SafetyFeedPage.tsx",
    "src/pages/dashboard/intelligence/LevyScenariosPage.tsx",
    "src/pages/dashboard/LevyFairnessPage.jsx",
    "src/pages/dashboard/BuildingHealthPage.jsx",
    "src/pages/dashboard/FinancialProjectionsPage.jsx",
    "src/app/(app)/intelligence/market/page.tsx",
    "src/app/(app)/intelligence/building/page.tsx",
    "src/pages/dashboard/OwnerDashboard.tsx",
];

/**
 * Shared components migrated to the design system (GAP-UI-002 batch 1).
 *
 * These are NOT pages, so the PageHeader/<h1> assertions above do not apply — a
 * component that rendered its own <h1> would be the bug. What they must satisfy is
 * the rest of the contract: no raw palette, no competing card language, and no
 * private chart palette. Each of these renders on several dashboards, so a
 * regression here re-infects every page that mounts it.
 */
const MIGRATED_COMPONENTS = [
    "src/components/dashboard/PropertyServicesActionCards.jsx",
    "src/components/dashboard/WaterBillCard.jsx",
    "src/components/finance/LevyKpiDialog.tsx",
];

/**
 * Same families the token ratchet watches (scripts/check-design-tokens.mjs).
 * Kept in step with it deliberately: the ratchet can only stop a file getting
 * WORSE, and a baselined file at zero would silently absorb new raw classes up to
 * its old count if the baseline were ever regenerated carelessly. For the handful
 * of files that have actually reached zero, zero is asserted outright.
 */
const NEUTRAL = "gray|slate|zinc|neutral|stone";
const DECORATIVE = "indigo|violet|purple|fuchsia|pink|cyan|sky|blue|teal";
const RAW_PALETTE = new RegExp(
    String.raw`\b(?:bg-white|text-black|(?:text|bg|border|ring|divide|from|to|via)-(?:${NEUTRAL}|${DECORATIVE})-\d{2,3})\b`,
    "g",
);

/** Strip comments so a `<h1>` mentioned in a code comment is not read as markup. */
function code(src: string): string {
    return src
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .split("\n")
        .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
        .join("\n");
}

describe("design-system structural contract", () => {
    it.each(MIGRATED_PAGES)("%s exists", (rel) => {
        expect(existsSync(join(ROOT, rel))).toBe(true);
    });

    it.each(MIGRATED_PAGES)("%s renders PageHeader", (rel) => {
        expect(code(readFileSync(join(ROOT, rel), "utf8"))).toContain("<PageHeader");
    });

    it.each(MIGRATED_PAGES)("%s does not hand-roll its own <h1>", (rel) => {
        // PageHeader supplies the page's single <h1>. A second one competes with it
        // and breaks the document outline for screen readers.
        const matches = code(readFileSync(join(ROOT, rel), "utf8")).match(/<h1[\s>]/g) || [];
        expect(matches).toHaveLength(0);
    });

    it.each(MIGRATED_PAGES)("%s does not constrain its own width", (rel) => {
        // DashboardLayout's <main className="p-4 lg:p-6"> already supplies the page's
        // padding, at full width. A page adding `container mx-auto` on top caps the
        // width at the breakpoint maximum and centres what is left — which is why the
        // /intelligence/* routes rendered with symmetric margins while every other
        // route ran edge to edge. Nine pages had it.
        //
        // The token migration missed this entirely because §3's "done" definition
        // covered colour, chrome, tiles, tables and charts, but never page width. A
        // page can be at zero raw-palette classes and still be visibly the odd one out.
        const src = code(readFileSync(join(ROOT, rel), "utf8"));
        expect(src).not.toMatch(/container mx-auto/);
    });

    it.each(MIGRATED_PAGES)("%s carries no competing card language", (rel) => {
        const src = code(readFileSync(join(ROOT, rel), "utf8"));
        // Each of these is a visual language from before the migration. They are what
        // made the section read as bolted on, and none is caught by the token ratchet.
        expect(src).not.toMatch(/backdrop-blur/);      // glassmorphic shell
        expect(src).not.toMatch(/rounded-\[[^\]]+\]/); // arbitrary radius vs rounded-xl
        expect(src).not.toMatch(/font-black/);         // outside the type scale
        expect(src).not.toMatch(/shadow-2xl/);         // outside the elevation scale
    });
});

describe("design-system structural contract — shared components", () => {
    it.each(MIGRATED_COMPONENTS)("%s exists", (rel) => {
        expect(existsSync(join(ROOT, rel))).toBe(true);
    });

    it.each(MIGRATED_COMPONENTS)("%s uses no raw palette classes", (rel) => {
        const found = readFileSync(join(ROOT, rel), "utf8").match(RAW_PALETTE) || [];
        expect(found).toEqual([]);
    });

    it.each(MIGRATED_COMPONENTS)("%s does not render its own <h1>", (rel) => {
        // A component mounted inside a page must not compete with PageHeader's <h1>.
        const matches = code(readFileSync(join(ROOT, rel), "utf8")).match(/<h1[\s>]/g) || [];
        expect(matches).toHaveLength(0);
    });

    it.each(MIGRATED_COMPONENTS)("%s carries no competing card language", (rel) => {
        const src = code(readFileSync(join(ROOT, rel), "utf8"));
        expect(src).not.toMatch(/backdrop-blur/);
        expect(src).not.toMatch(/rounded-\[[^\]]+\]/);
        expect(src).not.toMatch(/font-black/);
        expect(src).not.toMatch(/shadow-2xl/);
    });

    it.each(MIGRATED_COMPONENTS)("%s declares no private chart palette", (rel) => {
        // Six-digit hex literals are how every pre-migration file grew its own chart
        // colours. Colours come from lib/chartTheme so two charts never disagree.
        const src = code(readFileSync(join(ROOT, rel), "utf8"));
        expect(src.match(/#[0-9a-fA-F]{6}\b/g) || []).toEqual([]);
    });
});
