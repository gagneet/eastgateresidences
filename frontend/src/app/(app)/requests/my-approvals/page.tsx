"use client";
import MyApprovalsPage from "@/pages/dashboard/MyApprovalsPage";
import FeatureGuard from "@/components/layout/FeatureGuard";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/requests/my-approvals/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="approvals">
            <MyApprovalsPage/>
        </FeatureGuard>
    );
}
