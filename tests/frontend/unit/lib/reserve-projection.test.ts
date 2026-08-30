// @featuretrace:finance-reserve-forecast — Guards the canonical projection normaliser.
// Layer: test
// Data flow: raw forecast payload -> normaliseReserveProjection (building-scoped).
// Related: frontend/src/lib/reserve-projection.ts
/**
 * The canonical reserve-projection normaliser.
 *
 * These tests pin the two rules the module exists to enforce, both of which were
 * previously broken in two copy-pasted call sites at once:
 *   1. `expenses` is the key the Mongo read path emits for capital works.
 *   2. unknown (null/undefined) never collapses into 0, and a real 0 never becomes unknown.
 */
import {
    normaliseReserveProjection,
    normaliseReserveProjectionRow,
    isReserveResilient,
} from "@/lib/reserve-projection";

describe("normaliseReserveProjectionRow", () => {
    it('reads capital works from the "expenses" key the API actually returns', () => {
        expect(normaliseReserveProjectionRow({year: 2029, expenses: 456317.33}).capital_works)
            .toBe(456317.33);
    });

    it("prefers expenses over a stale alias when both are present", () => {
        expect(normaliseReserveProjectionRow({expenses: 500, capital_works: 999}).capital_works)
            .toBe(500);
    });

    it.each([
        ["capital_works", {capital_works: 1200}],
        ["capital_spend", {capital_spend: 1200}],
        ["projected_expenses", {projected_expenses: 1200}],
    ])("still honours the legacy %s alias", (_name, row) => {
        expect(normaliseReserveProjectionRow(row).capital_works).toBe(1200);
    });

    it("keeps a real $0 of capital works as 0", () => {
        // A year the capital schedule lists no asset for is a known zero.
        expect(normaliseReserveProjectionRow({expenses: 0}).capital_works).toBe(0);
    });

    it("reports unknown capital works as undefined, not 0", () => {
        expect(normaliseReserveProjectionRow({year: 2030}).capital_works).toBeUndefined();
        expect(normaliseReserveProjectionRow({expenses: null}).capital_works).toBeUndefined();
    });

    it("keeps a real $0 contribution and reports an absent one as undefined", () => {
        expect(normaliseReserveProjectionRow({contributions: 0}).contributions).toBe(0);
        expect(normaliseReserveProjectionRow({}).contributions).toBeUndefined();
    });

    it("resolves the Postgres path's `balance` into closing_balance", () => {
        // analytics_pg_service returns {year, balance, contributions, is_actual}.
        expect(normaliseReserveProjectionRow({year: 2027, balance: 211593.06}).closing_balance)
            .toBe(211593.06);
    });

    it("converts a cents-suffixed balance exactly once", () => {
        expect(normaliseReserveProjectionRow({projected_balance_cents: 21159306}).closing_balance)
            .toBe(211593.06);
    });

    it("preserves unrecognised fields so callers keep their extras", () => {
        const row = normaliseReserveProjectionRow({year: 2029, shock_label: "Facade repaint", events: [{item: "X", cost: 1}]});
        expect(row.shock_label).toBe("Facade repaint");
        expect(row.events).toEqual([{item: "X", cost: 1}]);
    });
});

describe("normaliseReserveProjection", () => {
    it("returns [] for a missing or non-array payload rather than throwing", () => {
        expect(normaliseReserveProjection(undefined)).toEqual([]);
        expect(normaliseReserveProjection(null)).toEqual([]);
        expect(normaliseReserveProjection({projection: []})).toEqual([]);
    });
});

describe("isReserveResilient", () => {
    it("is true when every known balance stays positive", () => {
        expect(isReserveResilient([{closing_balance: 10}, {closing_balance: 20}])).toBe(true);
    });

    it("is false when any known balance goes negative", () => {
        expect(isReserveResilient([{closing_balance: 10}, {closing_balance: -5}])).toBe(false);
    });

    it("is null — not true — when the projection is empty", () => {
        // `.every()` on [] is true, which is how an absent forecast came to claim
        // "reserves stay positive through 10-year forecast".
        expect(isReserveResilient([])).toBeNull();
        expect(isReserveResilient(undefined)).toBeNull();
    });

    it("is null when no row carries a known balance", () => {
        expect(isReserveResilient([{year: 2027}, {year: 2028, closing_balance: null}])).toBeNull();
    });

    it("ignores unknown rows instead of reading them as a failing zero", () => {
        // The old `?? 0` made an unknown balance fail the `> 0` test like a negative one.
        expect(isReserveResilient([{closing_balance: 10}, {year: 2028}])).toBe(true);
    });

    it("treats a balance of exactly zero as not resilient", () => {
        expect(isReserveResilient([{closing_balance: 0}])).toBe(false);
    });
});
