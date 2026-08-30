/**
 * GAP-UI-002 — visual verification for the app-wide design unification.
 *
 * Batch 1 migrated three SHARED COMPONENTS rather than pages, so this sweep walks
 * the routes that actually mount them. See ./helpers/visual-sweep.js for how to
 * run it and what it asserts.
 *
 * `requireSingleH1` is OFF here, unlike the intelligence sweep. These pages have
 * not been migrated yet — only their children have — and PageHeader is what
 * supplies the <h1>. Asserting it now would report work that is not due until the
 * page's own batch. Turn it on per route group as those batches land.
 *
 * Screenshots land in tests/reports/design-system-visual/.
 *
 * NOT COVERED, deliberately: LevyKpiDialog. It was migrated in batch 1 but has no
 * production consumer — nothing in src/ imports it outside its own unit test (its
 * FeatureTrace header claimed ManagementDashboard/OwnerDashboard render it; neither
 * does). There is no route to screenshot it on. If it is ever wired up, add that
 * route here and open the dialog before the capture.
 */
const {defineVisualSweep} = require('./helpers/visual-sweep');

const ROUTES = [
    // PropertyServicesActionCards — the six service tiles.
    ['owner-hub-classic', '/owner-hub/classic'],
    // WaterBillCard.
    ['water-bills', '/financials/water-bills'],
    ['council-rates', '/financials/council-rates'],
    // The full-page levy KPI view; the dialog variant is unmounted (see above).
    ['levy-kpi', '/financials/levy-kpi'],
];

defineVisualSweep({
    title: 'GAP-UI-002 design-system visual verification',
    routes: ROUTES,
    out: 'tests/reports/design-system-visual',
    requireSingleH1: false,
});
