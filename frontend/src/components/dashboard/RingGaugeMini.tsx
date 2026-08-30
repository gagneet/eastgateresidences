// @featuretrace:dashboard-v2 — Reusable ring gauge for dashboard hero scores.
// Layer: frontend
// Data flow: numeric value (0–100) → circular SVG meter (global).
// Related: frontend/src/components/dashboard/BuildingStrengthCard.tsx
//           frontend/src/components/dashboard/LevyFairnessCard.tsx

"use client";
import React from "react";

interface RingGaugeMiniProps {
  value: number;
  size?: number;
  stroke?: number;
  label?: React.ReactNode;
  sub?: string;
  color?: string;
  track?: string;
  textClassName?: string;
  subClassName?: string;
  ariaLabel?: string;
}
/**
 * @generated FunctionHeader
 * Function: RingGaugeMini
 * Path: frontend/src/components/dashboard/RingGaugeMini.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function RingGaugeMini({
  value,
  size = 96,
  stroke = 8,
  label,
  sub,
  color = "#4F46E5",
  track = "rgba(15,23,42,.08)",
  textClassName = "text-slate-900",
  subClassName = "text-slate-400",
  ariaLabel,
}: RingGaugeMiniProps) {
  const clamped = Math.max(0, Math.min(100, value || 0));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const off = c - (clamped / 100) * c;
  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={ariaLabel ?? `Score ${clamped} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} stroke={track} strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={color}
          strokeWidth={stroke}
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={off}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className={`text-xl font-black leading-none ${textClassName}`}>{label ?? `${clamped}`}</div>
        {sub && (
          <div className={`text-[9px] font-bold uppercase tracking-[0.16em] mt-1 ${subClassName}`}>{sub}</div>
        )}
      </div>
    </div>
  );
}
