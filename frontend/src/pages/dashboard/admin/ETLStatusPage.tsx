// @featuretrace:bi-analytics — ETL Status admin page: shows BI ETL run history and triggers manual runs.
// Layer: frontend
// Data flow: ETLStatusPage → GET /api/bi/admin/etl-status + POST /api/bi/admin/etl/run → analytics.bi_etl_runs (PG).
// Related: backend/routers/bi.py
//           frontend/src/pages/dashboard/BIAnalyticsPage.tsx
// Toggle: bi_analytics_enabled
"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock, Database, Play, RefreshCw } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

interface ETLTableRow {
  table: string;
  last_run: string | null;
  total_inserted: number;
  error_count: number;
}

interface ETLStatusResponse {
  tables: ETLTableRow[];
  error?: string;
}
/**
 * @generated FunctionHeader
 * Function: fmtDate
 * Path: frontend/src/pages/dashboard/admin/ETLStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function fmtDate(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  return d.toLocaleString("en-AU", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
/**
 * @generated FunctionHeader
 * Function: staleness
 * Path: frontend/src/pages/dashboard/admin/ETLStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function staleness(iso: string | null): "fresh" | "stale" | "never" {
  if (!iso) return "never";
  const age = Date.now() - new Date(iso).getTime();
  return age < 48 * 3600 * 1000 ? "fresh" : "stale";
}
/**
 * @generated FunctionHeader
 * Function: ETLStatusPage
 * Path: frontend/src/pages/dashboard/admin/ETLStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ETLStatusPage() {
  const { api, user, loading: authLoading, selectedBuilding } = useAuth() as any;
  const router = useRouter();

  const [status, setStatus] = useState<ETLStatusResponse | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const effectiveRole = user?.effective_role || user?.role || "";
  const isAdmin = ["super_admin", "strata_admin"].includes(effectiveRole);

  useEffect(() => {
    if (!authLoading && !isAdmin) {
      router.replace("/dashboard");
    }
  }, [authLoading, isAdmin, router]);

  const fetchStatus = useCallback(async () => {
    if (!api) return;
    setStatusLoading(true);
    try {
      const res = await api.get("/bi/admin/etl-status");
      setStatus(res.data);
    } catch {
      setStatus({ tables: [], error: "Failed to load ETL status" });
    } finally {
      setStatusLoading(false);
    }
  }, [api]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);
  /**
   * @generated FunctionHeader
   * Function: runETL
   * Path: frontend/src/pages/dashboard/admin/ETLStatusPage.tsx
   *
   * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
   */
  const runETL = async () => {
    const bid = selectedBuilding?.building_id || selectedBuilding?.id;
    if (!bid) { toast.error("No building selected"); return; }
    setRunning(true);
    try {
      await api.post(`/bi/admin/etl/run?building_id=${encodeURIComponent(bid)}`);
      toast.success("ETL run triggered — refresh in a few seconds to see results");
      setTimeout(fetchStatus, 5000);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "ETL run failed");
    } finally {
      setRunning(false);
    }
  };

  if (authLoading || !isAdmin) return null;

  const tables = status?.tables ?? [];
  const totalErrors = tables.reduce((s, r) => s + r.error_count, 0);
  const staleCount = tables.filter(r => staleness(r.last_run) !== "fresh").length;

  return (
    <div className="space-y-6 pb-16">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/intelligence/bi")}
            className="flex items-center gap-2 text-slate-500 hover:text-slate-900 text-sm font-bold transition-colors group"
            aria-label="Back to BI Analytics"
          >
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" aria-hidden="true" />
            BI Analytics
          </button>
          <span className="text-slate-300">/</span>
          <h1 className="text-xl font-black text-slate-900">ETL Status</h1>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchStatus}
            disabled={statusLoading}
            aria-label="Refresh ETL status"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${statusLoading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={runETL}
            disabled={running || statusLoading}
            aria-label="Run ETL now"
          >
            <Play className="w-4 h-4 mr-2" aria-hidden="true" />
            {running ? "Running…" : "Run ETL now"}
          </Button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        <Card className="border-none shadow-sm">
          <CardContent className="p-5">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-slate-100 rounded-xl"><Database className="w-5 h-5 text-slate-600" aria-hidden="true" /></div>
              <div>
                <p className="text-2xl font-black text-slate-900">{tables.length}</p>
                <p className="text-xs text-slate-500 font-semibold">Tables tracked</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className={`border-none shadow-sm ${staleCount > 0 ? "ring-1 ring-amber-200 bg-amber-50/40" : ""}`}>
          <CardContent className="p-5">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-xl ${staleCount > 0 ? "bg-amber-100" : "bg-emerald-100"}`}>
                <Clock className={`w-5 h-5 ${staleCount > 0 ? "text-amber-600" : "text-emerald-600"}`} aria-hidden="true" />
              </div>
              <div>
                <p className={`text-2xl font-black ${staleCount > 0 ? "text-amber-700" : "text-emerald-700"}`}>{staleCount}</p>
                <p className="text-xs text-slate-500 font-semibold">Stale (&gt;48 h)</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className={`border-none shadow-sm ${totalErrors > 0 ? "ring-1 ring-red-200 bg-red-50/40" : ""}`}>
          <CardContent className="p-5">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-xl ${totalErrors > 0 ? "bg-red-100" : "bg-emerald-100"}`}>
                {totalErrors > 0
                  ? <AlertTriangle className="w-5 h-5 text-red-600" aria-hidden="true" />
                  : <CheckCircle2 className="w-5 h-5 text-emerald-600" aria-hidden="true" />
                }
              </div>
              <div>
                <p className={`text-2xl font-black ${totalErrors > 0 ? "text-red-700" : "text-emerald-700"}`}>{totalErrors}</p>
                <p className="text-xs text-slate-500 font-semibold">Error runs (all-time)</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Error banner */}
      {status?.error && (
        <div className="flex items-center gap-3 p-4 rounded-xl bg-red-50 border border-red-200 text-red-800 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
          {status.error}
        </div>
      )}

      {/* Table */}
      <Card className="border-none shadow-sm overflow-hidden">
        <CardHeader className="bg-slate-50/60 border-b pb-3">
          <CardTitle className="text-base font-black text-slate-900">ETL Run History by Table</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {statusLoading ? (
            <div className="flex items-center justify-center py-16 text-slate-400">
              <RefreshCw className="w-5 h-5 animate-spin mr-2" aria-hidden="true" />
              Loading ETL status…
            </div>
          ) : tables.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
              <Database className="w-10 h-10 text-slate-300" aria-hidden="true" />
              <p className="text-sm font-semibold text-slate-500">No ETL runs recorded yet.</p>
              <p className="text-xs text-slate-400 max-w-sm">
                Trigger a run using the button above, or enable{" "}
                <code className="rounded bg-slate-100 px-1 font-mono">bi_analytics_building_enabled</code>{" "}
                per building in Feature Toggles first.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm" role="table" aria-label="ETL run history">
              <thead>
                <tr className="border-b bg-slate-50/40 text-left text-[11px] font-black uppercase tracking-widest text-slate-400">
                  <th className="px-6 py-3">Table</th>
                  <th className="px-6 py-3">Last run</th>
                  <th className="px-6 py-3 text-right">Rows inserted</th>
                  <th className="px-6 py-3 text-right">Errors</th>
                  <th className="px-6 py-3 text-right">Health</th>
                </tr>
              </thead>
              <tbody>
                {tables.map((row) => {
                  const age = staleness(row.last_run);
                  return (
                    <tr key={row.table} className="border-b last:border-0 hover:bg-slate-50/50 transition-colors">
                      <td className="px-6 py-3.5 font-mono text-slate-700 text-xs">
                        {row.table.replace("analytics.", "")}
                      </td>
                      <td className="px-6 py-3.5 text-slate-600">{fmtDate(row.last_run)}</td>
                      <td className="px-6 py-3.5 text-right tabular-nums text-slate-700">
                        {row.total_inserted.toLocaleString()}
                      </td>
                      <td className="px-6 py-3.5 text-right">
                        <span className={`font-black tabular-nums ${row.error_count > 0 ? "text-red-600" : "text-emerald-600"}`}>
                          {row.error_count}
                        </span>
                      </td>
                      <td className="px-6 py-3.5 text-right">
                        {age === "fresh" && (
                          <Badge className="bg-emerald-100 text-emerald-700 border-0 text-[10px] font-black">Fresh</Badge>
                        )}
                        {age === "stale" && (
                          <Badge className="bg-amber-100 text-amber-700 border-0 text-[10px] font-black">Stale</Badge>
                        )}
                        {age === "never" && (
                          <Badge className="bg-slate-100 text-slate-500 border-0 text-[10px] font-black">Never run</Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <p className="text-xs text-slate-400">
        ETL populates <code className="font-mono">analytics.fact_*</code> tables from MongoDB operational data.
        Run <strong>after</strong> enabling <code className="font-mono">bi_analytics_building_enabled</code> in Feature Toggles.
        Stale = last run &gt; 48 hours ago.
      </p>
    </div>
  );
}
