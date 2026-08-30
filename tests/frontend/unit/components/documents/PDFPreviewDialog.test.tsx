/**
 * PDFPreviewDialog test suite
 *
 * react-pdf and pdfjs-dist are resolved via moduleNameMapper to manual mocks
 * (tests/frontend/unit/__mocks__/react-pdf.tsx) — no real PDF rendering in tests.
 *
 * Coverage:
 *  1. Loading state while fetching document
 *  2. Error state when API fails / missing file_data
 *  3. Renders PDF viewer on successful fetch
 *  4. Zoom in / zoom out controls and scale display
 *  5. Zoom disabled at min (50%) and max (300%)
 *  6. Page navigation: prev disabled on page 1, next disabled on last page
 *  7. Page indicator updates correctly
 *  8. Download button triggers data-URI download
 *  9. Download disabled before file loads
 * 10. Dialog closed — no API call made
 * 11. State resets when dialog closes and reopens
 */

import React, {act} from "react";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";

// ── API mock ──────────────────────────────────────────────────────────────────
const mockApi = {get: jest.fn()};

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: () => ({api: mockApi}),
}));

// ── sonner toast mock ─────────────────────────────────────────────────────────
jest.mock("sonner", () => ({toast: {success: jest.fn(), error: jest.fn()}}));

// react-pdf and pdfjs-dist are automatically mocked via moduleNameMapper.
// The manual mock (react-pdf.tsx) calls onLoadSuccess({ numPages: 3 }) on mount.

import PDFPreviewDialog from "@/components/documents/PDFPreviewDialog";

// ── Helpers ───────────────────────────────────────────────────────────────────
const PDF_FILE_DATA = "AAABBBCCC";
const defaultProps = {
    documentId: "doc-123",
    fileName: "annual-report.pdf",
    fileType: "application/pdf",
    isOpen: true,
    onClose: jest.fn(),
};

function renderDialog(props: Partial<typeof defaultProps> = {}) {
    return render(<PDFPreviewDialog {...defaultProps} {...props} />);
}

function mockSuccess() {
    mockApi.get.mockResolvedValue({
        data: {file_data: PDF_FILE_DATA, file_type: "application/pdf"},
    });
}

// ─────────────────────────────────────────────────────────────────────────────

describe("PDFPreviewDialog", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });


    // ── 1. Loading ──────────────────────────────────────────────────────────────
    describe("loading state", () => {
        it("shows spinner while fetch is in-flight", async () => {
            mockApi.get.mockReturnValue(new Promise(() => {
            })); // never resolves
            renderDialog();
            expect(screen.getByTestId("pdf-loading-spinner")).toBeInTheDocument();
        });
    });

    // ── 2. Error states ─────────────────────────────────────────────────────────
    describe("error state", () => {
        it("shows error when API rejects", async () => {
            mockApi.get.mockRejectedValue(new Error("Network error"));
            renderDialog();
            await waitFor(() =>
                expect(screen.getByTestId("pdf-error")).toBeInTheDocument()
            );
            expect(screen.getByText(/could not load pdf/i)).toBeInTheDocument();
        });

        it("shows error when file_data is null in response", async () => {
            mockApi.get.mockResolvedValue({data: {file_data: null}});
            renderDialog();
            await waitFor(() =>
                expect(screen.getByTestId("pdf-error")).toBeInTheDocument()
            );
        });

        it("retry button re-calls the API", async () => {
            mockApi.get
                .mockRejectedValueOnce(new Error("fail"))
                .mockResolvedValue({data: {file_data: PDF_FILE_DATA}});
            renderDialog();
            await waitFor(() => screen.getByTestId("pdf-error"));
            fireEvent.click(screen.getByRole("button", {name: /retry/i}));
            await waitFor(() =>
                expect(screen.queryByTestId("pdf-error")).not.toBeInTheDocument()
            );
        });
    });

    // ── 3. Successful render ────────────────────────────────────────────────────
    describe("successful render", () => {
        beforeEach(mockSuccess);

        it("renders pdf-document after loading", async () => {
            renderDialog();
            await waitFor(() =>
                expect(screen.getByTestId("pdf-document")).toBeInTheDocument()
            );
        });

        it("displays the file name in the header", () => {
            renderDialog();
            expect(screen.getByText("annual-report.pdf")).toBeInTheDocument();
        });

        it("shows 100% zoom initially", async () => {
            renderDialog();
            await waitFor(() => screen.getByTestId("pdf-document"));
            expect(screen.getByTestId("zoom-level")).toHaveTextContent("100%");
        });

        it("shows page indicator once numPages resolves", async () => {
            renderDialog();
            // Mock Document calls onLoadSuccess({ numPages: 3 }) in useEffect
            await waitFor(() =>
                expect(screen.getByTestId("page-indicator")).toHaveTextContent("1 / 3")
            );
        });
    });

    // ── 4 & 5. Zoom controls ───────────────────────────────────────────────────
    describe("zoom controls", () => {
        beforeEach(mockSuccess);

        it("increases scale by 25% on zoom in", async () => {
            renderDialog();
            await waitFor(() => screen.getByTestId("pdf-document"));
            fireEvent.click(screen.getByTestId("zoom-in-btn"));
            expect(screen.getByTestId("zoom-level")).toHaveTextContent("125%");
        });

        it("decreases scale by 25% on zoom out", async () => {
            renderDialog();
            await waitFor(() => screen.getByTestId("pdf-document"));
            fireEvent.click(screen.getByTestId("zoom-out-btn"));
            expect(screen.getByTestId("zoom-level")).toHaveTextContent("75%");
        });

        it("disables zoom-out at minimum 50%", async () => {
            renderDialog();
            await waitFor(() => screen.getByTestId("pdf-document"));
            const btn = screen.getByTestId("zoom-out-btn");
            // 100 → 75 → 50
            fireEvent.click(btn);
            fireEvent.click(btn);
            fireEvent.click(btn); // would go below min — clamped at 50
            expect(btn).toBeDisabled();
            expect(screen.getByTestId("zoom-level")).toHaveTextContent("50%");
        });

        it("disables zoom-in at maximum 300%", async () => {
            renderDialog();
            await waitFor(() => screen.getByTestId("pdf-document"));
            const btn = screen.getByTestId("zoom-in-btn");
            // 100 → 125 → 150 → 175 → 200 → 225 → 250 → 275 → 300
            for (let i = 0; i < 9; i++) fireEvent.click(btn);
            expect(btn).toBeDisabled();
            expect(screen.getByTestId("zoom-level")).toHaveTextContent("300%");
        });
    });

    // ── 6 & 7. Page navigation ─────────────────────────────────────────────────
    describe("page navigation", () => {
        beforeEach(mockSuccess);

        it("disables prev button on page 1", async () => {
            renderDialog();
            await waitFor(() =>
                expect(screen.getByTestId("page-indicator")).toHaveTextContent("1 / 3")
            );
            expect(screen.getByTestId("page-prev-btn")).toBeDisabled();
        });

        it("navigates forward and back", async () => {
            renderDialog();
            // The mocked Document reports numPages from a React effect, so wait
            // for the loaded page count before asserting navigation state.
            await waitFor(() =>
                expect(screen.getByTestId("page-indicator")).toHaveTextContent("1 / 3")
            );
            fireEvent.click(screen.getByTestId("page-next-btn"));
            expect(screen.getByTestId("page-indicator")).toHaveTextContent("2 / 3");
            fireEvent.click(screen.getByTestId("page-prev-btn"));
            expect(screen.getByTestId("page-indicator")).toHaveTextContent("1 / 3");
        });

        it("disables next button on last page", async () => {
            renderDialog();
            await waitFor(() =>
                expect(screen.getByTestId("page-indicator")).toHaveTextContent("1 / 3")
            );
            const next = screen.getByTestId("page-next-btn");
            fireEvent.click(next); // page 2
            fireEvent.click(next); // page 3
            expect(next).toBeDisabled();
        });
    });

    // ── 8 & 9. Download button ─────────────────────────────────────────────────
    describe("download button", () => {
        it("is disabled before file loads", () => {
            mockApi.get.mockReturnValue(new Promise(() => {
            }));
            renderDialog();
            expect(screen.getByTestId("preview-download-btn")).toBeDisabled();
        });

        it("triggers data-URI download when clicked", async () => {
            mockSuccess();
            // Render first so React's createRoot/render path is unaffected by spies
            renderDialog();
            await waitFor(() =>
                expect(screen.getByTestId("preview-download-btn")).not.toBeDisabled()
            );

            // Install spies only after the component is mounted
            const clickSpy = jest.fn();
            const origCreateElement = document.createElement.bind(document);
            const createSpy = jest
                .spyOn(document, "createElement")
                .mockImplementation((tag: string) => {
                    if (tag === "a") return {href: "", download: "", click: clickSpy} as any;
                    return origCreateElement(tag);
                });
            const appendSpy = jest
                .spyOn(document.body, "appendChild")
                .mockReturnValue(null as any);
            const removeSpy = jest
                .spyOn(document.body, "removeChild")
                .mockReturnValue(null as any);

            try {
                fireEvent.click(screen.getByTestId("preview-download-btn"));
                expect(clickSpy).toHaveBeenCalledTimes(1);
            } finally {
                createSpy.mockRestore();
                appendSpy.mockRestore();
                removeSpy.mockRestore();
            }
        });
    });

    // ── 10 & 11. Dialog state ──────────────────────────────────────────────────
    describe("dialog lifecycle", () => {
        it("does not fetch when isOpen is false", () => {
            renderDialog({isOpen: false});
            expect(mockApi.get).not.toHaveBeenCalled();
        });

        it("shows spinner again when dialog is closed then reopened", async () => {
            mockSuccess();
            const {rerender} = renderDialog();
            await waitFor(() => screen.getByTestId("pdf-document"));

            // Close
            rerender(<PDFPreviewDialog {...defaultProps} isOpen={false}/>);
            // Reopen with a new pending API call
            mockApi.get.mockReturnValue(new Promise(() => {
            }));
            rerender(<PDFPreviewDialog {...defaultProps} isOpen={true}/>);

            await waitFor(() =>
                expect(screen.getByTestId("pdf-loading-spinner")).toBeInTheDocument()
            );
        });
    });
});
