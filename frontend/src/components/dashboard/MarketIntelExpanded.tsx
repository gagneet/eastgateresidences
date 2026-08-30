// @featuretrace:dashboard-v2 — Owner market intelligence: trend + suburb median + comps.
// Layer: frontend
// Data flow: OwnerDashboard → benchmarks + dashboard_v2_extras.market_signals → MarketIntelExpanded (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
//           backend/routers/analytics.py (/analytics/dashboard-v2-extras)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowRight, ArrowDownRight, ArrowUpRight } from "lucide-react";
import SparklineMini from "./SparklineMini";

export interface MarketSignals {
  estimate?: number;
  suburb_median?: number;
  yoy_pct?: number;
  rental_yield_pct?: number;
  days_on_market?: number;
  building_premium_pct?: number;
  trend_years?: string[];
  trend_values?: number[];
  comparable_sales?: Array<{
    unit: string;
    sold_date?: string;
    price: number;
    bedrooms?: number;
  }>;
}

interface MarketIntelExpandedProps {
  signals?: MarketSignals;
  onRefine?: () => void;
  onSignalSelect?: (payload?: any) => void;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: fmt$k
 * Path: frontend/src/components/dashboard/MarketIntelExpanded.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function fmt$k(n?: number) {
  if (n == null || !Number.isFinite(n)) return "—";
  return "$" + (n / 1000).toFixed(n >= 100_000 ? 0 : 1) + "k";
}
/**
 * @generated FunctionHeader
 * Function: formatShortDate
 * Path: frontend/src/components/dashboard/MarketIntelExpanded.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatShortDate(iso?: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
}
/**
 * @generated FunctionHeader
 * Function: MarketIntelExpanded
 * Path: frontend/src/components/dashboard/MarketIntelExpanded.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MarketIntelExpanded({
  signals,
  onRefine,
  onSignalSelect,
  className = "",
}: MarketIntelExpandedProps) {
  const s = signals || {};
  const estimate = s.estimate ?? 0;
  const yoy = s.yoy_pct ?? 0;
  const median = s.suburb_median ?? 0;
  const aboveMedianPct =
    median > 0 && estimate > 0 ? Math.round(((estimate - median) / median) * 100) : null;
  const trend = (s.trend_values && s.trend_values.length > 0)
    ? s.trend_values
    : [];
  const years = s.trend_years ?? [];
  const comps = s.comparable_sales ?? [];

  const hasAny =
    estimate > 0 || median > 0 || comps.length > 0 || trend.length > 0;

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="market-intel-expanded"
      role="region"
      aria-label="Your unit market intelligence"
    >
      <header className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Your unit · market intelligence
          </div>
          <h3 className="text-lg font-black text-slate-900">
            {estimate > 0 ? (
              <>Estimated {fmt$k(estimate)}{yoy ? ` · ${yoy > 0 ? "+" : ""}${yoy}% YoY` : ""}</>
            ) : (
              "Estimate not yet available"
            )}
          </h3>
        </div>
        {onRefine && (
          <button
            type="button"
            onClick={onRefine}
            className="text-xs font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
            aria-label="Refine my estimate"
          >
            Refine my estimate <ArrowRight size={12} />
          </button>
        )}
      </header>

      {!hasAny ? (
        <div className="py-6 text-center text-sm font-semibold text-slate-400">
          Market intelligence will populate once benchmark and comparable-sales feeds are connected for this building.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <button type="button" onClick={() => onSignalSelect?.()} className="text-left rounded-2xl ring-1 ring-slate-200 p-4 hover:ring-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-400">
              <div className="flex items-end justify-between mb-2">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
                    Price trend · {trend.length}y
                  </div>
                  <div className="text-2xl font-black text-slate-900">{fmt$k(estimate)}</div>
                </div>
                <span
                  className={`inline-flex items-center gap-0.5 text-xs font-bold ${
                    yoy >= 0 ? "text-emerald-600" : "text-rose-600"
                  }`}
                  aria-label={`Year over year: ${yoy > 0 ? "+" : ""}${yoy}%`}
                >
                  {yoy >= 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                  {yoy > 0 ? "+" : ""}
                  {yoy}%
                </span>
              </div>
              <div className="h-20">
                <SparklineMini
                  points={trend}
                  color="#4F46E5"
                  height={80}
                  ariaLabel="Six-year price trend"
                />
              </div>
              {years.length > 0 && (
                <div className="flex justify-between text-[9px] font-bold text-slate-400 mt-1">
                  {years.map((y) => (
                    <span key={y}>'{y.slice(-2)}</span>
                  ))}
                </div>
              )}
            </button>
            <div className="grid grid-cols-2 gap-2">
              <Stat
                label="Suburb median"
                value={fmt$k(median)}
                sub={
                  aboveMedianPct == null
                    ? "—"
                    : aboveMedianPct >= 0
                      ? `+${aboveMedianPct}% above median`
                      : `${aboveMedianPct}% below median`
                }
                subClass={aboveMedianPct != null && aboveMedianPct >= 0 ? "text-emerald-600" : "text-rose-600"}
              />
              <Stat
                label="Rental yield"
                value={s.rental_yield_pct != null ? `${s.rental_yield_pct}%` : "—"}
                sub="Estimated gross"
              />
              <Stat
                label="Days on market"
                value={s.days_on_market != null ? `${s.days_on_market}d` : "—"}
                sub="Suburb average"
              />
              <Stat
                label="Building premium"
                value={s.building_premium_pct != null ? `${s.building_premium_pct > 0 ? '+' : ''}${s.building_premium_pct}%` : "—"}
                sub="vs similar buildings"
              />
            </div>
          </div>

          {comps.length > 0 && (
            <div className="mt-5 pt-4 border-t border-slate-100">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
                Recent comparable sales · 90 days
              </div>
              <ul className="grid grid-cols-1 md:grid-cols-3 gap-3" role="list">
                {comps.slice(0, 3).map((c, i) => (
                  <li key={`${c.unit}-${i}`}>
                    <button
                      type="button"
                      onClick={() => onSignalSelect?.(c)}
                      className="w-full text-left flex items-center gap-3 rounded-xl ring-1 ring-slate-100 p-3 hover:ring-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                      aria-label={`Open comparable sale ${c.unit}`}
                    >
                    <div className="w-9 h-9 rounded-lg bg-slate-100 text-slate-500 font-black grid place-items-center text-xs">
                      {c.bedrooms ? `${c.bedrooms}br` : "—"}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-bold text-slate-900 text-sm truncate">{c.unit}</div>
                      <div className="text-[11px] text-slate-400">Sold {formatShortDate(c.sold_date)}</div>
                    </div>
                    <div className="text-sm font-black text-slate-900">{fmt$k(c.price)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
/**
 * @generated FunctionHeader
 * Function: Stat
 * Path: frontend/src/components/dashboard/MarketIntelExpanded.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Stat({
  label,
  value,
  sub,
  subClass = "text-slate-500",
}: {
  label: string;
  value: string;
  sub?: string;
  subClass?: string;
}) {
  return (
    <div className="rounded-2xl ring-1 ring-slate-200 p-4">
      <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{label}</div>
      <div className="text-xl font-black text-slate-900 mt-1">{value}</div>
      {sub && <div className={`text-[11px] font-bold mt-0.5 ${subClass}`}>{sub}</div>}
    </div>
  );
}
