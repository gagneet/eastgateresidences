// @featuretrace:dashboard-v2 — Owner "Your requests" + emergency contacts panel.
// Layer: frontend
// Data flow: OwnerDashboard → GET /workflow-requests (own requests) + dashboard_v2_extras.emergency_contacts → YourRequestsCard (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
//           backend/routers/analytics.py (/analytics/dashboard-v2-extras)
// Toggle: ft_dashboard_v2

"use client";
import React from "react";
import { Plus, AlertTriangle } from "lucide-react";

export interface OwnerRequest {
  id?: string;
  reference?: string;
  status?: string;       // in_progress | awaiting_owner | overdue | open
  title?: string;
  summary?: string;
  needs_reply?: boolean;
  href?: string;
}

export interface EmergencyContact {
  label: string;
  phone: string;
  detail?: string;
}

interface YourRequestsCardProps {
  requests?: OwnerRequest[];
  emergencyContacts?: EmergencyContact[];
  onNewRequest?: () => void;
  onRequest?: (request: OwnerRequest) => void;
  className?: string;
}
/**
 * @generated FunctionHeader
 * Function: statusStyling
 * Path: frontend/src/components/dashboard/YourRequestsCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function statusStyling(status?: string, needsReply?: boolean) {
  if (needsReply || status === "awaiting_owner") {
    return {
      ring: "ring-rose-200 bg-rose-50",
      pill: "bg-rose-50 text-rose-700 ring-rose-200",
      label: "Needs your reply",
    };
  }
  if (status === "overdue") {
    return {
      ring: "ring-amber-300 bg-amber-50",
      pill: "bg-amber-50 text-amber-800 ring-amber-200",
      label: "Overdue",
    };
  }
  return {
    ring: "ring-amber-200 bg-amber-50",
    pill: "bg-amber-50 text-amber-800 ring-amber-200",
    label: "In progress",
  };
}
/**
 * @generated FunctionHeader
 * Function: YourRequestsCard
 * Path: frontend/src/components/dashboard/YourRequestsCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function YourRequestsCard({
  requests = [],
  emergencyContacts = [],
  onNewRequest,
  onRequest,
  className = "",
}: YourRequestsCardProps) {
  const safe = requests.slice(0, 3);
  const needsReplyCount = safe.filter((r) => r.needs_reply || r.status === "awaiting_owner").length;

  return (
    <section
      className={`rounded-3xl bg-white ring-1 ring-slate-200/70 p-6 shadow-[0_1px_0_rgba(15,23,42,.04),0_12px_28px_-16px_rgba(15,23,42,.12)] ${className}`}
      data-testid="your-requests-card"
      aria-label="Your maintenance requests and emergency contacts"
    >
      <header className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">Your requests</div>
          <h3 className="text-lg font-black text-slate-900">
            {safe.length === 0
              ? "No active requests"
              : `${safe.length} open${needsReplyCount > 0 ? ` · ${needsReplyCount} awaiting your reply` : ""}`}
          </h3>
        </div>
        <button
          type="button"
          onClick={onNewRequest}
          className="rounded-lg bg-slate-900 text-white text-xs font-bold px-3 py-1.5 inline-flex items-center gap-1 hover:bg-slate-800 transition-colors"
          aria-label="Raise a new request"
        >
          <Plus size={14} /> New request
        </button>
      </header>

      {safe.length === 0 ? (
        <div className="py-4 text-sm font-semibold text-slate-400">
          When you raise a request, it'll show here with status and any replies needed.
        </div>
      ) : (
        <ul className="space-y-2" role="list">
          {safe.map((r, i) => {
            const styling = statusStyling(r.status, r.needs_reply);
            return (
              <li key={r.id ?? r.reference ?? i}>
                <button
                  type="button"
                  onClick={() => onRequest?.(r)}
                  className={`w-full text-left rounded-xl ring-1 p-3 ${styling.ring} hover:ring-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-400`}
                  aria-label={`Open ${r.title ?? "request"}`}
                >
                <div className="flex items-center justify-between">
                  <span
                    className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 ${styling.pill}`}
                  >
                    {styling.label}
                  </span>
                  {r.reference && (
                    <span className="text-[10px] font-bold text-slate-400">{r.reference}</span>
                  )}
                </div>
                <div className="font-bold text-sm text-slate-900 mt-1">{r.title ?? "Maintenance request"}</div>
                {r.summary && <div className="text-[11px] text-slate-500 mt-0.5">{r.summary}</div>}
                {(r.needs_reply || r.status === "awaiting_owner") && onRequest && (
                  <span className="mt-2 inline-block text-xs font-bold text-rose-700">Reply now →</span>
                )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {emergencyContacts.length > 0 && (
        <div className="mt-4 rounded-2xl bg-slate-900 text-white p-4">
          <div className="flex items-center gap-2 mb-2 text-rose-300" aria-hidden="true">
            <AlertTriangle size={14} />
            <span className="text-[10px] font-black uppercase tracking-widest">Emergency contacts</span>
          </div>
          <ul
            className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs"
            role="list"
            aria-label="Emergency contacts"
          >
            {emergencyContacts.slice(0, 4).map((c, i) => (
              <li key={`${c.label}-${i}`} className="rounded-lg bg-white/5 p-2">
                <div className="text-[10px] text-white/70 font-bold uppercase">{c.label}</div>
                <a
                  href={`tel:${c.phone.replace(/\s/g, "")}`}
                  className="font-bold text-white rounded hover:underline focus:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
                >
                  {c.phone}
                </a>
                {c.detail && <div className="text-[11px] text-white/70 mt-0.5">{c.detail}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
