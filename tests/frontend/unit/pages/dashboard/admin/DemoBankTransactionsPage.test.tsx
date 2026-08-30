// @featuretrace:demo_bank — Frontend tests for Demo Bank staging viewer/uploader.
// Layer: test
// Data flow: Jest -> DemoBankTransactionsPage -> mocked GET /admin/demo-bank/transactions and
//            POST /demo-bank/import/csv (building-scoped, mocked only).
// Related: frontend/src/pages/dashboard/admin/DemoBankTransactionsPage.tsx
//          backend/routers/bank_feeds.py

import React, { type ChangeEvent, type ReactNode } from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import DemoBankTransactionsPage from "@/pages/dashboard/admin/DemoBankTransactionsPage";

const mockApi = { get: jest.fn(), post: jest.fn() };

const mockAuth = {
  api: mockApi,
  loading: false,
  isAdmin: () => true,
};

jest.mock("@/contexts/AuthContext", () => ({
  useAuth: () => mockAuth,
}));

// Radix Select needs scrollIntoView/pointer-capture APIs jsdom doesn't
// implement; this codebase's established pattern (see MyRequestsTab.test.tsx)
// is to mock the shared Select wrapper down to a plain <select>.
jest.mock("@/components/ui/select", () => {
  const ReactLib = require("react");
  return {
    Select: ({
      value,
      onValueChange,
      children,
    }: {
      value: string;
      onValueChange: (value: string) => void;
      children: ReactNode;
    }) => (
      <select
        data-testid="demo-bank-tx-source-type-filter"
        value={value}
        onChange={(event: ChangeEvent<HTMLSelectElement>) => onValueChange(event.target.value)}
      >
        {children}
      </select>
    ),
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
      <option value={value}>{children}</option>
    ),
    SelectTrigger: () => null,
    SelectValue: () => null,
  };
});

const ROWS = [
  {
    id: "tx-1",
    posted_date: "2023-03-01T00:00:00Z",
    unit_number: "87",
    account_ref: "ADMIN-13195",
    amount_cents: 113615,
    direction: "credit",
    description: "Combined levy payment",
    source_type: "historical_reconstruction",
    transaction_origin: "reconstruction",
    reconstruction_batch_id: "2efca4cb-c687-48ad-a40e-c1e4c21ac0c1",
    status: "posted",
    confidence: "high",
    assumption_code: "quarterly_regular",
    allocations: [
      { fund_type: "admin", amount_cents: 89080 },
      { fund_type: "sinking", amount_cents: 24535 },
    ],
  },
  {
    id: "tx-2",
    posted_date: "2022-06-01T00:00:00Z",
    unit_number: "12",
    account_ref: "ADMIN-13195",
    amount_cents: 50000,
    direction: "credit",
    description: "Levy payment",
    source_type: "historical_mongo",
    transaction_origin: null,
    reconstruction_batch_id: null,
    status: "posted",
    confidence: null,
    assumption_code: null,
    allocations: [],
  },
];

describe("DemoBankTransactionsPage", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAuth.loading = false;
    mockAuth.isAdmin = () => true;
  });

  it("shows loading state before auth resolves", () => {
    mockAuth.loading = true;
    render(<DemoBankTransactionsPage />);
    expect(screen.getByTestId("demo-bank-tx-loading")).toBeInTheDocument();
  });

  it("blocks non-super-admin users", () => {
    mockAuth.isAdmin = () => false;
    render(<DemoBankTransactionsPage />);
    expect(screen.getByTestId("demo-bank-tx-forbidden")).toBeInTheDocument();
    expect(screen.queryByTestId("demo-bank-tx-page")).not.toBeInTheDocument();
  });

  it("does not call the API when the user is not super_admin", () => {
    mockAuth.isAdmin = () => false;
    render(<DemoBankTransactionsPage />);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("fetches and renders transactions for super_admin", async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { data: ROWS, pagination: { page: 1, limit: 50, total: 2, total_pages: 1 } },
    });
    render(<DemoBankTransactionsPage />);
    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/admin/demo-bank/transactions", {
        params: { page: 1, limit: 50 },
      });
    });
    expect(await screen.findByTestId("demo-bank-tx-row-tx-1")).toBeInTheDocument();
    expect(screen.getByTestId("demo-bank-tx-row-tx-2")).toBeInTheDocument();
  });

  it("uploads a Demo Bank CSV through the staging import route", async () => {
    mockApi.get
      .mockResolvedValueOnce({
        data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
      })
      .mockResolvedValueOnce({
        data: { data: ROWS, pagination: { page: 1, limit: 50, total: 2, total_pages: 1 } },
      });
    mockApi.post.mockResolvedValueOnce({
      data: { imported_count: 2, skipped_count: 0, error_count: 0, duplicate_batch: false },
    });

    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(1));

    const file = new File(["Date,Description,Amount,Balance\n"], "demo-bank.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("demo-bank-csv-upload-file"), {
      target: { files: [file] },
    });
    fireEvent.change(screen.getByTestId("demo-bank-csv-upload-account"), {
      target: { value: "ADMIN-13195" },
    });
    fireEvent.click(screen.getByTestId("demo-bank-csv-upload-submit"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/demo-bank/import/csv",
      expect.any(FormData),
      { headers: { "Content-Type": "multipart/form-data" } },
    ));
    const form = mockApi.post.mock.calls[0][1] as FormData;
    expect(form.get("file")).toBe(file);
    expect(form.get("account_ref")).toBe("ADMIN-13195");
    expect(form.get("bank_name")).toBe("bank_of_sydney");
    expect(form.get("is_test_data")).toBe("false");
    expect(await screen.findByTestId("demo-bank-csv-upload-result")).toHaveTextContent("Imported 2");
    expect(mockApi.get).toHaveBeenLastCalledWith("/admin/demo-bank/transactions", {
      params: { page: 1, limit: 50, source_type: "csv_upload" },
    });
  });

  it("validates required CSV upload fields before posting", async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
    });

    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("demo-bank-csv-upload-submit"));

    expect(mockApi.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("demo-bank-csv-upload-error")).toHaveTextContent("Choose a CSV file");
  });

  it("does not default the upload account to one building", async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
    });

    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(1));

    expect(screen.getByTestId("demo-bank-csv-upload-account")).toHaveValue("");
  });

  it("can explicitly mark CSV uploads as disposable test data", async () => {
    mockApi.get
      .mockResolvedValueOnce({
        data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
      })
      .mockResolvedValueOnce({
        data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
      });
    mockApi.post.mockResolvedValueOnce({
      data: { imported_count: 0, skipped_count: 0, error_count: 0, duplicate_batch: false },
    });

    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(1));

    const file = new File(["Date,Description,Amount,Balance\n"], "test-demo-bank.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("demo-bank-csv-upload-file"), {
      target: { files: [file] },
    });
    fireEvent.change(screen.getByTestId("demo-bank-csv-upload-account"), {
      target: { value: "TEST-ACCOUNT" },
    });
    fireEvent.click(screen.getByTestId("demo-bank-csv-upload-test-data"));
    fireEvent.click(screen.getByTestId("demo-bank-csv-upload-submit"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalled());
    const form = mockApi.post.mock.calls[0][1] as FormData;
    expect(form.get("is_test_data")).toBe("true");
  });

  it("shows the fund allocation breakdown for combined transactions", async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { data: ROWS, pagination: { page: 1, limit: 50, total: 2, total_pages: 1 } },
    });
    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(screen.getByTestId("demo-bank-tx-row-tx-1")).toBeInTheDocument());
    expect(screen.getByTestId("demo-bank-tx-row-tx-1")).toHaveTextContent("admin");
    expect(screen.getByTestId("demo-bank-tx-row-tx-1")).toHaveTextContent("sinking");
  });

  it("shows empty state when no transactions are returned", async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
    });
    render(<DemoBankTransactionsPage />);
    await waitFor(() => {
      expect(screen.getByText("No Demo Bank transactions found")).toBeInTheDocument();
    });
  });

  it("shows error state on failed fetch", async () => {
    mockApi.get.mockRejectedValueOnce({ response: { data: { detail: "Unauthorised" } } });
    render(<DemoBankTransactionsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("demo-bank-tx-error")).toHaveTextContent("Unauthorised");
    });
  });

  it("requests the next page when the next-page control is clicked", async () => {
    mockApi.get
      .mockResolvedValueOnce({
        data: { data: ROWS, pagination: { page: 1, limit: 50, total: 100, total_pages: 2 } },
      })
      .mockResolvedValueOnce({
        data: { data: ROWS, pagination: { page: 2, limit: 50, total: 100, total_pages: 2 } },
      });
    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(screen.getByTestId("demo-bank-tx-next-page")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("demo-bank-tx-next-page"));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/admin/demo-bank/transactions", {
        params: { page: 2, limit: 50 },
      });
    });
  });

  it("applies the source_type filter", async () => {
    mockApi.get.mockResolvedValue({
      data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
    });
    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByTestId("demo-bank-tx-source-type-filter"), {
      target: { value: "historical_reconstruction" },
    });

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenLastCalledWith("/admin/demo-bank/transactions", {
        params: { page: 1, limit: 50, source_type: "historical_reconstruction" },
      });
    });
  });

  it("resets to page 1 in a single request when the filter changes while on a later page", async () => {
    // Regression test: sourceTypeFilter used to be reset to page 1 via a separate
    // useEffect keyed on sourceTypeFilter, racing with the fetch effect (also keyed
    // on sourceTypeFilter) in the same commit — that fired one wasted/stale request
    // with the OLD page number and the NEW filter before a second, correct request
    // landed. setPage(1) now lives in the same handler as setSourceTypeFilter so both
    // land in one batched update and exactly one (correct) request fires.
    mockApi.get
      .mockResolvedValueOnce({
        data: { data: ROWS, pagination: { page: 1, limit: 50, total: 100, total_pages: 2 } },
      })
      .mockResolvedValueOnce({
        data: { data: ROWS, pagination: { page: 2, limit: 50, total: 100, total_pages: 2 } },
      })
      .mockResolvedValueOnce({
        data: { data: [], pagination: { page: 1, limit: 50, total: 0, total_pages: 1 } },
      });

    render(<DemoBankTransactionsPage />);
    await waitFor(() => expect(screen.getByTestId("demo-bank-tx-next-page")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("demo-bank-tx-next-page"));
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(2));

    fireEvent.change(screen.getByTestId("demo-bank-tx-source-type-filter"), {
      target: { value: "historical_reconstruction" },
    });

    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(3));
    expect(mockApi.get).toHaveBeenLastCalledWith("/admin/demo-bank/transactions", {
      params: { page: 1, limit: 50, source_type: "historical_reconstruction" },
    });
  });
});
