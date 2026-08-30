// @featuretrace:demo_bank — Super-admin-only viewer/uploader for raw Demo Bank staging transactions.
// Layer: frontend
// Data flow: DemoBankTransactionsPage -> GET /admin/demo-bank/transactions -> demo_bank_transactions (building-scoped).
//            DemoBankTransactionsPage -> POST /demo-bank/import/csv -> demo_bank_transactions (staging only).
// Related: backend/routers/admin_diagnostics.py (GAP-ADMIN-001 — deliberately not gated behind
//          financial_integration_layer_v2 or any other building-scoped feature toggle)
//          backend/routers/demo_bank.py (POST /demo-bank/import/csv)
//          frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx (guard pattern)
//          frontend/src/pages/dashboard/admin/LeadsPage.jsx (table/pagination pattern)

"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ChevronLeft, ChevronRight, Upload } from "lucide-react";

import {formatMoneyFromCents} from '@/lib/currency';
// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Allocation {
  fund_type: string;
  amount_cents: number;
  amount_ex_gst_cents?: number;
  gst_cents?: number;
}

interface DemoBankTransaction {
  id: string;
  posted_date: string | null;
  unit_number: string | null;
  account_ref: string | null;
  amount_cents: number | null;
  direction: string | null;
  description: string | null;
  source_type: string | null;
  transaction_origin: string | null;
  reconstruction_batch_id: string | null;
  status: string | null;
  confidence: string | null;
  assumption_code: string | null;
  allocations: Allocation[];
}

interface Pagination {
  page: number;
  limit: number;
  total: number;
  total_pages: number;
}

interface CsvImportResult {
  batch_id?: string;
  imported_count?: number;
  skipped_count?: number;
  error_count?: number;
  duplicate_batch?: boolean;
}

const SOURCE_TYPE_OPTIONS = [
  { value: "all", label: "All source types" },
  { value: "historical_reconstruction", label: "Historical reconstruction" },
  { value: "historical_mongo", label: "Historical (Mongo)" },
  { value: "synthetic_from_budget", label: "Synthetic (budget)" },
  { value: "seed", label: "Seed" },
  { value: "strata_web_inferred_payment", label: "Strata Web inferred" },
  { value: "csv_upload", label: "CSV upload" },
  { value: "manual", label: "Manual" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}

function AllocationBreakdown({ allocations }: { allocations: Allocation[] }) {
  if (!allocations || allocations.length === 0) return null;
  return (
    <div className="mt-0.5 text-xs text-muted-foreground">
      {allocations.map((a, i) => (
        <span key={a.fund_type}>
          {i > 0 && " + "}
          {a.fund_type} {formatMoneyFromCents(a.amount_cents)}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DemoBankTransactionsPage() {
  const { api, loading, isAdmin } = useAuth() as any;

  const [rows, setRows] = useState<DemoBankTransaction[]>([]);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, limit: 50, total: 0, total_pages: 1 });
  const [page, setPage] = useState(1);
  const [sourceTypeFilter, setSourceTypeFilter] = useState("all");
  const [isFetching, setIsFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadAccountRef, setUploadAccountRef] = useState("");
  const [uploadBankName, setUploadBankName] = useState("bank_of_sydney");
  const [uploadAsTestData, setUploadAsTestData] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<CsvImportResult | null>(null);

  const canAccess = !loading && isAdmin();

  // setPage(1) lives inside the Select's onValueChange handler below, not a
  // separate useEffect keyed on sourceTypeFilter — a separate effect would run
  // in the same commit as the fetch effect below (both depend on
  // sourceTypeFilter), so when the user changes the filter while on page > 1,
  // the fetch effect fires once with the OLD page + NEW filter (a combination
  // that may not even have a page 2) before the page-reset effect's own
  // setState triggers a second, correct fetch. Setting both in one handler
  // avoids the extra request and the momentary wrong-page fetch.
  function handleSourceTypeChange(value: string) {
    setSourceTypeFilter(value);
    setPage(1);
  }

  async function handleCsvUpload() {
    if (!uploadFile || !uploadAccountRef.trim() || !uploadBankName.trim()) {
      setUploadError("Choose a CSV file, account ref, and bank schema.");
      return;
    }
    setIsUploading(true);
    setUploadError(null);
    setUploadResult(null);
    try {
      const form = new FormData();
      form.append("file", uploadFile);
      form.append("account_ref", uploadAccountRef.trim());
      form.append("bank_name", uploadBankName.trim());
      form.append("is_test_data", String(uploadAsTestData));
      const res = await api.post("/demo-bank/import/csv", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setUploadResult(res?.data ?? {});
      setSourceTypeFilter("csv_upload");
      setPage(1);
      const refreshed = await api.get("/admin/demo-bank/transactions", {
        params: { page: 1, limit: 50, source_type: "csv_upload" },
      });
      setRows(refreshed?.data?.data ?? []);
      setPagination(refreshed?.data?.pagination ?? { page: 1, limit: 50, total: 0, total_pages: 1 });
    } catch (err: any) {
      setUploadError(err?.response?.data?.detail ?? "Failed to upload Demo Bank CSV.");
    } finally {
      setIsUploading(false);
    }
  }

  const fetchTransactions = useCallback(async () => {
    if (!canAccess) return;
    setIsFetching(true);
    setError(null);
    try {
      const params: Record<string, string | number> = { page, limit: 50 };
      if (sourceTypeFilter !== "all") params.source_type = sourceTypeFilter;
      const res = await api.get("/admin/demo-bank/transactions", { params });
      setRows(res?.data?.data ?? []);
      setPagination(res?.data?.pagination ?? { page: 1, limit: 50, total: 0, total_pages: 1 });
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to load Demo Bank transactions.");
    } finally {
      setIsFetching(false);
    }
  }, [api, canAccess, page, sourceTypeFilter]);

  useEffect(() => {
    if (loading || !canAccess) return;
    fetchTransactions();
  }, [loading, canAccess, fetchTransactions]);

  // ---------------------------------------------------------------------------
  // Guards
  // ---------------------------------------------------------------------------

  if (loading) {
    return <div data-testid="demo-bank-tx-loading" className="p-6 text-sm">Loading…</div>;
  }

  if (!canAccess) {
    return (
      <div data-testid="demo-bank-tx-forbidden" className="p-6">
        <p className="text-sm text-destructive font-medium">
          Access denied. This page is for super_admin only.
        </p>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <main className="space-y-6 p-4 md:p-6" data-testid="demo-bank-tx-page">
      <div
        className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
        data-testid="demo-bank-tx-internal-warning"
        role="alert"
      >
        <strong>⚠ INTERNAL ADMIN ONLY.</strong> Raw Demo Bank staging transactions,
        including unsynced historical reconstructions. For super_admin review only.
      </div>

      <header className="rounded-lg border p-4">
        <h1 className="text-xl font-semibold" data-testid="demo-bank-tx-title">
          Demo Bank Transactions
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          All staged Demo Bank transactions for your building, across every source type.
        </p>
      </header>

      <Card className="border shadow-sm" data-testid="demo-bank-csv-upload-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-semibold">CSV Upload</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-[1fr_160px_170px_auto] md:items-end">
          <div className="flex flex-col gap-1">
            <label htmlFor="demo-bank-upload-file" className="text-xs font-medium text-muted-foreground">
              File
            </label>
            <Input
              id="demo-bank-upload-file"
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
              data-testid="demo-bank-csv-upload-file"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="demo-bank-upload-account" className="text-xs font-medium text-muted-foreground">
              Account ref
            </label>
            <Input
              id="demo-bank-upload-account"
              value={uploadAccountRef}
              onChange={(event) => setUploadAccountRef(event.target.value)}
              data-testid="demo-bank-csv-upload-account"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="demo-bank-upload-bank" className="text-xs font-medium text-muted-foreground">
              Bank schema
            </label>
            <Input
              id="demo-bank-upload-bank"
              value={uploadBankName}
              onChange={(event) => setUploadBankName(event.target.value)}
              data-testid="demo-bank-csv-upload-bank"
            />
          </div>
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <input
                type="checkbox"
                checked={uploadAsTestData}
                onChange={(event) => setUploadAsTestData(event.target.checked)}
                data-testid="demo-bank-csv-upload-test-data"
              />
              Test data
            </label>
            <Button
              type="button"
              size="sm"
              onClick={handleCsvUpload}
              disabled={isUploading}
              data-testid="demo-bank-csv-upload-submit"
            >
              <Upload className="mr-2 h-4 w-4" />
              {isUploading ? "Uploading..." : "Upload"}
            </Button>
          </div>
          {(uploadError || uploadResult) && (
            <div className="md:col-span-4">
              {uploadError && (
                <div className="rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800" data-testid="demo-bank-csv-upload-error">
                  {uploadError}
                </div>
              )}
              {uploadResult && (
                <div className="rounded-md border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm text-emerald-800" data-testid="demo-bank-csv-upload-result">
                  Imported {uploadResult.imported_count ?? 0}, skipped {uploadResult.skipped_count ?? 0}, errors {uploadResult.error_count ?? 0}
                  {uploadResult.duplicate_batch ? " (duplicate batch)" : ""}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <section className="flex flex-wrap items-end gap-3" data-testid="demo-bank-tx-filters">
        <div className="flex flex-col gap-1">
          <label htmlFor="source-type-filter" className="text-xs font-medium text-muted-foreground">
            Source type
          </label>
          <Select value={sourceTypeFilter} onValueChange={handleSourceTypeChange}>
            <SelectTrigger id="source-type-filter" data-testid="demo-bank-tx-source-type-filter" className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SOURCE_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="rounded-full"
          onClick={fetchTransactions}
          disabled={isFetching}
          data-testid="demo-bank-tx-refresh"
        >
          {isFetching ? "Loading…" : "Refresh"}
        </Button>
      </section>

      {error && (
        <div
          data-testid="demo-bank-tx-error"
          className="rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <Card className="border shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-semibold">
            {isFetching ? "Loading…" : `${pagination.total} transaction${pagination.total !== 1 ? "s" : ""}`}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table data-testid="demo-bank-tx-table">
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">Date</TableHead>
                <TableHead>Unit</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Source type</TableHead>
                <TableHead>Batch</TableHead>
                <TableHead className="pr-6">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isFetching && (
                <TableRow>
                  <TableCell colSpan={8} className="text-center py-12 text-muted-foreground">
                    Loading transactions…
                  </TableCell>
                </TableRow>
              )}
              {!isFetching && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-center py-12 text-muted-foreground">
                    No Demo Bank transactions found
                  </TableCell>
                </TableRow>
              )}
              {!isFetching &&
                rows.map((row) => (
                  <TableRow key={row.id} data-testid={`demo-bank-tx-row-${row.id}`}>
                    <TableCell className="pl-6 text-sm whitespace-nowrap">{fmtDate(row.posted_date)}</TableCell>
                    <TableCell className="text-sm">{row.unit_number ?? "—"}</TableCell>
                    <TableCell className="text-sm font-mono">{row.account_ref ?? "—"}</TableCell>
                    <TableCell className="text-sm">
                      <div>{formatMoneyFromCents(row.amount_cents)}</div>
                      <AllocationBreakdown allocations={row.allocations} />
                    </TableCell>
                    <TableCell className="text-sm capitalize">{row.direction ?? "—"}</TableCell>
                    <TableCell className="text-sm">{row.source_type ?? "—"}</TableCell>
                    <TableCell className="text-xs font-mono text-muted-foreground">
                      {row.reconstruction_batch_id ? row.reconstruction_batch_id.slice(0, 8) : "—"}
                    </TableCell>
                    <TableCell className="pr-6 text-sm">{row.status ?? "—"}</TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </CardContent>

        {pagination.total_pages > 1 && (
          <div className="flex items-center justify-between px-6 py-3 border-t">
            <p className="text-sm text-muted-foreground">
              Page {pagination.page} of {pagination.total_pages}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-full"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                data-testid="demo-bank-tx-prev-page"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="rounded-full"
                disabled={page >= pagination.total_pages}
                onClick={() => setPage((p) => p + 1)}
                data-testid="demo-bank-tx-next-page"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </main>
  );
}
