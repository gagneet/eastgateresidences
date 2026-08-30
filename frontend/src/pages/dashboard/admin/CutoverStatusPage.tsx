// @featuretrace:finance-postgres-write-cutover — Internal admin panel shows selected finance write-route readiness and blockers.
// Data flow: CutoverStatusPage -> GET /admin/cutover/status-finance-write-routes/{building_id} -> finance_write_cutover_service (building-scoped).
// Related: backend/routers/cutover_admin.py
//          backend/services/finance_write_cutover_service.py
// @featuretrace:cutover-control-plane — Internal admin SOT dashboard: shows per-domain cutover mode, readiness, diffs, and promote/rollback controls.
// Layer: frontend
// Data flow: CutoverStatusPage → GET /api/admin/cutover/status → cutover_status_service → core.domain_cutover_status (building-scoped).
// Related: backend/routers/cutover_admin.py
//           backend/services/cutover_status_service.py
//           docs/architecture/source-of-truth-matrix.md
//           docs/architecture/feature-toggle-governance.md

"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/contexts/AuthContext";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type CutoverMode =
  | "mongo_primary"
  | "postgres_shadow"
  | "postgres_read"
  | "postgres_write"
  | "mongo_archive"
  | "disabled";

type ReadinessStatus =
  | "unknown"
  | "not_started"
  | "identity_ready"
  | "evidence_ready"
  | "genesis_ready"
  | "ready_for_shadow"
  | "shadow_active"
  | "shadow_passing"
  | "shadow_clean"
  | "promoted"
  | "rolled_back"
  | "blocked";

interface StatusSummary {
  building_id: string;
  domain: string;
  mode: CutoverMode;
  readiness_status: ReadinessStatus;
  read_source: string;
  write_source: string;
  last_readiness_check_at: string | null;
  last_promoted_at: string | null;
  last_shadow_diff_at: string | null;
  rollback_available: boolean;
  toggle_name: string | null;
  notes?: string | null;
  p0_snapshot?: Record<string, any>;
}

interface StatusListResponse {
  items: StatusSummary[];
  total: number;
}

interface FinanceRouteStatus {
  route_key: string;
  method: string;
  path: string;
  frontend_consumer: string;
  read_only: boolean;
  shadow_supported: boolean;
  postgres_read_supported: boolean;
  current_source: "mongo" | "postgres";
  domain_mode: string;
  readiness_status: string;
  diff_count: number;
  critical_count: number;
  last_compared_at: string | null;
  // False once a route is promoted to postgres (shadow compares stop -- nothing left to
  // shadow once PG is primary): readiness_status/diff_count/critical_count/last_compared_at
  // are then a FROZEN snapshot from before promotion, not a live health check.
  shadow_monitoring_active: boolean;
  eligible_for_postgres_read: boolean;
  shadow_waiver_applied: boolean;
  blocked_reason: string | null;
  notes: string;
}

interface FinanceWriteRouteStatus {
  route_key: string;
  method: string;
  path: string;
  frontend_consumer: string;
  current_write_source: string;
  target_write_source: string;
  current_source: "mongo" | "postgres";
  domain_mode: string;
  cutover_mode_required: string;
  idempotency_required: boolean;
  audit_required: boolean;
  reversal_supported: boolean;
  eligible_for_postgres_write: boolean;
  blocked_reason: string | null;
  production_readiness_status: string;
  notes: string;
}

// ---------------------------------------------------------------------------
// Mode badge colours
// ---------------------------------------------------------------------------

const MODE_BADGE: Record<CutoverMode, { bg: string; text: string; label: string }> = {
  mongo_primary:   { bg: "bg-slate-100",  text: "text-slate-700",  label: "Mongo Primary" },
  postgres_shadow: { bg: "bg-blue-100",   text: "text-blue-800",   label: "PG Shadow" },
  postgres_read:   { bg: "bg-yellow-100", text: "text-yellow-800", label: "PG Read" },
  postgres_write:  { bg: "bg-green-100",  text: "text-green-800",  label: "PG Write" },
  mongo_archive:   { bg: "bg-purple-100", text: "text-purple-800", label: "Mongo Archive" },
  disabled:        { bg: "bg-red-100",    text: "text-red-800",    label: "Disabled" },
};

const READINESS_BADGE: Record<ReadinessStatus, { dot: string; label: string }> = {
  unknown:        { dot: "bg-gray-400",   label: "Unknown" },
  not_started:    { dot: "bg-gray-400",   label: "Not Started" },
  identity_ready: { dot: "bg-blue-300",   label: "Identity Ready" },
  evidence_ready: { dot: "bg-cyan-500",   label: "Evidence Ready" },
  genesis_ready:  { dot: "bg-indigo-500", label: "Genesis Ready" },
  ready_for_shadow:{ dot: "bg-emerald-500",label: "Ready for Shadow" },
  shadow_active:  { dot: "bg-blue-500",   label: "Shadow Active" },
  shadow_passing: { dot: "bg-yellow-500", label: "Shadow Passing" },
  shadow_clean:   { dot: "bg-green-400",  label: "Shadow Clean" },
  promoted:       { dot: "bg-green-600",  label: "Promoted" },
  rolled_back:    { dot: "bg-orange-500", label: "Rolled Back" },
  blocked:        { dot: "bg-red-500",    label: "Blocked" },
};

// ---------------------------------------------------------------------------
// Application-wide source-of-truth reference (STATIC).
// The cutover control plane only tracks finance_ledger + identity_core; every
// other domain is MongoDB-only and not yet in cutover scope. This table gives
// operators the whole-app picture the live control-plane table below cannot
// (it shows only registered domains). Maintained from the 2026-08-07 app-wide
// source-code map (see tasks/CUTOVER-COMPLETION-REGISTER.md).
// ---------------------------------------------------------------------------

type AppTier = "pg_live" | "pg_gated" | "mongo_only";

const APP_TIER_BADGE: Record<AppTier, { bg: string; text: string; label: string }> = {
  pg_live:    { bg: "bg-green-100",  text: "text-green-800",  label: "PG wired" },
  pg_gated:   { bg: "bg-yellow-100", text: "text-yellow-800", label: "PG code — gated/shadow" },
  mongo_only: { bg: "bg-slate-100",  text: "text-slate-700",  label: "MongoDB only" },
};

const APP_CUTOVER_MAP: {
  domain: string; read: string; write: string; tier: AppTier; note: string;
}[] = [
  // Tier 1 — genuinely promotable PG paths (read + write)
  { domain: "Finance / levies / ledger", read: "Mongo (PG route-gated)", write: "Mongo → PG on promote (5/7 routes)", tier: "pg_live",
    note: "finance_ledger. 1/36 reads + 0/7 writes resolve to PG in production until deploy. Detail in the finance panel above." },
  { domain: "Identity / users / ownership", read: "PG-forward (gated)", write: "Mongo + PG (registration writes PG)", tier: "pg_live",
    note: "identity_core promoted for East Gate — core.lots / parties / ownership_periods." },
  // Tier 2 — PG code path exists but resolves Mongo today (gated / shadow / config)
  { domain: "Analytics / BI / intelligence", read: "Mongo (PG read models, gated)", write: "Mongo (read/ETL only)", tier: "pg_gated",
    note: "analytics_pg_service + BI fact tables; governed, mostly postgres_read_supported=False → shadow only." },
  { domain: "Trust accounting", read: "Mongo (PG read code, gated)", write: "Mongo", tier: "pg_gated",
    note: "Separate trust_ledger track (TrustReadService + shadow compare); dormant. No PG write. GAP-FIN-049." },
  { domain: "Settings / feature-toggles", read: "Mongo / PG (gated)", write: "PG for toggle config", tier: "pg_gated",
    note: "config_repo persists toggle config to PG; settings PG read gated + shadow-compared." },
  { domain: "Onboarding / trial / genesis", read: "PG for trial + genesis", write: "PG for trial + genesis", tier: "pg_gated",
    note: "trial_request is PG-native; financial genesis writes PG via financial_core." },
  { domain: "Occupancy", read: "Mongo (PG gated)", write: "Mongo", tier: "pg_gated",
    note: "occupancy domain; _pg_occupancy_summary gated read." },
  { domain: "Bank feeds", read: "PG summary", write: "Mongo", tier: "pg_gated",
    note: "strata_year_summary_postgres; bank-reconciliation PG write deferred (postgres_write_supported=False)." },
  { domain: "Organisations / mgmt hierarchy", read: "PG", write: "Mixed", tier: "pg_gated",
    note: "sm_organisations / management_hierarchy query PG directly." },
  { domain: "Outbox / workflow-requests", read: "PG (outbox) / Mongo", write: "PG outbox; workflow-requests Mongo+PG shadow", tier: "pg_gated",
    note: "Control-plane + event propagation; workflow_requests dual-writes a PG shadow copy." },
  // Tier 3 — 100% MongoDB, no PG code path yet
  { domain: "Governance (AGM / meetings / voting / proposals / EC)", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Maintenance / work-orders / defects", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Documents / OCR", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Communication / chat / notifications", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Marketplace / listings", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Community / events / bookings", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Compliance / safety / WHS", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Insurance (claims / lending)", read: "Mongo", write: "Mongo", tier: "mongo_only", note: "No PG code path." },
  { domain: "Council rates / water / utilities", read: "Mongo", write: "Mongo", tier: "mongo_only",
    note: "Inventoried in the finance route policy table but the routers never call the cutover engine." },
  { domain: "Owner-hub / tenancies / properties", read: "Mongo", write: "Mongo", tier: "mongo_only",
    note: "Owner-authored entities with no PG equivalent (GAP-FIN-051)." },
  { domain: "Arrears / payment-plans / reminders / savings / reports", read: "Mongo", write: "Mongo", tier: "mongo_only",
    note: "Finance-adjacent, Mongo-only; some routes inventoried in the finance route table for visibility." },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * @generated FunctionHeader
 * Function: ModeBadge
 * Path: frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ModeBadge({ mode }: { mode: CutoverMode }) {
  const cfg = MODE_BADGE[mode] ?? MODE_BADGE.disabled;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.text}`}
      data-testid={`mode-badge-${mode}`}
    >
      {cfg.label}
    </span>
  );
}
/**
 * @generated FunctionHeader
 * Function: ReadinessDot
 * Path: frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ReadinessDot({ status }: { status: ReadinessStatus }) {
  const cfg = READINESS_BADGE[status] ?? READINESS_BADGE.unknown;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={`h-2 w-2 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  );
}
/**
 * @generated FunctionHeader
 * Function: fmtTs
 * Path: frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function fmtTs(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}
// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

/**
 * @generated FunctionHeader
 * Function: CutoverStatusPage
 * Path: frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CutoverStatusPage() {
  const { api, loading, isAdmin, hasFeatureAccess } = useAuth() as any;

  const [items, setItems] = useState<StatusSummary[]>([]);
  const [isFetching, setIsFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [buildingFilter, setBuildingFilter] = useState("");
  const [domainFilter, setDomainFilter] = useState("");
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [acting, setActing] = useState<string | null>(null); // "building/domain" being acted on
  const [financeRoutes, setFinanceRoutes] = useState<FinanceRouteStatus[]>([]);
  const [financeRoutesLoading, setFinanceRoutesLoading] = useState(false);
  const [financeWriteRoutes, setFinanceWriteRoutes] = useState<FinanceWriteRouteStatus[]>([]);
  const [financeWriteRoutesLoading, setFinanceWriteRoutesLoading] = useState(false);

  const financeSummary = items.find((item) => item.domain === "finance_ledger");
  const financePromotableRoutes = financeRoutes.filter(
    (route) => route.postgres_read_supported && route.eligible_for_postgres_read
  );
  const financePromoteEligible = financePromotableRoutes.length > 0;
  const financePromoteBlockedReason =
    financeRoutes
      .filter((route) => route.postgres_read_supported && !route.eligible_for_postgres_read)
      .map((route) => `${route.route_key}: ${route.blocked_reason || route.readiness_status}`)
      .join(" | ") || "No promotable finance routes are configured";

  const canAccess = isAdmin();

  const fetchStatus = useCallback(async () => {
    if (!canAccess) return;
    setIsFetching(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (buildingFilter) params.set("building_id", buildingFilter);
      if (domainFilter) params.set("domain", domainFilter);
      const qs = params.toString() ? `?${params}` : "";
      const res = await api.get(`/admin/cutover/status${qs}`);
      setItems((res?.data as StatusListResponse)?.items ?? []);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to load cutover status.");
    } finally {
      setIsFetching(false);
    }
  }, [api, canAccess, buildingFilter, domainFilter]);

  const fetchFinanceRoutes = useCallback(
    async (buildingId: string) => {
      if (!buildingId) return;
      setFinanceRoutesLoading(true);
      try {
        const res = await api.get(`/admin/cutover/status-finance-routes/${buildingId}`);
        const rows = res?.data;
        setFinanceRoutes(Array.isArray(rows) ? (rows as FinanceRouteStatus[]) : []);
      } catch {
        setFinanceRoutes([]);
      } finally {
        setFinanceRoutesLoading(false);
      }
    },
    [api],
  );

  const fetchFinanceWriteRoutes = useCallback(
    async (buildingId: string) => {
      if (!buildingId) return;
      setFinanceWriteRoutesLoading(true);
      try {
        const res = await api.get(`/admin/cutover/status-finance-write-routes/${buildingId}`);
        const rows = res?.data;
        setFinanceWriteRoutes(Array.isArray(rows) ? (rows as FinanceWriteRouteStatus[]) : []);
      } catch {
        setFinanceWriteRoutes([]);
      } finally {
        setFinanceWriteRoutesLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    if (loading || !canAccess) return;
    fetchStatus();
  }, [loading, canAccess, fetchStatus]);

  useEffect(() => {
    if (loading || !canAccess) return;
    if (!financeSummary?.building_id) {
      setFinanceRoutes([]);
      setFinanceWriteRoutes([]);
      return;
    }
    fetchFinanceRoutes(financeSummary.building_id);
    fetchFinanceWriteRoutes(financeSummary.building_id);
  }, [loading, canAccess, financeSummary?.building_id, fetchFinanceRoutes, fetchFinanceWriteRoutes]);
  /**
   * @generated FunctionHeader
   * Function: doAction
   * Path: frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
   *
   * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
   */
  async function doAction(
    buildingId: string,
    domain: string,
    endpoint: string,
    body: Record<string, unknown> = {},
  ) {
    const key = `${buildingId}/${domain}`;
    setActing(key);
    setActionMsg(null);
    try {
      const res = await api.post(`/admin/cutover/${buildingId}/${domain}/${endpoint}`, body);
      const msg = res?.data?.message ?? `Action '${endpoint}' completed.`;
      setActionMsg({ ok: true, text: msg });
      await fetchStatus();
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? `Action '${endpoint}' failed.`;
      setActionMsg({ ok: false, text: detail });
    } finally {
      setActing(null);
    }
  }

  // ---------------------------------------------------------------------------
  // Guards
  // ---------------------------------------------------------------------------

  if (loading) {
    return <div data-testid="cutover-loading" className="p-6 text-sm">Loading…</div>;
  }

  if (!canAccess) {
    return (
      <div data-testid="cutover-forbidden" className="p-6">
        <p className="text-sm text-destructive font-medium">
          Access denied. This page is for super_admin only.
        </p>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <main className="space-y-6 p-4 md:p-6" data-testid="cutover-status-page">

      {/* ── INTERNAL WARNING BANNER ── */}
      <div
        className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
        data-testid="cutover-internal-warning"
        role="alert"
      >
        <strong>⚠ INTERNAL ADMIN ONLY.</strong> This dashboard controls the live
        MongoDB→PostgreSQL cutover. Incorrect promotion or rollback can corrupt
        production data. Only super_admin operators with an explicit cutover plan
        should use these controls. All actions are permanently logged.
      </div>

      {/* ── HEADER ── */}
      <header className="rounded-lg border p-4">
        <h1 className="text-xl font-semibold" data-testid="cutover-page-title">
          Source-of-Truth Control Plane
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Per-building, per-domain cutover status. Promote or roll back individual
          domains through the shadow → read → write lifecycle.
        </p>
      </header>

      {financeSummary && (
        <section className="rounded-lg border p-4" data-testid="finance-readiness-panel">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">Finance readiness</h2>
              <p className="text-sm text-muted-foreground">
                Opening evidence, genesis journals, and fund setup are staged for your building, but finance is not promoted yet.
              </p>
            </div>
            <span className="rounded-full border px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {financeSummary.mode}
            </span>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-md border p-3 text-sm">
              <div className="font-medium">Evidence</div>
              <div data-testid="finance-readiness-evidence" className="text-muted-foreground">
                {financeSummary.p0_snapshot?.financial_onboarding?.summary?.evidence_approved ? "Approved" : "Pending approval"}
              </div>
            </div>
            <div className="rounded-md border p-3 text-sm">
              <div className="font-medium">Genesis</div>
              <div data-testid="finance-readiness-genesis" className="text-muted-foreground">
                {financeSummary.p0_snapshot?.financial_onboarding?.summary?.genesis_journals_posted
                  ? `${financeSummary.p0_snapshot.financial_onboarding.summary.genesis_journals_posted} journal(s) posted`
                  : "Genesis journals staged"}
              </div>
            </div>
            <div className="rounded-md border p-3 text-sm">
              <div className="font-medium">Fund setup</div>
              <div data-testid="finance-readiness-funds" className="text-muted-foreground">
                {financeSummary.p0_snapshot?.financial_onboarding?.summary?.funds_bootstrapped ? "Bootstrapped" : "Pending"}
              </div>
            </div>
            <div className="rounded-md border p-3 text-sm">
              <div className="font-medium">Last audit</div>
              <div data-testid="finance-readiness-audit" className="text-muted-foreground">
                {fmtTs(financeSummary.last_readiness_check_at)}
              </div>
            </div>
          </div>
          <div className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900" data-testid="finance-readiness-warning">
            Finance remains in {financeSummary.mode}; it will not become PostgreSQL-primary until a later cutover promotion.
          </div>

          <div className="mt-4">
            <h3 className="text-sm font-semibold">Finance route readiness (read-only cutover)</h3>
            <p className="mt-1 text-xs text-muted-foreground" data-testid="finance-routes-help">
              When a route resolves to PostgreSQL it is served <strong>PG-first with MongoDB as a
              disaster-recovery fallback</strong> — Mongo is used only if the PG read fails, never
              because PG is empty (an empty PG result is served as-is). A route normally becomes
              eligible after its shadow soak reaches <span className="font-mono">shadow_pass</span>;
              with the <span className="font-mono">financial_pg_reads_bypass_shadow</span> waiver
              enabled for the building it can be promoted once parity is verified out-of-band,
              shown below as <span className="text-emerald-700">Yes — via waiver</span>. The waiver
              never bypasses a critical shadow diff.
            </p>
            {financeRoutesLoading ? (
              <div className="mt-2 text-sm text-muted-foreground" data-testid="finance-routes-loading">
                Loading finance route readiness…
              </div>
            ) : financeRoutes.length === 0 ? (
              <div className="mt-2 text-sm text-muted-foreground" data-testid="finance-routes-empty">
                No finance route readiness data yet.
              </div>
            ) : (
              <div className="mt-3 overflow-x-auto rounded-md border" data-testid="finance-routes-table">
                <table className="w-full text-xs">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="px-2 py-2 text-left">Route</th>
                      <th className="px-2 py-2 text-left">Status</th>
                      <th className="px-2 py-2 text-left">Serving</th>
                      <th className="px-2 py-2 text-left">Diffs</th>
                      <th className="px-2 py-2 text-left">Last comparison</th>
                      <th className="px-2 py-2 text-left">Eligible</th>
                    </tr>
                  </thead>
                  <tbody>
                    {financeRoutes.map((route) => (
                      <tr key={route.route_key} className="border-t">
                        <td className="px-2 py-2">
                          <div className="font-mono">{route.route_key}</div>
                          <div className="text-muted-foreground">{route.path}</div>
                        </td>
                        <td className="px-2 py-2">
                          {route.readiness_status}
                          {!route.shadow_monitoring_active && (
                            <span
                              className="ml-1 text-amber-700"
                              title="Shadow monitoring stopped once this route was promoted to postgres — readiness_status/diff/critical counts below are a frozen snapshot from before promotion, not a live health check."
                            >
                              (frozen)
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          {route.current_source === "postgres" ? (
                            <span className="font-medium text-green-700">PostgreSQL</span>
                          ) : (
                            <span className="text-slate-600">MongoDB</span>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <span
                            className={!route.shadow_monitoring_active ? "text-muted-foreground" : undefined}
                            title={
                              !route.shadow_monitoring_active
                                ? "Not actively monitored — last known state as of the comparison timestamp"
                                : undefined
                            }
                          >
                            {route.diff_count} / critical {route.critical_count}
                          </span>
                        </td>
                        <td className="px-2 py-2">
                          {fmtTs(route.last_compared_at)}
                          {!route.shadow_monitoring_active && (
                            <div className="text-muted-foreground">monitoring stopped</div>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          {route.eligible_for_postgres_read ? (
                            <span className="text-emerald-700">
                              Yes{route.shadow_waiver_applied ? " — via waiver" : ""}
                            </span>
                          ) : (
                            <span className="text-amber-700" title={route.blocked_reason || route.notes}>
                              No
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="mt-5" data-testid="finance-write-routes-section">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold">Finance write readiness</h3>
                <span className="text-xs text-muted-foreground">Selected routes only</span>
              </div>
              <div className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="finance-write-routes-warning">
                PostgreSQL write-primary is route-by-route. It is not a global finance write cutover.
              </div>
              {financeWriteRoutesLoading ? (
                <div className="mt-2 text-sm text-muted-foreground" data-testid="finance-write-routes-loading">
                  Loading finance write readiness...
                </div>
              ) : financeWriteRoutes.length === 0 ? (
                <div className="mt-2 text-sm text-muted-foreground" data-testid="finance-write-routes-empty">
                  No finance write readiness data yet.
                </div>
              ) : (
                <div className="mt-3 overflow-x-auto rounded-md border" data-testid="finance-write-routes-table">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-2 py-2 text-left">Route</th>
                        <th className="px-2 py-2 text-left">Current source</th>
                        <th className="px-2 py-2 text-left">Eligible</th>
                        <th className="px-2 py-2 text-left">Controls</th>
                        <th className="px-2 py-2 text-left">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {financeWriteRoutes.map((route) => (
                        <tr key={route.route_key} className="border-t">
                          <td className="px-2 py-2">
                            <div className="font-mono">{route.route_key}</div>
                            <div className="text-muted-foreground">{route.method} {route.path}</div>
                          </td>
                          <td className="px-2 py-2">{route.current_source}</td>
                          <td className="px-2 py-2">
                            {route.eligible_for_postgres_write ? (
                              <span className="text-emerald-700">Yes</span>
                            ) : (
                              <span className="text-amber-700" title={route.blocked_reason || route.notes}>
                                No
                              </span>
                            )}
                          </td>
                          <td className="px-2 py-2">
                            <span>{route.idempotency_required ? "Idempotency" : "No idempotency"}</span>
                            <span className="mx-1 text-muted-foreground">/</span>
                            <span>{route.audit_required ? "Audit" : "No audit"}</span>
                            <span className="mx-1 text-muted-foreground">/</span>
                            <span>{route.reversal_supported ? "Reversal" : "No reversal"}</span>
                          </td>
                          <td className="px-2 py-2">
                            <div>{route.production_readiness_status}</div>
                            {route.blocked_reason ? (
                              <div className="text-muted-foreground">{route.blocked_reason}</div>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── APPLICATION-WIDE SOURCE-OF-TRUTH MAP ── */}
      <section className="rounded-lg border p-4" data-testid="cutover-app-map">
        <div>
          <h2 className="text-base font-semibold">Application-wide source of truth</h2>
          <p className="text-sm text-muted-foreground">
            What is implemented on PostgreSQL vs still served by MongoDB, across the whole app. The
            cutover control plane formally tracks only <span className="font-mono">finance_ledger</span>{" "}
            and <span className="font-mono">identity_core</span>; every other domain is MongoDB-only and
            not yet in cutover scope.
          </p>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs" data-testid="cutover-app-map-legend">
          {(["pg_live", "pg_gated", "mongo_only"] as AppTier[]).map((t) => (
            <span
              key={t}
              className={`inline-flex items-center rounded-full px-2 py-0.5 font-medium ${APP_TIER_BADGE[t].bg} ${APP_TIER_BADGE[t].text}`}
            >
              {APP_TIER_BADGE[t].label}
            </span>
          ))}
        </div>
        <div className="mt-3 overflow-x-auto rounded-md border" data-testid="cutover-app-map-table">
          <table className="w-full text-xs">
            <thead className="bg-muted/50">
              <tr>
                <th scope="col" className="px-2 py-2 text-left">Domain</th>
                <th scope="col" className="px-2 py-2 text-left">Read</th>
                <th scope="col" className="px-2 py-2 text-left">Write</th>
                <th scope="col" className="px-2 py-2 text-left">Status</th>
                <th scope="col" className="px-2 py-2 text-left">Notes</th>
              </tr>
            </thead>
            <tbody>
              {APP_CUTOVER_MAP.map((row) => (
                <tr key={row.domain} className="border-t">
                  <td className="px-2 py-2 font-medium">{row.domain}</td>
                  <td className="px-2 py-2">{row.read}</td>
                  <td className="px-2 py-2">{row.write}</td>
                  <td className="px-2 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 font-medium ${APP_TIER_BADGE[row.tier].bg} ${APP_TIER_BADGE[row.tier].text}`}
                    >
                      {APP_TIER_BADGE[row.tier].label}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-muted-foreground">{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Reference snapshot (2026-08-07). Live per-route / per-domain state is in the finance panel
          above and the control-plane table below.
        </p>
      </section>

      {/* ── FILTERS ── */}
      <section className="flex flex-wrap gap-3" data-testid="cutover-filters">
        <div className="flex flex-col gap-1">
          <label htmlFor="building-filter" className="text-xs font-medium text-muted-foreground">
            Building ID
          </label>
          <input
            id="building-filter"
            data-testid="cutover-filter-building"
            type="text"
            placeholder="e.g. 13195"
            value={buildingFilter}
            onChange={(e) => setBuildingFilter(e.target.value)}
            className="h-8 w-44 rounded-md border px-2 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="domain-filter" className="text-xs font-medium text-muted-foreground">
            Domain
          </label>
          <input
            id="domain-filter"
            data-testid="cutover-filter-domain"
            type="text"
            placeholder="e.g. finance_ledger"
            value={domainFilter}
            onChange={(e) => setDomainFilter(e.target.value)}
            className="h-8 w-52 rounded-md border px-2 text-sm"
          />
        </div>
        <div className="flex items-end">
          <button
            data-testid="cutover-filter-apply"
            onClick={fetchStatus}
            disabled={isFetching}
            className="h-8 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {isFetching ? "Loading…" : "Apply"}
          </button>
        </div>
      </section>

      {/* ── ACTION FEEDBACK ── */}
      {actionMsg && (
        <div
          data-testid={actionMsg.ok ? "cutover-action-success" : "cutover-action-error"}
          className={`rounded-md border px-4 py-2 text-sm ${
            actionMsg.ok
              ? "border-green-300 bg-green-50 text-green-800"
              : "border-red-300 bg-red-50 text-red-800"
          }`}
        >
          {actionMsg.text}
        </div>
      )}

      {/* ── ERROR ── */}
      {error && (
        <div
          data-testid="cutover-error"
          className="rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {/* ── STATUS TABLE ── */}
      {!isFetching && !error && items.length === 0 && (
        <div
          data-testid="cutover-empty"
          className="rounded-lg border p-6 text-center text-sm text-muted-foreground"
        >
          No domains registered yet. Domains appear here once their first
          promote or shadow action is recorded via the API.
        </div>
      )}

      {items.length > 0 && (
        <section className="overflow-x-auto rounded-lg border" data-testid="cutover-table">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Building</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Domain</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Mode</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Readiness</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Read src</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Write src</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Last promoted</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Last diff</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const key = `${item.building_id}/${item.domain}`;
                const isActing = acting === key;

                const canShadow = item.mode === "mongo_primary";
                const canRead =
                  item.mode === "postgres_shadow" &&
                  (item.domain !== "finance_ledger" || financePromoteEligible);
                const canWrite  = item.mode === "postgres_read" && item.domain !== "finance_ledger";
                const canRollback = item.rollback_available && item.mode !== "mongo_primary";

                return (
                  <tr
                    key={key}
                    className="border-b last:border-0 hover:bg-muted/30"
                    data-testid={`cutover-row-${key.replace("/", "-")}`}
                  >
                    <td className="px-3 py-2 font-mono text-xs">{item.building_id}</td>
                    <td className="px-3 py-2 font-mono text-xs">{item.domain}</td>
                    <td className="px-3 py-2">
                      <ModeBadge mode={item.mode} />
                    </td>
                    <td className="px-3 py-2">
                      <ReadinessDot status={item.readiness_status} />
                    </td>
                    <td className="px-3 py-2 text-xs">{item.read_source}</td>
                    <td className="px-3 py-2 text-xs">{item.write_source}</td>
                    <td className="px-3 py-2 text-xs">{fmtTs(item.last_promoted_at)}</td>
                    <td className="px-3 py-2 text-xs">
                      {item.last_shadow_diff_at ? (
                        <span className="text-orange-600">{fmtTs(item.last_shadow_diff_at)}</span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1.5">
                        <button
                          data-testid={`cutover-shadow-${key.replace("/", "-")}`}
                          disabled={!canShadow || isActing}
                          onClick={() => doAction(item.building_id, item.domain, "shadow", {})}
                          className="rounded bg-blue-600 px-2 py-0.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-30"
                          title={canShadow ? "Enter shadow mode" : "Not available in current mode"}
                        >
                          → Shadow
                        </button>
                        <button
                          data-testid={`cutover-read-${key.replace("/", "-")}`}
                          disabled={!canRead || isActing}
                          onClick={() => doAction(item.building_id, item.domain, "promote-read", {})}
                          className="rounded bg-yellow-600 px-2 py-0.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-30"
                          title={
                            canRead
                              ? "Promote to PG read"
                              : item.domain === "finance_ledger"
                                ? financePromoteBlockedReason
                                : "Not available in current mode"
                          }
                        >
                          → PG Read
                        </button>
                        <button
                          data-testid={`cutover-write-${key.replace("/", "-")}`}
                          disabled={!canWrite || isActing}
                          onClick={() => doAction(item.building_id, item.domain, "promote-write", {})}
                          className="rounded bg-green-700 px-2 py-0.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-30"
                          title={canWrite ? "Promote to PG write" : "Not available in current mode"}
                        >
                          → PG Write
                        </button>
                        <button
                          data-testid={`cutover-rollback-${key.replace("/", "-")}`}
                          disabled={!canRollback || isActing}
                          onClick={async () => {
                            const reason = window.prompt(
                              `Rollback reason for ${item.domain} on ${item.building_id}?`,
                              "",
                            );
                            if (!reason || reason.trim().length < 5) return;
                            await doAction(item.building_id, item.domain, "rollback", { reason });
                          }}
                          className="rounded bg-orange-600 px-2 py-0.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-30"
                          title={canRollback ? "Roll back one step" : "Rollback not available"}
                        >
                          ↩ Rollback
                        </button>
                        {isActing && (
                          <span className="text-xs text-muted-foreground" data-testid="cutover-acting">
                            …
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      <p className="text-xs text-muted-foreground" data-testid="cutover-total-count">
        {isFetching ? "Loading…" : `${items.length} domain(s) shown`}
      </p>
    </main>
  );
}
