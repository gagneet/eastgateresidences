/**
 * Guards the invariants of the shared chart theme.
 *
 * Added in response to PR #713 review: `sequentialStep()` could return
 * `{bg: undefined, fg: undefined}` for an out-of-range `min`, and nothing
 * covered it. The palette's accessibility properties were verified by a
 * one-off script at authoring time but were likewise untested, so a future
 * swatch edit could silently break contrast.
 */
import {
    CHART_SEQUENTIAL,
    CHART_SERIES,
    CHART_STATUS,
    SEQUENTIAL_INK,
    sequentialStep,
    seriesColor,
} from "@/lib/chartTheme";

/** WCAG relative luminance for an #rrggbb string. */
function luminance(hex: string): number {
    const c = hex.replace("#", "");
    const rgb = [0, 2, 4].map((i) => parseInt(c.substr(i, 2), 16) / 255);
    const lin = rgb.map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrast(a: string, b: string): number {
    const [l1, l2] = [luminance(a), luminance(b)];
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

describe("sequentialStep", () => {
    // The bug the review caught: min >= length made `span` negative, so the
    // index ran past the end of the array and both lookups returned undefined.
    it.each([
        ["negative intensity", -5, undefined],
        ["intensity above 1", 99, undefined],
        ["NaN intensity", NaN, undefined],
        ["min past the end", 0.5, CHART_SEQUENTIAL.length],
        ["min far past the end", 0.5, 999],
        ["negative min", 0.5, -3],
        ["NaN min", 0.5, NaN],
        ["fractional min", 0.5, 6.9],
    ])("stays in bounds for %s", (_label, intensity, min) => {
        const {bg, fg} = min === undefined
            ? sequentialStep(intensity as number)
            : sequentialStep(intensity as number, min as number);
        expect(CHART_SEQUENTIAL).toContain(bg);
        expect(SEQUENTIAL_INK).toContain(fg);
    });

    it("maps the full intensity range onto the ramp, monotonically", () => {
        const indices = [0, 0.25, 0.5, 0.75, 1].map(
            (v) => CHART_SEQUENTIAL.indexOf(sequentialStep(v).bg),
        );
        expect(indices).toEqual([...indices].sort((a, b) => a - b));
        expect(indices[indices.length - 1]).toBe(CHART_SEQUENTIAL.length - 1);
    });

    it("defaults to min=1 so zero can render as an empty surface", () => {
        expect(sequentialStep(0).bg).toBe(CHART_SEQUENTIAL[1]);
    });
});

describe("palette accessibility", () => {
    // Every ramp step must have a readable label, or a heatmap cell's number
    // becomes unreadable. This is the invariant that forced CHART_SEQUENTIAL[5]
    // away from an evenly-spaced value.
    it("gives every sequential step a foreground clearing WCAG AA (4.5:1)", () => {
        CHART_SEQUENTIAL.forEach((bg, i) => {
            expect(contrast(bg, SEQUENTIAL_INK[i])).toBeGreaterThanOrEqual(4.5);
        });
    });

    it("keeps the ink table the same length as the ramp", () => {
        expect(SEQUENTIAL_INK).toHaveLength(CHART_SEQUENTIAL.length);
    });

    it("has no duplicate categorical series colours", () => {
        expect(new Set(CHART_SERIES).size).toBe(CHART_SERIES.length);
    });

    it("keeps status colours out of the categorical palette", () => {
        // Status hues carry meaning; reusing one as "series 4" would make a
        // neutral series read as an alert.
        Object.values(CHART_STATUS).forEach((s) => {
            expect(CHART_SERIES as readonly string[]).not.toContain(s);
        });
    });
});

describe("seriesColor", () => {
    it("returns the fixed slot for each index", () => {
        CHART_SERIES.forEach((c, i) => expect(seriesColor(i)).toBe(c));
    });

    it("folds past the end into the neutral colour instead of cycling", () => {
        // Cycling would give two different entities the same colour.
        expect(seriesColor(CHART_SERIES.length)).not.toBe(CHART_SERIES[0]);
        expect(seriesColor(CHART_SERIES.length + 5)).toBe(seriesColor(CHART_SERIES.length));
    });
});
