// @featuretrace:dashboard-v2 — Owner "True cost of ownership" 12-month breakdown card.
// Layer: frontend
// Data flow: OwnerDashboard → owner_overview.cost_breakdown (seeded) → TrueCostBreakdown (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
//           backend/seeds/demo_customer.py (cost_breakdown signal)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { formatCurrency } from "../../lib/utils";

export interface CostCategory {
  name: string;
  annual: number;
  color?: string;
}

interface TrueCostBreakdownProps {
  categories: CostCategory[];
  yoyDelta?: number | null;
  className?: string;
  onCategorySelect?: (category: CostCategory) => void;
}

const FALLBACK_COLORS = [
  "#4F46E5",
  "#0EA5E9",
  "#16A34A",
  "#7C3AED",
  "#E11D48",
  "#F59E0B",
  "#64748B",
];
/**
 * @generated FunctionHeader
 * Function: TrueCostBreakdown
 * Path: frontend/src/components/dashboard/TrueCostBreakdown.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TrueCostBreakdown({
  categories,
  yoyDelta = null,
  className = "",
  onCategorySelect,
}: TrueCostBreakdownProps) {
  const enriched = (categories || [])
    .filter((c) => Number.isFinite(Number(c.annual)))
    .map((c, i) => ({ ...c, annual: Number(c.annual) || 0, color: c.color || FALLBACK_COLORS[i % FALLBACK_COLORS.length] }));
  const total = enriched.reduce((s, c) => s + (Number(c.annual) || 0), 0);

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="true-cost-breakdown"
      role="region"
      aria-label="True cost of ownership"
    >
      <header className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            True cost of ownership · 12 mo
          </div>
          <h3 className="text-lg font-black text-slate-900">
            {formatCurrency(total)} all-in · everything you pay to hold this unit
          </h3>
        </div>
        {yoyDelta != null && (
          <div className="text-right">
            <span
              className={`inline-flex items-center gap-0.5 text-xs font-bold ${
                yoyDelta < 0 ? "text-emerald-600" : yoyDelta > 0 ? "text-rose-600" : "text-slate-400"
              }`}
              aria-label={`Year over year change: ${yoyDelta > 0 ? "+" : ""}${yoyDelta}%`}
            >
              {yoyDelta < 0 ? <ArrowDownRight size={12} /> : yoyDelta > 0 ? <ArrowUpRight size={12} /> : null}
              {yoyDelta > 0 ? "+" : ""}
              {yoyDelta}%
            </span>
            <div className="text-[10px] text-slate-400 font-bold uppercase tracking-widest mt-0.5">vs last year</div>
          </div>
        )}
      </header>

      {enriched.length === 0 ? (
        <div className="py-6 text-center text-sm font-semibold text-slate-400">
          No cost categories yet. Once your council, water, insurance and land tax records are connected, your full
          annual ownership cost will appear here.
        </div>
      ) : (
        <>
          <div
            className="rounded-xl overflow-hidden h-10 flex ring-1 ring-slate-200"
            role="img"
            aria-label="Cost composition stacked bar"
          >
            {enriched.map((c, i) => {
              const pct = total > 0 ? (c.annual / total) * 100 : 0;
              return (
                <button
                  key={`${c.name}-${i}`}
                  type="button"
                  onClick={() => onCategorySelect?.(c)}
                  className="h-full flex items-center justify-center text-[10px] font-black text-white px-2 focus:outline-none focus:ring-2 focus:ring-white/70"
                  style={{
                    flex: c.annual,
                    background: c.color,
                    // Drop shadow lifts the white text on lighter slice colors
                    // (e.g. amber-500, sky-500) where contrast would otherwise fail.
                    textShadow: "0 1px 1px rgba(0,0,0,0.45)",
                  }}
                  title={`${c.name}: ${pct.toFixed(0)}%`}
                >
                  {pct >= 6 ? `${pct.toFixed(0)}%` : ""}
                </button>
              );
            })}
          </div>
          <ul className="mt-4 grid grid-cols-2 lg:grid-cols-3 gap-3" role="list" aria-label="Cost categories">
            {enriched.map((c, i) => (
              <li key={`${c.name}-${i}`}>
                <button
                  type="button"
                  onClick={() => onCategorySelect?.(c)}
                  className="w-full text-left rounded-xl ring-1 ring-slate-200 p-3 hover:ring-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  aria-label={`Open ${c.name} ownership cost details`}
                >
                <div className="flex items-center gap-2">
                  <span
                    className="w-2 h-2 rounded-sm flex-shrink-0"
                    style={{ background: c.color }}
                    aria-hidden="true"
                  />
                  <div className="text-[11px] font-bold text-slate-500 uppercase tracking-widest truncate">{c.name}</div>
                </div>
                <div className="text-lg font-black text-slate-900 mt-1">{formatCurrency(c.annual)}</div>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
