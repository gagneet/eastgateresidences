"use client";
import InsuranceClaimsPage from "@/pages/dashboard/InsuranceClaimsPage";
import FeatureGuard from "@/components/layout/FeatureGuard";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/insurance/claims/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="insurance_claims">
            <InsuranceClaimsPage/>
        </FeatureGuard>
    );
}
