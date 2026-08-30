// @featuretrace:dashboard-v2 — Management "Cash position" card: Admin / Sinking / Arrears + Collection.
// Layer: frontend
// Data flow: ManagementDashboard → building_overview + sinking_fund_forecast → CashPositionCard (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { formatCurrency } from "../../lib/utils";
import SparklineMini from "./SparklineMini";

export interface FundSeries {
  balance: number;
  trend?: number[];
  target?: number | null;
}

export interface ArrearsSummary {
  total: number;
  units: number;
  delta_pct?: number | null;
  momentum?: number[];
}

interface CashPositionCardProps {
  admin: FundSeries;
  sinking: FundSeries;
  arrears: ArrearsSummary;
  collectionRatePct: number | null;
  collectionDeltaPct?: number | null;
  collectionTargetPct?: number | null;
  onOpenAdmin?: () => void;
  onOpenSinking?: () => void;
  onOpenArrears?: () => void;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: fmt$k
 * Path: frontend/src/components/dashboard/CashPositionCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function fmt$k(n?: number | null) {
  if (n == null || !Number.isFinite(n)) return "—";
  return "$" + (n / 1000).toFixed(n >= 100_000 ? 0 : 1) + "k";
}
// Badge derives status from arrears, not from collection-rate vs a fixed % threshold.
// levies_paid_pct is a YTD figure: a building at Q2 with 50% is ON TRACK, but comparing
// it to a 95% end-of-year target would wrongly label it "behind". Arrears total is the
// authoritative signal: if it is zero, everything is current; if it is falling, we're
// improving; if it is positive and stable/rising, there are active arrears.
/**
 * @generated FunctionHeader
 * Function: budgetStatusBadge
 * Path: frontend/src/components/dashboard/CashPositionCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function budgetStatusBadge(arrears: ArrearsSummary, rate: number | null) {
  if (rate == null) return { label: "No data", cls: "bg-slate-100 text-slate-500 ring-slate-200" };
  if (arrears.total === 0) return { label: "Fully current", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" };
  if (arrears.delta_pct != null && arrears.delta_pct < 0) return { label: "Improving", cls: "bg-amber-50 text-amber-800 ring-amber-200" };
  if (arrears.total > 0) return { label: "Arrears active", cls: "bg-rose-50 text-rose-700 ring-rose-200" };
  return { label: "Live", cls: "bg-slate-100 text-slate-700 ring-slate-200" };
}
/**
 * @generated FunctionHeader
 * Function: CashPositionCard
 * Path: frontend/src/components/dashboard/CashPositionCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CashPositionCard({
  admin,
  sinking,
  arrears,
  collectionRatePct,
  collectionDeltaPct = null,
  collectionTargetPct = null,
  onOpenAdmin,
  onOpenSinking,
  onOpenArrears,
  className = "",
}: CashPositionCardProps) {
  const collectionText = collectionRatePct != null ? `${collectionRatePct.toFixed(2)}%` : "—";
  const deltaSign = collectionDeltaPct == null ? null : collectionDeltaPct >= 0 ? "+" : "";
  const badge = budgetStatusBadge(arrears, collectionRatePct);

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="cash-position-card"
      role="region"
      aria-label="Cash position"
    >
      <header className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">Cash Position</div>
          <h3 className="text-lg font-black text-slate-900">Funds, arrears and collection momentum</h3>
        </div>
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${badge.cls}`}>
          {badge.label}
        </span>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        <FundTile
          label="Admin Fund"
          fund={admin}
          accent="#16A34A"
          onClick={onOpenAdmin}
        />
        <FundTile
          label="Sinking Fund"
          fund={sinking}
          accent="#4F46E5"
          onClick={onOpenSinking}
        />
      </div>

      <button
        type="button"
        onClick={onOpenArrears}
        className="w-full rounded-2xl bg-slate-900 text-white p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-left hover:bg-slate-800 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2"
        aria-label="View arrears and collection details"
      >
        <div>
          <div className="text-[10px] font-bold uppercase tracking-widest text-white/70">Arrears</div>
          <div className="flex items-baseline gap-2 mt-0.5">
            <span className="text-2xl font-black">{formatCurrency(arrears.total)}</span>
            {arrears.delta_pct != null && (
              <span
                className={`inline-flex items-center gap-0.5 text-xs font-bold ${
                  arrears.delta_pct < 0 ? "text-emerald-300" : "text-rose-300"
                }`}
                aria-label={`Arrears change ${arrears.delta_pct > 0 ? "+" : ""}${arrears.delta_pct}%`}
              >
                {arrears.delta_pct < 0 ? <ArrowDownRight size={12} /> : <ArrowUpRight size={12} />}
                {arrears.delta_pct > 0 ? "+" : ""}
                {arrears.delta_pct}%
              </span>
            )}
          </div>
          <div className="text-[11px] text-white/60 mt-0.5">
            {arrears.units} unit{arrears.units === 1 ? "" : "s"} outstanding
          </div>
        </div>
        {arrears.momentum && arrears.momentum.length > 0 && (
          <div className="w-24 text-emerald-300" aria-hidden="true">
            <SparklineMini points={arrears.momentum} color="#34D399" height={40} fill={false} />
          </div>
        )}
        <div className="text-right">
          <div className="text-[10px] font-bold uppercase tracking-widest text-white/70">Collection</div>
          <div className="text-2xl font-black mt-0.5">{collectionText}</div>
          <div className="text-[11px] text-emerald-300 font-bold">
            {collectionDeltaPct == null
              ? "—"
              : `${deltaSign}${collectionDeltaPct}pt wk${collectionTargetPct ? ` · goal ${collectionTargetPct}%` : ""}`}
          </div>
        </div>
      </button>
    </section>
  );
}
/**
 * @generated FunctionHeader
 * Function: FundTile
 * Path: frontend/src/components/dashboard/CashPositionCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FundTile({
  label,
  fund,
  accent,
  onClick,
}: {
  label: string;
  fund: FundSeries;
  accent: string;
  onClick?: () => void;
}) {
  const Wrapper: any = onClick ? "button" : "div";
  const wrapperProps = onClick
    ? {
        type: "button" as const,
        onClick,
        className:
          "rounded-2xl ring-1 ring-slate-200 p-4 text-left hover:ring-indigo-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 w-full",
        "aria-label": `View ${label} details`,
      }
    : { className: "rounded-2xl ring-1 ring-slate-200 p-4" };

  return (
    <Wrapper {...wrapperProps}>
      <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{label}</div>
      <div className="text-2xl font-black text-slate-900 mt-0.5">{formatCurrency(fund.balance)}</div>
      {fund.trend && fund.trend.length > 0 && (
        <div className="mt-2 h-12">
          <SparklineMini points={fund.trend} color={accent} height={48} ariaLabel={`${label} trend`} />
        </div>
      )}
      {fund.target != null && (
        <div className="text-[11px] text-slate-500 font-semibold mt-1">Target {fmt$k(fund.target)}</div>
      )}
    </Wrapper>
  );
}
