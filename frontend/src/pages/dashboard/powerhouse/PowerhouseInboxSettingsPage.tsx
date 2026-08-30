// @featuretrace:powerhouse-foundation — Building inbox settings shell UI.
// Layer: frontend
// Data flow: Inbox settings shell → /api/powerhouse/inboxes and /api/powerhouse/inboxes/configure → inbox shell stores (building-scoped).
// Related: backend/routers/powerhouse_conversations.py
//           docs/architecture/email-integration.md

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { FeatureDisabled, PermissionDenied } from "@/components/shared/RecoveryStates";
/**
 * @generated FunctionHeader
 * Function: PowerhouseInboxSettingsPage
 * Path: frontend/src/pages/powerhouse/PowerhouseInboxSettingsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PowerhouseInboxSettingsPage() {
  const { api, loading, isManager, hasFeatureAccess } = useAuth() as any;
  const [inboxes, setInboxes] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  const featureEnabled = hasFeatureAccess("powerhouse_shared_inbox");

  useEffect(() => {
    if (loading || !isManager() || !featureEnabled) return;
    let cancelled = false;
    api
      .get("/powerhouse/inboxes")
      .then((res: any) => {
        if (!cancelled) setInboxes(Array.isArray(res?.data) ? res.data : []);
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.response?.data?.detail || "Unable to load inbox settings.");
      });
    return () => {
      cancelled = true;
    };
  }, [api, featureEnabled, isManager, loading]);

  if (loading) return <div data-testid="powerhouse-inbox-settings-loading">Loading inbox settings...</div>;
  if (!featureEnabled) return <FeatureDisabled featureKey="powerhouse_shared_inbox" testId="powerhouse-inbox-settings-feature-disabled" />;
  if (!isManager()) return <PermissionDenied testId="powerhouse-inbox-settings-forbidden" />;

  return (
    <section className="space-y-4 p-4 md:p-6" data-testid="powerhouse-inbox-settings-page">
      <header className="rounded-lg border p-4">
        <h1 className="text-lg font-semibold">Building Inbox Settings (Shell)</h1>
        <p className="text-sm text-muted-foreground">
          Configure shared inboxes and provider adapters. Sending remains placeholder-only in this phase.
        </p>
      </header>
      {error ? <div data-testid="powerhouse-inbox-settings-error">{error}</div> : null}
      {!error && inboxes.length === 0 ? (
        <div data-testid="powerhouse-inbox-settings-empty" className="rounded-lg border p-4 text-sm text-muted-foreground">
          No inboxes configured yet.
        </div>
      ) : null}
      {!error && inboxes.length > 0 ? (
        <ul className="space-y-2" data-testid="powerhouse-inbox-settings-list">
          {inboxes.map((inbox) => (
            <li key={inbox.id} className="rounded border p-2 text-sm">
              {inbox.inbox_name} · {inbox.address}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
