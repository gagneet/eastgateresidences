// @featuretrace:dashboard-v2 — Owner "This week — only your stuff" 4-card personal action list.
// Layer: frontend
// Data flow: OwnerDashboard → owner_overview + proposals + agm + workflow-requests → ThisWeekActions (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowRight, DollarSign, Vote, Users, Wrench } from "lucide-react";

type Tone = "amber" | "indigo" | "rose" | "slate";

export interface WeekAction {
  id: string;
  title: string;
  sub: string;
  tone: Tone;
  cta: string;
  icon?: "dollar" | "vote" | "users" | "wrench";
  href?: string;
}

interface ThisWeekActionsProps {
  items: WeekAction[];
  estimatedMinutes?: number;
  onNavigate?: (action: WeekAction) => void;
  className?: string;
}

const ICONS = {
  dollar: DollarSign,
  vote: Vote,
  users: Users,
  wrench: Wrench,
};

const TONE_RING = {
  amber: "bg-amber-50 text-amber-600",
  indigo: "bg-indigo-50 text-indigo-600",
  rose: "bg-rose-50 text-rose-600",
  slate: "bg-slate-100 text-slate-600",
};

const TONE_PILL = {
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  rose: "bg-rose-50 text-rose-700 ring-rose-200",
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
};

const TONE_LABEL = {
  amber: "Due soon",
  indigo: "Vote open",
  rose: "Action needed",
  slate: "Update",
};
/**
 * @generated FunctionHeader
 * Function: ThisWeekActions
 * Path: frontend/src/components/dashboard/ThisWeekActions.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ThisWeekActions({
  items,
  estimatedMinutes,
  onNavigate,
  className = "",
}: ThisWeekActionsProps) {
  const safe = (items || []).slice(0, 4);
  const minutesLabel = estimatedMinutes ? `They take ~${estimatedMinutes} minutes total.` : "";

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 overflow-hidden shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="this-week-actions"
      aria-label="This week — only your stuff"
    >
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 text-indigo-600 grid place-items-center" aria-hidden="true">
            <Vote size={18} />
          </div>
          <div>
            <div className="text-[10px] font-black uppercase tracking-[0.22em] text-indigo-600">
              This week — only your stuff
            </div>
            <h3 className="text-lg font-black text-slate-900">
              {safe.length === 0 ? "Nothing waits for you this week." : `${safe.length} thing${safe.length === 1 ? "" : "s"} wait for you. ${minutesLabel}`}
            </h3>
          </div>
        </div>
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 bg-indigo-50 text-indigo-700 ring-indigo-200">
          Reminders on · Mon 9am
        </span>
      </header>
      {safe.length === 0 ? (
        <div className="p-8 text-center text-sm font-semibold text-slate-400">
          You're all caught up. Come back next Monday for your weekly digest.
        </div>
      ) : (
        <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4" role="list">
          {safe.map((t, i) => {
            const Icon = t.icon ? ICONS[t.icon] : Wrench;
            // Borders: vertical between lg-grid columns; horizontal only
            // between rows of the sm-grid (so the last row never carries a
            // stray bottom border at the 2-col breakpoint).
            const isLastInLgRow = i === safe.length - 1;
            const isInLastSmRow = i >= safe.length - (safe.length % 2 === 0 ? 2 : 1);
            return (
              <li
                key={t.id}
                className={`p-5 ${!isLastInLgRow ? "lg:border-r border-slate-100" : ""} ${
                  !isInLastSmRow ? "border-b lg:border-b-0" : ""
                }`}
              >
                <div className="flex items-center justify-between mb-3">
                  <div className={`w-9 h-9 rounded-xl grid place-items-center ${TONE_RING[t.tone]}`} aria-hidden="true">
                    <Icon size={18} />
                  </div>
                  <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${TONE_PILL[t.tone]}`}>
                    {TONE_LABEL[t.tone]}
                  </span>
                </div>
                <div className="text-sm font-black text-slate-900 leading-tight">{t.title}</div>
                <div className="text-[11px] text-slate-500 mt-1 leading-snug">{t.sub}</div>
                <button
                  type="button"
                  onClick={() => onNavigate?.(t)}
                  className="mt-4 w-full rounded-lg bg-slate-900 text-white text-xs font-bold py-2 inline-flex items-center justify-center gap-1.5 hover:bg-slate-800 transition-colors"
                  aria-label={`${t.cta}: ${t.title}`}
                >
                  {t.cta} <ArrowRight size={12} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
