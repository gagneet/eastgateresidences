/**
 * GAP-UI-001 — visual verification sweep for the design-system unification.
 *
 * The token/table/chart migration in PR #713 is type-checked, test-covered and
 * build-clean, but nothing in it was ever rendered on a screen. This spec is the
 * cheapest way to close that gap without deploying: it logs in once, walks every
 * /intelligence/* route, and for each one captures
 *
 *   1. a full-page screenshot, so layout/typography/colour can be eyeballed, and
 *   2. any console error or failed request, so a runtime break cannot hide behind
 *      a page that merely *looks* fine.
 *
 * The login flow, capture loop and assertions live in ./helpers/visual-sweep.js —
 * see that file for how to run this and why each assertion is the shape it is.
 * `requireSingleH1` is on here because every route below has had its PAGE migrated
 * to PageHeader, so a missing or duplicated <h1> is a real regression.
 *
 * Screenshots land in tests/reports/intelligence-visual/.
 */
const {defineVisualSweep} = require('./helpers/visual-sweep');

// Every route touched by GAP-UI-001 Phase 1/3, plus the Phase 2 page so the
// before/after is captured on the same run.
const ROUTES = [
    ['bi', '/intelligence/bi'],
    ['bi-platform', '/intelligence/bi/platform'],
    ['financial', '/intelligence/financial'],
    ['assets', '/intelligence/assets'],
    ['building', '/intelligence/building'],
    ['building-health', '/intelligence/building-health'],
    ['building-stress', '/intelligence/building-stress'],
    ['capital-planner', '/intelligence/capital-planner'],
    ['capital-risk', '/intelligence/capital-risk'],
    ['debt-recovery', '/intelligence/debt-recovery'],
    ['global-risk', '/intelligence/global-risk'],
    ['insurance-lending', '/intelligence/insurance-lending'],
    ['investor', '/intelligence/investor'],
    ['levy-fairness', '/intelligence/levy-fairness'],
    ['levy-scenarios', '/intelligence/levy-scenarios'],
    ['levy-stability', '/intelligence/levy-stability'],
    ['market', '/intelligence/market'],
    ['occupancy', '/intelligence/occupancy'],
    ['projections', '/intelligence/projections'],
    ['safety-feed', '/intelligence/safety-feed'],
    ['suburb-radar', '/intelligence/suburb-radar'],
];

defineVisualSweep({
    title: 'GAP-UI-001 intelligence visual verification',
    routes: ROUTES,
    out: 'tests/reports/intelligence-visual',
    requireSingleH1: true,
});
