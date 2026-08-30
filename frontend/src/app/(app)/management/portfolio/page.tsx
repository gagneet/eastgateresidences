// @featuretrace:management-hierarchy — Resolves audited management hierarchy nav route for independent manager portfolio management.
// Layer: frontend
// Data flow: /management/portfolio -> InternalPreview recovery -> /api/management-hierarchy/* (building-scoped).
// Related: backend/routers/management_hierarchy.py
//          docs/architecture/frontend_data_source_matrix.md

"use client";

import { InternalPreview } from "@/components/shared/RecoveryStates";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/management/portfolio/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
  return <InternalPreview featureKey="management_hierarchy_enabled" testId="management-hierarchy-my-managed-buildings-route" />;
}
