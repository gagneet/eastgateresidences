// @featuretrace:dashboard-v2 — Compact Calendar: this week's events from AGM, meetings, PPM, levy dues.
// Layer: frontend
// Data flow: ManagerDashboard → /agm + /ppm/dashboard → CompactCalendar (building-scoped).
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           backend/routers/analytics.py
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";

interface CalendarItem {
  date?: string;        // ISO date string
  kind?: string;        // AGM | PPM | EC | PAY | INSP | CASH | WO
  title?: string;
  href?: string;
}

interface CompactCalendarProps {
  items?: CalendarItem[];
  className?: string;
}

const KIND_TONE: Record<string, string> = {
  AGM:  "bg-rose-50 text-rose-700 ring-rose-200",
  EC:   "bg-indigo-50 text-indigo-700 ring-indigo-200",
  PPM:  "bg-amber-50 text-amber-800 ring-amber-200",
  INSP: "bg-amber-50 text-amber-800 ring-amber-200",
  PAY:  "bg-emerald-50 text-emerald-700 ring-emerald-200",
  CASH: "bg-slate-100 text-slate-700 ring-slate-200",
  WO:   "bg-slate-100 text-slate-700 ring-slate-200",
  LEVY: "bg-indigo-50 text-indigo-700 ring-indigo-200",
};

const DAY_NAMES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
/**
 * @generated FunctionHeader
 * Function: deriveKind
 * Path: frontend/src/components/dashboard/CompactCalendar.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function deriveKind(item: CalendarItem): string {
  const t = (item.title ?? "").toLowerCase();
  if (t.includes("agm")) return "AGM";
  if (t.includes("ec ") || t.includes("committee")) return "EC";
  if (t.includes("ppm") || t.includes("inspection")) return "INSP";
  if (t.includes("levy") || t.includes("payment")) return "LEVY";
  if (t.includes("bank") || t.includes("reconcil")) return "CASH";
  if (t.includes("work order") || t.includes("repair")) return "WO";
  return item.kind?.toUpperCase() ?? "EVENT";
}
/**
 * @generated FunctionHeader
 * Function: CompactCalendar
 * Path: frontend/src/components/dashboard/CompactCalendar.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CompactCalendar({ items = [], className = "" }: CompactCalendarProps) {
  // Sort by date ascending
  const sorted = [...items].sort((a, b) => {
    if (!a.date) return 1;
    if (!b.date) return -1;
    return new Date(a.date).getTime() - new Date(b.date).getTime();
  });

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-5
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="compact-calendar"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">This week</div>
        <Link
          href="/governance/meetings"
          className="text-[10px] font-bold text-indigo-600 hover:text-indigo-800 transition-colors"
        >
          Full calendar
        </Link>
      </div>

      {sorted.length === 0 ? (
        <div className="py-6 text-center text-sm text-slate-400 font-semibold">
          No events this week
        </div>
      ) : (
        <div className="space-y-2">
          {sorted.slice(0, 7).map((e, i) => {
            const d = e.date ? new Date(e.date) : null;
            const dayName = d ? DAY_NAMES[d.getDay()] : "—";
            const dateNum = d ? d.getDate() : "–";
            const kind = deriveKind(e);
            const toneClass = KIND_TONE[kind] ?? KIND_TONE.CASH;
            const content = (
              <div className="flex items-center gap-3 rounded-xl ring-1 ring-slate-100 p-2.5 hover:ring-slate-300 transition-colors cursor-pointer">
                <div className="text-center w-10 flex-shrink-0">
                  <div className="text-[9px] font-black uppercase tracking-widest text-slate-400">{dayName}</div>
                  <div className="text-lg font-black text-slate-900 leading-none"
                       style={{ fontFeatureSettings: '"tnum" 1' }}>
                    {dateNum}
                  </div>
                </div>
                <div className="flex-1 min-w-0">
                  <span className={`inline-flex items-center gap-1.5 px-1.5 py-0 rounded-full text-[9px] font-bold uppercase tracking-[0.12em] ring-1 ${toneClass}`}>
                    {kind}
                  </span>
                  <div className="text-xs font-bold text-slate-700 mt-1 leading-tight truncate">
                    {e.title ?? "Event"}
                  </div>
                </div>
                <ArrowRight size={12} className="text-slate-300 flex-shrink-0" aria-hidden="true" />
              </div>
            );

            return e.href ? (
              <Link key={i} href={e.href} className="block">
                {content}
              </Link>
            ) : (
              <div key={i}>{content}</div>
            );
          })}
        </div>
      )}
    </div>
  );
}
