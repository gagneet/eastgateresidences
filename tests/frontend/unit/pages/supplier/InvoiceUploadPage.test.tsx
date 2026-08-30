/**
 * tests/frontend/InvoiceUploadPage.test.tsx
 *
 * Unit tests for the supplier-facing invoice upload UI.
 * All data is in-memory; no DB writes occur.
 */
import React from "react";
import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";
import InvoiceUploadPage from "@/pages/supplier/InvoiceUploadPage";

// ── Mock API + Auth ─────────────────────────────────────────────────────────

const mockApi = {
    post: jest.fn(),
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: () => ({
        api: mockApi,
        user: {role: "service_provider", building_id: "16244"},
    }),
    AuthProvider: ({children}: any) => <div>{children}</div>,
}));

// ── Fixtures ───────────────────────────────────────────────────────────────

const makeUploadResult = (overrides: Partial<Record<string, any>> = {}) => ({
    invoice_id: "inv-sp-001",
    state: "draft",
    vendor_name: "Acme Cleaning Pty Ltd",
    vendor_abn: "51824753556",
    invoice_number: "INV-2026-001",
    issue_date: "2026-04-01",
    due_date: "2026-04-30",
    total_cents: 110000,
    gst_cents: 10000,
    is_tax_invoice: true,
    abn_valid: true,
    abn_entity_name: "Acme Cleaning Pty Ltd",
    withholding_required: false,
    overall_ocr_confidence: 0.92,
    low_confidence_fields: [],
    ...overrides,
});

const makePdfFile = (name = "invoice.pdf") =>
    new File(["%PDF-1.4 fake content"], name, {type: "application/pdf"});

// ── Setup ─────────────────────────────────────────────────────────────────

beforeEach(() => {
    jest.clearAllMocks();
    mockApi.post.mockResolvedValue({data: makeUploadResult()});
});

// ── Render ─────────────────────────────────────────────────────────────────

describe("InvoiceUploadPage — render", () => {
    it("renders the upload page", () => {
        render(<InvoiceUploadPage/>);
        expect(screen.getByTestId("invoice-upload-page")).toBeInTheDocument();
        expect(screen.getByTestId("drop-zone")).toBeInTheDocument();
        expect(screen.getByTestId("submit-btn")).toBeInTheDocument();
    });

    it("submit button is disabled when no file selected", () => {
        render(<InvoiceUploadPage/>);
        expect(screen.getByTestId("submit-btn")).toBeDisabled();
    });
});

// ── File selection ─────────────────────────────────────────────────────────

describe("InvoiceUploadPage — file selection", () => {
    it("shows selected filename after file input change", async () => {
        render(<InvoiceUploadPage/>);
        const input = screen.getByTestId("file-input");
        const file = makePdfFile("my-invoice.pdf");

        await act(async () => {
            fireEvent.change(input, {target: {files: [file]}});
        });

        expect(screen.getByTestId("selected-filename")).toHaveTextContent("my-invoice.pdf");
    });

    it("enables submit button after file selected", async () => {
        render(<InvoiceUploadPage/>);
        const input = screen.getByTestId("file-input");

        await act(async () => {
            fireEvent.change(input, {target: {files: [makePdfFile()]}});
        });

        expect(screen.getByTestId("submit-btn")).not.toBeDisabled();
    });

    it("handles drag and drop", async () => {
        render(<InvoiceUploadPage/>);
        const dropZone = screen.getByTestId("drop-zone");
        const file = makePdfFile("dropped.pdf");

        await act(async () => {
            fireEvent.dragOver(dropZone, {preventDefault: jest.fn()});
            fireEvent.drop(dropZone, {
                dataTransfer: {files: [file]},
            });
        });

        expect(screen.getByTestId("selected-filename")).toHaveTextContent("dropped.pdf");
    });
});

// ── Upload submission ──────────────────────────────────────────────────────

describe("InvoiceUploadPage — upload submission", () => {
    it("calls POST /ap/supplier-upload/ on submit", async () => {
        render(<InvoiceUploadPage/>);
        const input = screen.getByTestId("file-input");

        await act(async () => {
            fireEvent.change(input, {target: {files: [makePdfFile()]}});
        });

        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                "/ap/supplier-upload/",
                expect.any(FormData),
                expect.objectContaining({headers: {"Content-Type": "multipart/form-data"}})
            );
        });
    });

    it("shows result card on successful upload", async () => {
        render(<InvoiceUploadPage/>);
        const input = screen.getByTestId("file-input");

        await act(async () => {
            fireEvent.change(input, {target: {files: [makePdfFile()]}});
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.getByTestId("upload-result")).toBeInTheDocument();
        });
    });

    it("displays extracted vendor name in result", async () => {
        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.getByTestId("field-vendor")).toHaveTextContent("Acme Cleaning Pty Ltd");
        });
    });

    it("displays OCR confidence percentage", async () => {
        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.getByTestId("ocr-confidence-row")).toHaveTextContent("92%");
        });
    });

    it("shows ABN verified status", async () => {
        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            const abnStatus = screen.getByTestId("abn-status");
            expect(abnStatus).toHaveTextContent("ABN verified");
        });
    });
});

// ── Withholding warning ────────────────────────────────────────────────────

describe("InvoiceUploadPage — withholding", () => {
    it("shows withholding warning when required", async () => {
        mockApi.post.mockResolvedValueOnce({
            data: makeUploadResult({withholding_required: true, abn_valid: false}),
        });

        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.getByTestId("withholding-warning")).toBeInTheDocument();
            expect(screen.getByTestId("withholding-warning")).toHaveTextContent("47%");
        });
    });

    it("does not show withholding warning when not required", async () => {
        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.queryByTestId("withholding-warning")).not.toBeInTheDocument();
        });
    });
});

// ── Low confidence fields ──────────────────────────────────────────────────

describe("InvoiceUploadPage — low confidence", () => {
    it("shows low confidence field list when present", async () => {
        mockApi.post.mockResolvedValueOnce({
            data: makeUploadResult({low_confidence_fields: ["vendor_abn", "invoice_number"]}),
        });

        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            const lcField = screen.getByTestId("low-confidence-fields");
            expect(lcField).toHaveTextContent("vendor_abn");
            expect(lcField).toHaveTextContent("invoice_number");
        });
    });

    it("does not show low confidence section when all fields high confidence", async () => {
        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.queryByTestId("low-confidence-fields")).not.toBeInTheDocument();
        });
    });
});

// ── Error handling ─────────────────────────────────────────────────────────

describe("InvoiceUploadPage — error handling", () => {
    it("shows generic error on upload failure", async () => {
        mockApi.post.mockRejectedValueOnce({
            response: {data: {detail: "Upload failed. Please try again."}},
        });

        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            expect(screen.getByTestId("upload-error")).toHaveTextContent("Upload failed");
        });
    });

    it("shows duplicate invoice error with invoice ID", async () => {
        mockApi.post.mockRejectedValueOnce({
            response: {
                data: {
                    detail: {
                        message: "Duplicate invoice detected.",
                        existing_invoice_id: "inv-existing-001",
                    },
                },
            },
        });

        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => {
            const error = screen.getByTestId("upload-error");
            expect(error).toHaveTextContent("Duplicate invoice detected");
            expect(error).toHaveTextContent("inv-existing-001");
        });
    });
});

// ── Reset / Upload another ─────────────────────────────────────────────────

describe("InvoiceUploadPage — reset flow", () => {
    it("upload-another-btn resets to upload form", async () => {
        render(<InvoiceUploadPage/>);

        await act(async () => {
            fireEvent.change(screen.getByTestId("file-input"), {
                target: {files: [makePdfFile()]},
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("submit-btn"));
        });

        await waitFor(() => screen.getByTestId("upload-another-btn"));
        fireEvent.click(screen.getByTestId("upload-another-btn"));

        await waitFor(() => {
            expect(screen.getByTestId("drop-zone")).toBeInTheDocument();
            expect(screen.queryByTestId("upload-result")).not.toBeInTheDocument();
        });
    });
});
