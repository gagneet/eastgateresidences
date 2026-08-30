// @featuretrace:dashboard-v2 — Management vendor scorecard with on-time %, spend, and volume trend.
// Layer: frontend
// Data flow: ManagementDashboard → /analytics/maintenance/spend-trend → VendorScorecardCard (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowRight } from "lucide-react";
import { formatCurrency } from "../../lib/utils";
import SparklineMini from "./SparklineMini";

export interface VendorRow {
  name: string;
  jobs: number;
  on_time_pct?: number | null;
  spend: number;
  trend?: number[];
}

interface VendorScorecardCardProps {
  vendors: VendorRow[];
  onSelect?: (vendor: VendorRow) => void;
  onAll?: () => void;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: vendorInitials
 * Path: frontend/src/components/dashboard/VendorScorecardCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function vendorInitials(name: string) {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
/**
 * @generated FunctionHeader
 * Function: VendorScorecardCard
 * Path: frontend/src/components/dashboard/VendorScorecardCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function VendorScorecardCard({
  vendors,
  onSelect,
  onAll,
  className = "",
}: VendorScorecardCardProps) {
  const safe = (vendors || []).slice(0, 5);

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="vendor-scorecard-card"
      role="region"
      aria-label="Vendor performance scorecard"
    >
      <header className="flex items-end justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Vendor performance · 12mo
          </div>
          <h3 className="text-lg font-black text-slate-900">Who delivered, who slipped</h3>
        </div>
        {onAll && (
          <button
            type="button"
            onClick={onAll}
            className="text-xs font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
            aria-label="View all vendors"
          >
            All vendors <ArrowRight size={12} />
          </button>
        )}
      </header>

      {safe.length === 0 ? (
        <div className="py-8 text-center text-sm font-semibold text-slate-400">No vendor spend data available.</div>
      ) : (
        <div>
          <div className="grid grid-cols-12 text-[10px] font-black uppercase tracking-widest text-slate-400 pb-2 border-b border-slate-100">
            <div className="col-span-4">Vendor</div>
            <div className="col-span-1 text-right">Jobs</div>
            <div className="col-span-2 text-right">On-time</div>
            <div className="col-span-2 text-right">Spend 12mo</div>
            <div className="col-span-3 text-right">Volume trend</div>
          </div>
          <ul role="list">
            {safe.map((v, i) => {
              const onTime = v.on_time_pct;
              const onTimeColor =
                onTime == null
                  ? "text-slate-400"
                  : onTime >= 90
                    ? "text-emerald-600"
                    : onTime >= 80
                      ? "text-amber-600"
                      : "text-rose-600";
              return (
                <li key={`${v.name}-${i}`}>
                  <button
                    type="button"
                    onClick={() => onSelect?.(v)}
                    className="grid grid-cols-12 items-center py-2.5 border-b border-slate-50 hover:bg-slate-50/60 text-left w-full focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-1"
                    aria-label={`Open ${v.name} details`}
                  >
                    <div className="col-span-4 flex items-center gap-2 min-w-0">
                      <div
                        className="w-8 h-8 rounded-lg bg-slate-100 grid place-items-center text-[10px] font-black text-slate-500 flex-shrink-0"
                        aria-hidden="true"
                      >
                        {vendorInitials(v.name)}
                      </div>
                      <div className="font-bold text-sm text-slate-900 truncate">{v.name}</div>
                    </div>
                    <div className="col-span-1 text-right text-sm font-bold text-slate-900">{v.jobs}</div>
                    <div className="col-span-2 text-right">
                      <span className={`text-sm font-black ${onTimeColor}`}>
                        {onTime == null ? "—" : `${onTime}%`}
                      </span>
                    </div>
                    <div className="col-span-2 text-right text-sm font-bold text-slate-900">
                      {formatCurrency(v.spend)}
                    </div>
                    <div className="col-span-3 pl-6" aria-hidden="true">
                      {v.trend && v.trend.length > 0 ? (
                        <SparklineMini points={v.trend} color="#4F46E5" height={28} />
                      ) : (
                        <div className="h-7" />
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
