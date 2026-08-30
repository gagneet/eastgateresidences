/**
 * tests/frontend/APApprovalQueuePage.test.tsx
 *
 * Unit tests for the AP invoice approval queue UI.
 * Multi-tenant: tests use BUILDING_A ("16244") — never the real East Gate building.
 * All data is in-memory; no DB writes occur.
 */
import React from "react";
import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";
import APApprovalQueuePage from "@/pages/dashboard/financial/APApprovalQueuePage";

// ── Routing ────────────────────────────────────────────────────────────────

const mockReplace = jest.fn();

jest.mock("next/navigation", () => ({
    useRouter: () => ({push: jest.fn(), replace: mockReplace}),
    usePathname: () => "/financials/ap-approval",
}));

// ── Mock API + Auth ─────────────────────────────────────────────────────────

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

let mockIsManager = true;
let mockAuthLoading = false;

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: () => ({
        api: mockApi,
        user: {role: "strata_manager", building_id: "16244"},
        isManager: () => mockIsManager,
        loading: mockAuthLoading,
    }),
    AuthProvider: ({children}: any) => <div>{children}</div>,
}));

// ── Fixtures ───────────────────────────────────────────────────────────────

const BUILDING_A = "16244";
const INV_ID = "inv-16244-001";

const makeInvoice = (overrides: Partial<Record<string, any>> = {}) => ({
    invoice_id: INV_ID,
    state: "validated",
    vendor_name: "Acme Cleaning Pty Ltd",
    vendor_abn: "51824753556",
    invoice_number: "INV-2026-001",
    issue_date: "2026-04-01",
    due_date: "2026-04-30",
    total_cents: 110000,
    abn_valid: true,
    withholding_required: false,
    created_at: "2026-04-01T00:00:00Z",
    ...overrides,
});

const makeDetail = (overrides: Partial<Record<string, any>> = {}) => ({
    ...makeInvoice(),
    gst_cents: 10000,
    is_tax_invoice: true,
    description: "Monthly cleaning services",
    category: "cleaning",
    ocr_confidence: 0.92,
    low_confidence_fields: [],
    abn_entity_name: "Acme Cleaning Pty Ltd",
    abn_status: "Active",
    transitions: [],
    submitted_by: "supplier@acme.com.au",
    ...overrides,
});

// ── Setup ─────────────────────────────────────────────────────────────────

beforeEach(() => {
    jest.clearAllMocks();
    mockReplace.mockReset();
    mockIsManager = true;
    mockAuthLoading = false;

    mockApi.get.mockImplementation((url: string) => {
        if (url === "/ap/invoices") {
            return Promise.resolve({data: [makeInvoice()]});
        }
        if (url === `/ap/invoices/${INV_ID}`) {
            return Promise.resolve({data: makeDetail()});
        }
        return Promise.resolve({data: []});
    });

    mockApi.post.mockResolvedValue({data: {status: "ok"}});
});

// ── Access control ─────────────────────────────────────────────────────────

describe("APApprovalQueuePage — access control", () => {
    it("redirects non-managers to dashboard", async () => {
        mockIsManager = false;

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(mockReplace).toHaveBeenCalledWith("/dashboard");
        });
    });

    it("renders null while auth is loading", () => {
        mockAuthLoading = true;
        const {container} = render(<APApprovalQueuePage/>);
        expect(container.firstChild).toBeNull();
    });

    it("renders queue for managers", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("ap-approval-queue-page")).toBeInTheDocument();
        });
    });
});

// ── State tabs ─────────────────────────────────────────────────────────────

describe("APApprovalQueuePage — state tabs", () => {
    it("renders all state tabs", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("tab-validated")).toBeInTheDocument();
            expect(screen.getByTestId("tab-approved")).toBeInTheDocument();
            expect(screen.getByTestId("tab-scheduled")).toBeInTheDocument();
            expect(screen.getByTestId("tab-paid")).toBeInTheDocument();
            expect(screen.getByTestId("tab-rejected")).toBeInTheDocument();
            expect(screen.getByTestId("tab-draft")).toBeInTheDocument();
        });
    });

    it("clicking a tab reloads invoices with new state", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId("tab-approved"));
        fireEvent.click(screen.getByTestId("tab-approved"));

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith(
                "/ap/invoices",
                expect.objectContaining({params: {status: "approved"}})
            );
        });
    });
});

// ── Invoice list rendering ─────────────────────────────────────────────────

describe("APApprovalQueuePage — invoice list", () => {
    it("displays vendor name and invoice number", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId(`invoice-item-${INV_ID}`)).toBeInTheDocument();
            expect(screen.getByTestId(`invoice-item-${INV_ID}`)).toHaveTextContent("Acme Cleaning Pty Ltd");
            expect(screen.getByTestId(`invoice-item-${INV_ID}`)).toHaveTextContent("INV-2026-001");
        });
    });

    it("shows AUD formatted total", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId(`invoice-item-${INV_ID}`)).toHaveTextContent("$1,100.00");
        });
    });

    it("shows ABN verified flag for valid ABN", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("abn-valid-flag")).toBeInTheDocument();
        });
    });

    it("shows invalid ABN flag for invalid ABN", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/ap/invoices") {
                return Promise.resolve({data: [makeInvoice({abn_valid: false})]});
            }
            return Promise.resolve({data: {}});
        });

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("abn-invalid-flag")).toBeInTheDocument();
        });
    });

    it("shows withholding badge when required", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/ap/invoices") {
                return Promise.resolve({data: [makeInvoice({withholding_required: true})]});
            }
            return Promise.resolve({data: {}});
        });

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("withholding-badge")).toBeInTheDocument();
        });
    });

    it("shows empty state when no invoices", async () => {
        mockApi.get.mockImplementation(() => Promise.resolve({data: []}));

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("empty-state")).toBeInTheDocument();
        });
    });
});

// ── Detail panel ───────────────────────────────────────────────────────────

describe("APApprovalQueuePage — detail panel", () => {
    it("opens detail panel on invoice click", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => {
            expect(screen.getByTestId("invoice-detail-panel")).toBeInTheDocument();
        });
    });

    it("shows OCR confidence in detail", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => {
            expect(screen.getByTestId("ocr-confidence")).toHaveTextContent("92%");
        });
    });

    it("shows withholding warning in detail when required", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/ap/invoices") {
                return Promise.resolve({data: [makeInvoice({withholding_required: true})]});
            }
            if (url === `/ap/invoices/${INV_ID}`) {
                return Promise.resolve({data: makeDetail({withholding_required: true})});
            }
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => {
            expect(screen.getByTestId("withholding-warning")).toBeInTheDocument();
        });
    });

    it("shows Approve and Reject buttons for validated invoice", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => {
            expect(screen.getByTestId("approve-btn")).toBeInTheDocument();
            expect(screen.getByTestId("reject-btn")).toBeInTheDocument();
        });
    });

    it("shows Schedule Payment button for approved invoice", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/ap/invoices") {
                return Promise.resolve({data: [makeInvoice({state: "approved"})]});
            }
            if (url === `/ap/invoices/${INV_ID}`) {
                return Promise.resolve({data: makeDetail({state: "approved"})});
            }
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => {
            expect(screen.getByTestId("schedule-btn")).toBeInTheDocument();
        });
    });
});

// ── Actions ─────────────────────────────────────────────────────────────────

describe("APApprovalQueuePage — actions", () => {
    it("calls approve endpoint on Approve click", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => screen.getByTestId("approve-btn"));
        fireEvent.click(screen.getByTestId("approve-btn"));

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                `/ap/invoices/${INV_ID}/approve`,
                expect.any(Object)
            );
        });
    });

    it("calls reject endpoint on Reject click", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => screen.getByTestId("reject-btn"));
        fireEvent.click(screen.getByTestId("reject-btn"));

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                `/ap/invoices/${INV_ID}/reject`,
                expect.any(Object)
            );
        });
    });

    it("shows action error when API fails", async () => {
        mockApi.post.mockRejectedValueOnce({
            response: {data: {detail: "Transition not permitted"}},
        });

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId(`invoice-item-${INV_ID}`));
        fireEvent.click(screen.getByTestId(`invoice-item-${INV_ID}`));

        await waitFor(() => screen.getByTestId("approve-btn"));
        fireEvent.click(screen.getByTestId("approve-btn"));

        await waitFor(() => {
            expect(screen.getByTestId("action-error")).toHaveTextContent("Transition not permitted");
        });
    });
});

// ── Refresh ─────────────────────────────────────────────────────────────────

describe("APApprovalQueuePage — refresh", () => {
    it("refresh button reloads invoices", async () => {
        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => screen.getByTestId("refresh-btn"));
        fireEvent.click(screen.getByTestId("refresh-btn"));

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledTimes(2);
        });
    });
});

// ── Load error ─────────────────────────────────────────────────────────────

describe("APApprovalQueuePage — error handling", () => {
    it("shows error banner when list load fails", async () => {
        mockApi.get.mockRejectedValueOnce(new Error("Network error"));

        await act(async () => {
            render(<APApprovalQueuePage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("error-banner")).toBeInTheDocument();
        });
    });
});
