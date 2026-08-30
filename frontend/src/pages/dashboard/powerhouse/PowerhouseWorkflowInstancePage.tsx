// @featuretrace:powerhouse-foundation — Workflow instance detail shell UI.
// Layer: frontend
// Data flow: Workflow shell page → /api/powerhouse/workflows/instances/{id} → workflow shell stores (building-scoped).
// Related: backend/routers/powerhouse_conversations.py
//           docs/architecture/workflow-event-engine.md

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { FeatureDisabled, PermissionDenied } from "@/components/shared/RecoveryStates";

type Props = {
  instanceId: string;
};
/**
 * @generated FunctionHeader
 * Function: PowerhouseWorkflowInstancePage
 * Path: frontend/src/pages/powerhouse/PowerhouseWorkflowInstancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PowerhouseWorkflowInstancePage({ instanceId }: Props) {
  const { api, loading, isManager, hasFeatureAccess } = useAuth() as any;
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const featureEnabled = hasFeatureAccess("powerhouse_workflow_engine");

  useEffect(() => {
    if (loading || !instanceId || !isManager() || !featureEnabled) return;
    let cancelled = false;
    api
      .get(`/powerhouse/workflows/instances/${instanceId}`)
      .then((res: any) => {
        if (!cancelled) setData(res?.data || null);
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.response?.data?.detail || "Unable to load workflow instance.");
      });
    return () => {
      cancelled = true;
    };
  }, [api, featureEnabled, instanceId, isManager, loading]);

  if (loading) return <div data-testid="powerhouse-workflow-loading">Loading workflow...</div>;
  if (!featureEnabled) return <FeatureDisabled featureKey="powerhouse_workflow_engine" testId="powerhouse-workflow-feature-disabled" />;
  if (!isManager()) return <PermissionDenied testId="powerhouse-workflow-forbidden" />;
  if (error) return <div data-testid="powerhouse-workflow-error">{error}</div>;

  return (
    <section className="space-y-4 p-4 md:p-6" data-testid="powerhouse-workflow-instance">
      <header className="rounded-lg border p-4">
        <h1 className="text-lg font-semibold">Workflow Instance Shell</h1>
        <p className="text-sm text-muted-foreground">Instance ID: {instanceId}</p>
      </header>
      <article className="rounded-lg border p-4">
        <h2 className="font-medium">Event Stream</h2>
        {!data?.events?.length ? (
          <p className="mt-2 text-sm text-muted-foreground">No workflow events yet.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {data.events.map((event: any) => (
              <li key={event.id} className="rounded border p-2 text-sm">
                {event.event_type}
              </li>
            ))}
          </ul>
        )}
      </article>
    </section>
  );
}
