"use client";
import RequestsPage from "@/pages/dashboard/RequestsPage";
import FeatureGuard from "@/components/layout/FeatureGuard";
// GAP-FT-005: requests/page.tsx was fully unguarded — any authenticated user
// could access the full request portal (maintenance, insurance, pet, access
// control, alteration, reimbursement forms) with no feature or role check.
// FeatureGuard blocks the page and shows an "unavailable" message when the
// 'requests' feature toggle is disabled for the building.
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/requests/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="requests">
            <RequestsPage/>
        </FeatureGuard>
    );
}
