// @featuretrace:management-hierarchy — Resolves audited management hierarchy nav route for agency-managed strata entities.
// Layer: frontend
// Data flow: /admin/management-entities/agencies -> InternalPreview recovery -> /api/management-hierarchy/* (building-scoped).
// Related: backend/routers/management_hierarchy.py
//          docs/architecture/frontend_data_source_matrix.md

"use client";

import { InternalPreview } from "@/components/shared/RecoveryStates";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/admin/management-entities/agencies/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
  return <InternalPreview featureKey="management_hierarchy_enabled" testId="management-hierarchy-management-agencies-route" />;
}
