// @featuretrace:management-hierarchy — Resolves audited management hierarchy nav route for all management entities.
// Layer: frontend
// Data flow: /admin/management-entities -> InternalPreview recovery -> /api/management-hierarchy/* (building-scoped).
// Related: backend/routers/management_hierarchy.py
//          docs/architecture/frontend_data_source_matrix.md

"use client";

import { InternalPreview } from "@/components/shared/RecoveryStates";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/admin/management-entities/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
  return <InternalPreview featureKey="management_hierarchy_enabled" testId="management-hierarchy-management-entities-route" />;
}
