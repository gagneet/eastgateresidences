// @featuretrace:dashboard-v2 — Building Pulse hero card: composite health score, levy trend, 5-axis breakdown.
// Layer: frontend
// Data flow: ManagerDashboard → /stats/building-kpis → PulseScoreCard (building-scoped).
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           backend/routers/analytics.py
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

interface HealthAxis {
  k: string;
  v: number;
  color?: string;
}

interface PulseScoreCardProps {
  /**
   * 0-100, or `null` when the backend could not measure enough of the building
   * to publish a score. Null is NOT the same as 0 — see the render below.
   */
  score?: number | null;
  delta?: number;
  grade?: string;
  breakdown?: HealthAxis[];
  trend?: number[];
  /** Components the backend could not measure, shown when there is no score. */
  unavailableComponents?: string[];
  className?: string;
  updatedAt?: string;
  onOpenDetails?: () => void;
  onSelectAxis?: (axis: HealthAxis) => void;
  onSelectTrend?: () => void;
}

const DEFAULT_AXES: HealthAxis[] = [
  { k: "Cash",        v: 0, color: "#16A34A" },
  { k: "Compliance",  v: 0, color: "#0EA5E9" },
  { k: "Maintenance", v: 0, color: "#F59E0B" },
  { k: "Governance",  v: 0, color: "#7C3AED" },
  { k: "Community",   v: 0, color: "#E11D48" },
];
/** Tiny SVG sparkline — no Recharts dependency so it renders inside the dark card */
function MiniSparkline({ points, color = "#34D399", height = 48 }: { points: number[]; color?: string; height?: number }) {
  if (!points || points.length < 2) return null;
  const w = 200;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = (max - min) || 1;
  const step = w / (points.length - 1);
  const coords = points.map((v, i) => [i * step, height - ((v - min) / range) * (height - 6) - 3]);
  const d = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = d + ` L ${w} ${height} L 0 ${height} Z`;
  const last = coords[coords.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <path d={area} fill={color} opacity="0.15" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill={color} />
    </svg>
  );
}
/**
 * @generated FunctionHeader
 * Function: PulseScoreCard
 * Path: frontend/src/components/dashboard/PulseScoreCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */

// What each axis measures, taken from backend/services/health_score_service.py so the
// explanation cannot drift from the formula. Kept beside the render rather than in the
// hook: it is presentation copy, and the hook is shared with cards that do not show it.
export const AXIS_HELP: Record<string, string> = {
  Financial:
    "Weighted from sinking-fund adequacy (60%) and arrears rate (40%). Adequacy is "
    + "currently excluded because no capital-works forecast is configured, so this "
    + "reflects arrears alone, scaled so 20% of lots in arrears scores zero.",
  Maintenance:
    "Share of work orders completed within SLA, plus overdue count. 100 means nothing "
    + "is overdue.",
  Compliance:
    "Overdue items on the compliance register against the total tracked.",
  Engagement:
    "Volunteer events and meeting participation recorded year to date.",
  Disputes:
    "Unresolved by-law breaches against total lots — fewer is better. A matter referred "
    + "to ACAT or NCAT still counts as unresolved: the register has handed off, but a "
    + "tribunal case is the most serious live dispute a scheme can have, and excluding "
    + "it would report a building with five active cases as having none.",
};

// Why an axis is unavailable, where the reason is specific enough to be worth saying.
// The generic line ("no data recorded yet, excluded rather than counted as zero") is
// true for every axis; these add the part a manager would otherwise have to ask about.
export const AXIS_UNAVAILABLE_HELP: Record<string, string> = {
  Disputes:
    "Nothing has been recorded in the by-law breach register for this building. That is "
    + "not the same as having no disputes — it means there is no dispute evidence either "
    + "way, so the axis is excluded and its weight redistributed. Scoring it 100/100 "
    + "would award a tenth of the health score for an empty register. Record a breach to "
    + "start measuring it; once any report exists, zero unresolved becomes a real 100.",
  Financial:
    "No capital-works forecast is configured, so sinking-fund adequacy cannot be "
    + "measured.",
  Engagement:
    "No volunteer events or meeting participation have been recorded for this building.",
};

export default function PulseScoreCard({
  score = null,
  delta = 0,
  grade = "–",
  breakdown,
  trend = [],
  unavailableComponents = [],
  className = "",
  updatedAt,
  onOpenDetails,
  onSelectAxis,
  onSelectTrend,
}: PulseScoreCardProps) {
  const axes = breakdown && breakdown.length > 0
    ? breakdown
    : DEFAULT_AXES;

  const DeltaIcon = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus;
  const deltaColor = delta > 0 ? "text-emerald-300" : delta < 0 ? "text-rose-300" : "text-slate-400";
  // A null score has no grade colour. Falling through to the rose "failing"
  // palette would paint "we have no data" as "this building is in trouble".
  const hasScore = typeof score === "number";
  const gradeColor = !hasScore ? "bg-white/10 text-white/50 ring-white/20"
    : score >= 80 ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
    : score >= 60 ? "bg-amber-500/15 text-amber-300 ring-amber-500/30"
    : "bg-rose-500/15 text-rose-300 ring-rose-500/30";

  const now = updatedAt
    ? new Date(updatedAt).toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" })
    : new Date().toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" });

  return (
    <section
      className={`rounded-3xl bg-slate-900 text-white overflow-hidden relative ${onOpenDetails ? "cursor-pointer" : ""} ${className}`}
      data-testid="pulse-score-card"
      onClick={onOpenDetails}
      role={onOpenDetails ? "button" : "region"}
      tabIndex={onOpenDetails ? 0 : undefined}
      aria-label={onOpenDetails ? "Open building pulse details" : undefined}
      onKeyDown={(e) => {
        if (!onOpenDetails) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpenDetails();
        }
      }}
    >
      {/* Subtle radial glow */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ background: "radial-gradient(circle at 30% 30%, rgba(79,70,229,.09), transparent 60%)" }} />

      <div className="relative p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 bg-white/10 text-white ring-white/15">
            Building Pulse · live
          </span>
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/50">
            Updated {now}
          </span>
        </div>

        {/* Score + trend */}
        <div className="flex items-end gap-6">
          <div>
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40 mb-1">Health Score</div>
            <div className="flex items-baseline gap-2">
              {/* An unmeasurable score renders as an em-dash, never as 0. A zero
                  draws the full "failing" treatment and tells the reader this
                  building is in trouble, when the truth is that we have nothing
                  to go on. Missing and zero are distinct states. */}
              <span className="text-7xl font-black leading-none" style={{ fontFeatureSettings: '"tnum" 1' }}>
                {hasScore ? score : "—"}
              </span>
              <span className="text-2xl font-black text-white/40">/100</span>
            </div>
            <div className="mt-2 flex items-center gap-3">
              {hasScore ? (
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${gradeColor}`}>
                  Grade {grade}
                </span>
              ) : (
                <span
                  className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${gradeColor}`}
                  title={unavailableComponents.length
                    ? `Waiting on: ${unavailableComponents.join(", ")}`
                    : "Not enough measurable data yet"}
                  data-testid="pulse-insufficient-data"
                >
                  Not enough data
                </span>
              )}
              {delta !== 0 && (
                <span className={`text-xs font-bold inline-flex items-center gap-1 ${deltaColor}`}>
                  <DeltaIcon size={12} />
                  {delta > 0 ? "+" : ""}{delta} this week
                </span>
              )}
            </div>
          </div>

          {/* Levy trend sparkline — both callers feed this the last ~8 years of total
              annual levy ($), not a weekly score history, so the label says "Levy trend"
              rather than claiming a cadence ("8-week") this data doesn't have. */}
          {trend.length > 1 && (
            onSelectTrend ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectTrend();
                }}
                className="ml-auto self-stretch flex-1 max-w-[180px] text-left rounded-lg focus:outline-none focus:ring-2 focus:ring-white/40"
                aria-label="Open levy trend details"
              >
                <div className="text-[9px] font-bold uppercase tracking-[0.18em] text-white/40 mb-1">Levy trend</div>
                <MiniSparkline points={trend} color="#34D399" height={48} />
              </button>
            ) : (
              <div className="ml-auto self-stretch flex-1 max-w-[180px]">
                <div className="text-[9px] font-bold uppercase tracking-[0.18em] text-white/40 mb-1">Levy trend</div>
                <MiniSparkline points={trend} color="#34D399" height={48} />
              </div>
            )
          )}
        </div>

        {/* Axis breakdown */}
        <div className="mt-5 grid grid-cols-5 gap-2">
          {axes.map((b) => {
            // An axis with no data reads "NA" and draws no bar. Two things this must
            // NOT do: render the raw null (which printed an empty cell and, worse, set
            // `width: null%` — invalid CSS, so the track kept whatever width it last
            // had and the five axes no longer lined up); or substitute 0, which draws a
            // measured-looking empty bar. A fabricated zero on a health axis is the
            // exact bug fixed for the engagement pulse on 2026-08-24 — "no volunteer
            // events recorded" was scored 0/100 and drew a lone full-red axis beside
            // four blank ones.
            const hasValue = typeof b.v === "number" && Number.isFinite(b.v);
            const pct = hasValue ? Math.max(0, Math.min(100, b.v as number)) : 0;
            const why = AXIS_HELP[b.k] || "";
            // An unavailable axis gets the axis-specific reason where one exists, so the
            // card answers "why is this NA?" in place, rather than sending the reader to
            // the code to find out. Falls back to the generic line otherwise.
            const whyUnavailable = AXIS_UNAVAILABLE_HELP[b.k]
              || "No data is recorded for this axis yet, so it is excluded from the score "
                 + "rather than counted as zero.";
            const tooltip = hasValue
              ? `${b.k}: ${b.v}/100. ${why}`
              : `${b.k}: not available — ${whyUnavailable} ${why}`;

            const axisContent = (
              <>
                <div className="text-[9px] font-bold uppercase tracking-widest text-white/40">{b.k}</div>
                <div
                  className={`text-lg font-black mt-1 ${hasValue ? "" : "text-white/35"}`}
                  style={{ fontFeatureSettings: '"tnum" 1' }}
                  title={tooltip}
                >
                  {hasValue ? b.v : "NA"}
                </div>
                <div className="mt-2 h-1 rounded-full bg-white/10 overflow-hidden" title={tooltip}>
                  {hasValue ? (
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${pct}%`, background: b.color || "#4F46E5" }}
                    />
                  ) : (
                    // Deliberately no coloured fill: an unavailable axis must be visibly
                    // different from a measured zero, not a shorter version of it.
                    <div className="h-full w-full bg-[repeating-linear-gradient(45deg,rgba(255,255,255,0.12)_0px,rgba(255,255,255,0.12)_2px,transparent_2px,transparent_5px)]" />
                  )}
                </div>
              </>
            );

            if (!onSelectAxis) {
              return (
                <div key={b.k} className="rounded-xl bg-white/5 ring-1 ring-white/10 p-3 text-left">
                  {axisContent}
                </div>
              );
            }

            return (
              <button
                key={b.k}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectAxis(b);
                }}
                className="rounded-xl bg-white/5 ring-1 ring-white/10 p-3 text-left hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-white/40"
                aria-label={`Open ${b.k} pulse details`}
              >
                {axisContent}
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
