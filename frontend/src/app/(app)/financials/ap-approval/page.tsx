// @featuretrace:ap_automation — App Router thin wrapper for the AP invoice approval queue.
// Layer: frontend
// Data flow: delegates entirely to APApprovalQueuePage (pages/dashboard/financial/).
// Related: frontend/src/pages/dashboard/financial/APApprovalQueuePage.tsx
//           backend/routers/ap_approval.py
// Scope: (building-scoped)
"use client";
import APApprovalQueuePage from "@/pages/dashboard/financial/APApprovalQueuePage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/ap-approval/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <APApprovalQueuePage/>;
}