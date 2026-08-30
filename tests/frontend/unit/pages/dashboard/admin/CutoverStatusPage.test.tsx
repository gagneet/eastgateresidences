// @featuretrace:finance-postgres-write-cutover — Frontend tests for internal finance write readiness controls.
// Data flow: Jest → CutoverStatusPage → mocked cutover admin finance write readiness API (building-scoped).
// Related: frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import CutoverStatusPage from "@/pages/dashboard/admin/CutoverStatusPage";

const mockApi = { get: jest.fn(), post: jest.fn() };

const mockAuth = {
  api: mockApi,
  loading: false,
  isAdmin: () => true,
  hasFeatureAccess: () => true,
};

jest.mock("@/contexts/AuthContext", () => ({
  useAuth: () => mockAuth,
}));

const ITEMS = [
  {
    building_id: "13195",
    domain: "finance_ledger",
    mode: "mongo_primary",
    readiness_status: "unknown",
    read_source: "mongo",
    write_source: "mongo",
    last_readiness_check_at: "2026-06-04T10:00:00Z",
    last_promoted_at: null,
    last_shadow_diff_at: null,
    rollback_available: false,
    toggle_name: null,
    notes: null,
    p0_snapshot: {
      financial_onboarding: {
        summary: {
          evidence_approved: true,
          funds_bootstrapped: true,
          genesis_journals_posted: 2,
        },
      },
    },
  },
  {
    building_id: "13195",
    domain: "conversation_threads",
    mode: "postgres_shadow",
    readiness_status: "shadow_active",
    read_source: "mongo",
    write_source: "mongo",
    last_readiness_check_at: null,
    last_promoted_at: "2026-06-02T10:00:00Z",
    last_shadow_diff_at: "2026-06-02T10:05:00Z",
    rollback_available: true,
    toggle_name: "powerhouse_conversations",
    notes: null,
    p0_snapshot: {},
  },
];

describe("CutoverStatusPage", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAuth.loading = false;
    mockAuth.isAdmin = () => true;
  });

  it("shows internal warning banner", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: [], total: 0 } });
    render(<CutoverStatusPage />);
    expect(screen.getByTestId("cutover-internal-warning")).toBeInTheDocument();
    expect(screen.getByTestId("cutover-internal-warning")).toHaveTextContent("INTERNAL ADMIN ONLY");
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/admin/cutover/status"));
  });

  it("blocks non-admin users with 403 message", () => {
    mockAuth.isAdmin = () => false;
    render(<CutoverStatusPage />);
    expect(screen.getByTestId("cutover-forbidden")).toBeInTheDocument();
    expect(screen.queryByTestId("cutover-status-page")).not.toBeInTheDocument();
  });

  it("shows loading state", () => {
    mockAuth.loading = true;
    render(<CutoverStatusPage />);
    expect(screen.getByTestId("cutover-loading")).toBeInTheDocument();
  });

  it("shows empty state when no domains registered", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: [], total: 0 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("cutover-empty")).toBeInTheDocument();
    });
  });

  it("renders table rows for returned domains", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("cutover-table")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cutover-row-13195-finance_ledger")).toBeInTheDocument();
    expect(screen.getByTestId("cutover-row-13195-conversation_threads")).toBeInTheDocument();
  });

  it("renders correct mode badges", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("mode-badge-mongo_primary")).toBeInTheDocument();
      expect(screen.getByTestId("mode-badge-postgres_shadow")).toBeInTheDocument();
    });
  });

  it("shadow button enabled only for mongo_primary rows", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("cutover-table")).toBeInTheDocument();
    });
    const shadowBtnPrimary = screen.getByTestId("cutover-shadow-13195-finance_ledger");
    const shadowBtnShadow  = screen.getByTestId("cutover-shadow-13195-conversation_threads");
    expect(shadowBtnPrimary).not.toBeDisabled();
    expect(shadowBtnShadow).toBeDisabled();
  });

  it("promote-read button enabled only for postgres_shadow rows", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => expect(screen.getByTestId("cutover-table")).toBeInTheDocument());
    const readBtnPrimary = screen.getByTestId("cutover-read-13195-finance_ledger");
    const readBtnShadow  = screen.getByTestId("cutover-read-13195-conversation_threads");
    expect(readBtnPrimary).toBeDisabled();
    expect(readBtnShadow).not.toBeDisabled();
  });

  it("rollback button disabled when rollback_available=false", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => expect(screen.getByTestId("cutover-table")).toBeInTheDocument());
    // finance_ledger: mongo_primary + rollback_available=false
    expect(screen.getByTestId("cutover-rollback-13195-finance_ledger")).toBeDisabled();
    // conversation_threads: postgres_shadow + rollback_available=true
    expect(screen.getByTestId("cutover-rollback-13195-conversation_threads")).not.toBeDisabled();
  });

  it("calls /admin/cutover/{building}/{domain}/shadow on shadow button click", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    mockApi.post.mockResolvedValueOnce({
      data: { message: "Now in shadow mode", new_mode: "postgres_shadow" },
    });
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });

    render(<CutoverStatusPage />);
    await waitFor(() => expect(screen.getByTestId("cutover-table")).toBeInTheDocument());

    const shadowBtn = screen.getByTestId("cutover-shadow-13195-finance_ledger");
    fireEvent.click(shadowBtn);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/admin/cutover/13195/finance_ledger/shadow",
        {},
      );
    });
  });

  it("shows success message after successful action", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    mockApi.post.mockResolvedValueOnce({
      data: { message: "Domain is now in postgres_shadow mode", new_mode: "postgres_shadow" },
    });
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });

    render(<CutoverStatusPage />);
    await waitFor(() => expect(screen.getByTestId("cutover-table")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("cutover-shadow-13195-finance_ledger"));

    await waitFor(() => {
      expect(screen.getByTestId("cutover-action-success")).toBeInTheDocument();
    });
    await waitFor(() => expect(mockApi.get.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("shows error message when action fails", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    mockApi.post.mockRejectedValueOnce({ response: { data: { detail: "P0 check failed" } } });

    render(<CutoverStatusPage />);
    await waitFor(() => expect(screen.getByTestId("cutover-table")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("cutover-shadow-13195-finance_ledger"));

    await waitFor(() => {
      expect(screen.getByTestId("cutover-action-error")).toHaveTextContent("P0 check failed");
    });
  });

  it("shows error state on failed fetch", async () => {
    mockApi.get.mockRejectedValueOnce({ response: { data: { detail: "Unauthorised" } } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("cutover-error")).toHaveTextContent("Unauthorised");
    });
  });

  it("does not call API when user is not admin", () => {
    mockAuth.isAdmin = () => false;
    render(<CutoverStatusPage />);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("shows total count footer", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("cutover-total-count")).toHaveTextContent("2 domain(s) shown");
    });
  });

  it("shows the finance readiness panel when finance status is present", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("finance-readiness-panel")).toBeInTheDocument();
    });
    expect(screen.getByTestId("finance-readiness-evidence")).toHaveTextContent("Approved");
    expect(screen.getByTestId("finance-readiness-funds")).toHaveTextContent("Bootstrapped");
  });

  it("shows pending approval when evidence is not approved yet", async () => {
    const financeItem = ITEMS[0]!;
    const financeSummary = financeItem.p0_snapshot.financial_onboarding!.summary;
    const financePendingItem = {
      ...financeItem,
      p0_snapshot: {
        financial_onboarding: {
          summary: {
            ...financeSummary,
            evidence_approved: false,
          },
        },
      },
    };
    mockApi.get.mockResolvedValueOnce({ data: { items: [financePendingItem], total: 1 } });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("finance-readiness-panel")).toBeInTheDocument();
    });
    expect(screen.getByTestId("finance-readiness-evidence")).toHaveTextContent("Pending approval");
  });

  it("renders finance route readiness table for internal users", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } })
      .mockResolvedValueOnce({
        data: [
          {
            route_key: "finance.building_overview",
            method: "GET",
            path: "/finance/building-overview",
            frontend_consumer: "Owner dashboard",
            read_only: true,
            shadow_supported: true,
            postgres_read_supported: true,
            current_source: "mongo",
            domain_mode: "postgres_shadow",
            readiness_status: "shadow_pass",
            diff_count: 0,
            critical_count: 0,
            last_compared_at: null,
            shadow_monitoring_active: true,
            eligible_for_postgres_read: true,
            blocked_reason: null,
            notes: "ok",
          },
        ],
      })
      .mockResolvedValueOnce({
        data: [
          {
            route_key: "finance.levy_payment_create",
            method: "POST",
            path: "/levy-payments",
            frontend_consumer: "Finance payment recording forms",
            current_write_source: "MongoDB levy_payments + unit_levy_ledger",
            target_write_source: "PostgreSQL finance.receipts",
            current_source: "postgres",
            domain_mode: "postgres_write",
            cutover_mode_required: "postgres_write",
            idempotency_required: true,
            audit_required: true,
            reversal_supported: true,
            eligible_for_postgres_write: true,
            blocked_reason: null,
            production_readiness_status: "eligible for postgres_write now",
            notes: "ok",
          },
        ],
      });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("finance-routes-table")).toBeInTheDocument();
    });
    expect(screen.getByText("finance.building_overview")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("finance-write-routes-table")).toBeInTheDocument());
    expect(screen.getByText("finance.levy_payment_create")).toBeInTheDocument();
    expect(screen.getByTestId("finance-write-routes-warning")).toHaveTextContent(
      "route-by-route",
    );
  });

  it("shows a frozen indicator for promoted routes whose shadow monitoring has stopped", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: { items: ITEMS, total: 2 } })
      .mockResolvedValueOnce({
        data: [
          {
            route_key: "finance.arrears_detail",
            method: "GET",
            path: "/arrears/detail",
            frontend_consumer: "Arrears recovery board",
            read_only: true,
            shadow_supported: true,
            postgres_read_supported: true,
            current_source: "postgres",
            domain_mode: "postgres_write",
            readiness_status: "shadow_fail",
            diff_count: 16,
            critical_count: 16,
            last_compared_at: "2026-08-09T10:31:01+00:00",
            // Promoted to postgres -- shadow compares stopped, so the diff/critical
            // counts above are a frozen pre-promotion snapshot, not live.
            shadow_monitoring_active: false,
            eligible_for_postgres_read: true,
            blocked_reason: null,
            notes: "ok",
          },
        ],
      })
      .mockResolvedValueOnce({ data: [] });
    render(<CutoverStatusPage />);
    await waitFor(() => {
      expect(screen.getByTestId("finance-routes-table")).toBeInTheDocument();
    });
    expect(screen.getByText("(frozen)")).toBeInTheDocument();
    expect(screen.getByText("monitoring stopped")).toBeInTheDocument();
  });

  it("disables finance promote-read when no route is eligible", async () => {
    const financeShadowItem = { ...ITEMS[0], mode: "postgres_shadow" };
    mockApi.get
      .mockResolvedValueOnce({ data: { items: [financeShadowItem], total: 1 } })
      .mockResolvedValueOnce({
        data: [
          {
            route_key: "finance.building_overview",
            method: "GET",
            path: "/finance/building-overview",
            frontend_consumer: "Owner dashboard",
            read_only: true,
            shadow_supported: true,
            postgres_read_supported: true,
            current_source: "mongo",
            domain_mode: "postgres_shadow",
            readiness_status: "shadow_warn",
            diff_count: 2,
            critical_count: 0,
            last_compared_at: null,
            eligible_for_postgres_read: false,
            blocked_reason: "Route readiness is shadow_warn",
            notes: "wait",
          },
        ],
      });
    render(<CutoverStatusPage />);
    await waitFor(() => expect(screen.getByTestId("cutover-table")).toBeInTheDocument());
    expect(screen.getByTestId("cutover-read-13195-finance_ledger")).toBeDisabled();
  });
});
