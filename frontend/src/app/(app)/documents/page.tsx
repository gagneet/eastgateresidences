"use client";
import DocumentsPage from "@/pages/dashboard/DocumentsPage";
import FeatureGuard from "@/components/layout/FeatureGuard";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/documents/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="documents">
            <DocumentsPage/>
        </FeatureGuard>
    );
}
