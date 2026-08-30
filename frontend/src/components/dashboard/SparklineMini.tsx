// @featuretrace:dashboard-v2 — Reusable sparkline used by cash/vendor/market dashboard cards.
// Layer: frontend
// Data flow: numeric series → inline SVG (no external chart lib) (global).
// Related: frontend/src/components/dashboard/CashPositionCard.tsx
//           frontend/src/components/dashboard/VendorScorecardCard.tsx
//           frontend/src/components/dashboard/MarketIntelExpanded.tsx

"use client";
import React from "react";

interface SparklineMiniProps {
  points: number[];
  color?: string;
  height?: number;
  fill?: boolean;
  className?: string;
  ariaLabel?: string;
}
/**
 * @generated FunctionHeader
 * Function: SparklineMini
 * Path: frontend/src/components/dashboard/SparklineMini.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SparklineMini({
  points = [],
  color = "#4F46E5",
  height = 28,
  fill = true,
  className = "",
  ariaLabel,
}: SparklineMiniProps) {
  if (!points.length) {
    return (
      <div
        className={`h-full w-full ${className}`}
        style={{ height }}
        role="img"
        aria-label={ariaLabel ?? "No trend data"}
      />
    );
  }
  const w = 100;
  const h = height;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const step = points.length > 1 ? w / (points.length - 1) : w;
  const coords = points.map((v, i) => [i * step, h - ((v - min) / range) * (h - 4) - 2] as const);
  const d = coords.map(([x, y], i) => (i === 0 ? "M" : "L") + x.toFixed(2) + " " + y.toFixed(2)).join(" ");
  const area = `${d} L ${w} ${h} L 0 ${h} Z`;
  const last = coords[coords.length - 1];

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      width="100%"
      height={h}
      className={className}
      role="img"
      aria-label={ariaLabel ?? `Trend sparkline with ${points.length} points`}
    >
      {fill && <path d={area} fill={color} opacity="0.12" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="2.4" fill={color} />
    </svg>
  );
}
