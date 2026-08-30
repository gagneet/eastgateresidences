// @featuretrace:dashboard-v2 — Management expenses-by-supplier card (GL spend, not maintenance).
// Layer: frontend
// Data flow: ManagementDashboard → /analytics/expenses-by-supplier → ExpensesBySupplierCard (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
//          backend/routers/analytics.py::get_expenses_by_supplier
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { ArrowRight } from "lucide-react";
import { formatCurrency } from "../../lib/utils";
import SparklineMini from "./SparklineMini";

export interface SupplierSpendRow {
  supplier: string;
  spend: number;
  transactions: number;
  trend?: number[];
}

interface ExpensesBySupplierCardProps {
  suppliers: SupplierSpendRow[];
  windowMonths?: number;
  loading?: boolean;
  onAll?: () => void;
  className?: string;
}

function supplierInitials(name: string) {
  return (name || "?")
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

/**
 * Expenses by supplier — a SPEND-ONLY view sourced from finance expense_transactions
 * (distinct from the maintenance-work-order Vendor performance scorecard, which also
 * carries jobs / on-time). Shows top suppliers by 12-month GL spend.
 */
export default function ExpensesBySupplierCard({
  suppliers,
  windowMonths = 12,
  loading = false,
  onAll,
  className = "",
}: ExpensesBySupplierCardProps) {
  const safe = (suppliers || []).slice(0, 6);
  const windowLabel = `${windowMonths}mo`;

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="expenses-by-supplier-card"
      role="region"
      aria-label="Expenses by supplier"
    >
      <header className="flex items-end justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">
            Expenses by supplier · {windowLabel}
          </div>
          <h3 className="text-lg font-black text-slate-900">Where the money went</h3>
        </div>
        {onAll && (
          <button
            type="button"
            onClick={onAll}
            className="text-xs font-bold text-indigo-600 inline-flex items-center gap-1 hover:text-indigo-800 transition-colors"
            aria-label="View all expenses"
          >
            All expenses <ArrowRight size={12} />
          </button>
        )}
      </header>

      {loading ? (
        <div className="py-8 text-center text-sm font-semibold text-slate-400">Loading supplier spend…</div>
      ) : safe.length === 0 ? (
        <div className="py-8 text-center text-sm font-semibold text-slate-400">
          No supplier spend recorded in the last {windowLabel}.
        </div>
      ) : (
        <div>
          <div className="grid grid-cols-12 text-[10px] font-black uppercase tracking-widest text-slate-400 pb-2 border-b border-slate-100">
            <div className="col-span-5">Supplier</div>
            <div className="col-span-2 text-right">Txns</div>
            <div className="col-span-2 text-right">Spend {windowLabel}</div>
            <div className="col-span-3 text-right">Spend trend</div>
          </div>
          <ul role="list">
            {safe.map((s, i) => (
              <li
                key={`${s.supplier}-${i}`}
                className="grid grid-cols-12 items-center py-2.5 border-b border-slate-50 last:border-0"
              >
                <div className="col-span-5 flex items-center gap-2 min-w-0">
                  <span
                    aria-hidden="true"
                    className="shrink-0 h-7 w-7 rounded-full bg-indigo-50 text-indigo-600 text-[10px] font-black grid place-items-center"
                  >
                    {supplierInitials(s.supplier)}
                  </span>
                  <span className="truncate text-sm font-bold text-slate-800" title={s.supplier}>
                    {s.supplier}
                  </span>
                </div>
                <div className="col-span-2 text-right text-sm font-bold text-slate-900">{s.transactions}</div>
                <div className="col-span-2 text-right text-sm font-black text-slate-900">
                  {formatCurrency(s.spend)}
                </div>
                <div className="col-span-3 flex justify-end">
                  {s.trend && s.trend.length > 1 ? (
                    <SparklineMini points={s.trend} />
                  ) : (
                    <span className="text-xs text-slate-300">—</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
