// @featuretrace:powerhouse-foundation — Automation rules list shell UI.
// Layer: frontend
// Data flow: Automation rules shell → /api/powerhouse/workflows/automation-rule-runs → workflow automation run history (building-scoped).
// Related: backend/routers/powerhouse_conversations.py
//           docs/architecture/workflow-event-engine.md

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { FeatureDisabled, PermissionDenied } from "@/components/shared/RecoveryStates";
/**
 * @generated FunctionHeader
 * Function: PowerhouseAutomationRulesPage
 * Path: frontend/src/pages/powerhouse/PowerhouseAutomationRulesPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PowerhouseAutomationRulesPage() {
  const { api, loading, isManager, hasFeatureAccess } = useAuth() as any;
  const [runs, setRuns] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  const featureEnabled = hasFeatureAccess("powerhouse_automation_rules");

  useEffect(() => {
    if (loading || !isManager() || !featureEnabled) return;
    let cancelled = false;
    api
      .get("/powerhouse/workflows/automation-rule-runs")
      .then((res: any) => {
        if (!cancelled) setRuns(Array.isArray(res?.data) ? res.data : []);
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.response?.data?.detail || "Unable to load automation rule runs.");
      });
    return () => {
      cancelled = true;
    };
  }, [api, featureEnabled, isManager, loading]);

  if (loading) return <div data-testid="powerhouse-automation-loading">Loading automation...</div>;
  if (!featureEnabled) return <FeatureDisabled featureKey="powerhouse_automation_rules" testId="powerhouse-automation-feature-disabled" />;
  if (!isManager()) return <PermissionDenied testId="powerhouse-automation-forbidden" />;

  return (
    <section className="space-y-4 p-4 md:p-6" data-testid="powerhouse-automation-page">
      <header className="rounded-lg border p-4">
        <h1 className="text-lg font-semibold">Automation Rules (Shell)</h1>
        <p className="text-sm text-muted-foreground">
          Placeholder execution only. No legal/compliance automation runs without explicit human approval.
        </p>
      </header>
      {error ? <div data-testid="powerhouse-automation-error">{error}</div> : null}
      {!error && runs.length === 0 ? (
        <div data-testid="powerhouse-automation-empty" className="rounded-lg border p-4 text-sm text-muted-foreground">
          No automation rule runs yet.
        </div>
      ) : null}
      {!error && runs.length > 0 ? (
        <ul className="space-y-2" data-testid="powerhouse-automation-list">
          {runs.map((run) => (
            <li key={run.id} className="rounded border p-2 text-sm">
              {run.rule_key} · {run.status}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
