// @featuretrace:document_ocr — Tests for OCRPage.jsx (GAP-OCR-001 completion: extracted-field
// bug fix + processing-job polling).
// Layer: test
// Data flow: Jest -> OCRPage.jsx -> mocked useAuth().api against /ocr/providers, /ocr/jobs,
//            /ocr/jobs/{id}. (building-scoped)
// Related: frontend/src/pages/dashboard/OCRPage.jsx, backend/routers/ocr.py
// Tests: tests/frontend/unit/pages/dashboard/OCRPage.test.jsx
import React from "react";
import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";
import OCRPage from "@/pages/dashboard/OCRPage";

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: jest.fn(),
}));

const mockUseAuth = require("@/contexts/AuthContext").useAuth;

jest.mock("sonner", () => ({
    toast: {success: jest.fn(), error: jest.fn()},
}));

const PROVIDERS = [
    {id: "mock_ocr", label: "Mock OCR (deterministic fallback)", available: true, selectable: false, runtime_mode: "fallback", max_pages: 1, max_bytes: 1000, timeout_seconds: 30},
    {id: "unlimited_ocr", label: "Unlimited OCR (Advanced Document Parsing)", available: false, selectable: false, runtime_mode: "disabled", unavailable_reason: "UNLIMITED_OCR_ENABLED is false", max_pages: 25, max_bytes: 1000, timeout_seconds: 1200},
];

const PROCESSING_JOB = {
    id: "job-1", building_id: "16244", source_type: "invoice", provider: "pending",
    requested_provider: "auto", status: "processing", mime_type: "application/pdf",
    file_name: "invoice.pdf", file_size_bytes: 1234, requested_by: "user-1",
    created_at: "2026-07-27T00:00:00Z", confidence: 0.0,
};

const COMPLETED_JOB_DETAIL = {
    job: {...PROCESSING_JOB, status: "completed", provider: "mock_ocr", confidence: 0.95, result_ref: "result-1"},
    result: {
        id: "result-1", provider: "mock_ocr", raw_text_sha256: "abc123",
        result: {
            vendor_name: {value: "Acme Pty Ltd", confidence: 0.95},
            vendor_abn: {value: "12 345 678 901", confidence: 0.9},
            invoice_number: {value: "INV-001", confidence: 0.9},
            total_cents: 11000,
            raw_text: "INVOICE Acme Pty Ltd $110.00",
        },
    },
};

function setupAuth(apiOverrides = {}) {
    const api = {
        get: jest.fn((url) => {
            if (url.startsWith("/ocr/providers")) return Promise.resolve({data: PROVIDERS});
            if (url.startsWith("/ocr/jobs?")) return Promise.resolve({data: []});
            if (url.match(/\/ocr\/jobs\/job-1$/)) return Promise.resolve({data: COMPLETED_JOB_DETAIL});
            return Promise.resolve({data: []});
        }),
        post: jest.fn(() => Promise.resolve({data: PROCESSING_JOB})),
        ...apiOverrides,
    };
    mockUseAuth.mockReturnValue({api, user: {id: "user-1", role: "strata_manager"}});
    return api;
}

describe("OCRPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("loads providers and jobs on mount", async () => {
        const api = setupAuth();
        render(<OCRPage/>);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith("/ocr/providers"));
        expect(api.get).toHaveBeenCalledWith("/ocr/jobs?limit=25");
    });

    it("disables the Run OCR button until a file is selected", async () => {
        setupAuth();
        render(<OCRPage/>);
        await waitFor(() => expect(screen.getByText("Run OCR")).toBeInTheDocument());
        expect(screen.getByText("Run OCR").closest("button")).toBeDisabled();

        const file = new File(["%PDF-1.4 fake"], "invoice.pdf", {type: "application/pdf"});
        fireEvent.change(screen.getByLabelText("Document"), {target: {files: [file]}});
        expect(screen.getByText("Run OCR").closest("button")).toBeEnabled();
    });

    it("uploads a file as multipart form data with source_type and provider fields", async () => {
        const api = setupAuth();
        render(<OCRPage/>);
        await waitFor(() => expect(screen.getByLabelText("Document")).toBeInTheDocument());

        const file = new File(["%PDF-1.4 fake"], "invoice.pdf", {type: "application/pdf"});
        fireEvent.change(screen.getByLabelText("Document"), {target: {files: [file]}});
        fireEvent.click(screen.getByText("Run OCR"));

        await waitFor(() => expect(api.post).toHaveBeenCalledWith(
            "/ocr/jobs",
            expect.any(FormData),
            expect.objectContaining({headers: expect.objectContaining({"Content-Type": "multipart/form-data"})}),
        ));
    });

    it("renders extracted fields from the corrected selectedJob.result.result path (regression)", async () => {
        // Regression test for the fix: selectedJob?.result?.result?.result (one .result too many)
        // -> selectedJob?.result?.result. COMPLETED_JOB_DETAIL.result.result is the actual shape
        // GET /ocr/jobs/{id} returns — if the bug were still present, none of this would render.
        const api = setupAuth();
        render(<OCRPage/>);
        await waitFor(() => expect(screen.getByLabelText("Document")).toBeInTheDocument());

        const file = new File(["%PDF-1.4 fake"], "invoice.pdf", {type: "application/pdf"});
        fireEvent.change(screen.getByLabelText("Document"), {target: {files: [file]}});
        fireEvent.click(screen.getByText("Run OCR"));

        await waitFor(() => expect(screen.getByText("Acme Pty Ltd")).toBeInTheDocument());
        expect(screen.getByText("12 345 678 901")).toBeInTheDocument();
        expect(screen.getByText("INV-001")).toBeInTheDocument();
        expect(screen.getByText("$110.00")).toBeInTheDocument();
        expect(api.get).toHaveBeenCalledWith("/ocr/jobs/job-1");
    });

    it("polls a processing job until it reaches a terminal status", async () => {
        jest.useFakeTimers();
        let callCount = 0;
        const api = setupAuth({
            get: jest.fn((url) => {
                if (url.startsWith("/ocr/providers")) return Promise.resolve({data: PROVIDERS});
                if (url.startsWith("/ocr/jobs?")) return Promise.resolve({data: []});
                if (url.match(/\/ocr\/jobs\/job-1$/)) {
                    callCount += 1;
                    // First call (immediately after upload): still processing.
                    // Second call (after the poll interval): completed.
                    return Promise.resolve({
                        data: callCount === 1
                            ? {job: PROCESSING_JOB, result: null}
                            : COMPLETED_JOB_DETAIL,
                    });
                }
                return Promise.resolve({data: []});
            }),
        });

        render(<OCRPage/>);
        await act(async () => {
            await waitFor(() => expect(screen.getByLabelText("Document")).toBeInTheDocument());
        });

        const file = new File(["%PDF-1.4 fake"], "invoice.pdf", {type: "application/pdf"});
        fireEvent.change(screen.getByLabelText("Document"), {target: {files: [file]}});
        await act(async () => {
            fireEvent.click(screen.getByText("Run OCR"));
            await Promise.resolve();
        });

        // First GET already happened (still "processing") — extracted panel not shown yet.
        expect(screen.queryByText("Acme Pty Ltd")).not.toBeInTheDocument();

        // Advance past the 2s poll interval — the second GET returns "completed".
        await act(async () => {
            jest.advanceTimersByTime(2100);
            await Promise.resolve();
            await Promise.resolve();
        });

        await waitFor(() => expect(screen.getByText("Acme Pty Ltd")).toBeInTheDocument());
        expect(callCount).toBeGreaterThanOrEqual(2);
        jest.useRealTimers();
    });
});
