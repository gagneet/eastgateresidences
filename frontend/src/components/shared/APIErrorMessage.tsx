// @featuretrace:error-recovery-framework — Inline API error recovery display.
// Layer: frontend
// Data flow: classified API error -> APIErrorMessage -> retry/dashboard actions (global).
// Related: frontend/src/lib/api-error.ts
//          frontend/src/components/shared/RecoveryPanel.tsx

"use client";

import { useEffect } from "react";
import { AlertCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClassifiedApiError, logClientRecoveryEvent } from "@/lib/api-error";

type APIErrorMessageProps = {
  error: ClassifiedApiError;
  onRetry?: () => void;
  compact?: boolean;
};
/**
 * @generated FunctionHeader
 * Function: APIErrorMessage
 * Path: frontend/src/components/shared/APIErrorMessage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function APIErrorMessage({ error, onRetry, compact = false }: APIErrorMessageProps) {
  useEffect(() => {
    logClientRecoveryEvent(error, { surface: "api-error-message" });
  }, [error]);

  return (
    <div
      className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-700 shadow-sm"
      role="alert"
      aria-live="polite"
      data-testid={`api-error-${error.category}`}
    >
      <div className="flex gap-3">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-slate-700" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold text-slate-950">
            {error.category === "feature_disabled" ? "Feature unavailable" : "We could not complete that request"}
          </h2>
          <p className="mt-1">{error.message}</p>
          {!compact ? (
            <dl className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
              <div>
                <dt className="font-medium text-slate-900">Category</dt>
                <dd>{error.category}</dd>
              </div>
              {error.status ? (
                <div>
                  <dt className="font-medium text-slate-900">Status</dt>
                  <dd>{error.status}</dd>
                </div>
              ) : null}
              <div>
                <dt className="font-medium text-slate-900">Suggested action</dt>
                <dd>{error.suggestedAction}</dd>
              </div>
              {error.requestId ? (
                <div>
                  <dt className="font-medium text-slate-900">Request ID</dt>
                  <dd className="break-all font-mono">{error.requestId}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}
          {error.retryable && onRetry ? (
            <Button type="button" variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
              <RefreshCw aria-hidden="true" />
              Retry
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
