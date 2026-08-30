/**
 * @featuretrace:design-system — Canonical chart palette, ramps and recharts chrome.
 * Layer: frontend
 * Data flow: lib/chartTheme.ts → every recharts chart + ChartCard/StatTile → rendered dashboards.
 * Related: frontend/src/components/dashboard/ChartCard.tsx
 *           frontend/src/components/shared/StatTile.tsx
 *           frontend/scripts/check-design-tokens.mjs (ratchet)
 * Toggle: none
 * Tests: tests/frontend/unit/lib/chartTheme.test.ts
 *
 * Canonical chart theme — the single source of truth for every chart colour,
 * axis, grid and tooltip in the application.
 *
 * WHY THIS FILE EXISTS
 * Before GAP-UI-001, 42 separate files hand-declared their own hex palettes
 * (`BIAnalyticsPage` alone declared 34 distinct literals), so no two charts in
 * the app agreed on what "series 1" looked like. Import from here instead of
 * typing a hex into a chart.
 *
 * The categorical palette is validated — every adjacent pair clears the
 * colour-vision-deficiency separation floor, the OKLCH lightness band and the
 * chroma floor against this app's `--background` surface. Do not hand-edit a
 * swatch: re-run the validator if the palette ever needs to change.
 *
 * The app is light-mode only today (no `.dark` block in globals.css, no
 * ThemeProvider mounted), so these are light-surface values. If dark mode is
 * ever introduced, dark steps must be *chosen and validated* against the dark
 * surface — never derived by flipping these.
 */

/**
 * Categorical series colours, in fixed assignment order.
 *
 * Assign by the entity's stable identity, never by its rank in the current
 * result set — otherwise filtering a series out repaints every survivor.
 * There is deliberately no 9th colour: see {@link seriesColor}.
 *
 * PAIRED COMPARISONS (charged vs paid, planned vs actual, budget vs spend) use
 * two slots from this palette — slot 0 and slot 2 are the validated default pair
 * (CVD ΔE 14.5 deutan, normal-vision ΔE 16.4, both >= 3:1 on the card surface).
 * Do NOT paint the reference half with a neutral grey or with CHART_INK.grid:
 * a grey that is light enough to read as a backdrop lands at ΔE 3.6 (protan)
 * against the teal it sits beside, which is a hard accessibility failure, and
 * grid ink is ~1.1:1 against the card and effectively invisible.
 */
export const CHART_SERIES = [
    "#00a2ad", // 1 teal        — brand primary family
    "#de7949", // 2 terracotta  — brand secondary family
    "#496dc3", // 3 blue
    "#caac2f", // 4 ochre       — brand accent family
    "#94468f", // 5 plum
    "#54b66e", // 6 green
    "#a73447", // 7 crimson
    "#b191ea", // 8 violet
] as const;

/** Anything beyond the 8th series folds into this. Fold the tail into an "Other" bucket rather than generating a 9th hue. */
export const CHART_SERIES_OTHER = "#85919a";

/**
 * Colour for the i-th series. Past the palette length this returns the neutral
 * "Other" colour rather than cycling — a cycled palette silently gives two
 * different entities the same colour.
 */
export function seriesColor(i: number): string {
    return CHART_SERIES[i] ?? CHART_SERIES_OTHER;
}

/**
 * Single-hue ramp for magnitude (heatmaps, choropleths, intensity). Light → dark.
 *
 * Every step is guaranteed to have a foreground in {@link SEQUENTIAL_INK} that
 * clears WCAG AA (4.5:1) for normal text, so a cell can always carry a readable
 * label. Step 5 is `#007d86` rather than the geometrically-even `#00848c`
 * specifically because `#00848c` peaks at 4.49:1 against BOTH white (4.49) and
 * near-black (3.96) — it is the one step where an evenly-spaced ramp has no
 * accessible foreground at all. Lightness stays monotonic across the ramp.
 */
export const CHART_SEQUENTIAL = [
    "#d8f5f6", "#a9e4e7", "#71d0d5", "#14bbc2", "#00a2ad", "#007d86", "#00666d",
] as const;

/**
 * Accessible label colour for each {@link CHART_SEQUENTIAL} step, same index.
 *
 * Do NOT re-derive this from an intensity threshold like `intensity > 0.5 ? white
 * : black`. That is what this replaced, and it failed AA across the middle of the
 * ramp (white on `#00a2ad` is only 3.10:1). Contrast is a property of the STEP,
 * not of the underlying value, so it is tabulated per step.
 *
 * Measured contrast vs each step: 15.48, 12.63, 9.88, 7.54, 5.72, 4.91, 6.73.
 */
export const SEQUENTIAL_INK = [
    "#0f1a1a", "#0f1a1a", "#0f1a1a", "#0f1a1a", "#0f1a1a", "#ffffff", "#ffffff",
] as const;

/**
 * Pick a sequential step and its guaranteed-readable label colour from a
 * normalised intensity.
 *
 * @param intensity 0..1. Values outside the range are clamped.
 * @param min       Lowest ramp index to use. Defaults to 1 so that "zero" can be
 *                  rendered as an empty surface instead of the palest step,
 *                  keeping "no data" visually distinct from "lowest value".
 */
export function sequentialStep(intensity: number, min = 1): { bg: string; fg: string } {
    const last = CHART_SEQUENTIAL.length - 1;

    // Clamp rather than throw. This runs inside a render path (heatmap cells call
    // it once per cell), and a bad `min` should degrade to a slightly-off colour,
    // never take a finance dashboard down with an exception. NaN is handled by the
    // clamps too: Math.min/Math.max propagate it, so it is normalised to 0 first.
    const safeMin = Number.isFinite(min) ? Math.min(Math.max(Math.trunc(min), 0), last) : 0;
    const clamped = Number.isFinite(intensity) ? Math.max(0, Math.min(1, intensity)) : 0;

    const span = last - safeMin;                       // >= 0, because safeMin <= last
    const i = Math.min(safeMin + Math.round(clamped * span), last);

    return {bg: CHART_SEQUENTIAL[i], fg: SEQUENTIAL_INK[i]};
}

/**
 * Two-hue ramp for polarity (variance vs budget, surplus/deficit).
 * Terracotta (negative) → neutral midpoint → teal (positive).
 * The midpoint is deliberately a neutral, never a hue.
 */
export const CHART_DIVERGING = [
    "#a7391e", "#c36a4f", "#d79a83", "#dfdeda", "#69bbba", "#009ba0", "#007780",
] as const;

/**
 * Reserved status colours. These carry meaning (compliance state, arrears
 * severity, risk tier) and are never reused as "series 5".
 *
 * Status must never be encoded by colour alone — always pair with an icon or a
 * text label so it survives greyscale printing and colour-vision deficiency.
 */
export const CHART_STATUS = {
    good: "#2a904b",
    warning: "#cf9b00",
    serious: "#da6c1e",
    critical: "#c2272d",
    neutral: "#85919a",
} as const;

export type ChartStatus = keyof typeof CHART_STATUS;

/** Chart chrome, bound to the design tokens in globals.css. */
export const CHART_INK = {
    /** Recessive gridlines. */
    grid: "hsl(60 10% 90%)",
    /** Axis lines and ticks. */
    axis: "hsl(215 16% 47%)",
    /** Axis tick labels. */
    label: "hsl(215 16% 47%)",
    /** Tooltip / popover surface. */
    surface: "hsl(0 0% 100%)",
    /** Primary ink on a tooltip. */
    text: "hsl(0 0% 10%)",
} as const;

/**
 * Spread onto a recharts `<CartesianGrid>` — horizontal rules only, so the grid
 * stays recessive and never competes with the marks.
 */
export const gridProps = {
    stroke: CHART_INK.grid,
    strokeDasharray: "3 3",
    vertical: false,
} as const;

/** Spread onto a recharts `<XAxis>` / `<YAxis>`. */
export const axisProps = {
    stroke: CHART_INK.axis,
    tick: {fill: CHART_INK.label, fontSize: 12},
    tickLine: false,
    axisLine: false,
} as const;

/** Spread onto a recharts `<Tooltip>`. */
export const tooltipProps = {
    cursor: {fill: "hsl(60 10% 95%)"},
    contentStyle: {
        background: CHART_INK.surface,
        border: "1px solid hsl(60 10% 90%)",
        borderRadius: "0.75rem",
        boxShadow: "0 4px 12px rgba(0,0,0,0.06)",
        fontSize: 12,
        color: CHART_INK.text,
    },
    labelStyle: {color: CHART_INK.text, fontWeight: 600},
} as const;

/** Rounded data-end radius for a vertical `<Bar>`, anchored to the baseline. */
export const barRadius: [number, number, number, number] = [4, 4, 0, 0];
