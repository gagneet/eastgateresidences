// @featuretrace:powerhouse-ui-navigation — Role-aware Powerhouse Control Centre and diagnostic toggle status panel.
// Layer: frontend
// Data flow: /powerhouse -> GET /api/features/powerhouse/status -> feature toggle catalogue/readiness docs (building-scoped).
//            /powerhouse -> GET /api/admin/cutover/powerhouse-command-foundation/{building_id} -> command foundation health (building-scoped).
// Related: frontend/src/lib/powerhouseFeatureCatalogue.ts
//          backend/routers/powerhouse_status.py
//          backend/routers/cutover_admin.py
//          docs/architecture/powerhouse-ui-navigation.md

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";

import { useAuth } from "@/contexts/AuthContext";
import { FeatureDisabled, PermissionDenied, ServerError } from "@/components/shared/RecoveryStates";
import {
  POWERHOUSE_FEATURES,
  PowerhouseFeature,
  featureHasConcreteRoute,
  roleCanSeePowerhouseFeature,
} from "@/lib/powerhouseFeatureCatalogue";

type ApiFeature = PowerhouseFeature & {
  enabled: boolean;
  currentStatus: string;
  globalDefault?: boolean | null;
  buildingOverride?: boolean | null;
  visibleForRole: boolean;
  reason: string;
};

type CommandFoundationHealth = {
  building_id: string;
  scheme_found: boolean;
  domains: { domain: string; mode: string; readiness_status: string }[];
  outbox: { pending: number; oldest_pending: string | null; dead_lettered: number } | null;
  idempotency: { incomplete_records: number } | null;
};

const STATUS_LABELS: Record<string, string> = {
  disabled: "Disabled",
  internal_preview: "Internal preview",
  shell_only: "Shell only",
  beta: "Beta",
  production_ready: "Production ready",
};

const CATEGORY_ORDER: PowerhouseFeature["category"][] = ["control", "communications", "workflow", "finance", "archive"];

const CATEGORY_INFO: Record<PowerhouseFeature["category"], { label: string; blurb: string }> = {
  control: {
    label: "Control & Visibility",
    blurb: "Read-only status, toggle, and cutover-readiness surfaces. Use these to check what Powerhouse currently sees — none of them change anything by themselves.",
  },
  communications: {
    label: "Communications",
    blurb: "The conversation, shared-inbox, and AI-summary shell aimed at replacing ad hoc email threads with a structured, auditable record. Gated behind privacy and safety hardening before any resident-facing rollout.",
  },
  workflow: {
    label: "Workflow & Automation",
    blurb: "Committee and manager action queues plus rule-based automation. Still shells today — approval, rollback, and audit-trail work must land before automation can act without an operator in the loop.",
  },
  finance: {
    label: "Finance Cutover",
    blurb: "Route-level MongoDB → PostgreSQL readiness for financial reads and writes, scoped per building.",
  },
  archive: {
    label: "Archive",
    blurb: "Placeholder for post-cutover, read-only MongoDB archive controls — not implemented yet.",
  },
};
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/powerhouse/PowerhouseControlCentrePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusBadge({ status }: { status: string }) {
  const tone = status === "production_ready" ? "bg-emerald-100 text-emerald-800" :
    status === "beta" ? "bg-blue-100 text-blue-800" :
      status === "disabled" ? "bg-slate-100 text-slate-700" :
        "bg-amber-100 text-amber-900";
  return <span className={`rounded px-2 py-1 text-xs font-medium ${tone}`}>{STATUS_LABELS[status] ?? status}</span>;
}
/**
 * @generated FunctionHeader
 * Function: FeatureTile
 * Path: frontend/src/pages/powerhouse/PowerhouseControlCentrePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FeatureTile({
  feature,
  canViewDocs,
  currentPath,
}: {
  feature: ApiFeature;
  canViewDocs: boolean;
  currentPath: string;
}) {
  const isSelfLink = feature.routePath === currentPath;
  const canOpen = feature.enabled && feature.visibleForRole && featureHasConcreteRoute(feature) && !isSelfLink;
  // /tech-docs/* is hard-gated to super_admin by frontend/public/tech-docs/auth-guard.js.
  // Rendering this link for any other internal role sends them straight into that
  // guard's `window.location.href = '/dashboard'` redirect — indistinguishable from a
  // crash from the user's point of view. Gate visibility to match the guard, not to
  // this page's broader `isInternal` (manager/EC) audience.
  //
  // Route through the in-app tech-docs viewer (/admin/tech-docs) rather than
  // navigating straight to the static /tech-docs/index.html file — the static page is a
  // full document load outside the Next.js app shell, so clicking through from there to
  // another tech-docs page (most of which have no "Back to Dashboard" link of their own)
  // strands the user with no way back into the app. The in-app viewer embeds the same
  // static site in an iframe inside the normal DashboardLayout sidebar, plus an explicit
  // "open in new tab" escape hatch for anyone who wants the standalone page.
  const docsHref = feature.documentationLink.startsWith("/docs/") ? "/admin/tech-docs" : feature.documentationLink;
  return (
    <article data-testid={`powerhouse-feature-${feature.key}`} className="rounded-lg border bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">{feature.displayName}</h2>
          <p className="mt-1 text-sm text-slate-600">{feature.description}</p>
        </div>
        <StatusBadge status={feature.status} />
      </div>

      {(feature.status === "internal_preview" || feature.status === "shell_only") && (
        <div data-testid={`powerhouse-warning-${feature.key}`} className="mt-3 flex gap-2 rounded-md bg-amber-50 p-2 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{feature.status === "shell_only" ? "This is a shell only. Do not present it as production-ready." : "Internal preview only."}</span>
        </div>
      )}

      <dl className="mt-4 grid grid-cols-1 gap-2 text-sm md:grid-cols-2">
        <div><dt className="text-slate-500">Required toggle</dt><dd data-testid={`powerhouse-toggle-${feature.key}`}>{feature.requiredToggles.join(", ") || "None"}</dd></div>
        <div><dt className="text-slate-500">Role access</dt><dd>{feature.allowedRoles.join(", ")}</dd></div>
        <div><dt className="text-slate-500">Building status</dt><dd>{feature.enabled ? "Enabled" : "Disabled"}</dd></div>
        <div><dt className="text-slate-500">Risk</dt><dd>{feature.riskLabel}</dd></div>
      </dl>

      <p className="mt-3 text-sm text-slate-600">{feature.enabled ? feature.supportMessage : feature.reason || feature.disabledReason}</p>

      <div className="mt-4 flex flex-wrap gap-2">
        {isSelfLink ? (
          <span data-testid={`powerhouse-current-page-${feature.key}`} className="rounded-md border px-3 py-2 text-sm text-slate-500">
            You are here
          </span>
        ) : canOpen && feature.routePath ? (
          <Link data-testid={`powerhouse-open-${feature.key}`} className="inline-flex items-center gap-1 rounded-md bg-slate-900 px-3 py-2 text-sm text-white" href={feature.routePath}>
            Open <ExternalLink className="h-3 w-3" />
          </Link>
        ) : (
          <span
            data-testid={`powerhouse-disabled-link-${feature.key}`}
            title={feature.nextRequiredStep}
            className="rounded-md border px-3 py-2 text-sm text-slate-600"
          >
            {feature.routePath ? "Unavailable for this role/building" : "No UI route yet"}
          </span>
        )}
        {canViewDocs && (
          <Link className="rounded-md border px-3 py-2 text-sm text-slate-700" href={docsHref}>
            Docs
          </Link>
        )}
      </div>
    </article>
  );
}

/** Read-only outbox/idempotency/domain-readiness snapshot for the command foundation, fetched inline. */
function CommandFoundationHealthPanel({ api, buildingId }: { api: any; buildingId: string | null }) {
  const [health, setHealth] = useState<CommandFoundationHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!buildingId) return;
    let cancelled = false;
    api.get(`/admin/cutover/powerhouse-command-foundation/${buildingId}`)
      .then((res: any) => { if (!cancelled) setHealth(res.data); })
      .catch((err: any) => { if (!cancelled) setError(err?.response?.data?.detail || "Could not load command foundation health."); });
    return () => { cancelled = true; };
  }, [api, buildingId]);

  if (error) {
    return <p data-testid="powerhouse-command-foundation-error" className="mt-3 text-sm text-slate-500">{error}</p>;
  }
  if (!health) {
    return <p data-testid="powerhouse-command-foundation-loading" className="mt-3 text-sm text-slate-500">Loading command foundation health...</p>;
  }
  if (!health.scheme_found) {
    return <p data-testid="powerhouse-command-foundation-no-scheme" className="mt-3 text-sm text-slate-500">No PostgreSQL scheme context for this building yet — nothing to report.</p>;
  }

  return (
    <div data-testid="powerhouse-command-foundation-panel" className="mt-3 space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-md border p-3">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Outbox pending</dt>
          <dd className="mt-1 text-lg font-semibold text-slate-950">{health.outbox?.pending ?? 0}</dd>
        </div>
        <div className="rounded-md border p-3">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Outbox dead-lettered</dt>
          <dd className="mt-1 text-lg font-semibold text-slate-950">{health.outbox?.dead_lettered ?? 0}</dd>
        </div>
        <div className="rounded-md border p-3">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Idempotency records incomplete</dt>
          <dd className="mt-1 text-lg font-semibold text-slate-950">{health.idempotency?.incomplete_records ?? 0}</dd>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b text-slate-500">
            <tr>
              <th className="py-2 pr-4">Domain</th>
              <th className="py-2 pr-4">Mode</th>
              <th className="py-2 pr-4">Readiness</th>
            </tr>
          </thead>
          <tbody>
            {health.domains.map((d) => (
              <tr key={d.domain} className="border-b last:border-0">
                <td className="py-2 pr-4">{d.domain}</td>
                <td className="py-2 pr-4">{d.mode}</td>
                <td className="py-2 pr-4">{d.readiness_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
/**
 * @generated FunctionHeader
 * Function: PowerhouseControlCentrePage
 * Path: frontend/src/pages/powerhouse/PowerhouseControlCentrePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PowerhouseControlCentrePage() {
  const { api, loading, user, isAdmin, isManager } = useAuth() as any;
  const [features, setFeatures] = useState<ApiFeature[] | null>(null);
  const [resolvedBuildingId, setResolvedBuildingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const currentPath = usePathname() ?? "/powerhouse";

  const role = user?.role;
  // 'chairman' is not a top-level user.role value (see rules/post-compact-critical.md) — a
  // chairman is a user with role='ec_member', already covered by isManager() below.
  const isInternal = Boolean(isAdmin?.() || isManager?.());
  const canViewDocs = Boolean(isAdmin?.());

  useEffect(() => {
    if (loading || !isInternal) return;
    let cancelled = false;
    api.get("/features/powerhouse/status")
      .then((res: any) => {
        if (!cancelled) {
          setFeatures(res.data.features || []);
          setResolvedBuildingId(res.data.building_id || null);
        }
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.response?.data?.detail || "Could not load Powerhouse status.");
      });
    return () => { cancelled = true; };
  }, [api, loading, isInternal]);

  const visibleFeatures = useMemo(() => {
    if (features) return features;
    return POWERHOUSE_FEATURES
      .filter((feature) => roleCanSeePowerhouseFeature(feature, role))
      .map((feature) => ({
        ...feature,
        enabled: feature.requiredToggles.length === 0,
        currentStatus: feature.status,
        visibleForRole: roleCanSeePowerhouseFeature(feature, role),
        reason: feature.disabledReason,
        globalDefault: undefined,
        buildingOverride: undefined,
      } as ApiFeature));
  }, [features, role]);

  const groupedFeatures = useMemo(() => {
    const groups = new Map<PowerhouseFeature["category"], ApiFeature[]>();
    for (const feature of visibleFeatures) {
      const list = groups.get(feature.category) ?? [];
      list.push(feature);
      groups.set(feature.category, list);
    }
    return CATEGORY_ORDER
      .map((category) => ({ category, items: groups.get(category) ?? [] }))
      .filter((group) => group.items.length > 0);
  }, [visibleFeatures]);

  const showCommandFoundationHealth = visibleFeatures.some((f) => f.key === "powerhouse_command_foundation_health");

  if (loading) return <div data-testid="powerhouse-control-loading" className="p-6 text-sm">Loading Powerhouse status...</div>;
  if (!isInternal) return <PermissionDenied testId="powerhouse-control-forbidden" />;
  if (error) return <ServerError testId="powerhouse-control-error" />;
  if (!visibleFeatures.length) return <FeatureDisabled featureKey="powerhouse_control_centre" testId="powerhouse-control-empty" />;

  return (
    <main data-testid="powerhouse-control-centre" className="space-y-6 p-4 md:p-6">
      <header className="max-w-3xl space-y-2">
        <h1 className="text-xl font-semibold text-slate-950">Powerhouse Control Centre</h1>
        <p className="text-sm text-slate-600">
          Powerhouse is StrataOS's next-generation communications, workflow, and automation layer — the eventual
          home for AI-assisted conversation handling, committee/manager action queues, and rule-based automation,
          built on top of the MongoDB → PostgreSQL cutover control plane. This page is the single entry point for
          every Powerhouse-owned surface: what it does, who can see it, which toggle and building state gates it,
          and whether it is safe to rely on today.
        </p>
        <p className="text-sm text-slate-600">
          Every card is labelled with its real readiness state, grouped by what it does. <strong>Shell only</strong>{" "}
          and <strong>Internal preview</strong> are not placeholders for missing UI polish — they mark features
          whose safety-critical work (audit trails, approval gates, webhook verification, PII handling) is still in
          progress, tracked in the readiness backlog linked from each card&apos;s documentation. A button reading{" "}
          <em>&ldquo;Unavailable for this role/building&rdquo;</em> means the page exists but this toggle/role
          combination is not eligible yet; <em>&ldquo;No UI route yet&rdquo;</em> means no page has been built at
          all. Nothing on this page promotes a feature to production by itself — that only happens through the
          dedicated readiness and cutover workflows each card links to.
        </p>
      </header>

      {groupedFeatures.map(({ category, items }) => (
        <section key={category} className="space-y-3">
          <div>
            <h2 className="text-lg font-semibold text-slate-950">{CATEGORY_INFO[category].label}</h2>
            <p className="max-w-3xl text-sm text-slate-600">{CATEGORY_INFO[category].blurb}</p>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {items.map((feature) => (
              <FeatureTile key={feature.key} feature={feature} canViewDocs={canViewDocs} currentPath={currentPath} />
            ))}
          </div>
        </section>
      ))}

      {showCommandFoundationHealth && (
        <section className="rounded-lg border bg-white p-4">
          <h2 className="text-base font-semibold">Command foundation health</h2>
          <p className="mt-1 text-sm text-slate-600">
            Live outbox, idempotency, and per-domain readiness for the P2A PostgreSQL command foundation. Diagnostic
            only — nothing here promotes a domain out of MongoDB.
          </p>
          <CommandFoundationHealthPanel api={api} buildingId={resolvedBuildingId} />
        </section>
      )}

      <section data-testid="powerhouse-toggle-status-panel" className="rounded-lg border bg-white p-4">
        <h2 className="text-base font-semibold">Feature toggle status</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b text-slate-500">
            <tr>
              <th className="py-2 pr-4">Toggle</th>
              <th className="py-2 pr-4">Global default</th>
              <th className="py-2 pr-4">Building override</th>
              <th className="py-2 pr-4">Role visible</th>
              <th className="py-2 pr-4">UI route</th>
              <th className="py-2 pr-4">Next step</th>
            </tr>
            </thead>
            <tbody>
            {visibleFeatures.map((feature) => (
              <tr key={feature.key} className="border-b last:border-0">
                <td className="py-2 pr-4">{feature.requiredToggles.join(", ") || feature.key}</td>
                <td className="py-2 pr-4">{feature.globalDefault === undefined ? "n/a" : String(feature.globalDefault)}</td>
                <td className="py-2 pr-4">{feature.buildingOverride === undefined ? "n/a" : String(feature.buildingOverride)}</td>
                <td className="py-2 pr-4">{feature.visibleForRole ? "yes" : "no"}</td>
                <td className="py-2 pr-4">{feature.routePath || "No route"}</td>
                <td className="py-2 pr-4">{feature.nextRequiredStep}</td>
              </tr>
            ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
