// @featuretrace:powerhouse-foundation — Thread detail timeline shell UI.
// Layer: frontend
// Data flow: Thread detail shell → /api/powerhouse/conversations/threads/{id} → conversation/messages stores (building-scoped).
// Related: backend/routers/powerhouse_conversations.py
//           docs/architecture/conversation-engine.md

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { FeatureDisabled } from "@/components/shared/RecoveryStates";

type Props = {
  threadId: string;
};
/**
 * @generated FunctionHeader
 * Function: PowerhouseThreadDetailPage
 * Path: frontend/src/pages/powerhouse/PowerhouseThreadDetailPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PowerhouseThreadDetailPage({ threadId }: Props) {
  const { api, loading, hasFeatureAccess } = useAuth() as any;
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const featureEnabled = hasFeatureAccess("powerhouse_conversations");

  useEffect(() => {
    if (loading || !threadId || !featureEnabled) return;
    let cancelled = false;
    setError(null);
    api
      .get(`/powerhouse/conversations/threads/${threadId}`)
      .then((res: any) => {
        if (!cancelled) setData(res?.data || null);
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.response?.data?.detail || "Unable to load thread detail.");
      });
    return () => {
      cancelled = true;
    };
  }, [api, featureEnabled, loading, threadId]);

  if (loading) return <div data-testid="powerhouse-thread-loading">Loading thread...</div>;
  if (!featureEnabled) return <FeatureDisabled featureKey="powerhouse_conversations" testId="powerhouse-thread-feature-disabled" />;
  if (error) return <div data-testid="powerhouse-thread-error">{error}</div>;

  return (
    <section className="space-y-4 p-4 md:p-6" data-testid="powerhouse-thread-detail">
      <header className="rounded-lg border p-4">
        <h1 className="text-lg font-semibold">Thread Timeline (Shell)</h1>
        <p className="text-sm text-muted-foreground">Thread ID: {threadId}</p>
      </header>
      <article className="rounded-lg border p-4">
        <h2 className="font-medium">Timeline</h2>
        {!data?.messages?.length ? (
          <p className="mt-2 text-sm text-muted-foreground">No messages yet for this thread.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {data.messages.map((message: any) => (
              <li key={message.id} className="rounded border p-2 text-sm">
                <div>{message.body}</div>
                <div className="text-xs text-muted-foreground">{message.message_type}</div>
              </li>
            ))}
          </ul>
        )}
      </article>
    </section>
  );
}
