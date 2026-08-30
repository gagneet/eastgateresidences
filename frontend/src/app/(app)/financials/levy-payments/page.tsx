"use client";
import {Suspense} from "react";
import FeatureGuard from "@/components/layout/FeatureGuard";
import LevyPaymentPage from "@/pages/dashboard/LevyPaymentPage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/levy-payments/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="levy_payments">
            <Suspense>
                <LevyPaymentPage/>
            </Suspense>
        </FeatureGuard>
    );
}
