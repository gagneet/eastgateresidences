// @featuretrace:dashboard-v2 — Community Pulse Feed: filterable activity feed for owners (events, notices, market, social).
// Layer: frontend
// Data flow: OwnerDashboard → /analytics/activities?limit=15 → CommunityPulseFeed (building-scoped).
// Related: frontend/src/pages/dashboard/OwnerDashboard.tsx
//           frontend/src/components/dashboard/ActivityFeed.tsx  (manager variant)
//           backend/routers/analytics.py
// Toggle: ft_dashboard_v2

"use client";
import React, { useState } from "react";
import { ArrowRight } from "lucide-react";
import Link from "next/link";

export interface ActivityItem {
  id?: string;
  type?: string;       // "announcement" | "maintenance" | "payment" | "marketplace" | "event"
  title?: string;
  message?: string;
  created_at?: string;
  // kind for community display
  kind?: "event" | "market" | "notice" | "social" | "pay" | "req";
}

interface CommunityPulseFeedProps {
  items?: ActivityItem[];
  className?: string;
  onItemSelect?: (item: ActivityItem) => void;
}

type FilterChip = "All" | "Events" | "Market" | "Notices" | "Social";

const CHIP_KIND_MAP: Record<FilterChip, string[]> = {
  All:     [],
  Events:  ["event"],
  Market:  ["market", "marketplace"],
  // "pay" — levy payment activities (type: "payment"|"levy" → deriveKind → "pay")
  // "req" — maintenance/request activities (type: "maintenance"|"request"|"case" → "req")
  // Neither has a dedicated tab; both included here so they appear in Notices.
  Notices: ["notice", "announcement", "pay", "req"],
  Social:  ["social"],
};

const KIND_TONE: Record<string, string> = {
  event:        "bg-emerald-50 text-emerald-700 ring-emerald-200",
  market:       "bg-indigo-50 text-indigo-700 ring-indigo-200",
  marketplace:  "bg-indigo-50 text-indigo-700 ring-indigo-200",
  notice:       "bg-amber-50 text-amber-800 ring-amber-200",
  announcement: "bg-amber-50 text-amber-800 ring-amber-200",
  social:       "bg-rose-50 text-rose-700 ring-rose-200",
  pay:          "bg-emerald-50 text-emerald-700 ring-emerald-200",
  req:          "bg-amber-50 text-amber-800 ring-amber-200",
};
/**
 * @generated FunctionHeader
 * Function: deriveKind
 * Path: frontend/src/components/dashboard/CommunityPulseFeed.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function deriveKind(item: ActivityItem): string {
  if (item.kind) return item.kind;
  const t = (item.type ?? "").toLowerCase();
  if (t.includes("event")) return "event";
  if (t.includes("market")) return "market";
  if (t.includes("announc")) return "notice";
  if (t.includes("payment") || t.includes("levy")) return "pay";
  if (t.includes("maintenance") || t.includes("request") || t === "case") return "req";
  return "notice";
}
/**
 * @generated FunctionHeader
 * Function: relativeTime
 * Path: frontend/src/components/dashboard/CommunityPulseFeed.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function relativeTime(dateStr?: string): string {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const now = new Date();
  const diff = Math.round((now.getTime() - d.getTime()) / 60_000); // minutes
  if (diff < 60) return `${diff}m ago`;
  if (diff < 1440) return `${Math.floor(diff / 60)}h ago`;
  const days = Math.floor(diff / 1440);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString("en-AU", { day: "numeric", month: "short" });
}
/**
 * @generated FunctionHeader
 * Function: CommunityPulseFeed
 * Path: frontend/src/components/dashboard/CommunityPulseFeed.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CommunityPulseFeed({ items = [], className = "", onItemSelect }: CommunityPulseFeedProps) {
  const [filter, setFilter] = useState<FilterChip>("All");

  const filtered = filter === "All"
    ? items
    : items.filter((it) => CHIP_KIND_MAP[filter].includes(deriveKind(it)));

  const chips: FilterChip[] = ["All", "Events", "Market", "Notices", "Social"];

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="community-pulse-feed"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Building community
          </div>
          <h3 className="text-lg font-black text-slate-900">What neighbours are doing this week</h3>
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Filter community feed">
          {chips.map((t) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              aria-pressed={filter === t}
              className={`text-[10px] font-black uppercase tracking-widest px-2 py-1 rounded transition-colors ${
                filter === t ? "bg-slate-900 text-white" : "text-slate-400 hover:bg-slate-50"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* Feed items */}
      {filtered.length === 0 ? (
        <div className="py-6 text-center text-sm text-slate-400 font-semibold">
          Nothing in this category yet
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.slice(0, 6).map((c, i) => {
            const kind = deriveKind(c);
            const toneClass = KIND_TONE[kind] ?? KIND_TONE.notice;
            const when = relativeTime(c.created_at);
            const text = c.title ?? c.message ?? "";

            const rowContent = (
              <>
                <span className={`inline-flex items-center gap-1.5 px-1.5 py-0 rounded-full text-[9px] font-bold uppercase tracking-[0.12em] ring-1 flex-shrink-0 ${toneClass}`}>
                  {kind}
                </span>
                <div className="text-[11px] text-slate-400 font-bold uppercase tracking-widest w-20 flex-shrink-0">
                  {when}
                </div>
                <div className="flex-1 text-sm font-bold text-slate-900 truncate">{text}</div>
                <span className="text-xs font-black text-indigo-600 px-3 py-1 rounded-lg bg-indigo-50/0 flex-shrink-0">View</span>
              </>
            );

            if (!onItemSelect) {
              return (
                <Link
                  key={c.id ?? i}
                  href="/community"
                  className="w-full text-left flex items-center gap-3 rounded-xl ring-1 ring-slate-100 p-3 hover:ring-slate-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  aria-label={`View: ${text}`}
                >
                  {rowContent}
                </Link>
              );
            }

            return (
              <button
                key={c.id ?? i}
                type="button"
                onClick={() => onItemSelect(c)}
                className="w-full text-left flex items-center gap-3 rounded-xl ring-1 ring-slate-100 p-3 hover:ring-slate-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
                aria-label={`Open activity: ${text}`}
              >
                {rowContent}
              </button>
            );
          })}
        </div>
      )}

      {/* Footer */}
      <div className="mt-4 pt-4 border-t border-slate-100 flex items-center justify-between text-xs">
        <span className="text-slate-500 font-semibold">
          {items.length} activity items
        </span>
        <Link
          href="/community"
          className="font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
        >
          Open community <ArrowRight size={12} />
        </Link>
      </div>
    </div>
  );
}
