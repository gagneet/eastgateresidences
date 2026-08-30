// @featuretrace:dashboard-v2 — Owner-facing "Your building's strength" card.
// Layer: frontend
// Data flow: OwnerDashboard → compliance + maintenance + building_overview + insurance signals → BuildingStrengthCard (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
//           backend/routers/analytics.py  (/analytics/compliance-summary)
//           backend/routers/finance.py    (/finance/building-overview)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowRight, Check, AlertTriangle, Minus } from "lucide-react";
import RingGaugeMini from "./RingGaugeMini";

export interface StrengthItem {
  k: string;
  /**
   * true = signal is healthy, false = signal needs attention, null = UNKNOWN.
   *
   * `null` exists because a boolean cannot say "we have no data". Without it a missing
   * forecast had to be squeezed into one of the two verdicts, and both readings were
   * fabricated: an empty projection passed `.every()` and claimed "reserves stay
   * positive", while an unknown balance coerced to 0 claimed "forecast dips below zero".
   */
  ok: boolean | null;
  detail: string;
}

interface BuildingStrengthCardProps {
  /** 0-100, or null when no component signal is known. null renders as "—", never as 0. */
  score: number | null;
  grade?: string;
  delta?: number;
  items: StrengthItem[];
  onBreakdown?: () => void;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: BuildingStrengthCard
 * Path: frontend/src/components/dashboard/BuildingStrengthCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingStrengthCard({
  score,
  grade,
  delta = 0,
  items,
  onBreakdown,
  className = "",
}: BuildingStrengthCardProps) {
  const safeItems = (items || []).slice(0, 4);
  const hasScore = typeof score === "number" && Number.isFinite(score);
  // A null score used to fall through the threshold ladder to "D" — a confident failing
  // grade for a building we simply had no signals for.
  const resolvedGrade = hasScore
    ? (grade ?? (score! >= 85 ? "A" : score! >= 75 ? "B+" : score! >= 65 ? "B" : score! >= 50 ? "C" : "D"))
    : (grade ?? "—");
  // The badge was hardcoded emerald, so a "D" was presented in the same green as an "A".
  const gradeTone = !hasScore
    ? "bg-muted text-muted-foreground ring-border"
    : score! >= 75
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : score! >= 50
        ? "bg-amber-50 text-amber-700 ring-amber-200"
        : "bg-rose-50 text-rose-700 ring-rose-200";

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="building-strength-card"
      role="region"
      aria-label="Your building's strength"
    >
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-indigo-600">
            Your building's strength
          </div>
          <h3 className="text-lg font-black text-slate-900">What this means for your asset</h3>
        </div>
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${gradeTone}`}>
          Grade {resolvedGrade}
        </span>
      </div>
      <div className="flex items-center gap-5">
        <RingGaugeMini
          value={hasScore ? score! : 0}
          size={112}
          stroke={11}
          color={hasScore ? "#4F46E5" : "#CBD5E1"}
          sub={hasScore ? "Health" : "No data"}
          ariaLabel={hasScore ? `Building health score ${score} out of 100` : "Building health score unavailable"}
        />
        <ul className="flex-1 space-y-2" aria-label="Strength signal items">
          {safeItems.length === 0 ? (
            <li className="text-sm font-semibold text-slate-400">No signals available yet.</li>
          ) : (
            safeItems.map((it, i) => (
              <li key={`${it.k}-${i}`} className="flex items-start gap-2.5">
                <span
                  className={`w-6 h-6 rounded-full grid place-items-center flex-shrink-0 ${
                    it.ok == null
                      ? "bg-muted text-muted-foreground"
                      : it.ok
                        ? "bg-emerald-50 text-emerald-600"
                        : "bg-amber-50 text-amber-600"
                  }`}
                  aria-hidden="true"
                >
                  {it.ok == null ? (
                    <Minus size={14} strokeWidth={2.5} />
                  ) : it.ok ? (
                    <Check size={14} strokeWidth={2.5} />
                  ) : (
                    <AlertTriangle size={14} strokeWidth={2.5} />
                  )}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-bold text-slate-900 leading-tight">{it.k}</div>
                  <div className="text-[11px] text-slate-500">{it.detail}</div>
                </div>
              </li>
            ))
          )}
        </ul>
      </div>
      <div className="mt-4 pt-4 border-t border-slate-100 flex items-center justify-between text-xs">
        <span className="text-slate-500 font-semibold">
          Score moved{" "}
          <span className={`font-black ${delta > 0 ? "text-emerald-600" : delta < 0 ? "text-rose-600" : "text-slate-500"}`}>
            {delta > 0 ? "+" : ""}
            {delta} pts
          </span>{" "}
          this week
        </span>
        <button
          type="button"
          onClick={onBreakdown}
          className="font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
          aria-label="See building strength breakdown"
        >
          See breakdown <ArrowRight size={12} />
        </button>
      </div>
    </div>
  );
}
