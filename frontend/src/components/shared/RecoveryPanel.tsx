// @featuretrace:error-recovery-framework — Shared recovery page for broken routes, permission, feature, and server errors.
// Layer: frontend
// Data flow: app error/not-found/page guards -> RecoveryPanel -> role-aware dashboard/login actions (global).
// Related: frontend/src/lib/recovery.ts
//          frontend/src/components/shared/APIErrorMessage.tsx
//          frontend/src/app/error.tsx

"use client";

import { AlertTriangle, ArrowLeft, Home, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { getRoleAwareDashboardPath, supportContact } from "@/lib/recovery";

export type RecoveryVariant =
  | "not_found"
  | "server_error"
  | "network_error"
  | "permission_denied"
  | "feature_disabled"
  | "internal_preview"
  | "api_error";

type RecoveryPanelProps = {
  variant: RecoveryVariant;
  title: string;
  message: string;
  category?: string;
  requestId?: string;
  retryable?: boolean;
  onRetry?: () => void;
  featureKey?: string;
  featureStatus?: string;
  testId?: string;
};

const CATEGORY_LABELS: Record<RecoveryVariant, string> = {
  not_found: "Page not found",
  server_error: "Server error",
  network_error: "Network error",
  permission_denied: "Permission issue",
  feature_disabled: "Feature disabled",
  internal_preview: "Internal preview",
  api_error: "API error",
};
/**
 * @generated FunctionHeader
 * Function: RecoveryPanel
 * Path: frontend/src/components/shared/RecoveryPanel.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function RecoveryPanel({
  variant,
  title,
  message,
  category,
  requestId,
  retryable = false,
  onRetry,
  featureKey,
  featureStatus,
  testId = "recovery-panel",
}: RecoveryPanelProps) {
  const router = useRouter();
  const auth = useAuth() as any;
  const contact = supportContact();
  const dashboardPath = getRoleAwareDashboardPath(auth?.user);
  /**
   * @generated FunctionHeader
   * Function: retry
   * Path: frontend/src/components/shared/RecoveryPanel.tsx
   *
   * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
   */
  const retry = () => {
    if (onRetry) {
      onRetry();
      return;
    }
    router.refresh();
  };

  return (
    <main className="mx-auto flex min-h-[70vh] w-full max-w-3xl items-center px-4 py-8 sm:px-6" data-testid={testId}>
      <section className="w-full rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-8" aria-labelledby="recovery-title">
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-700">
            <AlertTriangle aria-hidden="true" className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {category || CATEGORY_LABELS[variant]}
            </p>
            <h1 id="recovery-title" className="mt-2 text-2xl font-semibold text-slate-950 sm:text-3xl">
              {title}
            </h1>
            <p className="mt-3 text-sm leading-6 text-slate-700 sm:text-base">{message}</p>

            <dl className="mt-5 grid gap-3 rounded-md bg-slate-50 p-4 text-sm text-slate-700 sm:grid-cols-2">
              <div>
                <dt className="font-medium text-slate-950">Error category</dt>
                <dd>{category || CATEGORY_LABELS[variant]}</dd>
              </div>
              {featureKey ? (
                <div>
                  <dt className="font-medium text-slate-950">Feature</dt>
                  <dd>{featureKey}</dd>
                </div>
              ) : null}
              {featureStatus ? (
                <div>
                  <dt className="font-medium text-slate-950">Feature status</dt>
                  <dd>{featureStatus}</dd>
                </div>
              ) : null}
              {requestId ? (
                <div>
                  <dt className="font-medium text-slate-950">Request ID</dt>
                  <dd className="break-all font-mono text-xs">{requestId}</dd>
                </div>
              ) : null}
            </dl>

            {contact ? (
              <p className="mt-4 text-sm text-slate-600">
                Need help? Contact support at <a className="font-medium text-slate-950 underline" href={`mailto:${contact}`}>{contact}</a>.
              </p>
            ) : null}

            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Button type="button" variant="outline" onClick={() => router.back()} aria-label="Go back">
                <ArrowLeft aria-hidden="true" />
                Go back
              </Button>
              <Button type="button" onClick={() => router.push(dashboardPath)} aria-label="Go to dashboard">
                <Home aria-hidden="true" />
                Go to dashboard
              </Button>
              {retryable ? (
                <Button type="button" variant="secondary" onClick={retry} aria-label="Retry">
                  <RefreshCw aria-hidden="true" />
                  Retry
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
