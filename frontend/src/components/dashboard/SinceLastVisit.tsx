// @featuretrace:dashboard-v2 — "Since your last visit" strip: highlights key changes since last dashboard open.
// Layer: frontend
// Data flow: ManagerDashboard → /analytics/diff-since?since=<ts> → SinceLastVisit (building-scoped).
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           backend/routers/analytics.py  (GET /analytics/diff-since)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { Check, ArrowRight } from "lucide-react";
import Link from "next/link";

export interface DiffItem {
  kind: string;
  tone: "rose" | "amber" | "mint" | "indigo" | "slate";
  label: string;
  value: string;
}

interface SinceLastVisitProps {
  /** Whole days since the previous visit. null means this is the FIRST visit. */
  daysSince?: number | null;
  items?: DiffItem[];
  onMarkRead?: () => void;
  onItemSelect?: (item: DiffItem) => void;
  className?: string;
}

const TONE_CLASSES: Record<string, string> = {
  rose:   "bg-rose-50 text-rose-700 ring-rose-200",
  amber:  "bg-amber-50 text-amber-800 ring-amber-200",
  mint:   "bg-emerald-50 text-emerald-700 ring-emerald-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  slate:  "bg-slate-100 text-slate-700 ring-slate-200",
};

const CADENCE = [
  { label: "Tue · cash check" },
  { label: "Thu · triage clear" },
  { label: "Fri · EC prep" },
];
/**
 * @generated FunctionHeader
 * Function: SinceLastVisit
 * Path: frontend/src/components/dashboard/SinceLastVisit.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
/** "3 days ago", "5 weeks ago", "7 months ago", "2 years ago".
 *
 * The old label was `${daysSince} days ago` at every magnitude, so a manager returning
 * after a long absence read "417 days ago" — technically true and useless at a glance.
 * Thresholds are deliberately coarse: this is a subtitle, not an audit field, and the
 * reader only needs to know whether they were away for days, weeks or months.
 */
function elapsedLabel(days: number): string {
  if (days <= 1) return "1 day ago";
  if (days < 14) return `${days} days ago`;
  if (days < 60) {
    const weeks = Math.round(days / 7);
    return `${weeks} week${weeks === 1 ? "" : "s"} ago`;
  }
  if (days < 365) {
    const months = Math.round(days / 30);
    return `${months} month${months === 1 ? "" : "s"} ago`;
  }
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

export default function SinceLastVisit({
  daysSince = null,
  items = [],
  onMarkRead,
  onItemSelect,
  className = "",
}: SinceLastVisitProps) {
  // null means the account has no previous login. Defaulting to 1 told a brand-new user
  // "Since your last visit · 1 day ago … here's what changed while you were away", which
  // is three false claims in one heading: that they have visited, when, and that the
  // items below postdate it.
  const isFirstVisit = daysSince == null;
  const dayLabel = isFirstVisit ? "Welcome" : elapsedLabel(daysSince as number);

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 h-full flex flex-col
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="since-last-visit"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-indigo-600">
            {isFirstVisit ? "1st visit · Welcome" : `Since your last visit · ${dayLabel}`}
          </div>
          <h3 className="text-xl font-black text-slate-900 mt-0.5">
            {isFirstVisit
              ? "Recent activity in your building"
              : "Here's what changed while you were away"}
          </h3>
        </div>
        {onMarkRead && (
          <button
            onClick={onMarkRead}
            className="text-xs font-bold text-slate-500 hover:text-slate-900 inline-flex items-center gap-1 transition-colors"
            aria-label="Mark all changes as read"
          >
            Mark all read <Check size={14} />
          </button>
        )}
      </div>

      {/* Change cards — always 4 key cards (SLA, CASH, AGM, COMPLIANCE) + optional 5th */}
      {items.length > 0 ? (
        <div className={`grid gap-3 flex-1 ${items.length >= 5 ? "grid-cols-5" : "grid-cols-2 sm:grid-cols-4"}`}>
          {items.slice(0, 5).map((it, i) => {
            const cardContent = (
              <>
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${TONE_CLASSES[it.tone] ?? TONE_CLASSES.slate}`}>
                  {it.kind}
                </span>
                <div className="mt-2 text-sm font-bold text-slate-900 leading-tight">{it.label}</div>
                <div className="mt-2 text-[11px] font-black uppercase tracking-wider text-slate-500"
                     style={{ fontFeatureSettings: '"tnum" 1' }}>
                  {it.value}
                </div>
              </>
            );
            const cardClassName = `rounded-2xl ring-1 p-3 text-left transition-all ${
              it.tone === "slate" ? "ring-slate-200 bg-white" : "ring-slate-200 bg-white"
            }`;

            if (!onItemSelect) {
              return <div key={i} className={cardClassName}>{cardContent}</div>;
            }

            return (
              <button
                key={i}
                type="button"
                onClick={() => onItemSelect(it)}
                className={`${cardClassName} hover:ring-slate-900 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-400`}
                aria-label={`Open ${it.kind}: ${it.label}`}
              >
                {cardContent}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-sm text-slate-400 font-semibold">
          Loading activity summary…
        </div>
      )}

      {/* Weekly cadence footer */}
      <div className="mt-4 pt-4 border-t border-slate-100 flex items-center justify-between text-xs">
        <div className="flex items-center gap-6">
          <span className="text-slate-400 font-semibold">Weekly cadence</span>
          {CADENCE.map((c) => (
            <span key={c.label} className="font-bold text-slate-900">{c.label}</span>
          ))}
        </div>
        <Link
          href="/requests"
          className="font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
        >
          Open weekly digest <ArrowRight size={12} />
        </Link>
      </div>
    </div>
  );
}
