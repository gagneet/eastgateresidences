// @featuretrace:powerhouse-foundation — Shared inbox and conversation center shell UI.
// Layer: frontend
// Data flow: PowerhouseConversationCenterPage → /api/powerhouse/conversations/* and /api/powerhouse/inboxes/* → powerhouse service stores (building-scoped).
// Related: backend/routers/powerhouse_conversations.py
//           frontend/src/pages/powerhouse/PowerhouseThreadDetailPage.tsx
//           docs/architecture/conversation-engine.md

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { FeatureDisabled, PermissionDenied } from "@/components/shared/RecoveryStates";

type ThreadItem = {
  id: string;
  subject: string;
  status: string;
  priority: string;
  source_channel: string;
};
/**
 * @generated FunctionHeader
 * Function: PowerhouseConversationCenterPage
 * Path: frontend/src/pages/powerhouse/PowerhouseConversationCenterPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PowerhouseConversationCenterPage() {
  const { api, loading, isManager, isOwner, isTenant, isGuest, hasFeatureAccess } = useAuth() as any;
  const [threads, setThreads] = useState<ThreadItem[]>([]);
  const [isFetching, setIsFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const featureEnabled = hasFeatureAccess("powerhouse_conversations");
  const canView = featureEnabled && (isManager() || isOwner() || isTenant() || isGuest());

  useEffect(() => {
    if (loading || !canView) return;

    let cancelled = false;
    setIsFetching(true);
    setError(null);

    api
      .get("/powerhouse/conversations/threads?limit=20&offset=0")
      .then((res: any) => {
        if (cancelled) return;
        setThreads(Array.isArray(res?.data?.items) ? res.data.items : []);
      })
      .catch((err: any) => {
        if (cancelled) return;
        const message = err?.response?.data?.detail || "Unable to load conversation threads.";
        setError(message);
      })
      .finally(() => {
        if (cancelled) return;
        setIsFetching(false);
      });

    return () => {
      cancelled = true;
    };
  }, [api, canView, loading]);

  if (loading) {
    return <div data-testid="powerhouse-center-loading">Loading conversation center...</div>;
  }

  if (!featureEnabled) {
    return <FeatureDisabled featureKey="powerhouse_conversations" testId="powerhouse-center-feature-disabled" />;
  }

  if (!canView) {
    return <PermissionDenied testId="powerhouse-center-forbidden" />;
  }

  return (
    <main className="space-y-4 p-4 md:p-6" data-testid="powerhouse-conversation-center">
      <header className="rounded-lg border p-4">
        <h1 className="text-xl font-semibold">Powerhouse Conversation Center (Shell)</h1>
        <p className="text-sm text-muted-foreground">
          Shared inbox, conversation timelines, internal notes, linked entities, and AI assistance placeholders.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <article className="rounded-lg border p-4 lg:col-span-2">
          <h2 className="mb-3 text-base font-medium">Conversation Threads</h2>
          {isFetching ? <div data-testid="powerhouse-thread-list-loading">Loading threads...</div> : null}
          {error ? <div data-testid="powerhouse-thread-list-error">{error}</div> : null}
          {!isFetching && !error && threads.length === 0 ? (
            <div data-testid="powerhouse-thread-list-empty">
              No threads yet. Once inbox mapping and request intake are active, threads will appear here.
            </div>
          ) : null}
          {!isFetching && !error && threads.length > 0 ? (
            <ul className="space-y-2" data-testid="powerhouse-thread-list">
              {threads.map((thread) => (
                <li key={thread.id} className="rounded border p-3">
                  <div className="font-medium">{thread.subject}</div>
                  <div className="text-xs text-muted-foreground">
                    {thread.status} · {thread.priority} · {thread.source_channel}
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </article>

        <aside className="space-y-4">
          <section className="rounded-lg border p-4" data-testid="powerhouse-internal-notes-panel">
            <h3 className="text-sm font-medium">Internal Notes Panel</h3>
            <p className="mt-2 text-xs text-muted-foreground">
              Internal notes are manager-visible only and are never exposed to owner/tenant/guest users.
            </p>
          </section>
          <section className="rounded-lg border p-4" data-testid="powerhouse-linked-entity-panel">
            <h3 className="text-sm font-medium">Linked Entity Panel</h3>
            <p className="mt-2 text-xs text-muted-foreground">
              Link threads to maintenance, levy, meeting, compliance, document request, and vendor records.
            </p>
          </section>
          <section className="rounded-lg border p-4" data-testid="powerhouse-ai-panel">
            <h3 className="text-sm font-medium">AI Summary & Suggested Action Panel</h3>
            <p className="mt-2 text-xs text-muted-foreground">
              AI suggestions are placeholders only in this phase. Human approval is always required before any action.
            </p>
          </section>
        </aside>
      </section>
    </main>
  );
}

