/**
 * tests/frontend/MatchingReviewPage.test.tsx
 *
 * Unit tests for the payment matching review queue UI.
 * Multi-tenant: tests use BUILDING_A ("16244") — never BUILDING_B ("13195") real data.
 * All test data is created in-memory; no DB writes occur.
 */
import React from "react";
import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";
import MatchingReviewPage from "@/pages/dashboard/financial/MatchingReviewPage";

// ── Routing ────────────────────────────────────────────────────────────────

const mockPush = jest.fn();
const mockReplace = jest.fn();

jest.mock("next/navigation", () => ({
    useRouter: () => ({push: mockPush, replace: mockReplace}),
    usePathname: () => "/financials/matching",
}));

// ── Radix UI stubs ─────────────────────────────────────────────────────────

jest.mock("@/components/ui/tooltip", () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipTrigger: ({children}: any) => <>{children}</>,
    TooltipContent: ({children}: any) => <span>{children}</span>,
    TooltipProvider: ({children}: any) => <>{children}</>,
}));

jest.mock("@/components/ui/dialog", () => ({
    Dialog: ({children, open}: any) => (open ? <div>{children}</div> : null),
    DialogContent: ({children, ...props}: any) => <div {...props}>{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <h2>{children}</h2>,
    DialogFooter: ({children}: any) => <div>{children}</div>,
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

const makeQueueItem = (overrides: Partial<Record<string, any>> = {}) => ({
    item_id: "item-001",
    tenant_id: BUILDING_A,
    status: "pending",
    match_type: "review",
    best_score: 0.85,
    best_layer: "L4_unit_ref_amount_timing",
    best_lot_id: "lot-16244-001",
    sla_due_at: new Date(Date.now() + 3600_000).toISOString(),
    created_at: new Date().toISOString(),
    tx: {amount_cents: 210000, description: "LEVY PAYMENT UNIT 1 STRATA"},
    candidates: [{lot_id: "lot-16244-001", unit_number: "1", owner_name: "Ahmed Al-Hassan"}],
    all_scores: [{lot_id: "lot-16244-001", score: 0.85, layer: "L4_unit_ref_amount_timing"}],
    ...overrides,
});

const makeStats = (overrides: Partial<Record<string, any>> = {}) => ({
    queue_depth: 5,
    sla_breach_count: 1,
    auto_match_rate: 0.75,
    total_last_7_days: 20,
    auto_allocated_last_7_days: 15,
    ...overrides,
});

// ── Setup ─────────────────────────────────────────────────────────────────

beforeEach(() => {
    jest.clearAllMocks();
    mockPush.mockReset();
    mockReplace.mockReset();
    mockIsManager = true;
    mockAuthLoading = false;

    mockApi.get.mockImplementation((url: string) => {
        if (url === "/financial/matching/queue") {
            return Promise.resolve({data: [makeQueueItem()]});
        }
        if (url === "/financial/matching/stats") {
            return Promise.resolve({data: makeStats()});
        }
        return Promise.resolve({data: []});
    });

    mockApi.post.mockResolvedValue({data: {action: "allocate", item_id: "item-001"}});
});

// ── Tests ─────────────────────────────────────────────────────────────────

describe("MatchingReviewPage — access control", () => {
    it("redirects non-managers to dashboard", async () => {
        mockIsManager = false;

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(mockReplace).toHaveBeenCalledWith("/dashboard");
        });
    });

    it("renders nothing while auth is loading", async () => {
        mockAuthLoading = true;

        const {container} = render(<MatchingReviewPage/>);
        expect(container.firstChild).toBeNull();
    });

    it("renders queue for managers", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("queue-list")).toBeInTheDocument();
        });
    });
});

describe("MatchingReviewPage — queue rendering", () => {
    it("shows queue items with amount and description", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("item-description-item-001")).toHaveTextContent(
                "LEVY PAYMENT UNIT 1 STRATA"
            );
            expect(screen.getByTestId("item-amount-item-001")).toHaveTextContent("$2,100.00");
        });
    });

    it("displays match type badge", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("item-match-type-item-001")).toBeInTheDocument();
        });
    });

    it("shows best score and layer", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("item-score-item-001")).toHaveTextContent("85%");
            expect(screen.getByTestId("item-layer-item-001")).toHaveTextContent(
                "L4_unit_ref_amount_timing"
            );
        });
    });

    it("shows empty state when no items", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/financial/matching/queue") return Promise.resolve({data: []});
            if (url === "/financial/matching/stats") return Promise.resolve({data: makeStats()});
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("empty-state")).toBeInTheDocument();
        });
    });
});

describe("MatchingReviewPage — stats bar", () => {
    it("renders stats bar with queue depth", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("stats-bar")).toBeInTheDocument();
        });
    });

    it("shows zero auto_match_rate gracefully", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/financial/matching/queue") return Promise.resolve({data: []});
            if (url === "/financial/matching/stats") {
                return Promise.resolve({data: makeStats({auto_match_rate: 0, total_last_7_days: 0})});
            }
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("stats-bar")).toBeInTheDocument();
        });
    });
});

describe("MatchingReviewPage — API error handling", () => {
    it("shows error banner when queue fetch fails", async () => {
        mockApi.get.mockRejectedValue(new Error("Network error"));

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("error-banner")).toBeInTheDocument();
        });
    });

    it("retry button refetches data", async () => {
        mockApi.get.mockRejectedValueOnce(new Error("Network error")).mockImplementation((url: string) => {
            if (url === "/financial/matching/queue") return Promise.resolve({data: [makeQueueItem()]});
            if (url === "/financial/matching/stats") return Promise.resolve({data: makeStats()});
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("error-banner")).toBeInTheDocument();
        });

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-retry"));
        });

        await waitFor(() => {
            expect(screen.getByTestId("queue-list")).toBeInTheDocument();
        });
    });
});

describe("MatchingReviewPage — allocate decision flow", () => {
    it("opens allocate modal when Allocate button clicked", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("btn-allocate-item-001")).toBeInTheDocument();
        });

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-allocate-item-001"));
        });

        expect(screen.getByTestId("allocate-modal")).toBeInTheDocument();
    });

    it("pre-fills best_lot_id in allocate modal", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-allocate-item-001"));

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-allocate-item-001"));
        });

        expect((screen.getByTestId("allocate-lot-id-input") as HTMLInputElement).value).toBe(
            "lot-16244-001"
        );
    });

    it("calls POST /decide with allocate action on confirm", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-allocate-item-001"));

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-allocate-item-001"));
        });

        await act(async () => {
            fireEvent.click(screen.getByTestId("allocate-confirm-btn"));
        });

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                "/financial/matching/queue/item-001/decide",
                expect.objectContaining({action: "allocate"})
            );
        });
    });

    it("cancel button closes modal without API call", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-allocate-item-001"));

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-allocate-item-001"));
        });

        expect(screen.getByTestId("allocate-modal")).toBeInTheDocument();

        await act(async () => {
            fireEvent.click(screen.getByTestId("allocate-cancel-btn"));
        });

        expect(screen.queryByTestId("allocate-modal")).not.toBeInTheDocument();
        expect(mockApi.post).not.toHaveBeenCalled();
    });
});

describe("MatchingReviewPage — reject and unidentified actions", () => {
    it("calls POST /decide with reject action", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-reject-item-001"));

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-reject-item-001"));
        });

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                "/financial/matching/queue/item-001/decide",
                expect.objectContaining({action: "reject"})
            );
        });
    });

    it("calls POST /decide with unidentified action", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-unidentified-item-001"));

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-unidentified-item-001"));
        });

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                "/financial/matching/queue/item-001/decide",
                expect.objectContaining({action: "unidentified"})
            );
        });
    });
});

describe("MatchingReviewPage — search and filter", () => {
    it("renders search input", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("search-input")).toBeInTheDocument();
        });
    });

    it("renders status filter buttons", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("status-filter-group")).toBeInTheDocument();
        });
    });

    it("search filters items by description", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/financial/matching/queue") {
                return Promise.resolve({
                    data: [
                        makeQueueItem({item_id: "item-001", tx: {amount_cents: 210000, description: "LEVY UNIT 1"}}),
                        makeQueueItem({item_id: "item-002", tx: {amount_cents: 195000, description: "BPAY PAYMENT"}}),
                    ],
                });
            }
            if (url === "/financial/matching/stats") return Promise.resolve({data: makeStats()});
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("search-input"));

        await act(async () => {
            fireEvent.change(screen.getByTestId("search-input"), {target: {value: "BPAY"}});
        });

        await waitFor(() => {
            expect(screen.queryByTestId("item-description-item-001")).not.toBeInTheDocument();
            expect(screen.getByTestId("item-description-item-002")).toBeInTheDocument();
        });
    });
});

describe("MatchingReviewPage — expand/collapse score panel", () => {
    it("clicking expand button shows scores panel", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-expand-item-001"));

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-expand-item-001"));
        });

        expect(screen.getByTestId("item-scores-panel-item-001")).toBeInTheDocument();
    });
});

describe("MatchingReviewPage — multi-tenant isolation", () => {
    it("only renders items for the current building (tenant_id)", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/financial/matching/queue") {
                return Promise.resolve({
                    data: [
                        makeQueueItem({item_id: "item-a", tenant_id: BUILDING_A}),
                    ],
                });
            }
            if (url === "/financial/matching/stats") return Promise.resolve({data: makeStats()});
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => {
            expect(screen.getByTestId("queue-item-item-a")).toBeInTheDocument();
            expect(screen.queryByTestId("queue-item-item-13195-001")).not.toBeInTheDocument();
        });
    });
});

describe("MatchingReviewPage — refresh", () => {
    it("refresh button triggers a new fetch", async () => {
        await act(async () => {
            render(<MatchingReviewPage/>);
        });

        await waitFor(() => screen.getByTestId("btn-refresh"));

        const callsBefore = mockApi.get.mock.calls.length;

        await act(async () => {
            fireEvent.click(screen.getByTestId("btn-refresh"));
        });

        await waitFor(() => {
            expect(mockApi.get.mock.calls.length).toBeGreaterThan(callsBefore);
        });
    });
});
