// @featuretrace:error-recovery-framework — Restores audited stale frontend route for document converter.
// Layer: frontend
// Data flow: /documents/converter -> DocumentConverterPage -> /api/document-converter/* (building-scoped).
// Related: docs/migration/ui-api-route-integrity-audit.md
//          frontend/src/pages/dashboard/DocumentConverterPage.jsx

"use client";

import DocumentConverterPage from "@/pages/dashboard/DocumentConverterPage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/documents/converter/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
  return <DocumentConverterPage />;
}
