// @featuretrace:dashboard-v2 — Dashboard footer cue (auto-digest / market comparison line).
// Layer: frontend
// Data flow: static text + optional action (global).
// Related: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
//           frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx

"use client";
import React from "react";

interface DashboardFooterCueProps {
  children: React.ReactNode;
  dotColor?: string;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: DashboardFooterCue
 * Path: frontend/src/components/dashboard/DashboardFooterCue.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function DashboardFooterCue({
  children,
  dotColor = "#10B981",
  className = "",
}: DashboardFooterCueProps) {
  // No role="contentinfo" — that is reserved for the single page-level footer
  // landmark and we render up to two of these cues per dashboard. They're
  // decorative status hints, not landmarks.
  return (
    <div className={`mt-8 flex items-center justify-center gap-3 text-xs ${className}`}>
      <span
        className="w-1.5 h-1.5 rounded-full motion-safe:animate-pulse flex-shrink-0"
        style={{ background: dotColor }}
        aria-hidden="true"
      />
      <span className="font-semibold text-slate-500 text-center">{children}</span>
    </div>
  );
}
