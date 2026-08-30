// @featuretrace:dashboard-v2 — Capital For Me: owner's dollar share of upcoming capital works from the 10yr plan.
// Layer: frontend
// Data flow: OwnerDashboard → /intelligence/capital-shock × unit entitlement → CapitalForMeCard (building-scoped).
// Related: frontend/src/pages/dashboard/OwnerDashboard.tsx
//           backend/routers/intelligence.py  (/intelligence/capital-shock)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { formatCurrency } from "../../lib/utils";
import { CheckCircle2 } from "lucide-react";

export interface CapitalEvent {
  year?: number;
  description?: string;
  estimated_cost?: number;
  severity?: string;       // "severe" | "major" | "moderate" | "minor"
  // computed client-side
  myShare?: number;
}

interface CapitalForMeCardProps {
  shockRows?: CapitalEvent[];
  entitlementPct?: number;    // e.g. 1.34 (percent of total UOE)
  className?: string;
  onEventSelect?: (event: CapitalEvent) => void;
  onOpenPlan?: () => void;
}

const SEVERITY_YEAR_TONE: Record<string, string> = {
  severe:   "bg-rose-50 text-rose-700",
  major:    "bg-orange-50 text-orange-700",
  moderate: "bg-amber-50 text-amber-700",
  minor:    "bg-emerald-50 text-emerald-700",
  // Postgres intelligence.py path uses "high"/"medium"/"low"
  high:     "bg-rose-50 text-rose-700",
  medium:   "bg-amber-50 text-amber-700",
  low:      "bg-emerald-50 text-emerald-700",
  normal:   "bg-emerald-50 text-emerald-700",
};
// MongoDB path (capital_shock_service.py CSI_TIERS) uses "major" and "severe".
// Postgres path (intelligence.py) uses "high". Both indicate a shock that may need
// a special levy — treat them identically in the UI.
/**
 * @generated FunctionHeader
 * Function: isHighSeverity
 * Path: frontend/src/components/dashboard/CapitalForMeCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function isHighSeverity(s?: string): boolean {
  return s === "severe" || s === "major" || s === "high";
}
/**
 * @generated FunctionHeader
 * Function: defaultTone
 * Path: frontend/src/components/dashboard/CapitalForMeCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function defaultTone(year: number): string {
  const now = new Date().getFullYear();
  if (year - now <= 3) return "bg-rose-50 text-rose-700";
  if (year - now <= 6) return "bg-amber-50 text-amber-700";
  return "bg-emerald-50 text-emerald-700";
}
/**
 * @generated FunctionHeader
 * Function: CapitalForMeCard
 * Path: frontend/src/components/dashboard/CapitalForMeCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CapitalForMeCard({
  shockRows = [],
  entitlementPct = 0,
  className = "",
  onEventSelect,
  onOpenPlan,
}: CapitalForMeCardProps) {
  // Compute owner share; filter to future events only, cap at 5
  const now = new Date().getFullYear();
  const upcoming = shockRows
    .filter((r) => (r.year ?? 0) >= now)
    .slice(0, 5)
    .map((r) => ({
      ...r,
      myShare: (r.estimated_cost && entitlementPct > 0)
        ? Math.round(r.estimated_cost * (entitlementPct / 100))
        : 0,
    }));

  // "good news" = all upcoming fit within sinking fund (no special levy flagged).
  // Uses isHighSeverity() to cover both MongoDB ("severe"/"major") and Postgres ("high") paths.
  const allCovered = upcoming.every((r) => !isHighSeverity(r.severity));

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="capital-for-me-card"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Capital works ahead · your share
          </div>
          <h3 className="text-lg font-black text-slate-900">What's coming, what it'll cost you</h3>
        </div>
      </div>

      {entitlementPct > 0 ? (
        <p className="text-[11px] text-slate-500 mb-4 leading-relaxed">
          Based on the 10-year plan and your unit entitlement of{" "}
          <strong className="text-slate-700">{entitlementPct.toFixed(2)}%</strong>. These figures appear in
          your levy notices when each project funds.
        </p>
      ) : (
        <p className="text-[11px] text-slate-400 mb-4 leading-relaxed italic">
          Entitlement percentage unavailable — connect your unit record to see your share of each project.
        </p>
      )}

      {upcoming.length === 0 ? (
        <div className="rounded-2xl bg-emerald-50 ring-1 ring-emerald-200 p-4 text-sm text-emerald-800 font-semibold text-center">
          No capital works scheduled — reserve plan is fully funded
        </div>
      ) : (
        <div className="space-y-2.5">
          {upcoming.map((c, i) => {
            const yearTone = c.severity
              ? SEVERITY_YEAR_TONE[c.severity] ?? defaultTone(c.year ?? now)
              : defaultTone(c.year ?? now);

            const rowContent = (
              <>
                {/* Year badge */}
                <div className={`text-center px-3 py-1.5 rounded-lg flex-shrink-0 ${yearTone}`}>
                  <div className="text-[9px] font-black uppercase tracking-widest">FY</div>
                  <div className="text-base font-black" style={{ fontFeatureSettings: '"tnum" 1' }}>
                    {c.year}
                  </div>
                </div>

                {/* Description */}
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-bold text-slate-900 truncate">
                    {c.description ?? "Capital works"}
                  </div>
                  <div className={`text-[11px] mt-0.5 ${
                    isHighSeverity(c.severity)
                      ? "text-amber-700 font-semibold"
                      : "text-slate-500"
                  }`}>
                    {isHighSeverity(c.severity)
                      ? "May require special levy — review with committee"
                      : "Sinking fund covers · no special levy expected"}
                  </div>
                </div>

                {/* Owner's share */}
                <div className="text-right flex-shrink-0">
                  <div className="text-base font-black text-slate-900"
                       style={{ fontFeatureSettings: '"tnum" 1' }}>
                    ~{c.myShare ? formatCurrency(c.myShare) : "—"}
                  </div>
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
                    your share
                  </div>
                </div>
              </>
            );

            if (!onEventSelect) {
              return (
                <div key={i} className="w-full flex items-center gap-3 rounded-2xl ring-1 ring-slate-200 p-3.5 text-left">
                  {rowContent}
                </div>
              );
            }

            return (
              <button
                key={i}
                type="button"
                onClick={() => onEventSelect(c)}
                className="w-full flex items-center gap-3 rounded-2xl ring-1 ring-slate-200 p-3.5 text-left hover:ring-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                aria-label={`Open capital works detail for FY ${c.year}`}
              >
                {rowContent}
              </button>
            );
          })}
        </div>
      )}

      {/* Good news / warning banner */}
      {upcoming.length > 0 && (() => {
        const bannerClassName = `mt-4 w-full text-left rounded-2xl ring-1 p-3 text-xs ${
          allCovered
            ? "bg-emerald-50 ring-emerald-200 text-emerald-800"
            : "bg-amber-50 ring-amber-200 text-amber-800"
        }`;
        const bannerContent = (
          <>
            <div className="font-black uppercase tracking-widest text-[10px] mb-0.5 flex items-center gap-1">
              {allCovered ? <CheckCircle2 size={12} aria-hidden="true" /> : "!"}
              {allCovered ? "Good news" : "Heads up"}
            </div>
            {allCovered
              ? "All upcoming projects fit within current sinking-fund forecasts. No special-levy events expected."
              : "One or more major capital events may require additional contributions. Review the 10-year plan."}
          </>
        );

        if (!onOpenPlan) {
          return <div className={bannerClassName}>{bannerContent}</div>;
        }

        return (
          <button type="button" onClick={onOpenPlan} className={bannerClassName}>
            {bannerContent}
          </button>
        );
      })()}
    </div>
  );
}
