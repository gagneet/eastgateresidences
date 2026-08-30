// @featuretrace:dashboard-v2 — Triage Queue: ranked list of items by impact × urgency with one-tap actions.
// Layer: frontend
// Data flow: ManagerDashboard → /workflow-requests?status=overdue|awaiting_review + /workflow-requests/stats/triage
//            → TriageQueue (building-scoped).
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           backend/routers/workflow_requests.py
// Toggle: ft_dashboard_v2

"use client";
import React, { useState } from "react";
import { Inbox, ArrowRight, ChevronRight, Zap } from "lucide-react";
import Link from "next/link";
import { OVERDUE_REQUEST_QUEUE_HREF } from "@/lib/requests/requestScope";

interface TriageItem {
  id?: string;
  request_number?: string;
  unit_number?: string;
  subject?: string;
  title?: string;
  request_type?: string;
  status?: string;
  sla_breached?: boolean;
  priority?: string;
  created_at?: string;
  // v2 enriched fields (computed client-side or from stats endpoint)
  rank?: number;
  code?: string;
  who?: string;
  what?: string;
  amount?: string;
  since?: string;
  urgency?: "now" | "today" | "this week";
  action?: string;
}

interface TriageStats {
  auto_resolved_today?: number;
  deflection_rate_pct?: number;
}

interface TriageQueueProps {
  items?: TriageItem[];
  stats?: TriageStats | null;
  onAction?: (item: TriageItem) => void;
  /**
   * Where "View request inbox" goes. /requests on its own renders the request
   * FORM CATALOGUE, not the queue — callers pass the tracking view
   * (?tab=my-requests, optionally &status=overdue) so the footer link matches
   * what this card is showing.
   */
  queueHref?: string;
  className?: string;
}

type FilterTab = "All" | "Now" | "Today" | "This week";
/**
 * @generated FunctionHeader
 * Function: daysAgo
 * Path: frontend/src/components/dashboard/TriageQueue.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function daysAgo(dateStr?: string): string {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const now = new Date();
  const diff = Math.round((now.getTime() - d.getTime()) / 86_400_000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "1 day ago";
  return `${diff} days ago`;
}
/**
 * @generated FunctionHeader
 * Function: deriveUrgency
 * Path: frontend/src/components/dashboard/TriageQueue.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function deriveUrgency(item: TriageItem): "now" | "today" | "this week" {
  if (item.sla_breached || item.status === "overdue") return "now";
  if (item.priority === "urgent" || item.priority === "high") return "today";
  return "this week";
}
/**
 * @generated FunctionHeader
 * Function: deriveAction
 * Path: frontend/src/components/dashboard/TriageQueue.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function deriveAction(item: TriageItem): string {
  const type = (item.request_type || "").toLowerCase();
  if (item.sla_breached) return "Handle breach";
  if (type.includes("levy") || type.includes("payment")) return "Send notice";
  if (type.includes("invoice") || type.includes("approval")) return "Approve";
  if (type.includes("maintenance") || type.includes("repair")) return "Assign vendor";
  return "Review";
}

const URGENCY_BUTTON: Record<string, string> = {
  now:       "bg-rose-600 text-white hover:bg-rose-700",
  today:     "bg-slate-900 text-white hover:bg-slate-800",
  "this week": "bg-white text-slate-900 hover:bg-slate-50 ring-1 ring-slate-200",
};

const URGENCY_PILL: Record<string, string> = {
  now:       "bg-rose-50 text-rose-700 ring-rose-200",
  today:     "bg-amber-50 text-amber-800 ring-amber-200",
  "this week": "bg-slate-100 text-slate-700 ring-slate-200",
};
/**
 * @generated FunctionHeader
 * Function: TriageQueue
 * Path: frontend/src/components/dashboard/TriageQueue.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TriageQueue({ items = [], stats, onAction, queueHref = OVERDUE_REQUEST_QUEUE_HREF, className = "" }: TriageQueueProps) {
  const [filter, setFilter] = useState<FilterTab>("All");

  // Enrich items with v2 fields
  const enriched = items.map((it, i) => ({
    ...it,
    rank: it.rank ?? i + 1,
    code: it.code ?? it.request_number ?? `REQ-${i + 1}`,
    who: it.who ?? it.unit_number ?? "—",
    what: it.what ?? it.subject ?? it.title ?? it.request_type?.replace(/_/g, " ").toUpperCase() ?? "Request",
    since: it.since ?? daysAgo(it.created_at),
    urgency: it.urgency ?? deriveUrgency(it),
    action: it.action ?? deriveAction(it),
    amount: it.amount ?? "",
  }));

  const filtered = filter === "All"
    ? enriched
    : enriched.filter((it) => {
        if (filter === "Now") return it.urgency === "now";
        if (filter === "Today") return it.urgency === "today";
        return it.urgency === "this week";
      });

  const TABS: FilterTab[] = ["All", "Now", "Today", "This week"];

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 overflow-hidden
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="triage-queue"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-rose-50 text-rose-600 grid place-items-center">
            <Inbox size={18} />
          </div>
          <div>
            <div className="text-[10px] font-black uppercase tracking-[0.22em] text-rose-600">
              Today's Triage · {items.length} item{items.length !== 1 ? "s" : ""}
            </div>
            <h3 className="text-lg font-black text-slate-900">
              Do these in order — ranked by impact × urgency
            </h3>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                filter === t ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              {t}
            </button>
          ))}
          {stats && (
            <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400 ml-3">
              Auto-resolved today:{" "}
              <span className="text-emerald-600">{stats.auto_resolved_today ?? 0}</span>
              {" "}· Deflection{" "}
              <span className="text-emerald-600">{stats.deflection_rate_pct ?? 0}%</span>
            </span>
          )}
        </div>
      </div>

      {/* Rows */}
      {filtered.length === 0 ? (
        <div className="px-6 py-8 text-center text-sm text-slate-400 font-semibold">
          <Zap size={24} className="mx-auto mb-2 text-slate-300" />
          No items in this category — nice work!
        </div>
      ) : (
        <div>
          {filtered.map((t, i) => (
            <div
              key={t.id ?? i}
              className={`px-6 py-3.5 flex items-center gap-4 group ${
                i !== filtered.length - 1 ? "border-b border-slate-100" : ""
              } hover:bg-slate-50/60 transition-colors`}
            >
              {/* Rank */}
              <div className="w-7 text-center text-[11px] font-black text-slate-300"
                   style={{ fontFeatureSettings: '"tnum" 1' }}>
                #{t.rank}
              </div>

              {/* SLA dot */}
              <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
                t.sla_breached || t.urgency === "now" ? "bg-rose-500" : "bg-amber-400"
              }`} />

              {/* Code */}
              <div className="w-24 font-mono text-[11px] font-bold text-slate-500 truncate">{t.code}</div>

              {/* Description */}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-bold text-slate-900 truncate">
                  {t.what}{t.who !== "—" && <span className="text-slate-500 font-medium"> · {t.who}</span>}
                </div>
                <div className="text-[11px] text-slate-400 mt-0.5 font-medium">{t.since}</div>
              </div>

              {/* Amount */}
              {t.amount && (
                <div className="w-32 text-right text-sm font-black text-slate-900"
                     style={{ fontFeatureSettings: '"tnum" 1' }}>
                  {t.amount}
                </div>
              )}

              {/* Urgency pill */}
              <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${
                URGENCY_PILL[t.urgency ?? "this week"]
              }`}>
                {t.urgency}
              </span>

              {/* Action button */}
              <button
                onClick={() => onAction?.(t)}
                className={`rounded-lg px-3 py-1.5 text-xs font-bold inline-flex items-center gap-1.5 transition-colors ${
                  URGENCY_BUTTON[t.urgency ?? "this week"]
                }`}
                aria-label={`${t.action} for ${t.code}`}
              >
                {t.action} <ArrowRight size={12} />
              </button>

              {/* Expand */}
              <Link
                href={t.id ? "/requests/" + t.id : queueHref}
                className="text-slate-300 hover:text-slate-900 px-1 transition-colors"
                aria-label={`Open ${t.code} detail`}
              >
                <ChevronRight size={16} />
              </Link>
            </div>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="px-6 py-3 bg-slate-50/60 border-t border-slate-100 text-xs flex items-center justify-between">
        <span className="text-slate-500 font-semibold">
          {stats
            ? `Auto-handled by rules: ${stats.auto_resolved_today ?? 0} · Deflection ${stats.deflection_rate_pct ?? 0}%`
            : "Ranked by urgency × impact"}
        </span>
        <Link
          href={queueHref}
          className="font-bold text-slate-900 inline-flex items-center gap-1 hover:text-indigo-600 transition-colors"
        >
          View request inbox <ArrowRight size={12} />
        </Link>
      </div>
    </div>
  );
}
