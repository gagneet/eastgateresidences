// @featuretrace:demo_bank — Tests for getAllowedBatchActions' reject-action addition.
// Layer: test
// Data flow: Jest -> getAllowedBatchActions (pure fn) -> asserted action list. (building-scoped)
// Related: HistoricalReconstructionPage.tsx.
// (HistoricalReconstructionPage.tsx). Full-page rendering isn't exercised here — this function
// is pure (status/role/toggle -> allowed actions) and is unit-tested directly, matching this
// repo's existing "test the pure decision logic in isolation" convention for finance pages
// (see tests/frontend/unit/finance/ForecastChart.test.tsx).
// Tests: tests/frontend/unit/pages/financial/HistoricalReconstructionPage.getAllowedBatchActions.test.tsx
import {getAllowedBatchActions} from "@/pages/dashboard/financial/HistoricalReconstructionPage";

describe("getAllowedBatchActions — needs_review (reject action)", () => {
    it("offers both Review and Reject before any review has been recorded", () => {
        const actions = getAllowedBatchActions("needs_review", "strata_admin", true, true, {reviewed_by: null}, "user-1");
        const keys = actions.map((a) => a.key);
        expect(keys).toEqual(["review", "reject"]);
    });

    it("offers both Approve and Reject once a review has been recorded", () => {
        const actions = getAllowedBatchActions("needs_review", "strata_admin", true, true, {reviewed_by: "user-2"}, "user-1");
        const keys = actions.map((a) => a.key);
        expect(keys).toEqual(["approve", "reject"]);
    });

    it("reject is enabled even when the current user recorded the review (no dual-control precondition, unlike approve)", () => {
        const actions = getAllowedBatchActions("needs_review", "strata_admin", true, true, {reviewed_by: "user-1"}, "user-1");
        const approve = actions.find((a) => a.key === "approve")!;
        const reject = actions.find((a) => a.key === "reject")!;
        expect(approve.enabled).toBe(false); // dual control: reviewer cannot also approve
        expect(reject.enabled).toBe(true); // reject has no such precondition
    });

    it("reject is disabled for a role outside APPROVE_ROLES", () => {
        const actions = getAllowedBatchActions("needs_review", "strata_manager", true, true, {reviewed_by: null}, "user-1");
        const reject = actions.find((a) => a.key === "reject")!;
        expect(reject.enabled).toBe(false);
        expect(reject.disabledReason).toMatch(/Requires one of/);
    });

    it("reject is disabled when posting is toggled off for the building", () => {
        const actions = getAllowedBatchActions("needs_review", "strata_admin", true, false, {reviewed_by: null}, "user-1");
        const reject = actions.find((a) => a.key === "reject")!;
        expect(reject.enabled).toBe(false);
        expect(reject.disabledReason).toMatch(/Posting is disabled/);
    });

    it("reject is enabled for super_admin regardless of who reviewed", () => {
        const actions = getAllowedBatchActions("needs_review", "super_admin", true, true, {reviewed_by: "user-1"}, "user-1");
        const reject = actions.find((a) => a.key === "reject")!;
        expect(reject.enabled).toBe(true);
    });

    it("no reject action offered for statuses other than needs_review", () => {
        for (const status of ["draft", "extracted", "approved", "generated", "synced", "failed"]) {
            const actions = getAllowedBatchActions(status, "super_admin", true, true, {}, "user-1");
            expect(actions.map((a) => a.key)).not.toContain("reject");
        }
    });
});
