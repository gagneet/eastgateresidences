// @featuretrace:dashboard-v2 — Management "Levy Fairness Index" card with ring + overpay/underpay + drivers.
// Layer: frontend
// Data flow: ManagementDashboard → /intelligence/levy-fairness → LevyFairnessCard (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
//           backend/routers/intelligence.py
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowRight } from "lucide-react";
import { formatCurrency } from "../../lib/utils";
import RingGaugeMini from "./RingGaugeMini";

export interface FairnessGroupImpact {
  group_name: string;
  net_subsidy: number;          // positive = group overpays vs benefit, negative = underpays
}

export interface FairnessDriver {
  name: string;
  amount: number;               // annual subsidy distortion driven by this asset/cost-centre
  share_pct?: number;           // 0..1 — share of total distortion
}

interface LevyFairnessCardProps {
  score: number | null;
  grade?: string;
  groupImpacts?: FairnessGroupImpact[];
  drivers?: FairnessDriver[];
  onClick?: () => void;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: defaultGrade
 * Path: frontend/src/components/dashboard/LevyFairnessCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function defaultGrade(score: number | null): string {
  if (score == null) return "Pending";
  if (score >= 88) return "Good";
  if (score >= 75) return "Watch";
  return "Review";
}
/**
 * @generated FunctionHeader
 * Function: LevyFairnessCard
 * Path: frontend/src/components/dashboard/LevyFairnessCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LevyFairnessCard({
  score,
  grade,
  groupImpacts = [],
  drivers = [],
  onClick,
  className = "",
}: LevyFairnessCardProps) {
  const resolvedGrade = grade ?? defaultGrade(score);
  const overpay = groupImpacts.find((g) => Number(g.net_subsidy) > 0);
  const underpay = groupImpacts.find((g) => Number(g.net_subsidy) < 0);
  const topDrivers = (drivers || []).slice(0, 3);
  const driverMax = topDrivers.length ? Math.max(...topDrivers.map((d) => d.amount || 0)) : 0;

  return (
    <section
      className={`rounded-3xl bg-card ring-1 ring-ring/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="levy-fairness-card"
      role="region"
      aria-label="Levy fairness index"
    >
      <header className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">Levy Fairness Index</div>
          <h3 className="text-lg font-semibold text-foreground">Who is subsidising whom?</h3>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${
            resolvedGrade === "Good"
              ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
              : resolvedGrade === "Watch"
                ? "bg-amber-50 text-amber-800 ring-amber-200"
                : resolvedGrade === "Review"
                  ? "bg-rose-50 text-rose-700 ring-rose-200"
                  : "bg-muted text-foreground ring-ring"
          }`}
        >
          {resolvedGrade}
        </span>
      </header>

      <div className="flex items-center gap-5">
        <RingGaugeMini
          value={score ?? 0}
          size={104}
          stroke={10}
          color={resolvedGrade === "Good" ? "#16A34A" : resolvedGrade === "Watch" ? "#F59E0B" : "#E11D48"}
          label={score != null ? Math.round(score).toString() : "—"}
          sub="LBFI"
          ariaLabel={`Levy fairness score ${score ?? "unavailable"} out of 100`}
        />
        <div className="flex-1 space-y-2">
          {overpay && (
            <div className="rounded-xl ring-1 ring-rose-200 bg-rose-50 p-2.5">
              <div className="text-[10px] font-bold uppercase tracking-widest text-rose-700">
                {overpay.group_name} · overpay
              </div>
              <div className="text-lg font-semibold text-rose-700">
                +{formatCurrency(Math.abs(overpay.net_subsidy))}/yr
              </div>
            </div>
          )}
          {underpay && (
            <div className="rounded-xl ring-1 ring-emerald-200 bg-emerald-50 p-2.5">
              <div className="text-[10px] font-bold uppercase tracking-widest text-emerald-700">
                {underpay.group_name} · underpay
              </div>
              <div className="text-lg font-semibold text-emerald-700">
                −{formatCurrency(Math.abs(underpay.net_subsidy))}/yr
              </div>
            </div>
          )}
          {!overpay && !underpay && (
            <div className="rounded-xl ring-1 ring-ring bg-muted p-2.5 text-xs font-semibold text-muted-foreground">
              No group-level subsidy detected.
            </div>
          )}
        </div>
      </div>

      {topDrivers.length > 0 && (
        <div className="mt-4 pt-4 border-t border-border">
          <div className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-2">Top subsidy drivers</div>
          <ul className="space-y-1.5" role="list">
            {topDrivers.map((d, i) => {
              const width = driverMax > 0 ? Math.max(2, (d.amount / driverMax) * 100) : 0;
              return (
                <li key={`${d.name}-${i}`} className="flex items-center gap-3">
                  <div className="w-32 text-sm font-bold text-foreground truncate">{d.name}</div>
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden" aria-hidden="true">
                    <div className="h-full bg-rose-500 rounded-full" style={{ width: `${width}%` }} />
                  </div>
                  <div className="text-xs font-semibold text-rose-600 w-20 text-right">
                    {formatCurrency(d.amount)}/yr
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {onClick && (
        <div className="mt-3 flex items-center justify-end text-xs">
          <button
            type="button"
            onClick={onClick}
            className="font-bold text-primary inline-flex items-center gap-1 rounded hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Open levy fairness model"
          >
            Open fairness model <ArrowRight size={12} />
          </button>
        </div>
      )}
    </section>
  );
}
