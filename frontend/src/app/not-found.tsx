// @featuretrace:error-recovery-framework — App Router 404 recovery page.
// Layer: frontend
// Data flow: stale/broken frontend route -> not-found.tsx -> RecoveryPanel role-aware actions (global).
// Related: frontend/src/components/shared/RecoveryPanel.tsx
//          docs/migration/ui-api-route-integrity-audit.md

"use client";

import RecoveryPanel from "@/components/shared/RecoveryPanel";
/**
 * @generated FunctionHeader
 * Function: NotFound
 * Path: frontend/src/app/not-found.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function NotFound() {
  return (
    <RecoveryPanel
      variant="not_found"
      title="We could not find that page"
      message="The link may be outdated, the page may have moved, or the feature may not be enabled for your building."
      category="not_found"
      testId="not-found-recovery"
    />
  );
}
