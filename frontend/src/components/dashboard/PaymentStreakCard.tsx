// @featuretrace:dashboard-v2 — Payment Streak: consecutive on-time quarters + rank badge for owners.
// Layer: frontend
// Data flow: OwnerDashboard → /owners-units/{unit} + streak_service → PaymentStreakCard (building-scoped).
// Related: frontend/src/pages/dashboard/OwnerDashboard.tsx
//           backend/services/streak_service.py
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { Flame, TrendingUp } from "lucide-react";

interface StreakData {
  streak?: number;          // consecutive on-time quarters
  total_quarters?: number;
  on_time_count?: number;
  on_time_pct?: number;
  recent_quarters?: Array<{ on_time: boolean; year?: string; quarter?: string }>;
}

interface PaymentStreakCardProps {
  data?: StreakData;
  rank?: number;            // rank among all payers (lower = better)
  totalLots?: number;
  className?: string;
  dark?: boolean;           // render on dark background (inside hero)
}
/**
 * @generated FunctionHeader
 * Function: PaymentStreakCard
 * Path: frontend/src/components/dashboard/PaymentStreakCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PaymentStreakCard({
  data,
  rank,
  totalLots,
  className = "",
  dark = false,
}: PaymentStreakCardProps) {
  const streak = data?.streak ?? 0;
  const recentQuarters = data?.recent_quarters ?? [];
  const onTimePct = data?.on_time_pct ?? 0;
  const rankPct = rank && totalLots ? Math.round((rank / totalLots) * 100) : null;

  // Pad recent quarters to 14 dots (a fixed-width recent-history visualization, not a claim
  // about the owner's total quarter count — total_quarters/streak come from the backend's own
  // count, which is no longer capped at 8, see analytics.py::get_my_streak).
  // streak_service returns recent_quarters descending (most-recent first), so
  // recentQuarters[0] = most recent. Dots render left→right = index 0..13 = most-recent..oldest.
  // When padding (no real quarter data for that slot), fill index 0..streak-1 as on-time
  // so the leftmost (most-recent) dots match the stated streak count.
  const dots = Array.from({ length: 14 }, (_, i) => {
    const q = recentQuarters[i];
    return q ? q.on_time : i < streak;
  });

  // Oldest quarter actually shown in the 14-dot track (real data only — never the padded
  // slots), so this label is always derived from what the dots literally represent instead of
  // a fixed guess. Previously a hardcoded "3.5 yrs ago" regardless of the real data — wrong for
  // any unit whose real history is shorter (e.g. a recently purchased unit) or a different
  // length than 3.5 years.
  const realQuartersShown = recentQuarters.slice(0, 14).filter((q) => q?.year);
  const oldestShown = realQuartersShown[realQuartersShown.length - 1];
  // analytics.py's get_my_streak serialises quarter as str(quarter_no or "") — a null/0
  // quarter_no (a non-quarterly levy period) comes through as "", never the string "0", so a
  // plain truthiness check is correct here; a `!== "0"` check would be guarding against a value
  // the backend never actually produces.
  const oldestLabel = oldestShown
    ? (oldestShown.quarter ? `Q${oldestShown.quarter} ` : "") + oldestShown.year
    : null;

  const textPrimary = dark ? "text-white" : "text-slate-900";
  const textMuted = dark ? "text-white/60" : "text-slate-500";
  const textLabel = dark ? "text-white/40" : "text-slate-400";
  const cardBg = dark ? "bg-white/5 ring-white/10" : "bg-slate-50 ring-slate-200";

  return (
    <div
      className={`rounded-2xl ring-1 p-4 ${cardBg} ${className}`}
      data-testid="payment-streak-card"
      role="region"
      aria-label={`Payment streak: ${streak} consecutive on-time quarters`}
    >
      {/* Header */}
      <div className={`flex items-center gap-2 mb-2 ${dark ? "text-amber-300" : "text-amber-500"}`}>
        <Flame size={16} aria-hidden="true" />
        <span className={`text-[10px] font-black uppercase tracking-widest ${dark ? "text-white/70" : "text-slate-600"}`}>
          Payment streak
        </span>
      </div>

      {/* Main number */}
      <div className="flex items-baseline gap-2">
        <span
          className={`text-4xl font-black ${textPrimary}`}
          style={{ fontFeatureSettings: '"tnum" 1' }}
        >
          {streak}
        </span>
        <span className={`text-base font-bold ${textMuted}`}>quarters</span>
      </div>

      {/* Sub-stats */}
      {onTimePct > 0 && (
        <div className={`text-xs ${textMuted} mt-1 flex items-center gap-1`}>
          <TrendingUp size={12} aria-hidden="true" />
          {onTimePct}% on-time overall
          {rankPct !== null && (
            <span> · top {rankPct}% of owners</span>
          )}
        </div>
      )}

      {/* Quarter-dot track */}
      <div
        className="mt-3 flex gap-0.5"
        role="list"
        aria-label="Recent 14 quarters payment history"
      >
        {dots.map((onTime, i) => (
          <span
            key={i}
            role="listitem"
            aria-label={onTime ? "Paid on time" : "Late or missed"}
            className={`flex-1 h-2 rounded-sm transition-colors ${
              onTime
                ? dark ? "bg-emerald-400/80" : "bg-emerald-500"
                : dark ? "bg-white/15" : "bg-slate-200"
            }`}
          />
        ))}
      </div>

      <div className={`mt-1.5 flex justify-between text-[9px] font-bold uppercase tracking-widest ${textLabel}`}>
        <span>{oldestLabel ?? ""}</span>
        <span>Now</span>
      </div>
    </div>
  );
}
