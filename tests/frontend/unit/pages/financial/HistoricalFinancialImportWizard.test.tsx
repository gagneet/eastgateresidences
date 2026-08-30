// @featuretrace:financial-onboarding — Tests for HistoricalFinancialImportWizard (gap-tolerant
// CSV/JSON upload -> coverage preview -> generate -> submit-review wizard).
// Layer: test
// Data flow: Jest -> HistoricalFinancialImportWizard -> mocked useAuth().api against
//            /financials/onboarding/building/{id}/* (building-scoped). GAP-FIN-024.
// Related: frontend/src/pages/dashboard/financial/HistoricalFinancialImportWizard.tsx
//          backend/routers/financial_onboarding.py
// Tests: tests/frontend/unit/pages/financial/HistoricalFinancialImportWizard.test.tsx
import React from "react";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";
import HistoricalFinancialImportWizard from "@/pages/dashboard/financial/HistoricalFinancialImportWizard";

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: jest.fn(),
}));

const mockUseAuth = require("@/contexts/AuthContext").useAuth;

jest.mock("sonner", () => ({
    toast: {
        success: jest.fn(),
        error: jest.fn(),
    },
}));

const BUILDING_ID = "16244";
const IMPORT_RESULT = {
    years_touched: [2024],
    fund_rows_written: 1,
    category_rows_written: 2,
    row_warnings: [],
    coverage: [
        {
            year: 2024,
            proposed_admin_present: true,
            proposed_sinking_present: false,
            closing_balance_admin_present: true,
            closing_balance_sinking_present: false,
            category_count: 2,
        },
    ],
};

const PREVIEW_RESULT = {
    expected_transaction_count: 5,
    expected_credit_cents: 400000,
    expected_debit_cents: 84500,
    fund_balance_reconciliation: [
        {
            account_ref: "ADMIN-16244", opening_balance_cents: 0, reconstructed_credits_cents: 400000,
            reconstructed_debits_cents: 84500, closing_balance_cents: 315500, matches_expected_closing: true,
        },
    ],
    warnings: [],
};

function makeApiMock(overrides: Record<string, jest.Mock> = {}) {
    const post = jest.fn((url: string, body: any) => {
        if (url.includes("/import-financial-data")) return Promise.resolve({data: IMPORT_RESULT});
        if (url.endsWith("/reconstruction-batches")) return Promise.resolve({data: {batch_id: "batch-1"}});
        if (url.endsWith("/extract")) return Promise.resolve({data: {status: "extracted"}});
        if (url.endsWith("/generate-preview")) return Promise.resolve({data: PREVIEW_RESULT});
        if (url.endsWith("/submit-review")) return Promise.resolve({data: {status: "needs_review"}});
        return Promise.resolve({data: {}});
    });
    return {post: overrides.post || post, get: jest.fn(() => Promise.resolve({data: {}}))};
}

function setupAuth(apiOverrides: Record<string, jest.Mock> = {}) {
    const api = makeApiMock(apiOverrides);
    mockUseAuth.mockReturnValue({api, user: {id: "user-1", role: "strata_manager"}});
    return api;
}

const testFile = (name: string, content = "year,fund_type,proposed_amount\n2024,admin,340870.02\n") =>
    new File([content], name, {type: "text/csv"});

async function advanceToStep2() {
    fireEvent.click(screen.getByRole("button", {name: /continue/i}));
    await waitFor(() => expect(screen.getByTestId("import-wizard-dropzone")).toBeInTheDocument());
}

async function uploadFile(api: ReturnType<typeof makeApiMock>) {
    const input = screen.getByTestId("import-wizard-file-input") as HTMLInputElement;
    fireEvent.change(input, {target: {files: [testFile("budget.csv")]}});
    await waitFor(() => expect(screen.getByText("budget.csv")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", {name: /^upload$/i}));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        `/financials/onboarding/building/${BUILDING_ID}/import-financial-data`,
        expect.any(FormData),
        expect.objectContaining({headers: expect.objectContaining({"Content-Type": "multipart/form-data"})}),
    ));
}

describe("HistoricalFinancialImportWizard", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("starts on step 1 (financial year range)", () => {
        setupAuth();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        expect(screen.getByLabelText(/from year/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/to year/i)).toBeInTheDocument();
    });

    it("blocks continue when from-year is after to-year", () => {
        setupAuth();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        fireEvent.change(screen.getByLabelText(/from year/i), {target: {value: "2026"}});
        fireEvent.change(screen.getByLabelText(/to year/i), {target: {value: "2024"}});
        expect(screen.getByRole("button", {name: /continue/i})).toBeDisabled();
    });

    it("advances to the upload step on continue", async () => {
        setupAuth();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        await advanceToStep2();
        expect(screen.getByTestId("import-wizard-dropzone")).toBeInTheDocument();
    });

    it("uploads a file and shows the coverage grid on success", async () => {
        const api = setupAuth();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        await advanceToStep2();
        await uploadFile(api);

        expect(screen.getByTestId("import-wizard-coverage-grid")).toBeInTheDocument();
        expect(screen.getByText("2024")).toBeInTheDocument();
    });

    it("shows the import error and stays on step 2 when upload fails", async () => {
        const post = jest.fn((url: string) => {
            if (url.includes("/import-financial-data")) {
                return Promise.reject({response: {data: {detail: "Missing required column(s): fund_type"}}});
            }
            return Promise.resolve({data: {}});
        });
        const api = setupAuth({post});
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        await advanceToStep2();

        const input = screen.getByTestId("import-wizard-file-input") as HTMLInputElement;
        fireEvent.change(input, {target: {files: [testFile("bad.csv")]}});
        fireEvent.click(screen.getByRole("button", {name: /^upload$/i}));

        await waitFor(() => expect(screen.getByText(/Missing required column/)).toBeInTheDocument());
        expect(screen.queryByTestId("import-wizard-coverage-grid")).not.toBeInTheDocument();
    });

    it("generates a preview showing both income and expense totals", async () => {
        const api = setupAuth();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        await advanceToStep2();
        await uploadFile(api);

        fireEvent.click(screen.getByRole("button", {name: /generate preview/i}));

        await waitFor(() => expect(screen.getByText(/Generated preview/i)).toBeInTheDocument());
        // Appears twice — once in the summary grid, once in the fund-balance-reconciliation table.
        expect(screen.getAllByText("$4,000.00").length).toBeGreaterThan(0); // income
        expect(screen.getAllByText("$845.00").length).toBeGreaterThan(0); // expense
        // create -> extract -> preview, in that order
        expect(api.post).toHaveBeenNthCalledWith(
            2, `/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches`,
            expect.objectContaining({financial_year_start: expect.any(Number), reconstruction_method: "generate_from_budget_v1"}),
        );
        expect(api.post).toHaveBeenNthCalledWith(3, `/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches/batch-1/extract`);
        expect(api.post).toHaveBeenNthCalledWith(4, `/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches/batch-1/generate-preview`);
    });

    it("submits for review and shows the confirmation, calling onBatchSubmitted", async () => {
        const api = setupAuth();
        const onBatchSubmitted = jest.fn();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID} onBatchSubmitted={onBatchSubmitted}/>);
        await advanceToStep2();
        await uploadFile(api);
        fireEvent.click(screen.getByRole("button", {name: /generate preview/i}));
        await waitFor(() => expect(screen.getByText(/Generated preview/i)).toBeInTheDocument());

        fireEvent.click(screen.getByRole("button", {name: /submit for review/i}));

        await waitFor(() => expect(screen.getByText(/Submitted for review/i)).toBeInTheDocument());
        expect(screen.getByText("batch-1")).toBeInTheDocument();
        expect(onBatchSubmitted).toHaveBeenCalled();
        expect(api.post).toHaveBeenCalledWith(`/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches/batch-1/submit-review`);
    });

    it("rejects a dropped file with an unsupported extension", async () => {
        setupAuth();
        render(<HistoricalFinancialImportWizard buildingId={BUILDING_ID}/>);
        await advanceToStep2();

        const dropzone = screen.getByTestId("import-wizard-dropzone");
        const dataTransfer = {files: [new File(["x"], "budget.pdf", {type: "application/pdf"})]};
        fireEvent.drop(dropzone, {dataTransfer});

        const {toast} = require("sonner");
        expect(toast.error).toHaveBeenCalledWith("Please drop a CSV or JSON file.");
    });
});
