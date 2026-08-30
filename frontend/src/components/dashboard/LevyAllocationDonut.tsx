// @featuretrace:dashboard-v2 — Levy Allocation Donut: shows where the owner's levy goes by category.
// Layer: frontend
// Data flow: OwnerDashboard → /analytics/levy-allocation-breakdown (category split) + owner_overview totals → LevyAllocationDonut (building-scoped).
// Related: frontend/src/pages/dashboard/OwnerDashboard.tsx
//           backend/routers/analytics.py  (GET /analytics/levy-allocation-breakdown)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { formatCurrency } from "../../lib/utils";

export interface AllocationCategory {
  name: string;
  pct: number;
  amount?: number;
  color?: string;
}

interface LevyAllocationDonutProps {
  categories?: AllocationCategory[];
  totalAnnual?: number;
  year?: string;
  className?: string;
  onCategorySelect?: (category: AllocationCategory) => void;
  onOpenDetails?: () => void;
}

const FALLBACK_COLORS = [
  "#4F46E5", "#16A34A", "#F59E0B", "#0EA5E9",
  "#7C3AED", "#E11D48", "#64748B",
];
/** Pure SVG donut — no Recharts so it works server-side and inside dark cards */
function Donut({ data, size = 180, stroke = 28, onCategorySelect }: { data: AllocationCategory[]; size?: number; stroke?: number; onCategorySelect?: (category: AllocationCategory) => void }) {
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;

  const segments: Array<{ category: AllocationCategory; length: number; rotation: number }> = [];
  data.reduce((accumulated, category) => {
    const length = (category.pct / 100) * circumference;
    const rotation = (accumulated / 100) * 360 - 90;
    segments.push({ category, length, rotation });
    return accumulated + category.pct;
  }, 0);

  return (
    <svg width={size} height={size} role="img" aria-label="Levy allocation donut chart">
      {/* Track */}
      <circle
        cx={size / 2} cy={size / 2} r={r}
        stroke="rgba(15,23,42,.06)" strokeWidth={stroke} fill="none"
      />
      {segments.map(({ category, length, rotation }, i) => (
        <circle
          key={i}
          cx={size / 2} cy={size / 2} r={r}
          stroke={category.color ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]}
          strokeWidth={stroke}
          fill="none"
          strokeDasharray={`${length} ${circumference - length}`}
          strokeDashoffset="0"
          transform={`rotate(${rotation} ${size / 2} ${size / 2})`}
          aria-label={`${category.name}: ${category.pct}%`}
          role={onCategorySelect ? "button" : undefined}
          tabIndex={onCategorySelect ? 0 : undefined}
          className={onCategorySelect ? "cursor-pointer focus:outline-none" : undefined}
          onClick={() => onCategorySelect?.(category)}
          onKeyDown={(e) => {
            if (!onCategorySelect) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onCategorySelect(category);
            }
          }}
        />
      ))}
    </svg>
  );
}
/**
 * @generated FunctionHeader
 * Function: LevyAllocationDonut
 * Path: frontend/src/components/dashboard/LevyAllocationDonut.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LevyAllocationDonut({
  categories = [],
  totalAnnual = 0,
  year,
  className = "",
  onCategorySelect,
  onOpenDetails,
}: LevyAllocationDonutProps) {
  const quarterlyAmount = totalAnnual ? totalAnnual / 4 : 0;
  const displayYear = year ?? new Date().getFullYear().toString();

  // Ensure all categories have colors
  const enriched = categories.map((c, i) => ({
    ...c,
    color: c.color ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length],
  }));

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="levy-allocation-donut"
      role={onOpenDetails ? "button" : "region"}
      tabIndex={onOpenDetails ? 0 : undefined}
      onClick={onOpenDetails}
      onKeyDown={(e) => {
        if (!onOpenDetails) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpenDetails();
        }
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Where your levy goes
          </div>
          <h3 className="text-lg font-black text-slate-900">
            Annual share{totalAnnual ? ` of ${formatCurrency(totalAnnual)}` : ""}
          </h3>
        </div>
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 bg-slate-100 text-slate-700 ring-slate-200">
          {displayYear}
        </span>
      </div>

      {enriched.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-sm text-slate-400 font-semibold">
          Levy allocation data unavailable
        </div>
      ) : (
        <div className="flex items-center gap-6">
          {/* Donut */}
          <div className="relative w-[180px] h-[180px] flex-shrink-0">
            <Donut data={enriched} size={180} stroke={28} onCategorySelect={onCategorySelect} />
            {/* Centre label */}
            <div className="absolute inset-0 grid place-items-center pointer-events-none">
              <div className="text-center">
                <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Per quarter</div>
                <div className="text-xl font-black text-slate-900 mt-0.5"
                     style={{ fontFeatureSettings: '"tnum" 1' }}>
                  {formatCurrency(quarterlyAmount)}
                </div>
              </div>
            </div>
          </div>

          {/* Legend */}
          <div className="flex-1 space-y-1.5 min-w-0">
            {enriched.map((a, i) => {
              const legendContent = (
                <>
                  <span
                    className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                    style={{ background: a.color }}
                    aria-hidden="true"
                  />
                  <span className="font-bold text-slate-700 flex-1 truncate">{a.name}</span>
                  <span className="font-black text-slate-900 w-10 text-right"
                        style={{ fontFeatureSettings: '"tnum" 1' }}>
                    {a.pct}%
                  </span>
                  {(a.amount != null) && (
                    <span className="font-semibold text-slate-400 w-14 text-right"
                          style={{ fontFeatureSettings: '"tnum" 1' }}>
                      {formatCurrency(a.amount)}
                    </span>
                  )}
                </>
              );

              if (!onCategorySelect) {
                return (
                  <div key={i} className="flex items-center gap-2 text-xs w-full text-left rounded-lg">
                    {legendContent}
                  </div>
                );
              }

              return (
                <button
                  key={i}
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCategorySelect(a);
                  }}
                  className="flex items-center gap-2 text-xs w-full text-left rounded-lg hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  aria-label={`Open ${a.name} levy allocation details`}
                >
                  {legendContent}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
