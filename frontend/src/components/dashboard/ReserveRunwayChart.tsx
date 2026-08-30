// @featuretrace:dashboard-v2 — Reserve Runway: 10-year sinking fund projection with capital-shock markers.
// Layer: frontend
// Data flow: ManagerDashboard / OwnerDashboard → /analytics/sinking-fund-forecast?years=10
//            + /intelligence/capital-shock → ReserveRunwayChart (building-scoped).
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           backend/routers/analytics.py
//           backend/routers/intelligence.py
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { formatCurrency } from "../../lib/utils";
import { ArrowRight } from "lucide-react";
import Link from "next/link";

// The row shape is owned by the normaliser that produces it, not redeclared here — a
// second copy is how `capital_works` drifted out of sync with the API in the first place.
// Re-exported so existing importers of `ProjectionRow` keep working.
// @featuretrace:finance-reserve-forecast — Reserve runway chart (row type + rendering).
// Layer: frontend
// Data flow: normaliseReserveProjection() -> ReserveRunwayChart (building-scoped).
// Related: frontend/src/lib/reserve-projection.ts
//          frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
//
// LESSON (2026-08-27): this file used to declare its OWN ProjectionRow interface, which is
// how the row shape drifted from the payload unnoticed. One declaration, owned by the
// normaliser that produces the rows.
export type { ReserveProjectionRow as ProjectionRow } from "../../lib/reserve-projection";
import type { ReserveProjectionRow as ProjectionRow } from "../../lib/reserve-projection";

export interface ShockRow {
  year?: number;
  description?: string;
  estimated_cost?: number;
  severity?: string;
}

interface ReserveRunwayChartProps {
  projection?: ProjectionRow[];
  shocks?: ShockRow[];
  className?: string;
  onSelectYear?: (row: ProjectionRow) => void;
}
/**
 * @generated FunctionHeader
 * Function: fmt$k
 * Path: frontend/src/components/dashboard/ReserveRunwayChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt$k = (n?: number) => n == null ? "—" : `$${(n / 1000).toFixed(0)}k`;
/**
 * @generated FunctionHeader
 * Function: mergeShocks
 * Path: frontend/src/components/dashboard/ReserveRunwayChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function mergeShocks(projection: ProjectionRow[], shocks: ShockRow[]): ProjectionRow[] {
  if (!shocks || shocks.length === 0) return projection;
  return projection.map((row) => {
    const yr = typeof row.year === "string" ? parseInt(row.year) : row.year;
    const shock = shocks.find((s) => s.year === yr);
    if (shock) {
      return {
        ...row,
        shock_label: shock.description
          ? `${shock.description.slice(0, 20)} ${fmt$k(shock.estimated_cost)}`
          : fmt$k(shock.estimated_cost),
      };
    }
    return row;
  });
}
/**
 * @generated FunctionHeader
 * Function: CustomTooltip
 * Path: frontend/src/components/dashboard/ReserveRunwayChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  const balance = payload.find((p: any) => p.dataKey === "closing_balance");
  const contrib = payload.find((p: any) => p.dataKey === "contributions");
  return (
    <div className="rounded-xl bg-white shadow-lg ring-1 ring-slate-200 p-3 text-xs">
      <div className="font-black text-slate-900 mb-1">FY{label}</div>
      {balance && <div className="text-indigo-600">Balance: {formatCurrency(balance.value)}</div>}
      {contrib && <div className="text-emerald-600">Contributions: {formatCurrency(contrib.value)}</div>}
    </div>
  );
};
/**
 * @generated FunctionHeader
 * Function: ReserveRunwayChart
 * Path: frontend/src/components/dashboard/ReserveRunwayChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ReserveRunwayChart({ projection = [], shocks = [], className = "", onSelectYear }: ReserveRunwayChartProps) {
  const merged = mergeShocks(projection, shocks);

  // Summary stats
  const today = merged[0];
  const lowest = merged.reduce((min, r) =>
    (r.closing_balance ?? Infinity) < (min.closing_balance ?? Infinity) ? r : min, merged[0] ?? {});
  // Average only over years whose contribution is actually KNOWN. Treating an unknown
  // contribution as 0 inside the sum drags the mean toward zero and reports a confident
  // figure derived partly from absent data. When nothing is known this stays null, which
  // the tile below renders as "—".
  const knownContribs = merged
    .map((r) => r.contributions)
    .filter((v): v is number => typeof v === "number");
  const avgContrib = knownContribs.length
    ? Math.round(knownContribs.reduce((s, v) => s + v, 0) / knownContribs.length)
    : null;
  const allPositive = merged.every((r) => (r.closing_balance ?? 0) > 0);

  // Shock reference lines
  const shockYears = merged
    .filter((r) => r.shock_label)
    .map((r) => ({ year: String(r.year), label: r.shock_label! }));
  /**
   * @generated FunctionHeader
   * Function: SummaryTile
   * Path: frontend/src/components/dashboard/ReserveRunwayChart.tsx
   *
   * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
   */
  const SummaryTile = ({ row, children }: { row?: ProjectionRow; children: React.ReactNode }) => {
    if (!onSelectYear || !row) {
      return <div className="text-left rounded-lg">{children}</div>;
    }

    return (
      <button
        type="button"
        onClick={() => onSelectYear(row)}
        className="text-left rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
      >
        {children}
      </button>
    );
  };

  return (
    <div
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6
        shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="reserve-runway-chart"
    >
      {/* Header */}
      <div className="flex items-end justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Sinking Fund Runway · 10y
          </div>
          <h3 className="text-lg font-black text-slate-900">
            Reserve forecast with capital-works shocks
          </h3>
        </div>
        <div className="flex items-center gap-4 text-[11px] font-bold text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-sm bg-indigo-600" />Balance ($)
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-sm bg-emerald-500" />Contributions
          </span>
          {shockYears.length > 0 && (
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-rose-500" />Capital shock
            </span>
          )}
        </div>
      </div>

      {/* Chart */}
      {merged.length > 0 ? (
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={merged}
              margin={{ top: 10, right: 10, left: -10, bottom: 0 }}
              onClick={(state: any) => {
                const row = state?.activePayload?.[0]?.payload as ProjectionRow | undefined;
                if (row) onSelectYear?.(row);
              }}
            >
              <defs>
                <linearGradient id="rrBalance" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4F46E5" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#4F46E5" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="rrContrib" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#16A34A" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#16A34A" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
              <XAxis
                dataKey="year"
                axisLine={false}
                tickLine={false}
                tick={{ fill: "#64748b", fontSize: 11, fontWeight: 600 }}
                dy={8}
                tickFormatter={(v) => `'${String(v).slice(-2)}`}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: "#64748b", fontSize: 11, fontWeight: 600 }}
                tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                width={52}
              />
              <RechartsTooltip content={<CustomTooltip />} cursor={{ stroke: "#e2e8f0", strokeWidth: 2 }} />

              {/* Shock reference lines */}
              {shockYears.map((s) => (
                <ReferenceLine
                  key={s.year}
                  x={s.year}
                  stroke="#E11D48"
                  strokeDasharray="4 2"
                  label={{
                    value: s.label,
                    position: "insideTopRight",
                    fill: "#E11D48",
                    fontSize: 9,
                    fontWeight: 700,
                  }}
                />
              ))}

              <Area
                type="monotone"
                dataKey="contributions"
                name="Contributions"
                stroke="#16A34A"
                fill="url(#rrContrib)"
                strokeWidth={2}
                animationDuration={1200}
              />
              <Area
                type="monotone"
                dataKey="closing_balance"
                name="Fund Balance"
                stroke="#4F46E5"
                fill="url(#rrBalance)"
                strokeWidth={2.5}
                animationDuration={1200}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="h-56 flex items-center justify-center text-sm text-slate-400 font-semibold">
          No forecast data — run the sinking fund plan first
        </div>
      )}

      {/* Summary row */}
      <div className="mt-6 pt-4 border-t border-slate-100 grid grid-cols-4 gap-4 text-xs">
        <SummaryTile row={today}>
          <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400">Today balance</div>
          <div className="font-black text-slate-900 mt-0.5" style={{ fontFeatureSettings: '"tnum" 1' }}>
            {today ? formatCurrency(today.closing_balance ?? 0) : "—"}
          </div>
        </SummaryTile>
        <SummaryTile row={today}>
          <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400">Avg p.a. contrib</div>
          <div className="font-black text-slate-900 mt-0.5" style={{ fontFeatureSettings: '"tnum" 1' }}>
            {avgContrib == null ? "—" : formatCurrency(avgContrib)}
          </div>
        </SummaryTile>
        <SummaryTile row={lowest?.year == null ? undefined : lowest}>
          <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400">
            Lowest dip · {lowest?.year ?? "—"}
          </div>
          {/* Alert on the fund going NEGATIVE (it cannot cover its own scheduled works) —
              not on an invented $100k floor, which meant nothing for a building of any
              particular size and disagreed with the backend's own `status` field. */}
          <div className={`font-black mt-0.5 ${(lowest?.closing_balance ?? 0) < 0 ? "text-rose-600" : "text-slate-900"}`}
               style={{ fontFeatureSettings: '"tnum" 1' }}>
            {lowest ? formatCurrency(lowest.closing_balance ?? 0) : "—"}
          </div>
        </SummaryTile>
        <SummaryTile row={today}>
          <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400">Stress-test</div>
          <div className={`font-black mt-0.5 ${allPositive ? "text-emerald-600" : "text-rose-600"}`}>
            {allPositive ? "Resilient" : "At risk"}
          </div>
        </SummaryTile>
      </div>

      <div className="mt-3 flex justify-end">
        <Link
          href="/intelligence/projections"
          className="text-xs font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
        >
          Full 10-year plan <ArrowRight size={12} />
        </Link>
      </div>
    </div>
  );
}
