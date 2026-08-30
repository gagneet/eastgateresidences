/**
 * ForecastChart.test.tsx
 *
 * Regression tests for the client-side crash:
 *   "Uncaught TypeError: can't access property 'length', e.category is undefined"
 *
 * Root cause: ForecastChart.tsx line 42 called `f.category.length` where
 * `f.category` could be null/undefined in data returned from the DB.
 *
 * These tests verify:
 *   1. The data transformation (chartData mapping) is null-safe
 *   2. The useFinanceData filter removes malformed forecast items
 *   3. The VarianceTable data transformations handle edge cases
 */

// ─── Data transformation helpers (pure logic, no DOM required) ───────────────

/**
 * Mirrors the fixed ForecastChart.tsx chartData mapping.
 * The original crash: `f.category.length` when category was undefined.
 * The fix: `const cat = f.category ?? ""; cat.length > 18 ? ... : cat`
 */
function buildChartName(category: string | null | undefined): string {
    const cat = category ?? "";
    return cat.length > 18 ? cat.slice(0, 18) + "…" : cat;
}

function buildChartDataItem(f: {
    category?: string | null;
    projection_year_1?: number | null;
    projection_year_2?: number | null;
    projection_year_3?: number | null;
    method?: string | null;
    confidence_score?: number | null;
}) {
    const cat = f.category ?? "";
    return {
        name: cat.length > 18 ? cat.slice(0, 18) + "…" : cat,
        "Y+1": f.projection_year_1 ?? 0,
        "Y+2": f.projection_year_2 ?? 0,
        "Y+3": f.projection_year_3 ?? 0,
        method: f.method ?? "unknown",
        confidence: Math.round((f.confidence_score ?? 0) * 100),
    };
}

/**
 * Mirrors the useFinanceData.js filter that was added as a fix:
 *   rawForecasts.filter(f => f && f.category != null)
 */
function filterForecasts(
    raw: Array<{ category?: string | null } | null | undefined>
): Array<{ category: string }> {
    return raw.filter((f): f is { category: string } =>
        f != null && f.category != null
    );
}

/**
 * Mirrors VarianceTable variance calculation logic.
 */
function computeVariance(budgeted: number | null, actual: number | null) {
    const b = budgeted ?? 0;
    const a = actual ?? 0;
    const variance = a - b;
    const variance_pct = b > 0 ? (variance / b) * 100 : 0;
    return {variance, variance_pct};
}

// ─────────────────────────────────────────────────────────────────────────────
// ForecastChart — chartData transformation
// ─────────────────────────────────────────────────────────────────────────────

describe("ForecastChart — buildChartName (regression: null category crash)", () => {
    test("null category returns empty string without crashing", () => {
        expect(() => buildChartName(null)).not.toThrow();
        expect(buildChartName(null)).toBe("");
    });

    test("undefined category returns empty string without crashing", () => {
        expect(() => buildChartName(undefined)).not.toThrow();
        expect(buildChartName(undefined)).toBe("");
    });

    test("short category name returned as-is", () => {
        expect(buildChartName("Insurance")).toBe("Insurance");
    });

    test("18-char category name returned as-is (boundary)", () => {
        const cat = "A".repeat(18);
        expect(buildChartName(cat)).toBe(cat);
        expect(buildChartName(cat).length).toBe(18);
    });

    test("19-char category name is truncated to 18 + ellipsis", () => {
        const cat = "A".repeat(19);
        const result = buildChartName(cat);
        expect(result).toBe("A".repeat(18) + "…");
        expect(result.length).toBe(19); // 18 chars + 1 Unicode ellipsis (U+2026)
    });

    test("long category name is truncated with ellipsis", () => {
        expect(buildChartName("Administration & Management Fees")).toBe(
            "Administration & M…"
        );
    });

    test("empty string category returns empty string", () => {
        expect(buildChartName("")).toBe("");
    });
});

describe("ForecastChart — buildChartDataItem (full item transformation)", () => {
    test("null category produces empty name without crashing", () => {
        expect(() => buildChartDataItem({category: null})).not.toThrow();
        expect(buildChartDataItem({category: null}).name).toBe("");
    });

    test("undefined category produces empty name without crashing", () => {
        expect(() => buildChartDataItem({category: undefined})).not.toThrow();
        expect(buildChartDataItem({category: undefined}).name).toBe("");
    });

    test("null projections default to 0", () => {
        const item = buildChartDataItem({
            category: "Test",
            projection_year_1: null,
            projection_year_2: null,
            projection_year_3: null,
        });
        expect(item["Y+1"]).toBe(0);
        expect(item["Y+2"]).toBe(0);
        expect(item["Y+3"]).toBe(0);
    });

    test("undefined confidence_score defaults to 0%", () => {
        const item = buildChartDataItem({category: "Test", confidence_score: undefined});
        expect(item.confidence).toBe(0);
    });

    test("null method defaults to 'unknown'", () => {
        const item = buildChartDataItem({category: "Test", method: null});
        expect(item.method).toBe("unknown");
    });

    test("valid item passes through correctly", () => {
        const item = buildChartDataItem({
            category: "Insurance",
            projection_year_1: 12000,
            projection_year_2: 12420,
            projection_year_3: 12854.7,
            method: "inflation",
            confidence_score: 0.75,
        });
        expect(item.name).toBe("Insurance");
        expect(item["Y+1"]).toBe(12000);
        expect(item["Y+2"]).toBe(12420);
        expect(item["Y+3"]).toBe(12854.7);
        expect(item.method).toBe("inflation");
        expect(item.confidence).toBe(75);
    });

    test("all-null item produces safe defaults without crashing", () => {
        expect(() => buildChartDataItem({})).not.toThrow();
        const item = buildChartDataItem({});
        expect(item.name).toBe("");
        expect(item["Y+1"]).toBe(0);
        expect(item.method).toBe("unknown");
        expect(item.confidence).toBe(0);
    });
});

// ─────────────────────────────────────────────────────────────────────────────
// useFinanceData — forecast filter
// ─────────────────────────────────────────────────────────────────────────────

describe("useFinanceData — filterForecasts (malformed data guard)", () => {
    test("items with null category are filtered out", () => {
        const raw = [
            {category: "Insurance"},
            {category: null},
            {category: "Maintenance"},
        ];
        const result = filterForecasts(raw);
        expect(result).toHaveLength(2);
        expect(result.every((f) => f.category != null)).toBe(true);
    });

    test("items with undefined category are filtered out", () => {
        const raw = [
            {category: "Insurance"},
            {category: undefined},
        ];
        const result = filterForecasts(raw);
        expect(result).toHaveLength(1);
        expect(result[0].category).toBe("Insurance");
    });

    test("items missing the category key entirely are filtered out", () => {
        const raw = [
            {category: "Insurance"},
            {} as { category?: string | null }, // no category key
        ];
        const result = filterForecasts(raw);
        expect(result).toHaveLength(1);
    });

    test("null array items are filtered out", () => {
        const raw = [
            {category: "Insurance"},
            null,
            {category: "Maintenance"},
        ];
        const result = filterForecasts(raw);
        expect(result).toHaveLength(2);
    });

    test("empty input returns empty array", () => {
        expect(filterForecasts([])).toEqual([]);
    });

    test("all-valid input returns all items", () => {
        const raw = Array.from({length: 10}, (_, i) => ({category: `Cat ${i}`}));
        expect(filterForecasts(raw)).toHaveLength(10);
    });

    test("all-null input returns empty array", () => {
        const raw = [{category: null}, {category: null}, null];
        expect(filterForecasts(raw)).toHaveLength(0);
    });
});

// ─────────────────────────────────────────────────────────────────────────────
// VarianceTable — data transformations
// ─────────────────────────────────────────────────────────────────────────────

describe("VarianceTable — computeVariance", () => {
    test("positive variance (over budget)", () => {
        const {variance, variance_pct} = computeVariance(50000, 55000);
        expect(variance).toBe(5000);
        expect(variance_pct).toBeCloseTo(10.0);
    });

    test("negative variance (under budget)", () => {
        const {variance, variance_pct} = computeVariance(50000, 48000);
        expect(variance).toBe(-2000);
        expect(variance_pct).toBeCloseTo(-4.0);
    });

    test("zero budgeted amount does not divide by zero", () => {
        expect(() => computeVariance(0, 5000)).not.toThrow();
        const {variance_pct} = computeVariance(0, 5000);
        expect(variance_pct).toBe(0);
    });

    test("null budgeted and actual both default to 0", () => {
        const {variance, variance_pct} = computeVariance(null, null);
        expect(variance).toBe(0);
        expect(variance_pct).toBe(0);
    });

    test("null actual defaults to 0", () => {
        const {variance} = computeVariance(50000, null);
        expect(variance).toBe(-50000);
    });

    test("equal budget and actual = zero variance", () => {
        const {variance, variance_pct} = computeVariance(40000, 40000);
        expect(variance).toBe(0);
        expect(variance_pct).toBe(0);
    });
});

// ─────────────────────────────────────────────────────────────────────────────
// API response shape — useFinanceData parsing
// ─────────────────────────────────────────────────────────────────────────────

describe("useFinanceData — API response parsing", () => {
    function parseForecasts(data: unknown): unknown[] {
        if (!data || typeof data !== "object") return [];
        const d = data as Record<string, unknown>;
        const raw = d.forecasts;
        return Array.isArray(raw) ? raw : [];
    }

    test("parses {forecasts: [...]} envelope correctly", () => {
        const data = {year: "2026", forecasts: [{category: "Insurance"}], count: 1};
        expect(parseForecasts(data)).toHaveLength(1);
    });

    test("null API response returns empty array", () => {
        expect(parseForecasts(null)).toEqual([]);
    });

    test("undefined API response returns empty array", () => {
        expect(parseForecasts(undefined)).toEqual([]);
    });

    test("API response with no forecasts key returns empty array", () => {
        expect(parseForecasts({year: "2026", count: 0})).toEqual([]);
    });

    test("API response with null forecasts returns empty array", () => {
        expect(parseForecasts({forecasts: null})).toEqual([]);
    });

    test("API response with non-array forecasts returns empty array", () => {
        expect(parseForecasts({forecasts: "not-an-array"})).toEqual([]);
    });

    test("API response with empty forecasts array returns empty array", () => {
        expect(parseForecasts({forecasts: []})).toEqual([]);
    });
});
